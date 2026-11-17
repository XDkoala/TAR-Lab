"""Local stdio MCP server that gives an agent project-scoped access to Undermind.

The agent never talks to Undermind directly. The proxy exposes only tools the policy
allows, pins every call to the project's own workspace (``workspace_id`` is removed from the
schemas the agent sees), counts deep searches against the charter quota, and records each
call in the project ledger in addition to the client's own audit log.

Rate-limited calls are retried with backoff. When Undermind stays rate limited, runs out of
quota, or cannot be reached, the proxy records ``undermind.degraded`` and tells the agent to
switch to the ``literature`` server (OpenAlex, Semantic Scholar, arXiv); after a quota error it
stops calling Undermind for a cooling-off period.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
RETRY_DELAYS = (5.0, 20.0, 60.0)
QUOTA_COOLDOWN = timedelta(hours=1)
_RATE = re.compile(r"rate[_ -]?limit|too many requests|\b429\b", re.IGNORECASE)
_QUOTA = re.compile(r"quota|credits?\b|usage limit|limit (?:reached|exceeded)|upgrade|"
                    r"payment required|\b402\b|out of (?:searches|credits)|plan limit",
                    re.IGNORECASE)
FALLBACK = ("Switch to the `literature` MCP server (search_papers, get_paper, find_citations "
            "over OpenAlex, Semantic Scholar and arXiv) and state in your report which sources "
            "you used instead of Undermind.")


def _result_text(result: types.CallToolResult) -> str:
    return " ".join(c.text for c in result.content if isinstance(c, types.TextContent))


def classify(result: types.CallToolResult) -> str:
    """``ok``, ``rate_limited``, ``quota`` or ``error`` for one Undermind tool result."""
    if not result.is_error:
        return "ok"
    text = _result_text(result)
    if _RATE.search(text):
        return "rate_limited"
    return "quota" if _QUOTA.search(text) else "error"


def degraded_until(ledger: Ledger, now: datetime | None = None) -> datetime | None:
    """End of the cooling-off period after the latest quota error, if still running."""
    quota = [e for e in ledger.events() if e.kind == "undermind.degraded"
             and e.payload.get("reason") == "quota"]
    if not quota:
        return None
    until = quota[-1].ts + QUOTA_COOLDOWN
    return until if until > (now or datetime.now(UTC)) else None


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


@dataclass
class UndermindProxy:
    client: UndermindClient
    scope: ProxyScope
    retry_delays: Sequence[float] = RETRY_DELAYS
    ledger: Ledger = field(init=False)
    _pinned: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.ledger = Ledger(self.scope.ledger_path)

    def _degrade(self, reason: str, tool: str, detail: str) -> types.CallToolResult:
        self.ledger.append("undermind.degraded", self.scope.order_id,
                           {"reason": reason, "tool": tool, "detail": detail[:500]})
        label = {"quota": "out of quota", "rate_limited": "rate limited after retries",
                 "unavailable": "unreachable"}[reason]
        return _error(f"Undermind is {label}: {detail[:300]}\n{FALLBACK}")

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
        until = degraded_until(self.ledger)
        if until is not None:
            return _error(f"Undermind ran out of quota; TAR-Lab retries it after "
                          f"{until:%H:%M} UTC.\n{FALLBACK}")
        args = dict(arguments)
        if name in self._pinned:
            args[WORKSPACE_ARG] = self.scope.workspace_id
        started = time.monotonic()
        record: dict[str, Any] = {"tool": name, "arguments": arguments}
        outcome = "error"
        try:
            for attempt in range(len(self.retry_delays) + 1):
                result = await self.client.call(name, args)
                outcome = classify(result)
                if outcome != "rate_limited" or attempt == len(self.retry_delays):
                    break
                record["retries"] = attempt + 1
                await anyio.sleep(self.retry_delays[attempt])
        except ToolNotPermittedError as exc:
            record["error"] = str(exc)
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - transport failures become a fallback, not a crash
            record.update(is_error=True, outcome="unavailable", error=f"{type(exc).__name__}: "
                          f"{exc}"[:500], duration_s=round(time.monotonic() - started, 3))
            self.ledger.append("undermind.call", self.scope.order_id, record)
            return self._degrade("unavailable", name, record["error"])
        record.update(is_error=bool(result.is_error), outcome=outcome,
                      duration_s=round(time.monotonic() - started, 3))
        self.ledger.append("undermind.call", self.scope.order_id, record)
        if outcome in ("quota", "rate_limited"):
            return self._degrade(outcome, name, _result_text(result))
        return result


def proxy_policy(scope: ProxyScope) -> ToolPolicy:
    allow = frozenset({DEEP_SEARCH}) if scope.max_deep_searches > 0 else frozenset()
    return ToolPolicy(mode="read_write", allow=allow,
                      deny=frozenset({"write_file", "delete_file", "delete_folder",
                                      "delete_deep_search"}))


async def _serve(on_list: Any, on_call: Any, instructions: str) -> None:
    server: Server[Any] = Server("tar-lab-undermind", on_list_tools=on_list,
                                 on_call_tool=on_call, instructions=instructions)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def serve_unavailable(scope: ProxyScope, detail: str) -> None:
    """Undermind cannot be reached: answer every call with the fallback instructions."""
    ledger = Ledger(scope.ledger_path)
    ledger.append("undermind.degraded", scope.order_id,
                  {"reason": "unavailable", "tool": "connect", "detail": detail[:500]})
    ledger.close()
    message = f"Undermind is unreachable: {detail[:300]}\n{FALLBACK}"
    tool = types.Tool(name="get_orientation", description=message,
                      input_schema={"type": "object", "properties": {}})

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool])

    async def on_call_tool(_ctx: Any, _params: Any) -> types.CallToolResult:
        return _error(message)

    await _serve(on_list_tools, on_call_tool, message)


async def serve(scope: ProxyScope, settings: UndermindSettings | None = None) -> None:
    try:
        client = await UndermindClient(settings, policy=proxy_policy(scope)).__aenter__()
    except Exception as exc:  # noqa: BLE001 - login, network or server failure
        await serve_unavailable(scope, f"{type(exc).__name__}: {exc}")
        return
    try:
        proxy = UndermindProxy(client, scope)

        async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
            return types.ListToolsResult(tools=await proxy.exposed())

        async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams
                               ) -> types.CallToolResult:
            return await proxy.call(params.name, dict(params.arguments or {}))

        await _serve(on_list_tools, on_call_tool,
                     "Undermind literature search, scoped to this research project's "
                     "workspace. Call get_orientation first. If it reports a quota, rate limit "
                     "or outage, use the `literature` server instead.")
    finally:
        await client.__aexit__(None, None, None)


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
