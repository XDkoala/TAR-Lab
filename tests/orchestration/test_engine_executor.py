import json
import sys
import textwrap
from pathlib import Path

import pytest

from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Limits, WorkOrder
from tar_lab.integrations.ai_scientist import AIScientistEngine, AIScientistExecutor, load_run
from tar_lab.orchestration.kernel import Kernel, Project

# Stands in for the engine's interpreter: `python -m src.investigation.cli <overrides>`.
FAKE_PYTHON = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = dict(a.split("=", 1) for a in sys.argv[3:] if "=" in a)
    run = args["paths.results_dir"]
    os.makedirs(run)
    status = os.environ.get("FAKE_STATUS", "finished")
    events = [
        {{"event": "run_config", "timestamp": "2026-11-17T08:00:00.000Z", "fields": {{}}}},
        {{"event": "cost_summary", "timestamp": "2026-11-17T08:09:00.000Z",
          "fields": {{"run_total_cost_usd": "1.25"}}}},
        {{"event": status, "timestamp": "2026-11-17T08:10:00.000Z",
          "fields": {{"cycles_completed": int(args["execution.max_idea_cycles"])}}}},
    ]
    with open(os.path.join(run, "events.jsonl"), "w") as f:
        f.write("\\n".join(json.dumps(e) for e in events) + "\\n")
    json.dump({{"combined_score": 2.6, "correct": True, "program_dir": run + "/ideas/x/program_1",
               "entrypoint_path": "src/program.py", "source_attempt_id": "cycle_01"}},
              open(os.path.join(run, "best_program_manifest.json"), "w"))
    print("engine finished", sys.argv[1:3], os.environ.get("HEADLESS_BILLING"))
""")


@pytest.fixture
def kernel(tmp_path: Path) -> Kernel:
    engine_dir = tmp_path / "engine"
    (engine_dir / ".venv" / "bin").mkdir(parents=True)
    python = engine_dir / ".venv" / "bin" / "python"
    python.write_text(FAKE_PYTHON.format(python=sys.executable))
    python.chmod(0o755)
    project = Project(tmp_path / "proj")
    project.init()
    executor = AIScientistExecutor(AIScientistEngine(engine_dir, tmp_path / "runs"))
    return Kernel(project, Ledger(project.ledger_path), {"ai-scientist": executor})


def submit(kernel: Kernel, tmp_path: Path) -> None:
    task = tmp_path / "circpack"
    task.mkdir()
    kernel.submit(WorkOrder("wo-1", "engineer", "ai-scientist", "baseline", (), "score",
                            Limits(60), params={"task_dir": str(task), "cycles": 2}))


def test_engine_executor_reports_score_cost_and_run_dir(kernel: Kernel, tmp_path: Path) -> None:
    submit(kernel, tmp_path)
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.result is not None
    assert state.result.cost_usd == 1.25 and "combined_score=2.6" in state.result.summary
    assert "outcome=winner" in state.result.summary
    run_dir = Path(state.result.deliverables[0])
    assert run_dir.parent == tmp_path / "runs" / "circpack" and run_dir.name == state.session_id
    summary = load_run(run_dir)
    assert summary.wall_seconds == 600 and summary.cycles_completed == 2
    log = next((tmp_path / "proj").rglob("engine.log")).read_text()
    assert "['-m', 'src.investigation.cli']" in log and "subscription" in log


def test_engine_executor_failed_run(kernel: Kernel, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_STATUS", "campaign_failed")
    submit(kernel, tmp_path)
    kernel.run_pending()
    result = kernel.ledger.orders()["wo-1"].result
    assert result is not None and result.status == "failed"
    assert "engine finished" in (result.error or "")


def test_order_params_round_trip(kernel: Kernel, tmp_path: Path) -> None:
    submit(kernel, tmp_path)
    order = kernel.ledger.orders()["wo-1"].order
    assert json.loads(json.dumps(order.to_dict()))["params"]["cycles"] == 2
