"""Policy-checked, audited Undermind MCP client."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from .auth import LoginRequiredError, UndermindOAuthProvider
from .http import make_http_client
from .policy import ToolDecision, ToolPolicy
from .settings import UndermindSettings
from .storage import FileTokenStorage


class ToolNotPermittedError(PermissionError):
    pass


def _raise_login_required(exc: BaseException) -> None:
    """Surface a ``LoginRequiredError`` buried in the transport's exception groups."""
    if isinstance(exc, BaseExceptionGroup):
        match = exc.subgroup(LoginRequiredError)
        while isinstance(match, BaseExceptionGroup):
            match = match.exceptions[0]  # type: ignore[assignment]
        if match is not None:
            raise match from exc


@dataclass(frozen=True)
class ToolInfo:
    tool: Tool
    decision: ToolDecision


class UndermindClient:
    """Async context manager around an MCP session with Undermind.

    Every tool call is checked against ``policy`` and appended to the audit
    log. ``interactive=False`` (the default for agents) never opens a browser:
    missing or revoked credentials raise ``LoginRequiredError``.
    """

    def __init__(
        self,
        settings: UndermindSettings | None = None,
        *,
        policy: ToolPolicy | None = None,
        interactive: bool = False,
        open_browser: bool = True,
        connect: Callable[[], Client] | None = None,
    ) -> None:
        self.settings = settings or UndermindSettings.from_env()
        self.policy = policy or ToolPolicy()
        self._interactive = interactive
        self._open_browser = open_browser
        self._connect = connect
        self._http: httpx2.AsyncClient | None = None
        self._client: Client | None = None
        self._tools: dict[str, Tool] | None = None

    async def __aenter__(self) -> Self:
        if self._connect is not None:
            client = self._connect()
        else:
            storage = FileTokenStorage(self.settings.credentials_path)
            auth = UndermindOAuthProvider(
                self.settings, storage, interactive=self._interactive,
                open_browser=self._open_browser,
            )
            self._http = make_http_client(
                self.settings.server_url, auth=auth, timeout=httpx2.Timeout(30, read=300)
            )
            client = Client(streamable_http_client(self.settings.server_url, http_client=self._http))
        try:
            self._client = await client.__aenter__()
        except BaseException as exc:
            if self._http is not None:
                await self._http.aclose()
            _raise_login_required(exc)
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._client is not None:
                await self._client.__aexit__(exc_type, exc, tb)
        finally:
            if self._http is not None:
                await self._http.aclose()

    @property
    def _session(self) -> Client:
        if self._client is None:
            raise RuntimeError("UndermindClient is not connected; use `async with`")
        return self._client

    async def tools(self) -> list[ToolInfo]:
        """All server tools with this client's policy decision for each."""
        if self._tools is None:
            listed: list[Tool] = []
            cursor: str | None = None
            while True:
                page = await self._session.list_tools(cursor=cursor)
                listed.extend(page.tools)
                cursor = page.next_cursor
                if not cursor:
                    break
            self._tools = {tool.name: tool for tool in listed}
        return [ToolInfo(tool, self.policy.decide(tool)) for tool in self._tools.values()]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        infos = {info.tool.name: info for info in await self.tools()}
        info = infos.get(name)
        decision = info.decision if info else ToolDecision(False, "unknown tool")
        record: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(), "tool": name,
            "arguments": arguments or {}, "allowed": decision.allowed, "reason": decision.reason,
        }
        if not decision.allowed:
            self._audit(record)
            raise ToolNotPermittedError(f"Undermind tool {name!r} denied: {decision.reason}")
        started = time.monotonic()
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            _raise_login_required(exc)
            raise
        else:
            record["is_error"] = bool(result.is_error)
            return result
        finally:
            record["duration_s"] = round(time.monotonic() - started, 3)
            self._audit(record)

    def _audit(self, record: dict[str, Any]) -> None:
        path: Path = self.settings.audit_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
