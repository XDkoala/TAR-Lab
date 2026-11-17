import subprocess
import sys
import threading
import time
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
from tar_lab.orchestration.notify import NotificationError

from .test_kernel import FakeExecutor

CHARTER = {"title": "Tiny world model", "goal": "workshop paper", "target_venue": "workshop",
           "constraints": "8 GB GPU", "budget_usd": 30, "deadline_hours": 24,
           "approval_points": ["topic"], "chat_language": "zh", "report_language": "zh",
           "paper_language": "en", "on_timeout": "default", "risk_notes": "small budget"}


def decision(orders: list[dict[str, Any]] | None = None, ask: list[dict[str, Any]] | None = None,
             finish: bool = False, message: str = "", cancel: list[str] | None = None,
             changes: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"rationale": "r", "message_to_user": message, "orders": orders or [],
            "cancel_orders": cancel or [], "ask_user": ask or [],
            "charter_changes": changes or [], "finish": finish,
            "final_summary": "all done" if finish else ""}


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
             clock: Callable[[], datetime] | None = None, executor: Any = None,
             threaded: bool = False, confirm_finish: bool = False) -> tuple[Lab, list[str]]:
    project = Project(tmp_path / "proj")
    project.init()
    executor = executor or FakeExecutor(results or [])
    kernel = Kernel(project, Ledger(project.ledger_path), {"fake": executor})
    sent: list[str] = []

    class Notes:
        def notify(self, subject: str, body: str) -> None:
            sent.append(subject)

    lab = Lab(kernel, MainAgent(model, project.root), Notes(),
              {"high": "fake", "medium": "fake", "low": "fake"}, clock or Clock(), threaded,
              confirm_finish=confirm_finish)
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


def test_notification_failures_are_recorded(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel([decision(ask=[APPROVAL])]))

    class Broken:
        def notify(self, subject: str, body: str) -> None:
            raise NotificationError("smtp down")

    lab.notifier = Broken()
    lab.step()
    failed = lab._events("notify.failed")
    assert failed and failed[0].payload["error"] == "smtp down"


def test_start_twice_rejected(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel())
    with pytest.raises(RuntimeError):
        lab.start("again", ask=lambda q: "", confirm=lambda c: True)


@dataclass
class BlockingExecutor:
    """Runs until released or until its (fake) process group is killed."""

    name: str = "fake"
    supports_resume: bool = True
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def run(self, attempt: Any, emit: Any) -> WorkResult:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                start_new_session=True)
        emit("order.process", {"pid": proc.pid, "pgid": proc.pid})
        self.started.set()
        while proc.poll() is None and not self.release.is_set():
            time.sleep(0.05)
        proc.kill()
        return WorkResult("done" if self.release.is_set() else "crashed", "ran",
                          session_id="s")

    def session_usage(self, session_id: str) -> Usage | None:
        return None


def drive(lab: Lab, until: Callable[[], bool], limit: float = 10) -> None:
    deadline = time.monotonic() + limit
    while not until():
        assert time.monotonic() < deadline, "timed out"
        lab.step()
        time.sleep(0.05)


def test_message_waits_for_running_order_by_default(tmp_path: Path) -> None:
    ex = BlockingExecutor()
    model = FakeModel([decision([order("engineer")]), decision(message="noted", finish=True)])
    lab, sent = make_lab(tmp_path, model, executor=ex, threaded=True)
    lab.step()  # decision 1
    assert lab.step() == "busy" and ex.started.wait(5)
    lab.say("use MiniGrid instead")
    assert lab.step() == "busy" and len(model.prompts) == 3  # nobody woken yet
    ex.release.set()
    drive(lab, lambda: lab.status() == "finished")
    assert "use MiniGrid instead" in model.prompts[-1] and "NEW" in model.prompts[-1]
    assert "Message from the main agent: Tiny world model" in sent


def test_urgent_message_wakes_agent_which_cancels_running_order(tmp_path: Path) -> None:
    ex = BlockingExecutor()
    model = FakeModel([decision([order("engineer")]),
                       decision(message="stopping it", cancel=["o4-1-engineer"]),
                       decision(finish=True)])
    lab, _ = make_lab(tmp_path, model, executor=ex, threaded=True)
    lab.step()
    lab.step()
    assert ex.started.wait(5)
    lab.say("stop that run, wrong env", now=True)
    drive(lab, lambda: lab.status() == "finished")
    state = lab.ledger.orders()["o4-1-engineer"]
    assert state.status == "cancelled" and "stop that run" in model.prompts[3]
    assert [e.payload["text"] for e in lab._events("pi.message")] == ["stopping it"]


def test_stop_cancels_running_order_and_suspends(tmp_path: Path) -> None:
    ex = BlockingExecutor()
    lab, _ = make_lab(tmp_path, FakeModel([decision([order("engineer")])]), executor=ex,
                      threaded=True)
    lab.step()
    lab.step()
    assert ex.started.wait(5)
    assert lab.stop() == ["o4-1-engineer"]
    drive(lab, lambda: lab.status() == "suspended")
    assert lab.ledger.orders()["o4-1-engineer"].status == "cancelled"


def test_pause_after_current_order(tmp_path: Path) -> None:
    model = FakeModel([decision([order(), order("analyst")])])
    lab, _ = make_lab(tmp_path, model, [WorkResult("done", "a", session_id="s")] * 2)
    lab.step()
    lab.step()  # first order runs
    lab.pause()
    assert lab.step() == "suspended"
    assert lab.ledger.orders()["o4-2-analyst"].status == "pending"


def test_charter_change_applied_once_with_validation(tmp_path: Path) -> None:
    changes = [{"field": "budget_usd", "value": "35"},
               {"field": "approval_points", "value": "topic, paper_final"},
               {"field": "on_timeout", "value": "explode"}]
    lab, sent = make_lab(tmp_path, FakeModel([decision(changes=changes, message="ok")]))
    lab.step()
    lab.step()
    charter = lab.charter()
    assert charter is not None and charter.budget_usd == 35
    assert charter.approval_points == ("topic", "paper_final") and charter.on_timeout == "default"
    events = lab._events("project.charter_changed")
    assert len(events) == 1 and events[0].payload["rejected"] == [
        {"field": "on_timeout", "value": "explode"}]
    assert sent.count("Message from the main agent: Tiny world model") == 1


def test_undermind_trouble_is_notified_once_and_given_to_the_main_agent(tmp_path: Path
                                                                         ) -> None:
    model = FakeModel([decision(), decision()])
    lab, sent = make_lab(tmp_path, model)
    for reason in ("quota", "quota", "unavailable"):
        lab.ledger.append("undermind.degraded", "o1-1-researcher",
                          {"reason": reason, "tool": "search_papers", "detail": "quota reached"})
    lab.step()
    lab.step()
    alerts = [s for s in sent if s.startswith("Undermind")]
    assert alerts == ["Undermind 额度用完: Tiny world model", "Undermind 无法连接: Tiny world model"]
    notices = lab._events("lab.literature_notice")
    assert [n.payload["silent"] for n in notices] == [False, True, False]
    assert "## Literature tools" in model.prompts[-1] and "unavailable" in model.prompts[-1]


def test_finish_needs_the_users_confirmation(tmp_path: Path) -> None:
    from tar_lab.orchestration.lab import FINISH_NO, FINISH_YES

    model = FakeModel([decision(finish=True), decision(finish=True)])
    lab, sent = make_lab(tmp_path, model, confirm_finish=True)
    lab.step()
    assert lab.step() == "waiting" and lab.status() is None  # asked, not finished
    [(qid, q)] = lab.questions().items()
    assert qid.endswith("-finish") and q.payload["options"] == [FINISH_YES, FINISH_NO]
    assert "all done" in q.payload["text"] and any(s.startswith("Approval needed") for s in sent)

    lab.reply(qid, "不要结束，再找两个新方向")  # anything but yes: the main agent continues
    lab.step()
    assert lab.step() == "waiting" and lab.status() is None
    assert "再找两个新方向" in model.prompts[-1]
    [(qid2, _)] = lab.questions().items()  # it proposed finishing again, asked again
    assert qid2 != qid
    lab.reply(qid2, FINISH_YES)
    assert lab.step() == "finished"
    assert lab._events("project.finished")[0].payload["summary"] == "all done"


def test_finish_question_times_out_to_the_charter_default(tmp_path: Path) -> None:
    clock = Clock()
    lab, _ = make_lab(tmp_path, FakeModel([decision(finish=True)]), clock=clock,
                      confirm_finish=True)
    lab.step()
    clock.offset = timedelta(hours=73)  # CHARTER: on_timeout "default", default is "yes"
    assert lab.step() == "finished"
