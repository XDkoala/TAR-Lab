"""What the interface shows, computed from the ledger and the agents' event files."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tar_lab.core.charter import Charter
from tar_lab.core.ledger import Event, Ledger, OrderState
from tar_lab.core.pricing import estimate_usd
from tar_lab.core.workorder import Usage
from tar_lab.orchestration.kernel import Project

from .activity import ActivityTail, Step
from .system import GpuInfo

MAIN_AGENT = "main"
# A decision directory untouched this long belongs to a runner that died mid-decision.
STALE_DECISION_SECONDS = 600


@dataclass
class AgentView:
    id: str
    role: str
    executor: str
    model: str
    status: str
    attempt: int
    elapsed: float
    limit: float
    usage: Usage
    cost_usd: float | None
    steps: list[Step]
    goal: str = ""
    summary: str = ""
    gpu_mib: int | None = None


@dataclass
class ProjectView:
    title: str
    phase: str  # intake | finished | suspended | approval | question | thinking | working | idle
    elapsed_h: float
    deadline_h: float
    spent_usd: float
    budget_usd: float
    deep_used: int
    deep_max: int
    questions: dict[str, dict[str, Any]]
    agents: list[AgentView]
    unread: int
    orders: dict[str, OrderState] = field(default_factory=dict)
    gpu: GpuInfo | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class ProjectState:
    def __init__(self, root: Path, models: Mapping[str, str], main_model: str,
                 clock: Callable[[], datetime] = _now) -> None:
        self.project = Project(root)
        if not self.project.ledger_path.exists():
            raise FileNotFoundError(f"{root} is not a TAR-Lab project")
        self.ledger = Ledger(self.project.ledger_path)
        self.models, self.main_model, self.clock = models, main_model, clock
        self._tails: dict[Path, ActivityTail] = {}

    def close(self) -> None:
        self.ledger.close()

    def _tail(self, path: Path) -> list[Step]:
        tail = self._tails.get(path)
        if tail is None:
            tail = self._tails[path] = ActivityTail(path, self.project.workspace)
        return tail.poll()

    def model_of(self, executor: str) -> str:
        return self.models.get(executor, executor)

    def order_cost(self, state: OrderState) -> float | None:
        if state.result is not None and state.result.cost_usd is not None:
            return state.result.cost_usd
        return estimate_usd(self.model_of(state.order.executor), state.usage)

    def order_view(self, oid: str, state: OrderState, gpu: GpuInfo | None = None) -> AgentView:
        now = self.clock()
        elapsed = state.used_seconds
        if state.status == "running" and state.started_at is not None:
            elapsed += (now - state.started_at).total_seconds()
        steps = (self._tail(self.project.attempt_dir(oid, state.attempts) / "events.jsonl")
                 if state.attempts else [])
        return AgentView(
            oid, state.order.role, state.order.executor, self.model_of(state.order.executor),
            state.status, state.attempts, elapsed, float(state.order.limits.wall_seconds),
            state.usage, self.order_cost(state), steps, state.order.goal,
            state.result.summary if state.result else "",
            gpu.by_pgid.get(state.pgid) if gpu and state.pgid else None)

    def _main_agent(self, decisions: int) -> AgentView | None:
        d = self.project.root / "pi" / f"decision-{decisions + 1:03d}"
        events = d / "events.jsonl"
        if (d / "answer.json").exists() or not events.exists():
            return None
        age = time.time() - events.stat().st_mtime
        if age > STALE_DECISION_SECONDS:
            return None
        started = (d / "prompt.md").stat().st_mtime if (d / "prompt.md").exists() else time.time()
        return AgentView(MAIN_AGENT, "main", "codex", self.main_model, "thinking", 1,
                         time.time() - started, 0.0, Usage(), None, self._tail(events))

    def snapshot(self, gpu: GpuInfo | None = None) -> ProjectView:
        events = self.ledger.events()
        by_kind: dict[str, list[Event]] = {}
        for e in events:
            by_kind.setdefault(e.kind, []).append(e)
        charters = by_kind.get("project.charter", []) + by_kind.get("project.charter_changed", [])
        charter = (Charter.from_dict(max(charters, key=lambda e: e.seq).payload["charter"])
                   if charters else None)
        orders = self.ledger.orders()
        decisions = by_kind.get("pi.decision", [])
        last_decision = decisions[-1].seq if decisions else 0

        spent = 0.0
        for state in orders.values():
            spent += self.order_cost(state) or 0.0
        for kind in ("pi.decision", "intake.answers", "project.charter"):
            for e in by_kind.get(kind, []):
                if "usage" in e.payload:
                    spent += estimate_usd(self.main_model, Usage(**e.payload["usage"])) or 0.0

        answered = {e.payload["qid"] for e in by_kind.get("user.reply", [])}
        questions = {e.payload["qid"]: e.payload for e in by_kind.get("user.question", [])
                     if e.payload["qid"] not in answered}
        agents = [self.order_view(oid, st, gpu) for oid, st in orders.items()
                  if st.status == "running"]
        main = self._main_agent(len(decisions))
        if main is not None:
            agents.insert(0, main)

        last = max((e for k in ("project.finished", "project.suspended", "project.resumed")
                    for e in by_kind.get(k, [])), key=lambda e: e.seq, default=None)
        if charter is None:
            phase = "intake"
        elif by_kind.get("project.finished"):
            phase = "finished"
        elif last is not None and last.kind == "project.suspended":
            phase = "suspended"
        elif any(q.get("kind") == "approval" for q in questions.values()):
            phase = "approval"
        elif questions:
            phase = "question"
        elif main is not None:
            phase = "thinking"
        elif len(agents) > 0:
            phase = "working"
        else:
            phase = "idle"

        start = by_kind["project.charter"][0].ts if by_kind.get("project.charter") else None
        deep = sum(1 for e in by_kind.get("undermind.call", [])
                   if e.payload.get("tool") == "launch_deep_search"
                   and not e.payload.get("is_error"))
        return ProjectView(
            title=charter.title if charter else self.project.root.name,
            phase=phase,
            elapsed_h=(self.clock() - start).total_seconds() / 3600 if start else 0.0,
            deadline_h=charter.deadline_hours if charter else 0.0,
            spent_usd=spent,
            budget_usd=charter.budget_usd if charter else 0.0,
            deep_used=deep,
            deep_max=charter.max_deep_searches if charter else 0,
            questions=questions,
            agents=agents,
            unread=sum(1 for e in by_kind.get("user.message", []) if e.seq > last_decision),
            orders=orders,
            gpu=gpu,
        )
