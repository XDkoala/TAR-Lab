"""Investigation CLI configuration loader using the Hydra Compose API."""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

from .config import (
    AgentConfig,
    ConfigValidationError,
    ExecutionConfig,
    LoggingConfig,
    PathsConfig,
    InvestigationConfig,
    resolve_defaults,
    validate,
)

logger = logging.getLogger(__name__)

_CONFIGS_DIR = str(Path(__file__).resolve().parents[2] / "configs")


def _normalize_cli_overrides(overrides: list[str]) -> list[str]:
    """Translate convenience flags into Hydra overrides."""
    normalized: list[str] = []
    index = 0
    while index < len(overrides):
        override = overrides[index]
        if override == "--log-format":
            if index + 1 >= len(overrides):
                raise ConfigValidationError("--log-format requires human or json")
            normalized.append(f"logging.format={overrides[index + 1]}")
            index += 2
            continue
        if override.startswith("--log-format="):
            normalized.append(f"logging.format={override.split('=', 1)[1]}")
            index += 1
            continue
        if override == "--verbose":
            normalized.append("logging.verbose=true")
            index += 1
            continue
        if override.startswith("agent.models."):
            normalized.append(f"++{override}")
            index += 1
            continue
        normalized.append(override)
        index += 1
    return normalized


def load_config(overrides: list[str] | None = None) -> InvestigationConfig:
    """Load and validate the investigation configuration using the Hydra Compose API.

    Args:
        overrides: Hydra-style override strings, e.g. ``["execution.workers=4"]``.
                   When ``None``, defaults to ``sys.argv[1:]``.

    Returns:
        A fully validated and resolved ``InvestigationConfig`` instance.
    """
    if overrides is None:
        overrides = sys.argv[1:]
    overrides = _normalize_cli_overrides(list(overrides))

    # Show help if requested.
    if "-h" in overrides or "--help" in overrides:
        _print_help()
        raise SystemExit(0)

    # Hydra Compose API requires a clean global state.
    GlobalHydra.instance().clear()

    config_dir = os.environ.get("WRAPPER_CONFIGS_DIR", _CONFIGS_DIR)

    with initialize_config_dir(config_dir=config_dir, version_base="1.3"):
        raw_cfg = compose(
            config_name="config",
            overrides=overrides,
            return_hydra_config=True,
        )

    logger.info("Loaded config from %s", config_dir)

    plain = OmegaConf.to_container(
        OmegaConf.masked_copy(raw_cfg, [str(key) for key in raw_cfg.keys() if key != "hydra"]),
        resolve=True,
    )
    hydra_cfg = (
        OmegaConf.to_container(raw_cfg.hydra, resolve=False)
        if "hydra" in raw_cfg
        else {}
    )

    _check_unknown_keys(plain)  # type: ignore[arg-type]
    cfg = _dict_to_config(plain)  # type: ignore[arg-type]
    cfg.hydra_overrides = list(overrides)
    cfg.hydra_config = (
        {str(k): v for k, v in hydra_cfg.items()} if isinstance(hydra_cfg, dict) else {}
    )

    validate(cfg)
    resolve_defaults(cfg)
    cfg.hydra_runtime_config = _build_hydra_runtime_config(cfg, config_dir)

    logger.debug("Final resolved config: %s", cfg)
    return cfg


def _build_hydra_runtime_config(
    cfg: InvestigationConfig,
    config_dir: str,
) -> dict[str, Any]:
    """Build the Hydra metadata snapshot stored under results_dir/.hydra."""
    return {
        "run": {"dir": cfg.paths.results_dir},
        "runtime": {
            "cwd": str(Path.cwd()),
            "config_sources": [
                {
                    "schema": "file",
                    "provider": "main",
                    "path": config_dir,
                }
            ],
        },
        "job": {
            "name": "wrapper",
            "override_dirname": ",".join(cfg.hydra_overrides),
            "config_name": "config",
        },
        "overrides": {"task": list(cfg.hydra_overrides)},
    }


def write_hydra_snapshot(cfg: InvestigationConfig, results_dir: Path) -> None:
    """Write Hydra-style config snapshots under ``results_dir/.hydra``."""
    hydra_dir = results_dir / ".hydra"
    hydra_dir.mkdir(parents=True, exist_ok=True)
    hydra_runtime = deepcopy(cfg.hydra_config) if cfg.hydra_config else {}
    hydra_runtime.update(cfg.hydra_runtime_config)
    hydra_runtime["run"] = {"dir": str(results_dir)}
    hydra_runtime["overrides"] = {"task": list(cfg.hydra_overrides)}
    hydra_runtime.setdefault("job", {})
    if isinstance(hydra_runtime["job"], dict):
        hydra_runtime["job"].setdefault("name", "wrapper")
        hydra_runtime["job"]["config_name"] = "config"
        hydra_runtime["job"]["override_dirname"] = ",".join(cfg.hydra_overrides)
    OmegaConf.save(
        config=OmegaConf.create(_serializable_config_dict(cfg)),
        f=str(hydra_dir / "config.yaml"),
        resolve=True,
    )
    OmegaConf.save(
        config=OmegaConf.create(hydra_runtime),
        f=str(hydra_dir / "hydra.yaml"),
        resolve=False,
    )
    OmegaConf.save(
        config=OmegaConf.create(list(cfg.hydra_overrides)),
        f=str(hydra_dir / "overrides.yaml"),
        resolve=True,
    )


def _serializable_config_dict(cfg: InvestigationConfig) -> dict[str, Any]:
    """Convert InvestigationConfig to a dict without internal metadata fields."""
    serializable: dict[str, Any] = {}
    for field in dataclasses.fields(cfg):
        if field.metadata.get("internal", False):
            continue
        value = getattr(cfg, field.name)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            serializable[field.name] = dataclasses.asdict(value)
        else:
            serializable[field.name] = value
    return serializable


_KNOWN_TOP_LEVEL_KEYS = {
    "agent",
    "paths",
    "execution",
    "logging",
    "run_id",
}

_GROUP_FIELDS: dict[str, set[str]] = {
    group_name: {f.name for f in dataclasses.fields(cls)}
    for group_name, cls in [
        ("agent", AgentConfig),
        ("paths", PathsConfig),
        ("execution", ExecutionConfig),
        ("logging", LoggingConfig),
    ]
}


def _check_unknown_keys(d: dict) -> None:
    """Raise ``ConfigValidationError`` if *d* contains keys not recognised by InvestigationConfig.

    This catches typos such as ``execution.max_idea_cyles`` that would
    otherwise be silently ignored during the dict-to-dataclass conversion.
    """
    unknown_top = set(d.keys()) - _KNOWN_TOP_LEVEL_KEYS
    if unknown_top:
        raise ConfigValidationError(
            f"Unknown top-level config keys: {sorted(unknown_top)}. "
            "Check for typos or see configs/config.yaml for valid keys."
        )

    for group_name, valid_keys in _GROUP_FIELDS.items():
        group_d = d.get(group_name)
        if not isinstance(group_d, dict):
            continue
        unknown = set(group_d.keys()) - valid_keys
        if unknown:
            raise ConfigValidationError(
                f"Unknown keys in '{group_name}': {sorted(unknown)}. "
                "Check for typos or see configs/config.yaml for valid keys."
            )


def _optional_str(value: object | None) -> str | None:
    """Handle optional string normalization."""
    if value is None:
        return None
    return str(value)


def _string_list(value: object | None) -> list[str]:
    """Normalize a config value into a list of strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _string_dict(value: object | None) -> dict[str, object]:
    """Normalize a config value into a dict with string keys."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _dict_to_config(d: dict) -> InvestigationConfig:
    """Convert a plain dict (from OmegaConf) to a InvestigationConfig dataclass."""
    agent_d = d.get("agent", {})
    paths_d = d.get("paths", {})
    execution_d = d.get("execution", {})
    logging_d = d.get("logging", {})

    return InvestigationConfig(
        agent=AgentConfig(
            name=str(agent_d.get("name", "claude")),
            model=agent_d.get("model"),
            profile=agent_d.get("profile"),
            reasoning_effort=agent_d.get("reasoning_effort"),
            pool=_string_list(agent_d.get("pool")),
            models=_string_dict(agent_d.get("models")),
        ),
        paths=PathsConfig(
            task_dir=str(paths_d.get("task_dir", "examples/circpack")),
            results_dir=paths_d.get("results_dir"),
            global_memory_dir=paths_d.get("global_memory_dir"),
            instruct=paths_d.get("instruct"),
            task_python=_optional_str(paths_d.get("task_python")),
        ),
        execution=ExecutionConfig(
            max_idea_cycles=int(execution_d.get("max_idea_cycles", 20)),
            resume=bool(execution_d.get("resume", False)),
            max_program_evals_per_attempt=int(
                execution_d.get("max_program_evals_per_attempt", 4)
            ),
            run_timeout_seconds=int(execution_d.get("run_timeout_seconds", 21600)),
            idea_timeout_seconds=int(execution_d.get("idea_timeout_seconds", 3600)),
            max_idea_generation_attempts=int(
                execution_d.get("max_idea_generation_attempts", 2)
            ),
            run_timeout_grace_seconds=int(execution_d.get("run_timeout_grace_seconds", 30)),
        ),
        logging=LoggingConfig(
            format=str(logging_d.get("format", "human")),
            verbose=bool(logging_d.get("verbose", False)),
            heartbeat_seconds=int(logging_d.get("heartbeat_seconds", 60)),
            show_run_header=bool(logging_d.get("show_run_header", True)),
        ),
        run_id=str(d.get("run_id", "")) if d.get("run_id") else InvestigationConfig().run_id,
    )


_CONFIG_GROUPS: list[tuple[str, type]] = [
    ("agent", AgentConfig),
    ("paths", PathsConfig),
    ("execution", ExecutionConfig),
    ("logging", LoggingConfig),
]


def _print_help() -> None:
    """Print help text for the investigation CLI, auto-generated from field metadata."""
    lines = [
        "usage: python -m src.investigation.cli [HYDRA_OVERRIDES...]",
        "",
        "Rubric-driven investigation loop: idea generation and attempts.",
        "",
        "Configuration is loaded from configs/config.yaml and composed",
        "with CLI overrides using Hydra syntax.",
        "",
        "Examples:",
        "  python -m src.investigation.cli                          # defaults",
        "  python -m src.investigation.cli execution.max_idea_cycles=5",
        "  python -m src.investigation.cli agent.name=codex paths.task_dir=examples/circpack",
        "",
        "Config keys:",
    ]

    for group_name, cls in _CONFIG_GROUPS:
        for f in dataclasses.fields(cls):
            help_text = f.metadata.get("help", "")
            key = f"{group_name}.{f.name}"
            lines.append(f"  {key:<40} {help_text}")

    lines.append("")
    lines.append("Convenience flags:")
    lines.append("  --log-format human|json            Select terminal log renderer")
    lines.append("  --verbose                          Show detailed human logs")
    lines.append("")
    lines.append("See configs/config.yaml for all available keys and defaults.")
    print("\n".join(lines))
