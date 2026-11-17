from pathlib import Path
from typing import Any

import anyio
from mcp import types

from tar_lab.core.ledger import Ledger
from tar_lab.core.workorder import McpServer
from tar_lab.integrations.cli_agents.codex import mcp_config
from tar_lab.integrations.undermind.client import ToolInfo
from tar_lab.integrations.undermind.proxy import (
    DEEP_SEARCH,
    ProxyScope,
    UndermindProxy,
    create_project_workspace,
    proxy_policy,
)

WS_SCHEMA = {"type": "object", "properties": {"workspace_id": {"type": "string"},
                                               "sample_abstract": {"type": "string"}},
             "required": ["workspace_id", "sample_abstract"]}


def tool(name: str, read_only: bool | None = None, destructive: bool | None = None,
         schema: dict[str, Any] | None = None) -> types.Tool:
    return types.Tool(name=name, input_schema=schema or {"type": "object", "properties": {}},
                      annotations=types.ToolAnnotations(read_only_hint=read_only,
                                                        destructive_hint=destructive))


TOOLS = [tool("get_orientation", read_only=True), tool("list_workspaces", read_only=True),
         tool("search_papers", read_only=True, schema=WS_SCHEMA),
         tool(DEEP_SEARCH, destructive=True, schema=WS_SCHEMA),
         tool("delete_file", destructive=True)]


class FakeClient:
    def __init__(self, scope: ProxyScope) -> None:
        self.policy = proxy_policy(scope)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def tools(self) -> list[ToolInfo]:
        return [ToolInfo(t, self.policy.decide(t)) for t in TOOLS]

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        self.calls.append((name, arguments))
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])


def make(tmp_path: Path, quota: int = 1, workspace: str | None = "ws-123"
         ) -> tuple[UndermindProxy, FakeClient]:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    scope = ProxyScope(ledger.path, "o4-1-researcher", workspace, quota)
    client = FakeClient(scope)
    return UndermindProxy(client, scope), client  # type: ignore[arg-type]


def test_exposes_allowed_tools_and_hides_workspace_argument(tmp_path: Path) -> None:
    proxy, _ = make(tmp_path)
    tools = {t.name: t for t in anyio.run(proxy.exposed)}
    assert set(tools) == {"get_orientation", "search_papers", DEEP_SEARCH}
    assert "workspace_id" not in tools["search_papers"].input_schema["properties"]
    assert tools["search_papers"].input_schema["required"] == ["sample_abstract"]


def test_calls_are_pinned_recorded_and_deep_search_is_rationed(tmp_path: Path) -> None:
    proxy, client = make(tmp_path)

    async def scenario() -> list[types.CallToolResult]:
        return [await proxy.call("search_papers", {"sample_abstract": "x",
                                                   "workspace_id": "users-other"}),
                await proxy.call(DEEP_SEARCH, {"sample_abstract": "y"}),
                await proxy.call(DEEP_SEARCH, {"sample_abstract": "z"}),
                await proxy.call("delete_file", {})]

    first, deep, over, denied = anyio.run(scenario)
    assert client.calls[0] == ("search_papers", {"sample_abstract": "x",
                                                 "workspace_id": "ws-123"})
    assert not first.is_error and not deep.is_error
    assert over.is_error and "quota exhausted" in over.content[0].text  # type: ignore[union-attr]
    assert denied.is_error and len(client.calls) == 2
    events = [e for e in proxy.ledger.events() if e.kind == "undermind.call"]
    assert [e.payload["tool"] for e in events] == ["search_papers", DEEP_SEARCH]
    assert events[0].order_id == "o4-1-researcher"


def test_without_workspace_scoped_tools_are_hidden(tmp_path: Path) -> None:
    proxy, _ = make(tmp_path, quota=0, workspace=None)
    assert {t.name for t in anyio.run(proxy.exposed)} == {"get_orientation"}


def test_scope_from_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("project.charter", None, {"charter": {"max_deep_searches": 2}})
    ledger.append("undermind.workspace", None, {"workspace_id": "ws-9", "title": "t"})
    scope = ProxyScope.from_ledger(ledger.path, "o1")
    assert (scope.workspace_id, scope.max_deep_searches) == ("ws-9", 2)


def test_create_project_workspace_is_idempotent(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("undermind.workspace", None, {"workspace_id": "ws-1", "title": "t"})
    assert anyio.run(create_project_workspace, ledger, "ignored") == "ws-1"


def test_codex_mcp_config_flags() -> None:
    flags = mcp_config({"undermind": McpServer("/py", ("-m", "x"), {"HOME": "/h"})})
    assert flags[1] == 'mcp_servers.undermind.command="/py"'
    assert flags[3] == 'mcp_servers.undermind.args=["-m", "x"]'
    assert flags[5] == 'mcp_servers.undermind.env={HOME = "/h"}'
