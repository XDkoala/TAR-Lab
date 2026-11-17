import json
import sys
import textwrap
from pathlib import Path

import pytest

from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import Limits, Usage, WorkOrder
from tar_lab.integrations.cli_agents.devin import DevinExecutor, extract_report
from tar_lab.orchestration.kernel import Kernel, Project


def test_extract_report_takes_last_valid_object() -> None:
    text = ('It printed 5050.{"status": "done", "summary": "first"} more '
            '{"note": 1} {"status": "failed", "summary": "last", "deliverables": ["a"]}')
    assert extract_report(text) == {"status": "failed", "summary": "last",
                                    "deliverables": ["a"]}
    assert extract_report('{"status": "weird", "summary": "x"}') is None
    assert extract_report("no json") is None


FAKE_DEVIN = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = sys.argv[1:]
    log = os.environ["FAKE_LOG"]
    if args[0] == "list":
        print(json.dumps([{{"id": "old", "last_activity_at": 1}}]
                         + ([{{"id": "new-sess", "last_activity_at": 2}}]
                            if os.path.exists(log) else [])))
        sys.exit(0)
    with open(log, "a") as f:
        f.write(json.dumps({{"args": args, "host": "ACP_BACKEND" in os.environ}}) + "\\n")
    export = args[args.index("--export") + 1]
    json.dump({{"session_id": "new-sess", "final_metrics": {{"total_prompt_tokens": "40",
               "total_cached_tokens": "30", "total_completion_tokens": "2"}}}},
              open(export, "w"))
    print('Done. {{"status": "done", "summary": "ok", "deliverables": ["hello.py"], '
          '"sources": []}}')
""")


def test_devin_executor_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "devin"
    script.write_text(FAKE_DEVIN.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("ACP_BACKEND", "windsurf")
    project = Project(tmp_path / "proj")
    project.init()
    kernel = Kernel(project, Ledger(project.ledger_path),
                    {"devin": DevinExecutor(binary=str(script))})
    kernel.submit(WorkOrder("wo-1", "designer", "devin", "g", ("hello.py",), "a",
                            Limits(60)))
    kernel.run_pending()
    state = kernel.ledger.orders()["wo-1"]
    assert state.status == "done" and state.session_id == "new-sess"
    assert state.usage == Usage(40, 30, 2)
    call = json.loads((tmp_path / "calls.log").read_text().splitlines()[0])
    assert call["host"] is False  # host-mode variables are stripped
    assert "-r" not in call["args"] and "--prompt-file" in call["args"]
