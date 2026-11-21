import json
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from tar_lab.cli import main as cli_main
from tar_lab.core.ledger import Ledger
from tar_lab.orchestration import service
from tar_lab.orchestration.kernel import Project

CHARTER = {"idea": "idea", "title": "Tiny", "goal": "paper", "target_venue": "workshop",
           "constraints": "", "budget_usd": 20, "deadline_hours": 24, "chat_language": "en"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = Project(tmp_path / "my proj")
    p.init()
    ledger = Ledger(p.ledger_path)
    ledger.append("project.charter", None, {"charter": CHARTER})
    ledger.append("user.question", None, {"qid": "q1-1", "kind": "approval", "text": "Pick A?",
                                          "options": ["A", "B"], "default": "A"})
    ledger.close()
    return p.root


def test_unit_restarts_crashes_but_not_finished_or_suspended_runs(project: Path) -> None:
    text = service.unit_text(project, python="/venv/bin/python")
    assert f"ExecStart=/venv/bin/python -m tar_lab.cli run {project}" in text
    assert "Restart=on-failure" in text and "UnsetEnvironment=PYTHONPATH" in text
    assert "EnvironmentFile=-%E/tar-lab/runner.env" in text  # optional TARLAB_* settings
    assert (f"RestartPreventExitStatus={service.EXIT_SUSPENDED} "
            f"{service.EXIT_ALREADY_RUNNING}") in text
    name = service.unit_name(project)
    assert name.startswith("tarlab-my-proj-") and name.endswith(".service")
    assert name != service.unit_name(project.parent / "other" / "my proj")


def test_install_and_uninstall(project: Path, tmp_path: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls: list[list[str]] = []

    def systemctl(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    service.install(project, systemctl)
    unit = tmp_path / "config" / "systemd" / "user" / service.unit_name(project)
    assert unit.exists() and service.installed(project)
    assert calls == [["daemon-reload"], ["enable", "--now", unit.name]]
    assert service.uninstall(project, systemctl) and not unit.exists()
    assert calls[-2:] == [["disable", "--now", unit.name], ["daemon-reload"]]


def test_only_one_runner_per_project(project: Path) -> None:
    with service.runner_lock(project) as first:
        assert first
        with service.runner_lock(project) as second:
            assert not second
        assert cli_main(["run", str(project)]) == service.EXIT_ALREADY_RUNNING
    with service.runner_lock(project) as again:
        assert again  # released with the holder


def test_print_mode_without_runner_queues_the_message(project: Path,
                                                      capsys: pytest.CaptureFixture[str]
                                                      ) -> None:
    assert cli_main(["-p", "how is it going?", str(project), "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["reply"] is None and "no runner" in out["note"]
    assert out["conversation"][-1] == {"ts": out["conversation"][-1]["ts"], "from": "user",
                                       "text": "how is it going?"}
    assert out["open_questions"][0]["qid"] == "q1-1"


def test_print_mode_prints_the_main_agents_answer(project: Path,
                                                  monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("tar_lab.tui.system.runner_pids", lambda _root: [1])
    ledger = Ledger(project / "ledger.sqlite")

    def answer() -> None:  # stands in for the runner's main agent
        while not any(e.kind == "user.message" for e in ledger.events()):
            time.sleep(0.05)
        ledger.append("pi.decision", None, {"decision": {"message_to_user": "All on track."}})

    threading.Thread(target=answer, daemon=True).start()
    assert cli_main(["-p", "status?", str(project), "--wait", "20"]) == 0
    assert capsys.readouterr().out.strip() == "All on track."


def test_status_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["status", str(project), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "Tiny" and out["status"] == "active" and out["budget_usd"] == 20
