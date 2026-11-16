import json
import socket
import stat
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import TextContent, Tool, ToolAnnotations

from tar_lab.integrations.undermind import (
    FileTokenStorage,
    LoginRequiredError,
    ToolNotPermittedError,
    ToolPolicy,
    UndermindClient,
    UndermindSettings,
)
from tar_lab.integrations.undermind.auth import LoopbackReceiver, UndermindOAuthProvider

SERVER = "https://mcp.undermind.ai/mcp"
PRM = {"resource": SERVER, "authorization_servers": ["https://api.undermind.ai/"],
       "scopes_supported": ["mcp"]}
ASM = {
    "issuer": "https://api.undermind.ai/",
    "authorization_endpoint": "https://api.undermind.ai/o/authorize/",
    "token_endpoint": "https://api.undermind.ai/o/token/",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "code_challenge_methods_supported": ["S256"],
    "token_endpoint_auth_methods_supported": ["none"],
    "client_id_metadata_document_supported": True,
}


def _settings(tmp_path: Path, **kwargs: Any) -> UndermindSettings:
    return UndermindSettings(
        credentials_path=tmp_path / "cfg" / "credentials.json",
        audit_path=tmp_path / "cfg" / "audit.jsonl", **kwargs,
    )


def _tool(name: str, **hints: bool) -> Tool:
    annotations = ToolAnnotations.model_validate(hints) if hints else None
    return Tool(name=name, input_schema={"type": "object"}, annotations=annotations)


@pytest.mark.parametrize(
    ("policy", "tool", "allowed"),
    [
        (ToolPolicy(), _tool("search", read_only_hint=True), True),
        (ToolPolicy(), _tool("create_folder", destructive_hint=False), False),
        (ToolPolicy(mode="read_write"), _tool("create_folder", destructive_hint=False), True),
        (ToolPolicy(mode="read_write"), _tool("delete_folder", destructive_hint=False), False),
        (ToolPolicy(mode="read_write"), _tool("drop", destructive_hint=True), False),
        (ToolPolicy(mode="read_write"), _tool("unannotated"), False),
        (ToolPolicy(allow=frozenset({"unannotated"})), _tool("unannotated"), True),
        (ToolPolicy(deny=frozenset({"search"})), _tool("search", read_only_hint=True), False),
    ],
)
def test_tool_policy(policy: ToolPolicy, tool: Tool, allowed: bool) -> None:
    assert policy.decide(tool).allowed is allowed


def test_storage_roundtrip_is_owner_only(tmp_path: Path) -> None:
    storage = FileTokenStorage(tmp_path / "cfg" / "credentials.json")

    async def scenario() -> None:
        assert await storage.get_tokens() is None
        await storage.set_tokens(OAuthToken(access_token="a", refresh_token="r", expires_in=3600))
        await storage.set_client_info(OAuthClientInformationFull(client_id="cid"))
        tokens = await storage.get_tokens()
        info = await storage.get_client_info()
        assert tokens is not None and tokens.refresh_token == "r"
        assert info is not None and info.client_id == "cid"

    anyio.run(scenario)
    assert stat.S_IMODE(storage.path.stat().st_mode) == 0o600
    expires_at = storage.expires_at()
    assert expires_at is not None and 3500 < expires_at - time.time() <= 3600
    assert storage.clear() and not storage.clear()


def _server() -> MCPServer:
    server = MCPServer("fake-undermind")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def search(query: str) -> str:
        return f"papers about {query}"

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_folder(folder_id: str) -> str:
        return "deleted"

    return server


def test_client_enforces_policy_and_audits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server = _server()

    async def scenario() -> None:
        async with UndermindClient(settings, connect=lambda: Client(server)) as client:
            decisions = {i.tool.name: i.decision.allowed for i in await client.tools()}
            assert decisions == {"search": True, "delete_folder": False}
            result = await client.call("search", {"query": "circle packing"})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "papers about circle packing"
            with pytest.raises(ToolNotPermittedError, match="destructive"):
                await client.call("delete_folder", {"folder_id": "f1"})
            with pytest.raises(ToolNotPermittedError, match="unknown tool"):
                await client.call("nope")

    anyio.run(scenario)
    records = [json.loads(line) for line in settings.audit_path.read_text().splitlines()]
    assert [(r["tool"], r["allowed"]) for r in records] == [
        ("search", True), ("delete_folder", False), ("nope", False)
    ]
    assert records[0]["is_error"] is False and "duration_s" in records[0]


def test_expired_token_is_refreshed_after_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = FileTokenStorage(settings.credentials_path)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        url = str(request.url)
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return httpx2.Response(200, json=PRM)
        if url.endswith("/.well-known/oauth-authorization-server"):
            return httpx2.Response(200, json=ASM)
        if url == "https://api.undermind.ai/o/token/":
            return httpx2.Response(200, json={"access_token": "new", "token_type": "Bearer",
                                              "expires_in": 3600, "refresh_token": "r2"})
        if request.headers.get("authorization") == "Bearer new":
            return httpx2.Response(200, json={"ok": True})
        return httpx2.Response(401)

    async def scenario() -> None:
        await storage.set_tokens(OAuthToken(access_token="old", refresh_token="r1", expires_in=1))
        await storage.set_client_info(OAuthClientInformationFull(client_id=settings.client_id))
        data = json.loads(storage.path.read_text())
        data["expires_at"] = time.time() - 60
        storage.path.write_text(json.dumps(data))
        transport = httpx2.MockTransport(handler)
        auth = UndermindOAuthProvider(settings, storage, interactive=False,
                                      discovery_transport=transport)
        async with httpx2.AsyncClient(auth=auth, transport=transport) as http:
            response = await http.post(SERVER, json={})
        assert response.status_code == 200

    anyio.run(scenario)
    refresh = next(r for r in seen if str(r.url) == "https://api.undermind.ai/o/token/")
    body = refresh.content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=r1" in body
    tokens = json.loads(storage.path.read_text())["tokens"]
    assert tokens["access_token"] == "new" and tokens["refresh_token"] == "r2"


def test_non_interactive_client_never_opens_login(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = FileTokenStorage(settings.credentials_path)

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return httpx2.Response(200, json=PRM)
        if url.endswith("/.well-known/oauth-authorization-server"):
            return httpx2.Response(200, json=ASM)
        return httpx2.Response(401, headers={"www-authenticate": "Bearer"})

    async def scenario() -> None:
        auth = UndermindOAuthProvider(settings, storage, interactive=False)
        async with httpx2.AsyncClient(auth=auth, transport=httpx2.MockTransport(handler)) as http:
            with pytest.raises(LoginRequiredError):
                await http.post(SERVER, json={})

    anyio.run(scenario)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_loopback_receiver_returns_code(tmp_path: Path) -> None:
    receiver = LoopbackReceiver(_free_port(), open_browser=False)

    async def scenario() -> None:
        await receiver.redirect("https://api.undermind.ai/o/authorize/?x=1")
        url = f"http://127.0.0.1:{receiver.port}/callback?code=c1&state=s1"
        threading.Thread(target=lambda: urllib.request.urlopen(url).read(), daemon=True).start()
        result = await receiver.callback()
        assert (result.code, result.state) == ("c1", "s1")

    anyio.run(scenario)


class _FailingConnection:
    async def __aenter__(self) -> None:
        raise ExceptionGroup("transport", [ExceptionGroup("auth", [LoginRequiredError("login")])])


def test_client_unwraps_login_required(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = UndermindClient(_settings(tmp_path), connect=lambda: _FailingConnection())  # type: ignore[arg-type,return-value]
        with pytest.raises(LoginRequiredError):
            async with client:
                pass

    anyio.run(scenario)
