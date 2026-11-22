"""Sealed final evaluation: fingerprint the evaluation protocol and the frozen candidate.

The final evaluation is fixed before it runs: everything under ``eval/final/`` (protocol,
script, held-out split definition) and every path listed in ``eval/final/frozen.txt`` (the
code, configs and checkpoints being evaluated) is hashed when the final-evaluation order is
dispatched, and hashed again when it ends. Any change in between is reported, not hidden.

Sealing also needs a dry run (``eval/final/dry_run.json``): the protocol run end to end on
development data, on every split or environment and through the analysis, recorded against
the exact files being sealed. Bugs in the evaluation itself (a hard-coded constant that only
fits one environment, an analysis that cannot serialise its output) then surface before the
held-out data is spent, not after.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

FINAL_DIR = "eval/final"
PROTOCOL = f"{FINAL_DIR}/protocol.md"
FROZEN = f"{FINAL_DIR}/frozen.txt"
DRY_RUN = f"{FINAL_DIR}/dry_run.json"
RESULTS_DIR = "results/final"
SKIP_DIRS = frozenset({".git", ".venv", "venv", ".cache", "__pycache__", "node_modules",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints"})


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
    return found + dry_run_problems(workspace)


def _dry_run_scope(workspace: Path) -> list[str]:
    """Files the dry run must have been made with: all of ``eval/final/`` but its record."""
    return [rel for rel in _files(workspace, FINAL_DIR) if rel != DRY_RUN]


def stamp_dry_run(workspace: Path) -> None:
    """Record the current hashes of ``eval/final/`` in the dry-run record."""
    path = workspace / DRY_RUN
    record = json.loads(path.read_text(encoding="utf-8"))
    record["files"] = fingerprint(workspace, _dry_run_scope(workspace))
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def dry_run_problems(workspace: Path) -> list[str]:
    path = workspace / DRY_RUN
    if not path.is_file():
        return [(f"`{DRY_RUN}` is missing: run the protocol end to end on development data "
                "first, on every split or environment and through the analysis (see the "
                "engineer guide)")]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"`{DRY_RUN}` is not valid JSON: {exc}"]
    if not isinstance(record, dict):
        return [f"`{DRY_RUN}` must be a JSON object"]
    found = []
    runs = record.get("runs")
    if not isinstance(runs, list) or not runs:
        found.append(f"`{DRY_RUN}` lists no `runs` (one per split or environment)")
    else:
        failed = [str(r.get("name", i)) for i, r in enumerate(runs)
                  if not isinstance(r, dict) or r.get("ok") is not True]
        if failed:
            found.append(f"dry runs did not pass: {', '.join(failed)}")
    analysis = record.get("analysis")
    if not isinstance(analysis, dict) or analysis.get("ok") is not True:
        found.append(f"`{DRY_RUN}` has no passing `analysis` run")
    files = record.get("files")
    if not isinstance(files, dict):
        found.append(f"`{DRY_RUN}` records no file hashes; run `tarlab final stamp`")
    else:
        current = fingerprint(workspace, _dry_run_scope(workspace))
        stale = sorted(rel for rel in set(current) | set(files)
                       if current.get(rel) != files.get(rel))
        if stale:
            found.append("the dry run predates changes to "
                         + ", ".join(f"`{rel}`" for rel in stale[:10])
                         + "; dry-run again and stamp it")
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


def change_kinds(before: dict[str, str | None], workspace: Path) -> dict[str, list[str]]:
    """``changes`` split into files ``added``, ``modified`` and ``removed`` since sealing."""
    kinds: dict[str, list[str]] = {"added": [], "modified": [], "removed": []}
    after = fingerprint(workspace, changes(before, workspace))
    for rel, digest in after.items():
        kind = ("added" if before.get(rel) is None else
                "removed" if digest is None else "modified")
        kinds[kind].append(rel)
    return kinds


def changes(before: dict[str, str | None], workspace: Path) -> list[str]:
    """Sealed files that changed, disappeared or appeared since ``before``."""
    after = fingerprint(workspace, sorted(set(before) | set(sealed_files(workspace))))
    return sorted(rel for rel, digest in after.items() if before.get(rel) != digest)
