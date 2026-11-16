"""OAuth for Undermind: PKCE authorization code flow with a loopback redirect."""

from __future__ import annotations

import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    ProtectedResourceMetadata,
)
from pydantic import AnyUrl, ValidationError

from .http import make_http_client
from .settings import UndermindSettings
from .storage import FileTokenStorage

LOGIN_TIMEOUT_SECONDS = 300


class LoginRequiredError(RuntimeError):
    """Browser authorization is needed but the client runs non-interactively."""


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        receiver: LoopbackReceiver = self.server.receiver  # type: ignore[attr-defined]
        if url.path != "/callback":
            self.send_error(404)
            return
        receiver.params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TAR-Lab: authorization received, you can close this page.")
        receiver.done.set()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class LoopbackReceiver:
    """Receive one OAuth redirect on ``127.0.0.1:<port>/callback``."""

    def __init__(self, port: int, *, open_browser: bool = True) -> None:
        self.port = port
        self.open_browser = open_browser
        self.params: dict[str, str] = {}
        self.done = threading.Event()
        self._server: HTTPServer | None = None

    async def redirect(self, authorization_url: str) -> None:
        # Listen before the browser can be redirected back.
        server = HTTPServer(("127.0.0.1", self.port), _CallbackHandler)
        server.receiver = self  # type: ignore[attr-defined]
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Open this URL to authorize TAR-Lab with Undermind:\n{authorization_url}",
              file=sys.stderr)
        if self.open_browser:
            webbrowser.open(authorization_url)

    async def callback(self) -> AuthorizationCodeResult:
        try:
            received = await anyio.to_thread.run_sync(self.done.wait, LOGIN_TIMEOUT_SECONDS)
        finally:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        if not received:
            raise TimeoutError(f"no authorization received within {LOGIN_TIMEOUT_SECONDS}s")
        if "error" in self.params:
            raise PermissionError(
                f"authorization denied: {self.params['error']} {self.params.get('error_description', '')}"
            )
        return AuthorizationCodeResult(
            code=self.params.get("code", ""), state=self.params.get("state"),
            iss=self.params.get("iss"),
        )


async def _refuse_login(_: str) -> None:
    raise LoginRequiredError("Undermind authorization required: run `tarlab undermind login`")


async def _no_callback() -> AuthorizationCodeResult:
    raise LoginRequiredError("Undermind authorization required: run `tarlab undermind login`")


class UndermindOAuthProvider(OAuthClientProvider):
    """``OAuthClientProvider`` that can refresh stored tokens after a restart.

    The SDK learns the token endpoint and expiry only during an interactive
    flow. Here they are restored from storage and server discovery, so a
    background job refreshes an expired token instead of failing with 401
    and demanding a new browser login.
    """

    def __init__(self, settings: UndermindSettings, storage: FileTokenStorage,
                 *, interactive: bool, open_browser: bool = True,
                 discovery_transport: httpx2.AsyncBaseTransport | None = None) -> None:
        receiver = LoopbackReceiver(settings.redirect_port, open_browser=open_browser)
        super().__init__(
            server_url=settings.server_url,
            client_metadata=OAuthClientMetadata(
                client_name="TAR-Lab",
                redirect_uris=[AnyUrl(settings.redirect_uri)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
                scope="mcp",
            ),
            storage=storage,
            redirect_handler=receiver.redirect if interactive else _refuse_login,
            callback_handler=receiver.callback if interactive else _no_callback,
            client_metadata_url=settings.client_id if settings.uses_cimd else None,
        )
        self._settings = settings
        self._storage = storage
        self._discovery_transport = discovery_transport

    async def _initialize(self) -> None:
        await super()._initialize()
        ctx = self.context
        if ctx.client_info is None and not self._settings.uses_cimd:
            ctx.client_info = OAuthClientInformationFull(
                client_id=self._settings.client_id, token_endpoint_auth_method="none",
                redirect_uris=[AnyUrl(self._settings.redirect_uri)],
            )
        if ctx.current_tokens is None:
            return
        ctx.token_expiry_time = self._storage.expires_at()
        if ctx.oauth_metadata is None:
            await self._discover()

    async def _discover(self) -> None:
        """Fetch protected-resource and authorization-server metadata (RFC 9728 / 8414)."""
        parsed = urlparse(self._settings.server_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        prm_url = f"{origin}/.well-known/oauth-protected-resource{parsed.path.rstrip('/')}"
        async with make_http_client(
            prm_url, timeout=30, transport=self._discovery_transport
        ) as http:
            try:
                prm = ProtectedResourceMetadata.model_validate_json((await http.get(prm_url)).content)
                issuer = str(prm.authorization_servers[0]).rstrip("/")
                asm_response = await http.get(f"{issuer}/.well-known/oauth-authorization-server")
                asm = OAuthMetadata.model_validate_json(asm_response.content)
            except (httpx2.HTTPError, ValidationError, IndexError):
                return  # the 401 path rediscovers; refresh then falls back to a login
        self.context.protected_resource_metadata = prm
        self.context.auth_server_url = str(prm.authorization_servers[0])
        self.context.oauth_metadata = asm
