"""Summarize an engine run directory (the AI-Scientist-v3 output contract)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PhaseResult:
    name: str
    status: str
    return_code: int | None
    outcome: str | None = None


@dataclass(frozen=True)
class RunSummary:
    run_dir: Path
    status: str | None
    """Final ``pipeline_end`` status of the latest session (``completed``, ``failed``, ...)."""
    phases: dict[str, PhaseResult] = field(default_factory=dict)
    best_program: dict[str, Any] | None = None
    investigation_outcome: str | None = None
    paper_pdf: Path | None = None
    estimated_cost_usd: float | None = None
    wall_seconds: float | None = None
    """Sum of ``pipeline_start`` → ``pipeline_end`` spans over all sessions of the run."""

    @property
    def completed(self) -> bool:
        return self.status == "completed"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a run killed mid-write can leave a partial last line
        if isinstance(event, dict):
            events.append(event)
    return events


def load_run(run_dir: Path) -> RunSummary:
    """Read engine artifacts; missing files yield ``None`` fields rather than errors.

    Later events win, so resumed runs report each phase's most recent result.
    """
    run_dir = Path(run_dir)
    status: str | None = None
    phases: dict[str, PhaseResult] = {}
    cost: float | None = None
    wall: float | None = None
    started: datetime | None = None
    for event in _events(run_dir / "pipeline_events.jsonl"):
        fields = event.get("fields") or {}
        kind = event.get("event")
        ts = _timestamp(event)
        if kind == "pipeline_start":
            started = ts
        elif kind == "pipeline_end" and started is not None and ts is not None:
            wall = (wall or 0.0) + (ts - started).total_seconds()
            started = None
        if kind == "phase_end" and fields.get("phase_name"):
            name = str(fields["phase_name"])
            phases[name] = PhaseResult(
                name, str(fields.get("status")), fields.get("return_code"), fields.get("outcome")
            )
        elif kind == "pipeline_end":
            status = fields.get("status")
        elif kind == "cost_summary" and fields.get("run_total_cost_usd") is not None:
            try:
                cost = float(fields["run_total_cost_usd"])
            except (TypeError, ValueError):
                pass
    completion = _read_json(run_dir / ".investigation_completion.json") or {}
    pdf = run_dir / "paper" / "paper.pdf"
    return RunSummary(
        run_dir=run_dir,
        status=status,
        phases=phases,
        best_program=_read_json(run_dir / "investigation" / "best_program_manifest.json"),
        investigation_outcome=completion.get("outcome"),
        paper_pdf=pdf if pdf.is_file() else None,
        estimated_cost_usd=cost,
        wall_seconds=wall,
    )


def _timestamp(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event["timestamp"]))
    except (KeyError, ValueError):
        return None
