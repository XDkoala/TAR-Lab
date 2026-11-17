"""Live steps of a running agent, read incrementally from its JSONL event stream."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

StepStatus = Literal["running", "ok", "failed"]
MAX_STEPS = 200


@dataclass
class Step:
    kind: str  # "command" | "tool" | "edit" | "note" | "search"
    text: str
    status: StepStatus = "running"


_SHELL = re.compile(r"^/bin/(?:ba)?sh -l?c (.+)$", re.DOTALL)


def _command(cmd: str) -> str:
    m = _SHELL.match(cmd.strip())
    if m:
        try:
            cmd = shlex.split(m.group(1))[0]
        except (ValueError, IndexError):
            cmd = m.group(1).strip("'\"")
    lines = [ln.strip() for ln in cmd.strip().splitlines() if ln.strip()] or [""]
    return lines[0] + (" …" if len(lines) > 1 else "")


def _short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_args(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("query", "sample_abstract", "paper_id", "path", "url", "prompt"):
            if args.get(key):
                return _short(args[key], 60)
        return _short(args, 60) if args else ""
    return ""


def step_of(item: dict[str, Any], workspace: Path | None = None) -> Step | None:
    kind, status = item.get("type"), item.get("status")
    state: StepStatus = ("failed" if status in ("failed", "declined") or item.get("error")
                         else "running" if status == "in_progress" else "ok")
    if kind == "command_execution":
        return Step("command", _command(str(item.get("command", ""))), state)
    if kind == "mcp_tool_call":
        args = _tool_args(item.get("arguments"))
        name = f"{item.get('server')}.{item.get('tool')}"
        return Step("tool", f"{name}({args})" if args else name, state)
    if kind == "file_change":
        paths = []
        for change in item.get("changes", []):
            p = Path(str(change.get("path", "")))
            if workspace is not None and p.is_relative_to(workspace):
                p = p.relative_to(workspace)
            paths.append(f"{change.get('kind', 'edit')} {p}")
        return Step("edit", ", ".join(paths), state)
    if kind == "agent_message":
        return Step("note", _short(item.get("text", ""), 200), "ok")
    if kind == "web_search":
        return Step("search", _short(item.get("query", ""), 80), state)
    return None


class ActivityTail:
    """Follows one ``events.jsonl``; safe to poll while the agent is still writing it."""

    def __init__(self, path: Path, workspace: Path | None = None) -> None:
        self.path, self.workspace = path, workspace
        self._offset = 0
        self._steps: dict[str, Step] = {}

    def poll(self) -> list[Step]:
        try:
            with self.path.open("rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return self.steps
        end = data.rfind(b"\n") + 1  # leave a half-written last line for the next poll
        self._offset += end
        for line in data[:end].splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item")
            if isinstance(item, dict) and event.get("type", "").startswith("item."):
                step = step_of(item, self.workspace)
                if step is not None:
                    self._steps[str(item.get("id", len(self._steps)))] = step
        if len(self._steps) > MAX_STEPS:
            for key in list(self._steps)[: len(self._steps) - MAX_STEPS]:
                del self._steps[key]
        return self.steps

    @property
    def steps(self) -> list[Step]:
        return list(self._steps.values())
