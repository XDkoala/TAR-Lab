"""Project loop: runs work orders, wakes the main agent on ledger events, handles the user.

Everything the loop knows comes from the ledger, so a killed loop resumes where it stopped:
unanswered wake-up events trigger a new decision, and the last decision is re-applied
idempotently in case the loop died halfway through applying it.
"""

from __future__ import annotations

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
from tar_lab.core.workorder import Limits, Usage, WorkOrder

from .kernel import Kernel
from .notify import ConsoleNotifier, NotificationError, Notifier

LabStatus = Literal["intake", "working", "busy", "waiting", "error", "finished", "suspended"]
WAKE = frozenset({"project.charter", "project.resumed", "order.finished", "order.cancelled",
                  "user.reply", "user.message", "order.over_budget", "lab.idle",
                  "lab.deadline"})
BUSY_POLL_SECONDS = 5.0
REMINDER_HOURS = (1.0, 4.0, 12.0, 24.0)
MAX_ORDER_MINUTES = 90.0
MAX_IDLE_WAKES = 3
MAX_DECISION_ERRORS = 3
LITERATURE_ALERT_HOURS = 6.0


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
    _worker: threading.Thread | None = field(default=None, init=False, repr=False)
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
        busy = self._worker is not None and self._worker.is_alive()
        if not busy and not self._recovered:
            self.kernel.recover()  # only orders left by a previous, dead loop
            self._recovered = True
        decisions = self._events("pi.decision")
        if decisions:
            self._apply(decisions[-1], charter)
            if (status := self.status()) is not None:
                return status
        if self._timers(charter) == "suspended":
            return "suspended"
        self._check_deadline(charter)
        self._literature_alerts(charter)
        if busy:
            return self._decide(charter) if self._urgent_message() else "busy"
        if self._pause_requested():
            return self._suspend("Paused by the user.")
        if not self._urgent() and self._has_pending():
            if not self.threaded:
                self.kernel.run_next()
                return "working"
            self._worker = threading.Thread(target=self.kernel.run_next, daemon=True)
            self._worker.start()
            return "busy"
        if self._needs_decision():
            return self._decide(charter)
        if self.questions():
            return "waiting"
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
        if self._worker is not None:
            self._worker.join()

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
        return any(e.seq > since for e in self._events(*WAKE))

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
            if not self._events("project.finished"):
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
            self.kernel.submit(WorkOrder(
                oid, o["role"], self.tiers[ROLES[o["role"]].tier], o["goal"],
                tuple(o["deliverables"]), o["acceptance"], Limits(int(minutes * 60)),
                inputs=tuple(o["inputs"])))
        asked = {e.payload["qid"] for e in self._events("user.question")}
        for i, q in enumerate(decision.ask_user, 1):
            qid = f"q{ev.seq}-{i}"
            if qid in asked:
                continue
            payload = {"qid": qid, **q}
            self.ledger.append("user.question", None, payload)
            subject = "Approval needed" if q["kind"] == "approval" else "Question"
            self._notify(f"{subject}: {charter.title}", question_text(qid, payload))

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
        tokens = Usage()
        for oid, s in self.ledger.orders().items():
            tokens = tokens + s.usage
            lines.append(f"- {oid} [{s.order.role}] status={s.status} attempts={s.attempts} "
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
            lines.append(f"- {e.payload['qid']} [{e.payload['kind']}] {e.payload['text'][:300]}"
                         f" -> {answer}")
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
        reports = self.kernel.project.workspace / "reports"
        files = sorted(p.name for p in reports.iterdir()) if reports.is_dir() else []
        lines += ["## Files", f"workspace: {self.kernel.project.workspace}",
                  f"reports: {', '.join(files) or '(none)'}",
                  f"## Spend\nwork-order tokens so far: {tokens.total_tokens}",
                  "## Why you were woken"]
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
