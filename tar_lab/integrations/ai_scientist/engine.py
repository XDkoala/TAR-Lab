"""Run the AI-Scientist-v3 idea search natively on the host.

``engines/ai-scientist-v3`` is trimmed to the investigation loop and is launched as
``python -m src.investigation.cli``. ``engines/ai-scientist-v3-original`` is the untouched
upstream engine, kept for baseline comparisons; it runs through its own pipeline CLI
(``agsci run``) restricted to the investigation phase, with containers and sidecar
versioning switched off.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Keys this adapter derives itself; passing them as overrides would break the run layout.
_RESERVED_KEYS = frozenset(
    {"paths.task_dir", "paths.results_dir", "paths.results_root", "paths.run_dir", "run_id",
     "agent.name", "agent.model", "agent.reasoning_effort", "execution.max_idea_cycles",
     "investigation.execution.max_idea_cycles", "execution.phases"}
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
    agent: str = "codex"
    model: str | None = None
    reasoning_effort: str | None = None
    max_idea_cycles: int = 3
    overrides: Mapping[str, OverrideValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _RUN_ID.match(self.run_id):
            raise ValueError(f"run_id must be one safe path component, got {self.run_id!r}")
        if self.max_idea_cycles < 1:
            raise ValueError("max_idea_cycles must be at least 1")
        reserved = sorted(set(self.overrides) & _RESERVED_KEYS)
        if reserved:
            raise ValueError(f"overrides {reserved} are set by RunRequest fields")


@dataclass(frozen=True)
class AIScientistEngine:
    """Handle to an engine checkout with its own ``.venv``.

    ``tools`` maps command names to executables the agents must find on ``PATH`` (e.g.
    ``codex``); they are linked into ``<results_root>/.bin``. ``headless_bin`` is the
    locally built headless CLI that launches the coding agents.
    """

    engine_dir: Path
    results_root: Path
    node_dir: Path | None = None
    headless_bin: Path | None = None
    tools: Mapping[str, Path] = field(default_factory=dict)
    pipeline: bool = False  # the untouched upstream engine (``agsci run``)

    @property
    def python(self) -> Path:
        return self.engine_dir / ".venv" / "bin" / "python"

    def run_dir(self, request: RunRequest) -> Path:
        if self.pipeline:  # the upstream envelope: <results_root>/<task>/<agent>/<run_id>
            return self.results_root / request.task_dir.name / request.agent / request.run_id
        return self.results_root / request.task_dir.name / request.run_id

    def build_command(self, request: RunRequest) -> list[str]:
        task = str(Path(request.task_dir).resolve())
        if self.pipeline:
            cmd = [str(self.engine_dir / ".venv" / "bin" / "agsci"), "run", task,
                   format_override("agent.name", request.agent),
                   format_override("run_id", request.run_id),
                   format_override("paths.results_root", str(self.results_root.resolve())),
                   "execution.phases=investigation", "container.enabled=false",
                   "versioning.enabled=false",
                   format_override("investigation.execution.max_idea_cycles",
                                   request.max_idea_cycles)]
        else:
            cmd = [str(self.python), "-m", "src.investigation.cli",
                   format_override("paths.task_dir", task),
                   format_override("paths.results_dir", str(self.run_dir(request).resolve())),
                   format_override("run_id", request.run_id),
                   format_override("agent.name", request.agent),
                   format_override("execution.max_idea_cycles", request.max_idea_cycles)]
        if request.model:
            cmd.append(format_override("agent.model", request.model))
        if request.reasoning_effort:
            cmd.append(format_override("agent.reasoning_effort", request.reasoning_effort))
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
        if self.tools:
            bin_dir = self.results_root / ".bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            for name, target in self.tools.items():
                link = bin_dir / name
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(target)
            prefix.insert(0, str(bin_dir))
        env["PATH"] = os.pathsep.join([*prefix, env.get("PATH", "")])
        if self.headless_bin:
            env["HEADLESS_BIN"] = str(self.headless_bin)
        # Agents run on the user's subscription login, never on a pay-per-token API key.
        env.setdefault("HEADLESS_BILLING", "subscription")
        return env
