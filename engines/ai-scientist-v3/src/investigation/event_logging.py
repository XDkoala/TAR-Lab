"""Structured event journal and terminal renderers for experiment runs."""

from __future__ import annotations

import math
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from src.orchestrator import state as state_store

from .cost_tracking import (
    CostTrackingError,
    format_cost_checkpoint,
    load_summary,
    summary_output_tokens,
)
from .event_renderers import HumanRenderer, JsonRenderer
from .storage_failures import storage_failure_category, warn_storage_failure
from .terminal_ui import (
    PROGRESS_LABELS as _PROGRESS_LABELS,
    SPINNER_FRAMES,
    TERMINAL_WRITE_LOCK,
    colors_enabled,
    style,
    terminal_width,
    truncate_terminal_text,
)

_STEP_PHASES = {
    "idea_generation": "idea",
    "attempt": "run",
}
_COST_START_EVENTS = {"step_start"}
_COST_END_EVENTS = {"step_end"}
_COST_FINAL_EVENTS = {"campaign_failed", "finished"}
_COST_READ_ERRORS = (CostTrackingError, ArithmeticError, OSError, TypeError, ValueError)
_CostSnapshot = tuple[Decimal, Decimal, int, int, int, int, int]
_EMPTY_COST_SNAPSHOT: _CostSnapshot = (Decimal(0), Decimal(0), 0, 0, 0, 0, 0)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(normalize_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def infer_phase(event: str, fields: dict[str, Any]) -> str | None:
    step = fields.get("step")
    if isinstance(step, str):
        return _STEP_PHASES.get(step)
    if event.startswith("idea_"):
        return "idea"
    if event.startswith(("attempt_", "run_")):
        return "run"
    return None


def format_duration(seconds: float) -> str:
    rounded = max(0, int(seconds))
    if rounded < 60:
        return f"{rounded}s"
    minutes, remaining_seconds = divmod(rounded, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m"


def format_pipeline_elapsed(seconds: float) -> str:
    return format_duration(seconds)


class EventLogger:
    """Write complete events to disk and render them for the active terminal."""

    def __init__(
        self,
        *,
        log_format: str,
        verbose: bool,
        heartbeat_seconds: int,
        results_dir: Path,
        worker_index: int,
        stream: TextIO | None = None,
        session_id: str | None = None,
        timestamp_fn: Callable[[], str] = utc_timestamp,
        monotonic_fn: Callable[[], float] = time.monotonic,
        progress_tick_seconds: float = 0.1,
        start_progress_thread: bool = True,
        source: str = "wrapper",
        renderer: str = "scientist",
        journal_name: str = "events.jsonl",
        journal_enabled: bool = True,
        cost_root: Path | None = None,
        pipeline_started_at: float | None = None,
    ) -> None:
        if log_format not in {"human", "json"}:
            raise ValueError(f"log_format must be human or json, got {log_format!r}")
        self.log_format = log_format
        self.verbose = verbose
        self.heartbeat_seconds = heartbeat_seconds
        self.results_dir = results_dir
        self.cost_root = (cost_root or results_dir).expanduser().absolute()
        self.worker_index = worker_index
        self.source = source
        self.renderer_name = renderer
        self.journal_name = journal_name
        self.stream = stream or sys.stdout
        self.session_id = session_id or uuid.uuid4().hex
        self.timestamp_fn = timestamp_fn
        self.monotonic_fn = monotonic_fn
        self.pipeline_started_at = pipeline_started_at
        self.progress_tick_seconds = progress_tick_seconds
        self.start_progress_thread = start_progress_thread
        self._lock = threading.RLock()
        self._journal_available = journal_enabled
        self._journal_warning_emitted = False
        self._active_progress: dict[str, Any] | None = None
        self._preparation_progress_stack: list[tuple[str, dict[str, Any] | None]] = []
        self._progress_stop = threading.Event()
        self._progress_thread: threading.Thread | None = None
        self._closed = False
        self._spinner_index = 0
        self._cost_baselines: dict[str, _CostSnapshot] = {}
        try:
            self._run_cost_baseline = self._cost_snapshot()
        except _COST_READ_ERRORS:
            self._run_cost_baseline = _EMPTY_COST_SNAPSHOT
        self.renderer = (
            HumanRenderer(
                self.stream,
                verbose=verbose,
                results_dir=results_dir,
                worker_index=worker_index,
                transient_progress=self._uses_transient_progress(),
            )
            if log_format == "human"
            else JsonRenderer(self.stream)
        )

    def emit(self, event: str, **fields: Any) -> None:
        normalized_fields = normalize_value(fields)
        record = {
            "timestamp": self.timestamp_fn(),
            "source": self.source,
            "event": event,
            "phase": infer_phase(event, normalized_fields),
            "worker_index": self.worker_index,
            "session_id": self.session_id,
            "fields": normalized_fields,
        }
        with self._lock:
            self._clear_transient_locked()
            self._capture_cost_baseline(event, normalized_fields)
            if event in {"interrupted", "campaign_failed"}:
                self._active_progress = None
                self._preparation_progress_stack.clear()
            self._write_record_locked(record)
            self._write_cost_checkpoint(event, normalized_fields)
            if event == "preparation_step":
                self._update_preparation_progress(normalized_fields)

    def _update_preparation_progress(self, fields: dict[str, Any]) -> None:
        step = str(fields["step"])
        state = fields.get("state")
        if state == "started":
            self._preparation_progress_stack.append((step, self._active_progress))
            self.start_progress(
                step,
                started_at=self.monotonic_fn(),
                **{key: value for key, value in fields.items() if key != "step"},
            )
        elif state in {"completed", "failed"}:
            if not self._preparation_progress_stack:
                return
            previous_step, previous_progress = self._preparation_progress_stack[-1]
            if previous_step != step:
                return
            self._preparation_progress_stack.pop()
            if self._active_progress is not None and self._active_progress.get("step") == step:
                self._active_progress = previous_progress

    @staticmethod
    def _cost_label(event: str, fields: dict[str, Any]) -> str:
        if event in _COST_FINAL_EVENTS:
            return "final run"
        value = (
            fields.get("label")
            or fields.get("phase_name")
            or fields.get("stage")
            or fields.get("step")
            or fields.get("gate")
            or event
        )
        return str(value).replace("_", " ")

    @staticmethod
    def _cost_key(event: str, fields: dict[str, Any]) -> str | None:
        if event in _COST_FINAL_EVENTS:
            return "final"
        if event.startswith("step_"):
            kind = "step"
        else:
            return None
        return f"{kind}:{EventLogger._cost_label(event, fields)}"

    @staticmethod
    def _cost_snapshot_from_summary(summary: dict[str, Any]) -> _CostSnapshot:
        reported = Decimal(str(summary.get("reported_cost_usd", 0)))
        estimated = Decimal(str(summary.get("estimated_cost_usd", 0)))
        estimated_events = int(summary.get("estimated_events", 0))
        usage_only = int(summary.get("usage_only_events", 0))
        unknown = int(summary.get("unknown_events", 0))
        tokens = summary_output_tokens(summary)
        unreported = int(summary.get("unreported_usage_events", 0))
        if (
            not reported.is_finite()
            or reported < 0
            or not estimated.is_finite()
            or estimated < 0
            or estimated_events < 0
            or usage_only < 0
            or unknown < 0
            or unreported < 0
        ):
            raise CostTrackingError("invalid cost summary counters")
        return reported, estimated, estimated_events, usage_only, unknown, tokens, unreported

    def _cost_snapshot(self) -> _CostSnapshot:
        return self._cost_snapshot_from_summary(load_summary(self.cost_root))

    def _pipeline_elapsed_fields(self) -> dict[str, str | float]:
        if self.pipeline_started_at is None:
            return {}
        elapsed_seconds = max(0.0, self.monotonic_fn() - self.pipeline_started_at)
        return {
            "pipeline_elapsed_seconds": round(elapsed_seconds, 1),
            "pipeline_elapsed": format_pipeline_elapsed(elapsed_seconds),
        }

    def _capture_cost_baseline(self, event: str, fields: dict[str, Any]) -> None:
        if self.worker_index != 0:
            return
        is_start = event in _COST_START_EVENTS
        key = self._cost_key(event, fields)
        if not is_start or key is None:
            return
        try:
            baseline = self._cost_snapshot()
        except _COST_READ_ERRORS:
            baseline = _EMPTY_COST_SNAPSHOT
        self._cost_baselines[key] = baseline

    def _write_cost_checkpoint(self, event: str, fields: dict[str, Any]) -> None:
        if self.worker_index != 0:
            return
        is_end = event in _COST_END_EVENTS or event in _COST_FINAL_EVENTS
        key = self._cost_key(event, fields)
        summary_path = self.cost_root / "metrics" / "cost_summary.json"
        if not is_end or key is None or not summary_path.is_file():
            return
        baseline = (
            self._run_cost_baseline
            if event in _COST_FINAL_EVENTS
            else self._cost_baselines.pop(key, _EMPTY_COST_SNAPSHOT)
        )
        try:
            summary = load_summary(self.cost_root)
            _, cost_fields = self._cost_checkpoint_fields(
                summary=summary,
                baseline=baseline,
                label=self._cost_label(event, fields),
            )
        except _COST_READ_ERRORS:
            return
        cost_fields.update(self._pipeline_elapsed_fields())
        self._write_cost_record(event, fields, cost_fields)

    def _cost_checkpoint_fields(
        self,
        *,
        summary: dict[str, Any],
        baseline: _CostSnapshot,
        label: str,
    ) -> tuple[_CostSnapshot, dict[str, Any]]:
        current = self._cost_snapshot_from_summary(summary)
        run_reported, run_estimated = current[:2]
        reported = max(Decimal(0), run_reported - baseline[0])
        estimated = max(Decimal(0), run_estimated - baseline[1])
        message = format_cost_checkpoint(
            summary,
            label=label,
            baseline_output_tokens=baseline[5],
            baseline_unreported=baseline[6],
        )
        return current, {
            "label": label,
            "message": message,
            "reported_cost_usd": format(reported, "f"),
            "estimated_cost_usd": format(estimated, "f"),
            "total_cost_usd": format(reported + estimated, "f"),
            "run_reported_cost_usd": format(run_reported, "f"),
            "run_estimated_cost_usd": format(run_estimated, "f"),
            "run_total_cost_usd": format(run_reported + run_estimated, "f"),
            "estimated_events": max(0, current[2] - baseline[2]),
            "usage_only_events": max(0, current[3] - baseline[3]),
            "unknown_events": max(0, current[4] - baseline[4]),
            "output_tokens": max(0, current[5] - baseline[5]),
            "run_output_tokens": current[5],
            "unreported_usage_events": max(0, current[6] - baseline[6]),
            "ledger": str(self.cost_root / "metrics" / "cost_events.jsonl"),
            "summary": str(self.cost_root / "metrics" / "cost_summary.json"),
        }

    def _write_cost_record(
        self,
        event: str,
        fields: dict[str, Any],
        cost_fields: dict[str, Any],
    ) -> None:
        record = {
            "timestamp": self.timestamp_fn(),
            "source": self.source,
            "event": "cost_summary",
            "phase": infer_phase(event, fields),
            "worker_index": self.worker_index,
            "session_id": self.session_id,
            "fields": cost_fields,
        }
        self._write_record_locked(record)

    def start_progress(self, step: str, *, started_at: float, **fields: Any) -> None:
        """Track a long-running step for TTY refreshes and durable heartbeats."""
        if self.heartbeat_seconds == 0 and not self._uses_transient_progress():
            return
        with self._lock:
            self._active_progress = {
                "step": step,
                "started_at": started_at,
                "last_heartbeat": started_at,
                "fields": normalize_value(fields),
            }
            if self.start_progress_thread and self._progress_thread is None:
                self._progress_thread = threading.Thread(
                    target=self._progress_loop,
                    name="experiment-log-progress",
                    daemon=True,
                )
                self._progress_thread.start()

    def stop_progress(self, step: str) -> None:
        with self._lock:
            active = self._active_progress
            if active is None or active.get("step") != step:
                return
            self._clear_transient_locked()
            self._active_progress = None

    def poll_progress(self) -> None:
        """Refresh active progress once; public for deterministic schedulers and tests."""
        with self._lock:
            active = self._active_progress
            if active is None or self._closed:
                return
            now = self.monotonic_fn()
            elapsed_seconds = max(0.0, now - float(active["started_at"]))
            elapsed = format_duration(elapsed_seconds)
            if self._uses_transient_progress():
                with TERMINAL_WRITE_LOCK:
                    self._clear_transient_locked()
                    fields = active["fields"]
                    prefix = f"W{self.worker_index:02d} · " if self.worker_index else ""
                    label = fields.get("label") or _PROGRESS_LABELS.get(
                        str(active["step"]),
                        "Working…",
                    )
                    current = fields.get(
                        "current", fields.get("global_cycle", fields.get("cycle"))
                    )
                    total = fields.get("total", fields.get("total_cycles"))
                    progress = (
                        f"{current}/{total} · " if current is not None and total is not None else ""
                    )
                    frame = SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)]
                    self._spinner_index += 1
                    terminal_columns = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)
                    fixed_width = terminal_width(f"{frame} {prefix}{progress} {elapsed}")
                    label_columns = max(0, terminal_columns - fixed_width)
                    rendered = (
                        f"{frame} {prefix}{progress}"
                        f"{truncate_terminal_text(label, columns=label_columns)} {elapsed}"
                    )
                    rendered = truncate_terminal_text(rendered, columns=terminal_columns)
                    rendered = style(
                        rendered,
                        "running",
                        enabled=colors_enabled(self.stream),
                    )
                    self.stream.write(f"\r\x1b[2K{rendered}")
                    self.stream.flush()
            if self.heartbeat_seconds <= 0:
                return
            since_heartbeat = now - float(active["last_heartbeat"])
            if since_heartbeat < self.heartbeat_seconds:
                return
            active["last_heartbeat"] = now
            heartbeat_fields = dict(active["fields"])
            heartbeat_fields.update(
                step=active["step"],
                elapsed=elapsed,
                elapsed_seconds=round(elapsed_seconds, 1),
            )
            record = {
                "timestamp": self.timestamp_fn(),
                "source": self.source,
                "event": "heartbeat",
                "phase": infer_phase("heartbeat", heartbeat_fields),
                "worker_index": self.worker_index,
                "session_id": self.session_id,
                "fields": heartbeat_fields,
            }
            self._write_record_locked(record, render=not self._uses_transient_progress())

    def _progress_loop(self) -> None:
        interval = (
            self.progress_tick_seconds
            if self._uses_transient_progress()
            else max(1, self.heartbeat_seconds)
        )
        while not self._progress_stop.wait(interval):
            self.poll_progress()

    def _uses_transient_progress(self) -> bool:
        isatty = getattr(self.stream, "isatty", None)
        return self.log_format == "human" and callable(isatty) and bool(isatty())

    def _clear_transient_locked(self) -> None:
        if self._active_progress is not None and self._uses_transient_progress():
            with TERMINAL_WRITE_LOCK:
                self.stream.write("\r\x1b[2K")
                self.stream.flush()

    @contextmanager
    def external_output(self) -> Iterator[None]:
        """Give external terminal output a clean line while progress remains active."""
        with TERMINAL_WRITE_LOCK:
            if self._active_progress is not None and self._uses_transient_progress():
                self.stream.write("\r\x1b[2K\n")
                self.stream.flush()
            yield

    def _write_record_locked(self, record: dict[str, Any], *, render: bool = True) -> None:
        if self._journal_available:
            try:
                state_store.append_jsonl(
                    self.results_dir / self.journal_name,
                    record,
                    mode=0o600,
                )
            except OSError as exc:
                self._journal_available = False
                self._warn_journal_unavailable(exc)
        if render:
            terminal_record = record
            if self.log_format == "json" and "output_tail" in record["fields"]:
                terminal_record = dict(record)
                terminal_record["fields"] = {
                    key: value for key, value in record["fields"].items() if key != "output_tail"
                }
            self.renderer.render(terminal_record)

    def _warn_journal_unavailable(self, exc: OSError) -> None:
        if self._journal_warning_emitted:
            return
        self._journal_warning_emitted = True
        fields = {"error": type(exc).__name__}
        category = storage_failure_category(exc)
        if category is not None:
            fields["failure_category"] = category
            fields["operation"] = "event_journal_append"
            warn_storage_failure(exc, operation="event_journal_append")
        warning = {
            "timestamp": self.timestamp_fn(),
            "source": self.source,
            "event": "event_journal_unavailable",
            "phase": None,
            "worker_index": self.worker_index,
            "session_id": self.session_id,
            "fields": fields,
        }
        self.renderer.render(warning)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._progress_stop.set()
        thread = self._progress_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.1, self.progress_tick_seconds * 2))
        with self._lock:
            self._clear_transient_locked()
            self._active_progress = None
            self._preparation_progress_stack.clear()
            self.stream.flush()
