"""Runtime logging and subprocess lifecycle helpers."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .event_logging import EventLogger
from .terminal_ui import TERMINAL_WRITE_LOCK
from .temp_storage import temporary_directory

ACTIVE_SUBPROCESSES: set[subprocess.Popen[str]] = set()
ACTIVE_SUBPROCESSES_LOCK = threading.Lock()

_ANSI_RESET = "\033[0m"
_STEP_PHASES: dict[str, str] = {
    "task_draft": "task",
    "idea_generation": "idea",
    "attempt": "run",
    "paper_generation": "paper",
}
_PHASE_LABELS: dict[str, str] = {
    "task": "TASK",
    "idea": "IDEA",
    "run": "RUN",
    "paper": "PAPER",
}
_PHASE_ANSI_COLORS: dict[str, str] = {
    "task": "\033[96m",
    "idea": "\033[93m",
    "run": "\033[92m",
    "paper": "\033[94m",
}
_LOG_EVENT_SEPARATOR = "-" * 96
_LOG_RENDER_LOCK = threading.Lock()
_LOG_EVENT_EMITTED = False
_EVENT_LOGGER: EventLogger | None = None
_INHERITED_COST_ROOT = os.environ.get("AGENTIC_COST_ROOT")
_MANAGED_COST_ROOT: str | None = None
STANDARD_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
class StandardLogHandler(logging.StreamHandler[Any]):
    """Keep standard diagnostics separate from transient progress output."""

    def emit(self, record: logging.LogRecord) -> None:
        event_logger = _EVENT_LOGGER
        if event_logger is not None:
            with event_logger.external_output():
                super().emit(record)
            return
        with TERMINAL_WRITE_LOCK:
            super().emit(record)


def configure_standard_logging(*, level: int = logging.WARNING) -> None:
    """Give standard-library diagnostics one consistent terminal format."""
    root_logger = logging.getLogger()
    host_terminal_handler = False
    for existing in root_logger.handlers[:]:
        stream = getattr(existing, "stream", None)
        project_owned = isinstance(existing, StandardLogHandler)
        replaceable_default = (
            type(existing) is logging.StreamHandler
            and not existing.filters
            and existing.formatter is None
            and existing.level == logging.NOTSET
            and (stream is sys.stdout or stream is sys.stderr)
        )
        custom_terminal = (
            isinstance(existing, logging.StreamHandler)
            and (stream is sys.stdout or stream is sys.stderr)
            and not project_owned
            and not replaceable_default
        )
        host_terminal_handler = host_terminal_handler or custom_terminal
        if project_owned or replaceable_default:
            root_logger.removeHandler(existing)
            existing.close()
    if not host_terminal_handler:
        handler = StandardLogHandler()
        handler.setFormatter(logging.Formatter(STANDARD_LOG_FORMAT))
        root_logger.addHandler(handler)
        root_logger.setLevel(level)


def configure_event_logging(
    *,
    log_format: str,
    verbose: bool,
    heartbeat_seconds: int,
    results_dir: Path,
    worker_index: int,
    source: str = "wrapper",
    renderer: str = "scientist",
    journal_name: str = "events.jsonl",
    journal_enabled: bool = True,
    pipeline_started_at: float | None = None,
) -> None:
    """Configure the process-wide experiment event logger."""
    global _EVENT_LOGGER, _MANAGED_COST_ROOT
    configured_root = (
        _MANAGED_COST_ROOT
        if _EVENT_LOGGER is not None and _MANAGED_COST_ROOT is not None
        else _INHERITED_COST_ROOT or str(results_dir.expanduser().absolute())
    )
    cost_root = Path(configured_root).expanduser().absolute()
    _MANAGED_COST_ROOT = str(cost_root)
    os.environ["AGENTIC_COST_ROOT"] = str(cost_root)
    if _EVENT_LOGGER is not None:
        _EVENT_LOGGER.close()
    _EVENT_LOGGER = EventLogger(
        log_format=log_format,
        verbose=verbose,
        heartbeat_seconds=heartbeat_seconds,
        results_dir=results_dir,
        worker_index=worker_index,
        source=source,
        renderer=renderer,
        journal_name=journal_name,
        journal_enabled=journal_enabled,
        cost_root=cost_root,
        pipeline_started_at=pipeline_started_at,
    )


def shutdown_event_logging() -> None:
    """Flush and release the process-wide event logger."""
    global _EVENT_LOGGER, _MANAGED_COST_ROOT
    if _EVENT_LOGGER is not None:
        _EVENT_LOGGER.close()
        _EVENT_LOGGER = None
    if _INHERITED_COST_ROOT is None:
        os.environ.pop("AGENTIC_COST_ROOT", None)
    else:
        os.environ["AGENTIC_COST_ROOT"] = _INHERITED_COST_ROOT
    _MANAGED_COST_ROOT = None


def event_logging_configured() -> bool:
    """Return whether structured event logging is active in this process."""
    return _EVENT_LOGGER is not None


def _utc_timestamp() -> str:
    """Render a compact UTC timestamp for wrapper logs."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _colors_enabled() -> bool:
    """Return whether ANSI coloring should be emitted."""
    force_color = os.environ.get("FORCE_COLOR")
    if force_color is not None:
        return force_color != "0"
    if os.environ.get("NO_COLOR") is not None:
        return False
    stream = getattr(sys, "stdout", None)
    return bool(stream and hasattr(stream, "isatty") and stream.isatty())


def _infer_phase(event: str, fields: dict[str, Any]) -> str | None:
    """Infer log phase for highlighting."""
    step = fields.get("step")
    if isinstance(step, str):
        return _STEP_PHASES.get(step)

    lowered_event = event.lower()
    if lowered_event.startswith("task_"):
        return "task"
    if lowered_event.startswith("idea_"):
        return "idea"
    if lowered_event.startswith("attempt_") or lowered_event.startswith("run_"):
        return "run"
    if lowered_event.startswith("paper_"):
        return "paper"
    return None


def _render_phase_badge(phase: str) -> str:
    """Render a phase badge (including trailing space) with optional ANSI color."""
    label = f"[{_PHASE_LABELS.get(phase, phase.upper())}]"
    if not _colors_enabled():
        return f"{label} "
    color = _PHASE_ANSI_COLORS.get(phase)
    if color is None:
        return f"{label} "
    # Keep the separating space inside the colored segment so the rendered output
    # contains the plain-text substring "[PHASE] " even when ANSI is enabled.
    return f"{color}{label} {_ANSI_RESET}"


def reset_log_render_state() -> None:
    """Reset separator insertion state (used by tests)."""
    global _LOG_EVENT_EMITTED
    with _LOG_RENDER_LOCK:
        _LOG_EVENT_EMITTED = False


def ensure_text(value: str | bytes | None) -> str:
    """Normalize subprocess output to a text string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds for human-readable logs."""
    normalized = max(0.0, seconds)
    rounded_seconds = int(normalized + 0.5)
    if rounded_seconds < 60:
        return f"{normalized:.1f}s"
    minutes, remaining_seconds = divmod(rounded_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m {remaining_seconds}s"


def _format_log_value(value: Any) -> str:
    """Format log value."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, set):
        return json.dumps(sorted(value), ensure_ascii=True)
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=True)

    text = str(value)
    if not text:
        return '""'
    if any(ch.isspace() for ch in text):
        return json.dumps(text, ensure_ascii=True)
    return text


def log_event(event: str, **fields: Any) -> None:
    """Emit a structured wrapper log event."""
    if _EVENT_LOGGER is not None:
        _EVENT_LOGGER.emit(event, **fields)
        return
    global _LOG_EVENT_EMITTED
    phase = _infer_phase(event, fields)
    head = f"{_utc_timestamp()} [wrapper] {event}"
    if phase:
        head = f"{_render_phase_badge(phase)}{head}"
    parts = [head]
    for key, value in fields.items():
        parts.append(f"{key}={_format_log_value(value)}")
    if phase and "phase" not in fields:
        parts.append(f"phase={phase}")

    line = " ".join(parts)
    with _LOG_RENDER_LOCK:
        if _LOG_EVENT_EMITTED:
            line = f"{_LOG_EVENT_SEPARATOR}\n{line}"
        _LOG_EVENT_EMITTED = True
    print(line)


def log_step_start(step: str, **fields: Any) -> float:
    """Log step start metadata and return start time."""
    started_at = time.monotonic()
    log_event("step_start", step=step, status="started", **fields)
    if _EVENT_LOGGER is not None:
        _EVENT_LOGGER.start_progress(step, started_at=started_at, **fields)
    return started_at


def log_step_end(step: str, started_at: float, **fields: Any) -> None:
    """Log step completion metadata and elapsed duration."""
    elapsed_seconds = time.monotonic() - started_at
    if _EVENT_LOGGER is not None:
        _EVENT_LOGGER.stop_progress(step)
    log_event(
        "step_end",
        step=step,
        status="completed",
        elapsed=format_elapsed(elapsed_seconds),
        **fields,
    )


def register_active_subprocess(proc: subprocess.Popen[str]) -> None:
    """Register active subprocess."""
    with ACTIVE_SUBPROCESSES_LOCK:
        ACTIVE_SUBPROCESSES.add(proc)


def unregister_active_subprocess(proc: subprocess.Popen[str]) -> None:
    """Unregister active subprocess."""
    with ACTIVE_SUBPROCESSES_LOCK:
        ACTIVE_SUBPROCESSES.discard(proc)


def terminate_active_process_group(proc: subprocess.Popen[str], *, grace_seconds: int) -> None:
    """Terminate active process group."""
    if proc.poll() is not None:
        return

    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    if proc.poll() is not None:
        return

    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def terminate_all_active_processes(
    *,
    grace_seconds: int,
    terminate_process_group: Callable[[subprocess.Popen[str]], None],
) -> list[int]:
    """Terminate all active processes."""
    with ACTIVE_SUBPROCESSES_LOCK:
        procs = list(ACTIVE_SUBPROCESSES)
    terminated_pids: list[int] = []
    for proc in procs:
        pid = getattr(proc, "pid", None)
        terminate_process_group(proc)
        if isinstance(pid, int) and pid > 0:
            terminated_pids.append(pid)
    return terminated_pids


def script_environment() -> dict[str, str]:
    """Environment for wrapper shell scripts: the caller's, with the wrapper interpreter."""
    temporary_directory()
    env = os.environ.copy()
    # Ensure shell scripts invoked by the wrapper use the same interpreter as the wrapper,
    # regardless of the caller's PATH configuration.
    env.setdefault("WRAPPER_PYTHON", sys.executable)
    return env
