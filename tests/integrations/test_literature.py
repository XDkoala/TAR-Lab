import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import anyio
import httpx2

from tar_lab.core.ledger import Ledger
from tar_lab.integrations.literature import Literature
from tar_lab.integrations.literature.server import LiteratureServer

OA_WORK = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.48550/arxiv.2603.19312",
           "display_name": "LeWorldModel", "publication_year": 2026, "cited_by_count": 390,
           "authorships": [{"author": {"display_name": "Lucas Maes"}}],
           "primary_location": {"landing_page_url": "https://arxiv.org/abs/2603.19312",
                                "source": {"display_name": "arXiv"}},
           "open_access": {"oa_url": "https://arxiv.org/pdf/2603.19312"},
           "abstract_inverted_index": {"stable": [0], "JEPA": [1]},
           "referenced_works": ["https://openalex.org/W2"]}
OA_OTHER = {**OA_WORK, "id": "https://openalex.org/W3", "doi": "https://doi.org/10.1/x",
            "display_name": "DreamerV3", "primary_location": {}, "cited_by_count": 900}
ARXIV_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
 <id>http://arxiv.org/abs/2603.19312v2</id><title>LeWorldModel:  Stable</title>
 <published>2026-03-19T00:00:00Z</published><summary>Full abstract.</summary>
 <author><name>Lucas Maes</name></author>
 <link href="https://arxiv.org/pdf/2603.19312v2" title="pdf"/></entry></feed>"""


class FakeHttp:
    def __init__(self, s2_status: list[int] | None = None) -> None:
        self.s2_status = s2_status or [429, 429, 429]
        self.urls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, url: str, params: Mapping[str, Any], headers: Mapping[str, str]
                       ) -> httpx2.Response:
        self.urls.append((url, dict(params)))
        req = httpx2.Request("GET", url)
        if "openalex" in url and url.endswith("/works"):
            if "cites:" in str(params.get("filter")) or "openalex:" in str(params.get("filter")):
                return httpx2.Response(200, json={"results": [OA_OTHER]}, request=req)
            return httpx2.Response(200, json={"results": [OA_WORK, OA_OTHER]}, request=req)
        if "openalex" in url:
            return httpx2.Response(200, json=OA_WORK, request=req)
        if "semanticscholar" in url:
            status = self.s2_status.pop(0) if self.s2_status else 429
            if status == 200:
                return httpx2.Response(200, json={"paperId": "a" * 40, "title": "LeWorldModel",
                                                  "citationCount": 400, "tldr": None}, request=req)
            return httpx2.Response(status, text="Too Many Requests", request=req)
        return httpx2.Response(200, text=ARXIV_FEED, request=req)


def test_search_merges_sources_and_reports_failures() -> None:
    http = FakeHttp()
    lit = Literature(get=http, backoff_seconds=0)
    result = anyio.run(lit.search, "latent world model", 5)
    assert result["sources"]["openalex"] == "ok (2)" and result["sources"]["arxiv"] == "ok (1)"
    assert result["sources"]["semantic_scholar"].startswith("error: HTTP 429 (rate limited")
    first, second = result["papers"]
    assert first["title"] == "LeWorldModel" and first["arxiv_id"] == "2603.19312"
    assert first["sources"] == ["openalex", "arxiv"] and first["abstract"] == "stable JEPA"
    assert second["title"] == "DreamerV3"
    assert sum("semanticscholar" in u for u, _ in http.urls) == 3  # retried twice


def test_lookup_and_citations_resolve_arxiv_ids() -> None:
    http = FakeHttp([200])
    lit = Literature(get=http, backoff_seconds=0)
    paper = anyio.run(lit.get_paper, "arXiv:2603.19312")
    assert paper["paper"]["title"] == "LeWorldModel" and paper["paper"]["citations"] == 400
    assert paper["sources"] == {"openalex": "ok", "semantic_scholar": "ok"}
    assert any(u.endswith("/works/doi:10.48550/arXiv.2603.19312") for u, _ in http.urls)
    refs = anyio.run(lit.citations, "2603.19312", "references", 5)
    assert [p["title"] for p in refs["papers"]] == ["DreamerV3"]
    assert any(p.get("filter") == "openalex:W2" for _, p in http.urls)


def test_server_ledgers_calls(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    server = LiteratureServer(ledger, "o1-1-researcher",
                              Literature(get=FakeHttp(), backoff_seconds=0))
    ok = anyio.run(server.call, "search_papers", {"query": "world model", "limit": 3})
    bad = anyio.run(server.call, "get_paper", {})
    assert not ok.is_error and json.loads(ok.content[0].text)["papers"]  # type: ignore[union-attr]
    assert bad.is_error
    calls = [e for e in ledger.events() if e.kind == "literature.call"]
    assert [c.payload["is_error"] for c in calls] == [False, True]
    assert calls[0].payload["results"] == 2 and calls[0].order_id == "o1-1-researcher"
