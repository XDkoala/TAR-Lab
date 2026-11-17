"""Devin CLI executor (``devin -p``)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tar_lab.agents.roles import render_prompt
from tar_lab.core.workorder import REPORTED_STATUSES, Attempt, EventSink, Usage, WorkResult

from .process import run_supervised, tail

# Set when the caller itself runs inside Devin Desktop / Windsurf; they switch the CLI to
# host-provided credentials, so a child CLI would report "Not logged in".
HOST_ENV_VARS = ("ACP_BACKEND", "WINDSURF_IDE_TYPE", "WINDSURF_EXT_HOST_PID",
                 "WINDSURF_CODEIUM_DIR", "DEVIN_FD_PATH")
SESSION_POLL_SECONDS = 3.0


def find_devin() -> str:
    found = os.environ.get("TARLAB_DEVIN_BIN") or shutil.which("devin")
    if not found:
        raise FileNotFoundError("devin CLI not found; set TARLAB_DEVIN_BIN")
    return found


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in HOST_ENV_VARS}


@dataclass
class DevinExecutor:
    model: str = "claude-opus-5-5-medium"
    binary: str | None = None
    name: str = "devin"
    supports_resume: bool = True

    def command(self, attempt: Attempt, prompt: Path, export: Path) -> list[str]:
        cmd = [self.binary or find_devin(), "-p", "--prompt-file", str(prompt),
               "--model", self.model, "--permission-mode", "dangerous",
               "--respect-workspace-trust", "false", "--process-model", "in-process",
               "--export", str(export)]
        if attempt.resume_session:
            cmd += ["-r", attempt.resume_session]
        return cmd

    def sessions(self, workspace: Path) -> list[str]:
        """Session ids for ``workspace``, newest first."""
        proc = subprocess.run([self.binary or find_devin(), "list", "--format", "json"],
                              cwd=workspace, env=clean_env(), capture_output=True, text=True,
                              timeout=60, check=False)
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        rows = [r for r in rows if isinstance(r, dict) and "id" in r]
        rows.sort(key=lambda r: r.get("last_activity_at") or 0, reverse=True)
        return [r["id"] for r in rows]

    def run(self, attempt: Attempt, emit: EventSink) -> WorkResult:
        d = attempt.attempt_dir
        d.mkdir(parents=True, exist_ok=True)
        prompt, export = d / "prompt.md", d / "export.json"
        prompt.write_text(render_prompt(attempt.order, resume=attempt.resume_session is not None)
                          + "\n\nEnd your reply with the JSON object and nothing after it.")
        export.unlink(missing_ok=True)
        session = {"id": attempt.resume_session}
        done = threading.Event()
        if session["id"] is None:
            known = set(self.sessions(attempt.workspace))

            def discover() -> None:
                # ``-p`` prints nothing until it finishes; the new session shows up in
                # ``devin list`` right away, which is what makes a killed attempt resumable.
                while not done.wait(SESSION_POLL_SECONDS):
                    new = [s for s in self.sessions(attempt.workspace) if s not in known]
                    if new:
                        session["id"] = new[0]
                        emit("order.session", {"session_id": new[0]})
                        return

            threading.Thread(target=discover, daemon=True).start()
        try:
            outcome = run_supervised(
                self.command(attempt, prompt, export), cwd=attempt.workspace, stdin_path=None,
                stdout_path=d / "stdout.txt", stderr_path=d / "stderr.log",
                timeout=attempt.timeout_seconds, emit=emit, env=clean_env())
        finally:
            done.set()
        metrics = _read_export(export)
        if metrics.get("session_id") and session["id"] is None:
            session["id"] = metrics["session_id"]
            emit("order.session", {"session_id": session["id"]})
        common = {"usage": metrics.get("usage", Usage()), "session_id": session["id"],
                  "wall_seconds": round(outcome.wall_seconds, 1)}
        if outcome.timed_out:
            return WorkResult("limit_exceeded", "Stopped at the wall-time limit.", **common)
        report = extract_report((d / "stdout.txt").read_text(encoding="utf-8", errors="replace"))
        if report is None:
            error = tail(d / "stderr.log") or tail(d / "stdout.txt") or (
                f"exit code {outcome.returncode}")
            return WorkResult("crashed", "No final JSON report from devin.", error=error,
                              **common)
        return WorkResult(report["status"], report["summary"],
                          tuple(map(str, report.get("deliverables", []))),
                          tuple(map(str, report.get("sources", []))), **common)

    def session_usage(self, session_id: str) -> Usage | None:
        # The export is only written when a turn completes; a killed turn leaves no record.
        return None


def extract_report(text: str) -> dict[str, Any] | None:
    """The last JSON object in ``text`` that looks like a work-order report."""
    decoder = json.JSONDecoder()
    pos = text.rfind("{")
    while pos != -1:
        try:
            data, _ = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            data = None
        if (isinstance(data, dict) and data.get("status") in REPORTED_STATUSES
                and isinstance(data.get("summary"), str)):
            return data
        pos = text.rfind("{", 0, pos)
    return None


def _read_export(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    m = data.get("final_metrics") or {}
    return {"session_id": data.get("session_id"),
            "usage": Usage(int(m.get("total_prompt_tokens", 0)),
                           int(m.get("total_cached_tokens", 0)),
                           int(m.get("total_completion_tokens", 0)))}
