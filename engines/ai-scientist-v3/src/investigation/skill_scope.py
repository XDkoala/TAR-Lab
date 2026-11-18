"""Step-scoped skill selection for agent execution scripts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Sequence

SKILLS_BY_STEP: dict[str, tuple[str, ...]] = {
    "idea": (
        "create-idea",
        "memory-log",
    ),
    "run": (
        "edit-program",
        "evaluate-program",
        "verify-rubrics",
        "memory-log",
    ),
}


def skills_for_step(step: str) -> tuple[str, ...]:
    """Return configured skill names for a workflow step."""
    selected = SKILLS_BY_STEP.get(step)
    if selected is None:
        available = ", ".join(sorted(SKILLS_BY_STEP))
        raise ValueError(f"Unknown step '{step}'. Available steps: {available}")
    return selected


def validate_skill_set(skills_src_dir: Path, selected: Sequence[str]) -> None:
    """Validate selected skills exist on disk."""
    if not selected:
        raise ValueError("Selected skill set must not be empty")

    missing = [skill for skill in selected if not (skills_src_dir / skill).is_dir()]
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise FileNotFoundError(
            f"Missing configured skills for selection in {skills_src_dir}: {missing_text}"
        )


_STAGING_IGNORED_DIRS = frozenset({".git"})


def _ignore_staging_dirs(directory: str, entries: list[str]) -> set[str]:
    return {name for name in entries if name in _STAGING_IGNORED_DIRS}


def _copy_replacing(src: str, dst: str, *, follow_symlinks: bool = True) -> str:
    """Copy ``src`` over ``dst`` even when a previous staging left ``dst`` read-only."""
    try:
        os.unlink(dst)
    except FileNotFoundError:
        pass
    return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def stage_skill_tree(src_dir: Path, dst_dir: Path) -> None:
    """Copy a skill into an agent workspace, tolerating an earlier staging of the same skill.

    Nested ``.git`` directories (for example a vendored tool checkout inside a skill)
    are skipped: agents never need them, and git stores pack
    files read-only, so re-staging over a previous copy would fail with EACCES. Stale
    ``.git`` copies left by earlier stagings are removed from the destination.
    """
    shutil.copytree(
        src_dir,
        dst_dir,
        dirs_exist_ok=True,
        ignore=_ignore_staging_dirs,
        copy_function=_copy_replacing,
    )
    for root, dirs, _files in os.walk(dst_dir):
        stale = [name for name in dirs if name in _STAGING_IGNORED_DIRS]
        for name in stale:
            shutil.rmtree(Path(root) / name)
            dirs.remove(name)
