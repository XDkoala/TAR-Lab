"""Run kernel: executes work orders from the ledger with hard limits and crash recovery."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
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
from tar_lab.integrations.cli_agents.process import kill_group, quota_error

# Events that need the main agent's judgement.
ATTENTION = frozenset({"order.finished", "order.half_time", "order.over_budget"})

AttentionHook = Callable[[str, OrderState], None]
McpHook = Callable[[WorkOrder], Mapping[str, McpServer]]
_GIT_ID = ("-c", "user.name=TAR-Lab", "-c", "user.email=tar-lab@localhost")
# Snapshots track what agents write by hand (code, configs, reports, small results). Rebuildable
# environments and caches, and large files such as datasets, wheels and checkpoints, would make
# every snapshot copy gigabytes into git; they are listed in the ledger instead.
SNAPSHOT_SKIP_DIRS = frozenset({".git", ".venv", "venv", ".cache", "__pycache__", "node_modules",
                                ".mypy_cache", ".pytest_cache", ".ruff_cache",
                                ".ipynb_checkpoints"})
SNAPSHOT_MAX_FILE_BYTES = 10 * 1024 * 1024
# How long an executor that reported an exhausted quota or rate limit is left alone.
EXECUTOR_COOLDOWN = timedelta(minutes=30)
# Git stores a repository cloned inside the workspace as a bare commit pointer, so its files
# and local edits are not in the snapshot. Each one is recorded here instead.
NESTED_DIR = ".tar-lab/nested"
_EXCLUDE_BEGIN, _EXCLUDE_END = "# >>> tar-lab snapshot excludes", "# <<< tar-lab snapshot excludes"


@dataclass(frozen=True)
class SnapshotExcludes:
    dirs: tuple[str, ...]
    large_files: tuple[tuple[str, int], ...]  # (path relative to the workspace, bytes)
    nested_repos: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        biggest = sorted(self.large_files, key=lambda f: -f[1])[:20]
        return {"dirs": list(self.dirs), "nested_repos": list(self.nested_repos),
                "large_files": len(self.large_files),
                "large_bytes": sum(b for _, b in self.large_files),
                "largest": [{"path": p, "bytes": b} for p, b in biggest],
                "max_file_bytes": SNAPSHOT_MAX_FILE_BYTES}


def snapshot_excludes(workspace: Path) -> SnapshotExcludes:
    """Find what the snapshot skips, by file size only (no file is read)."""
    dirs, large, nested = [], [], []
    for top, subdirs, files in os.walk(workspace):
        rel_top = Path(top).relative_to(workspace)
        for d in list(subdirs):
            if d in SNAPSHOT_SKIP_DIRS:
                subdirs.remove(d)
                if d != ".git":
                    dirs.append((rel_top / d).as_posix())
            elif (Path(top) / d / ".git").exists():
                subdirs.remove(d)
                nested.append((rel_top / d).as_posix())
        for f in files:
            path = Path(top) / f
            try:
                size = path.lstat().st_size
            except OSError:
                continue
            if size > SNAPSHOT_MAX_FILE_BYTES and not path.is_symlink():
                large.append(((rel_top / f).as_posix(), size))
    return SnapshotExcludes(tuple(sorted(dirs)), tuple(sorted(large)), tuple(sorted(nested)))


def _git(repo: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=env, check=False).stdout.strip()


def nested_repo_state(workspace: Path, rel: str) -> dict[str, Any]:
    """Origin, commit and a binary patch of every local change (new files included, files over
    ``SNAPSHOT_MAX_FILE_BYTES`` listed instead), built in a throwaway index so the
    repository's own index is untouched. The patch is written under ``NESTED_DIR``."""
    repo = workspace / rel
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD") or None
    state: dict[str, Any] = {"path": rel, "remote": _git(repo, "remote", "get-url", "origin")
                             or None, "head": head, "patch": None, "skipped_large": []}
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if head:
            _git(repo, "read-tree", "HEAD", env=env)
        _git(repo, "add", "-A", env=env)
        base = head or _git(repo, "hash-object", "-t", "tree", "/dev/null")
        for name in _git(repo, "diff", "--cached", "--name-only", base, env=env).splitlines():
            f = repo / name
            if f.is_file() and not f.is_symlink() and f.stat().st_size > SNAPSHOT_MAX_FILE_BYTES:
                _git(repo, "rm", "--cached", "-q", "--", name, env=env)
                state["skipped_large"].append(name)
        patch = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--binary", base],
                               capture_output=True, env=env, check=False).stdout
    if patch:
        target = workspace / NESTED_DIR / (rel.replace("/", "__") + ".patch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(patch)
        state["patch"] = target.relative_to(workspace).as_posix()
    return state


def _record_nested(workspace: Path, repos: tuple[str, ...]) -> None:
    out = workspace / NESTED_DIR
    for old in out.glob("*.patch") if out.is_dir() else ():
        old.unlink()
    if not repos:
        return
    states = [nested_repo_state(workspace, rel) for rel in repos]
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "restore": "git clone <remote> <path> && git -C <path> checkout <head> && "
                   "git -C <path> apply --binary <workspace>/<patch>",
        "repos": states}, indent=2, ensure_ascii=False) + "\n")


def _glob_escape(path: str) -> str:
    return "".join("\\" + c if c in "*?[]\\!# " else c for c in path)


def _write_excludes(workspace: Path, ex: SnapshotExcludes) -> None:
    info = workspace / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "exclude"
    text = path.read_text() if path.exists() else ""
    if _EXCLUDE_BEGIN in text:
        head, _, rest = text.partition(_EXCLUDE_BEGIN)
        text = head + rest.partition(_EXCLUDE_END)[2].lstrip("\n")
    names = sorted(SNAPSHOT_SKIP_DIRS - {".git"})
    lines = [_EXCLUDE_BEGIN, *(f"{n}/" for n in names),
             *("/" + _glob_escape(p) for p, _ in ex.large_files), _EXCLUDE_END]
    path.write_text(text.rstrip("\n") + ("\n" if text.strip() else "") + "\n".join(lines) + "\n")


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
    # Executor to switch to when one runs out of quota, e.g. {"codex": "devin"}.
    fallbacks: Mapping[str, str] = field(default_factory=dict)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # Orders may run on several threads; git snapshots of the shared workspace may not.
    _snapshot_lock: threading.Lock = field(default_factory=threading.Lock, init=False,
                                           repr=False)

    def unavailable(self) -> dict[str, datetime]:
        """Executors cooling off after a quota error, with the time they may be tried again."""
        now, until = self.clock(), {}
        for e in self.ledger.events():
            if e.kind == "executor.unavailable":
                until[e.payload["executor"]] = datetime.fromisoformat(e.payload["until"])
        return {name: t for name, t in until.items() if t > now}

    def mark_unavailable(self, executor: str, error: str, order_id: str | None = None) -> None:
        self.ledger.append("executor.unavailable", order_id, {
            "executor": executor, "error": error[:500],
            "until": (self.clock() + EXECUTOR_COOLDOWN).isoformat()})

    def fallback_for(self, executor: str) -> str | None:
        fallback = self.fallbacks.get(executor)
        usable = fallback in self.executors and fallback not in self.unavailable()
        return fallback if usable else None

    def submit(self, order: WorkOrder) -> None:
        if order.id in self.ledger.orders():
            raise ValueError(f"work order {order.id} already exists")
        if order.executor not in self.executors:
            raise ValueError(f"unknown executor {order.executor!r}")
        self.ledger.append("order.created", order.id, {"order": order.to_dict()})

    def cancel(self, order_id: str, reason: str) -> bool:
        """Cancel a pending order, or stop a running one; returns False if already terminal.

        Safe to call from another process: a running agent is killed through its recorded
        process group, and the kernel running it then books the order as cancelled."""
        state = self.ledger.orders().get(order_id)
        if state is None or state.terminal:
            return False
        if state.status == "running":
            self.ledger.append("order.cancel_requested", order_id, {"reason": reason})
            if state.pgid:
                kill_group(state.pgid)
        else:
            self.ledger.append("order.cancelled", order_id, {"reason": reason})
        return True

    def _cancel_requested(self, order_id: str, attempt: int) -> str | None:
        started = False
        for ev in self.ledger.events(order_id):
            if ev.kind == "order.started" and ev.payload["attempt"] == attempt:
                started = True
            elif started and ev.kind == "order.cancel_requested":
                return str(ev.payload["reason"])
        return None

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

    def run_order(self, order_id: str) -> None:
        state = self.ledger.orders()[order_id]
        if state.status in ("pending", "interrupted"):
            self._run(state)

    def _run(self, state: OrderState) -> None:
        oid = state.order.id

        def emit(kind: str, payload: Mapping[str, Any]) -> None:
            self._emit(oid, kind, payload)

        while True:
            order = state.order
            executor = self.executors[order.executor]
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
            snapshot, excluded = self._snapshot(order.id, number)
            self.ledger.append("order.started", order.id, {
                "attempt": number, "snapshot": snapshot, "snapshot_excluded": excluded,
                "resume_session": resume, "timeout": round(remaining, 1)})
            attempt = Attempt(order, number, self.project.workspace,
                              self.project.attempt_dir(order.id, number), remaining, resume,
                              self.mcp_for(order))
            result = executor.run(attempt, emit)
            if (cancel_reason := self._cancel_requested(order.id, number)) is not None:
                self.ledger.append("order.cancelled", order.id,
                                   {"reason": cancel_reason, "result": result.to_dict()})
                return
            if result.status == "crashed" and quota_error(result.error):
                # Out of quota: not the order's fault. Switch executor or wait out the cooldown.
                self.mark_unavailable(order.executor, result.error or "", order.id)
                if result.session_id:
                    self.ledger.append("order.usage", order.id, {
                        "session_id": result.session_id, "usage": asdict(result.usage)})
                self.ledger.append("order.interrupted", order.id,
                                   {"reason": "executor unavailable", "error": result.error})
                if (fallback := self.fallback_for(order.executor)) is None:
                    return
                self.ledger.append("order.reassigned", order.id,
                                   {"from": order.executor, "to": fallback,
                                    "reason": "executor out of quota"})
                state = self.ledger.orders()[order.id]
                continue
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

    def _snapshot(self, order_id: str, attempt: int) -> tuple[str, dict[str, Any]]:
        """Commit the workspace so every attempt can be diffed or reverted; large files and
        rebuildable directories are skipped and reported instead."""
        with self._snapshot_lock:
            return self._snapshot_locked(order_id, attempt)

    def _snapshot_locked(self, order_id: str, attempt: int) -> tuple[str, dict[str, Any]]:
        ws = str(self.project.workspace)
        ex = snapshot_excludes(self.project.workspace)
        _write_excludes(self.project.workspace, ex)
        _record_nested(self.project.workspace, ex.nested_repos)
        if ex.large_files or ex.dirs:
            # Anything tracked before it became skipped stays on disk but leaves the snapshot.
            subprocess.run(["git", "--literal-pathspecs", "-C", ws, "rm", "-r", "-q", "--cached",
                            "--ignore-unmatch", "--", *ex.dirs, *(p for p, _ in ex.large_files)],
                           check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", ws, "add", "-A"], check=True)
        subprocess.run(["git", *_GIT_ID, "-C", ws, "commit", "-q", "--allow-empty",
                        "--no-verify", "-m", f"tar-lab: before {order_id} attempt {attempt}"],
                       check=True)
        head = subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        return head, ex.summary()
