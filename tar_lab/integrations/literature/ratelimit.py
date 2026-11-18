"""Machine-wide request pacing shared by every process (one literature server per work order).

Each limiter is a file under the user's state directory holding the wall-clock time of the
last request. A caller takes an exclusive ``flock`` on it, sleeps until ``min_interval`` has
passed since that time, writes the new time and releases the lock, so callers in any process
are spaced at least ``min_interval`` apart. The blocking part runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


def default_state_dir() -> Path:
    """``$TARLAB_RATE_DIR``, else ``$XDG_STATE_HOME/tar-lab/rate`` (``~/.local/state``)."""
    if override := os.environ.get("TARLAB_RATE_DIR"):
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tar-lab" / "rate"


class RateLimiter:
    """At most one request per ``min_interval`` seconds per ``name``, across processes."""

    def __init__(self, name: str, min_interval: float, state_dir: Path | None = None) -> None:
        self.name, self.min_interval = name, max(0.0, min_interval)
        self.path = (state_dir or default_state_dir()) / f"{name}.lock"
        self._broken = False

    def acquire_blocking(self) -> float:
        """Wait for this caller's slot; returns the time (``time.time()``) it was granted."""
        if self.min_interval <= 0 or self._broken or fcntl is None:
            return time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            self._broken = True
            print(f"[tar-lab] literature: rate limiter {self.name} disabled ({exc})",
                  file=sys.stderr)
            return time.time()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                last = float(os.pread(fd, 64, 0).decode().strip() or 0)
            except (ValueError, UnicodeDecodeError):
                last = 0.0
            now = time.time()
            if last > now + self.min_interval:  # clock went backwards; don't stall
                last = 0.0
            if (wait := last + self.min_interval - now) > 0:
                time.sleep(wait)
                now = time.time()
            data = f"{now:.6f}".encode()
            os.ftruncate(fd, 0)
            os.pwrite(fd, data, 0)
            return now
        finally:
            os.close(fd)  # also releases the lock

    async def wait(self) -> float:
        return await asyncio.to_thread(self.acquire_blocking)
