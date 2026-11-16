"""Persist Undermind OAuth tokens on disk (owner-only, atomic writes)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class FileTokenStorage:
    """``mcp.client.auth.TokenStorage`` backed by a 0600 JSON file.

    Undermind rotates refresh tokens on every use, so each refresh must be
    persisted before the old token is lost; writes go through a temp file and
    ``os.replace``. The absolute expiry is stored too, because the SDK only
    knows ``expires_in`` relative to when a token was received.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".credentials.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self._load().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        info = self._load().get("client_info")
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)

    def expires_at(self) -> float | None:
        value = self._load().get("expires_at")
        return float(value) if value is not None else None

    def clear(self) -> bool:
        """Delete stored credentials; return whether a file existed."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True
