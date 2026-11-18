"""Write engine task directories (the AI-Scientist-v3 input contract)."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class TaskSpec:
    """An engine task: ``task.md`` plus optional scaffold files.

    ``files`` maps task-relative POSIX paths (e.g. ``evaluate.py`` or
    ``program_0/src/program.py``) to file contents. A task without
    ``program_0/`` must run the engine's ``task_drafting`` phase first.
    """

    name: str
    description: str
    files: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"task name must be one safe path component, got {self.name!r}")
        if not self.description.strip():
            raise ValueError("task description (task.md) must not be empty")
        for rel in self.files:
            path = PurePosixPath(rel)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"task file path must be relative and stay inside the task: {rel!r}")
            if path == PurePosixPath("task.md"):
                raise ValueError("task.md comes from TaskSpec.description, not files")

    @property
    def has_scaffold(self) -> bool:
        return any(PurePosixPath(rel).parts[0] == "program_0" for rel in self.files)


def write_task_dir(spec: TaskSpec, parent: Path) -> Path:
    """Create ``<parent>/<spec.name>`` with the task files; refuse to overwrite."""
    task_dir = Path(parent) / spec.name
    task_dir.mkdir(parents=True, exist_ok=False)
    (task_dir / "task.md").write_text(spec.description, encoding="utf-8")
    for rel, content in spec.files.items():
        target = task_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return task_dir


def task_problems(task_dir: Path) -> list[str]:
    """What keeps ``task_dir`` from running the engine's ``investigation`` phase directly:
    ``task.md``, an ``evaluate.py`` and a ``program_0`` scaffold whose entrypoint exists."""
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        return [f"{task_dir} is not a directory"]
    problems = []
    md = task_dir / "task.md"
    if not md.is_file() or not md.read_text(encoding="utf-8", errors="replace").strip():
        problems.append("task.md is missing or empty")
    if not (task_dir / "evaluate.py").is_file():
        problems.append("evaluate.py is missing")
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
