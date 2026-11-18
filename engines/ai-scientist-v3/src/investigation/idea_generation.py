"""Validation helpers for generated idea artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.orchestrator.idea_bundle import (
    IdeaBundleValidationError,
    normalize_idea_bundle,
    normalize_target_rubrics_payload,
    without_target_rubric_verification,
)


class IdeaArtifactsError(ValueError):
    """Generated idea artifacts are missing, invalid, or inconsistent."""

    def __init__(self, reason: str, *, path: Path | None = None, detail: str = "") -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(detail or reason)


@dataclass(frozen=True)
class IdeaArtifacts:
    bundle: dict[str, Any]
    target_rubrics: list[dict[str, Any]]


def load_idea_artifacts(
    workspace: Path,
    cycle_index: int,
    read_json: Callable[[Path], dict[str, Any] | None],
) -> IdeaArtifacts:
    """Load and cross-check the required idea-generation outputs."""
    bundle_path = workspace / "idea_bundle.json"
    bundle_raw = read_json(bundle_path)
    if bundle_raw is None:
        raise IdeaArtifactsError(
            "missing_or_invalid_idea_bundle",
            path=bundle_path,
        )
    bundle_raw = without_target_rubric_verification(bundle_raw)
    try:
        bundle = normalize_idea_bundle(bundle_raw, cycle_index)
    except IdeaBundleValidationError as exc:
        raise IdeaArtifactsError(
            "invalid_idea_bundle",
            path=bundle_path,
            detail=str(exc),
        ) from exc

    rubrics_path = workspace / "target_rubrics.json"
    if not rubrics_path.exists():
        raise IdeaArtifactsError("missing_target_rubrics", path=rubrics_path)
    try:
        rubrics_raw = json.loads(rubrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdeaArtifactsError(
            "invalid_target_rubrics_json",
            path=rubrics_path,
            detail=str(exc),
        ) from exc
    rubrics_raw = without_target_rubric_verification(rubrics_raw)
    try:
        target_rubrics = normalize_target_rubrics_payload(rubrics_raw)
    except IdeaBundleValidationError as exc:
        raise IdeaArtifactsError(
            "invalid_target_rubrics_payload",
            path=rubrics_path,
            detail=str(exc),
        ) from exc
    if target_rubrics != bundle["target_rubrics"]:
        raise IdeaArtifactsError("target_rubrics_mismatch", path=rubrics_path)
    return IdeaArtifacts(bundle=bundle, target_rubrics=target_rubrics)
