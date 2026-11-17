import json
import sys
import textwrap
from pathlib import Path

import pytest

from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Limits, WorkOrder
from tar_lab.integrations.ai_scientist import AIScientistEngine, AIScientistExecutor, load_run
from tar_lab.orchestration.kernel import Kernel, Project

FAKE_AGSCI = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = dict(a.split("=", 1) for a in sys.argv[3:] if "=" in a)
    task = os.path.basename(sys.argv[2])
    run = os.path.join(args["paths.results_root"], task, args["agent.name"], args["run_id"])
    os.makedirs(os.path.join(run, "investigation"))
    events = [
        {{"event": "pipeline_start", "timestamp": "2026-11-17T08:00:00.000Z", "fields": {{}}}},
        {{"event": "cost_summary", "timestamp": "2026-11-17T08:09:00.000Z",
          "fields": {{"run_total_cost_usd": "1.25"}}}},
        {{"event": "pipeline_end", "timestamp": "2026-11-17T08:10:00.000Z",
          "fields": {{"status": os.environ.get("FAKE_STATUS", "completed")}}}},
    ]
    with open(os.path.join(run, "pipeline_events.jsonl"), "w") as f:
        f.write("\\n".join(json.dumps(e) for e in events) + "\\n")
    json.dump({{"combined_score": 2.6, "correct": True}},
              open(os.path.join(run, "investigation", "best_program_manifest.json"), "w"))
    print("engine finished")
""")


@pytest.fixture
def kernel(tmp_path: Path) -> Kernel:
    engine_dir = tmp_path / "engine"
    (engine_dir / ".venv" / "bin").mkdir(parents=True)
    agsci = engine_dir / ".venv" / "bin" / "agsci"
    agsci.write_text(FAKE_AGSCI.format(python=sys.executable))
    agsci.chmod(0o755)
    project = Project(tmp_path / "proj")
    project.init()
    executor = AIScientistExecutor(AIScientistEngine(engine_dir, tmp_path / "runs"))
    return Kernel(project, Ledger(project.ledger_path), {"ai-scientist": executor})


def submit(kernel: Kernel, tmp_path: Path) -> None:
    task = tmp_path / "circpack"
    task.mkdir()
    kernel.submit(WorkOrder("wo-1", "engineer", "ai-scientist", "baseline", (), "score",
                            Limits(60), params={"task_dir": str(task),
                                                "phases": ["investigation"],
                                                "overrides": {"agent.reasoning_effort":
                                                              "medium"}}))


def test_engine_executor_reports_score_cost_and_run_dir(kernel: Kernel, tmp_path: Path) -> None:
    submit(kernel, tmp_path)
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.result is not None
    assert state.result.cost_usd == 1.25 and "combined_score=2.6" in state.result.summary
    run_dir = Path(state.result.deliverables[0])
    assert run_dir.parent == tmp_path / "runs" / "circpack" / "codex"
    assert run_dir.name == state.session_id
    assert load_run(run_dir).wall_seconds == 600


def test_engine_executor_failed_run(kernel: Kernel, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_STATUS", "failed")
    submit(kernel, tmp_path)
    kernel.run_pending()
    result = kernel.ledger.orders()["wo-1"].result
    assert result is not None and result.status == "failed"
    assert result.error == "engine finished"


def test_order_params_round_trip(kernel: Kernel, tmp_path: Path) -> None:
    submit(kernel, tmp_path)
    order = kernel.ledger.orders()["wo-1"].order
    assert order.params["overrides"] == {"agent.reasoning_effort": "medium"}
    assert json.loads(json.dumps(order.to_dict()))["params"]["phases"] == ["investigation"]
