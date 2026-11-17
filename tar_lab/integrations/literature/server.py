"""Local stdio MCP server ``literature``: open scholarly sources for agents, ledgered."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from tar_lab.core.ledger import Ledger
from tar_lab.integrations.undermind.proxy import RESOURCE_HANDLERS

from .sources import SOURCES, Literature, SourceError

_ID = {"type": "string", "description": "DOI, arXiv id (e.g. 2603.19312), OpenAlex id "
       "(W...), Semantic Scholar paper id, or an exact title."}
TOOLS = [
    types.Tool(
        name="search_papers",
        description=("Search OpenAlex, Semantic Scholar and arXiv at once; results are merged "
                     "and de-duplicated, with abstract, DOI/arXiv id, citations and an "
                     "open-access PDF link when known. `sources` reports each source's status."),
        input_schema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
            "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)}},
        }}),
    types.Tool(
        name="get_paper",
        description="Full metadata and abstract of one paper.",
        input_schema={"type": "object", "required": ["identifier"],
                      "properties": {"identifier": _ID}}),
    types.Tool(
        name="find_citations",
        description=("Papers citing a paper (most cited first), or the papers it references "
                     "(OpenAlex)."),
        input_schema={"type": "object", "required": ["identifier"], "properties": {
            "identifier": _ID,
            "direction": {"type": "string", "enum": ["citations", "references"],
                          "default": "citations"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        }}),
]


def _text(data: Any, is_error: bool = False) -> types.CallToolResult:
    body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return types.CallToolResult(content=[types.TextContent(type="text", text=body)],
                                is_error=is_error)


class LiteratureServer:
    def __init__(self, ledger: Ledger | None, order_id: str | None,
                 literature: Literature | None = None) -> None:
        self.ledger, self.order_id = ledger, order_id
        self.lit = literature or Literature()

    async def call(self, name: str, args: dict[str, Any]) -> types.CallToolResult:
        started = time.monotonic()
        record: dict[str, Any] = {"tool": name, "arguments": args}
        try:
            if name == "search_papers":
                data = await self.lit.search(
                    str(args["query"]), int(args.get("limit", 10)), args.get("year_from"),
                    args.get("year_to"), args.get("sources") or SOURCES)
                record["sources"], record["results"] = data["sources"], len(data["papers"])
            elif name == "get_paper":
                data = await self.lit.get_paper(str(args["identifier"]))
                record["sources"] = data["sources"]
            elif name == "find_citations":
                data = await self.lit.citations(str(args["identifier"]),
                                                str(args.get("direction", "citations")),
                                                int(args.get("limit", 20)))
                record["results"] = len(data["papers"])
            else:
                return _text(f"Unknown tool {name!r}.", True)
            record["is_error"] = False
            return _text(data)
        except (SourceError, KeyError, ValueError, TypeError) as exc:
            record["is_error"], record["error"] = True, str(exc)[:500]
            return _text(f"{name} failed: {exc}", True)
        finally:
            record["duration_s"] = round(time.monotonic() - started, 3)
            if self.ledger is not None:
                self.ledger.append("literature.call", self.order_id, record)


async def serve(ledger_path: Path | None, order_id: str | None) -> None:
    ledger = Ledger(ledger_path) if ledger_path else None
    server_impl = LiteratureServer(ledger, order_id)

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=TOOLS)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams
                           ) -> types.CallToolResult:
        return await server_impl.call(params.name, dict(params.arguments or {}))

    server: Server[Any] = Server(
        "tar-lab-literature", on_list_tools=on_list_tools, on_call_tool=on_call_tool,
        **RESOURCE_HANDLERS,
        instructions=("Open literature sources (OpenAlex, Semantic Scholar, arXiv). Use when "
                      "Undermind is rate limited, out of quota or unavailable, or to "
                      "cross-check it. Download PDFs yourself from `pdf_url`."))
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        if ledger is not None:
            ledger.close()


def run_stdio(ledger_path: Path | None, order_id: str | None) -> None:
    anyio.run(serve, ledger_path, order_id)
