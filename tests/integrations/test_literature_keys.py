import email.utils
import itertools
import multiprocessing
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest

from tar_lab.integrations.literature import (
    Literature,
    LiteratureConfig,
    RateLimiter,
    literature_env_path,
)
from tar_lab.integrations.literature import sources as sources_mod

SECRET = "s2-secret-value-123"


def _write_env(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_key_file_parsing_and_env_precedence(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_env(tmp_path / "literature.env", (
        "# Semantic Scholar\n"
        f"SEMANTIC_SCHOLAR_API_KEY='{SECRET}'\n"
        "  TARLAB_OPENALEX_API_KEY = \"oa-file\"  \n"
        "TARLAB_OPENALEX_MAILTO=me@example.org\n"
        "UNRELATED=ignored\n"
        "not a setting\n"))
    cfg = LiteratureConfig.load(path, environ={})
    assert cfg.get("TARLAB_S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY") == SECRET
    assert cfg.get("TARLAB_OPENALEX_API_KEY") == "oa-file"
    assert cfg.get("UNRELATED") is None
    assert cfg.key_status() == {"semantic_scholar": "file", "openalex": "file"}

    # Any environment alias beats the file, even a lower-priority alias name.
    cfg = LiteratureConfig.load(path, environ={"OPENALEX_API_KEY": "oa-env",
                                               "TARLAB_S2_API_KEY": ""})
    assert cfg.get("TARLAB_OPENALEX_API_KEY", "OPENALEX_API_KEY") == "oa-env"
    assert cfg.lookup("TARLAB_S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY") == (SECRET, "file")
    assert cfg.key_status()["openalex"] == "env"

    lit = Literature(get=_unused_get, config=cfg)
    assert lit.s2_key == SECRET and lit._s2_headers() == {"x-api-key": SECRET}
    assert lit.openalex_mailto == "me@example.org"
    assert SECRET not in capsys.readouterr().err

    missing = LiteratureConfig.load(tmp_path / "absent.env", environ={})
    assert missing.key_status() == {"semantic_scholar": "no", "openalex": "no"}


def test_group_readable_key_file_warns_without_leaking(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_env(tmp_path / "loose.env", f"TARLAB_S2_API_KEY={SECRET}\n", 0o644)
    cfg = LiteratureConfig.load(path, environ={})
    assert cfg.get("TARLAB_S2_API_KEY") == SECRET  # warn, don't refuse
    err = capsys.readouterr().err
    assert "chmod 600" in err and SECRET not in err


def test_key_file_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TARLAB_LITERATURE_ENV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert literature_env_path() == tmp_path / "xdg" / "tar-lab" / "literature.env"
    monkeypatch.setenv("TARLAB_LITERATURE_ENV", str(tmp_path / "custom.env"))
    assert literature_env_path() == tmp_path / "custom.env"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.delenv("TARLAB_LITERATURE_ENV")
    assert literature_env_path() == Path.home() / ".config" / "tar-lab" / "literature.env"


async def _unused_get(url: str, params: Mapping[str, Any], headers: Mapping[str, str]
                      ) -> httpx2.Response:
    raise AssertionError("no request expected")


class Scripted:
    """Replies with the given (status, headers) in order, then 200."""

    def __init__(self, replies: list[tuple[int, dict[str, str]]]) -> None:
        self.replies, self.calls = list(replies), 0

    async def __call__(self, url: str, params: Mapping[str, Any], headers: Mapping[str, str]
                       ) -> httpx2.Response:
        self.calls += 1
        status, hdrs = self.replies.pop(0) if self.replies else (200, {})
        return httpx2.Response(status, headers=hdrs, json={"data": []},
                               request=httpx2.Request("GET", url))


@pytest.mark.parametrize(("status", "header", "expected"), [
    (429, "7", (7.0, 7.0)),
    (503, "2.5", (2.5, 2.5)),
    (429, "999", (60.0, 60.0)),             # capped
    (429, None, (1.0, 1.0)),                # no header: exponential backoff (1 * 2**0)
    (429, "soon", (1.0, 1.0)),              # unparseable: backoff
    (500, "7", (1.0, 1.0)),                 # only honoured on 429/503
    (429, "date+30", (25.0, 30.0)),         # HTTP date
    (429, "date-30", (0.0, 0.0)),           # date in the past
])
def test_retry_after(monkeypatch: pytest.MonkeyPatch, status: int, header: str | None,
                     expected: tuple[float, float]) -> None:
    if header and header.startswith("date"):
        header = email.utils.formatdate(time.time() + float(header[4:]), usegmt=True)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(sources_mod.asyncio, "sleep", fake_sleep)
    http = Scripted([(status, {"Retry-After": header} if header else {})])
    lit = Literature(get=http, backoff_seconds=1.0, config=LiteratureConfig({}, {}))
    result = anyio.run(lit.s2_search, "q", 1, None, None)
    assert result == [] and http.calls == 2
    assert len(sleeps) == 1 and expected[0] <= sleeps[0] <= expected[1]


class CountingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__("test", 0)
        self.calls = 0

    async def wait(self) -> float:
        self.calls += 1
        return time.time()


def test_limiter_paces_only_its_source() -> None:
    s2, arxiv = CountingLimiter(), CountingLimiter()
    http = Scripted([(429, {"Retry-After": "0"})])
    lit = Literature(get=http, backoff_seconds=0, config=LiteratureConfig({}, {}),
                     limiters={"semantic_scholar": s2, "arxiv": arxiv})
    anyio.run(lit.s2_search, "q", 1, None, None)
    anyio.run(lit.openalex_search, "q", 1, None, None)
    assert s2.calls == 2 and arxiv.calls == 0  # every attempt, retries included


def test_default_limiters_only_for_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = LiteratureConfig({"TARLAB_S2_MIN_INTERVAL": "0.4"}, {})
    assert Literature(get=_unused_get, config=cfg).limiters == {}
    lit = Literature(config=cfg)
    assert lit.limiters["semantic_scholar"].min_interval == 0.4
    assert lit.limiters["arxiv"].min_interval == 3.0


INTERVAL = 0.2
PER_PROCESS = 3


def _hammer(state_dir: str, out: Any) -> None:
    limiter = RateLimiter("s2", INTERVAL, Path(state_dir))
    for _ in range(PER_PROCESS):
        out.put((os.getpid(), anyio.run(limiter.wait)))


def test_limiter_spaces_requests_across_processes(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_hammer, args=(str(tmp_path), out)) for _ in range(2)]
    for p in procs:
        p.start()
    stamps = sorted(out.get(timeout=30) for _ in range(2 * PER_PROCESS))
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0
    times = sorted(t for _, t in stamps)
    assert len({pid for pid, _ in stamps}) == 2
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert min(gaps) >= INTERVAL - 0.01, gaps
    assert (tmp_path / "s2.lock").stat().st_mode & 0o077 == 0
