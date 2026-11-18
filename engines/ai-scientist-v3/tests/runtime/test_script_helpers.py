"""Agent wrapper shell scripts and their Python support modules (skills, publication guard)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "src" / "scripts"
PYTHON_HELPER = SCRIPTS / "lib" / "python_bin.sh"
AGENT_HELPER = SCRIPTS / "lib" / "agent.sh"
TASK_PATHING_HELPER = SCRIPTS / "lib" / "task_pathing.sh"
AGENT_SCRIPTS = ("idea.sh", "run.sh")


def _bash(script: str, env: dict[str, str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "-c", script], check=False, capture_output=True, text=True,
        env={**os.environ, **env}, **kwargs,
    )


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# task_pathing.sh
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# python_bin.sh resolver
# ---------------------------------------------------------------------------


def _resolve_python(
    tmp_path: Path, *, repo_root: Path | None = None, wrapper_python: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": str(tmp_path / "bin"),
        "REPO_ROOT": str(repo_root or tmp_path / "repo"),
        "PYTHON_HELPER": str(PYTHON_HELPER),
    }
    if wrapper_python is not None:
        environment["WRAPPER_PYTHON"] = wrapper_python
    return _bash('source "$PYTHON_HELPER"; resolve_python_bin', environment)


def test_resolver_honors_explicit_and_relative_wrapper_python(tmp_path: Path) -> None:
    # an explicit WRAPPER_PYTHON is honoured and executable
    explicit_python = _executable(tmp_path / "python dir/python")
    result = _resolve_python(tmp_path, wrapper_python=str(explicit_python))
    assert result.returncode == 0
    assert result.stdout.strip() == str(explicit_python)
    executed = _bash(
        'python_bin="$(source "$PYTHON_HELPER"; resolve_python_bin)"; "$python_bin"',
        {"PATH": str(tmp_path / "bin"), "PYTHON_HELPER": str(PYTHON_HELPER),
         "REPO_ROOT": str(tmp_path / "repo"), "WRAPPER_PYTHON": str(explicit_python)},
    )
    assert executed.returncode == 0

    # a relative WRAPPER_PYTHON is canonicalised
    relative_python = _executable(tmp_path / "relative/python")
    result = _bash(
        'cd "$WORK_DIR"; source "$PYTHON_HELPER"; resolve_python_bin',
        {"PATH": str(tmp_path / "bin"), "PYTHON_HELPER": str(PYTHON_HELPER),
         "REPO_ROOT": str(tmp_path / "repo"), "WORK_DIR": str(tmp_path),
         "WRAPPER_PYTHON": "relative/python"},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(relative_python)


def test_resolver_prefers_virtualenv_then_python3_then_python_and_fails_clearly(
    tmp_path: Path,
) -> None:
    # without any interpreter the failure is clear
    result = _resolve_python(tmp_path)
    assert result.returncode == 127
    assert "Python interpreter not found" in result.stderr

    # python3 is preferred over python on PATH
    python3 = _executable(tmp_path / "bin/python3")
    _executable(tmp_path / "bin/python")
    python3_result = _resolve_python(tmp_path)
    python3.unlink()
    python_result = _resolve_python(tmp_path)
    assert python3_result.stdout.strip() == str(python3)
    assert python_result.stdout.strip() == str(tmp_path / "bin/python")

    # the repository virtualenv is preferred over PATH
    repo_root = tmp_path / "repo"
    venv_python = _executable(repo_root / ".venv/bin/python")
    _executable(tmp_path / "bin/python3")
    result = _resolve_python(tmp_path, repo_root=repo_root)
    assert result.returncode == 0
    assert result.stdout.strip() == str(venv_python)


def test_resolver_rejects_invalid_explicit_interpreters(tmp_path: Path) -> None:
    for kind in ("missing", "directory", "non_executable"):
        invalid_python = tmp_path / f"{kind}-python"
        if kind == "directory":
            invalid_python.mkdir()
        elif kind == "non_executable":
            invalid_python.write_text("#!/bin/sh\n", encoding="utf-8")
        result = _resolve_python(tmp_path, wrapper_python=str(invalid_python))
        assert result.returncode == 127, kind
        assert "Python interpreter is not an executable file" in result.stderr, kind




# ---------------------------------------------------------------------------
# --reasoning-effort handling and concept-figure command construction
# ---------------------------------------------------------------------------


def _run_script(script_name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script_name), *arguments],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("script_name", AGENT_SCRIPTS)
def test_agent_script_validates_exports_and_logs_reasoning_effort(script_name: str) -> None:
    # an invalid reasoning effort is rejected
    completed = _run_script(script_name, "--reasoning-effort", "max")
    assert completed.returncode == 1
    assert (
        "--reasoning-effort must be one of: low, medium, high, xhigh (got: max)"
        in completed.stderr
    )

    # reasoning effort is not supported for gemini
    completed = _run_script(script_name, "--agent", "gemini", "--reasoning-effort", "high")
    assert completed.returncode == 1
    assert "--reasoning-effort is not supported for gemini" in completed.stderr

    # the override is exported and logged
    script = (SCRIPTS / script_name).read_text(encoding="utf-8")
    assert 'AGENT_REASONING_EFFORT=""' in script
    assert "${AGENT_REASONING_EFFORT:-" not in script
    assert "export AGENT_REASONING_EFFORT" in script
    assert "Agent reasoning effort override: $AGENT_REASONING_EFFORT" in script








def _skill_source(root: Path) -> Path:
    src = root / "skills" / "generate-concept-figure"
    pack = src / "drawio-figure" / ".git" / "objects" / "pack"
    pack.mkdir(parents=True)
    (src / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (src / "drawio-figure" / "README.md").write_text("readme\n", encoding="utf-8")
    pack_file = pack / "pack-abc.pack"
    pack_file.write_bytes(b"pack")
    pack_file.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return src




# ---------------------------------------------------------------------------
# content_publication guard
# ---------------------------------------------------------------------------


