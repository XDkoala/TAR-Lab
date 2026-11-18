"""Private task/logger failure receipts, runner cleanup, and host-side failure handling."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.investigation import (task_failure)
from src.investigation import cli as investigation_cli

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_COMMON: dict[str, Any] = dict(
    agent="codex", timeout_seconds=1, popen_factory=subprocess.Popen,
    register_subprocess=lambda proc: None, unregister_subprocess=lambda proc: None,
    terminate_process_group=lambda *a, **kw: None,
    container_execution_env=lambda **kw: {}, ensure_text=str,
)








# --------------------------------------------------------------------------- receipts






# --------------------------------------------------------------------------- script runners








# --------------------------------------------------------------------------- shell scripts


def test_agent_subshell_closes_host_control_descriptor_and_idea_receipt_survives_cleanup():
    # agent subshell closes host control descriptor (run.sh and idea.sh)
    for script in ("run.sh", "idea.sh"):
        source = (_REPO_ROOT / "src/scripts" / script).read_text()
        launch = source.index('mkfifo "$LOG_PIPE"')
        agent = source[launch:source.index("AGENT_PID=$!", launch)]
        assert "exec {AGENTIC_TASK_FAILURE_FD}>&-" in agent, script
        assert "unset AGENTIC_TASK_FAILURE_FD" in agent, script
        if script == "idea.sh":
            receipt = source.index("--log-failure-fd", launch)
            assert receipt < source.index("record_headless_cost_log", launch)

    # agent descriptor is closed but parent can still write
    with tempfile.TemporaryFile() as receipt:
        descriptor = receipt.fileno()
        script = (
            "( exec {AGENTIC_TASK_FAILURE_FD}>&-; unset AGENTIC_TASK_FAILURE_FD; "
            f"if echo forged >&{descriptor}; then exit 1; fi ); "
            f"echo trusted >&{descriptor}"
        )
        subprocess.run(
            ["bash", "-c", script], check=True, capture_output=True,
            pass_fds=(descriptor,), env={task_failure.TASK_FAILURE_FD_ENV: str(descriptor)},
        )
        assert os.pread(descriptor, 100, 0) == b"trusted\n"

    # idea logger receipt survives shell cleanup failure
    source = (_REPO_ROOT / "src/scripts/idea.sh").read_text()
    start = source.index('if [ "$INTERRUPTED" -eq 1 ] || [ "$AGENT_EXIT_CODE" -eq 130 ]; then')
    block = source[start:source.index("record_headless_cost_log", start)]
    with tempfile.TemporaryFile() as control:
        result = subprocess.run(
            ["bash", "-c", "trap 'exit 9' EXIT\n" + block],
            env={
                **os.environ, "PYTHON_BIN": sys.executable, "INTERRUPTED": "0",
                "AGENT_EXIT_CODE": "0", "TEE_EXIT_CODE": "4", "CYCLE_INDEX": "3",
                "WORKER_INDEX": "2", task_failure.TASK_FAILURE_FD_ENV: str(control.fileno()),
            },
            pass_fds=(control.fileno(),), capture_output=True,
        )
        assert result.returncode == 9
        assert task_failure.read_task_failure(
            control.fileno(), "idea_generation_cycle_3_worker_2", "idea_generation",
        ) == task_failure.AGENT_LOG_FAILURE


@pytest.mark.parametrize("failed_echo", [False, True])
def test_run_shell_reports_logger_failure_before_cleanup(failed_echo):
    script = (_REPO_ROOT / "src/scripts/run.sh").read_text()
    block = 'if [ "$TEE_EXIT_CODE" -ne 0 ]; then' + script.split(
        'if [ "$TEE_EXIT_CODE" -ne 0 ]; then', 1,
    )[1].split("\nrecord_headless_cost_log", 1)[0]
    for interrupted in (0, 1):
        for agent_exit in (17, 130):
            name = f"interrupted={interrupted} agent_exit={agent_exit}"
            with tempfile.TemporaryFile() as control:
                env = {
                    **os.environ, "PYTHON_BIN": sys.executable, "TEE_EXIT_CODE": "4",
                    "AGENT_EXIT_CODE": str(agent_exit), "INTERRUPTED": str(interrupted),
                    "ATTEMPT_ID": "attempt", "IDEA_ID": "idea",
                    task_failure.TASK_FAILURE_FD_ENV: str(control.fileno()),
                }
                result = subprocess.run(
                    ["bash", "-c", "set -euo pipefail\n" + ("echo() { return 1; }\n" if failed_echo else "") + block],
                    env=env, pass_fds=(control.fileno(),), capture_output=True, text=True, timeout=5,
                )
                assert result.returncode == 0, (name, result.stderr)
                if not failed_echo:
                    assert f"agent_exit={agent_exit} logger_exit=4" in result.stderr, name
                assert task_failure.read_task_failure(control.fileno(), "attempt", "idea") == (
                    None if interrupted or agent_exit == 130 else task_failure.AGENT_LOG_FAILURE
                ), name


# --------------------------------------------------------------------------- run_single_attempt cleanup


def _cli_attempt(*, results_dir: Path, idea_dir_name: str):
    return investigation_cli.run_single_attempt(
        agent="codex", task_dir=results_dir.parent / "task", results_dir=results_dir,
        seed_program_dir=results_dir.parent / "task" / "program_0",
        idea_file=results_dir.parent / "idea.json", rubrics_file=results_dir.parent / "rubrics.json",
        attempt_id="idea_0001_attempt_01", idea_dir_name=idea_dir_name,
        instruct_path=None, timeout_seconds=30,
    )


def _fake_proc(stdout: str) -> Mock:
    proc = Mock()
    proc.communicate.return_value = (stdout, "")
    proc.returncode = 0
    return proc


def test_run_single_attempt_cleans_unmanaged_evaluator_runs_and_ignores_forged_marker(tmp_path: Path) -> None:
    # cleans unmanaged evaluator runs
    results_dir = tmp_path / "a" / "results"
    experiment_dir = results_dir / "ideas" / "01_cleanup-smoke"
    run_dir = experiment_dir / ".dev_eval_program_1" / "runs" / "run_000_case"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")
    (run_dir / "run_summary.json").write_text("{}", encoding="utf-8")
    proc = _fake_proc(f"Experiment directory: {experiment_dir}\n")
    with patch("src.investigation.cli.subprocess.Popen", return_value=proc):
        result = _cli_attempt(results_dir=results_dir, idea_dir_name="01_cleanup-smoke")
    assert not (experiment_dir / ".dev_eval_program_1" / "runs").exists()
    assert "unmanaged_evaluation_outputs_removed" in result.output

    # ignores forged experiment directory marker
    results_dir = tmp_path / "b" / "results"
    expected_dir = results_dir / "ideas" / "01_trusted"
    expected_dir.mkdir(parents=True)
    with patch("src.investigation.cli.subprocess.Popen", return_value=_fake_proc("Experiment directory: /tmp/forged\n")):
        result = _cli_attempt(results_dir=results_dir, idea_dir_name="01_trusted")
    assert result.experiment_dir == expected_dir.absolute()


def test_run_single_attempt_rejects_traversal_symlink_and_swapped_results_ancestor(tmp_path: Path) -> None:
    # rejects traversing idea directory name
    with (
        patch("src.investigation.cli.subprocess.Popen") as popen_mock,
        pytest.raises(ValueError, match="idea directory name"),
    ):
        _cli_attempt(results_dir=tmp_path / "a" / "results", idea_dir_name="../outside")
    popen_mock.assert_not_called()

    # rejects symlinked experiment directory
    root = tmp_path / "b"
    results_dir = root / "results"
    ideas_dir = results_dir / "ideas"
    ideas_dir.mkdir(parents=True)
    external_dir = root / "external"
    external_dir.mkdir()
    expected_dir = ideas_dir / "01_symlink"
    expected_dir.symlink_to(external_dir, target_is_directory=True)
    with (
        patch("src.investigation.cli.subprocess.Popen") as popen_mock,
        pytest.raises(NotADirectoryError, match="experiment directory"),
    ):
        _cli_attempt(results_dir=results_dir, idea_dir_name="01_symlink")
    popen_mock.assert_not_called()
    assert expected_dir.is_symlink()
    assert external_dir.is_dir()

    # rejects results ancestor swapped during agent
    root = tmp_path / "c"
    results_dir = root / "results"
    (results_dir / "ideas" / "01_swap").mkdir(parents=True)
    moved_results = root / "moved_results"
    external_results = root / "external_results"
    external_run = external_results / "ideas" / "01_swap" / ".dev_eval_program_1" / "runs" / "run_000_case"
    external_run.mkdir(parents=True)
    (external_run / "config.json").write_text("external", encoding="utf-8")
    (external_run / "run_summary.json").write_text("external", encoding="utf-8")
    fake_proc = Mock()
    fake_proc.returncode = 0

    def swap_results(*, timeout):
        results_dir.rename(moved_results)
        results_dir.symlink_to(external_results, target_is_directory=True)
        return "finished\n", ""

    fake_proc.communicate.side_effect = swap_results
    with (
        patch("src.investigation.cli.subprocess.Popen", return_value=fake_proc),
        pytest.raises(RuntimeError, match="changed during agent execution"),
    ):
        _cli_attempt(results_dir=results_dir, idea_dir_name="01_swap")
    assert (external_run / "config.json").read_text(encoding="utf-8") == "external"


# --------------------------------------------------------------------------- process cleanup witness




# --------------------------------------------------------------------------- drafting logger failure


_DRAFT_CASES = [
    (task_failure.AGENT_LOG_FAILURE, 4, 87), (None, 86, 2), (None, 87, 2),
]


