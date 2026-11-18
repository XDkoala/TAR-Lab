"""Shared constants for wrapper orchestration."""

from __future__ import annotations

import re

from src.orchestrator import state as state_store

PROGRAM_DIR_RE = re.compile(r"^program_(\d+)$")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
EXPERIMENT_DIR_RE = re.compile(r"^Experiment directory:\s*(.+)$", re.MULTILINE)
IDEA_DIR_RE = re.compile(r"^Idea directory:\s*(.+)$", re.MULTILINE)

DEFAULT_RUN_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_RUN_TIMEOUT_GRACE_SECONDS = 30
DEFAULT_IDEA_TIMEOUT_SECONDS = 60 * 60
DEFAULT_MAX_IDEA_GENERATION_ATTEMPTS = 2
DEFAULT_MAX_PROGRAM_EVALS_PER_ATTEMPT = 4
DEFAULT_INTERRUPT_GRACE_SECONDS = 3

CODEX_STREAM_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_STREAM_DISCONNECT_MARKER = f"error sending request for url ({CODEX_STREAM_URL})"


# Directories that are derived build/cache artifacts rather than source content.
# Skipped when copying task trees.
TASK_COPY_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "node_modules",
        ".git",
    }
)

PARALLEL_WORKER_MEMORY_FILES: tuple[str, ...] = (
    state_store.GLOBAL_MEMORY_MD_FILE,
    state_store.GLOBAL_MEMORY_JSONL_FILE,
    state_store.LEGACY_GLOBAL_MEMORY_MD_FILE,
    *state_store.LEGACY_GLOBAL_MEMORY_JSONL_FILES,
)
PARALLEL_WORKER_STATE_FILES: tuple[str, ...] = (
    state_store.OUTER_STATE_FILE,
    state_store.IDEAS_HISTORY_FILE,
    state_store.ATTEMPTS_HISTORY_FILE,
    state_store.IDEA_GENERATION_FAILURES_FILE,
    state_store.BEST_MANIFEST_FILE,
    *PARALLEL_WORKER_MEMORY_FILES,
)
