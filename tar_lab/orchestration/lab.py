"""Project loop: runs work orders, wakes the main agent on ledger events, handles the user.

Everything the loop knows comes from the ledger, so a killed loop resumes where it stopped:
unanswered wake-up events trigger a new decision, and the last decision is re-applied
idempotently in case the loop died halfway through applying it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from tar_lab.agents.main_agent import Decision, MainAgent, Question, compact
from tar_lab.agents.roles import ROLES
from tar_lab.core.charter import Charter
from tar_lab.core.ledger import Event, Ledger
from tar_lab.core.workorder import Limits, Usage, WorkOrder

from .kernel import Kernel

LabStatus = Literal["intake", "working", "waiting", "error", "finished", "suspended"]
WAKE = frozenset({"project.charter", "project.resumed", "order.finished", "user.reply",
                  "order.over_budget", "lab.idle", "lab.deadline"})
REMINDER_HOURS = (1.0, 4.0, 12.0, 24.0)
MAX_ORDER_MINUTES = 90.0
MAX_IDLE_WAKES = 3
MAX_DECISION_ERRORS = 3


class Notifier(Protocol):
    def notify(self, subject: str, body: str) -> None: ...


@dataclass
class ConsoleNotifier:
    def notify(self, subject: str, body: str) -> None:
        print(f"[TAR-Lab] {subject}\n{body}\n", file=sys.stderr)


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

    @property
    def ledger(self) -> Ledger:
        return self.kernel.ledger

    # ---- ledger views -------------------------------------------------------------------

    def _events(self, *kinds: str) -> list[Event]:
        return [e for e in self.ledger.events() if not kinds or e.kind in kinds]

    def charter(self) -> Charter | None:
        events = self._events("project.charter")
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

    # ---- the loop -----------------------------------------------------------------------

    def run(self, poll_seconds: float = 30.0,
            sleep: Callable[[float], None] = time.sleep) -> LabStatus:
        while True:
            status = self.step()
            if status in ("finished", "suspended", "intake"):
                return status
            if status in ("waiting", "error"):
                sleep(poll_seconds)

    def step(self) -> LabStatus:
        status = self.status()
        if status is not None:
            return status
        charter = self.charter()
        assert charter is not None
        self.kernel.recover()
        decisions = self._events("pi.decision")
        if decisions:
            self._apply(decisions[-1], charter)
            if (status := self.status()) is not None:
                return status
        if self._timers(charter) == "suspended":
            return "suspended"
        self._check_deadline(charter)
        if not self._urgent() and self.kernel.run_next() is not None:
            return "working"
        if self._needs_decision():
            return self._decide(charter)
        if self.questions():
            return "waiting"
        return self._idle()

    # ---- internals ----------------------------------------------------------------------

    def _last_seq(self, kind: str) -> int:
        events = self._events(kind)
        return events[-1].seq if events else 0

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
            self.notifier.notify("Deadline reached",
                                 f"{charter.title}: the main agent must now wrap up.")

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
        self.notifier.notify("Project suspended", f"{reason}\nResume with `tarlab resume`.")
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
                self.notifier.notify(f"No answer to {qid}; default taken",
                                     f"{ev.payload['text']}\nDefault: {ev.payload['default']}")
                continue
            sent = reminders.get(qid, 0)
            if sent < len(REMINDER_HOURS) and hours >= REMINDER_HOURS[sent]:
                self.ledger.append("user.reminder", None, {"qid": qid, "number": sent + 1})
                self.notifier.notify(f"Reminder: {qid} is waiting for you",
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
            self.notifier.notify("Main agent error (will retry)", str(exc)[:2000])
            return "error"
        ev = self.ledger.append("pi.decision", None,
                                {"decision": decision.to_dict(), "usage": asdict(usage)})
        self._apply(ev, charter)
        return self.status() or "working"

    def _apply(self, ev: Event, charter: Charter) -> None:
        decision = Decision.from_dict(ev.payload["decision"])
        if decision.finish:
            if not self._events("project.finished"):
                self.ledger.append("project.finished", None, {"summary": decision.final_summary})
                self.notifier.notify(f"Project finished: {charter.title}",
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
            self.notifier.notify(f"{subject}: {charter.title}", question_text(qid, payload))

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
