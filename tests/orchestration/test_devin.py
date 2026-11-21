import json
import sys
import textwrap
from pathlib import Path

import pytest

from tar_lab.cli import _fallbacks, _tiers
from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Limits, McpServer, Usage, WorkOrder
from tar_lab.integrations.cli_agents import devin as dv
from tar_lab.integrations.cli_agents.devin import DevinExecutor, extract_report
from tar_lab.orchestration.kernel import Kernel, Project


def test_extract_report_takes_last_valid_object() -> None:
    text = ('It printed 5050.{"status": "done", "summary": "first"} more '
            '{"note": 1} {"status": "failed", "summary": "last", "deliverables": ["a"]}')
    assert extract_report(text) == {"status": "failed", "summary": "last",
                                    "deliverables": ["a"]}
    assert extract_report('{"status": "weird", "summary": "x"}') is None
    assert extract_report("no json") is None


FAKE_DEVIN = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = sys.argv[1:]
    log = os.environ["FAKE_LOG"]
    if args[0] == "list":
        print(json.dumps([{{"id": "old", "last_activity_at": 1}}]
                         + ([{{"id": "new-sess", "last_activity_at": 2}}]
                            if os.path.exists(log) else [])))
        sys.exit(0)
    with open(log, "a") as f:
        f.write(json.dumps({{"args": args, "host": "ACP_BACKEND" in os.environ,
                            "order": os.environ.get("TARLAB_ORDER_ID")}}) + "\\n")
    export = args[args.index("--export") + 1]
    json.dump({{"session_id": "new-sess", "final_metrics": {{"total_prompt_tokens": "40",
               "total_cached_tokens": "30", "total_completion_tokens": "2"}}}},
              open(export, "w"))
    print('Done. {{"status": "done", "summary": "ok", "deliverables": ["hello.py"], '
          '"sources": []}}')
""")


def test_devin_executor_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "devin"
    script.write_text(FAKE_DEVIN.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("ACP_BACKEND", "windsurf")
    project = Project(tmp_path / "proj")
    project.init()
    servers = {"literature": McpServer("/py", ("-m", "tar_lab.cli", "literature", "serve",
                                               "--ledger", "/l.sqlite", "--order", "wo-1"),
                                       {"SEMANTIC_SCHOLAR_API_KEY": "secret-key-1234"})}
    kernel = Kernel(project, Ledger(project.ledger_path),
                    {"devin": DevinExecutor(binary=str(script))},
                    mcp_for=lambda order: servers)
    kernel.submit(WorkOrder("wo-1", "designer", "devin", "g", ("hello.py",), "a",
                            Limits(60)))
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.session_id == "new-sess"
    assert state.usage == Usage(40, 30, 2)
    call = json.loads((tmp_path / "calls.log").read_text().splitlines()[0])
    assert call["host"] is False  # host-mode variables are stripped
    assert "-r" not in call["args"] and "--prompt-file" in call["args"]
    assert call["order"] == "wo-1"  # MCP servers read their order from the environment
    config_text = (project.workspace / dv.MCP_CONFIG).read_text()
    assert json.loads(config_text) == {"mcpServers": {"literature": {
        "command": "/py", "args": ["-m", "tar_lab.cli", "literature", "serve",
                                   "--ledger", "/l.sqlite"]}}}
    assert "secret" not in config_text  # env values are inherited, never written
    prompt = next(project.root.rglob("prompt.md")).read_text()
    assert "`literature` MCP server" in prompt


def test_parse_usage() -> None:
    screen = ("\x1b[2J\x1b[1m Usage\x1b[0m\r\n Daily   \u25a0\u25a0  12% used \u00b7 resets in "
              "3h 1m\r\n Weekly  \x1b[32m\u25a0\u25a0\u25a0\x1b[0m  77% used \u00b7 resets in "
              "13h 52m\r\n\r\n No usage yet in this session.\r\n")
    assert dv.parse_usage(screen) == {
        "daily": {"used_percent": 12.0, "resets_in": "3h 1m"},
        "weekly": {"used_percent": 77.0, "resets_in": "13h 52m"}}
    assert dv.parse_usage("nothing here") == {}


def test_tiers_and_fallbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executors = {"codex": object(), "devin": object()}
    fake = tmp_path / "devin"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("TARLAB_DEVIN_BIN", str(fake))
    monkeypatch.delenv("TARLAB_TIERS", raising=False)
    assert _tiers(executors) == {"high": "devin", "medium": "codex", "low": "codex"}
    monkeypatch.setenv("TARLAB_TIERS", "high=codex,low=devin")
    assert _tiers(executors) == {"high": "codex", "medium": "codex", "low": "devin"}
    monkeypatch.setenv("TARLAB_TIERS", "high=nope")
    with pytest.raises(SystemExit):
        _tiers(executors)
    monkeypatch.delenv("TARLAB_TIERS")
    monkeypatch.delenv("TARLAB_DEVIN_BIN")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(dv, "BUNDLED_DEVIN", tmp_path / "missing")
    assert _tiers(executors)["high"] == "codex"  # no Devin CLI: fall back with a warning
    monkeypatch.delenv("TARLAB_FALLBACK", raising=False)
    assert _fallbacks() == {"devin": "codex"}
    monkeypatch.setenv("TARLAB_FALLBACK", "none")
    assert _fallbacks() == {}
    monkeypatch.setenv("TARLAB_FALLBACK", "codex=devin")
    assert _fallbacks() == {"codex": "devin"}


FAKE_ASK = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = sys.argv[1:]
    log = os.environ["FAKE_LOG"]
    with open(log, "a") as f:
        f.write(json.dumps({{"args": args,
                            "prompt": open(args[args.index("--prompt-file") + 1]).read()}})
                + "\\n")
    json.dump({{"session_id": "s1", "final_metrics": {{"total_prompt_tokens": 9,
               "total_cached_tokens": 1, "total_completion_tokens": 3}}}},
              open(args[args.index("--export") + 1], "w"))
    if "-r" in args:
        print('Fixed: {{"questions": [{{"key": "k", "text": "t"}}]}}')
    else:
        print('Here: {{"questions": [{{"key": "k"}}]}}')
""")


def test_devin_ask_validates_and_corrects_once(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "devin"
    script.write_text(FAKE_ASK.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    item = {"type": "object", "required": ["key", "text"],
            "properties": {"key": {"type": "string"}, "text": {"type": "string"}}}
    schema = {"type": "object", "required": ["questions"],
              "properties": {"questions": {"type": "array", "items": item}}}
    answer, usage = DevinExecutor(binary=str(script)).ask(
        "Ask the user.", schema, cwd=tmp_path, log_dir=tmp_path / "log")
    assert answer == {"questions": [{"key": "k", "text": "t"}]} and usage == Usage(9, 1, 3)
    first, second = (json.loads(line) for line in (tmp_path / "calls.log").read_text()
                     .splitlines())
    assert first["args"][first["args"].index("--permission-mode") + 1] == "auto"  # read-only
    assert '"required": ["questions"]' in first["prompt"] and "-r" not in first["args"]
    assert second["args"][-2:] == ["-r", "s1"]
    assert "'text' is a required property (at questions/0)" in second["prompt"]


def test_devin_ask_raises_without_answer(tmp_path: Path) -> None:
    script = tmp_path / "devin"
    script.write_text(f"#!{sys.executable}\nimport sys\nprint('Weekly usage limit reached',"
                      " file=sys.stderr)\nsys.exit(1)\n")
    script.chmod(0o755)
    with pytest.raises(RuntimeError, match="usage limit"):
        DevinExecutor(binary=str(script)).ask("q", {"type": "object"}, cwd=tmp_path,
                                              log_dir=tmp_path / "log")


def test_main_agent_defaults_to_devin(monkeypatch: pytest.MonkeyPatch) -> None:
    from tar_lab.cli import _executors, _main_executor

    executors = _executors()
    monkeypatch.setenv("TARLAB_DEVIN_BIN", sys.executable)
    monkeypatch.delenv("TARLAB_MAIN", raising=False)
    assert _main_executor(executors) == "devin"
    monkeypatch.setenv("TARLAB_MAIN", "codex")
    assert _main_executor(executors) == "codex"
    monkeypatch.setenv("TARLAB_MAIN", "ai-scientist")
    with pytest.raises(SystemExit):
        _main_executor(executors)
