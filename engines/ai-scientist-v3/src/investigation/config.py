"""Structured configuration dataclasses for wrapper orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .constants import (
    DEFAULT_IDEA_TIMEOUT_SECONDS,
    DEFAULT_MAX_IDEA_GENERATION_ATTEMPTS,
    DEFAULT_MAX_PROGRAM_EVALS_PER_ATTEMPT,
    DEFAULT_RUN_TIMEOUT_GRACE_SECONDS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

VALID_AGENTS = ("claude", "codex", "cursor", "opencode", "gemini", "pi")
VALID_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
VALID_LOG_FORMATS = ("human", "json")
PROFILE_MODEL_ERROR = "agent.model is required when agent.profile is provided"
GEMINI_REASONING_EFFORT_ERROR = (
    "agent.reasoning_effort is not supported for gemini, including agent.pool entries"
)


@dataclass
class AgentConfig:
    name: str = field(
        default="claude",
        metadata={"help": "Agent backend (claude|codex|cursor|opencode|gemini|pi)"},
    )
    model: Optional[str] = field(
        default=None,
        metadata={"help": "Model override for the agent CLI"},
    )
    profile: Optional[str] = field(
        default=None,
        metadata={"help": "Codex CLI profile name"},
    )
    reasoning_effort: Optional[str] = field(
        default=None,
        metadata={"help": "Agent reasoning effort (unsupported by Gemini)"},
    )
    pool: list[str] = field(
        default_factory=list,
        metadata={"help": "Optional agent harness pool sampled uniformly per cycle"},
    )
    models: dict[str, Any] = field(
        default_factory=dict,
        metadata={"help": "Per-agent model overrides used before agent.model"},
    )


@dataclass
class PathsConfig:
    task_dir: str = field(
        default="examples/circpack",
        metadata={"help": "Task directory"},
    )
    results_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Results directory (auto-computed if omitted)"},
    )
    global_memory_dir: Optional[str] = field(
        default=None,
        metadata={"help": "Shared memory directory across runs"},
    )
    instruct: Optional[str] = field(
        default=None,
        metadata={"help": "Prompt template path"},
    )
    task_python: Optional[str] = field(
        default=None,
        metadata={
            "help": (
                "Python interpreter of the task's own environment, used for every official "
                "evaluation (default: <task_dir>/.venv/bin/python if present, else the "
                "engine's interpreter)"
            )
        },
    )


@dataclass
class ExecutionConfig:
    max_idea_cycles: int = field(
        default=20,
        metadata={"help": "Max outer idea-generation cycles"},
    )
    resume: bool = field(
        default=False,
        metadata={"help": "Resume from existing state"},
    )
    max_program_evals_per_attempt: int = field(
        default=DEFAULT_MAX_PROGRAM_EVALS_PER_ATTEMPT,
        metadata={"help": "Max evaluations per attempt"},
    )
    run_timeout_seconds: int = field(
        default=DEFAULT_RUN_TIMEOUT_SECONDS,
        metadata={"help": "Per-run timeout in seconds"},
    )
    idea_timeout_seconds: int = field(
        default=DEFAULT_IDEA_TIMEOUT_SECONDS,
        metadata={"help": "Per-idea-generation timeout in seconds"},
    )
    max_idea_generation_attempts: int = field(
        default=DEFAULT_MAX_IDEA_GENERATION_ATTEMPTS,
        metadata={"help": "Maximum fresh idea-generation attempts per cycle"},
    )
    run_timeout_grace_seconds: int = field(
        default=DEFAULT_RUN_TIMEOUT_GRACE_SECONDS,
        metadata={"help": "Grace period after timeout in seconds"},
    )


@dataclass
class LoggingConfig:
    format: str = field(
        default="human",
        metadata={"help": "Terminal log format (human|json)"},
    )
    verbose: bool = field(
        default=False,
        metadata={"help": "Show full paths, configuration, and rubric evidence"},
    )
    heartbeat_seconds: int = field(
        default=60,
        metadata={"help": "Progress heartbeat interval in seconds (0 = disabled)"},
    )
    show_run_header: bool = field(
        default=True,
        metadata={"help": "Render the user-facing run banner and configuration header"},
    )


def _generate_run_id() -> str:
    """Generate a timestamp-based run identifier (e.g. 20260224_153042)."""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass
class InvestigationConfig:
    agent: AgentConfig = field(default_factory=AgentConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    run_id: str = field(default_factory=_generate_run_id)
    hydra_overrides: list[str] = field(
        default_factory=list,
        repr=False,
        metadata={"internal": True},
    )
    hydra_config: dict[str, object] = field(
        default_factory=dict,
        repr=False,
        metadata={"internal": True},
    )
    hydra_runtime_config: dict[str, object] = field(
        default_factory=dict,
        repr=False,
        metadata={"internal": True},
    )


class ConfigValidationError(SystemExit):
    """Raised when configuration validation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(2)


def validate(cfg: InvestigationConfig) -> None:
    """Validate configuration values, raising ConfigValidationError on problems."""
    if cfg.agent.name not in VALID_AGENTS:
        raise ConfigValidationError(
            f"agent.name must be one of {VALID_AGENTS}, got {cfg.agent.name!r}"
        )
    if cfg.agent.model is not None and not str(cfg.agent.model).strip():
        raise ConfigValidationError("agent.model must be non-empty when provided")
    if cfg.agent.profile is not None and not str(cfg.agent.profile).strip():
        raise ConfigValidationError("agent.profile must be non-empty when provided")
    if cfg.agent.profile is not None and cfg.agent.name != "codex":
        raise ConfigValidationError("agent.profile is only supported when agent.name='codex'")
    if cfg.agent.profile is not None and cfg.agent.model is None:
        raise ConfigValidationError(PROFILE_MODEL_ERROR)
    if (
        cfg.agent.reasoning_effort is not None
        and cfg.agent.reasoning_effort not in VALID_REASONING_EFFORTS
    ):
        raise ConfigValidationError(
            "agent.reasoning_effort must be one of "
            f"{VALID_REASONING_EFFORTS}, got {cfg.agent.reasoning_effort!r}"
        )
    if cfg.agent.reasoning_effort is not None and (
        cfg.agent.name == "gemini" or "gemini" in cfg.agent.pool
    ):
        raise ConfigValidationError(GEMINI_REASONING_EFFORT_ERROR)
    seen_pool: set[str] = set()
    for agent in cfg.agent.pool:
        if agent not in VALID_AGENTS:
            raise ConfigValidationError(
                f"agent.pool entries must be one of {VALID_AGENTS}, got {agent!r}"
            )
        if agent in seen_pool:
            raise ConfigValidationError(f"agent.pool entries must be unique, got duplicate {agent!r}")
        seen_pool.add(agent)
    for agent, model in cfg.agent.models.items():
        if agent not in VALID_AGENTS:
            raise ConfigValidationError(
                f"agent.models keys must be one of {VALID_AGENTS}, got {agent!r}"
            )
        if not isinstance(model, str) or not model.strip():
            raise ConfigValidationError(
                f"agent.models[{agent!r}] must be a non-empty string"
            )
    if cfg.execution.max_idea_cycles <= 0:
        raise ConfigValidationError("execution.max_idea_cycles must be > 0")
    if cfg.execution.run_timeout_seconds <= 0:
        raise ConfigValidationError("execution.run_timeout_seconds must be > 0")
    if cfg.execution.idea_timeout_seconds <= 0:
        raise ConfigValidationError("execution.idea_timeout_seconds must be > 0")
    if cfg.execution.max_idea_generation_attempts <= 0:
        raise ConfigValidationError("execution.max_idea_generation_attempts must be > 0")
    if cfg.execution.run_timeout_grace_seconds <= 0:
        raise ConfigValidationError("execution.run_timeout_grace_seconds must be > 0")
    if cfg.execution.max_program_evals_per_attempt <= 0:
        raise ConfigValidationError("execution.max_program_evals_per_attempt must be > 0")
    if cfg.logging.format not in VALID_LOG_FORMATS:
        raise ConfigValidationError(
            f"logging.format must be one of {VALID_LOG_FORMATS}, got {cfg.logging.format!r}"
        )
    if cfg.logging.heartbeat_seconds < 0:
        raise ConfigValidationError("logging.heartbeat_seconds must be >= 0")
    if cfg.paths.task_python is not None and not str(cfg.paths.task_python).strip():
        raise ConfigValidationError("paths.task_python must be non-empty when provided")


def config_to_flat_dict(cfg: InvestigationConfig) -> dict[str, str]:
    """Flatten a InvestigationConfig into a dict with dotted keys for display.

    Example: {"agent.name": "claude", "execution.workers": "1", ...}
    """
    import dataclasses

    flat: dict[str, str] = {}
    for section_field in dataclasses.fields(cfg):
        if section_field.metadata.get("internal", False):
            continue
        section = getattr(cfg, section_field.name)
        if dataclasses.is_dataclass(section):
            for param_field in dataclasses.fields(section):
                key = f"{section_field.name}.{param_field.name}"
                value = getattr(section, param_field.name)
                flat[key] = str(value) if value is not None else "null"
        else:
            flat[section_field.name] = str(section) if section is not None else "null"
    return flat


def has_hydra_override(cfg: InvestigationConfig, dotted_key: str) -> bool:
    """Return true when the user explicitly supplied a Hydra override for a key."""
    override_prefixes = (
        f"{dotted_key}=",
        f"+{dotted_key}=",
        f"++{dotted_key}=",
    )
    deletion_forms = (f"~{dotted_key}", f"~{dotted_key}=")
    for raw_override in cfg.hydra_overrides:
        override = raw_override.strip()
        if override.startswith(override_prefixes):
            return True
        if override == deletion_forms[0] or override.startswith(deletion_forms[1]):
            return True
    return False


def resolve_defaults(cfg: InvestigationConfig) -> InvestigationConfig:
    """Compute derived defaults (results_dir from task_dir + agent.name when null).

    Returns the same config object, mutated in place for convenience.
    """

    if cfg.paths.results_dir is None:
        agent_dir = "ensemble" if cfg.agent.pool else cfg.agent.name
        task_name = Path(cfg.paths.task_dir).name.strip() or "task"
        cfg.paths.results_dir = str(
            Path("results") / task_name / agent_dir / cfg.run_id
        )
        logger.debug(
            "Resolved results_dir to %s (from task_dir=%s, agent=%s, run_id=%s)",
            cfg.paths.results_dir,
            cfg.paths.task_dir,
            agent_dir,
            cfg.run_id,
        )
    return cfg
