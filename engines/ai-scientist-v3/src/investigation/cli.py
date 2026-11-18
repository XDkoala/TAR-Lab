#!/usr/bin/env python3
"""Investigation entry point (``python -m src.investigation.cli``): idea and attempt loops."""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from src.orchestrator import state as state_store
from src.investigation.config_loader import load_config
from src.investigation.constants import (
    DEFAULT_INTERRUPT_GRACE_SECONDS,
    DEFAULT_MAX_PROGRAM_EVALS_PER_ATTEMPT,
    DEFAULT_RUN_TIMEOUT_GRACE_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS as DEFAULT_RUN_TIMEOUT_SECONDS,
)
from src.investigation.defaults import (
    default_idea_instruction_path,
    default_instruction_path,
    default_results_path,
)
from src.investigation.files import (
    discover_program_indices as discover_program_indices,
    ensure_directory_alias as ensure_directory_alias,
    ensure_task_copy_in_results as ensure_task_copy_in_results,
    extract_markdown_section as extract_markdown_section,
    find_best_program_in_experiment as find_best_program_in_experiment,
    parse_experiment_dir as parse_experiment_dir,
    parse_idea_dir as parse_idea_dir,
    read_task_sections as read_task_sections,
    relative_display_path as relative_display_path,
    resolve_path,
    staged_task_copy_dir as staged_task_copy_dir,
)
from src.investigation.main_entry import run_main_flow
from src.investigation.temp_bootstrap import initialize_runtime_storage
from src.investigation.models import (
    IdeaGenerationResult,
    RunAttemptResult,
)
from src.investigation.task_setup import run_task_setup
from src.investigation import runtime as runtime_utils

_ACTIVE_SUBPROCESSES = runtime_utils.ACTIVE_SUBPROCESSES
_ACTIVE_SUBPROCESSES_LOCK = runtime_utils.ACTIVE_SUBPROCESSES_LOCK
# Keep an explicit module-level export used by tests/integration callers.
_STATE_STORE = state_store


def _reconcile_legacy_idea_local_memory(
    *,
    results_dir: Path,
    cycle_index: int,
    idea_id: str,
    canonical_idea_dir_name: str,
) -> None:
    """Handle reconcile legacy idea local memory logic for this module."""
    from src.investigation import idea_memory

    idea_memory.reconcile_legacy_idea_local_memory(
        results_dir=results_dir,
        cycle_index=cycle_index,
        idea_id=idea_id,
        canonical_idea_dir_name=canonical_idea_dir_name,
        log_event_fn=log_event,
    )


_ensure_text = runtime_utils.ensure_text
format_elapsed = runtime_utils.format_elapsed
log_event = runtime_utils.log_event
log_step_start = runtime_utils.log_step_start
log_step_end = runtime_utils.log_step_end
configure_event_logging = runtime_utils.configure_event_logging
configure_standard_logging = runtime_utils.configure_standard_logging
shutdown_event_logging = runtime_utils.shutdown_event_logging
_register_active_subprocess = runtime_utils.register_active_subprocess
_unregister_active_subprocess = runtime_utils.unregister_active_subprocess
_terminate_process_group = runtime_utils.terminate_active_process_group


def _terminate_all_active_processes(*, grace_seconds: int) -> list[int]:
    """Terminate all active processes."""
    return runtime_utils.terminate_all_active_processes(
        grace_seconds=grace_seconds,
        terminate_process_group=lambda proc: _terminate_process_group(
            proc,
            grace_seconds=grace_seconds,
        ),
    )


def run_idea_generation(
    *,
    agent: str,
    agent_model: str | None = None,
    agent_profile: str | None = None,
    reasoning_effort: str | None = None,
    task_dir: Path,
    results_dir: Path,
    global_memory_dir: Path | None = None,
    conclusions_dir: Path | None = None,
    worker_index: int = 0,
    cycle_index: int,
    output_dir: Path,
    instruct_path: Path,
    timeout_seconds: int,
    timeout_grace_seconds: int = DEFAULT_RUN_TIMEOUT_GRACE_SECONDS,
    validation_feedback: str = "",
) -> IdeaGenerationResult:
    """Run idea generation."""
    from src.investigation import script_runners

    return script_runners.run_idea_generation(
        agent=agent,
        agent_model=agent_model,
        agent_profile=agent_profile,
        reasoning_effort=reasoning_effort,
        task_dir=task_dir,
        results_dir=results_dir,
        global_memory_dir=global_memory_dir,
        conclusions_dir=conclusions_dir,
        worker_index=worker_index,
        cycle_index=cycle_index,
        output_dir=output_dir,
        instruct_path=instruct_path,
        timeout_seconds=timeout_seconds,
        timeout_grace_seconds=timeout_grace_seconds,
        validation_feedback=validation_feedback,
        script_path=resolve_path(Path(__file__).resolve().parents[2] / "src" / "scripts" / "idea.sh"),
        popen_factory=subprocess.Popen,
        register_subprocess=_register_active_subprocess,
        unregister_subprocess=_unregister_active_subprocess,
        terminate_process_group=_terminate_process_group,
        ensure_text=_ensure_text,
    )


def run_single_attempt(
    *,
    agent: str,
    agent_model: str | None = None,
    agent_profile: str | None = None,
    reasoning_effort: str | None = None,
    task_dir: Path,
    results_dir: Path,
    global_memory_dir: Path | None = None,
    conclusions_dir: Path | None = None,
    worker_index: int = 0,
    seed_program_dir: Path,
    idea_file: Path,
    rubrics_file: Path,
    attempt_id: str,
    idea_dir_name: str,
    attempt_dir_name: str | None = None,
    instruct_path: Path | None,
    timeout_seconds: int,
    timeout_grace_seconds: int = DEFAULT_RUN_TIMEOUT_GRACE_SECONDS,
    max_program_evals_per_attempt: int = DEFAULT_MAX_PROGRAM_EVALS_PER_ATTEMPT,
) -> RunAttemptResult:
    """Run single attempt."""
    from src.investigation import script_runners

    return script_runners.run_single_attempt(
        agent=agent,
        agent_model=agent_model,
        agent_profile=agent_profile,
        reasoning_effort=reasoning_effort,
        task_dir=task_dir,
        results_dir=results_dir,
        global_memory_dir=global_memory_dir,
        conclusions_dir=conclusions_dir,
        worker_index=worker_index,
        seed_program_dir=seed_program_dir,
        idea_file=idea_file,
        rubrics_file=rubrics_file,
        attempt_id=attempt_id,
        idea_dir_name=idea_dir_name,
        attempt_dir_name=attempt_dir_name,
        instruct_path=instruct_path,
        timeout_seconds=timeout_seconds,
        timeout_grace_seconds=timeout_grace_seconds,
        max_program_evals_per_attempt=max_program_evals_per_attempt,
        script_path=resolve_path(Path(__file__).resolve().parents[2] / "src" / "scripts" / "run.sh"),
        popen_factory=subprocess.Popen,
        register_subprocess=_register_active_subprocess,
        unregister_subprocess=_unregister_active_subprocess,
        terminate_process_group=_terminate_process_group,
        ensure_text=_ensure_text,
    )


def main(
    *, overrides: Sequence[str] | None = None, _retain_event_logging: bool = False
) -> int:
    """Run the command-line entry point.

    ``overrides`` are Hydra overrides; ``None`` reads them from ``sys.argv[1:]``.
    """
    configure_standard_logging()
    cfg = load_config(None if overrides is None else list(overrides))
    try:
        initialize_runtime_storage()
    except (OSError, ValueError) as exc:
        logging.error("Temporary storage initialization failed: %s", exc)
        return 1
    configure_standard_logging(
        level=logging.INFO if cfg.logging.verbose else logging.WARNING
    )
    assert cfg.paths.results_dir is not None
    logging_results_dir = resolve_path(cfg.paths.results_dir)
    configure_event_logging(
        log_format=cfg.logging.format,
        verbose=cfg.logging.verbose,
        heartbeat_seconds=cfg.logging.heartbeat_seconds,
        results_dir=logging_results_dir,
        worker_index=0,
    )
    main_started_at = time.monotonic()
    try:
        return run_main_flow(
            cfg=cfg,
            main_started_at=main_started_at,
            default_results_path_fn=default_results_path,
            default_instruction_path_fn=default_instruction_path,
            default_idea_instruction_path_fn=default_idea_instruction_path,
            log_event_fn=log_event,
            log_step_start_fn=log_step_start,
            log_step_end_fn=log_step_end,
            run_idea_generation_fn=run_idea_generation,
            run_single_attempt_fn=run_single_attempt,
            run_task_setup_fn=run_task_setup,
            reconcile_legacy_idea_local_memory_fn=_reconcile_legacy_idea_local_memory,
            format_elapsed_fn=format_elapsed,
        )
    finally:
        if not _retain_event_logging:
            shutdown_event_logging()


def _install_cleanup_signal_ignores() -> dict[int, Any]:
    """Temporarily ignore interrupt signals while shutdown cleanup is running."""
    previous: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, signal.SIG_IGN)
        except (AttributeError, OSError, RuntimeError, ValueError):
            continue
    return previous


def _restore_signal_handlers(previous: dict[int, Any]) -> None:
    """Restore previously registered signal handlers."""
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (AttributeError, OSError, RuntimeError, ValueError):
            continue


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run the investigation CLI with interrupt cleanup."""
    interrupted = False
    exit_code = 0
    terminated_pids: list[int] = []
    ignored_signal_handlers: dict[int, Any] = {}
    try:
        exit_code = main(overrides=argv, _retain_event_logging=True)
    except KeyboardInterrupt:
        interrupted = True
        ignored_signal_handlers = _install_cleanup_signal_ignores()
        log_event(
            "interrupted",
            action="terminate_active_processes",
            grace_seconds=DEFAULT_INTERRUPT_GRACE_SECONDS,
        )
        try:
            raw_terminated_pids = _terminate_all_active_processes(
                grace_seconds=DEFAULT_INTERRUPT_GRACE_SECONDS
            )
        except KeyboardInterrupt:
            log_event("interrupt_cleanup_reentered", stage="processes")
            raw_terminated_pids = []
        terminated_pids = (
            [pid for pid in raw_terminated_pids if isinstance(pid, int) and pid > 0]
            if isinstance(raw_terminated_pids, list)
            else []
        )
        exit_code = 130
    finally:
        if interrupted and not ignored_signal_handlers:
            ignored_signal_handlers = _install_cleanup_signal_ignores()
        try:
            if interrupted:
                if terminated_pids:
                    log_event(
                        "interrupt_cleanup_targets",
                        mode="processes",
                        targets=",".join(str(pid) for pid in terminated_pids),
                    )
                else:
                    log_event("interrupt_cleanup_targets", mode="none", targets="")
        finally:
            _restore_signal_handlers(ignored_signal_handlers)
            shutdown_event_logging()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run_cli())
