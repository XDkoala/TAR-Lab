"""Codex CLI executor (``codex exec --json``)."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tar_lab.agents.roles import render_prompt
from tar_lab.core.workorder import (
    REPORTED_STATUSES,
    RESULT_SCHEMA,
    Attempt,
    EventSink,
    Usage,
    WorkResult,
)

from .process import run_supervised, tail


def find_codex() -> str:
    found = os.environ.get("TARLAB_CODEX_BIN") or shutil.which("codex")
    if not found:
        raise FileNotFoundError("codex CLI not found; set TARLAB_CODEX_BIN")
    return found


@dataclass
class CodexExecutor:
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "medium"
    # Codex's bwrap sandbox needs unprivileged user namespaces, which Ubuntu 24.04 blocks by
    # default; "bypass" runs unsandboxed and relies on the kernel's snapshots and limits.
    sandbox: str = "bypass"
    binary: str | None = None
    name: str = "codex"
    supports_resume: bool = True

    def command(self, attempt: Attempt, schema: Path, last_message: Path) -> list[str]:
        cmd = [self.binary or find_codex(), "exec"]
        if attempt.resume_session:
            cmd.append("resume")
        cmd += ["--json", "--ignore-user-config", "-m", self.model,
                "-c", f'model_reasoning_effort="{self.reasoning_effort}"',
                "--output-schema", str(schema), "-o", str(last_message)]
        if self.sandbox == "bypass":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif not attempt.resume_session:  # resume inherits the session's sandbox
            cmd += ["-s", self.sandbox]
        if attempt.resume_session:
            cmd.append(attempt.resume_session)
        else:
            cmd += ["-C", str(attempt.workspace)]
        return [*cmd, "-"]  # prompt from stdin

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        d = attempt.attempt_dir
        d.mkdir(parents=True, exist_ok=True)
        schema, last, prompt = d / "schema.json", d / "last_message.json", d / "prompt.md"
        schema.write_text(json.dumps(RESULT_SCHEMA))
        prompt.write_text(render_prompt(attempt.order, resume=attempt.resume_session is not None))
        last.unlink(missing_ok=True)
        state: dict[str, Any] = {"session": attempt.resume_session, "usage": Usage(),
                                 "error": None}

        def on_line(line: str) -> None:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            kind = event.get("type")
            if kind == "thread.started":
                state["session"] = event["thread_id"]
                emit("order.session", {"session_id": event["thread_id"]})
            elif kind == "turn.completed":
                # Codex reports the session's running total, also after ``resume``.
                state["usage"] = _usage(event.get("usage", {}))
            elif kind in ("turn.failed", "error"):
                state["error"] = json.dumps(event, ensure_ascii=False)[:2000]

        outcome = run_supervised(
            self.command(attempt, schema, last), cwd=attempt.workspace, stdin_path=prompt,
            stdout_path=d / "events.jsonl", stderr_path=d / "stderr.log",
            timeout=attempt.timeout_seconds, emit=emit, on_line=on_line)
        common = {"usage": state["usage"], "session_id": state["session"],
                  "wall_seconds": round(outcome.wall_seconds, 1)}
        if outcome.timed_out:
            return WorkResult("limit_exceeded", "Stopped at the wall-time limit.", **common)
        report = _read_report(last)
        if report is None:
            error = state["error"] or tail(d / "stderr.log") or f"exit code {outcome.returncode}"
            return WorkResult("crashed", "No final report from codex.", error=error, **common)
        return WorkResult(report["status"], report["summary"],
                          tuple(report["deliverables"]), tuple(report["sources"]), **common)


    def session_usage(self, session_id: str) -> Usage | None:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        usage = None
        for rollout in home.glob(f"sessions/*/*/*/rollout-*-{session_id}.jsonl"):
            with rollout.open(encoding="utf-8") as f:
                for line in f:
                    if '"token_count"' not in line:
                        continue
                    info = (json.loads(line).get("payload") or {}).get("info") or {}
                    if info.get("total_token_usage"):
                        usage = _usage(info["total_token_usage"])
        return usage


def _usage(u: dict[str, Any]) -> Usage:
    return Usage(u.get("input_tokens", 0), u.get("cached_input_tokens", 0),
                 u.get("output_tokens", 0))


def _read_report(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("status") not in REPORTED_STATUSES:
        return None
    if not isinstance(data.get("summary"), str):
        return None
    for key in ("deliverables", "sources"):
        if not isinstance(data.get(key), list):
            return None
    return data
