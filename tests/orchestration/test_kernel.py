import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Attempt, EventSink, Limits, Usage, WorkOrder, WorkResult
from tar_lab.integrations.cli_agents.codex import CodexExecutor
from tar_lab.orchestration.kernel import Kernel, Project


def order(oid: str = "wo-1", executor: str = "fake", **limits: Any) -> WorkOrder:
    return WorkOrder(oid, "engineer", executor, "write hello.py", ("hello.py",),
                     "prints 5050", Limits(**{"wall_seconds": 60, **limits}))


@dataclass
class FakeExecutor:
    results: list[WorkResult]
    name: str = "fake"
    supports_resume: bool = True
    seen: list[Attempt] = field(default_factory=list)

    session_totals: dict[str, Usage] = field(default_factory=dict)

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        self.seen.append(attempt)
        emit("order.session", {"session_id": "sess-1"})
        return self.results.pop(0)

    def session_usage(self, session_id: str) -> Usage | None:
        return self.session_totals.get(session_id)


def make_kernel(tmp_path: Path, executor: Any, **kw: Any) -> Kernel:
    project = Project(tmp_path / "proj")
    project.init()
    return Kernel(project, Ledger(project.ledger_path), {executor.name: executor}, **kw)


def test_done_order_is_recorded_with_snapshot_and_usage(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("done", "ok", ("hello.py",), usage=Usage(10, 5, 2),
                                  session_id="sess-1")])
    seen: list[str] = []
    kernel = make_kernel(tmp_path, ex, on_attention=lambda kind, _s: seen.append(kind))
    kernel.submit(order())
    assert kernel.run_pending() == ["wo-1"]
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.attempts == 1
    assert state.usage.total_tokens == 12 and state.session_id == "sess-1"
    assert state.snapshot and len(state.snapshot) == 40
    assert seen == ["order.finished"]
    assert kernel.run_pending() == []  # terminal orders are not rerun


def test_duplicate_and_unknown_executor_rejected(tmp_path: Path) -> None:
    kernel = make_kernel(tmp_path, FakeExecutor([]))
    kernel.submit(order())
    with pytest.raises(ValueError, match="already exists"):
        kernel.submit(order())
    with pytest.raises(ValueError, match="unknown executor"):
        kernel.submit(order("wo-2", executor="nope"))


def test_over_budget_raises_attention(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("done", "ok", usage=Usage(900, 0, 200), session_id="s")])
    seen: list[str] = []
    kernel = make_kernel(tmp_path, ex, on_attention=lambda kind, _s: seen.append(kind))
    kernel.submit(order(max_tokens=1000))
    kernel.run_pending()
    assert seen == ["order.finished", "order.over_budget"]


def test_recovery_kills_stray_agent_and_resumes_session(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("done", "resumed")])
    kernel = make_kernel(tmp_path, ex)
    kernel.submit(order())
    # Simulate a kernel that died mid-attempt, leaving its agent running.
    stray = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             start_new_session=True)
    led = kernel.ledger
    led.append("order.started", "wo-1", {"attempt": 1, "snapshot": None})
    led.append("order.process", "wo-1", {"pid": stray.pid, "pgid": stray.pid})
    led.append("order.session", "wo-1", {"session_id": "sess-0"})
    assert kernel.run_pending() == ["wo-1"]
    assert stray.wait(timeout=15) == -signal.SIGTERM
    assert ex.seen[0].resume_session == "sess-0" and ex.seen[0].number == 2
    kinds = [e.kind for e in led.events("wo-1")]
    assert "order.interrupted" in kinds
    assert led.orders()["wo-1"].status == "done"


def test_recovery_counts_time_until_last_output(tmp_path: Path) -> None:
    kernel = make_kernel(tmp_path, FakeExecutor([WorkResult("done", "ok")]))
    kernel.submit(order())
    started = kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    out = kernel.project.attempt_dir("wo-1", 1)
    out.mkdir(parents=True)
    (out / "events.jsonl").write_text("{}")
    stamp = started.ts.timestamp() + 30
    os.utime(out / "events.jsonl", (stamp, stamp))
    kernel.recover()
    assert kernel.ledger.orders()["wo-1"].used_seconds == pytest.approx(30, abs=0.5)


def test_usage_is_cumulative_per_session(tmp_path: Path) -> None:
    # sess-0 was killed (its spend comes from the agent's records), sess-1 then finished.
    ex = FakeExecutor([WorkResult("crashed", "bad", session_id="sess-0"),
                       WorkResult("done", "fresh", usage=Usage(30, 0, 3), session_id="sess-1")],
                      session_totals={"sess-0": Usage(50, 0, 5)})
    kernel = make_kernel(tmp_path, ex)
    kernel.submit(order())
    kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    kernel.ledger.append("order.session", "wo-1", {"session_id": "sess-0"})
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.usage == Usage(80, 0, 8)


def test_resumed_session_total_is_not_double_counted(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("done", "ok", usage=Usage(175, 150, 3), session_id="s")],
                      session_totals={"s": Usage(50, 0, 1)})
    kernel = make_kernel(tmp_path, ex)
    kernel.submit(order())
    kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    kernel.ledger.append("order.session", "wo-1", {"session_id": "s"})
    kernel.run_pending()
    assert kernel.ledger.orders()["wo-1"].usage == Usage(175, 150, 3)


def test_failed_resume_falls_back_to_fresh_attempt(tmp_path: Path) -> None:
    ex = FakeExecutor([WorkResult("crashed", "bad", error="no session"),
                       WorkResult("done", "fresh")])
    kernel = make_kernel(tmp_path, ex)
    kernel.submit(order())
    kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    kernel.ledger.append("order.session", "wo-1", {"session_id": "sess-0"})
    kernel.run_pending()
    assert [a.resume_session for a in ex.seen] == ["sess-0", None]
    assert kernel.ledger.orders()["wo-1"].status == "done"


def test_attempt_cap_and_time_already_spent(tmp_path: Path) -> None:
    kernel = make_kernel(tmp_path, FakeExecutor([]))
    kernel.submit(order(max_attempts=1))
    kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "limit_exceeded" and state.result is not None
    assert "attempts" in state.result.summary


FAKE_CODEX = textwrap.dedent("""\
    #!{python}
    import json, os, sys, time
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    out = args[args.index("-o") + 1]
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps(args) + "\\n")
    print(json.dumps({{"type": "thread.started", "thread_id": "t-123"}}), flush=True)
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(60)
    if mode == "ok":
        json.dump({{"status": "done", "summary": "printed 5050", "deliverables": ["hello.py"],
                   "sources": []}}, open(out, "w"))
    print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 100,
          "cached_input_tokens": 40, "output_tokens": 7}}}}), flush=True)
    if mode == "noreport":
        print("boom", file=sys.stderr)
        sys.exit(1)
""")


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "codex"
    script.write_text(FAKE_CODEX.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    return script


def calls(tmp_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "calls.log").read_text().splitlines()]


def test_codex_executor_parses_report_session_and_usage(tmp_path: Path,
                                                        fake_codex: Path) -> None:
    kernel = make_kernel(tmp_path, CodexExecutor(binary=str(fake_codex)))
    kernel.submit(order(executor="codex"))
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.result is not None
    assert state.result.summary == "printed 5050" and state.session_id == "t-123"
    assert state.usage == Usage(100, 40, 7)
    args = calls(tmp_path)[0]
    assert args[0] == "exec" and "resume" not in args and args[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    for feature in ("apps", "plugins", "remote_plugin"):  # no tools outside our policy
        assert args[args.index(feature) - 1] == "--disable"
    prompt = (kernel.project.attempt_dir("wo-1", 1) / "prompt.md").read_text()
    assert "write hello.py" in prompt and "Wall time: 1 min" in prompt


def test_project_paths_are_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    project = Project(Path("proj"))
    assert project.attempt_dir("wo-1", 1).is_absolute() and project.workspace.is_absolute()


def test_codex_executor_resume_command(tmp_path: Path, fake_codex: Path) -> None:
    kernel = make_kernel(tmp_path, CodexExecutor(binary=str(fake_codex)))
    kernel.submit(order(executor="codex"))
    kernel.ledger.append("order.started", "wo-1", {"attempt": 1})
    kernel.ledger.append("order.session", "wo-1", {"session_id": "t-123"})
    kernel.run_pending()
    args = calls(tmp_path)[0]
    assert args[:2] == ["exec", "resume"] and args[-2:] == ["t-123", "-"]
    assert "-C" not in args


def test_codex_session_usage_reads_rollout(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    day = tmp_path / "sessions" / "2026" / "11" / "17"
    day.mkdir(parents=True)
    lines = [{"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": n, "cached_input_tokens": 0,
                              "output_tokens": 1}}}} for n in (10, 49)]
    (day / "rollout-2026-11-17T16-28-47-abc.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n")
    ex = CodexExecutor()
    assert ex.session_usage("abc") == Usage(49, 0, 1)
    assert ex.session_usage("missing") is None


def test_codex_executor_crash_reports_stderr(tmp_path: Path, fake_codex: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "noreport")
    kernel = make_kernel(tmp_path, CodexExecutor(binary=str(fake_codex)))
    kernel.submit(order(executor="codex"))
    kernel.run_pending()
    result = kernel.ledger.orders()["wo-1"].result
    assert result is not None and result.status == "crashed" and result.error == "boom"


def test_codex_executor_hard_wall_time_limit(tmp_path: Path, fake_codex: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "sleep")
    kernel = make_kernel(tmp_path, CodexExecutor(binary=str(fake_codex)))
    kernel.submit(order(executor="codex", wall_seconds=2))
    start = time.monotonic()
    kernel.run_pending()
    assert time.monotonic() - start < 15
    led = kernel.ledger
    state = led.orders()["wo-1"]
    assert state.status == "limit_exceeded" and state.session_id == "t-123"
    kinds = [e.kind for e in led.events("wo-1")]
    assert "order.half_time" in kinds and "order.process" in kinds
    pgid = next(e.payload["pgid"] for e in led.events("wo-1") if e.kind == "order.process")
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)




def test_prompt_includes_role_guide_and_limits() -> None:
    from tar_lab.agents.roles import render_prompt

    prompt = render_prompt(order())
    assert "## How to work" in prompt and "CUDA 12.8" in prompt and "15 minutes" in prompt
    assert "interrupted" in render_prompt(order(), resume=True)
    assert "## Tools" not in prompt
    for text in (render_prompt(order(), mcp_servers=["undermind"]),
                 render_prompt(order(), resume=True, mcp_servers=["undermind"])):
        assert "`undermind` MCP server is attached" in text and "get_orientation" in text


def test_cancel_pending_and_terminal(tmp_path: Path) -> None:
    kernel = make_kernel(tmp_path, FakeExecutor([WorkResult("done", "ok")]))
    kernel.submit(order())
    kernel.submit(order("wo-2"))
    assert kernel.cancel("wo-2", "not needed")
    kernel.run_pending()
    states = kernel.ledger.orders()
    assert states["wo-1"].status == "done" and states["wo-2"].status == "cancelled"
    assert not kernel.cancel("wo-1", "too late") and not kernel.cancel("missing", "x")


def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True,
                          text=True).stdout


def test_snapshot_skips_environments_caches_and_large_files(tmp_path: Path) -> None:
    from tar_lab.orchestration import kernel as kernel_module

    ex = FakeExecutor([WorkResult("done", "ok"), WorkResult("done", "ok")])
    kernel = make_kernel(tmp_path, ex)
    ws = kernel.project.workspace
    (ws / "code").mkdir()
    (ws / "code" / "run.py").write_text("print(1)\n")
    (ws / "weird name #1.txt").write_text("small\n")
    for d in (".venv/lib", ".cache/uv", "code/__pycache__"):
        (ws / d).mkdir(parents=True)
        (ws / d / "f.bin").write_bytes(b"x" * 100)
    big = ws / "assets" / "data [v1].h5"
    big.parent.mkdir()
    big.write_bytes(b"\0" * (kernel_module.SNAPSHOT_MAX_FILE_BYTES + 1))
    grows = ws / "results" / "log.txt"  # tracked while small, then grows past the limit
    grows.parent.mkdir()
    grows.write_text("start\n")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "before")
    grows.write_bytes(b"y" * (kernel_module.SNAPSHOT_MAX_FILE_BYTES + 1))

    kernel.submit(order())
    kernel.run_pending()
    tracked = set(_git(ws, "ls-tree", "-r", "--name-only", "HEAD").split("\n")) - {""}
    assert tracked == {"code/run.py", "weird name #1.txt"}
    assert big.exists() and grows.exists()  # skipped, never deleted
    started = next(e for e in kernel.ledger.events("wo-1") if e.kind == "order.started")
    excluded = started.payload["snapshot_excluded"]
    assert excluded["dirs"] == [".cache", ".venv", "code/__pycache__"]
    assert excluded["large_files"] == 2 and {f["path"] for f in excluded["largest"]} == {
        "assets/data [v1].h5", "results/log.txt"}

    big.unlink()  # a file that shrinks or disappears drops out of the exclude list
    kernel.submit(order("wo-2"))
    kernel.run_pending()
    exclude = (ws / ".git" / "info" / "exclude").read_text()
    assert exclude.count("tar-lab snapshot excludes") == 2 and "data" not in exclude
