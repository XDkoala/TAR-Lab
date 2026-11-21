import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tar_lab.integrations.literature import LiteratureConfig
from tar_lab.integrations.literature.status import collect, render, run

S2_SECRET = "s2-secret-value-a1b2"
OA_SECRET = "oa-secret-value-c3d4"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("TARLAB_LITERATURE_ENV", str(tmp_path / "literature.env"))
    monkeypatch.setenv("TARLAB_RATE_DIR", str(tmp_path / "rate"))
    for name in ("TARLAB_S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "TARLAB_OPENALEX_API_KEY",
                 "OPENALEX_API_KEY", "TARLAB_OPENALEX_MAILTO"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _env_file(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def test_status_without_keys(isolated: Path) -> None:
    out, code = run()
    assert code == 0
    assert "(missing)" in out
    assert "Semantic Scholar key: not set" in out
    assert "anonymous shared pool, expect HTTP 429 under load" in out
    assert "last request never" in out


def test_status_key_from_env_shows_only_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_S2_API_KEY", S2_SECRET)
    for as_json in (False, True):
        out, _ = run(as_json=as_json)
        assert S2_SECRET not in out and S2_SECRET[:-4] not in out
        assert "a1b2" in out
    assert "set (env TARLAB_S2_API_KEY, …a1b2)" in run()[0]


def test_status_keys_from_file_and_mailto(isolated: Path) -> None:
    path = isolated / "literature.env"
    _env_file(path, f"SEMANTIC_SCHOLAR_API_KEY={S2_SECRET}\nOPENALEX_API_KEY={OA_SECRET}\n"
                    "TARLAB_OPENALEX_MAILTO=me@example.org\n")
    out, _ = run()
    assert S2_SECRET not in out and OA_SECRET not in out
    assert "set (file, …a1b2)" in out and "set (file, …c3d4)" in out
    assert "me@example.org" in out
    assert "mode 0600" in out and "WARNING" not in out
    data = json.loads(run(as_json=True)[0])
    assert data["semantic_scholar_key"] == {"set": True, "from": "file", "name": None,
                                            "value": "…a1b2"}


def test_loose_permissions_warn(isolated: Path) -> None:
    path = isolated / "literature.env"
    _env_file(path, f"TARLAB_S2_API_KEY={S2_SECRET}\n", 0o644)
    out, _ = run()
    assert "mode 0644" in out and f"chmod 600 {path}" in out
    assert S2_SECRET not in out


def test_rate_lock_time(isolated: Path) -> None:
    rate = isolated / "rate"
    rate.mkdir()
    (rate / "s2.lock").write_text(f"{time.time() - 42:.6f}")
    (rate / "arxiv.lock").write_text("garbage")
    report = collect(LiteratureConfig.load(), now=time.time())
    limits = report["rate_limits"]
    assert 40 <= limits["semantic_scholar"]["seconds_since_last_request"] <= 50
    assert limits["arxiv"]["seconds_since_last_request"] is None
    assert limits["semantic_scholar"]["min_interval_s"] == 1.05
    assert "last request 4" in render(report)


class Fake:
    """Replies per URL host fragment: an int status, or an exception to raise."""

    def __init__(self, replies: Mapping[str, int | Exception]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, url: str, params: Mapping[str, Any],
                       headers: Mapping[str, str]) -> httpx2.Response:
        self.calls.append((url, dict(headers)))
        reply = next(v for k, v in self.replies.items() if k in url)
        if isinstance(reply, Exception):
            raise reply
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom"></feed>' if "arxiv" in url else None)
        req = httpx2.Request("GET", url)
        if feed:
            return httpx2.Response(reply, text=feed, request=req)
        return httpx2.Response(reply, json={"results": [], "data": []}, request=req)


def test_online_ok() -> None:
    fake = Fake({"openalex": 200, "semanticscholar": 200, "arxiv": 200})
    out, code = run(online=True, get=fake)
    assert code == 0 and len(fake.calls) == 3
    assert out.count("OK") == 3


def test_online_429_and_network_error_exit_codes() -> None:
    fake = Fake({"openalex": 200, "semanticscholar": 429,
                 "arxiv": httpx2.ConnectError("boom")})
    out, code = run(online=True, get=fake)
    assert code == 0  # one source worked
    assert "Semantic Scholar: HTTP 429 FAILED" in out
    assert "arXiv: FAILED" in out and "ConnectError" in out
    data = json.loads(run(online=True, as_json=True, get=fake)[0])
    assert data["online"]["semantic_scholar"]["http_status"] == 429
    assert data["online"]["openalex"]["ok"] is True

    allbad = Fake({"openalex": httpx2.ConnectError("x"), "semanticscholar": 429,
                   "arxiv": httpx2.ReadTimeout("t")})
    assert run(online=True, get=allbad)[1] == 1


def test_online_403_with_key_means_invalid_and_key_is_not_printed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_S2_API_KEY", S2_SECRET)
    fake = Fake({"openalex": 200, "semanticscholar": 403, "arxiv": 200})
    out, code = run(online=True, get=fake)
    assert code == 0
    assert "key is invalid" in out and S2_SECRET not in out
    s2_headers = next(h for u, h in fake.calls if "semanticscholar" in u)
    assert s2_headers == {"x-api-key": S2_SECRET}
    assert os.environ["TARLAB_S2_API_KEY"] == S2_SECRET
