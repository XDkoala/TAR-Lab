"""Investigation CLI contracts: image pinning, paper step, live task/paper events, receipts, scratch cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


from tests.program_testutils import make_program_dir

IMAGE_ID = "sha256:" + "ab" * 32
_DOCKER_PATCHES = (
    ("src.investigation.cli.shutil.which", {"return_value": "/usr/bin/docker"}),
    ("src.investigation.cli._probe_docker_available", {"return_value": (True, "")}),
)


def _task_dir(root: Path) -> Path:
    task_dir = root / "task"
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 1\n")
    (task_dir / "capture.py").write_text("", encoding="utf-8")
    (task_dir / "evaluate.py").write_text("", encoding="utf-8")
    (task_dir / "task.md").write_text(
        "# Goal\n- optimize objective\n\n# Rubrics\n- score threshold\n", encoding="utf-8"
    )
    (task_dir / "goal.md").write_text("legacy", encoding="utf-8")
    return task_dir






# --------------------------------------------------------------------------- image pinning


@dataclass
class _FakeLease:
    image_id: str
    tag: str
    released: bool = False
    verified: int = field(default=0)

    def verify(self) -> None:
        self.verified += 1

    def release(self) -> None:
        self.released = True




# --------------------------------------------------------------------------- paper step














# --------------------------------------------------------------------------- live paper / task events












# --------------------------------------------------------------------------- task drafting at main()






# --------------------------------------------------------------------------- container scratch cleanup






