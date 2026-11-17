"""Codex CLI executor (``codex exec --json``)."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tar_lab.agents.roles import render_prompt
from tar_lab.core.workorder import (
    REPORTED_STATUSES,
    RESULT_SCHEMA,
    Attempt,
    EventSink,
    McpServer,
    Usage,
    WorkResult,
)

from .process import run_supervised, tail

# Account-level apps/connectors and plugins would give the agent tools outside TAR-Lab's
# policy and audit (e.g. the user's own Undermind connector with all their workspaces).
DISABLED_FEATURES = ("apps", "plugins", "remote_plugin")


def _base_flags(model: str, effort: str) -> list[str]:
    flags = ["--json", "--ignore-user-config", "-m", model,
             "-c", f'model_reasoning_effort="{effort}"']
    for feature in DISABLED_FEATURES:
        flags += ["--disable", feature]
    return flags


# Where the ChatGPT desktop app ships its Codex CLI when it is not on PATH.
BUNDLED_CODEX = Path("/usr/lib/chatgpt/resources/codex")


def find_codex() -> str:
    found = os.environ.get("TARLAB_CODEX_BIN") or shutil.which("codex")
    if not found and os.access(BUNDLED_CODEX, os.X_OK):
        found = str(BUNDLED_CODEX)
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
        cmd += [*_base_flags(self.model, self.reasoning_effort),
                "--output-schema", str(schema), "-o", str(last_message)]
        if self.sandbox == "bypass":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif not attempt.resume_session:  # resume inherits the session's sandbox
            cmd += ["-s", self.sandbox]
        cmd += mcp_config(attempt.mcp_servers)
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
        prompt.write_text(render_prompt(attempt.order, resume=attempt.resume_session is not None,
                                         mcp_servers=list(attempt.mcp_servers)))
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


    def ask(self, prompt: str, schema: dict[str, Any], *, cwd: Path, log_dir: Path,
            timeout: float = 900) -> tuple[dict[str, Any], Usage]:
        """One fresh session that must answer with JSON matching ``schema``."""
        cwd, log_dir = cwd.resolve(), log_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        schema_path, last, prompt_path = (log_dir / "schema.json", log_dir / "answer.json",
                                          log_dir / "prompt.md")
        schema_path.write_text(json.dumps(schema))
        prompt_path.write_text(prompt)
        last.unlink(missing_ok=True)
        usage, errors = [Usage()], []

        def on_line(line: str) -> None:
            if '"turn.completed"' in line:
                usage[0] = _usage(json.loads(line).get("usage", {}))
            elif '"turn.failed"' in line or '"type":"error"' in line.replace(" ", ""):
                errors.append(line[:1000])

        attempt_cmd = [self.binary or find_codex(), "exec",
                       *_base_flags(self.model, self.reasoning_effort),
                       "--output-schema", str(schema_path), "-o", str(last),
                       "--skip-git-repo-check", "-C", str(cwd)]
        if self.sandbox == "bypass":
            attempt_cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            attempt_cmd += ["-s", self.sandbox]
        outcome = run_supervised(
            [*attempt_cmd, "-"], cwd=cwd, stdin_path=prompt_path,
            stdout_path=log_dir / "events.jsonl", stderr_path=log_dir / "stderr.log",
            timeout=timeout, emit=lambda _kind, _payload: None, on_line=on_line)
        try:
            answer = json.loads(last.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            detail = "timed out" if outcome.timed_out else (
                " ".join(errors[-2:]) or tail(log_dir / "stderr.log"))
            raise RuntimeError(f"codex gave no JSON answer: {detail}") from exc
        if not isinstance(answer, dict):
            raise TypeError("codex answer is not a JSON object")
        return answer, usage[0]

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


def mcp_config(servers: Mapping[str, McpServer]) -> list[str]:
    """``-c`` overrides that register stdio MCP servers (values are TOML; JSON strings and
    arrays are valid TOML)."""
    flags = []
    for name, server in servers.items():
        key = f"mcp_servers.{name}"
        env = ", ".join(f"{k} = {json.dumps(v)}" for k, v in server.env.items())
        flags += ["-c", f"{key}.command={json.dumps(server.command)}",
                  "-c", f"{key}.args={json.dumps(list(server.args))}",
                  "-c", f"{key}.env={{{env}}}",
                  "-c", f"{key}.startup_timeout_sec=60",
                  "-c", f"{key}.tool_timeout_sec={server.tool_timeout_seconds}"]
    return flags


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
