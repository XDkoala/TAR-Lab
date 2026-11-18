#!/usr/bin/env python3
"""Investigation CLI: config loading, helpers, parallel workers, run_cli and main."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from tests.program_testutils import make_program_dir

from src.investigation import cli as investigation_cli
from src.investigation import runtime as runtime_module
from src.investigation import script_runners
from src.investigation.files import load_instructions
from src.investigation.config import AgentConfig, ExecutionConfig, InvestigationConfig, PathsConfig


def _main_setup(
    root: Path, *, scaffold: bool = True, max_idea_cycles: int = 1, instruct_name: str = "instruct.md"
) -> tuple[Path, Path, InvestigationConfig]:
    task_dir = _make_task_dir(root, scaffold=scaffold)
    results_dir = root / "results"
    instruct = root / instruct_name
    instruct.write_text("template", encoding="utf-8")
    cfg = InvestigationConfig(
        agent=AgentConfig(name="codex"),
        paths=PathsConfig(task_dir=str(task_dir), results_dir=str(results_dir), instruct=str(instruct)),
        execution=ExecutionConfig(
            max_idea_cycles=max_idea_cycles,
            run_timeout_seconds=5,
            run_timeout_grace_seconds=1,
            max_program_evals_per_attempt=4,
            resume=False,
        ),
    )
    return task_dir, results_dir, cfg


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
CLI = "src.investigation.cli"
HEADLESS_PREFIX = [
    "env",
    "HEADLESS_MODELS_DEV_CACHE=",
    "npx",
    "-y",
    "git+https://github.com/RobertTLange/headless-cli.git#93cd9b06b85f848af1308c41e018991b33907c5e",
]
WORKER_MODULE = [sys.executable, "-m", "src.investigation.cli"]
IDEA_FAILED = investigation_cli.IdeaGenerationResult(output="idea failed", exit_code=1, idea_dir=None)
METRIC_RUBRIC = {"id": "score_threshold", "description": "score > 2.6", "type": "metric"}


class FakeStreamingProcess:
    def __init__(self, output: str, *, returncode: int = 0) -> None:
        self.stdout = io.StringIO(output)
        self.stderr = io.StringIO()
        self.returncode = returncode
        self.pid = 1234

    def wait(self) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode


def _attr(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _fake_proc(output: str, returncode: int = 0) -> Mock:
    proc = Mock()
    proc.communicate.return_value = (output, "")
    proc.returncode = returncode
    return proc






def _campaign_setup(root: Path, **execution: Any) -> tuple[Path, Path, InvestigationConfig]:
    task_dir = root / "task"
    task_dir.mkdir(parents=True)
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 0\n")
    cfg = InvestigationConfig(execution=ExecutionConfig(resume=False, **execution))
    return task_dir, root / "results", cfg




def _attempt_kwargs(root: Path, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        agent="codex",
        task_dir=root / "task",
        results_dir=root / "results",
        seed_program_dir=root / "task/program_0",
        idea_file=root / "idea.json",
        rubrics_file=root / "rubrics.json",
        attempt_id="idea_0001_attempt_01",
        idea_dir_name="01_timeout-smoke",
        attempt_dir_name="01_attempt",
        instruct_path=None,
        timeout_seconds=30,
        timeout_grace_seconds=7,
        max_program_evals_per_attempt=1,
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Helpers and config loading
# ---------------------------------------------------------------------------




LOAD_CONFIG_OVERRIDE_CASES: list[tuple[str, list[str], dict[str, Any]]] = [
    (
        "later_cli_overrides_win",
        ["agent.name=codex", "agent.name=gemini", "execution.max_idea_cycles=7"],
        {"agent.name": "gemini", "execution.max_idea_cycles": 7},
    ),
    ("agent_model", ["agent.model=gpt-5.2"], {"agent.model": "gpt-5.2"}),
    ("reasoning_effort", ["agent.reasoning_effort=xhigh"], {"agent.reasoning_effort": "xhigh"}),
    ("task_python", ["paths.task_python=/opt/task/bin/python"], {"paths.task_python": "/opt/task/bin/python"}),
    (
        "codex_profile",
        ["agent.name=codex", "agent.profile=custom", "agent.model=custom-model"],
        {"agent.profile": "custom"},
    ),
    (
        "pool_and_model_map",
        ["agent.pool=[codex,claude]", "agent.models.codex=gpt-5.2", "agent.models.claude=claude-opus-4-6"],
        {"agent.pool": ["codex", "claude"], "agent.models": {"codex": "gpt-5.2", "claude": "claude-opus-4-6"}},
    ),
    (
        "ensemble_results_dir",
        ["agent.pool=[codex,claude]", "run_id=test_run"],
        {"paths.results_dir": "results/circpack/ensemble/test_run"},
    ),
    (
        "idea_generation_recovery",
        ["execution.idea_timeout_seconds=42", "execution.max_idea_generation_attempts=3"],
        {"execution.idea_timeout_seconds": 42, "execution.max_idea_generation_attempts": 3},
    ),
]


def test_load_config_accepts_overrides() -> None:
    for name, overrides, expected in LOAD_CONFIG_OVERRIDE_CASES:
        cfg = investigation_cli.load_config(overrides)
        assert {key: _attr(cfg, key) for key in expected} == expected, name


LOAD_CONFIG_REJECTED_CASES: list[tuple[str, list[str]]] = [
    ("invalid_reasoning_effort", ["agent.reasoning_effort=max"]),
    ("profile_without_model", ["agent.name=codex", "agent.profile=custom"]),
    ("empty_profile", ["agent.name=codex", "agent.profile=''"]),
    ("profile_for_non_codex", ["agent.name=claude", "agent.profile=custom"]),
    ("invalid_pool_entry", ["agent.pool=[codex,bogus]"]),
    ("duplicate_pool_entries", ["agent.pool=[codex,codex]"]),
    ("invalid_model_map_key", ["agent.models.bogus=gpt-test"]),
    ("null_model_map_value", ["agent.models.codex=null"]),
    ("non_string_model_map_value", ["agent.models.codex=123"]),
    ("non_positive_timeout_grace", ["execution.run_timeout_grace_seconds=0"]),
    ("non_positive_idea_timeout", ["execution.idea_timeout_seconds=0"]),
    ("non_positive_idea_attempts", ["execution.max_idea_generation_attempts=0"]),
    ("removed_container_group", ["+container.enabled=true"]),
    ("unknown_top_level_key", ["+bogus_key=1"]),
    ("typo_in_execution_group", ["+execution.max_idea_cyles=5"]),
    ("typo_in_agent_group", ["+agent.nme=claude"]),
]
GEMINI_REASONING_EFFORT_CASES: list[tuple[str, list[str]]] = [
    ("gemini_agent", ["agent.name=gemini", "agent.reasoning_effort=high"]),
    ("gemini_pool_entry", ["agent.pool=[codex,gemini]", "agent.reasoning_effort=high"]),
]


def test_load_config_rejects_invalid_overrides_with_exit_2_and_gemini_reasoning_effort() -> None:
    # invalid overrides exit with code 2
    for name, overrides in LOAD_CONFIG_REJECTED_CASES:
        with pytest.raises(SystemExit) as ctx:
            investigation_cli.load_config(overrides)
        assert ctx.value.code == 2, name

    # reasoning effort is rejected for gemini (as agent or pool entry)
    for name, overrides in GEMINI_REASONING_EFFORT_CASES:
        with pytest.raises(SystemExit) as ctx:
            investigation_cli.load_config(overrides)
        assert "not supported for gemini" in str(getattr(ctx.value, "message", ctx.value)), name


NORMALIZE_RESULTS_DIR_CASES: list[tuple[str, str, str]] = [
    ("agent_is_leaf", "results/circpack/claude", "results/circpack/claude"),
    ("agent_is_intermediate_segment", "results/circpack/claude/20260224_153042", "results/circpack/claude/20260224_153042"),
    ("agent_absent", "results/circpack/20260224_153042", "results/circpack/20260224_153042/claude"),
    ("user_path_containing_agent", "/custom/claude/output", "/custom/claude/output"),
    ("user_path_without_agent", "/custom/output", "/custom/output/claude"),
]




def test_ensure_task_copy_in_results_and_directory_alias(tmp_path: Path) -> None:
    # ensure_task_copy_in_results copies once
    root = tmp_path / "once"
    task_dir = root / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text("# Goal\nx\n", encoding="utf-8")
    results_dir = root / "results"

    staged = investigation_cli.ensure_task_copy_in_results(task_dir, results_dir)
    assert (staged / "task.md").is_file()

    (task_dir / "late_change.txt").write_text("new", encoding="utf-8")
    staged_again = investigation_cli.ensure_task_copy_in_results(task_dir, results_dir)
    assert staged == staged_again
    assert not (staged_again / "late_change.txt").exists()

    # ensure_task_copy_in_results respects the task ignore file
    root = tmp_path / "ignore"
    task_dir = root / "task"
    (task_dir / "program_0" / "src").mkdir(parents=True)
    (task_dir / "Kamon").mkdir(parents=True)
    (task_dir / ".venv").mkdir()
    (task_dir / "program_0" / "results_smoke").mkdir()
    (task_dir / "task.md").write_text("# Goal\nx\n", encoding="utf-8")
    (task_dir / "Kamon" / "large.jpg").write_text("large", encoding="utf-8")
    (task_dir / ".venv" / "python").write_text("python", encoding="utf-8")
    (task_dir / "program_0" / "src" / "_results.json").write_text("{}", encoding="utf-8")
    (task_dir / "program_0" / "results_smoke" / "metrics.json").write_text("{}", encoding="utf-8")
    (task_dir / ".agentic_task_ignore").write_text(
        "Kamon/\n.venv/\nprogram_*/src/_results.json\nprogram_*/results*/\n", encoding="utf-8"
    )

    staged = investigation_cli.ensure_task_copy_in_results(task_dir, root / "results")

    assert (staged / "task.md").is_file()
    for ignored in ("Kamon", ".venv", "program_0/src/_results.json", "program_0/results_smoke"):
        assert not (staged / ignored).exists()

    # ensure_task_copy_in_results rejects a pre-existing task symlink
    root = tmp_path / "symlink"
    task_dir = root / "source-task"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text("# Goal\nx\n", encoding="utf-8")
    results_dir = root / "results"
    results_dir.mkdir()
    (results_dir / "task").symlink_to(root, target_is_directory=True)

    with pytest.raises(FileExistsError, match="Unsafe staged task symlink"):
        investigation_cli.ensure_task_copy_in_results(task_dir, results_dir)

    # ensure_directory_alias creates a relative symlink or keeps an existing target
    root = tmp_path / "alias"
    workspace = root / "workspace"
    target = root / "results" / "task"
    workspace.mkdir(parents=True)
    target.mkdir(parents=True)

    alias = investigation_cli.ensure_directory_alias(workspace / "task", target)
    assert alias.is_symlink()
    assert alias.resolve() == target.resolve()

    same = investigation_cli.ensure_directory_alias(target, target)
    assert same.is_dir()
    assert not same.is_symlink()




OUTPUT_DIR_PARSERS: list[tuple[str, Any, str]] = [
    ("experiment", investigation_cli.parse_experiment_dir, "Experiment"),
    ("idea", investigation_cli.parse_idea_dir, "Idea"),
]


def test_load_instructions_output_dir_markers_and_best_program_selection(tmp_path: Path) -> None:
    # load_instructions injects core and extra placeholders
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.md").write_text("# Goal\n- optimize\n\n# Rubrics\n- do x\n", encoding="utf-8")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    template = tmp_path / "template.md"
    template.write_text(
        "GOAL={{TASK_GOAL}}\nRUBRICS={{TASK_RUBRICS}}\nTD={{TASK_DIR}}\nRD={{RESULTS_DIR}}\nA={{ATTEMPT_ID}}\n",
        encoding="utf-8",
    )

    rendered = load_instructions(template, task_dir, results_dir, {"ATTEMPT_ID": "attempt_01"})

    for needle in ("GOAL=- optimize", "RUBRICS=- do x", f"TD={task_dir}", f"RD={results_dir}", "A=attempt_01"):
        assert needle in rendered

    # parse_*_dir markers extract the last match
    for name, parser, label in OUTPUT_DIR_PARSERS:
        output = f"line\n{label} directory: /tmp/x_1\nmore\n{label} directory: /tmp/x_2\n"
        parsed = parser(output)
        assert parsed is not None and parsed.name == "x_2", name

    # find_best_program_in_experiment prefers the highest correct score
    exp = tmp_path / "exp"
    for name, score, correct in (("program_0", 1.0, True), ("program_1", 3.0, False), ("program_2", 2.5, True)):
        program = exp / name
        program.mkdir(parents=True)
        make_program_dir(program, content="x")
        (program / "metrics.json").write_text(json.dumps({"combined_score": score}), encoding="utf-8")
        (program / "correct.json").write_text(json.dumps({"correct": correct}), encoding="utf-8")

    best = investigation_cli.find_best_program_in_experiment(exp)

    assert best is not None
    assert best[0].name == "program_2"
    assert best[1] == "src/program.py"
    assert best[2] == pytest.approx(2.5)
    assert best[3]


# ---------------------------------------------------------------------------
# Agent command construction
# ---------------------------------------------------------------------------


INVALID_AGENT_COMMAND_CASES: list[tuple[str, str, dict[str, str], str]] = [
    ("gemini_reasoning_effort", "gemini", {"reasoning_effort": "high"}, "not supported for gemini"),
    ("codex_profile_without_model", "codex", {"agent_profile": "custom"}, "agent_model is required"),
]


def test_build_agent_command_routes_through_headless_forwards_model_and_rejects_invalid_combinations() -> None:
    # every agent is routed through headless
    for agent in ("claude", "codex", "cursor", "gemini", "opencode", "pi"):
        command = script_runners.build_agent_command(agent, "/private/prompt.md")
        expected_prefix = [*HEADLESS_PREFIX, agent]
        assert command[: len(expected_prefix)] == expected_prefix, agent
        assert command[command.index("--allow") + 1] == "yolo", agent
        assert "--usage" in command and "--debug" in command, agent
        assert "--json" not in command and "--model" not in command, agent
        if agent in {"claude", "codex", "gemini"}:
            assert command[command.index("--prompt-file") + 1] == "/private/prompt.md", agent
        else:
            assert command[command.index("--prompt") + 1] == (
                "Read the complete task instructions from /private/prompt.md, then follow them exactly."
            ), agent

    # claude billing selection is left to headless
    command = script_runners.build_agent_command("claude", "prompt", agent_model="claude-opus-4-8")
    assert command[:6] == [*HEADLESS_PREFIX, "claude"]

    # model and effort are forwarded to headless
    command = script_runners.build_agent_command(
        "pi", "/private/prompt.md", agent_model="test-model", reasoning_effort="high"
    )
    assert command == [
        *HEADLESS_PREFIX,
        "pi",
        "--debug",
        "--usage",
        "--allow",
        "yolo",
        "--model",
        "test-model",
        "--reasoning-effort",
        "high",
        "--prompt",
        "Read the complete task instructions from /private/prompt.md, then follow them exactly.",
    ]

    # invalid combinations are rejected
    for name, agent, kwargs, message in INVALID_AGENT_COMMAND_CASES:
        with pytest.raises(ValueError, match=message):
            script_runners.build_agent_command(agent, "prompt", **kwargs)


# ---------------------------------------------------------------------------
# Logging and process bookkeeping
# ---------------------------------------------------------------------------


LOG_STEP_CASES: list[tuple[str, Any, str]] = [
    (
        "step_end",
        lambda: investigation_cli.log_step_end("idea_generation", 10.0),
        "[wrapper] step_end step=idea_generation status=completed elapsed=1.5s",
    ),
    (
        "step_start",
        lambda: investigation_cli.log_step_start("idea_generation", cycle=1),
        "[wrapper] step_start step=idea_generation status=started cycle=1",
    ),
]


def test_log_rendering_elapsed_units_field_order_phase_badge_and_separator() -> None:
    # format_elapsed uses readable units
    assert [investigation_cli.format_elapsed(v) for v in (0.04, 1.26, 61.2, 3723.6)] == [
        "0.0s",
        "1.3s",
        "1m 1s",
        "1h 2m 4s",
    ]

    # log_event renders fields in call order
    runtime_module.reset_log_render_state()
    with patch("builtins.print") as print_mock:
        investigation_cli.log_event("sample", b=2, a=1)

    print_mock.assert_called_once()
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z \[wrapper\] sample b=2 a=1$", print_mock.call_args.args[0]
    )

    # log_step_* include the phase badge for the idea step
    for name, call, expected in LOG_STEP_CASES:
        runtime_module.reset_log_render_state()
        with (
            patch("src.investigation.runtime.time.monotonic", return_value=11.5),
            patch("builtins.print") as print_mock,
        ):
            call()

        print_mock.assert_called_once()
        rendered = ANSI_ESCAPE_RE.sub("", print_mock.call_args.args[0])
        assert "[IDEA] " in rendered, name
        assert expected in rendered, name
        assert "phase=idea" in rendered, name

    # log_event inserts a separator between consecutive events
    runtime_module.reset_log_render_state()
    with patch("builtins.print") as print_mock:
        investigation_cli.log_event("first")
        investigation_cli.log_event("second")

    assert print_mock.call_count == 2
    first_rendered = print_mock.call_args_list[0].args[0]
    second_rendered = print_mock.call_args_list[1].args[0]
    assert "\n" not in first_rendered
    assert second_rendered.startswith("-" * 96 + "\n")
    assert "[wrapper] second" in second_rendered




# ---------------------------------------------------------------------------
# Parallel campaign and workers
# ---------------------------------------------------------------------------






CODEX_STREAM_DISCONNECT = (
    '{"type":"error","message":"stream disconnected before completion: '
    "error sending request for url (https://chatgpt.com/backend-api/codex/responses)\"}\n"
)
BRIDGE_EGRESS_HINT_CASES: list[tuple[str, tuple[str, ...], bool]] = [
    ("bridge", ("agent.name=codex", "container.enabled=true", "container.network=bridge"), True),
    ("host", ("--agent", "codex", "--containerize", "--container-network", "host"), False),
]






# ---------------------------------------------------------------------------
# Idea generation and single attempts
# ---------------------------------------------------------------------------




def test_run_single_attempt_times_out_with_graceful_shutdown_marker(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 1\n")
    (task_dir / "capture.py").write_text("# capture stub\n", encoding="utf-8")
    (task_dir / "evaluate.py").write_text("# evaluate stub\n", encoding="utf-8")
    (task_dir / "task.md").write_text("# Goal\n- optimize\n\n# Rubrics\n- score threshold\n", encoding="utf-8")
    rubric = {"id": "score_threshold", "description": "combined_score > 2.6", "type": "metric"}
    (tmp_path / "idea.json").write_text(
        json.dumps({"idea_id": "idea_0001", "title": "timeout smoke", "target_rubrics": [rubric]}), encoding="utf-8"
    )
    (tmp_path / "rubrics.json").write_text(json.dumps([rubric]), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_headless = bin_dir / "headless"
    fake_headless.write_text("#!/bin/bash\nset -euo pipefail\nsleep 60\n", encoding="utf-8")
    fake_headless.chmod(0o755)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HEADLESS_BIN": str(fake_headless)}

    with patch.dict(os.environ, env, clear=True):
        result = investigation_cli.run_single_attempt(
            **_attempt_kwargs(tmp_path, task_dir=task_dir, timeout_seconds=2, timeout_grace_seconds=1)
        )

    assert result.exit_code == 124
    assert "timed out after 2 seconds" in result.output
    assert "grace=1 seconds" in result.output


# ---------------------------------------------------------------------------
# run_cli interrupt handling
# ---------------------------------------------------------------------------


RUN_CLI_INTERRUPT_CASES: list[tuple[str, dict[str, Any], dict[str, Any], list[tuple[str, dict[str, str]]]]] = [
    ("terminates_active_processes", {"return_value": [101]}, {"return_value": []}, []),
    (
        "logs_process_targets_non_docker",
        {"return_value": [1234, 5678]},
        {"return_value": []},
        [("interrupt_cleanup_targets", {"mode": "processes", "targets": "1234,5678"})],
    ),
    (
        "logs_container_targets_docker",
        {"return_value": [1234]},
        {"return_value": ["agentic-codex-abc123"]},
        [("interrupt_cleanup_targets", {"mode": "containers", "targets": "agentic-codex-abc123"})],
    ),
    (
        "survives_second_interrupt_during_process_cleanup",
        {"side_effect": KeyboardInterrupt},
        {"return_value": ["agentic-codex-abc123"]},
        [
            ("interrupt_cleanup_reentered", {"stage": "processes"}),
            ("interrupt_cleanup_targets", {"mode": "containers", "targets": "agentic-codex-abc123"}),
        ],
    ),
    (
        "survives_second_interrupt_during_container_cleanup",
        {"return_value": [1234]},
        {"side_effect": KeyboardInterrupt},
        [
            ("interrupt_cleanup_reentered", {"stage": "containers"}),
            ("interrupt_cleanup_targets", {"mode": "processes", "targets": "1234"}),
        ],
    ),
]




# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _make_task_dir(root: Path, *, scaffold: bool = True) -> Path:
    task_dir = root / "task"
    task_dir.mkdir(parents=True)
    if scaffold:
        _write_scaffold(task_dir)
    (task_dir / "task.md").write_text("# Goal\n- optimize objective\n\n# Rubrics\n- use restarts\n", encoding="utf-8")
    (task_dir / "goal.md").write_text("legacy", encoding="utf-8")
    return task_dir


def _write_scaffold(task_dir: Path) -> None:
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 1\n")
    (task_dir / "capture.py").write_text("", encoding="utf-8")
    (task_dir / "evaluate.py").write_text("", encoding="utf-8")




def _write_idea_workspace(
    workspace: Path,
    *,
    idea_id: str = "idea_0001",
    title: str = "idea",
    rubrics: list[dict[str, Any]] | None = None,
    hypothesis: str = "hyp",
    plan: list[str] | None = None,
    notes: str = "notes",
) -> Path:
    rubrics = [METRIC_RUBRIC] if rubrics is None else rubrics
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "idea_bundle.json").write_text(
        json.dumps(
            {
                "idea_id": idea_id,
                "title": title,
                "hypothesis": hypothesis,
                "implementation_plan": plan or ["step"],
                "target_rubrics": rubrics,
                "notes_for_agent": notes,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "target_rubrics.json").write_text(json.dumps(rubrics), encoding="utf-8")
    return workspace


def _write_success_experiment(
    exp: Path,
    *,
    idea_id: str = "idea_0001",
    score: float = 2.8,
    rubrics: list[dict[str, Any]] | None = None,
) -> Path:
    make_program_dir(exp / "program_1", content="# candidate\n")
    (exp / "program_1" / "metrics.json").write_text(json.dumps({"combined_score": score}), encoding="utf-8")
    (exp / "program_1" / "correct.json").write_text('{"correct": true}', encoding="utf-8")
    (exp / "rubric_status.json").write_text(
        json.dumps(
            {
                "attempt_id": f"{idea_id}_attempt_01",
                "idea_id": idea_id,
                "overall_pass": True,
                "rubrics": rubrics or [{"id": "score_threshold", "pass": True, "evidence": str(score)}],
            }
        ),
        encoding="utf-8",
    )
    return exp


def _idea_result(workspace: Path) -> investigation_cli.IdeaGenerationResult:
    return investigation_cli.IdeaGenerationResult(
        output=f"Idea directory: {workspace}\n", exit_code=0, idea_dir=workspace
    )


def _attempt_result(exp: Path) -> investigation_cli.RunAttemptResult:
    return investigation_cli.RunAttemptResult(output=f"Experiment directory: {exp}\n", exit_code=0, experiment_dir=exp)








def _config_error_calls(log_mock: Mock, reason: str) -> list[Any]:
    return [
        call
        for call in log_mock.call_args_list
        if call.args and call.args[0] == "config_error" and call.kwargs.get("reason") == reason
    ]




STALE_IDEA_MEMORY_CASES: list[tuple[str, str, str]] = [
    ("legacy_idea_id_dir", "01_basin_hopping_optimization", "legacy idea note"),
    (
        "same_cycle_alternate_dir",
        "01_l_bfgs_b_annealed_penalty_lp_radii_multi_start_diversification",
        "alternate idea note",
    ),
]


def test_main_successful_attempt_updates_manifest_prints_summary_and_reconciles_stale_memory(
    tmp_path: Path,
) -> None:
    # the best manifest and attempt history are updated on a successful attempt
    root = tmp_path / "manifest"
    _, results_dir, cfg = _main_setup(root)
    rubrics = [
        {"id": "uses_restarts", "description": "use restarts", "type": "implementation"},
        METRIC_RUBRIC,
    ]
    exp = _write_success_experiment(
        root / "exp_success",
        rubrics=[
            {"id": "uses_restarts", "pass": True, "evidence": "ok"},
            {"id": "score_threshold", "pass": True, "evidence": "2.8"},
        ],
    )
    idea_workspace = _write_idea_workspace(root / "idea_workspace", rubrics=rubrics)

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(f"{CLI}.run_idea_generation", return_value=_idea_result(idea_workspace)),
        patch(f"{CLI}.run_single_attempt", return_value=_attempt_result(exp)),
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 0
    manifest = json.loads((results_dir / "best_program_manifest.json").read_text())
    assert manifest["source_attempt_id"] == "idea_0001_attempt_01"
    assert manifest["combined_score"] == pytest.approx(2.8)
    attempts = (results_dir / "attempts_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(attempts) == 1
    assert json.loads(attempts[0])["wrapper_output_log"] == str(exp / "wrapper_output.log")
    assert "Experiment directory" in (exp / "wrapper_output.log").read_text(encoding="utf-8")

    # the human experiment summary is printed
    root = tmp_path / "summary"
    _, _, cfg = _main_setup(root)
    exp = _write_success_experiment(
        root / "exp_success", rubrics=[{"id": "score_threshold", "pass": True, "evidence": "ok"}]
    )
    idea_workspace = _write_idea_workspace(root / "idea_workspace")

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(f"{CLI}.run_idea_generation", return_value=_idea_result(idea_workspace)),
        patch(f"{CLI}.run_single_attempt", return_value=_attempt_result(exp)),
        contextlib.redirect_stdout(io.StringIO()) as stdout,
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 0
    rendered = stdout.getvalue()
    for needle in (
        "Agentic Scientist · task · 1 cycles",
        "Cycle 1/1",
        "Idea: idea",
        "Score: 2.80",
        "Rubrics: 1/1 passed",
        "PASS  score_threshold",
        "Outcome: PASSED",
        "Finished · 1 cycles · best score 2.80",
    ):
        assert needle in rendered
    assert runtime_module._EVENT_LOGGER is None

    # stale idea memory dirs are reconciled into the canonical title dir
    for name, stale_dir_name, note in STALE_IDEA_MEMORY_CASES:
        root = tmp_path / name
        _, results_dir, cfg = _main_setup(root)
        exp = _write_success_experiment(root / "exp_success", idea_id="basin_hopping_optimization")
        idea_workspace = _write_idea_workspace(
            root / "idea_workspace",
            idea_id="basin_hopping_optimization",
            title="Basin Hopping Optimization of Circle Centers",
        )
        stale_dir = results_dir / "ideas" / stale_dir_name
        stale_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "memory.md").write_text(f"# Memory\n{note}\n", encoding="utf-8")

        with (
            patch(f"{CLI}.load_config", return_value=cfg),
            patch(f"{CLI}.run_idea_generation", return_value=_idea_result(idea_workspace)),
            patch(f"{CLI}.run_single_attempt", return_value=_attempt_result(exp)),
        ):
            exit_code = investigation_cli.main()

        assert exit_code == 0, name
        canonical_memory = results_dir / "ideas" / "01_basin_hopping_optimization_of_circle_centers" / "memory.md"
        assert canonical_memory.is_file(), name
        assert note in canonical_memory.read_text(encoding="utf-8"), name
        assert not stale_dir.exists(), name




def test_main_idea_generation_retries_and_failure_budget(tmp_path: Path) -> None:
    # main moves to the next idea after the failure budget
    root = tmp_path / "budget"
    _, results_dir, cfg = _main_setup(root, max_idea_cycles=2)
    exp_pass = _write_success_experiment(root / "exp_pass", idea_id="idea_0002", score=2.7)
    rubric = [{"id": "score_threshold", "description": "score", "type": "metric"}]
    workspace_1 = _write_idea_workspace(
        root / "idea_workspace_1", title="first", hypothesis="h1", plan=["a"], rubrics=rubric, notes=""
    )
    workspace_2 = _write_idea_workspace(
        root / "idea_workspace_2", idea_id="idea_0002", title="second", hypothesis="h2", plan=["b"], rubrics=rubric, notes=""
    )
    attempts = [investigation_cli.RunAttemptResult(output="", exit_code=1, experiment_dir=None), _attempt_result(exp_pass)]

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(f"{CLI}.run_idea_generation", side_effect=[_idea_result(workspace_1), _idea_result(workspace_2)]),
        patch(f"{CLI}.run_single_attempt", side_effect=attempts),
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 0
    assert len((results_dir / "ideas_history.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 2
    attempt_lines = (results_dir / "attempts_history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(attempt_lines) == 2
    first_log = Path(json.loads(attempt_lines[0])["wrapper_output_log"])
    assert first_log.resolve() == (results_dir / "ideas" / "01_first" / "wrapper_output.log").resolve()
    assert first_log.is_file()

    # idea generation is retried, then fails without consuming a cycle
    root = tmp_path / "retry_fail"
    _, results_dir, cfg = _main_setup(root, max_idea_cycles=2)

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(f"{CLI}.run_idea_generation", return_value=IDEA_FAILED) as idea_mock,
        patch(f"{CLI}.run_single_attempt") as attempt_mock,
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 2
    assert idea_mock.call_count == 2
    assert [call.kwargs["timeout_seconds"] for call in idea_mock.call_args_list] == [3600, 3600]
    assert len({call.kwargs["output_dir"] for call in idea_mock.call_args_list}) == 2
    assert [call.kwargs["cycle_index"] for call in idea_mock.call_args_list] == [1, 1]
    attempt_mock.assert_not_called()
    assert json.loads((results_dir / "outer_state.json").read_text(encoding="utf-8"))["current_cycle"] == 0
    failures = [
        json.loads(line)
        for line in (results_dir / "idea_generation_failures.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [failure["generation_attempt"] for failure in failures] == [1, 2]
    assert failures[-1]["reason"] == "idea_generation_exit_nonzero"

    # an invalid idea bundle is retried, then the valid idea runs
    root = tmp_path / "invalid_bundle"
    _, results_dir, cfg = _main_setup(root)
    invalid_workspace = root / "invalid_idea"
    invalid_workspace.mkdir()
    valid_workspace = _write_idea_workspace(
        root / "valid_idea",
        idea_id="recovered_idea",
        title="Recovered idea",
        hypothesis="retry succeeds",
        plan=["implement"],
        rubrics=[{"id": "score", "description": "improve score", "type": "metric"}],
        notes="",
    )

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(
            f"{CLI}.run_idea_generation",
            side_effect=[
                investigation_cli.IdeaGenerationResult("invalid", 0, invalid_workspace),
                investigation_cli.IdeaGenerationResult("valid", 0, valid_workspace),
            ],
        ) as idea_mock,
        patch(
            f"{CLI}.run_single_attempt", return_value=investigation_cli.RunAttemptResult("failed", 1, None)
        ) as attempt_mock,
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 0
    assert idea_mock.call_count == 2
    attempt_mock.assert_called_once()
    failures_text = (results_dir / "idea_generation_failures.jsonl").read_text(encoding="utf-8")
    assert "missing_or_invalid_idea_bundle" in failures_text

    # an idea generation launch error is retried and logged
    root = tmp_path / "launch_error"
    _, results_dir, cfg = _main_setup(root)

    with (
        patch(f"{CLI}.load_config", return_value=cfg),
        patch(f"{CLI}.run_idea_generation", side_effect=OSError("launch failed")) as idea_mock,
        patch(f"{CLI}.run_single_attempt") as attempt_mock,
    ):
        exit_code = investigation_cli.main()

    assert exit_code == 2
    assert idea_mock.call_count == 2
    attempt_mock.assert_not_called()
    store = investigation_cli.state_store
    failures = store.tail_jsonl(store.idea_generation_failures_path(results_dir), max_lines=2)
    assert len(failures) == 2
    assert "launch failed" in Path(str(failures[0]["log_path"])).read_text(encoding="utf-8")




# draft_exit 0 covers a drafting run that reports success but creates no scaffold.
DRAFT_EXIT_CASES: list[tuple[int, int]] = [(0, 2), (1, 2), (2, 2), (3, 3), (4, 4), (75, 75), (124, 4), (130, 130)]














