"""Project charter: what the user asked for and the limits they set at intake."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

ApprovalPoint = Literal["topic", "experiment_plan", "paper_final", "large_change"]
APPROVAL_POINTS: tuple[ApprovalPoint, ...] = (
    "topic", "experiment_plan", "paper_final", "large_change")
TimeoutAction = Literal["default", "suspend"]


@dataclass(frozen=True)
class Charter:
    idea: str
    title: str
    goal: str
    target_venue: str
    constraints: str
    budget_usd: float
    deadline_hours: float
    approval_points: tuple[ApprovalPoint, ...] = ("topic",)
    chat_language: str = "zh"
    report_language: str = "zh"
    paper_language: str = "en"
    approval_timeout_hours: float = 72.0
    on_timeout: TimeoutAction = "default"
    max_deep_searches: int = 2
    risk_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Charter:
        fields = dict(data)
        fields["approval_points"] = tuple(fields.get("approval_points", ("topic",)))
        return cls(**fields)


CHARTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "goal", "target_venue", "constraints", "budget_usd",
                 "deadline_hours", "approval_points", "chat_language", "report_language",
                 "paper_language", "on_timeout", "risk_notes"],
    "properties": {
        "title": {"type": "string"},
        "goal": {"type": "string"},
        "target_venue": {"type": "string"},
        "constraints": {"type": "string"},
        "budget_usd": {"type": "number"},
        "deadline_hours": {"type": "number"},
        "approval_points": {"type": "array",
                            "items": {"type": "string", "enum": list(APPROVAL_POINTS)}},
        "chat_language": {"type": "string", "enum": ["zh", "en"]},
        "report_language": {"type": "string", "enum": ["zh", "en"]},
        "paper_language": {"type": "string", "enum": ["zh", "en"]},
        "on_timeout": {"type": "string", "enum": ["default", "suspend"]},
        "risk_notes": {"type": "string"},
    },
}
