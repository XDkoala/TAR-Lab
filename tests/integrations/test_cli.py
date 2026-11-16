import json
from pathlib import Path

import pytest

from tar_lab.cli import main
from tar_lab.integrations.undermind.settings import DEFAULT_CLIENT_ID, DEFAULT_REDIRECT_PORT

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cimd_document_matches_client_settings() -> None:
    doc = json.loads((REPO_ROOT / "deploy" / "oauth" / "client-metadata.json").read_text())
    assert doc["client_id"] == DEFAULT_CLIENT_ID
    assert f"http://127.0.0.1:{DEFAULT_REDIRECT_PORT}/callback" in doc["redirect_uris"]
    assert doc["token_endpoint_auth_method"] == "none"


def test_undermind_status_and_logout_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TARLAB_UNDERMIND_CREDENTIALS", str(tmp_path / "creds.json"))
    assert main(["undermind", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["logged_in"] is False
    assert main(["undermind", "logout"]) == 0
    assert "No stored credentials" in capsys.readouterr().out


def test_engine_summary_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pipeline_events.jsonl").write_text(
        json.dumps({"event": "pipeline_end", "fields": {"status": "completed"}}) + "\n"
    )
    assert main(["engine", "summary", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["completed"] is True
    assert main(["engine", "summary", str(tmp_path / "missing")]) == 1
