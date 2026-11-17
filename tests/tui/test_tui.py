import io
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from tar_lab.cli import main as cli_main
from tar_lab.core.ledger import Ledger
from tar_lab.core.pricing import estimate_usd
from tar_lab.core.workorder import Limits, Usage, WorkOrder, WorkResult
from tar_lab.orchestration.kernel import Project
from tar_lab.tui import render
from tar_lab.tui.activity import ActivityTail
from tar_lab.tui.app import Tui
from tar_lab.tui.state import ProjectState

CHARTER = {"idea": "idea", "title": "小型世界模型", "goal": "workshop paper",
           "target_venue": "workshop", "constraints": "8 GB GPU", "budget_usd": 20,
           "deadline_hours": 24, "approval_points": ["topic"], "max_deep_searches": 2}
QUESTION = {"qid": "q9-1", "kind": "approval", "text": "批准候选 A？",
            "options": ["批准", "暂不批准"], "default": "暂不批准"}


def _order(oid: str, role: str = "engineer") -> WorkOrder:
    return WorkOrder(oid, role, "codex", f"{role} goal", ("reports/x.md",), "ok", Limits(600))


def _item(kind: str, status: str, **fields: Any) -> str:
    event = "item.started" if status == "in_progress" else "item.completed"
    return json.dumps({"type": event, "item": {"id": fields.pop("id"), "type": kind,
                                               "status": status, **fields}})


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = Project(tmp_path / "proj")
    p.init()
    ledger = Ledger(p.ledger_path)
    ledger.append("project.charter", None, {"charter": CHARTER,
                                             "usage": {"input_tokens": 1000}})
    ledger.append("order.created", "o1-1-researcher",
                  {"order": _order("o1-1-researcher", "researcher").to_dict()})
    ledger.append("order.started", "o1-1-researcher", {"attempt": 1})
    result = WorkResult("done", "调研完成", usage=Usage(2_000_000, 1_000_000, 10_000),
                        session_id="s1", wall_seconds=300)
    ledger.append("order.finished", "o1-1-researcher", {"result": result.to_dict()})
    ledger.append("order.created", "o5-1-engineer", {"order": _order("o5-1-engineer").to_dict()})
    ledger.append("order.started", "o5-1-engineer", {"attempt": 1})
    ledger.append("order.process", "o5-1-engineer", {"pgid": 4242})
    ledger.append("user.question", None, QUESTION)
    ledger.append("user.message", None, {"text": "将预算提高到 $50", "now": False})
    ledger.close()
    d = p.attempt_dir("o5-1-engineer", 1)
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("\n".join([
        _item("command_execution", "completed", id="i1",
              command="/bin/bash -lc 'nvidia-smi\nexit 0'", exit_code=0),
        _item("file_change", "completed", id="i2",
              changes=[{"path": str(p.workspace / "code/run.py"), "kind": "add"}]),
        _item("command_execution", "in_progress", id="i3",
              command="/bin/bash -lc \"python code/run.py --steps 10\""),
    ]) + "\n")
    return p.root


def _state(root: Path) -> ProjectState:
    return ProjectState(root, {"codex": "gpt-6.1-sol"}, "gpt-6.1-sol")


def test_activity_tail_is_incremental_and_skips_partial_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(_item("command_execution", "in_progress", id="a",
                          command="/bin/bash -lc 'ls -la'") + "\n" + '{"type": "item.comp')
    tail = ActivityTail(path)
    steps = tail.poll()
    assert [(s.kind, s.text, s.status) for s in steps] == [("command", "ls -la", "running")]
    with path.open("w") as f:  # the agent finishes the half-written line and the command
        f.write(_item("command_execution", "in_progress", id="a",
                      command="/bin/bash -lc 'ls -la'") + "\n")
        f.write(_item("command_execution", "failed", id="a", command="ls -la",
                      exit_code=1) + "\n")
        f.write(_item("mcp_tool_call", "completed", id="b", server="undermind",
                      tool="search_papers", arguments={"query": "world models"}) + "\n")
    tail = ActivityTail(path)
    assert [(s.text, s.status) for s in tail.poll()] == [
        ("ls -la", "failed"), ("undermind.search_papers(world models)", "ok")]


def test_snapshot_phase_cost_agents_and_gpu(project: Path) -> None:
    from tar_lab.tui.system import GpuInfo

    state = _state(project)
    view = state.snapshot(GpuInfo(30, 2048, 8151, {4242: 1536}))
    assert view.title == "小型世界模型" and view.phase == "approval"
    assert list(view.questions) == ["q9-1"] and view.unread == 1
    assert view.deep_max == 2 and view.deadline_h == 24 and view.budget_usd == 20
    [engineer] = view.agents
    assert engineer.id == "o5-1-engineer" and engineer.status == "running"
    assert engineer.gpu_mib == 1536 and engineer.model == "gpt-6.1-sol"
    assert [s.text for s in engineer.steps] == [
        "nvidia-smi …", "add code/run.py", "python code/run.py --steps 10"]
    expected = (estimate_usd("gpt-6.1-sol", Usage(2_000_000, 1_000_000, 10_000)) or 0) + \
        (estimate_usd("gpt-6.1-sol", Usage(1000)) or 0)
    assert view.spent_usd == pytest.approx(expected) and expected == pytest.approx(2.202)
    state.close()


def test_main_agent_shows_while_deciding(project: Path) -> None:
    d = project / "pi" / "decision-001"
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("p")
    (d / "events.jsonl").write_text(_item("command_execution", "in_progress", id="x",
                                          command="cat reports/topic_report.md") + "\n")
    state = _state(project)
    agents = state.snapshot().agents
    assert agents[0].id == "main" and agents[0].steps[0].text == "cat reports/topic_report.md"
    (d / "answer.json").write_text("{}")
    assert state.snapshot().agents[0].id != "main"
    state.close()


def test_clip_counts_wide_characters() -> None:
    assert render.clip("一二三四五", 6) == "一二…"
    assert render.clip("abc", 6) == "abc"


def test_panel_and_toolbar_render(project: Path) -> None:
    state = _state(project)
    view = state.snapshot()
    text = "".join(t for _, t in render.panel(view, 0, 80, verbose=True))
    assert "工程师 · o5-1-engineer" in text and "python code/run.py" in text
    assert "1 条留言" in text and text.endswith("› ")
    bar = "".join(t for _, t in render.toolbar(view, runner_alive=False, width=80))
    assert "等待你审批" in bar and "/run" in bar and "$20" in bar
    state.close()


def test_feed_items_render_every_kind(project: Path) -> None:
    state = _state(project)
    console = Console(file=io.StringIO(), width=100)
    orders = state.ledger.orders()
    rendered = [render.feed_item(e, orders) for e in state.ledger.events()]
    for r in rendered:
        if r is not None:
            console.print(r)
    out = console.file.getvalue()  # type: ignore[attr-defined]
    assert "调研员 · o1-1-researcher 完成" in out and "需要你审批 · q9-1" in out
    assert "将预算提高到 $50" in out
    state.close()


@dataclass
class FakeLab:
    ledger: Ledger
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def say(self, text: str, *, now: bool = False) -> None:
        self.calls.append(("say", (text, now)))
        self.ledger.append("user.message", None, {"text": text, "now": now})

    def reply(self, qid: str, answer: str) -> None:
        self.calls.append(("reply", (qid, answer)))
        self.ledger.append("user.reply", None, {"qid": qid, "answer": answer, "auto": False})

    def pause(self) -> None:
        self.calls.append(("pause", None))


@pytest.fixture
def session(project: Path) -> Iterator[tuple[Tui, FakeLab, Any, io.StringIO]]:
    state = _state(project)
    lab = FakeLab(state.ledger)
    out = io.StringIO()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        tui = Tui(state, lambda: lab, Console(file=out, width=120), refresh_seconds=0.05,  # type: ignore[arg-type, return-value]
                  gpu=lambda: None, runners=lambda _: [1])
        yield tui, lab, pipe, out
        tui.close()
    state.close()


def _drive(tui: Tui, pipe: Any, *keys: str) -> None:
    def feed() -> None:
        for k in keys:
            time.sleep(0.3)
            pipe.send_text(k)
    t = threading.Thread(target=feed, daemon=True)
    t.start()
    tui.run(patch=False)
    t.join(5)


def test_chat_urgent_and_commands(session: tuple[Tui, FakeLab, Any, io.StringIO]) -> None:
    tui, lab, pipe, out = session
    _drive(tui, pipe, "换成 MiniGrid\r", "马上停下!\r", "先看日志", "\x1b", "/pause\r",
           "/nope\r", "/quit\r")
    assert lab.calls == [("say", ("换成 MiniGrid", False)), ("say", ("马上停下", True)),
                         ("say", ("先看日志", True)), ("pause", None)]
    text = out.getvalue()
    assert "TAR-Lab" in text and "需要你审批 · q9-1" in text  # header and history
    assert "› 先看日志  [紧急]" in text  # echoed from the ledger, not from the input line
    assert "未知命令 /nope" in text


def test_ctrl_a_answers_the_open_approval(session: tuple[Tui, FakeLab, Any, io.StringIO]
                                          ) -> None:
    tui, lab, pipe, out = session
    _drive(tui, pipe, "\x01", "\r", "/quit\r")  # Ctrl+A, then Enter on the first option
    assert lab.calls == [("reply", ("q9-1", "批准"))]
    assert "你已回答 q9-1：批准" in out.getvalue()


def test_completion_lists_commands_and_files(session: tuple[Tui, FakeLab, Any, io.StringIO]
                                             ) -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from tar_lab.tui.app import TuiCompleter

    tui, *_ = session
    tui.refresh()
    (tui.state.project.workspace / "reports").mkdir()
    (tui.state.project.workspace / "reports" / "survey.md").write_text("# s")
    comp = TuiCompleter(tui)

    def texts(s: str) -> list[str]:
        return [c.text for c in comp.get_completions(Document(s), CompleteEvent())]
    assert "/approve" in texts("/ap") and texts("/agent o5") == ["o5-1-engineer"]
    assert texts("看 @rep") == ["@reports/"] and texts("看 @reports/") == ["@reports/survey.md"]
    assert texts("/report s") == ["survey.md"]


def test_cli_routes_bare_project_to_the_interface(monkeypatch: pytest.MonkeyPatch,
                                                  project: Path) -> None:
    seen: list[Path | None] = []

    def fake_tui(p: Path | None) -> int:
        seen.append(p)
        return 0
    monkeypatch.setattr("tar_lab.cli._tui", fake_tui)
    assert cli_main([str(project)]) == 0 and cli_main([]) == 0 and cli_main(["ui"]) == 0
    assert seen == [project, None, None]


def test_stale_decision_dir_is_not_thinking(project: Path) -> None:
    import os

    d = project / "pi" / "decision-001"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("")
    old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    os.utime(d / "events.jsonl", (old, old))
    state = _state(project)
    assert all(a.id != "main" for a in state.snapshot().agents)
    state.close()


def test_new_project_intake_in_the_terminal(tmp_path: Path) -> None:
    from tar_lab.agents.main_agent import Question
    from tar_lab.core.charter import Charter
    from tar_lab.tui.launch import create_project

    answers: list[str] = []
    created: list[Path] = []

    @dataclass
    class IntakeLab:
        root: Path

        def start(self, idea: str, ask: Any, confirm: Any) -> Charter | None:
            answers.append(idea)
            answers.append(ask(Question("budget", "预算？", ("$20", "$50"), "$20")))
            answers.append(ask(Question("venue", "目标会议？", ("workshop",), "workshop")))
            charter = Charter.from_dict(CHARTER)
            return charter if confirm(charter) else None

    out = io.StringIO()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        # idea, directory (accept default), budget: down + Enter, venue: custom answer, confirm
        for k in ("世界模型论文\r", "\r", "\x1b[B\r", "\x1b[B\r", "ICLR workshop\r", "\r"):
            pipe.send_text(k)
        root = create_project(Console(file=out, width=100), tmp_path,
                              lambda r: IntakeLab(r),  # type: ignore[arg-type, return-value]
                              lambda r: created.append(r), lambda lab, c: None)
    assert answers == ["世界模型论文", "$50", "ICLR workshop"]
    assert root is not None and created == [root] and root.parent == tmp_path / "projects"
    assert "小型世界模型" in out.getvalue()
