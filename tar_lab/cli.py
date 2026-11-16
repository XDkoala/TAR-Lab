"""``tarlab`` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio

from .integrations.ai_scientist import load_run
from .integrations.undermind import (
    FileTokenStorage,
    LoginRequiredError,
    ToolPolicy,
    UndermindClient,
    UndermindSettings,
)


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


def _engine_summary(run_dir: Path) -> int:
    summary = load_run(run_dir)
    data = asdict(summary)
    data["completed"] = summary.completed
    _print_json(data)
    return 0 if summary.completed else 1


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # The SDK logs a traceback for every failed OAuth flow; the CLI reports the error itself.
    logging.getLogger("mcp.client.auth").setLevel(logging.CRITICAL)
    if args.group == "engine":
        return _engine_summary(args.run_dir)
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
