import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tar_lab.core.workorder import Attempt, Limits, WorkOrder
from tar_lab.integrations.ai_scientist import (
    AIScientistEngine,
    AIScientistExecutor,
    RunRequest,
    format_override,
    load_run,
    task_problems,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = REPO_ROOT / "engines" / "ai-scientist-v3"
EXAMPLE = ENGINE_DIR / "examples" / "circpack"


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("a", True, "a=true"),
        ("a", 3, "a=3"),
        ("a", "codex", "a=codex"),
        ("a", "/x y/z", "a='/x y/z'"),
        ("a", ["x", "y z"], "a=[x,'y z']"),
    ],
)
def test_format_override(key: str, value: object, expected: str) -> None:
    assert format_override(key, value) == expected  # type: ignore[arg-type]


def test_format_override_rejects_quotes() -> None:
    with pytest.raises(ValueError):
        format_override("a", "it's")


def test_run_request_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="set by RunRequest"):
        RunRequest(tmp_path, "r1", overrides={"paths.results_dir": "x"})
    with pytest.raises(ValueError, match="run_id"):
        RunRequest(tmp_path, "a/b")
    with pytest.raises(ValueError, match="max_idea_cycles"):
        RunRequest(tmp_path, "r1", max_idea_cycles=0)


def test_trimmed_engine_runs_the_investigation_cli_natively(tmp_path: Path) -> None:
    codex = tmp_path / "codex-bin"
    codex.write_text("#!/bin/sh\n")
    engine = AIScientistEngine(tmp_path / "engine", tmp_path / "runs",
                               headless_bin=tmp_path / "cli.js", tools={"codex": codex})
    request = RunRequest(tmp_path / "circpack", "r1", model="gpt-x", reasoning_effort="high",
                         max_idea_cycles=2, overrides={"paths.task_python": "/opt/py"})
    cmd = engine.build_command(request)
    run_dir = (tmp_path / "runs" / "circpack" / "r1").resolve()
    assert cmd[:3] == [str(engine.python), "-m", "src.investigation.cli"]
    assert f"paths.results_dir={run_dir}" in cmd and "execution.max_idea_cycles=2" in cmd
    assert "agent.model=gpt-x" in cmd and "agent.reasoning_effort=high" in cmd
    assert "paths.task_python=/opt/py" in cmd and not any("container" in c for c in cmd)
    env = engine.environment()
    assert env["HEADLESS_BIN"] == str(tmp_path / "cli.js")
    assert env["HEADLESS_BILLING"] == "subscription" and "PYTHONPATH" not in env
    assert (tmp_path / "runs" / ".bin" / "codex").resolve() == codex
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "runs" / ".bin")


def test_original_engine_runs_its_pipeline_without_containers(tmp_path: Path) -> None:
    engine = AIScientistEngine(tmp_path / "orig", tmp_path / "runs", pipeline=True)
    request = RunRequest(tmp_path / "circpack", "r1", max_idea_cycles=2)
    cmd = engine.build_command(request)
    assert cmd[1] == "run" and "container.enabled=false" in cmd
    assert "execution.phases=investigation" in cmd
    assert "investigation.execution.max_idea_cycles=2" in cmd
    assert engine.run_dir(request) == tmp_path / "runs" / "circpack" / "codex" / "r1"


def _event(kind: str, ts: str, **fields: object) -> str:
    return json.dumps({"event": kind, "timestamp": ts, "fields": fields})


@pytest.mark.parametrize("nested", [False, True])
def test_load_run_reads_the_investigation_journal(tmp_path: Path, nested: bool) -> None:
    inv = tmp_path / "investigation" if nested else tmp_path
    inv.mkdir(exist_ok=True)
    (inv / "events.jsonl").write_text("\n".join([
        _event("run_config", "2026-11-17T08:00:00.000Z"),
        _event("cost_summary", "2026-11-17T08:04:00.000Z", run_total_cost_usd="0.5"),
        _event("finished", "2026-11-17T08:05:00.000Z", cycles_completed=1),
    ]) + "\n{partial")
    (inv / "best_program_manifest.json").write_text(json.dumps(
        {"combined_score": 2.6, "correct": True, "source_attempt_id": "bootstrap"}))
    summary = load_run(tmp_path)
    assert summary.investigation_dir == inv and summary.completed
    assert summary.estimated_cost_usd == 0.5 and summary.wall_seconds == 300
    assert summary.outcome == "no_winner" and summary.cycles_completed == 1


def test_load_run_tolerates_empty_dir(tmp_path: Path) -> None:
    summary = load_run(tmp_path)
    assert summary.status is None and summary.best_program is None and summary.outcome is None


def test_task_problems_accepts_a_complete_task_and_names_what_is_missing(tmp_path: Path) -> None:
    assert task_problems(EXAMPLE) == []
    (tmp_path / "task.md").write_text("Maximise the score.\n")
    (tmp_path / "program_0").mkdir()
    (tmp_path / "program_0" / "program_spec.toml").write_text(
        'code_root = "src"\n[entrypoint]\npath = "program.py"\n')
    problems = task_problems(tmp_path)
    assert any("evaluate.py" in p for p in problems) and any("capture.py" in p for p in problems)
    assert any("program_0/src/program.py" in p for p in problems)
    assert task_problems(tmp_path / "nope") == [f"{tmp_path / 'nope'} is not a directory"]


def test_executor_runs_in_the_orders_results_root(tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    python = engine_dir / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    # A fake engine: writes the run directory the real one would, then echoes its arguments.
    python.write_text("""#!/bin/sh
for a in "$@"; do case "$a" in paths.results_dir=*) d=${a#paths.results_dir=} ;; esac; done
mkdir -p "$d"
printf '%s\\n' '{"event": "cost_summary", "fields": {"run_total_cost_usd": "0.25"}}' \
  '{"event": "finished", "fields": {"cycles_completed": 1}}' > "$d/events.jsonl"
printf '%s' '{"combined_score": 2.5, "correct": true, "program_dir": "/x/program_1",' \
  ' "entrypoint_path": "src/program.py", "source_attempt_id": "c1"}' \
  > "$d/best_program_manifest.json"
echo "args=$*"
""")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    executor = AIScientistExecutor(AIScientistEngine(engine_dir, tmp_path / "shared"),
                                   model="gpt-x")
    root = tmp_path / "ws" / "engine_runs"
    order = WorkOrder("o1", "engineer", "ai-scientist", "search", (), "score", Limits(60),
                      params={"task_dir": str(tmp_path / "circpack"), "results_root": str(root),
                              "cycles": 4, "reasoning_effort": "high"})
    result = executor.run(Attempt(order, 1, tmp_path / "ws", tmp_path / "att", 60),
                          lambda kind, payload: None)
    args = (tmp_path / "att" / "engine.log").read_text()
    assert result.status == "done" and result.cost_usd == 0.25
    assert str(root) in result.deliverables[0] and not (tmp_path / "shared").exists()
    assert "agent.model=gpt-x" in args and "execution.max_idea_cycles=4" in args
    assert "agent.reasoning_effort=high" in args
    assert "outcome=winner" in result.summary and "/x/program_1/src/program.py" in result.summary


@pytest.mark.skipif(not (ENGINE_DIR / ".venv" / "bin" / "python").exists(),
                    reason="engine environment not installed (uv sync --frozen in engines/ai-scientist-v3)")
def test_real_engine_accepts_the_generated_configuration(tmp_path: Path) -> None:
    engine = AIScientistEngine(ENGINE_DIR, tmp_path / "runs")
    request = RunRequest(EXAMPLE, "check", model="gpt-x", reasoning_effort="medium",
                         max_idea_cycles=2)
    probe = ("import sys; from src.investigation.config_loader import load_config; "
             "cfg = load_config(sys.argv[1:]); "
             "print(cfg.execution.max_idea_cycles, cfg.paths.results_dir, cfg.agent.model)")
    cmd = engine.build_command(request)
    result = subprocess.run([cmd[0], "-c", probe, *cmd[3:]], check=False, cwd=ENGINE_DIR,
                            env=engine.environment(), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["2", str(engine.run_dir(request).resolve()), "gpt-x"]
