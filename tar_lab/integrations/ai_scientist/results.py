"""Summarize an engine run directory.

The investigation loop writes its own event journal (``events.jsonl``: ``finished`` or
``campaign_failed``, ``cost_summary``) and ``best_program_manifest.json``. Runs of the
upstream pipeline keep those files under ``investigation/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunSummary:
    run_dir: Path
    investigation_dir: Path
    status: str | None
    """``completed`` (the campaign finished), ``failed``, or ``None`` (still running/killed)."""
    best_program: dict[str, Any] | None = None
    outcome: str | None = None
    """``winner`` when an idea beat the baseline program, ``no_winner`` otherwise."""
    estimated_cost_usd: float | None = None
    wall_seconds: float | None = None
    cycles_completed: int | None = None

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


def _timestamp(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event["timestamp"]))
    except (KeyError, ValueError):
        return None


def load_run(run_dir: Path) -> RunSummary:
    """Read engine artifacts; missing files yield ``None`` fields rather than errors."""
    run_dir = Path(run_dir)
    nested = run_dir / "investigation"
    inv = nested if (nested / "events.jsonl").exists() else run_dir
    status: str | None = None
    cost: float | None = None
    cycles: int | None = None
    events = _events(inv / "events.jsonl")
    for event in events:
        fields = event.get("fields") or {}
        kind = event.get("event")
        if kind == "finished":
            status = "completed"
            cycles = fields.get("cycles_completed")
        elif kind == "campaign_failed":
            status = "failed"
        elif kind == "cost_summary" and fields.get("run_total_cost_usd") is not None:
            try:
                cost = float(fields["run_total_cost_usd"])
            except (TypeError, ValueError):
                pass
    stamps = [t for t in map(_timestamp, events) if t is not None]
    wall = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else None
    best = _read_json(inv / "best_program_manifest.json")
    outcome = None
    if status == "completed":
        outcome = "no_winner" if not best or best.get("source_attempt_id") == "bootstrap" \
            else "winner"
    return RunSummary(run_dir, inv, status, best, outcome, cost, wall, cycles)
