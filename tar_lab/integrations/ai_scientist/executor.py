"""Run the AI-Scientist-v3 idea search as a kernel executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from tar_lab.core.workorder import Attempt, EventSink, ResultStatus, Usage, WorkResult
from tar_lab.integrations.cli_agents.process import run_supervised, tail

from .engine import AIScientistEngine, OverrideValue, RunRequest
from .results import RunSummary, load_run


@dataclass
class AIScientistExecutor:
    """Work-order ``params``: ``task_dir`` (required), ``cycles`` (idea cycles), ``agent``,
    ``model``, ``reasoning_effort``, ``overrides``, ``results_root`` (default: the
    engine's). Unset values fall back to the executor's defaults. Each attempt is a fresh
    engine run; engine-level resume is not wired yet."""

    engine: AIScientistEngine
    name: str = "ai-scientist"
    supports_resume: bool = False
    agent: str = "codex"
    model: str | None = None
    reasoning_effort: str | None = "medium"
    overrides: Mapping[str, OverrideValue] = field(default_factory=dict)

    def engine_for(self, attempt: Attempt) -> AIScientistEngine:
        root = attempt.order.params.get("results_root")
        return replace(self.engine, results_root=Path(root)) if root else self.engine

    def request(self, attempt: Attempt) -> RunRequest:
        p = attempt.order.params
        run_id = f"{attempt.order.id}-a{attempt.number}-{datetime.now():%Y%m%d_%H%M%S}"
        return RunRequest(Path(p["task_dir"]), run_id, agent=p.get("agent", self.agent),
                          model=p.get("model", self.model),
                          reasoning_effort=p.get("reasoning_effort", self.reasoning_effort),
                          max_idea_cycles=int(p.get("cycles", 3)),
                          overrides={**self.overrides, **p.get("overrides", {})})

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        d = attempt.attempt_dir
        d.mkdir(parents=True, exist_ok=True)
        engine = self.engine_for(attempt)
        request = self.request(attempt)
        run_dir = engine.run_dir(request)
        emit("order.session", {"session_id": request.run_id, "run_dir": str(run_dir)})
        engine.results_root.mkdir(parents=True, exist_ok=True)
        outcome = run_supervised(
            engine.build_command(request), cwd=engine.engine_dir, stdin_path=None,
            stdout_path=d / "engine.log", stderr_path=d / "engine.stderr.log",
            timeout=attempt.timeout_seconds, emit=emit, env=engine.environment())

        def result(status: ResultStatus, summary: str, *, error: str | None = None,
                   cost: float | None = None) -> WorkResult:
            return WorkResult(status, summary, (str(run_dir),), session_id=request.run_id,
                              wall_seconds=round(outcome.wall_seconds, 1), error=error,
                              cost_usd=cost)

        summary = load_run(run_dir)
        if not (summary.investigation_dir / "events.jsonl").exists():
            if outcome.timed_out:
                return result("limit_exceeded", "Stopped at the wall-time limit.")
            return result("crashed", "The engine produced no run directory.",
                          error=tail(d / "engine.log") or f"exit code {outcome.returncode}")
        text = describe_run(summary)
        if outcome.timed_out:
            return result("limit_exceeded", f"Stopped at the wall-time limit. {text}",
                          cost=summary.estimated_cost_usd)
        if summary.completed and outcome.returncode == 0:
            return result("done", text, cost=summary.estimated_cost_usd)
        return result("failed", text, error=tail(d / "engine.log"),
                      cost=summary.estimated_cost_usd)

    def session_usage(self, session_id: str) -> Usage | None:
        return None


def describe_run(summary: RunSummary) -> str:
    """One paragraph for the main agent: outcome, best score, where the best program is, and
    the engine's latest conclusion."""
    best = summary.best_program or {}
    inv = summary.investigation_dir
    parts = [f"Engine run {summary.status or 'unfinished'}; outcome={summary.outcome}."]
    if not best or best.get("source_attempt_id") == "bootstrap":
        parts.append("No idea beat the baseline program; there is no improved program.")
    else:
        parts.append(f"Best combined_score={best.get('combined_score')}, "
                     f"correct={best.get('correct')}: {best.get('program_dir')}/"
                     f"{best.get('entrypoint_path', '')} "
                     f"(attempt {best.get('source_attempt_id')}).")
    try:
        attempts = len((inv / "attempts_history.jsonl").read_text(encoding="utf-8").splitlines())
        conclusion = (inv / "conclusions.md").read_text(encoding="utf-8").strip()
    except OSError:
        attempts, conclusion = 0, ""
    parts.append(f"{attempts} attempt(s) evaluated.")
    if conclusion:
        parts.append(f"Latest conclusion: {conclusion[:600]}")
    parts.append(f"Details: {inv} (conclusions.md, attempts_history.jsonl, ideas/).")
    return " ".join(parts)
