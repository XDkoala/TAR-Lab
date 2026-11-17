"""Supervised subprocess for CLI agents: own process group, hard deadline, parent-death kill."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tar_lab.core.workorder import EventSink

KILL_GRACE_SECONDS = 10.0


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int
    timed_out: bool
    wall_seconds: float


def _with_parent_death_signal(command: Sequence[str]) -> list[str]:
    # If the kernel is SIGKILLed, the agent gets SIGTERM instead of running on unsupervised;
    # its own children are cleaned up via the recorded pgid on recovery.
    setpriv = shutil.which("setpriv")
    return [setpriv, "--pdeathsig", "TERM", *command] if setpriv else list(command)


def kill_group(pgid: int, grace: float = KILL_GRACE_SECONDS) -> bool:
    """Terminate a process group; returns False if it no longer existed."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def run_supervised(
    command: Sequence[str],
    *,
    cwd: Path,
    stdin_path: Path | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    emit: EventSink,
    on_line: Callable[[str], None] = lambda _line: None,
    heartbeat_seconds: float = 60.0,
    env: dict[str, str] | None = None,
) -> ProcessOutcome:
    start = time.monotonic()
    with (open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb")) as stdin, \
            open(stdout_path, "w", encoding="utf-8") as out, \
            open(stderr_path, "wb") as err:
        proc = subprocess.Popen(
            _with_parent_death_signal(command), cwd=cwd, stdin=stdin, stdout=subprocess.PIPE, stderr=err,
            text=True, encoding="utf-8", errors="replace", env=env,
            start_new_session=True)
        emit("order.process", {"pid": proc.pid, "pgid": proc.pid})
        timed_out = threading.Event()
        done = threading.Event()

        def watchdog() -> None:
            half_sent, next_beat = False, heartbeat_seconds
            while not done.wait(min(1.0, timeout / 10)):
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    timed_out.set()
                    kill_group(proc.pid)
                    return
                if not half_sent and elapsed >= timeout / 2:
                    half_sent = True
                    emit("order.half_time", {"elapsed": round(elapsed, 1)})
                elif elapsed >= next_beat:
                    next_beat += heartbeat_seconds
                    emit("order.heartbeat", {"elapsed": round(elapsed, 1)})

        guard = threading.Thread(target=watchdog, daemon=True)
        guard.start()
        assert proc.stdout is not None
        for line in proc.stdout:
            out.write(line)
            out.flush()
            on_line(line)
        returncode = proc.wait()
        done.set()
        guard.join()
    return ProcessOutcome(returncode, timed_out.is_set(), time.monotonic() - start)


def tail(path: Path, limit: int = 2000) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()
