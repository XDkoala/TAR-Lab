"""Open scholarly APIs (OpenAlex, Semantic Scholar, arXiv) behind one normalized paper record.

Used when Undermind is rate limited, out of quota, or unreachable. Every source is optional:
a failing source is reported next to the results instead of failing the whole call.
"""

from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx2

from tar_lab.integrations.undermind.http import make_http_client

OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
ARXIV = "https://export.arxiv.org/api/query"
SOURCES = ("openalex", "semantic_scholar", "arxiv")
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_S2_FIELDS = ("title,year,authors,venue,externalIds,citationCount,openAccessPdf,url,abstract,"
              "referenceCount")
_OA_SELECT = ("id,doi,display_name,publication_year,cited_by_count,open_access,authorships,"
              "primary_location,abstract_inverted_index,referenced_works_count")
ABSTRACT_CHARS = 1200


@dataclass
class Paper:
    title: str
    year: int | None = None
    authors: list[str] = field(default_factory=list)
    venue: str | None = None
    citations: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    abstract: str | None = None
    sources: list[str] = field(default_factory=list)

    def key(self) -> str:
        if self.doi and not self.doi.lower().startswith("10.48550/arxiv."):
            return "doi:" + self.doi.lower()
        if self.arxiv_id:
            return "arxiv:" + self.arxiv_id
        return "title:" + re.sub(r"[^a-z0-9]", "", self.title.lower())

    def merge(self, other: Paper) -> None:
        for name, value in asdict(other).items():
            if name == "sources":
                self.sources += [s for s in other.sources if s not in self.sources]
            elif name == "citations":
                self.citations = max(filter(None, (self.citations, other.citations)),
                                     default=self.citations or other.citations)
            elif not getattr(self, name) and value:
                setattr(self, name, value)

    def to_dict(self, abstract_chars: int = ABSTRACT_CHARS) -> dict[str, Any]:
        data = {k: v for k, v in asdict(self).items() if v not in (None, [], "")}
        if self.abstract and len(self.abstract) > abstract_chars:
            data["abstract"] = self.abstract[:abstract_chars] + "…"
        return data


class SourceError(RuntimeError):
    pass


Getter = Callable[[str, Mapping[str, Any], Mapping[str, str]], Awaitable[httpx2.Response]]


async def _http_get(url: str, params: Mapping[str, Any], headers: Mapping[str, str]
                    ) -> httpx2.Response:
    async with make_http_client(url, timeout=httpx2.Timeout(30), follow_redirects=True) as c:
        return await c.get(url, params=dict(params), headers=dict(headers))


def _env(*names: str) -> str | None:
    return next((v for n in names if (v := os.environ.get(n))), None)


def _arxiv_from(text: str | None) -> str | None:
    m = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", text or "")
    return m.group(1) if m else None


def _strip_doi(doi: str | None) -> str | None:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE) if doi else doi


def _abstract_from_index(index: Mapping[str, Sequence[int]] | None) -> str | None:
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(w for _, w in words)


def _from_openalex(w: Mapping[str, Any]) -> Paper:
    loc = w.get("primary_location") or {}
    oa = w.get("open_access") or {}
    landing = loc.get("landing_page_url") or ""
    return Paper(
        title=w.get("display_name") or "", year=w.get("publication_year"),
        authors=[a["author"]["display_name"] for a in (w.get("authorships") or [])[:6]
                 if a.get("author")],
        venue=((loc.get("source") or {}).get("display_name")),
        citations=w.get("cited_by_count"), doi=_strip_doi(w.get("doi")),
        arxiv_id=_arxiv_from(landing) if "arxiv" in landing else None,
        openalex_id=str(w.get("id", "")).rsplit("/", 1)[-1] or None,
        url=landing or w.get("doi"), pdf_url=loc.get("pdf_url") or oa.get("oa_url"),
        abstract=_abstract_from_index(w.get("abstract_inverted_index")), sources=["openalex"])


def _from_s2(p: Mapping[str, Any]) -> Paper:
    ids = p.get("externalIds") or {}
    pdf = p.get("openAccessPdf") or {}
    return Paper(
        title=p.get("title") or "", year=p.get("year"),
        authors=[a["name"] for a in (p.get("authors") or [])[:6] if a.get("name")],
        venue=p.get("venue") or None, citations=p.get("citationCount"),
        doi=ids.get("DOI"), arxiv_id=ids.get("ArXiv"), s2_id=p.get("paperId"),
        url=p.get("url"), pdf_url=pdf.get("url") or None, abstract=p.get("abstract"),
        sources=["semantic_scholar"])


def _from_arxiv(entry: ET.Element) -> Paper:
    def text(tag: str) -> str:
        node = entry.find(tag, _ATOM)
        return " ".join((node.text or "").split()) if node is not None else ""
    pdf = next((ln.get("href") for ln in entry.findall("a:link", _ATOM)
                if ln.get("title") == "pdf"), None)
    published = text("a:published")
    return Paper(
        title=text("a:title"), year=int(published[:4]) if published[:4].isdigit() else None,
        authors=[" ".join((a.findtext("a:name", "", _ATOM)).split())
                 for a in entry.findall("a:author", _ATOM)][:6],
        venue="arXiv", doi=text("arxiv:doi") or None, arxiv_id=_arxiv_from(text("a:id")),
        url=text("a:id"), pdf_url=pdf, abstract=text("a:summary"), sources=["arxiv"])


class Literature:
    """Search and look up papers across the open sources."""

    def __init__(self, get: Getter = _http_get, retries: int = 2,
                 backoff_seconds: float = 2.0) -> None:
        self._get, self.retries, self.backoff = get, retries, backoff_seconds
        self.openalex_key = _env("TARLAB_OPENALEX_API_KEY", "OPENALEX_API_KEY")
        self.openalex_mailto = _env("TARLAB_OPENALEX_MAILTO")
        self.s2_key = _env("TARLAB_S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY")

    async def _json(self, url: str, params: Mapping[str, Any],
                    headers: Mapping[str, str] | None = None) -> Any:
        resp = await self._fetch(url, params, headers)
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"invalid JSON from {url}: {resp.text[:200]}") from exc

    async def _fetch(self, url: str, params: Mapping[str, Any],
                     headers: Mapping[str, str] | None = None) -> httpx2.Response:
        for attempt in range(self.retries + 1):
            try:
                resp = await self._get(url, params, headers or {})
            except httpx2.HTTPError as exc:
                if attempt == self.retries:
                    raise SourceError(f"{type(exc).__name__}: {exc}") from exc
            else:
                if resp.status_code == 200:
                    return resp
                if resp.status_code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    hint = " (rate limited; an API key raises the limit)" \
                        if resp.status_code == 429 else ""
                    raise SourceError(f"HTTP {resp.status_code}{hint}: {resp.text[:200]}")
            await asyncio.sleep(self.backoff * (2 ** attempt))
        raise SourceError("unreachable")

    def _oa_params(self, **params: Any) -> dict[str, Any]:
        if self.openalex_key:
            params["api_key"] = self.openalex_key
        if self.openalex_mailto:
            params["mailto"] = self.openalex_mailto
        return params

    def _s2_headers(self) -> dict[str, str]:
        return {"x-api-key": self.s2_key} if self.s2_key else {}

    # ---- search ------------------------------------------------------------------------

    async def openalex_search(self, query: str, limit: int, year_from: int | None,
                              year_to: int | None) -> list[Paper]:
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        params = self._oa_params(search=query, **{"per-page": limit}, select=_OA_SELECT)
        if filters:
            params["filter"] = ",".join(filters)
        data = await self._json(f"{OPENALEX}/works", params)
        return [_from_openalex(w) for w in data.get("results", [])]

    async def s2_search(self, query: str, limit: int, year_from: int | None,
                        year_to: int | None) -> list[Paper]:
        params: dict[str, Any] = {"query": query, "limit": limit, "fields": _S2_FIELDS}
        if year_from or year_to:
            params["year"] = f"{year_from or ''}-{year_to or ''}"
        data = await self._json(f"{S2}/paper/search", params, self._s2_headers())
        return [_from_s2(p) for p in data.get("data") or []]

    async def arxiv_search(self, query: str, limit: int, year_from: int | None,
                           year_to: int | None) -> list[Paper]:
        terms = [t for t in re.findall(r"[\w\-]+", query) if len(t) > 1][:10]
        if not terms:
            return []
        q = " AND ".join(f"all:{t}" for t in terms)
        if year_from or year_to:
            q += (f" AND submittedDate:[{year_from or 1990}01010000 TO "
                  f"{year_to or 2100}12312359]")
        resp = await self._fetch(ARXIV, {"search_query": q, "max_results": limit,
                                         "sortBy": "relevance"})
        root = ET.fromstring(resp.text)
        return [_from_arxiv(e) for e in root.findall("a:entry", _ATOM)]

    async def search(self, query: str, limit: int = 10, year_from: int | None = None,
                     year_to: int | None = None, sources: Sequence[str] = SOURCES
                     ) -> dict[str, Any]:
        limit = max(1, min(limit, 50))
        runners = {"openalex": self.openalex_search, "semantic_scholar": self.s2_search,
                   "arxiv": self.arxiv_search}
        names = [s for s in sources if s in runners] or list(SOURCES)
        outcomes = await asyncio.gather(
            *(runners[n](query, limit, year_from, year_to) for n in names),
            return_exceptions=True)
        merged: dict[str, Paper] = {}
        score: dict[str, float] = {}
        status: dict[str, str] = {}
        for name, outcome in zip(names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                status[name] = f"error: {outcome}"[:300]
                continue
            status[name] = f"ok ({len(outcome)})"
            for rank, paper in enumerate(outcome):
                key = paper.key()
                if key in merged:
                    merged[key].merge(paper)
                else:
                    merged[key] = paper
                score[key] = score.get(key, 0.0) + 1.0 / (rank + 1)
        ranked = sorted(merged, key=lambda k: -score[k])[:limit]
        return {"query": query, "sources": status,
                "papers": [merged[k].to_dict() for k in ranked]}

    # ---- lookup ------------------------------------------------------------------------

    async def _openalex_work(self, identifier: str) -> dict[str, Any]:
        ident = identifier.strip()
        arxiv = (_arxiv_from(ident)
                 if re.search(r"arxiv|^\d{4}\.\d{4,5}", ident, re.IGNORECASE) else None)
        if re.match(r"^(https://openalex\.org/)?W\d+$", ident):
            path = ident.rsplit("/", 1)[-1]
        elif arxiv:
            path = f"doi:10.48550/arXiv.{arxiv}"
        elif re.match(r"^(https?://(dx\.)?doi\.org/|doi:)?10\.\d{4,9}/\S+$", ident,
                      re.IGNORECASE):
            path = "doi:" + (_strip_doi(ident) or "").removeprefix("doi:")
        else:
            data = await self._json(f"{OPENALEX}/works", self._oa_params(
                filter=f"title.search:{ident}", **{"per-page": 1}, select=_OA_SELECT + ",id"))
            if not data.get("results"):
                raise SourceError(f"no paper matches {identifier!r}")
            return dict(data["results"][0])
        return dict(await self._json(f"{OPENALEX}/works/{path}",
                                     self._oa_params(select=_OA_SELECT + ",referenced_works")))

    async def get_paper(self, identifier: str) -> dict[str, Any]:
        status: dict[str, str] = {}
        paper: Paper | None = None
        try:
            paper = _from_openalex(await self._openalex_work(identifier))
            status["openalex"] = "ok"
        except SourceError as exc:
            status["openalex"] = f"error: {exc}"[:300]
        s2_ref = None
        if paper and (paper.doi or paper.arxiv_id):
            s2_ref = f"ARXIV:{paper.arxiv_id}" if paper.arxiv_id else f"DOI:{paper.doi}"
        elif paper is None:
            arxiv = _arxiv_from(identifier)
            s2_ref = f"ARXIV:{arxiv}" if arxiv else (
                identifier if re.fullmatch(r"[0-9a-f]{40}", identifier) else
                f"DOI:{_strip_doi(identifier)}" if identifier.startswith(("10.", "http"))
                else None)
        if s2_ref:
            try:
                s2 = _from_s2(await self._json(f"{S2}/paper/{s2_ref}", {"fields": _S2_FIELDS},
                                               self._s2_headers()))
                status["semantic_scholar"] = "ok"
                if paper is None:
                    paper = s2
                else:
                    paper.merge(s2)
            except SourceError as exc:
                status["semantic_scholar"] = f"error: {exc}"[:300]
        if paper is None:
            raise SourceError(f"paper not found: {identifier!r} ({status})")
        return {"sources": status, "paper": paper.to_dict(abstract_chars=4000)}

    async def citations(self, identifier: str, direction: str = "citations",
                        limit: int = 20) -> dict[str, Any]:
        """Papers citing ``identifier`` (most cited first), or the papers it references."""
        limit = max(1, min(limit, 50))
        work = await self._openalex_work(identifier)
        wid = str(work.get("id", "")).rsplit("/", 1)[-1]
        if direction == "references":
            refs = [str(r).rsplit("/", 1)[-1] for r in work.get("referenced_works") or []]
            if not refs:
                return {"paper": work.get("display_name"), "direction": direction,
                        "papers": []}
            params = self._oa_params(filter="openalex:" + "|".join(refs[:limit]),
                                     **{"per-page": limit}, select=_OA_SELECT)
        else:
            params = self._oa_params(filter=f"cites:{wid}", sort="cited_by_count:desc",
                                     **{"per-page": limit}, select=_OA_SELECT)
        data = await self._json(f"{OPENALEX}/works", params)
        return {"paper": work.get("display_name"), "direction": direction,
                "papers": [_from_openalex(w).to_dict(400) for w in data.get("results", [])]}
