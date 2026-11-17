"""Work orders: the only way the main agent hands work to an executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

Permission = Literal["small", "large", "approval"]
ResultStatus = Literal["done", "failed", "needs_help", "limit_exceeded", "crashed"]
# Statuses the executor itself reports in its final structured message.
REPORTED_STATUSES: tuple[ResultStatus, ...] = ("done", "failed", "needs_help")


@dataclass(frozen=True)
class Limits:
    wall_seconds: int
    max_tokens: int | None = None
    max_attempts: int = 3


@dataclass(frozen=True)
class WorkOrder:
    id: str
    role: str
    executor: str
    goal: str
    deliverables: tuple[str, ...]
    acceptance: str
    limits: Limits
    inputs: tuple[str, ...] = ()
    permission: Permission = "small"
    require_sources: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkOrder:
        fields = dict(data)
        fields["limits"] = Limits(**fields["limits"])
        fields["deliverables"] = tuple(fields["deliverables"])
        fields["inputs"] = tuple(fields.get("inputs", ()))
        return cls(**fields)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens,
                     self.cached_input_tokens + other.cached_input_tokens,
                     self.output_tokens + other.output_tokens)


@dataclass(frozen=True)
class WorkResult:
    status: ResultStatus
    summary: str
    deliverables: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    # Cumulative for ``session_id`` (a resumed session reports its running total).
    usage: Usage = field(default_factory=Usage)
    session_id: str | None = None
    wall_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkResult:
        fields = dict(data)
        fields["usage"] = Usage(**fields.get("usage", {}))
        fields["deliverables"] = tuple(fields.get("deliverables", ()))
        fields["sources"] = tuple(fields.get("sources", ()))
        return cls(**fields)


# JSON Schema of the executor's final message (strict: every property required).
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "deliverables", "sources"],
    "properties": {
        "status": {"type": "string", "enum": list(REPORTED_STATUSES)},
        "summary": {"type": "string"},
        "deliverables": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}

EventSink = Callable[[str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class Attempt:
    """Everything an executor needs for one attempt at a work order."""

    order: WorkOrder
    number: int
    workspace: Path
    attempt_dir: Path
    timeout_seconds: float
    resume_session: str | None = None


class Executor(Protocol):
    name: str
    supports_resume: bool

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        """Run one attempt. ``emit`` records ``process`` / ``session`` / ``half_time`` events
        as soon as they happen, so a crashed kernel can still find and resume the work."""
        ...

    def session_usage(self, session_id: str) -> Usage | None:
        """Cumulative usage of a session from the agent's own records, if it keeps any."""
        ...
