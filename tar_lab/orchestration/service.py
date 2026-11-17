"""Keep a project's runner alive as a systemd user service, and allow only one runner.

The unit restarts ``tarlab run`` after a crash or a kill; a project that finished, was
suspended, or already has a runner exits with a status the unit does not restart on.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

EXIT_FINISHED, EXIT_SUSPENDED, EXIT_ALREADY_RUNNING = 0, 3, 4
LOCK_NAME = "runner.lock"

Systemctl = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _systemctl(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                          check=False)


@contextmanager
def runner_lock(root: Path) -> Iterator[bool]:
    """Hold the project's runner lock for the duration; yields False if another runner has it.
    The OS drops the lock with the process, so a killed runner never leaves it stale."""
    with (root / LOCK_NAME).open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")
        f.flush()
        yield True


def unit_name(root: Path) -> str:
    root = root.resolve()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", root.name).strip("-") or "project"
    return f"tarlab-{slug}-{hashlib.sha1(str(root).encode()).hexdigest()[:8]}.service"


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def unit_text(root: Path, python: str = sys.executable) -> str:
    root = root.resolve()
    return f"""\
[Unit]
Description=TAR-Lab runner for {root.name}
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={python} -m tar_lab.cli run {root}
UnsetEnvironment=PYTHONPATH
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=30
RestartPreventExitStatus={EXIT_SUSPENDED} {EXIT_ALREADY_RUNNING}
SuccessExitStatus={EXIT_SUSPENDED} {EXIT_ALREADY_RUNNING}
StandardOutput=append:{root / "runner.log"}
StandardError=inherit

[Install]
WantedBy=default.target
"""


def installed(root: Path) -> bool:
    return (unit_dir() / unit_name(root)).exists()


def install(root: Path, systemctl: Systemctl = _systemctl) -> list[str]:
    """Write the unit, enable it at login and start it; returns notes for the user."""
    path = unit_dir() / unit_name(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(root))
    notes = [f"unit: {path}"]
    for args in (["daemon-reload"], ["enable", "--now", path.name]):
        result = systemctl(args)
        if result.returncode != 0:
            notes.append(f"systemctl --user {' '.join(args)} failed: {result.stderr.strip()}")
    return notes


def uninstall(root: Path, systemctl: Systemctl = _systemctl) -> bool:
    path = unit_dir() / unit_name(root)
    if not path.exists():
        return False
    systemctl(["disable", "--now", path.name])
    path.unlink()
    systemctl(["daemon-reload"])
    return True


def control(root: Path, action: str, systemctl: Systemctl = _systemctl
            ) -> subprocess.CompletedProcess[str]:
    return systemctl([action, unit_name(root)])


def lingering() -> bool:
    """Whether user services keep running after logout (``loginctl enable-linger``)."""
    out = subprocess.run(["loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger"],
                         capture_output=True, text=True, check=False).stdout
    return out.strip() == "Linger=yes"
