"""Run the AI-Scientist-v3 engine as a subprocess (``agsci run``)."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_PHASES = (
    "task_drafting",
    "investigation",
    "ablations",
    "final_evaluation",
    "posthoc",
    "paper",
    "review",
    "revision",
)
# Keys this adapter derives itself; passing them as overrides would break run_dir.
_RESERVED_KEYS = frozenset(
    {"task.dir", "run_id", "agent.name", "agent.model", "agent.pool", "paths.results_root",
     "paths.run_dir", "execution.phases"}
)
_PLAIN = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

OverrideValue = str | int | float | bool | Sequence[str]


def format_override(key: str, value: OverrideValue) -> str:
    """Render one Hydra ``key=value`` override."""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, int | float):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value if _PLAIN.match(value) else _quote(value)
    else:
        rendered = "[" + ",".join(v if _PLAIN.match(v) else _quote(v) for v in value) + "]"
    return f"{key}={rendered}"


def _quote(value: str) -> str:
    if "'" in value or "\\" in value:
        raise ValueError(f"override value cannot contain quotes or backslashes: {value!r}")
    return f"'{value}'"


@dataclass(frozen=True)
class RunRequest:
    task_dir: Path
    run_id: str
    agent: str = "claude"
    model: str | None = None
    phases: Sequence[str] = ("task_drafting", "investigation", "ablations",
                             "final_evaluation", "posthoc", "paper")
    overrides: Mapping[str, OverrideValue] = field(default_factory=dict)
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not _RUN_ID.match(self.run_id):
            raise ValueError(f"run_id must be one safe path component, got {self.run_id!r}")
        if not self.phases:
            raise ValueError("at least one phase is required")
        unknown = [p for p in self.phases if p not in PIPELINE_PHASES]
        if unknown:
            raise ValueError(f"unknown engine phases {unknown}; valid: {', '.join(PIPELINE_PHASES)}")
        reserved = sorted(set(self.overrides) & _RESERVED_KEYS)
        if reserved:
            raise ValueError(f"overrides {reserved} are set by RunRequest fields")


@dataclass(frozen=True)
class EngineRun:
    request: RunRequest
    command: tuple[str, ...]
    run_dir: Path
    return_code: int
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True)
class AIScientistEngine:
    """Handle to an AI-Scientist-v3 checkout with its own ``.venv``."""

    engine_dir: Path
    results_root: Path
    node_dir: Path | None = None

    @property
    def agsci(self) -> Path:
        return self.engine_dir / ".venv" / "bin" / "agsci"

    def run_dir(self, request: RunRequest) -> Path:
        """Mirror the engine's run envelope: ``<results_root>/<task>/<agent>/<run_id>``."""
        return self.results_root / request.task_dir.name / request.agent / request.run_id

    def build_command(self, request: RunRequest) -> list[str]:
        cmd = [str(self.agsci), "run", str(Path(request.task_dir).resolve())]
        if request.dry_run:
            cmd.append("--dry-run")
        cmd += [
            format_override("agent.name", request.agent),
            format_override("run_id", request.run_id),
            format_override("paths.results_root", str(self.results_root.resolve())),
            # Phases are validated names; the engine expects them comma-joined, unquoted.
            "execution.phases=" + ",".join(request.phases),
        ]
        if request.model:
            cmd.append(format_override("agent.model", request.model))
        cmd += [format_override(k, v) for k, v in request.overrides.items()]
        return cmd

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        # Host Python settings (e.g. ROS) must not leak into the engine interpreter.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        prefix = [str(self.engine_dir / ".venv" / "bin")]
        if self.node_dir and (self.node_dir / "bin" / "node").exists():
            prefix.insert(0, str(self.node_dir / "bin"))
        env["PATH"] = os.pathsep.join([*prefix, env.get("PATH", "")])
        return env

    def run(self, request: RunRequest, *, log_path: Path, timeout: float | None = None) -> EngineRun:
        """Run the engine to completion, streaming its console output to ``log_path``."""
        if not self.agsci.exists():
            raise FileNotFoundError(
                f"engine CLI not found at {self.agsci}; run `uv sync --frozen` in {self.engine_dir}"
            )
        command = self.build_command(request)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not request.dry_run:
            self.results_root.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                command, cwd=self.engine_dir, env=self.environment(), stdout=log,
                stderr=subprocess.STDOUT, timeout=timeout, check=False,
            )
        return EngineRun(request, tuple(command), self.run_dir(request), proc.returncode, log_path)
