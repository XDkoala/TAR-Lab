"""``tarlab`` command line."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio

from .core.ledger import Ledger, OrderState
from .core.workorder import Executor, Limits, WorkOrder
from .integrations.ai_scientist import AIScientistEngine, AIScientistExecutor, load_run
from .integrations.cli_agents.codex import CodexExecutor
from .integrations.cli_agents.devin import DevinExecutor
from .integrations.undermind import (
    FileTokenStorage,
    LoginRequiredError,
    ToolPolicy,
    UndermindClient,
    UndermindSettings,
)
from .orchestration.kernel import Kernel, Project


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


async def _undermind_login(settings: UndermindSettings, open_browser: bool) -> int:
    async with UndermindClient(settings, interactive=True, open_browser=open_browser) as client:
        tools = await client.tools()
    print(f"Logged in to Undermind; {len(tools)} tools available. "
          f"Credentials: {settings.credentials_path}")
    return 0


async def _undermind_tools(settings: UndermindSettings, policy: ToolPolicy, as_json: bool) -> int:
    async with UndermindClient(settings, policy=policy) as client:
        infos = await client.tools()
    if as_json:
        _print_json([
            {"name": i.tool.name, "allowed": i.decision.allowed, "reason": i.decision.reason,
             "annotations": i.tool.annotations.model_dump(exclude_none=True)
             if i.tool.annotations else None,
             "description": i.tool.description}
            for i in infos
        ])
        return 0
    for i in sorted(infos, key=lambda i: (not i.decision.allowed, i.tool.name)):
        mark = "allow" if i.decision.allowed else "deny "
        print(f"{mark}  {i.tool.name:32} {i.decision.reason}")
    return 0


async def _undermind_call(settings: UndermindSettings, policy: ToolPolicy, name: str,
                          arguments: dict[str, Any]) -> int:
    async with UndermindClient(settings, policy=policy) as client:
        result = await client.call(name, arguments)
    _print_json(result.model_dump(mode="json", exclude_none=True))
    return 1 if result.is_error else 0


def _undermind_status(settings: UndermindSettings) -> int:
    storage = FileTokenStorage(settings.credentials_path)
    tokens = anyio.run(storage.get_tokens)
    expires_at = storage.expires_at()
    _print_json({
        "server_url": settings.server_url,
        "client_id": settings.client_id,
        "credentials_path": str(settings.credentials_path),
        "logged_in": tokens is not None,
        "has_refresh_token": bool(tokens and tokens.refresh_token),
        "access_token_expires_at": datetime.fromtimestamp(expires_at).isoformat()
        if expires_at else None,
    })
    return 0


def _engine_compare(run_dirs: Sequence[Path]) -> int:
    rows = []
    for run_dir in run_dirs:
        summary = load_run(run_dir)
        best = summary.best_program or {}
        rows.append({"run_dir": str(run_dir), "status": summary.status,
                     "combined_score": best.get("combined_score"), "correct": best.get("correct"),
                     "cost_usd": summary.estimated_cost_usd,
                     "wall_minutes": round(summary.wall_seconds / 60, 1)
                     if summary.wall_seconds is not None else None})
    _print_json(rows)
    return 0


def _engine_summary(run_dir: Path) -> int:
    summary = load_run(run_dir)
    data = asdict(summary)
    data["completed"] = summary.completed
    _print_json(data)
    return 0 if summary.completed else 1


REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine(name: str) -> AIScientistEngine:
    workspace = REPO_ROOT.parent
    runs = Path(os.environ.get("TARLAB_RUNS_DIR") or workspace / "runs")
    return AIScientistEngine(REPO_ROOT / "engines" / name, runs, workspace / ".tools" / "node")


def _executors() -> dict[str, Executor]:
    return {
        "codex": CodexExecutor(),
        "devin": DevinExecutor(),
        "ai-scientist": AIScientistExecutor(_engine("ai-scientist-v3")),
        "ai-scientist-original": AIScientistExecutor(
            _engine("ai-scientist-v3-original"), name="ai-scientist-original"),
    }


def _print_attention(kind: str, state: OrderState) -> None:
    print(f"[attention] {state.order.id}: {kind} (status={state.status})", file=sys.stderr)


def _kernel(root: Path) -> Kernel:
    project = Project(root)
    if not project.ledger_path.exists():
        raise SystemExit(f"{root} is not a TAR-Lab project; run `tarlab project init` first")
    return Kernel(project, Ledger(project.ledger_path), _executors(),
                  on_attention=_print_attention)


def _order_command(args: argparse.Namespace) -> int:
    kernel = _kernel(args.project)
    if args.command == "add":
        params = {}
        for item in args.param:
            key, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"--param expects KEY=JSON, got {item!r}")
            try:
                params[key] = json.loads(value)
            except json.JSONDecodeError:
                params[key] = value
        kernel.submit(WorkOrder(
            args.id, args.role, args.executor, args.goal, tuple(args.deliverable),
            args.acceptance, Limits(int(args.minutes * 60), args.max_tokens), params=params))
        return 0
    if args.command == "run":
        kernel.run_pending()
    states = kernel.ledger.orders()
    if args.command == "events":
        _print_json([{"seq": e.seq, "ts": e.ts, "kind": e.kind, "order": e.order_id,
                      "payload": e.payload} for e in kernel.ledger.events(args.order)])
        return 0
    _print_json({oid: {"status": s.status, "attempts": s.attempts,
                       "used_seconds": round(s.used_seconds, 1), "session_id": s.session_id,
                       "usage": asdict(s.usage),
                       "cost_usd": s.result.cost_usd if s.result else None,
                       "summary": s.result.summary if s.result else None}
                 for oid, s in states.items()})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tarlab", description="TAR-Lab research platform")
    groups = parser.add_subparsers(dest="group", required=True)

    um = groups.add_parser("undermind", help="Undermind MCP connection").add_subparsers(
        dest="command", required=True)
    login = um.add_parser("login", help="authorize in the browser and store credentials")
    login.add_argument("--no-browser", action="store_true", help="print the URL only")
    um.add_parser("status", help="show stored credential state (no network)")
    um.add_parser("logout", help="delete stored credentials")
    for name, help_text in (("tools", "list tools and policy decisions"),
                            ("call", "call one tool")):
        sub = um.add_parser(name, help=help_text)
        sub.add_argument("--allow-write", action="store_true",
                         help="also allow tools annotated destructiveHint=false")
        if name == "tools":
            sub.add_argument("--json", action="store_true")
        else:
            sub.add_argument("tool")
            sub.add_argument("--args", default="{}", help="JSON object of tool arguments")

    engine = groups.add_parser("engine", help="AI-Scientist-v3 engine").add_subparsers(
        dest="command", required=True)
    summary = engine.add_parser("summary", help="summarize a run directory")
    summary.add_argument("run_dir", type=Path)
    compare = engine.add_parser("compare", help="final score, cost and time of several runs")
    compare.add_argument("run_dirs", type=Path, nargs="+")

    project = groups.add_parser("project", help="research projects").add_subparsers(
        dest="command", required=True)
    project.add_parser("init", help="create a project directory").add_argument(
        "project", type=Path)

    order = groups.add_parser("order", help="work orders").add_subparsers(
        dest="command", required=True)
    add = order.add_parser("add", help="submit a work order")
    add.add_argument("project", type=Path)
    add.add_argument("--id", required=True)
    add.add_argument("--goal", required=True)
    add.add_argument("--role", default="engineer")
    add.add_argument("--executor", default="codex")
    add.add_argument("--deliverable", action="append", default=[])
    add.add_argument("--acceptance", default="The deliverables exist and work.")
    add.add_argument("--minutes", type=float, default=10)
    add.add_argument("--max-tokens", type=int)
    add.add_argument("--param", action="append", default=[],
                     help="executor setting KEY=JSON (plain strings need no quotes)")
    for name, help_text in (("run", "run pending orders, resuming interrupted ones"),
                            ("status", "show order states")):
        order.add_parser(name, help=help_text).add_argument("project", type=Path)
    events = order.add_parser("events", help="dump ledger events")
    events.add_argument("project", type=Path)
    events.add_argument("--order")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # The SDK logs a traceback for every failed OAuth flow; the CLI reports the error itself.
    logging.getLogger("mcp.client.auth").setLevel(logging.CRITICAL)
    if args.group == "engine":
        if args.command == "compare":
            return _engine_compare(args.run_dirs)
        return _engine_summary(args.run_dir)
    if args.group == "project":
        project = Project(args.project)
        project.init()
        Ledger(project.ledger_path).close()
        print(f"Project ready: {args.project}")
        return 0
    if args.group == "order":
        return _order_command(args)
    settings = UndermindSettings.from_env()
    policy = ToolPolicy(mode="read_write" if getattr(args, "allow_write", False) else "read_only")
    try:
        if args.command == "login":
            return anyio.run(_undermind_login, settings, not args.no_browser)
        if args.command == "status":
            return _undermind_status(settings)
        if args.command == "logout":
            removed = FileTokenStorage(settings.credentials_path).clear()
            print("Credentials deleted." if removed else "No stored credentials.")
            return 0
        if args.command == "tools":
            return anyio.run(_undermind_tools, settings, policy, args.json)
        arguments = json.loads(args.args)
        if not isinstance(arguments, dict):
            raise SystemExit("--args must be a JSON object")
        return anyio.run(_undermind_call, settings, policy, args.tool, arguments)
    except LoginRequiredError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
