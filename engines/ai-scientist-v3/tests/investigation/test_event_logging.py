"""EventLogger renderers, journals, heartbeats, cost summaries, preparation milestones and logo."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from src.investigation import runtime as runtime_module
from src.investigation.cost_tracking import record_events
from src.investigation.event_logging import EventLogger
from src.investigation.logo import render_logo
from src.investigation.terminal_ui import terminal_width

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
GET_TERMINAL_SIZE = "src.investigation.event_logging.shutil.get_terminal_size"


class TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def _sub(tmp_path: Path, name: str) -> Path:
    """Fresh results directory for one section of a merged test (isolates journals and cost roots)."""
    path = tmp_path / name
    path.mkdir()
    return path


def _logger(
    tmp_path: Path,
    stream: io.StringIO | None = None,
    *,
    log_format: str = "human",
    verbose: bool = False,
    heartbeat_seconds: int = 0,
    clock: list[float] | None = None,
    **kwargs: Any,
) -> EventLogger:
    """EventLogger with a fixed session/timestamp; `clock` pins monotonic time and disables the thread."""
    if clock is not None:
        kwargs["monotonic_fn"] = lambda: clock[0]
        kwargs.setdefault("start_progress_thread", False)
    return EventLogger(
        log_format=log_format,
        verbose=verbose,
        heartbeat_seconds=heartbeat_seconds,
        results_dir=tmp_path,
        worker_index=kwargs.pop("worker_index", 0),
        stream=stream if stream is not None else io.StringIO(),
        session_id="session-test",
        timestamp_fn=lambda: "2026-08-17T10:00:00.000Z",
        **kwargs,
    )


def _records(tmp_path: Path, name: str = "events.jsonl") -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / name).read_text(encoding="utf-8").splitlines()]


def _cost_record(results_dir: Path, event_id: str, stage: str, **fields: Any) -> None:
    record = {
        "event_id": event_id,
        "invocation_id": event_id,
        "phase": "paper",
        "stage": stage,
        "source": "headless",
        "agent": "codex",
        "model": "gpt-5.6-sol",
        "status": "completed",
        "billing_basis": "reported",
        "usage": {},
    }
    record.update(fields)
    record_events(results_dir, [record])


def _cost_summary(tmp_path: Path) -> dict[str, Any]:
    return next(record for record in _records(tmp_path) if record["event"] == "cost_summary")


def _log_record(name: str, msg: str, *, checkpoint_stored: bool = False) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name, level=logging.WARNING, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None
    )
    if checkpoint_stored:
        record.checkpoint_stored = True
    return record


def _emit_run_config(logger: EventLogger, **overrides: Any) -> None:
    fields = {
        "agent.name": "codex",
        "paths.task_dir": "task",
        "execution.max_idea_cycles": 1,
        "execution.max_program_evals_per_attempt": 1,
        "execution.workers": 1,
        "container.enabled": False,
    }
    fields.update(overrides)
    logger.emit("run_config", **fields)


@contextmanager
def _restored_root_logger():
    """Snapshot root handlers/level inside the test body (pytest swaps stderr/handlers per phase)."""
    root = logging.getLogger()
    original_handlers, original_level = root.handlers[:], root.level
    try:
        yield root
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)


def _patched_stderr(monkeypatch) -> io.StringIO:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    return stream


@pytest.fixture
def fresh_cost_root(monkeypatch):
    monkeypatch.setattr(runtime_module, "_INHERITED_COST_ROOT", None)
    monkeypatch.setattr(runtime_module, "_MANAGED_COST_ROOT", None)


def _configure_event_logging(results_dir: Path) -> None:
    runtime_module.configure_event_logging(
        log_format="human", verbose=False, heartbeat_seconds=0, results_dir=results_dir,
        worker_index=0, journal_enabled=False,
    )


# ---------------------------------------------------------------------------
# Standard logging / formatter
# ---------------------------------------------------------------------------


FORMATTER_CASES = [
    (
        "checkpoint-label-drops-module-prefix",
        "src.investigation.run_checkpoint",
        "Checkpoint 'paper phase' excluded 3 sensitive path(s)",
        True,
        "[CHECKPOINT STORED] Checkpoint 'paper phase'",
        ["[WARNING]", "src.investigation.run_checkpoint"],
    ),
    (
        "label-cannot-be-claimed-by-other-logger",
        "untrusted.extension",
        "not a checkpoint",
        True,
        "[WARNING] untrusted.extension: not a checkpoint",
        ["[CHECKPOINT STORED]"],
    ),
    (
        "warning-keeps-severity-and-module",
        "src.pipeline.cli",
        "ordinary warning",
        False,
        "[WARNING] src.pipeline.cli: ordinary warning",
        [],
    ),
]








# ---------------------------------------------------------------------------
# Human / JSON renderers and journal
# ---------------------------------------------------------------------------


def test_human_run_header_rendering(tmp_path: Path) -> None:
    # compact run header without repeated paths
    results = _sub(tmp_path, "compact")
    stream = io.StringIO()
    logger = _logger(results, stream)
    logger.emit(
        "run_hydra_config", **{"paths.task_dir": "/very/long/task", "execution.max_idea_cycles": "20"}
    )
    _emit_run_config(
        logger,
        **{
            "agent.name": "pi",
            "agent.model": "deepseek-v4-flash",
            "agent.reasoning_effort": "high",
            "paths.task_dir": "/very/long/bayesian_optimization",
            "paths.results_dir": results,
            "execution.max_idea_cycles": 20,
            "execution.max_program_evals_per_attempt": 4,
            "container.enabled": True,
        },
    )

    rendered = stream.getvalue()
    assert rendered.index("████") < rendered.index("Agentic Scientist")
    assert "Agentic Scientist · bayesian_optimization · 20 cycles" in rendered
    assert "Agent: pi / deepseek-v4-flash / high" in rendered
    assert "Config: 4 evaluations/idea\n" in rendered
    assert f"Results: {results}" in rendered
    assert "Artifacts:" not in rendered
    assert "/very/long/task" not in rendered
    assert "run_hydra_config" not in rendered

    # managed child keeps the run_config event without rendering the header
    child = _sub(tmp_path, "child")
    stream = io.StringIO()
    _emit_run_config(
        _logger(child, stream),
        show_run_header=False,
        **{"execution.max_idea_cycles": 2, "container.enabled": True},
    )

    assert stream.getvalue() == ""
    records = _records(child)
    assert records[-1]["event"] == "run_config"
    assert records[-1]["fields"]["show_run_header"] is False

    # header escapes untrusted agent fields
    stream = io.StringIO()
    _emit_run_config(
        _logger(_sub(tmp_path, "escape"), stream),
        **{
            "agent.name": "codex\nforged",
            "agent.model": "model\x1b]8;;https://example.com\x07link",
            "agent.reasoning_effort": "high\nforged",
        },
    )

    rendered = stream.getvalue()
    assert "codex\\nforged" in rendered
    assert "high\\nforged" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered

    # header omits unspecified reasoning effort
    stream = io.StringIO()
    _emit_run_config(_logger(_sub(tmp_path, "effort"), stream), **{"agent.model": "gpt-5.6-sol"})

    assert "Agent: codex / gpt-5.6-sol\n" in stream.getvalue()


def test_human_renderer_cycle_rubrics_and_verbose_evidence(tmp_path: Path) -> None:
    # cycle, idea, score and every rubric (evidence hidden when not verbose)
    stream = io.StringIO()
    logger = _logger(tmp_path, stream)

    logger.emit("cycle_start", cycle=2, total_cycles=20)
    logger.emit("step_start", step="idea_generation", cycle=2, status="started")
    logger.emit(
        "step_end", step="idea_generation", cycle=2, status="completed", elapsed="3m 53s", exit=0
    )
    logger.emit(
        "idea_bundle_loaded",
        cycle=2,
        idea_id="ensemble_ts",
        idea_title="Ensemble Surrogate with Thompson Sampling",
        target_rubric_count=3,
    )
    logger.emit("step_start", step="attempt", cycle=2, status="started")
    logger.emit(
        "attempt_summary",
        cycle=2,
        elapsed="1h 32m",
        exit=0,
        combined_score=93.61,
        score_delta=1.75,
        best_score=93.61,
        correct=True,
        rubric_valid=True,
        rubric_results=[
            {"id": "held_out_generalization", "pass": True, "evidence": "0.93"},
            {"id": "noise_robustness", "pass": False, "evidence": "below threshold"},
            {"id": "wall_clock_overhead", "pass": True, "evidence": "12 seconds"},
        ],
        attempt_status="fail",
        failure_reason="rubrics",
    )
    logger.emit("cycle_end", cycle=2, total_cycles=20, elapsed="1h 36m")

    rendered = stream.getvalue()
    for text in (
        "Cycle 2/20",
        "Generating idea",
        "3m 53s",
        "Idea: Ensemble Surrogate with Thompson Sampling",
        "Score: 93.61  ▲ +1.75",
        "Best: 93.61",
        "Correctness: PASS",
        "Rubrics: 2/3 passed",
        "PASS  held_out_generalization",
        "FAIL  noise_robustness",
        "PASS  wall_clock_overhead",
        "Outcome: FAILED — rubric checks did not pass",
    ):
        assert text in rendered
    assert "below threshold" not in rendered

    # verbose renderer includes paths and rubric evidence
    stream = io.StringIO()
    log_path = tmp_path / "ideas" / "idea-1" / "wrapper_output.log"
    _logger(tmp_path, stream, verbose=True).emit(
        "attempt_summary",
        cycle=1,
        elapsed="2.0s",
        exit=0,
        combined_score=1.0,
        best_score=1.0,
        correct=True,
        rubric_valid=True,
        rubric_results=[{"id": "score", "pass": True, "evidence": "score exceeded 0.9"}],
        attempt_status="pass",
        log_path=log_path,
    )

    assert "score exceeded 0.9" in stream.getvalue()
    assert str(log_path) in stream.getvalue()


def test_json_renderer_records_redaction_and_non_finite_numbers(tmp_path: Path) -> None:
    # one parseable record per line
    stream = io.StringIO()
    _logger(tmp_path, stream, log_format="json").emit(
        "cycle_start", cycle=3, total_cycles=8, output_dir=tmp_path / "cycle"
    )

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "event": "cycle_start",
        "fields": {"cycle": 3, "output_dir": str(tmp_path / "cycle"), "total_cycles": 8},
        "phase": None,
        "session_id": "session-test",
        "source": "wrapper",
        "timestamp": "2026-08-17T10:00:00.000Z",
        "worker_index": 0,
    }

    # raw output tail is redacted from the console but retained in the journal
    redacted = _sub(tmp_path, "redacted")
    stream = io.StringIO()
    _logger(redacted, stream, log_format="json").emit(
        "paper_generation_failed", output_tail="token=secret", exit=1
    )

    assert "output_tail" not in json.loads(stream.getvalue())["fields"]
    assert _records(redacted)[0]["fields"]["output_tail"] == "token=secret"

    # non-finite numbers normalize to null
    stream = io.StringIO()
    _logger(tmp_path, stream, log_format="json").emit(
        "attempt_summary", combined_score=float("nan"), best_score=float("inf")
    )

    record = json.loads(
        stream.getvalue(),
        parse_constant=lambda value: pytest.fail(f"non-standard JSON constant: {value}"),
    )
    assert record["fields"]["combined_score"] is None
    assert record["fields"]["best_score"] is None


def test_event_journal_permissions_symlink_guard_and_log_format_validation(tmp_path: Path) -> None:
    # sessions append to the journal with private permissions
    journal_dir = _sub(tmp_path, "journal")
    event_path = journal_dir / "events.jsonl"
    event_path.touch(mode=0o644)
    event_path.chmod(0o644)
    first = _logger(journal_dir, log_format="json")
    first.emit("first", value=1)
    first.close()
    second = EventLogger(
        log_format="json", verbose=False, heartbeat_seconds=0, results_dir=journal_dir, worker_index=0,
        stream=io.StringIO(), session_id="session-two",
        timestamp_fn=lambda: "2026-08-17T10:00:01.000Z",
    )
    second.emit("second", value=2)
    second.close()

    records = _records(journal_dir)
    assert [record["event"] for record in records] == ["first", "second"]
    assert [record["session_id"] for record in records] == ["session-test", "session-two"]
    assert os.stat(event_path).st_mode & 0o777 == 0o600

    # an unsafe (symlinked) journal warns once and terminal logging continues
    unsafe_dir = _sub(tmp_path, "unsafe")
    target = unsafe_dir / "elsewhere.log"
    target.write_text("untouched\n", encoding="utf-8")
    (unsafe_dir / "events.jsonl").symlink_to(target)
    stream = io.StringIO()
    logger = _logger(unsafe_dir, stream, log_format="json")

    logger.emit("first")
    logger.emit("second")

    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
    assert events == ["event_journal_unavailable", "first", "second"]
    assert target.read_text(encoding="utf-8") == "untouched\n"

    # unknown log_format is rejected
    with pytest.raises(ValueError, match="log_format"):
        EventLogger(
            log_format="legacy", verbose=False, heartbeat_seconds=0, results_dir=tmp_path, worker_index=0
        )


# ---------------------------------------------------------------------------
# Progress / heartbeat
# ---------------------------------------------------------------------------


def test_redirected_progress_heartbeat_global_cycle_and_interrupt(tmp_path: Path) -> None:
    # redirected progress emits a durable heartbeat and stops cleanly
    durable = _sub(tmp_path, "durable")
    stream, clock = io.StringIO(), [100.0]
    logger = _logger(durable, stream, heartbeat_seconds=60, clock=clock)

    logger.start_progress("attempt", started_at=100.0, cycle=4, total_cycles=20)
    clock[0] = 161.0
    logger.poll_progress()
    logger.stop_progress("attempt")
    before = stream.getvalue()
    clock[0] = 230.0
    logger.poll_progress()

    assert "Cycle 4/20 · Running experiment… 1m 1s · still running" in before
    assert stream.getvalue() == before
    heartbeat = _records(durable)[-1]
    assert heartbeat["event"] == "heartbeat"
    assert heartbeat["fields"]["elapsed_seconds"] == 61.0

    # parallel workers render the global cycle rather than the local one
    stream, clock = io.StringIO(), [100.0]
    logger = _logger(_sub(tmp_path, "global"), stream, heartbeat_seconds=60, clock=clock, worker_index=2)

    # Worker 2's first local cycle is campaign cycle 2 of 3.
    logger.emit("cycle_start", cycle=1, global_cycle=2, total_cycles=3)
    logger.start_progress("attempt", started_at=100.0, cycle=1, global_cycle=2, total_cycles=3)
    clock[0] = 161.0
    logger.poll_progress()
    logger.stop_progress("attempt")
    logger.emit("cycle_end", cycle=1, global_cycle=2, total_cycles=3, elapsed="1m 1s")

    rendered = stream.getvalue()
    assert "W02 · Cycle 2/3\n" in rendered
    assert "W02 · Cycle 2/3 · Running experiment… 1m 1s · still running" in rendered
    assert "W02 · Cycle 2 complete  ·  1m 1s" in rendered
    assert "Cycle 1/" not in rendered

    # an interrupt cancels active progress
    stream, clock = io.StringIO(), [0.0]
    logger = _logger(_sub(tmp_path, "interrupt"), stream, heartbeat_seconds=1, clock=clock)
    logger.start_progress("attempt", started_at=0.0, cycle=1, total_cycles=1)

    logger.emit("interrupted", action="cleanup")
    before = stream.getvalue()
    clock[0] = 10.0
    logger.poll_progress()

    assert stream.getvalue() == before




def test_background_progress_thread_stops_on_close(tmp_path: Path) -> None:
    stream = io.StringIO()
    logger = _logger(tmp_path, stream, log_format="json", heartbeat_seconds=1, progress_tick_seconds=0.01)
    logger.start_progress("attempt", started_at=time.monotonic() - 2, cycle=1, total_cycles=1)

    deadline = time.monotonic() + 1
    while '"event": "heartbeat"' not in stream.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    logger.close()
    rendered = stream.getvalue()
    time.sleep(0.03)

    assert '"event": "heartbeat"' in rendered
    assert stream.getvalue() == rendered


def test_human_renderer_escapes_controls_external_paths_and_soft_errors(tmp_path: Path) -> None:
    # terminal control characters are escaped
    stream = io.StringIO()
    logger = _logger(tmp_path, stream)

    logger.emit(
        "idea_bundle_loaded",
        idea_title="forged\nline\x1b]8;;https://example.com\x07link‮evil",
        target_rubric_count=1,
    )
    logger.emit(
        "attempt_summary", elapsed="1s", exit=0, rubric_valid=True,
        rubric_results=[{"id": "rubric\rname\x1b[2J", "pass": True}], attempt_status="pass",
    )

    rendered = stream.getvalue()
    assert "forged\\nline" in rendered
    assert "rubric\\rname" in rendered
    assert "\\u202e" in rendered
    assert "‮" not in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered

    # non-verbose external error paths do not disclose the absolute path
    stream = io.StringIO()
    _logger(tmp_path, stream).emit(
        "config_error", reason="missing task", path="/home/private/project/task.md"
    )

    assert "/home/private/project" not in stream.getvalue()
    assert "<external>/task.md" in stream.getvalue()

    # an expected incomplete idea is not rendered as a system error
    stream = io.StringIO()
    _logger(tmp_path, stream).emit("idea_incomplete", idea_id="test", next_action="generate_new_idea")

    assert "ERROR" not in stream.getvalue()
    assert "Idea incomplete · continuing with a new idea" in stream.getvalue()


# ---------------------------------------------------------------------------
# TTY styling
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Pipeline renderer / headers / logo
# ---------------------------------------------------------------------------


PIPELINE_HEADER_CASES: list[tuple[str, dict[str, Any], bool]] = [
    (
        "without-investigation-omits-investigation-config",
        {"phases": ["paper", "review"], "investigation_selected": False, "show_run_header": True},
        True,
    ),
    (
        "header-suppressed-keeps-pipeline-status",
        {"phases": ["investigation"], "investigation_selected": True, "show_run_header": False},
        False,
    ),
    ("fail-closed-without-ownership", {"phases": ["investigation"]}, False),
]






def test_render_logo_variants_and_gradient_endpoints() -> None:
    # plain logo is a compact six-line wordmark
    lines = render_logo(colors=False)

    assert len(lines) == 6
    assert all(lines)
    assert max(terminal_width(line) for line in lines) <= 96
    assert all("\x1b" not in line for line in lines)

    # colored logo (full and compact) matches the shinka gradient endpoints
    for case, columns in (("full", None), ("compact", 86)):
        lines = render_logo(colors=True) if columns is None else render_logo(colors=True, columns=columns)

        assert lines[0].startswith("\x1b[38;5;202m"), case
        assert lines[-1].startswith("\x1b[38;5;75m"), case
        assert all(line.endswith("\x1b[0m") for line in lines), case

    # minimal colored logo keeps the gradient endpoints
    minimal = render_logo(colors=True, columns=26)

    assert minimal[0].startswith("\x1b[38;5;202mA")
    assert "\x1b[38;5;75mI" in minimal[0]
    assert minimal[0].endswith("\x1b[0m")

    # compact block logo is used below full width
    lines = render_logo(colors=False, columns=86)

    assert len(lines) == 5
    assert lines[0] == " ███   ███  ████   ███  ███"
    assert lines[-1] == "█   █  ███  ████   ███  ███"
    assert max(terminal_width(line) for line in lines) == 27

    # full logo is used at the exact width boundary
    lines = render_logo(colors=False, columns=87)

    assert len(lines) == 6
    assert max(terminal_width(line) for line in lines) == 87

    # minimal logo is used below the compact width
    assert render_logo(colors=False, columns=26) == ["AGSCI"]


# ---------------------------------------------------------------------------
# Review / paper gate events
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Cost summaries
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# configure_event_logging / inherited pipeline start
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Started lines: TTY spinner vs redirected output
# ---------------------------------------------------------------------------


def _emit_started_and_completed_steps(logger: EventLogger, clock: list[float]) -> None:
    logger.emit("step_start", step="idea_generation", status="started", cycle=1, total_cycles=2)
    logger.start_progress("idea_generation", started_at=clock[0], cycle=1, total_cycles=2)
    clock[0] += 41.0
    logger.poll_progress()
    logger.stop_progress("idea_generation")
    logger.emit("step_end", step="idea_generation", status="completed", exit=0, elapsed="41s")
    logger.emit("task_drafting_checkpoint", gate="design", state="started", label="Generate task design")
    clock[0] += 5.0
    logger.poll_progress()
    logger.emit(
        "task_drafting_checkpoint", gate="design", state="completed", label="Generate task design",
        elapsed="4m 57s",
    )
    logger.emit("preparation_step", step="smoke", label="Search smoke", state="started")
    clock[0] += 5.0
    logger.poll_progress()
    logger.emit("preparation_step", step="smoke", label="Search smoke", state="completed")
    logger.emit("paper_gate", gate="references", state="started", phase="initial")
    clock[0] += 5.0
    logger.poll_progress()
    logger.emit("paper_gate", gate="references", state="completed", phase="initial")




# ---------------------------------------------------------------------------
# Preparation milestones (shared terminal feedback, no cost accounting)
# ---------------------------------------------------------------------------


def _prep(logger: EventLogger, step: str, label: str, state: str, **fields: Any) -> None:
    logger.emit("preparation_step", step=step, label=label, state=state, **fields)


def test_preparation_milestones_console_variants(tmp_path: Path) -> None:
    # default console shows milestones and a durable heartbeat
    default = _sub(tmp_path, "default")
    stream, clock = io.StringIO(), [0.0]
    logger = _logger(default, stream, heartbeat_seconds=60, clock=clock)
    _prep(logger, "runtime_copy", "Copying runtime", "started")
    clock[0] = 61.0
    logger.poll_progress()
    _prep(logger, "runtime_copy", "Copying runtime", "completed", elapsed="1m 1s", elapsed_seconds=61.0)

    assert "Copying runtime…" in stream.getvalue()
    assert "Copying runtime 1m 1s · still running" in stream.getvalue()
    assert "Copying runtime · complete · 1m 1s" in stream.getvalue()
    records = _records(default)
    assert [record["event"] for record in records] == ["preparation_step", "heartbeat", "preparation_step"]
    assert records[1]["fields"]["step"] == "runtime_copy"
    assert all(record["phase"] is None for record in records)
    logger.close()

    # disabled heartbeats retain the milestones
    disabled = _sub(tmp_path, "disabled")
    stream, clock = io.StringIO(), [0.0]
    logger = _logger(disabled, stream, heartbeat_seconds=0, clock=clock)
    _prep(logger, "smoke", "Search smoke", "started")
    clock[0] = 120.0
    logger.poll_progress()
    _prep(logger, "smoke", "Search smoke", "completed")
    assert "Search smoke…" in stream.getvalue()
    assert "Search smoke · complete" in stream.getvalue()
    assert not any(record["event"] == "heartbeat" for record in _records(disabled))
    logger.close()

    # the JSON console preserves structured milestones and heartbeat
    json_dir = _sub(tmp_path, "json")
    stream, clock = io.StringIO(), [0.0]
    logger = _logger(json_dir, stream, log_format="json", heartbeat_seconds=60, clock=clock)
    _prep(logger, "smoke", "Search smoke", "started")
    clock[0] = 61.0
    logger.poll_progress()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["event"] for record in records] == ["preparation_step", "heartbeat"]
    assert records == _records(json_dir)
    logger.close()

    # a TTY refreshes in place and still journals the heartbeat
    tty_dir = _sub(tmp_path, "tty")
    stream, clock = TtyStream(), [0.0]
    logger = _logger(tty_dir, stream, heartbeat_seconds=60, clock=clock)
    _prep(logger, "runtime_copy", "Copying runtime", "started")
    clock[0] = 61.0
    logger.poll_progress()
    assert "\r\x1b[2K" in stream.getvalue()
    assert "still running" not in stream.getvalue()
    assert _records(tty_dir)[-1]["event"] == "heartbeat"
    logger.close()


