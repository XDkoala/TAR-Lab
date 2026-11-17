"""Append-only project ledger in SQLite; project state is a fold over its events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .workorder import Usage, WorkOrder, WorkResult

TERMINAL = frozenset({"done", "failed", "needs_help", "limit_exceeded", "crashed", "cancelled"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    order_id TEXT,
    payload TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Event:
    seq: int
    ts: datetime
    kind: str
    order_id: str | None
    payload: dict[str, Any]


@dataclass
class OrderState:
    order: WorkOrder
    status: str = "pending"
    attempts: int = 0
    used_seconds: float = 0.0
    usage_by_session: dict[str, Usage] = field(default_factory=dict)
    session_id: str | None = None
    pgid: int | None = None
    snapshot: str | None = None
    result: WorkResult | None = None
    started_at: datetime | None = None

    @property
    def usage(self) -> Usage:
        return sum(self.usage_by_session.values(), Usage())

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def append(self, kind: str, order_id: str | None = None,
               payload: Mapping[str, Any] | None = None) -> Event:
        body = json.dumps(dict(payload or {}), ensure_ascii=False, default=str)
        with self._lock:
            ts = datetime.now(UTC)
            cur = self._db.execute(
                "INSERT INTO events (ts, kind, order_id, payload) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), kind, order_id, body))
        assert cur.lastrowid is not None
        return Event(cur.lastrowid, ts, kind, order_id, json.loads(body))

    def events(self, order_id: str | None = None) -> list[Event]:
        query = "SELECT seq, ts, kind, order_id, payload FROM events"
        args: tuple[str, ...] = ()
        if order_id is not None:
            query, args = query + " WHERE order_id = ?", (order_id,)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY seq", args).fetchall()
        return [Event(s, datetime.fromisoformat(t), k, o, json.loads(p)) for s, t, k, o, p in rows]

    def orders(self) -> dict[str, OrderState]:
        states: dict[str, OrderState] = {}
        for ev in self.events():
            if ev.kind == "order.created":
                assert ev.order_id is not None
                states[ev.order_id] = OrderState(WorkOrder.from_dict(ev.payload["order"]))
                continue
            state = states.get(ev.order_id or "")
            if state is not None:
                _apply(state, ev)
        return states


def _apply(state: OrderState, ev: Event) -> None:
    p = ev.payload
    if ev.kind == "order.started":
        state.status, state.attempts, state.started_at = "running", p["attempt"], ev.ts
        state.snapshot, state.pgid = p.get("snapshot"), None
    elif ev.kind == "order.process":
        state.pgid = p["pgid"]
    elif ev.kind == "order.session":
        state.session_id = p["session_id"]
    elif ev.kind == "order.usage":
        state.usage_by_session[p["session_id"]] = Usage(**p["usage"])
    elif ev.kind in ("order.finished", "order.interrupted"):
        if state.started_at is not None:
            # A kernel restart reports the elapsed time it can prove, not the downtime.
            state.used_seconds += p.get("elapsed", (ev.ts - state.started_at).total_seconds())
            state.started_at = None
        state.pgid = None
        if ev.kind == "order.interrupted":
            state.status = "interrupted"
            return
        result = WorkResult.from_dict(p["result"])
        state.status, state.result = result.status, result
        state.session_id = result.session_id or state.session_id
        if state.session_id is not None:
            state.usage_by_session[state.session_id] = result.usage
    elif ev.kind == "order.cancelled":
        if state.started_at is not None:
            state.used_seconds += (ev.ts - state.started_at).total_seconds()
            state.started_at = None
        state.status, state.pgid = "cancelled", None
        if "result" in p:
            result = WorkResult.from_dict(p["result"])
            state.result = result
            if result.session_id:
                state.session_id = result.session_id
                state.usage_by_session[result.session_id] = result.usage
