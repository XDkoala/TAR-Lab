"""Summaries of recent attempts for idea prompts; never used to decide acceptance."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


MAX_SEARCH_FEEDBACK_BYTES = 8 * 1024


def summarize_recent_attempts(task_dir: Path, entries: list[Any]) -> str:
    """Render historical outcomes as bounded data, never as prompt instructions."""
    keys = (
        "cycle_index", "idea_id", "attempt_id", "attempt_status",
        "run_exit_code", "rubric_overall_pass",
    )
    lines = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        summary = {key: entry[key] for key in keys if key in entry}
        metrics = entry.get("metrics")
        if isinstance(metrics, dict):
            scientific: dict[str, Any] = {}
            score = metrics.get("combined_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                try:
                    if math.isfinite(score):
                        scientific["combined_score"] = score
                except OverflowError:
                    pass
            if isinstance(metrics.get("correct"), bool):
                scientific["correct"] = metrics["correct"]
            summary["metrics"] = scientific
        encoded = json.dumps(summary, ensure_ascii=True, sort_keys=True)
        # Keep each record intact; oversized metadata must not displace scientific feedback.
        if len(encoded.encode()) > MAX_SEARCH_FEEDBACK_BYTES + 1024:
            encoded = json.dumps({"metrics": summary.get("metrics", {})}, ensure_ascii=True)
        lines.append("- " + encoded)
    return "\n".join(lines) if lines else "- no recent attempts"
