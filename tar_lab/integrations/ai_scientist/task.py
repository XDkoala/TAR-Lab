"""Check engine task directories (the AI-Scientist-v3 input contract)."""

from __future__ import annotations

import tomllib
from pathlib import Path


def task_problems(task_dir: Path) -> list[str]:
    """What keeps ``task_dir`` from running the engine's idea search: ``task.md``,
    ``evaluate.py``, ``capture.py`` and a ``program_0`` scaffold whose entrypoint exists."""
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        return [f"{task_dir} is not a directory"]
    problems = []
    md = task_dir / "task.md"
    if not md.is_file() or not md.read_text(encoding="utf-8", errors="replace").strip():
        problems.append("task.md is missing or empty")
    for name in ("evaluate.py", "capture.py"):
        if not (task_dir / name).is_file():
            problems.append(f"{name} is missing")
    spec_path = task_dir / "program_0" / "program_spec.toml"
    try:
        spec = tomllib.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"program_0/program_spec.toml is missing or invalid ({exc})")
    else:
        entry = Path(str(spec.get("code_root", "src"))) / str(
            spec.get("entrypoint", {}).get("path", ""))
        if not (task_dir / "program_0" / entry).is_file():
            problems.append(f"the entrypoint program_0/{entry.as_posix()} does not exist")
    return problems
