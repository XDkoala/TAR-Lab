"""Project loop: runs work orders, wakes the main agent on ledger events, handles the user.

Everything the loop knows comes from the ledger, so a killed loop resumes where it stopped:
unanswered wake-up events trigger a new decision, and the last decision is re-applied
idempotently in case the loop died halfway through applying it.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from tar_lab.agents.main_agent import Decision, MainAgent, Question, compact
from tar_lab.agents.roles import ROLES
from tar_lab.core.charter import APPROVAL_POINTS, Charter
from tar_lab.core.ledger import Event, Ledger
from tar_lab.core.pricing import Spend, project_spend
from tar_lab.core.workorder import Limits, WorkOrder
from tar_lab.integrations.cli_agents.process import quota_error

from .kernel import Kernel
from .notify import ConsoleNotifier, NotificationError, Notifier

LabStatus = Literal["intake", "working", "busy", "waiting", "error", "finished", "suspended"]
WAKE = frozenset({"project.charter", "project.resumed", "order.finished", "order.cancelled",
                  "user.reply", "user.message", "order.over_budget", "lab.idle",
                  "lab.deadline", "lab.question_held", "lab.budget"})
# While orders run, only these (and urgent user messages) wake the main agent at once.
BUSY_WAKE = frozenset({"order.finished", "order.cancelled", "lab.question_held"})
BUSY_POLL_SECONDS = 5.0
REMINDER_HOURS = (1.0, 4.0, 12.0, 24.0)
MAX_ORDER_MINUTES = 90.0
MAX_IDLE_WAKES = 3
MAX_DECISION_ERRORS = 3
LITERATURE_ALERT_HOURS = 6.0
FINISH_YES = "确认结束项目"
FINISH_NO = "不结束，继续工作（请写明方向）"
TOPIC_SUMMARY = "reports/topic_summary.md"
SUMMARY_MAX_CHARS = 4000
MAX_TOPIC_HOLDS = 2
DEEP_SEARCH = "launch_deep_search"
BUDGET_WARN_FRACTION = 0.8
BUDGET_NO = "不追加，用现有结果收尾"
MAX_PARALLEL_ORDERS = 3
# Roles whose orders may use the GPU run one at a time; the rest run alongside them.
EXCLUSIVE_ROLES = frozenset({"engineer"})


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Lab:
    kernel: Kernel
    agent: MainAgent
    notifier: Notifier = field(default_factory=ConsoleNotifier)
    tiers: Mapping[str, str] = field(
        default_factory=lambda: {"high": "codex", "medium": "codex", "low": "codex"})
    clock: Callable[[], datetime] = _now
    # Run work orders on a background thread so messages, timers and cancellations are
    # handled while an order runs. Tests use the synchronous mode.
    threaded: bool = True
    # Ending a project is the user's decision: ``finish`` becomes a confirmation question.
    confirm_finish: bool = True
    max_parallel: int = MAX_PARALLEL_ORDERS
    _workers: dict[str, threading.Thread] = field(default_factory=dict, init=False, repr=False)
    _recovered: bool = field(default=False, init=False, repr=False)

    @property
    def ledger(self) -> Ledger:
        return self.kernel.ledger

    # ---- ledger views -------------------------------------------------------------------

    def _events(self, *kinds: str) -> list[Event]:
        return [e for e in self.ledger.events() if not kinds or e.kind in kinds]

    def charter(self) -> Charter | None:
        events = self._events("project.charter", "project.charter_changed")
        return Charter.from_dict(events[-1].payload["charter"]) if events else None

    def status(self) -> LabStatus | None:
        """Terminal or blocked states; ``None`` while the project is active."""
        events = self._events("project.charter", "project.finished", "project.suspended",
                              "project.resumed")
        if not events:
            return "intake"
        last = events[-1].kind
        if any(e.kind == "project.finished" for e in events):
            return "finished"
        return "suspended" if last == "project.suspended" else None

    def questions(self) -> dict[str, Event]:
        """Open questions by id."""
        answered = {e.payload["qid"] for e in self._events("user.reply")}
        return {e.payload["qid"]: e for e in self._events("user.question")
                if e.payload["qid"] not in answered}

    # ---- intake and user actions --------------------------------------------------------

    def start(self, idea: str, ask: Callable[[Question], str],
              confirm: Callable[[Charter], bool]) -> Charter | None:
        if self.status() != "intake":
            raise RuntimeError("project already has a charter")
        if not self._events("project.created"):
            self.ledger.append("project.created", None, {"idea": idea})
        pi_dir = self.kernel.project.root / "pi"
        questions, usage = self.agent.intake_questions(idea, pi_dir / "intake-questions")
        answers = []
        for q in questions:
            answer = ask(q).strip() or q.default
            answers.append((q, answer))
        self.ledger.append("intake.answers", None, {
            "answers": [{**asdict(q), "answer": a} for q, a in answers],
            "usage": asdict(usage)})
        charter, usage = self.agent.draft_charter(idea, answers, pi_dir / "intake-charter")
        if not confirm(charter):
            self.ledger.append("intake.rejected", None, {"charter": charter.to_dict()})
            return None
        self.ledger.append("project.charter", None,
                           {"charter": charter.to_dict(), "usage": asdict(usage)})
        return charter

    def reply(self, qid: str, answer: str, *, auto: bool = False) -> None:
        if qid not in self.questions():
            raise KeyError(f"no open question {qid}")
        self.ledger.append("user.reply", None, {"qid": qid, "answer": answer, "auto": auto})

    def resume(self) -> None:
        if self.status() != "suspended":
            raise RuntimeError("project is not suspended")
        self.ledger.append("project.resumed", None, {})

    def say(self, text: str, *, now: bool = False) -> None:
        """Message the main agent. ``now`` wakes it even while a work order is running."""
        self.ledger.append("user.message", None, {"text": text, "now": now})

    def pause(self) -> None:
        """Suspend once the running work order (if any) has finished."""
        self.ledger.append("project.pause_requested", None, {})

    def stop(self) -> list[str]:
        """Cancel running work orders now and suspend; returns the cancelled ids."""
        running = [oid for oid, st in self.ledger.orders().items() if st.status == "running"]
        for oid in running:
            self.kernel.cancel(oid, "stopped by the user")
        self.pause()
        return running

    # ---- the loop -----------------------------------------------------------------------

    def run(self, poll_seconds: float = 30.0,
            sleep: Callable[[float], None] = time.sleep) -> LabStatus:
        while True:
            status = self.step()
            if status in ("finished", "suspended", "intake"):
                self._join_worker()
                return status
            if status in ("waiting", "error"):
                sleep(poll_seconds)
            elif status == "busy":
                sleep(min(poll_seconds, BUSY_POLL_SECONDS))

    def step(self) -> LabStatus:
        status = self.status()
        if status is not None:
            return status
        charter = self.charter()
        assert charter is not None
        busy = bool(self._running())
        if not busy and not self._recovered:
            self.kernel.recover()  # only orders left by a previous, dead loop
            self._recovered = True
        # Timers first: a timeout default that confirms finishing is applied in the same step.
        if self._timers(charter) == "suspended":
            return "suspended"
        decisions = self._events("pi.decision")
        if decisions:
            self._apply(decisions[-1], charter)
            if (status := self.status()) is not None:
                return status
        self._check_deadline(charter)
        self._literature_alerts(charter)
        self._executor_alerts(charter)
        self._budget_replies(charter)
        charter = self.charter() or charter
        exhausted = self._budget(charter)
        agent_down = self._agent_executor() in self.kernel.unavailable()
        if busy and not agent_down and (self._urgent_message() or self._settled()):
            return self._decide(charter)
        if self._pause_requested():
            return "busy" if busy else self._suspend("Paused by the user.")
        if not self._urgent() and not exhausted:
            startable = self._startable()
            if startable and not self.threaded:
                self.kernel.run_order(startable[0])
                return "working"
            for oid in startable:
                worker = threading.Thread(target=self.kernel.run_order, args=(oid,), daemon=True)
                self._workers[oid] = worker
                worker.start()
            busy = busy or bool(startable)
        if busy:
            return "busy"
        if self._needs_decision():
            return "waiting" if agent_down else self._decide(charter)
        if self.questions():
            return "waiting"
        if self._has_pending() and not exhausted:
            return "waiting"  # held until an executor is available again
        return self._idle()

    # ---- internals ----------------------------------------------------------------------

    def _notify(self, subject: str, body: str) -> None:
        try:
            self.notifier.notify(subject, body)
        except NotificationError as exc:
            self.ledger.append("notify.failed", None, {"subject": subject, "error": str(exc)})
        else:
            self.ledger.append("notify.sent", None, {"subject": subject})

    def _last_seq(self, kind: str) -> int:
        events = self._events(kind)
        return events[-1].seq if events else 0

    def _join_worker(self) -> None:
        for worker in list(self._workers.values()):
            worker.join()
        self._workers.clear()

    def _running(self) -> dict[str, threading.Thread]:
        self._workers = {oid: w for oid, w in self._workers.items() if w.is_alive()}
        return self._workers

    def _startable(self) -> list[str]:
        """Orders to start now, oldest first: at most ``max_parallel`` running in all, one
        GPU order at a time, none on an executor that is out of quota."""
        orders, unavailable = self.ledger.orders(), self.kernel.unavailable()
        gpu_busy = any(_exclusive(orders[oid].order) for oid in self._workers if oid in orders)
        picked: list[str] = []
        for oid, st in orders.items():
            if len(self._workers) + len(picked) >= self.max_parallel:
                break
            if (st.status not in ("pending", "interrupted") or oid in self._workers
                    or st.order.executor in unavailable):
                continue
            if _exclusive(st.order):
                if gpu_busy:
                    continue
                gpu_busy = True
            picked.append(oid)
        return picked

    def _settled(self) -> bool:
        """An order ended (or a question was held back) since the last decision."""
        since = self._last_seq("pi.decision")
        return any(e.seq > since for e in self._events(*BUSY_WAKE))

    def _agent_executor(self) -> str:
        return str(getattr(self.agent.model, "name", "main-agent"))

    def spend(self) -> Spend:
        models = {name: str(getattr(ex, "model", name)) for name, ex in
                  self.kernel.executors.items()}
        return project_spend(self.ledger.events(), self.ledger.orders(),
                             lambda e: models.get(e, e), str(getattr(self.agent.model, "model", "")))

    def _budget(self, charter: Charter) -> bool:
        """Warn at 80 % of the budget; at 100 % hold new orders and ask the user whether to
        raise it. Each level fires once per budget value. Returns whether it is used up."""
        spent, budget = self.spend().usd, charter.budget_usd
        fired = {(e.payload["level"], e.payload["budget_usd"]) for e in self._events("lab.budget")}
        for level, fraction in (("warn", BUDGET_WARN_FRACTION), ("exhausted", 1.0)):
            if spent < fraction * budget or (level, budget) in fired or (
                    level == "warn" and budget <= 0):
                continue
            ev = self.ledger.append("lab.budget", None, {
                "level": level, "spent_usd": round(spent, 4), "budget_usd": budget})
            if level == "warn":
                self._notify(f"Budget {spent / budget:.0%} used: {charter.title}",
                             f"已花费约 ${spent:.2f}（按 API 标价估算），预算 ${budget:g}。")
                continue
            qid = f"q{ev.seq}-budget"
            payload = {"qid": qid, "kind": "approval", "purpose": "budget",
                       "text": f"已花费约 ${spent:.2f}（按 API 标价估算），用完了预算 ${budget:g}。"
                               "新工单已暂停，正在跑的工单会跑完。要追加预算吗？可以选一个金额，"
                               "也可以直接写别的金额。",
                       "options": [f"追加到 ${math.ceil(budget * 1.5)}",
                                   f"追加到 ${math.ceil(budget * 2)}", BUDGET_NO],
                       "default": BUDGET_NO}
            self.ledger.append("user.question", None, payload)
            self._notify(f"Budget used up: {charter.title}", question_text(qid, payload))
        return spent >= budget

    def _budget_replies(self, charter: Charter) -> None:
        """A reply naming a higher amount raises the budget; the user's answer is the
        confirmation, so no extra round trip through the main agent is needed."""
        done = {e.payload["qid"] for e in self._events("lab.budget_answer")}
        for e in self._events("user.reply"):
            qid = e.payload["qid"]
            if not qid.endswith("-budget") or qid in done:
                continue
            match = re.search(r"\d+(?:\.\d+)?", e.payload["answer"].replace(",", ""))
            amount = float(match.group()) if match else None
            raised = amount is not None and amount > charter.budget_usd
            self.ledger.append("lab.budget_answer", None,
                               {"qid": qid, "budget_usd": amount if raised else None})
            if raised and amount is not None:
                updated, _ = _change_charter(charter, [{"field": "budget_usd",
                                                        "value": f"{amount:g}"}])
                self.ledger.append("project.charter_changed", None, {
                    "qid": qid, "charter": updated.to_dict(),
                    "changes": [{"field": "budget_usd", "value": f"{amount:g}"}], "rejected": []})
                self._notify(f"Charter changed: {charter.title}",
                             f"budget_usd: {charter.budget_usd:g} -> {amount:g}")
                charter = updated

    def _executor_alerts(self, charter: Charter) -> None:
        """Tell the user once per executor outage."""
        told = {e.payload["seq"] for e in self._events("lab.executor_notice")}
        for e in self._events("executor.unavailable"):
            if e.seq in told:
                continue
            self.ledger.append("lab.executor_notice", None, {"seq": e.seq})
            name = e.payload["executor"]
            fallback = self.kernel.fallbacks.get(name)
            then = (f"受影响的工单已改由 {fallback} 执行。" if fallback in self.kernel.executors
                    else "受影响的工单和主 Agent 先暂停，到时自动重试；也可以配置备用执行者"
                         "（TARLAB_FALLBACK，例如 codex=devin）。")
            until = datetime.fromisoformat(e.payload["until"])
            self._notify(f"{name} 额度不足或被限流: {charter.title}",
                         f"{name} 报错：{e.payload['error'][:300]}\n\n{until:%H:%M} UTC 后重试。{then}")

    def _has_pending(self) -> bool:
        return any(st.status in ("pending", "interrupted")
                   for st in self.ledger.orders().values())

    def _urgent_message(self) -> bool:
        since = self._last_seq("pi.decision")
        return any(e.seq > since and e.payload.get("now") for e in self._events("user.message"))

    def _pause_requested(self) -> bool:
        return self._last_seq("project.pause_requested") > max(
            self._last_seq("project.resumed"), self._last_seq("project.suspended"))

    def _needs_decision(self) -> bool:
        since = self._last_seq("pi.decision")
        return any(e.seq > since and not _confirms_finish(e) for e in self._events(*WAKE))

    def _urgent(self) -> bool:
        """A failed order since the last decision preempts the rest of the queue."""
        since = self._last_seq("pi.decision")
        return any(e.seq > since and e.payload["result"]["status"] != "done"
                   for e in self._events("order.finished"))

    def _elapsed_hours(self) -> float:
        start = self._events("project.charter")[0].ts
        return (self.clock() - start).total_seconds() / 3600

    def _check_deadline(self, charter: Charter) -> None:
        if self._elapsed_hours() >= charter.deadline_hours and not self._events("lab.deadline"):
            self.ledger.append("lab.deadline", None, {"deadline_hours": charter.deadline_hours})
            self._notify("Deadline reached",
                                 f"{charter.title}: the main agent must now wrap up.")

    def _literature_alerts(self, charter: Charter) -> None:
        """Tell the user once per kind of Undermind trouble within ``LITERATURE_ALERT_HOURS``."""
        noticed = self._events("lab.literature_notice")
        for e in self._events("undermind.degraded"):
            if any(n.payload["degraded_seq"] == e.seq for n in noticed):
                continue
            recent = any(n.payload["reason"] == e.payload["reason"]
                         and (e.ts - n.ts).total_seconds() < LITERATURE_ALERT_HOURS * 3600
                         for n in noticed)
            notice = self.ledger.append("lab.literature_notice", None, {
                "degraded_seq": e.seq, "reason": e.payload["reason"], "silent": recent})
            noticed.append(notice)
            if not recent:
                label = {"quota": "额度用完", "rate_limited": "持续限流",
                         "unavailable": "无法连接"}.get(e.payload["reason"], e.payload["reason"])
                self._notify(
                    f"Undermind {label}: {charter.title}",
                    f"Undermind {label}（{e.payload.get('tool')}）：{e.payload.get('detail', '')}"
                    "\n\n文献检索已自动改用 OpenAlex、Semantic Scholar 和 arXiv；Agent 会在报告"
                    "里注明来源。额度恢复或升级后无需操作，平台会自动重新使用 Undermind。")

    def _idle(self) -> LabStatus:
        since = max(self._last_seq("order.finished"), self._last_seq("user.reply"),
                    self._last_seq("project.resumed"))
        idles = [e for e in self._events("lab.idle") if e.seq > since]
        if len(idles) >= MAX_IDLE_WAKES:
            return self._suspend("The main agent stopped dispatching work.")
        self.ledger.append("lab.idle", None, {"count": len(idles) + 1})
        return "working"

    def _suspend(self, reason: str) -> LabStatus:
        self.ledger.append("project.suspended", None, {"reason": reason})
        self._notify("Project suspended", f"{reason}\nResume with `tarlab resume`.")
        return "suspended"

    def _timers(self, charter: Charter) -> LabStatus | None:
        now = self.clock()
        reminders: dict[str, int] = {}
        for e in self._events("user.reminder"):
            reminders[e.payload["qid"]] = reminders.get(e.payload["qid"], 0) + 1
        for qid, ev in self.questions().items():
            hours = (now - ev.ts).total_seconds() / 3600
            if hours >= charter.approval_timeout_hours:
                if charter.on_timeout == "suspend":
                    return self._suspend(f"No answer to {qid} within "
                                         f"{charter.approval_timeout_hours:g} hours.")
                self.reply(qid, ev.payload["default"], auto=True)
                self._notify(f"No answer to {qid}; default taken",
                                     f"{ev.payload['text']}\nDefault: {ev.payload['default']}")
                continue
            sent = reminders.get(qid, 0)
            if sent < len(REMINDER_HOURS) and hours >= REMINDER_HOURS[sent]:
                self.ledger.append("user.reminder", None, {"qid": qid, "number": sent + 1})
                self._notify(f"Reminder: {qid} is waiting for you",
                                     question_text(qid, ev.payload))
        return None

    def _decide(self, charter: Charter) -> LabStatus:
        n = len(self._events("pi.decision")) + 1
        log_dir = self.kernel.project.root / "pi" / f"decision-{n:03d}"
        try:
            decision, usage = self.agent.decide(self.context(charter), log_dir)
        except (RuntimeError, TypeError, KeyError, ValueError, OSError) as exc:
            if quota_error(str(exc)):
                self.kernel.mark_unavailable(self._agent_executor(), str(exc))
                return "waiting"
            since = self._last_seq("pi.decision")
            errors = [e for e in self._events("lab.error") if e.seq > since]
            self.ledger.append("lab.error", None, {"error": str(exc)[:2000]})
            if len(errors) + 1 >= MAX_DECISION_ERRORS:
                return self._suspend(f"The main agent failed {MAX_DECISION_ERRORS} times: {exc}")
            self._notify("Main agent error (will retry)", str(exc)[:2000])
            return "error"
        ev = self.ledger.append("pi.decision", None,
                                {"decision": decision.to_dict(), "usage": asdict(usage)})
        self._apply(ev, charter)
        return self.status() or "working"

    def _apply(self, ev: Event, charter: Charter) -> None:
        decision = Decision.from_dict(ev.payload["decision"])
        applied = {e.payload.get("decision_seq") for e in self._events(
            "pi.message", "project.charter_changed", "lab.cancel_applied")}
        if ev.seq not in applied:
            self._apply_once(ev.seq, decision, charter)
            charter = self.charter() or charter
        if decision.finish:
            if self._finish_confirmed(ev.seq, decision, charter) \
                    and not self._events("project.finished"):
                self.ledger.append("project.finished", None, {"summary": decision.final_summary})
                self._notify(f"Project finished: {charter.title}",
                                     decision.final_summary)
            return
        existing = self.ledger.orders()
        remaining_minutes = (charter.deadline_hours - self._elapsed_hours()) * 60
        for i, o in enumerate(decision.orders, 1):
            oid = f"o{ev.seq}-{i}-{o['role']}"
            if oid in existing:
                continue
            if remaining_minutes <= 1:
                self.ledger.append("lab.order_rejected", oid, {"reason": "deadline passed"})
                continue
            minutes = max(1.0, min(float(o["minutes"]), MAX_ORDER_MINUTES, remaining_minutes))
            executor = self.tiers[ROLES[o["role"]].tier]
            if executor in self.kernel.unavailable():
                executor = self.kernel.fallback_for(executor) or executor
            self.kernel.submit(WorkOrder(
                oid, o["role"], executor, o["goal"],
                tuple(o["deliverables"]), o["acceptance"], Limits(int(minutes * 60)),
                inputs=tuple(o["inputs"])))
        asked = {e.payload["qid"] for e in self._events("user.question", "lab.question_held")}
        for i, q in enumerate(decision.ask_user, 1):
            qid = f"q{ev.seq}-{i}"
            if qid in asked:
                continue
            payload: dict[str, Any] | None = {"qid": qid, **q}
            if q.get("point") == "topic":
                payload = self._topic_question({"qid": qid, **q}, charter)
            if payload is None:
                continue
            self.ledger.append("user.question", None, payload)
            subject = "Approval needed" if q["kind"] == "approval" else "Question"
            self._notify(f"{subject}: {charter.title}", question_text(qid, payload))

    def _finish_confirmed(self, seq: int, decision: Decision, charter: Charter) -> bool:
        """Ask once per finishing decision; only an explicit yes (or a timeout default that
        is yes) ends the project. Any other answer wakes the main agent to continue."""
        if not self.confirm_finish:
            return True
        qid = f"q{seq}-finish"
        if not any(e.payload["qid"] == qid for e in self._events("user.question")):
            payload = {"qid": qid, "kind": "approval", "purpose": "finish",
                       "text": f"主 Agent 提议结束项目。\n\n{decision.final_summary}\n\n"
                               "要结束吗？不结束的话，请写明希望继续做什么。",
                       "options": [FINISH_YES, FINISH_NO], "default": FINISH_YES}
            self.ledger.append("user.question", None, payload)
            self._notify(f"Approval needed: finish {charter.title}?", question_text(qid, payload))
            return False
        reply = next((e for e in self._events("user.reply") if e.payload["qid"] == qid), None)
        return reply is not None and reply.payload["answer"].strip() == FINISH_YES

    def _topic_question(self, payload: dict[str, Any], charter: Charter
                        ) -> dict[str, Any] | None:
        """Put the plain-language summary in front of a topic approval, or hold the question
        back (waking the main agent) while the summary or the novelty deep search is missing.
        After ``MAX_TOPIC_HOLDS`` holds it goes out anyway, with the gaps stated."""
        path = self.kernel.project.workspace / TOPIC_SUMMARY
        summary = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        problems = []
        if not summary:
            problems.append(f"`{TOPIC_SUMMARY}` is missing or empty: dispatch a writer order "
                            "for the plain-language topic summary.")
        elif len(summary) > SUMMARY_MAX_CHARS:
            problems.append(f"`{TOPIC_SUMMARY}` has {len(summary)} characters; it must stay "
                            f"under {SUMMARY_MAX_CHARS} (one page).")
        searched = any(e.payload.get("tool") == DEEP_SEARCH and not e.payload.get("is_error")
                       for e in self._events("undermind.call"))
        unavailable = self._deep_search_unavailable(charter)
        if not searched and unavailable is None:
            problems.append("No Undermind deep search has been run: the novelty of the "
                            "recommended topic needs one on its closest prior work "
                            "(researcher or reviewer order).")
        held = [e for e in self._events("lab.question_held") if e.payload.get("point") == "topic"]
        if problems and len(held) < MAX_TOPIC_HOLDS:
            self.ledger.append("lab.question_held", None,
                               {"qid": payload["qid"], "point": "topic", "problems": problems})
            return None
        notes = []
        if not searched:
            notes.append(f"注意：新颖性判断没有用深度检索（{unavailable or '主 Agent 未安排'}），"
                         "只用了普通文献检索，可能漏掉相近工作。")
        if problems:
            notes.append("平台检查未通过，主 Agent 补了两次仍未补齐，问题如下：\n"
                         + "\n".join(f"- {p}" for p in problems))
        parts = [summary[:SUMMARY_MAX_CHARS], *notes,
                 f"详细材料：`{TOPIC_SUMMARY}`、`reports/topic_report.md`", "---", payload["text"]]
        return {**payload, "asked": payload["text"],
                "text": "\n\n".join(p for p in parts if p)}

    def _spend_lines(self, charter: Charter) -> list[str]:
        spend = self.spend()
        lines = [f"About ${spend.usd:.2f} of the ${charter.budget_usd:g} budget "
                 f"({spend.usd / charter.budget_usd:.0%}), estimated at API list prices."
                 if charter.budget_usd > 0 else f"About ${spend.usd:.2f}; the budget is $0."]
        if spend.unpriced:
            lines.append(f"No price known (counted as $0): {', '.join(spend.unpriced)}.")
        if spend.usd >= charter.budget_usd:
            lines.append("Budget used up: new orders are held (running ones finish) until the "
                         "user raises the budget; the platform has asked. Do not dispatch more "
                         "work unless the budget was raised; otherwise wrap up with `finish`.")
        for name, until in self.kernel.unavailable().items():
            lines.append(f"Executor {name} is out of quota until {until:%H:%M} UTC; its orders "
                         "wait, or run on the fallback if one is configured.")
        return lines

    def _deep_search_unavailable(self, charter: Charter) -> str | None:
        if charter.max_deep_searches <= 0:
            return "章程的深度检索额度为 0"
        if not self._events("undermind.workspace"):
            return "项目没有连接 Undermind"
        if self._events("undermind.degraded"):
            return "Undermind 额度不足或无法连接"
        return None

    def _apply_once(self, seq: int, decision: Decision, charter: Charter) -> None:
        """Side effects that must happen once per decision, marked by ``decision_seq``."""
        if decision.message_to_user.strip():
            self.ledger.append("pi.message", None,
                               {"decision_seq": seq, "text": decision.message_to_user})
            self._notify(f"Message from the main agent: {charter.title}",
                         decision.message_to_user)
        cancelled = [oid for oid in decision.cancel_orders
                     if self.kernel.cancel(oid, f"cancelled by the main agent (decision {seq})")]
        if decision.cancel_orders:
            self.ledger.append("lab.cancel_applied", None,
                               {"decision_seq": seq, "cancelled": cancelled,
                                "requested": list(decision.cancel_orders)})
        if decision.charter_changes:
            updated, rejected = _change_charter(charter, decision.charter_changes)
            self.ledger.append("project.charter_changed", None, {
                "decision_seq": seq, "charter": updated.to_dict(),
                "changes": list(decision.charter_changes), "rejected": rejected})
            self._notify(f"Charter changed: {charter.title}", "\n".join(
                f"{c['field']}: {getattr(charter, c['field'])} -> {c['value']}"
                for c in decision.charter_changes))

    def context(self, charter: Charter) -> str:
        elapsed = self._elapsed_hours()
        since = self._last_seq("pi.decision")
        lines = [
            "## Charter", compact(charter.to_dict(), 3000),
            "## Time",
            (f"{elapsed:.2f} of {charter.deadline_hours:g} hours used "
             f"({max(0.0, charter.deadline_hours - elapsed):.2f} left)."),
            "## Work orders",
        ]
        for oid, s in self.ledger.orders().items():
            lines.append(f"- {oid} [{s.order.role}/{s.order.executor}] status={s.status} "
                         f"attempts={s.attempts} "
                         f"used={s.used_seconds / 60:.1f}/{s.order.limits.wall_seconds / 60:.0f}"
                         f" min tokens={s.usage.total_tokens}")
            lines.append(f"  goal: {s.order.goal[:400]}")
            if s.result:
                lines.append(f"  result: {s.result.summary[:1200]}")
                if s.result.deliverables:
                    lines.append(f"  deliverables: {', '.join(s.result.deliverables)[:400]}")
                if s.result.error:
                    lines.append(f"  error: {s.result.error[:400]}")
        if not self.ledger.orders():
            lines.append("- (none yet)")
        lines.append("## Questions to the user")
        replies = {e.payload["qid"]: e.payload for e in self._events("user.reply")}
        for e in self._events("user.question"):
            r = replies.get(e.payload["qid"])
            answer = ("(open)" if r is None else
                      f"{r['answer']}{' (default taken: no reply in time)' if r['auto'] else ''}")
            text = e.payload.get("asked", e.payload["text"])
            lines.append(f"- {e.payload['qid']} [{e.payload['kind']}] {text[:300]} -> {answer}")
        lines.append("## Messages from the user")
        msgs = self._events("user.message")
        lines += [f"- [{e.ts:%Y-%m-%d %H:%M} UTC]{' NEW' if e.seq > since else ''}"
                  f"{' (urgent)' if e.payload.get('now') else ''} {e.payload['text'][:1500]}"
                  for e in msgs] or ["- (none)"]
        replies_out = self._events("pi.message")
        if replies_out:
            lines.append("## Your earlier messages to the user")
            lines += [f"- {e.payload['text'][:600]}" for e in replies_out[-5:]]
        degraded = self._events("undermind.degraded")
        if degraded:
            last = degraded[-1]
            detail = str(last.payload.get("detail"))[:200]
            lines += ["## Literature tools",
                      (f"Undermind degraded {len(degraded)} time(s); latest at {last.ts:%H:%M} "
                       f"UTC: {last.payload['reason']} ({detail}). Agents fall back to the "
                       "`literature` server (OpenAlex, Semantic Scholar, arXiv).")]
        held = [e for e in self._events("lab.question_held") if e.seq > since]
        if held:
            lines.append("## Questions held back by the platform (fix, then ask again)")
            lines += [f"- {e.payload['qid']}: {p}" for e in held for p in e.payload["problems"]]
        reports = self.kernel.project.workspace / "reports"
        files = sorted(p.name for p in reports.iterdir()) if reports.is_dir() else []
        lines += ["## Files", f"workspace: {self.kernel.project.workspace}",
                  f"reports: {', '.join(files) or '(none)'}",
                  "## Spend", *self._spend_lines(charter), "## Why you were woken"]
        wake = [e for e in self.ledger.events() if e.seq > since and e.kind in WAKE
                or e.seq > since and e.kind in ("lab.error", "lab.order_rejected")]
        lines += [f"- {e.kind} {e.order_id or ''} {compact(_brief(e.payload), 300)}"
                  for e in wake] or ["- (re-check)"]
        return "\n".join(lines)


_FLOAT_FIELDS = {"budget_usd", "deadline_hours", "approval_timeout_hours"}


def _change_charter(charter: Charter, changes: Sequence[Mapping[str, str]]
                    ) -> tuple[Charter, list[dict[str, str]]]:
    data = charter.to_dict()
    rejected = []
    for change in changes:
        name, value = change["field"], change["value"].strip()
        try:
            if name in _FLOAT_FIELDS:
                data[name] = float(value)
            elif name == "max_deep_searches":
                data[name] = int(value)
            elif name == "approval_points":
                points = [p.strip() for p in value.split(",") if p.strip()]
                if not set(points) <= set(APPROVAL_POINTS):
                    raise ValueError(value)
                data[name] = points
            elif name == "on_timeout" and value not in ("default", "suspend"):
                raise ValueError(value)
            elif name in data:
                data[name] = value
            else:
                raise ValueError(name)
        except ValueError:
            rejected.append({"field": name, "value": value})
    return Charter.from_dict(data), rejected


def _exclusive(order: WorkOrder) -> bool:
    return order.role in EXCLUSIVE_ROLES or order.executor.startswith("ai-scientist")


def _confirms_finish(e: Event) -> bool:
    return (e.kind == "user.reply" and str(e.payload.get("qid", "")).endswith("-finish")
            and str(e.payload.get("answer", "")).strip() == FINISH_YES)


def _brief(payload: Mapping[str, Any]) -> Any:
    if "result" in payload:
        return {"status": payload["result"]["status"]}
    if "charter" in payload:
        return "charter set"
    return payload


def question_text(qid: str, payload: Mapping[str, Any]) -> str:
    options = "".join(f"\n  - {o}" for o in payload.get("options", []))
    return (f"[{qid}] {payload['text']}{options}\nDefault: {payload['default']}\n"
            f"Answer with: tarlab reply <project> {qid} \"<answer>\"")
