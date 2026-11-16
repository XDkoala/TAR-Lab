import json
import stat
from pathlib import Path

import pytest

from tar_lab.integrations.ai_scientist import (
    AIScientistEngine,
    RunRequest,
    TaskSpec,
    format_override,
    load_run,
    write_task_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = REPO_ROOT / "engines" / "ai-scientist-v3"


def test_write_task_dir_creates_files_and_refuses_overwrite(tmp_path: Path) -> None:
    spec = TaskSpec(
        "demo", "# Demo\n", {"evaluate.py": "print(1)\n", "program_0/src/program.py": "x = 1\n"}
    )
    task_dir = write_task_dir(spec, tmp_path)
    assert (task_dir / "task.md").read_text() == "# Demo\n"
    assert (task_dir / "program_0" / "src" / "program.py").read_text() == "x = 1\n"
    assert spec.has_scaffold
    with pytest.raises(FileExistsError):
        write_task_dir(spec, tmp_path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "../x", "description": "d"},
        {"name": "ok", "description": "  "},
        {"name": "ok", "description": "d", "files": {"../evil.py": ""}},
        {"name": "ok", "description": "d", "files": {"/abs.py": ""}},
        {"name": "ok", "description": "d", "files": {"task.md": ""}},
    ],
)
def test_task_spec_rejects_unsafe_input(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        TaskSpec(**kwargs)


def test_task_spec_without_program_0_needs_drafting() -> None:
    assert not TaskSpec("t", "d").has_scaffold


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
    with pytest.raises(ValueError, match="unknown engine phases"):
        RunRequest(tmp_path, "r1", phases=("nope",))
    with pytest.raises(ValueError, match="set by RunRequest"):
        RunRequest(tmp_path, "r1", overrides={"paths.run_dir": "x"})
    with pytest.raises(ValueError, match="run_id"):
        RunRequest(tmp_path, "a/b")


def test_build_command_and_run_dir(tmp_path: Path) -> None:
    engine = AIScientistEngine(tmp_path / "engine", tmp_path / "runs")
    request = RunRequest(
        tmp_path / "circpack", "r1", agent="codex", model="gpt-x",
        phases=("investigation", "paper"), overrides={"investigation.execution.max_idea_cycles": 1},
        dry_run=True,
    )
    cmd = engine.build_command(request)
    assert cmd[:4] == [str(engine.agsci), "run", str((tmp_path / "circpack").resolve()), "--dry-run"]
    assert "execution.phases=investigation,paper" in cmd
    assert f"paths.results_root={(tmp_path / 'runs').resolve()}" in cmd
    assert "agent.model=gpt-x" in cmd and "investigation.execution.max_idea_cycles=1" in cmd
    assert engine.run_dir(request) == tmp_path / "runs" / "circpack" / "codex" / "r1"


def test_run_uses_engine_env_and_captures_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine_dir = tmp_path / "engine"
    agsci = engine_dir / ".venv" / "bin" / "agsci"
    agsci.parent.mkdir(parents=True)
    agsci.write_text('#!/bin/sh\necho "cwd=$PWD pp=${PYTHONPATH:-unset} args=$*"\nexit 3\n')
    agsci.chmod(agsci.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PYTHONPATH", "/ros/python")
    engine = AIScientistEngine(engine_dir, tmp_path / "runs")
    result = engine.run(RunRequest(tmp_path / "t", "r1"), log_path=tmp_path / "logs" / "run.log")
    log = result.log_path.read_text()
    assert result.return_code == 3 and not result.ok
    assert f"cwd={engine_dir}" in log and "pp=unset" in log and "run_id=r1" in log


def test_run_reports_missing_engine(tmp_path: Path) -> None:
    engine = AIScientistEngine(tmp_path / "missing", tmp_path / "runs")
    with pytest.raises(FileNotFoundError, match="uv sync"):
        engine.run(RunRequest(tmp_path / "t", "r1"), log_path=tmp_path / "run.log")


def _event(kind: str, session: str, **fields: object) -> str:
    return json.dumps({"event": kind, "session_id": session, "fields": fields})


def test_load_run_takes_latest_results(tmp_path: Path) -> None:
    (tmp_path / "investigation").mkdir()
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "paper.pdf").write_bytes(b"%PDF")
    (tmp_path / "investigation" / "best_program_manifest.json").write_text(
        json.dumps({"combined_score": 2.6, "correct": True})
    )
    (tmp_path / ".investigation_completion.json").write_text(json.dumps({"outcome": "winner"}))
    (tmp_path / "pipeline_events.jsonl").write_text(
        "\n".join(
            [
                _event("phase_end", "s1", phase_name="investigation", status="completed",
                       return_code=0, outcome="winner"),
                _event("phase_end", "s1", phase_name="ablations", status="degraded", return_code=1),
                _event("pipeline_end", "s1", status="failed", return_code=1),
                _event("cost_summary", "s1", run_total_cost_usd="1.5"),
                _event("phase_end", "s2", phase_name="ablations", status="completed", return_code=0),
                _event("pipeline_end", "s2", status="completed", return_code=0),
                _event("cost_summary", "s2", run_total_cost_usd="5.62"),
                '{"event": "heartbe',
            ]
        )
    )
    summary = load_run(tmp_path)
    assert summary.completed
    assert summary.phases["ablations"].status == "completed"
    assert summary.phases["investigation"].outcome == "winner"
    assert summary.best_program == {"combined_score": 2.6, "correct": True}
    assert summary.investigation_outcome == "winner"
    assert summary.paper_pdf == tmp_path / "paper" / "paper.pdf"
    assert summary.estimated_cost_usd == pytest.approx(5.62)


def test_load_run_tolerates_empty_dir(tmp_path: Path) -> None:
    summary = load_run(tmp_path)
    assert summary.status is None and summary.phases == {} and summary.paper_pdf is None


@pytest.mark.skipif(
    not (ENGINE_DIR / ".venv" / "bin" / "agsci").exists(),
    reason="engine environment not installed (uv sync --frozen in engines/ai-scientist-v3)",
)
def test_real_engine_dry_run(tmp_path: Path) -> None:
    engine = AIScientistEngine(ENGINE_DIR, tmp_path / "runs")
    request = RunRequest(
        ENGINE_DIR / "examples" / "circpack", "tarlab_dry_run", agent="codex", model="gpt-x",
        phases=("investigation",), overrides={"investigation.execution.max_idea_cycles": 1},
        dry_run=True,
    )
    result = engine.run(request, log_path=tmp_path / "dry_run.log", timeout=120)
    log = result.log_path.read_text()
    assert result.ok, log
    assert str(engine.run_dir(request).resolve()) in log
    assert not (tmp_path / "runs").exists()
