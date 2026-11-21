from pathlib import Path

import pytest

from tar_lab.core import preflight
from tar_lab.core.workorder import WorkResult
from tar_lab.figures import build as fb

from .orchestration.test_lab import FakeModel, decision, make_lab, order


def test_requirements_follow_executors_and_roles() -> None:
    names = [r.name for r in preflight.requirements(["codex"])]
    assert "codex CLI" in names and "devin CLI, logged in" not in names
    reqs = preflight.requirements(["codex", "devin"],
                                  {"devin": frozenset({"designer"}),
                                   "codex": preflight.ALL_ROLES})
    devin = next(r for r in reqs if r.name.startswith("devin"))
    assert devin.roles == {"designer"}
    tex = next(r for r in reqs if r.name.startswith("TeX Live"))
    assert tex.roles == preflight.PAPER_ROLES


def test_run_filters_by_role_and_optional_never_blocks() -> None:
    ok = preflight.Requirement("a", frozenset({"writer"}), lambda: None, "-")
    bad = preflight.Requirement("b", frozenset({"writer"}), lambda: "gone", "install b")
    soft = preflight.Requirement("c", frozenset({"writer"}), lambda: "gone", "-", optional=True)
    other = preflight.Requirement("d", frozenset({"engineer"}), lambda: "gone", "-")
    findings = preflight.run([ok, bad, soft, other], role="writer")
    assert [f.requirement.name for f in findings] == ["a", "b", "c"]
    assert [f.requirement.name for f in preflight.blocking(findings)] == ["b"]
    assert findings[1].line() == "MISSING b (writer): gone. Fix: install b"
    assert findings[2].line().startswith("WARN    c")
    assert preflight.run([other], role="writer") == []


def test_lab_rejects_a_stage_whose_tools_are_missing(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_FIGURES_PYTHON", str(tmp_path / "missing-python"))
    model = FakeModel([decision([order("writer"), order("engineer")]), decision(),
                       decision()])
    lab, sent = make_lab(tmp_path, model, [WorkResult("done", "ok", session_id="s")])
    lab.check_requirements = True
    lab.step()
    [rejected] = lab._events("lab.order_rejected")
    assert (rejected.order_id or "").endswith("writer")
    assert "figures environment" in rejected.payload["reason"]
    assert "tarlab figures setup" in rejected.payload["reason"]
    assert any(o.endswith("engineer") for o in lab.ledger.orders())  # other stages run
    [report] = lab._events("lab.preflight")
    assert any("MISSING figures environment" in line for line in report.payload["findings"])
    assert any(s.startswith("运行环境缺少工具") for s in sent)
    assert fb.figures_python() is None
