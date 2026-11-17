"""Run the AI-Scientist-v3 engine as a kernel executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tar_lab.core.workorder import Attempt, EventSink, ResultStatus, Usage, WorkResult
from tar_lab.integrations.cli_agents.process import run_supervised, tail

from .engine import AIScientistEngine, RunRequest
from .results import load_run


@dataclass
class AIScientistExecutor:
    """Work-order ``params``: ``task_dir`` (required), ``agent``, ``model``, ``phases``,
    ``overrides``. Each attempt is a fresh engine run; engine-level resume is not wired yet."""

    engine: AIScientistEngine
    name: str = "ai-scientist"
    supports_resume: bool = False

    def request(self, attempt: Attempt) -> RunRequest:
        p = attempt.order.params
        run_id = f"{attempt.order.id}-a{attempt.number}-{datetime.now():%Y%m%d_%H%M%S}"
        extra = {k: p[k] for k in ("model", "phases") if k in p}
        if "phases" in extra:
            extra["phases"] = tuple(extra["phases"])
        return RunRequest(Path(p["task_dir"]), run_id, agent=p.get("agent", "codex"),
                          overrides=dict(p.get("overrides", {})), **extra)

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        d = attempt.attempt_dir
        d.mkdir(parents=True, exist_ok=True)
        request = self.request(attempt)
        run_dir = self.engine.run_dir(request)
        emit("order.session", {"session_id": request.run_id, "run_dir": str(run_dir)})
        self.engine.results_root.mkdir(parents=True, exist_ok=True)
        outcome = run_supervised(
            self.engine.build_command(request), cwd=self.engine.engine_dir, stdin_path=None,
            stdout_path=d / "engine.log", stderr_path=d / "engine.stderr.log",
            timeout=attempt.timeout_seconds, emit=emit, env=self.engine.environment())
        def result(status: ResultStatus, summary: str, *, error: str | None = None,
                   cost: float | None = None) -> WorkResult:
            return WorkResult(status, summary, (str(run_dir),), session_id=request.run_id,
                              wall_seconds=round(outcome.wall_seconds, 1), error=error,
                              cost_usd=cost)

        if outcome.timed_out:
            return result("limit_exceeded", "Stopped at the wall-time limit.")
        if not (run_dir / "pipeline_events.jsonl").exists():
            return result("crashed", "The engine produced no run directory.",
                          error=tail(d / "engine.log") or f"exit code {outcome.returncode}")
        summary = load_run(run_dir)
        best = summary.best_program or {}
        text = (f"Engine run {summary.status}; best combined_score="
                f"{best.get('combined_score')}, correct={best.get('correct')}.")
        if summary.completed and outcome.returncode == 0:
            return result("done", text, cost=summary.estimated_cost_usd)
        return result("failed", text, error=tail(d / "engine.log"),
                      cost=summary.estimated_cost_usd)

    def session_usage(self, session_id: str) -> Usage | None:
        return None
