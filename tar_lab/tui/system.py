"""Host facts the interface shows: GPU use and whether a project runner is alive."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GpuInfo:
    util_percent: int
    used_mib: int
    total_mib: int
    # VRAM by process group, so it can be attributed to a work order's agent.
    by_pgid: dict[int, int] = field(default_factory=dict)


def _smi(*query: str) -> list[list[str]]:
    out = subprocess.run(["nvidia-smi", *query, "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=5, check=True).stdout
    return [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]


def gpu_info() -> GpuInfo | None:
    try:
        util, used, total = _smi("--query-gpu=utilization.gpu,memory.used,memory.total")[0]
        apps = _smi("--query-compute-apps=pid,used_memory")
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    by_pgid: dict[int, int] = {}
    for row in apps:
        try:
            pgid = os.getpgid(int(row[0]))
            by_pgid[pgid] = by_pgid.get(pgid, 0) + int(row[1])
        except (OSError, ValueError, IndexError):
            continue
    return GpuInfo(int(util), int(used), int(total), by_pgid)


def runner_pids(root: Path) -> list[int]:
    """PIDs of ``tarlab run <root>`` processes, whatever directory they were started from."""
    root, pids = root.resolve(), []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")
            cwd = Path(os.readlink(proc / "cwd"))
        except OSError:
            continue
        if not any(a.endswith("tarlab") or a == "tar_lab.cli" for a in args) or "run" not in args:
            continue
        target = args[args.index("run") + 1] if args.index("run") + 1 < len(args) else ""
        if target and (cwd / target).resolve() == root:
            pids.append(int(proc.name))
    return pids


def start_runner(root: Path, poll_seconds: float = 30.0) -> tuple[int, Path]:
    """Start ``tarlab run`` detached from this terminal; returns its PID and log file."""
    n = 1
    while (root / f"run-{n}.log").exists():
        n += 1
    log = root / f"run-{n}.log"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with log.open("ab") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tar_lab.cli", "run", str(root.resolve()),
             "--poll", f"{poll_seconds:g}"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
            start_new_session=True)
    return proc.pid, log
