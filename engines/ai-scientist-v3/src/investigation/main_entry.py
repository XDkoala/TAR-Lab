"""Main orchestration flow invoked by the wrapper entry point."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

from src.orchestrator import state as state_store

from . import campaign_loop
from .agent_selection import resolve_agent_model
from .config_loader import write_hydra_snapshot
from .config import InvestigationConfig, config_to_flat_dict
from .files import (
    find_best_program_in_experiment,
    missing_task_scaffold_files,
    read_json,
    resolve_path,
    utc_now,
)
from .program_clone import clone_program_dir
from .program_spec import ProgramSpecError, entrypoint_relpath_from_spec
from .task_setup import run_task_setup
from .models import IdeaGenerationResult, RunAttemptResult

logger = logging.getLogger(__name__)


def _freeze_seed_program(seed_program_dir: Path, results_dir: Path) -> Path:
    """Copy a run's seed beneath its agent-writable investigation workspace."""
    seed_program_dir = resolve_path(seed_program_dir)
    results_dir = resolve_path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    snapshot_root = Path(
        tempfile.mkdtemp(prefix=".baseline-seed-", dir=results_dir)
    )
    frozen_seed = snapshot_root / "program_0"
    try:
        clone_program_dir(src_dir=seed_program_dir, dst_dir=frozen_seed)
    except Exception:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise
    return resolve_path(frozen_seed)


def resolve_task_python(cfg: InvestigationConfig, task_dir: Path) -> Path | None:
    """The interpreter that runs official evaluations: ``paths.task_python``, else the
    task's own ``.venv``, else ``None`` (the engine's interpreter)."""
    if cfg.paths.task_python:
        return Path(str(cfg.paths.task_python)).expanduser()
    venv_python = task_dir / ".venv" / "bin" / "python"
    return venv_python if venv_python.is_file() else None


LogEventFn = Callable[..., None]
LogStepStartFn = Callable[..., float]
LogStepEndFn = Callable[..., None]
RunIdeaGenerationFn = Callable[..., IdeaGenerationResult]
RunSingleAttemptFn = Callable[..., RunAttemptResult]
RunTaskSetupFn = Callable[[Path, dict[str, object], int], bool]
ReconcileIdeaLocalMemoryFn = Callable[..., None]
FormatElapsedFn = Callable[[float], str]
DefaultResultsPathFn = Callable[[Path, str], Path]
DefaultInstructionPathFn = Callable[[Path], Path]
DefaultIdeaInstructionPathFn = Callable[[], Path]


def run_main_flow(
    *,
    cfg: InvestigationConfig,
    main_started_at: float,
    default_results_path_fn: DefaultResultsPathFn,
    default_instruction_path_fn: DefaultInstructionPathFn,
    default_idea_instruction_path_fn: DefaultIdeaInstructionPathFn,
    log_event_fn: LogEventFn,
    log_step_start_fn: LogStepStartFn,
    log_step_end_fn: LogStepEndFn,
    run_idea_generation_fn: RunIdeaGenerationFn,
    run_single_attempt_fn: RunSingleAttemptFn,
    run_task_setup_fn: RunTaskSetupFn = run_task_setup,
    reconcile_legacy_idea_local_memory_fn: ReconcileIdeaLocalMemoryFn,
    format_elapsed_fn: FormatElapsedFn,
) -> int:
    """Execute the wrapper main control flow and return an exit code."""
    task_dir = resolve_path(str(cfg.paths.task_dir))
    results_dir = (
        resolve_path(cfg.paths.results_dir)
        if cfg.paths.results_dir
        else default_results_path_fn(task_dir, cfg.agent.name)
    )
    global_memory_dir_raw = cfg.paths.global_memory_dir
    global_memory_dir = (
        resolve_path(global_memory_dir_raw)
        if global_memory_dir_raw
        else results_dir
    )
    if cfg.paths.instruct:
        instruct_path = resolve_path(cfg.paths.instruct)
    else:
        instruct_path = default_instruction_path_fn(task_dir)
    idea_instruct_path = default_idea_instruction_path_fn()
    agent_model = resolve_agent_model(cfg.agent, cfg.agent.name)
    agent_profile = (
        str(cfg.agent.profile).strip()
        if isinstance(cfg.agent.profile, str) and cfg.agent.profile.strip()
        else None
    )
    reasoning_effort = cfg.agent.reasoning_effort

    if not task_dir.is_dir():
        log_event_fn(
            "config_error",
            reason="missing_task_dir",
            path=task_dir,
            hint="check paths.task_dir",
        )
        return 2
    if not instruct_path.is_file():
        log_event_fn(
            "config_error",
            reason="missing_instruction_file",
            path=instruct_path,
            hint="check paths.instruct or src/prompts/general.md",
        )
        return 2
    if not idea_instruct_path.is_file():
        log_event_fn(
            "config_error",
            reason="missing_idea_instruction_file",
            path=idea_instruct_path,
            hint="restore src/prompts/idea.md",
        )
        return 2
    task_python = resolve_task_python(cfg, task_dir)
    if task_python is not None and not os.access(task_python, os.X_OK):
        log_event_fn(
            "config_error",
            reason="missing_task_python",
            path=task_python,
            hint="check paths.task_python or the task's .venv",
        )
        return 2
    # Agents and their evaluation wrapper read this; workers inherit it.
    os.environ["TASK_PYTHON"] = str(task_python) if task_python else sys.executable

    task_setup_args: dict[str, object] = {
        "agent": cfg.agent.name,
        "run_id": cfg.run_id,
        "task_dir": str(task_dir),
        "results_dir": str(results_dir),
        "global_memory_dir": str(global_memory_dir),
    }
    if agent_model is not None:
        task_setup_args["agent_model"] = agent_model
    if agent_profile is not None:
        task_setup_args["agent_profile"] = agent_profile
    if reasoning_effort is not None:
        task_setup_args["reasoning_effort"] = reasoning_effort

    if not run_task_setup_fn(task_dir, task_setup_args, cfg.execution.run_timeout_seconds):
        log_event_fn(
            "task_setup_failed",
            task_dir=task_dir,
            timeout_seconds=cfg.execution.run_timeout_seconds,
        )
        return 2

    missing_scaffold = missing_task_scaffold_files(task_dir)
    if missing_scaffold:
        log_event_fn(
            "config_error",
            reason="missing_task_scaffold_files",
            missing_scaffold=[str(path) for path in missing_scaffold],
            hint="add capture.py, evaluate.py and program_0/ to the task",
        )
        return 2

    seed_program_dir = resolve_path(task_dir / "program_0")
    if not seed_program_dir.is_dir():
        log_event_fn(
            "config_error",
            reason="missing_seed_program_dir",
            path=seed_program_dir,
            hint="expected <task_dir>/program_0 to be a directory",
        )
        return 2

    run_config_fields: dict[str, object] = {
        "show_run_header": cfg.logging.show_run_header,
        "agent.name": cfg.agent.name,
        "agent.model": agent_model,
        "agent.profile": agent_profile,
        "agent.reasoning_effort": reasoning_effort,
        "paths.task_dir": task_dir,
        "paths.results_dir": results_dir,
        "paths.global_memory_dir": global_memory_dir,
        "paths.instruct": instruct_path,
        "paths.idea_instruct": idea_instruct_path,
        "paths.task_python": os.environ["TASK_PYTHON"],
        "execution.resume": cfg.execution.resume,
        "execution.max_idea_cycles": cfg.execution.max_idea_cycles,
        "execution.max_program_evals_per_attempt": (
            cfg.execution.max_program_evals_per_attempt
        ),
        "execution.run_timeout_seconds": cfg.execution.run_timeout_seconds,
        "execution.idea_timeout_seconds": cfg.execution.idea_timeout_seconds,
        "execution.max_idea_generation_attempts": cfg.execution.max_idea_generation_attempts,
        "execution.run_timeout_grace_seconds": cfg.execution.run_timeout_grace_seconds,
    }

    results_dir.mkdir(parents=True, exist_ok=True)
    try:
        seed_program_dir = _freeze_seed_program(seed_program_dir, results_dir)
    except (OSError, ProgramSpecError, ValueError) as exc:
        log_event_fn(
            "config_error",
            reason="seed_program_snapshot_failed",
            path=seed_program_dir,
            error=str(exc),
        )
        return 2
    write_hydra_snapshot(cfg, results_dir)
    include_global_memory = results_dir == global_memory_dir
    state_store.ensure_histories(
        results_dir, include_global_memory=include_global_memory
    )
    state_store.ensure_global_memory_files(global_memory_dir)

    outer_state = state_store.load_or_init_outer_state(results_dir, seed_program_dir, cfg.execution.resume)
    best_manifest = state_store.load_or_init_best_manifest(results_dir, seed_program_dir, cfg.execution.resume)

    # Backward-compatible normalization: legacy manifests used program_path.
    if not isinstance(best_manifest.get("program_dir"), str) or not str(best_manifest.get("program_dir")).strip():
        legacy_program_path_raw = best_manifest.get("program_path")
        if isinstance(legacy_program_path_raw, str) and legacy_program_path_raw.strip():
            legacy_program_path = resolve_path(legacy_program_path_raw)
            best_manifest["program_dir"] = str(legacy_program_path.parent)
            best_manifest["entrypoint_path"] = legacy_program_path.relative_to(
                legacy_program_path.parent
            ).as_posix()
            state_store.save_best_manifest(results_dir, best_manifest)

    # Backward-compatible normalization: legacy outer_state used best_program_path.
    if not isinstance(outer_state.get("best_program_dir"), str) or not str(outer_state.get("best_program_dir")).strip():
        legacy_best_program_raw = outer_state.get("best_program_path")
        if isinstance(legacy_best_program_raw, str) and legacy_best_program_raw.strip():
            legacy_best_program_path = resolve_path(legacy_best_program_raw)
            outer_state["best_program_dir"] = str(
                legacy_best_program_path
                if legacy_best_program_path.is_dir()
                else legacy_best_program_path.parent
            )
            state_store.save_outer_state(results_dir, outer_state)

    best_dir_raw = best_manifest.get("program_dir")
    best_program_dir = (
        resolve_path(best_dir_raw) if isinstance(best_dir_raw, str) else seed_program_dir
    )
    if not best_program_dir.is_dir():
        best_program_dir = seed_program_dir
        best_manifest["program_dir"] = str(best_program_dir)
        best_manifest["entrypoint_path"] = (
            entrypoint_relpath_from_spec(best_program_dir) or "src/program.py"
        )
        state_store.save_best_manifest(results_dir, best_manifest)

    # If manifest is missing entrypoint_path, attempt to derive it.
    entrypoint_raw = best_manifest.get("entrypoint_path")
    if not isinstance(entrypoint_raw, str) or not entrypoint_raw.strip():
        best_manifest["entrypoint_path"] = (
            entrypoint_relpath_from_spec(best_program_dir) or "src/program.py"
        )
        state_store.save_best_manifest(results_dir, best_manifest)

    start_cycle = int(outer_state.get("current_cycle", 0)) + 1
    max_program_evals_per_attempt = cfg.execution.max_program_evals_per_attempt

    # Log all Hydra hyperparameters
    flat_cfg = config_to_flat_dict(cfg)
    log_event_fn("run_hydra_config", **flat_cfg)

    log_event_fn(
        "run_config",
        start_cycle=start_cycle,
        **run_config_fields,
    )
    return campaign_loop.run_cycles_and_finalize(
        cfg=cfg,
        task_dir=task_dir,
        results_dir=results_dir,
        global_memory_dir=global_memory_dir,
        instruct_path=instruct_path,
        idea_instruct_path=idea_instruct_path,
        outer_state=outer_state,
        best_manifest=best_manifest,
        best_program_dir=best_program_dir,
        start_cycle=start_cycle,
        max_program_evals_per_attempt=max_program_evals_per_attempt,
        main_started_at=main_started_at,
        log_event_fn=log_event_fn,
        log_step_start_fn=log_step_start_fn,
        log_step_end_fn=log_step_end_fn,
        format_elapsed_fn=format_elapsed_fn,
        run_idea_generation_fn=run_idea_generation_fn,
        run_single_attempt_fn=run_single_attempt_fn,
        reconcile_legacy_idea_local_memory_fn=reconcile_legacy_idea_local_memory_fn,
        find_best_program_in_experiment_fn=find_best_program_in_experiment,
        read_json_fn=read_json,
        utc_now_fn=utc_now,
    )
