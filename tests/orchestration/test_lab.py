from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tar_lab.agents.main_agent import CHARTER_SCHEMA, DECISION_SCHEMA, INTAKE_SCHEMA, MainAgent
from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Usage, WorkResult
from tar_lab.orchestration.kernel import Kernel, Project
from tar_lab.orchestration.lab import Lab

from .test_kernel import FakeExecutor

CHARTER = {"title": "Tiny world model", "goal": "workshop paper", "target_venue": "workshop",
           "constraints": "8 GB GPU", "budget_usd": 30, "deadline_hours": 24,
           "approval_points": ["topic"], "chat_language": "zh", "report_language": "zh",
           "paper_language": "en", "on_timeout": "default", "risk_notes": "small budget"}


def decision(orders: list[dict[str, Any]] | None = None, ask: list[dict[str, Any]] | None = None,
             finish: bool = False) -> dict[str, Any]:
    return {"rationale": "r", "orders": orders or [], "ask_user": ask or [],
            "finish": finish, "final_summary": "all done" if finish else ""}


def order(role: str = "researcher", minutes: float = 30) -> dict[str, Any]:
    return {"role": role, "goal": f"{role} work", "deliverables": ["reports/x.md"],
            "acceptance": "exists", "minutes": minutes, "inputs": []}


APPROVAL = {"kind": "approval", "text": "Pick topic?", "options": ["A", "B"], "default": "A"}


@dataclass
class FakeModel:
    decisions: list[Any] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    def ask(self, prompt: str, schema: dict[str, Any], *, cwd: Path, log_dir: Path,
            timeout: float = 900) -> tuple[dict[str, Any], Usage]:
        self.prompts.append(prompt)
        if schema is INTAKE_SCHEMA:
            return {"questions": [{"key": "budget", "text": "Budget?",
                                   "options": ["$30", "$100"], "default": ""}]}, Usage(5, 0, 1)
        if schema is CHARTER_SCHEMA:
            return dict(CHARTER), Usage(5, 0, 1)
        assert schema is DECISION_SCHEMA
        nxt = self.decisions.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt, Usage(10, 0, 2)


@dataclass
class Clock:
    offset: timedelta = timedelta()

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset


def make_lab(tmp_path: Path, model: FakeModel, results: list[WorkResult] | None = None,
             clock: Callable[[], datetime] | None = None) -> tuple[Lab, list[str]]:
    project = Project(tmp_path / "proj")
    project.init()
    executor = FakeExecutor(results or [])
    kernel = Kernel(project, Ledger(project.ledger_path), {"fake": executor})
    sent: list[str] = []

    class Notes:
        def notify(self, subject: str, body: str) -> None:
            sent.append(subject)

    lab = Lab(kernel, MainAgent(model, project.root), Notes(),
              {"high": "fake", "medium": "fake", "low": "fake"}, clock or Clock())
    lab.start("write a world model paper", ask=lambda q: "", confirm=lambda c: True)
    return lab, sent


def test_intake_records_defaults_and_charter(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel())
    answers = lab._events("intake.answers")[0].payload["answers"]
    assert answers[0]["answer"] == "$30"
    charter = lab.charter()
    assert charter is not None and charter.idea == "write a world model paper"
    assert charter.approval_points == ("topic",) and lab.status() is None


def test_full_cycle_with_approval(tmp_path: Path) -> None:
    model = FakeModel([decision([order()], [APPROVAL]), decision(), decision(finish=True)])
    lab, sent = make_lab(tmp_path, model, [WorkResult("done", "survey ok", session_id="s")])
    assert lab.step() == "working"  # decision 1: one order + approval question
    assert "Approval needed: Tiny world model" in sent
    assert lab.step() == "working"  # runs the survey order
    assert lab.step() == "working"  # decision 2 after order.finished: keep waiting
    assert lab.step() == "waiting"
    (qid,) = lab.questions()
    lab.reply(qid, "B")
    assert lab.step() == "finished"
    assert "-> B" in model.prompts[-1] and "survey ok" in model.prompts[-1]
    assert lab.ledger.orders()["o4-1-researcher"].status == "done"


def test_last_decision_is_reapplied_after_crash(tmp_path: Path) -> None:
    model = FakeModel()
    lab, _ = make_lab(tmp_path, model, [WorkResult("done", "ok", session_id="s")])
    # A loop that died right after recording its decision, before submitting the order.
    lab.ledger.append("pi.decision", None, {"decision": decision([order()], [APPROVAL]),
                                            "usage": {}})
    assert lab.step() == "working"
    assert list(lab.ledger.orders()) == ["o4-1-researcher"] and list(lab.questions()) == ["q4-1"]
    assert lab.ledger.orders()["o4-1-researcher"].status == "done"
    lab._apply(lab._events("pi.decision")[-1], lab.charter())  # type: ignore[arg-type]
    assert len(lab._events("user.question")) == 1  # nothing duplicated


def test_reminders_then_default_on_timeout(tmp_path: Path) -> None:
    clock = Clock()
    lab, sent = make_lab(tmp_path, FakeModel([decision(ask=[APPROVAL]), decision(finish=True)]),
                         clock=clock)
    lab.step()
    clock.offset = timedelta(hours=1.5)
    assert lab.step() == "waiting"
    assert lab.step() == "waiting"  # one reminder per threshold
    assert sent.count("Reminder: q4-1 is waiting for you") == 1
    clock.offset = timedelta(hours=80)
    assert lab.step() == "finished"
    reply = lab._events("user.reply")[0].payload
    assert reply == {"qid": "q4-1", "answer": "A", "auto": True}


def test_timeout_can_suspend_and_resume(tmp_path: Path) -> None:
    clock = Clock()
    lab, _ = make_lab(tmp_path, FakeModel([decision(ask=[APPROVAL]), decision(finish=True)]),
                      clock=clock)
    lab.ledger.append("project.charter", None, {"charter": {**lab.charter().to_dict(),  # type: ignore[union-attr]
                                                            "on_timeout": "suspend"}})
    lab.step()
    clock.offset = timedelta(hours=80)
    assert lab.step() == "suspended" and lab.step() == "suspended"
    lab.resume()
    lab.reply("q5-1", "B")
    assert lab.step() == "finished"


def test_failed_order_preempts_queue(tmp_path: Path) -> None:
    model = FakeModel([decision([order("engineer"), order("analyst")]), decision(finish=True)])
    lab, _ = make_lab(tmp_path, model, [WorkResult("failed", "oom", session_id="s")])
    lab.step()
    lab.step()  # engineer fails
    assert lab.step() == "finished"  # main agent woken before the analyst runs
    assert lab.ledger.orders()["o4-2-analyst"].status == "pending"
    assert "oom" in model.prompts[-1]


def test_repeated_decision_errors_suspend(tmp_path: Path) -> None:
    lab, sent = make_lab(tmp_path, FakeModel([RuntimeError("no JSON")] * 3))
    assert [lab.step() for _ in range(3)] == ["error", "error", "suspended"]
    assert "Project suspended" in sent


def test_idle_main_agent_is_suspended(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel([decision()] * 4))
    statuses = [lab.step() for _ in range(8)]
    assert statuses[-1] == "suspended"
    assert len(lab._events("lab.idle")) == 3


def test_deadline_rejects_new_orders(tmp_path: Path) -> None:
    clock = Clock()
    lab, sent = make_lab(tmp_path, FakeModel([decision([order()]), decision(finish=True)]),
                         clock=clock)
    clock.offset = timedelta(hours=25)
    assert lab.step() == "working"
    assert "Deadline reached" in sent
    assert lab._events("lab.order_rejected") and not lab.ledger.orders()


def test_order_minutes_capped_and_mapped_to_tier_executor(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel([decision([order(minutes=500)])]))
    lab.step()
    state = lab.ledger.orders()["o4-1-researcher"]
    assert state.order.limits.wall_seconds == 90 * 60 and state.order.executor == "fake"


def test_start_twice_rejected(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel())
    with pytest.raises(RuntimeError):
        lab.start("again", ask=lambda q: "", confirm=lambda c: True)
