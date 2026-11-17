"""Local stdio MCP server that gives an agent project-scoped access to Undermind.

The agent never talks to Undermind directly. The proxy exposes only tools the policy
allows, pins every call to the project's own workspace (``workspace_id`` is removed from the
schemas the agent sees), counts deep searches against the charter quota, and records each
call in the project ledger in addition to the client's own audit log.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from tar_lab.core.ledger import Ledger

from .client import ToolNotPermittedError, UndermindClient
from .policy import ToolPolicy
from .settings import UndermindSettings

DEEP_SEARCH = "launch_deep_search"
# Agents work in the project's workspace only and must not discover the user's others.
HIDDEN = frozenset({"list_workspaces", "create_workspace"})
WORKSPACE_ARG = "workspace_id"


@dataclass(frozen=True)
class ProxyScope:
    ledger_path: Path
    order_id: str | None
    workspace_id: str | None
    max_deep_searches: int

    @classmethod
    def from_ledger(cls, ledger_path: Path, order_id: str | None) -> ProxyScope:
        ledger = Ledger(ledger_path)
        try:
            ws = [e for e in ledger.events() if e.kind == "undermind.workspace"]
            charters = [e for e in ledger.events() if e.kind == "project.charter"]
        finally:
            ledger.close()
        quota = charters[-1].payload["charter"].get("max_deep_searches", 0) if charters else 0
        return cls(ledger_path, order_id, ws[-1].payload["workspace_id"] if ws else None,
                   int(quota))


def deep_searches_used(ledger: Ledger) -> int:
    return sum(1 for e in ledger.events() if e.kind == "undermind.call"
               and e.payload["tool"] == DEEP_SEARCH and not e.payload.get("is_error"))


def _scoped_schema(schema: dict[str, Any]) -> dict[str, Any]:
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k != WORKSPACE_ARG}
    required = [r for r in schema.get("required") or [] if r != WORKSPACE_ARG]
    return {**schema, "properties": props, "required": required}


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                is_error=True)


class UndermindProxy:
    def __init__(self, client: UndermindClient, scope: ProxyScope) -> None:
        self.client = client
        self.scope = scope
        self.ledger = Ledger(scope.ledger_path)
        self._pinned: set[str] = set()

    async def exposed(self) -> list[types.Tool]:
        tools = []
        for info in await self.client.tools():
            if not info.decision.allowed or info.tool.name in HIDDEN:
                continue
            schema = dict(info.tool.input_schema or {})
            if WORKSPACE_ARG in (schema.get("properties") or {}):
                if self.scope.workspace_id is None:
                    continue  # workspace-scoped tools need the project workspace
                self._pinned.add(info.tool.name)
                schema = _scoped_schema(schema)
            tools.append(info.tool.model_copy(update={"input_schema": schema}))
        return tools

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if name not in {t.name for t in await self.exposed()}:
            return _error(f"Tool {name!r} is not available to this project.")
        if name == DEEP_SEARCH and deep_searches_used(self.ledger) >= self.scope.max_deep_searches:
            return _error(f"Deep-search quota exhausted ({self.scope.max_deep_searches} per "
                          "project). Use search_papers instead.")
        args = dict(arguments)
        if name in self._pinned:
            args[WORKSPACE_ARG] = self.scope.workspace_id
        started = time.monotonic()
        record: dict[str, Any] = {"tool": name, "arguments": arguments}
        try:
            result = await self.client.call(name, args)
        except ToolNotPermittedError as exc:
            record["error"] = str(exc)
            return _error(str(exc))
        finally:
            record["duration_s"] = round(time.monotonic() - started, 3)
        record["is_error"] = bool(result.is_error)
        self.ledger.append("undermind.call", self.scope.order_id, record)
        return result


def proxy_policy(scope: ProxyScope) -> ToolPolicy:
    allow = frozenset({DEEP_SEARCH}) if scope.max_deep_searches > 0 else frozenset()
    return ToolPolicy(mode="read_write", allow=allow,
                      deny=frozenset({"write_file", "delete_file", "delete_folder",
                                      "delete_deep_search"}))


async def serve(scope: ProxyScope, settings: UndermindSettings | None = None) -> None:
    async with UndermindClient(settings, policy=proxy_policy(scope)) as client:
        proxy = UndermindProxy(client, scope)

        async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
            return types.ListToolsResult(tools=await proxy.exposed())

        async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams
                               ) -> types.CallToolResult:
            return await proxy.call(params.name, dict(params.arguments or {}))

        server: Server[Any] = Server(
            "tar-lab-undermind", on_list_tools=on_list_tools, on_call_tool=on_call_tool,
            instructions=("Undermind literature search, scoped to this research project's "
                          "workspace. Call get_orientation first."))
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


_WORKSPACE_ID = re.compile(r"workspace_id\W+([A-Za-z0-9_-]{6,})")


async def create_project_workspace(ledger: Ledger, title: str,
                                   settings: UndermindSettings | None = None) -> str:
    """Create the project's own Undermind workspace once and record it in the ledger."""
    existing = [e for e in ledger.events() if e.kind == "undermind.workspace"]
    if existing:
        return str(existing[-1].payload["workspace_id"])
    policy = ToolPolicy(allow=frozenset({"create_workspace"}))
    async with UndermindClient(settings, policy=policy) as client:
        result = await client.call("create_workspace", {"title": title})
    data = result.structured_content or {}
    text = " ".join(c.text for c in result.content if isinstance(c, types.TextContent))
    match = _WORKSPACE_ID.search(text)
    workspace_id = data.get(WORKSPACE_ARG) or (match.group(1) if match else None)
    if result.is_error or not workspace_id:
        raise RuntimeError(f"create_workspace failed: {text[:500]}")
    ledger.append("undermind.workspace", None, {"workspace_id": workspace_id, "title": title})
    return str(workspace_id)


def run_stdio(ledger_path: Path, order_id: str | None) -> None:
    anyio.run(serve, ProxyScope.from_ledger(ledger_path, order_id))
