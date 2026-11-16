"""Undermind MCP integration: OAuth, tool wrappers and result normalization.

Undermind authorizes per user (OAuth + PKCE, no API keys). Run
``tarlab undermind login`` once; afterwards agents connect non-interactively
and the stored refresh token is rotated automatically.
"""

from .auth import LoginRequiredError
from .client import ToolInfo, ToolNotPermittedError, UndermindClient
from .policy import ToolDecision, ToolPolicy
from .settings import UndermindSettings
from .storage import FileTokenStorage

__all__ = [
    "FileTokenStorage",
    "LoginRequiredError",
    "ToolDecision",
    "ToolInfo",
    "ToolNotPermittedError",
    "ToolPolicy",
    "UndermindClient",
    "UndermindSettings",
]
