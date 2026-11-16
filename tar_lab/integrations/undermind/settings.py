"""Undermind connection settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_URL = "https://mcp.undermind.ai/mcp"
# Client ID Metadata Document published from deploy/oauth/client-metadata.json (gh-pages).
DEFAULT_CLIENT_ID = "https://xdkoala.github.io/TAR-Lab/oauth/client-metadata.json"
DEFAULT_REDIRECT_PORT = 47615


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tar-lab"


@dataclass(frozen=True)
class UndermindSettings:
    server_url: str = SERVER_URL
    client_id: str = DEFAULT_CLIENT_ID
    """A CIMD URL (``https://...``) or a client ID pre-registered with Undermind."""
    redirect_port: int = DEFAULT_REDIRECT_PORT
    credentials_path: Path = _config_dir() / "undermind" / "credentials.json"
    audit_path: Path = _config_dir() / "undermind" / "audit.jsonl"

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.redirect_port}/callback"

    @property
    def uses_cimd(self) -> bool:
        return self.client_id.startswith("https://")

    @classmethod
    def from_env(cls) -> UndermindSettings:
        """Override defaults with ``TARLAB_UNDERMIND_*`` environment variables."""
        env = os.environ
        default = cls()
        return cls(
            server_url=env.get("TARLAB_UNDERMIND_SERVER_URL", default.server_url),
            client_id=env.get("TARLAB_UNDERMIND_CLIENT_ID", default.client_id),
            redirect_port=int(env.get("TARLAB_UNDERMIND_REDIRECT_PORT", default.redirect_port)),
            credentials_path=Path(
                env.get("TARLAB_UNDERMIND_CREDENTIALS", default.credentials_path)
            ).expanduser(),
            audit_path=Path(env.get("TARLAB_UNDERMIND_AUDIT_LOG", default.audit_path)).expanduser(),
        )
