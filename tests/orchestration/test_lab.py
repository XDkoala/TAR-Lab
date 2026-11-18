import json
import shutil
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


TOPIC = {**APPROVAL, "point": "topic"}


def write_summary(lab: Lab, text: str = "我们要做什么：……") -> None:
    reports = lab.kernel.project.workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "topic_summary.md").write_text(text, encoding="utf-8")


def test_topic_approval_waits_for_summary_and_deep_search(tmp_path: Path) -> None:
    model = FakeModel([decision(ask=[TOPIC]), decision(ask=[TOPIC])])
    lab, sent = make_lab(tmp_path, model)
    lab.ledger.append("undermind.workspace", None, {"workspace_id": "w", "title": "t"})
    lab.step()
    assert not lab.questions() and not any(s.startswith("Approval") for s in sent)
    [held] = lab._events("lab.question_held")
    assert len(held.payload["problems"]) == 2

    write_summary(lab)
    lab.ledger.append("undermind.call", "o1-1-researcher",
                      {"tool": "launch_deep_search", "is_error": False})
    lab.step()  # the hold woke the main agent, which asks again
    assert "Questions held back" in model.prompts[-1] and "topic_summary.md" in model.prompts[-1]
    [(qid, q)] = lab.questions().items()
    assert q.payload["text"].startswith("我们要做什么") and q.payload["text"].endswith("Pick topic?")
    assert "深度检索" not in q.payload["text"]
    assert "Approval needed: Tiny world model" in sent
    charter = lab.charter()
    assert charter is not None
    assert f"{qid} [approval] Pick topic? -> (open)" in lab.context(charter)


def test_topic_approval_says_when_deep_search_was_impossible(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel([decision(ask=[TOPIC])]))  # no Undermind workspace
    write_summary(lab)
    lab.step()
    [(_, q)] = lab.questions().items()
    assert "没有用深度检索（项目没有连接 Undermind）" in q.payload["text"]


def test_topic_approval_goes_out_with_warnings_after_two_holds(tmp_path: Path) -> None:
    lab, _ = make_lab(tmp_path, FakeModel([decision(ask=[TOPIC])] * 3))
    lab.step()
    lab.step()
    assert not lab.questions() and len(lab._events("lab.question_held")) == 2
    lab.step()
    [(_, q)] = lab.questions().items()
    assert "平台检查未通过" in q.payload["text"] and "topic_summary.md" in q.payload["text"]


@dataclass
class PricedExecutor(FakeExecutor):
    model: str = "gpt-6.1-sol"


def test_budget_warns_holds_orders_and_is_raised_by_the_users_answer(tmp_path: Path) -> None:
    cost16 = Usage(8_000_000, 0, 0)  # $16 at gpt-6.1-sol list prices
    ex = PricedExecutor([WorkResult("done", "ok", usage=cost16, session_id="s")] * 3)
    model = FakeModel([decision([order(), order(), order()]), decision(), decision()])
    lab, sent = make_lab(tmp_path, model, executor=ex)  # budget $30
    lab.step()
    lab.step()
    lab.step()  # two orders ran: $32
    assert lab.step() == "working"  # the budget events wake the main agent
    assert [e.payload["level"] for e in lab._events("lab.budget")] == ["warn", "exhausted"]
    assert "Budget used up" in model.prompts[-1] and "of the $30 budget" in model.prompts[-1]
    assert lab.step() == "waiting" and lab.ledger.orders()["o4-3-researcher"].status == "pending"
    [(qid, q)] = lab.questions().items()
    assert qid.endswith("-budget") and q.payload["default"] == "不追加，用现有结果收尾"
    assert any(s.startswith("Budget used up") for s in sent)

    lab.reply(qid, "追加到 $45")
    lab.step()
    charter = lab.charter()
    assert charter is not None and charter.budget_usd == 45
    assert lab.ledger.orders()["o4-3-researcher"].status == "done"  # released and run


def test_declining_more_budget_keeps_orders_held(tmp_path: Path) -> None:
    ex = PricedExecutor([WorkResult("done", "ok", usage=Usage(16_000_000, 0, 0),
                                    session_id="s")])
    model = FakeModel([decision([order(), order()]), decision(), decision(), decision()])
    lab, _ = make_lab(tmp_path, model, executor=ex)
    lab.step()
    lab.step()  # $32 spent
    lab.step()
    [(qid, _)] = lab.questions().items()
    lab.reply(qid, "不追加，用现有结果收尾")
    lab.step()
    charter = lab.charter()
    assert charter is not None and charter.budget_usd == 30
    assert lab.ledger.orders()["o4-2-researcher"].status == "pending"


@dataclass
class ConcurrentExecutor:
    name: str = "fake"
    supports_resume: bool = True
    release: threading.Event = field(default_factory=threading.Event)
    active: set[str] = field(default_factory=set)
    peak: list[set[str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def run(self, attempt: Any, emit: Any) -> WorkResult:
        with self.lock:
            self.active.add(attempt.order.id)
            self.peak.append(set(self.active))
        self.release.wait(10)
        with self.lock:
            self.active.discard(attempt.order.id)
        return WorkResult("done", "ok", session_id=attempt.order.id)

    def session_usage(self, session_id: str) -> Usage | None:
        return None


def test_orders_run_in_parallel_but_gpu_orders_one_at_a_time(tmp_path: Path) -> None:
    ex = ConcurrentExecutor()
    roles = ["engineer", "engineer", "researcher", "designer", "reviewer"]
    model = FakeModel([decision([order(r) for r in roles])] + [decision()] * 20)
    lab, _ = make_lab(tmp_path, model, executor=ex, threaded=True)
    lab.step()
    drive(lab, lambda: len(ex.active) == 3)
    time.sleep(0.2)
    assert ex.active == {"o4-1-engineer", "o4-3-researcher", "o4-4-designer"}  # cap 3, 1 GPU
    ex.release.set()
    drive(lab, lambda: all(s.status == "done" for s in lab.ledger.orders().values()))
    assert all(sum(o.endswith("engineer") for o in seen) <= 1 for seen in ex.peak)
    lab._join_worker()


def test_main_agent_out_of_quota_waits_instead_of_failing(tmp_path: Path) -> None:
    clock = Clock()
    error = RuntimeError("codex gave no JSON answer: You've hit your usage limit.")
    model = FakeModel([error, decision(message="back")])
    lab, sent = make_lab(tmp_path, model, clock=clock)
    lab.kernel.clock = clock
    assert lab.step() == "waiting"
    assert lab.step() == "waiting" and len(model.prompts) == 3  # not asked again yet
    assert not lab._events("lab.error") and len([s for s in sent if "额度" in s]) == 1
    clock.offset = timedelta(minutes=31)
    lab.step()
    assert [e.payload["text"] for e in lab._events("pi.message")] == ["back"]


def test_platform_questions_follow_the_charters_chat_language(tmp_path: Path) -> None:
    from tar_lab.orchestration.lab import FINISH_YES_EN

    model = FakeModel([decision(finish=True)])
    lab, _ = make_lab(tmp_path, model, confirm_finish=True)
    charter = lab.charter()
    assert charter is not None
    lab.ledger.append("project.charter_changed", None,
                      {"charter": {**charter.to_dict(), "chat_language": "en"}, "changes": []})
    lab.step()
    lab.step()
    [(qid, q)] = lab.questions().items()
    assert q.payload["text"].startswith("The main agent proposes") and FINISH_YES_EN in \
        q.payload["options"]
    lab.reply(qid, FINISH_YES_EN)
    assert lab.step() == "finished"


ENGINE_EXAMPLE = (Path(__file__).resolve().parents[2] / "engines" / "ai-scientist-v3"
                  / "examples" / "circpack")


def steps_until(lab: Lab, cond: Callable[[], bool], limit: int = 12) -> None:
    for _ in range(limit):
        if cond():
            return
        lab.step()
    assert cond()


def test_engine_order_needs_a_complete_task_then_runs_on_the_engine(tmp_path: Path) -> None:
    engine = FakeExecutor([WorkResult("done", "best combined_score=2.6", session_id="s")],
                          name="ai-scientist")
    bad = {**order("engineer", minutes=35), "kind": "engine", "task_dir": "engine_tasks/pack"}
    model = FakeModel([decision([bad]), decision(), decision([bad]), decision()])
    lab, _ = make_lab(tmp_path, model)
    lab.kernel.executors["ai-scientist"] = engine  # type: ignore[index]
    steps_until(lab, lambda: len(model.prompts) == 4)  # intake, charter, two decisions
    [rejected] = lab._events("lab.order_rejected")
    assert "is not ready" in rejected.payload["reason"] and not lab.ledger.orders()
    assert "lab.order_rejected" in model.prompts[-1] and "not a directory" in model.prompts[-1]

    shutil.copytree(ENGINE_EXAMPLE, lab.kernel.project.workspace / "engine_tasks" / "pack")
    lab.say("task ready")
    steps_until(lab, lambda: any(st.status == "done" for st in lab.ledger.orders().values()))
    [st] = lab.ledger.orders().values()
    ws = lab.kernel.project.workspace
    assert st.order.executor == "ai-scientist" and st.order.role == "engineer" and engine.seen
    assert st.order.params["task_dir"] == str((ws / "engine_tasks" / "pack").resolve())
    assert st.order.params["results_root"] == str(ws / "engine_runs")
    assert st.order.params["overrides"]["investigation.execution.max_idea_cycles"] == 3


@dataclass
class TamperingExecutor(FakeExecutor):
    """Runs the final evaluation; the first run also edits a frozen file."""

    tamper: list[bool] = field(default_factory=list)

    def run(self, attempt: Any, emit: Any) -> WorkResult:
        if self.tamper.pop(0):
            (attempt.workspace / "code" / "model.py").write_text("tuned = True\n")
        return super().run(attempt, emit)


def test_final_evaluation_is_sealed_checked_and_rerun_only_with_approval(tmp_path: Path) -> None:
    ex = TamperingExecutor([WorkResult("done", "final: 0.71", session_id="s")] * 2,
                           tamper=[True, False])
    final = {**order("engineer"), "kind": "final_eval", "task_dir": ""}
    model = FakeModel([decision([final]), decision(), decision([final]), decision([final]),
                       decision(), decision()])
    lab, sent = make_lab(tmp_path, model, executor=ex)
    steps_until(lab, lambda: len(model.prompts) == 4)
    [rejected] = lab._events("lab.order_rejected")
    assert "eval/final/protocol.md" in rejected.payload["reason"]

    ws = lab.kernel.project.workspace
    (ws / "eval" / "final").mkdir(parents=True)
    (ws / "eval" / "final" / "protocol.md").write_text("Run eval.py on held-out seeds 100-109.\n")
    (ws / "eval" / "final" / "eval.py").write_text("print('eval')\n")
    (ws / "eval" / "final" / "frozen.txt").write_text("code  # the method\n")
    (ws / "code").mkdir()
    (ws / "code" / "model.py").write_text("tuned = False\n")
    lab.say("eval ready")
    steps_until(lab, lambda: bool(lab.questions()))  # sealed, ran, verified, rerun asked
    [sealed] = lab._events("eval.sealed")
    assert set(sealed.payload["files"]) == {"code/model.py", "eval/final/eval.py",
                                            "eval/final/frozen.txt", "eval/final/protocol.md"}
    oid = sealed.order_id
    assert oid is not None and lab.ledger.orders()[oid].order.params == {"final_eval": True}
    [check] = lab._events("eval.verified")
    assert check.payload["changed"] == ["code/model.py"]
    assert any("封存被改动" in s for s in sent)
    record = json.loads((ws / "results" / "final" / oid / "SEAL.json").read_text())
    assert record["changed"] == ["code/model.py"] and record["final_evaluation_number"] == 1
    assert "CHANGED during the run: code/model.py" in model.prompts[-1]

    [(qid, q)] = lab.questions().items()
    assert qid.endswith("-rerun") and q.payload["default"] == "不允许重跑"
    assert len(lab._events("eval.sealed")) == 1 and len(lab.ledger.orders()) == 1
    lab.reply(qid, "允许重跑（记进账本，论文里披露）")
    steps_until(lab, lambda: len(lab._events("eval.verified")) == 2
                and "rerun allowed by the user" in model.prompts[-1])
    second = lab._events("eval.sealed")[-1]
    assert second.payload["rerun"] is True and second.order_id == q.payload["order_id"]
    assert lab._events("eval.verified")[-1].payload["changed"] == []


def test_declined_rerun_rejects_the_order(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("done", "final", session_id="s")])
    final = {**order("engineer"), "kind": "final_eval", "task_dir": ""}
    model = FakeModel([decision([final]), decision([final]), decision()])
    lab, _ = make_lab(tmp_path, model, executor=ex)
    ws = lab.kernel.project.workspace
    (ws / "eval" / "final").mkdir(parents=True)
    (ws / "eval" / "final" / "protocol.md").write_text("Run it once.\n")
    (ws / "eval" / "final" / "frozen.txt").write_text("eval/final\n")
    steps_until(lab, lambda: bool(lab.questions()))
    [(qid, q)] = lab.questions().items()
    lab.reply(qid, "不允许重跑")
    steps_until(lab, lambda: len(model.prompts) == 5)
    [rejected] = lab._events("lab.order_rejected")
    assert rejected.order_id == q.payload["order_id"] and len(lab._events("eval.sealed")) == 1


def test_final_eval_prompt_explains_the_seal() -> None:
    from tar_lab.agents.roles import render_prompt
    from tar_lab.core.workorder import Limits, WorkOrder

    wo = WorkOrder("o9-1-engineer", "engineer", "codex", "run it", (), "numbers", Limits(600),
                   params={"final_eval": True})
    text = render_prompt(wo)
    assert "## Sealed final evaluation" in text and "results/final/o9-1-engineer/" in text
    assert "## Sealed final evaluation" not in render_prompt(
        WorkOrder("o9-2-engineer", "engineer", "codex", "x", (), "y", Limits(600)))
