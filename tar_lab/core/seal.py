"""Sealed final evaluation: fingerprint the evaluation protocol and the frozen candidate.

The final evaluation is fixed before it runs: everything under ``eval/final/`` (protocol,
script, held-out split definition) and every path listed in ``eval/final/frozen.txt`` (the
code, configs and checkpoints being evaluated) is hashed when the final-evaluation order is
dispatched, and hashed again when it ends. Any change in between is reported, not hidden.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

FINAL_DIR = "eval/final"
PROTOCOL = f"{FINAL_DIR}/protocol.md"
FROZEN = f"{FINAL_DIR}/frozen.txt"
RESULTS_DIR = "results/final"
SKIP_DIRS = frozenset({".git", ".venv", "venv", ".cache", "__pycache__", "node_modules",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints",
                       ".agent_home"})


def frozen_entries(workspace: Path) -> list[str]:
    path = workspace / FROZEN
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [line for line in lines if line]


def problems(workspace: Path) -> list[str]:
    """Why the final evaluation cannot be sealed yet (empty when it can)."""
    found = []
    protocol = workspace / PROTOCOL
    if not protocol.is_file() or not protocol.read_text(encoding="utf-8").strip():
        found.append(f"`{PROTOCOL}` is missing or empty")
    entries = frozen_entries(workspace)
    if not entries:
        found.append(f"`{FROZEN}` is missing or lists nothing to freeze")
    root = workspace.resolve()
    for entry in entries:
        target = (workspace / entry).resolve()
        if not target.is_relative_to(root):
            found.append(f"`{entry}` in `{FROZEN}` is outside the workspace")
        elif not target.exists():
            found.append(f"`{entry}` in `{FROZEN}` does not exist")
    return found


def _files(workspace: Path, rel: str) -> list[str]:
    path = workspace / rel
    if path.is_file():
        return [Path(rel).as_posix()]
    out = []
    for top, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        out += [(Path(top) / f).relative_to(workspace).as_posix() for f in files]
    return out


def sealed_files(workspace: Path) -> list[str]:
    files = set(_files(workspace, FINAL_DIR))
    for entry in frozen_entries(workspace):
        if (workspace / entry).resolve().is_relative_to(workspace.resolve()):
            files.update(_files(workspace, entry))
    return sorted(files)


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def fingerprint(workspace: Path, files: list[str] | None = None) -> dict[str, str | None]:
    """SHA-256 per file (``None`` for a file that is gone)."""
    return {rel: _sha256(workspace / rel)
            for rel in (sealed_files(workspace) if files is None else files)}


def changes(before: dict[str, str | None], workspace: Path) -> list[str]:
    """Sealed files that changed, disappeared or appeared since ``before``."""
    after = fingerprint(workspace, sorted(set(before) | set(sealed_files(workspace))))
    return sorted(rel for rel, digest in after.items() if before.get(rel) != digest)
