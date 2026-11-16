"""Which Undermind tools agents may call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mcp.types import Tool

_DESTRUCTIVE_PREFIXES = ("delete_", "remove_", "revoke_")


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ToolPolicy:
    """Default-deny tool policy driven by MCP tool annotations.

    ``read_only`` allows tools annotated ``readOnlyHint``. ``read_write`` also
    allows tools explicitly annotated ``destructiveHint: false`` (e.g. creating
    a search or folder). Destructive or unannotated tools are denied unless
    listed in ``allow``; ``deny`` always wins.
    """

    mode: Literal["read_only", "read_write"] = "read_only"
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    def decide(self, tool: Tool) -> ToolDecision:
        notes = tool.annotations
        if tool.name in self.deny:
            return ToolDecision(False, "listed in deny")
        if tool.name in self.allow:
            return ToolDecision(True, "listed in allow")
        if tool.name.startswith(_DESTRUCTIVE_PREFIXES) or (notes and notes.destructive_hint):
            return ToolDecision(False, "destructive")
        if notes and notes.read_only_hint:
            return ToolDecision(True, "read-only")
        if self.mode == "read_write" and notes and notes.destructive_hint is False:
            return ToolDecision(True, "non-destructive write")
        if self.mode == "read_only":
            return ToolDecision(False, "not read-only")
        return ToolDecision(False, "no destructiveHint=false annotation")
