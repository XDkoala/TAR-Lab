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

from .agents.main_agent import MainAgent, Question
from .core.charter import Charter
from .core.ledger import Ledger, OrderState
from .core.machine import describe
from .core.workorder import Executor, Limits, McpServer, WorkOrder
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
from .integrations.undermind.proxy import create_project_workspace, run_stdio
from .orchestration.kernel import Kernel, McpHook, Project
from .orchestration.lab import Lab, question_text
from .orchestration.notify import NotificationError, default_notifier
from .tools.tex import build as build_tex


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


LITERATURE_ROLES = frozenset({"researcher", "designer", "reviewer", "writer"})
_PROXY_ENV = ("HOME", "PATH", "USER", "LANG", "LC_ALL", "http_proxy", "https_proxy",
              "all_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _mcp_hook(ledger: Ledger) -> McpHook:
    def mcp_for(order: WorkOrder) -> dict[str, McpServer]:
        has_workspace = any(e.kind == "undermind.workspace" for e in ledger.events())
        if order.role not in LITERATURE_ROLES or not has_workspace:
            return {}
        env = {k: v for k, v in os.environ.items()
               if k in _PROXY_ENV or k.startswith("TARLAB_UNDERMIND_")}
        args = ("-m", "tar_lab.cli", "undermind", "serve", "--ledger", str(ledger.path),
                "--order", order.id)
        return {"undermind": McpServer(sys.executable, args, env)}
    return mcp_for


def _kernel(root: Path) -> Kernel:
    project = Project(root)
    if not project.ledger_path.exists():
        raise SystemExit(f"{root} is not a TAR-Lab project; run `tarlab project init` first")
    ledger = Ledger(project.ledger_path)
    return Kernel(project, ledger, _executors(), on_attention=_print_attention,
                  mcp_for=_mcp_hook(ledger))


def _lab(root: Path) -> Lab:
    kernel = _kernel(root)
    agent = MainAgent(CodexExecutor(), kernel.project.root, describe(kernel.project.root))
    return Lab(kernel, agent, default_notifier())


def _ask(q: Question) -> str:
    print(f"\n{q.text}")
    for option in q.options:
        print(f"  - {option}")
    return input(f"[默认 / default: {q.default}] > ")


def _show_charter(charter: Charter) -> None:
    print(f"\n== {charter.title}\n{charter.goal}\n"
          f"venue: {charter.target_venue} | budget: ${charter.budget_usd:g} | "
          f"deadline: {charter.deadline_hours:g} h | approvals: "
          f"{', '.join(charter.approval_points) or '-'} | on timeout: {charter.on_timeout}\n"
          f"constraints: {charter.constraints}\nrisks: {charter.risk_notes}")


def _lab_command(args: argparse.Namespace) -> int:
    if args.group == "new":
        project = Project(args.project)
        project.init()
        Ledger(project.ledger_path).close()
        lab = _lab(args.project)

        def confirm(charter: Charter) -> bool:
            _show_charter(charter)
            return args.defaults or input("\n确认立项？/ Start? [Y/n] ").strip().lower() in (
                "", "y", "yes", "是")

        charter = lab.start(args.idea, ask=(lambda q: "") if args.defaults else _ask,
                            confirm=confirm)
        if charter is None:
            print("Not started; run `tarlab new` again to redo the intake.")
            return 1
        try:
            ws = anyio.run(create_project_workspace, lab.ledger, f"TAR-Lab: {charter.title}")
            print(f"Undermind workspace: {ws}")
        except (LoginRequiredError, RuntimeError) as exc:
            print(f"Warning: no Undermind workspace ({exc}); literature tools are disabled. "
                  "Run `tarlab undermind login`, then `tarlab undermind workspace` for this "
                  "project.", file=sys.stderr)
        print(f"\nProject ready. Run: tarlab run {args.project}")
        return 0
    lab = _lab(args.project)
    if args.group == "run":
        status = lab.run(poll_seconds=args.poll)
        print(f"Project {status}.")
        return 0 if status == "finished" else 1
    if args.group == "reply":
        lab.reply(args.qid, args.answer)
        print(f"Answer recorded for {args.qid}.")
        return 0
    if args.group == "resume":
        lab.resume()
        print(f"Resumed. Run: tarlab run {args.project}")
        return 0
    if args.group == "inbox":
        open_questions = lab.questions()
        for qid, ev in open_questions.items():
            print(question_text(qid, ev.payload) + "\n")
        if not open_questions:
            print("No open questions.")
        return 0
    charter = lab.charter()
    if charter:
        _show_charter(charter)
    print(f"\nstatus: {lab.status() or 'active'}")
    for oid, s in lab.ledger.orders().items():
        print(f"  {oid:28} {s.status:15} {s.result.summary[:90] if s.result else ''}")
    for qid in lab.questions():
        print(f"  open question: {qid}")
    return 0


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

    serve = um.add_parser("serve", help="stdio MCP proxy scoped to one project (for agents)")
    serve.add_argument("--ledger", type=Path, required=True)
    serve.add_argument("--order")
    um.add_parser("workspace", help="create the project's Undermind workspace").add_argument(
        "project", type=Path)

    engine = groups.add_parser("engine", help="AI-Scientist-v3 engine").add_subparsers(
        dest="command", required=True)
    summary = engine.add_parser("summary", help="summarize a run directory")
    summary.add_argument("run_dir", type=Path)
    compare = engine.add_parser("compare", help="final score, cost and time of several runs")
    compare.add_argument("run_dirs", type=Path, nargs="+")

    new = groups.add_parser("new", help="start a research project from one sentence")
    new.add_argument("project", type=Path)
    new.add_argument("idea")
    new.add_argument("--defaults", action="store_true", help="accept every default answer")
    run = groups.add_parser("run", help="run a project until it finishes or is suspended")
    run.add_argument("project", type=Path)
    run.add_argument("--poll", type=float, default=30.0, help="seconds between checks")
    for name, help_text in (("status", "show project state"),
                            ("inbox", "list questions waiting for you"),
                            ("resume", "resume a suspended project")):
        groups.add_parser(name, help=help_text).add_argument("project", type=Path)
    tex = groups.add_parser("tex", help="LaTeX builds in Docker").add_subparsers(
        dest="command", required=True)
    tex_build = tex.add_parser("build", help="compile a paper directory to PDF")
    tex_build.add_argument("paper_dir", type=Path)
    tex_build.add_argument("--main", default="main.tex")
    groups.add_parser("notify-test", help="send a test notification (console and email)")
    reply = groups.add_parser("reply", help="answer a question or approval")
    reply.add_argument("project", type=Path)
    reply.add_argument("qid")
    reply.add_argument("answer")

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
    if args.group == "tex":
        result = build_tex(args.paper_dir, args.main)
        print(f"PDF: {result.pdf}" if result.pdf else f"Build failed; see {result.log}")
        return 0 if result.pdf else 1
    if args.group == "notify-test":
        try:
            default_notifier().notify("Test notification", "TAR-Lab can reach you here.")
        except NotificationError as exc:
            print(f"Notification failed: {exc}\nAn SSL/EOF error usually means a proxy (e.g. "
                  "Clash in global/TUN mode) is routing SMTP through a node that blocks mail "
                  "ports; send the SMTP host directly.", file=sys.stderr)
            return 1
        print("Sent.")
        return 0
    if args.group in ("new", "run", "status", "inbox", "reply", "resume"):
        return _lab_command(args)
    if args.command == "serve":
        run_stdio(args.ledger, args.order)
        return 0
    if args.command == "workspace":
        lab = _lab(args.project)
        charter = lab.charter()
        if charter is None:
            raise SystemExit("project has no charter yet")
        print(anyio.run(create_project_workspace, lab.ledger, f"TAR-Lab: {charter.title}"))
        return 0
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
