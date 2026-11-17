"""Run kernel: executes work orders from the ledger with hard limits and crash recovery."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tar_lab.core.ledger import Ledger, OrderState
from tar_lab.core.workorder import (
    Attempt,
    Executor,
    McpServer,
    Usage,
    WorkOrder,
    WorkResult,
)
from tar_lab.integrations.cli_agents.process import kill_group

# Events that need the main agent's judgement.
ATTENTION = frozenset({"order.finished", "order.half_time", "order.over_budget"})

AttentionHook = Callable[[str, OrderState], None]
McpHook = Callable[[WorkOrder], Mapping[str, McpServer]]
_GIT_ID = ("-c", "user.name=TAR-Lab", "-c", "user.email=tar-lab@localhost")


@dataclass
class Project:
    root: Path

    def __post_init__(self) -> None:
        # Executors run inside the workspace, so every path handed to them must be absolute.
        self.root = self.root.resolve()

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.sqlite"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    def attempt_dir(self, order_id: str, attempt: int) -> Path:
        return self.root / "orders" / order_id / f"attempt-{attempt}"

    def init(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not (self.workspace / ".git").exists():
            subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)


@dataclass
class Kernel:
    project: Project
    ledger: Ledger
    executors: Mapping[str, Executor]
    on_attention: AttentionHook = field(default=lambda _kind, _state: None)
    mcp_for: McpHook = field(default=lambda _order: {})

    def submit(self, order: WorkOrder) -> None:
        if order.id in self.ledger.orders():
            raise ValueError(f"work order {order.id} already exists")
        if order.executor not in self.executors:
            raise ValueError(f"unknown executor {order.executor!r}")
        self.ledger.append("order.created", order.id, {"order": order.to_dict()})

    def recover(self) -> list[str]:
        """Mark orders left ``running`` by a dead kernel as interrupted; kill stray agents."""
        recovered = []
        for oid, state in self.ledger.orders().items():
            if state.status != "running":
                continue
            killed = kill_group(state.pgid) if state.pgid else False
            elapsed = 0.0
            if state.started_at is not None:
                # The dead attempt provably ran until its last ledger event or its last output.
                last = self.ledger.events(oid)[-1].ts.timestamp()
                attempt_dir = self.project.attempt_dir(oid, state.attempts)
                if attempt_dir.is_dir():
                    last = max([last, *(f.stat().st_mtime for f in attempt_dir.iterdir())])
                elapsed = max(0.0, last - state.started_at.timestamp())
            self._record_session_usage(state)
            self.ledger.append("order.interrupted", oid, {
                "reason": "kernel restarted", "elapsed": round(elapsed, 1),
                "killed_stray_process": killed})
            recovered.append(oid)
        return recovered

    def run_pending(self) -> list[str]:
        """Run every non-terminal order once to completion; returns the processed ids."""
        self.recover()
        processed = []
        for oid, state in self.ledger.orders().items():
            if state.status in ("pending", "interrupted"):
                self._run(state)
                processed.append(oid)
        return processed

    def run_next(self) -> str | None:
        """Run the oldest non-terminal order to completion; returns its id."""
        for oid, state in self.ledger.orders().items():
            if state.status in ("pending", "interrupted"):
                self._run(state)
                return oid
        return None

    def _run(self, state: OrderState) -> None:
        order = state.order
        executor = self.executors[order.executor]
        while True:
            remaining = order.limits.wall_seconds - state.used_seconds
            if remaining <= 0 or state.attempts >= order.limits.max_attempts:
                reason = "wall time" if remaining <= 0 else "attempts"
                self._finish(order.id, WorkResult(
                    "limit_exceeded", f"Limit reached ({reason}) before completion.",
                    usage=state.usage_by_session.get(state.session_id or "", Usage()),
                    session_id=state.session_id))
                return
            resume = (state.session_id if state.status == "interrupted"
                      and executor.supports_resume else None)
            number = state.attempts + 1
            self.ledger.append("order.started", order.id, {
                "attempt": number, "snapshot": self._snapshot(order.id, number),
                "resume_session": resume, "timeout": round(remaining, 1)})
            attempt = Attempt(order, number, self.project.workspace,
                              self.project.attempt_dir(order.id, number), remaining, resume,
                              self.mcp_for(order))
            result = executor.run(attempt, lambda kind, payload: self._emit(order.id, kind, payload))
            if result.status == "crashed" and resume is not None:
                # A failed resume falls back to a fresh attempt on the current workspace.
                self._record_session_usage(self.ledger.orders()[order.id])
                self.ledger.append("order.interrupted", order.id,
                                   {"reason": "resume failed", "error": result.error})
                state = self.ledger.orders()[order.id]
                state.session_id = None
                continue
            self._finish(order.id, result)
            return

    def _record_session_usage(self, state: OrderState) -> None:
        """Book what an abandoned attempt spent, from the agent's own session records."""
        if state.session_id is None:
            return
        usage = self.executors[state.order.executor].session_usage(state.session_id)
        if usage is not None:
            self.ledger.append("order.usage", state.order.id,
                               {"session_id": state.session_id, "usage": asdict(usage)})

    def _finish(self, order_id: str, result: WorkResult) -> None:
        self._emit(order_id, "order.finished", {"result": result.to_dict()})
        state = self.ledger.orders()[order_id]
        cap = state.order.limits.max_tokens
        if cap is not None and state.usage.total_tokens > cap:
            self._emit(order_id, "order.over_budget",
                       {"tokens": state.usage.total_tokens, "max_tokens": cap})

    def _emit(self, order_id: str, kind: str, payload: Mapping[str, Any]) -> None:
        self.ledger.append(kind, order_id, payload)
        if kind in ATTENTION:
            self.on_attention(kind, self.ledger.orders()[order_id])

    def _snapshot(self, order_id: str, attempt: int) -> str:
        """Commit the workspace so every attempt can be diffed or reverted."""
        ws = str(self.project.workspace)
        subprocess.run(["git", "-C", ws, "add", "-A"], check=True)
        subprocess.run(["git", *_GIT_ID, "-C", ws, "commit", "-q", "--allow-empty",
                        "--no-verify", "-m", f"tar-lab: before {order_id} attempt {attempt}"],
                       check=True)
        return subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
