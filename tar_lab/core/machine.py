"""Facts about the local machine that planning must respect."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def gpus() -> list[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"], capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def describe(root: Path) -> str:
    free_gb = shutil.disk_usage(root).free / 1e9
    gpu_lines = gpus() or ["none detected"]
    return "\n".join([
        "Compute available to this project: the local machine only (no cloud or rented GPUs).",
        f"- OS: {platform.system()} {platform.release()}; CPU threads: {os.cpu_count()}",
        *(f"- GPU: {g}" for g in gpu_lines),
        f"- Free disk at the project: {free_gb:.0f} GB",
        "- LaTeX builds use Tectonic on the host; network access is allowed.",
    ])
