"""Role cards and the work-order prompt shared by every executor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tar_lab.core import seal
from tar_lab.core.workorder import WorkOrder

from .guides import GUIDES


@dataclass(frozen=True)
class RoleCard:
    name: str
    tier: str  # "high" | "medium" | "low"; mapped to a concrete model by configuration
    brief: str


ROLES: dict[str, RoleCard] = {card.name: card for card in (
    RoleCard("researcher", "medium",
             "You survey literature, code and benchmarks, check novelty and find baselines. Use the "
             "`undermind` MCP tools for literature search and PDF reading when available."),
    RoleCard("designer", "high",
             "You propose research questions and experiment designs grounded in prior human "
             "work; every design choice must cite where it comes from."),
    RoleCard("engineer", "low",
             "You set up environments, write and run code, and fix bugs."),
    RoleCard("analyst", "medium",
             "You analyse results, compute statistics and judge significance."),
    RoleCard("writer", "medium", "You write research reports and papers."),
    RoleCard("reviewer", "high",
             "You review work against the standards of the target venue and judge its value; "
             "verify novelty and citations with the `undermind` tools when available."),
)}

_PERMISSIONS = {
    "small": "You may edit files and install Python packages inside the workspace and download "
             "data. Do not use sudo, do not modify files outside the workspace, do not delete "
             "files you did not create.",
    "large": "You may additionally install system software and modify files outside the "
             "workspace; list every such change in your summary so it can be reverted.",
    "approval": "This order was approved by the user; the same rules as 'large' apply.",
}


_TOOL_NOTES = {
    "undermind": ("The `undermind` MCP server is attached to this session: literature "
                  "search (`search_papers`), paper metadata (`get_paper_info`), full-text PDF "
                  "reading (`read_pdfs`), and deep search (`launch_deep_search`, small "
                  "per-project quota). Its tools may be loaded lazily: search your tools for "
                  "`undermind` and call `get_orientation` first. It is your primary literature "
                  "source; do not conclude it is unavailable without trying it. If it reports "
                  "a quota, rate limit or outage, switch to the `literature` server and say so "
                  "in your report."),
    "literature": ("The `literature` MCP server searches OpenAlex, Semantic Scholar and arXiv "
                   "(`search_papers`, `get_paper`, `find_citations`); results include DOIs, "
                   "arXiv ids and open-access PDF links you can download. Use it when "
                   "Undermind is unavailable, and to cross-check citations."),
}


_TOOLS_NOT_RESOURCES = ("These servers provide MCP tools, not MCP resources: an empty or "
                        "failing resource listing says nothing about them. Find the tools "
                        "(e.g. search your tools for the server name) and call one before "
                        "deciding a server is unavailable.")


def tool_notes(servers: Sequence[str]) -> str:
    notes = [_TOOL_NOTES.get(s, f"The `{s}` MCP server is attached to this session.")
             for s in servers]
    if notes:
        notes.append(_TOOLS_NOT_RESOURCES)
    return "\n".join(f"- {n}" for n in notes)


def render_prompt(order: WorkOrder, *, resume: bool = False,
                  mcp_servers: Sequence[str] = ()) -> str:
    role = ROLES[order.role]
    tools = ["", "## Tools", tool_notes(mcp_servers)] if mcp_servers else []
    if resume:
        return "\n".join([
            (f"You were interrupted while working on work order {order.id}. Inspect the current "
            "workspace state, continue from where you stopped, and finish the order as "
            "originally specified. Reply with the same final JSON report."), *tools])
    lines = [
        f"# Work order {order.id} ({role.name})",
        role.brief,
        "",
        "## Goal",
        order.goal,
        "",
        "## Deliverables",
        *[f"- {d}" for d in order.deliverables],
        "",
        "## Acceptance",
        order.acceptance,
        "",
        "## Limits",
        (f"- Wall time: {max(1, order.limits.wall_seconds // 60)} min; you are stopped when it "
         "runs out. Deliver a working result early instead of polishing details."),
        f"- Permissions: {_PERMISSIONS[order.permission]}",
    ]
    if order.inputs:
        lines += ["", "## Inputs", *[f"- {i}" for i in order.inputs]]
    lines += tools
    if role.name in GUIDES:
        lines += ["", "## How to work", GUIDES[role.name]]
    if order.params.get("final_eval"):
        lines += ["", "## Sealed final evaluation", (
            f"This is the project's sealed final evaluation. `{seal.FINAL_DIR}/` and every path "
            f"in `{seal.FROZEN}` were fingerprinted when this order was dispatched and are "
            f"checked again when it ends. Follow `{seal.PROTOCOL}` exactly, once. Do not edit "
            "anything sealed, and do not tune, select or rerun to improve numbers. Write all "
            f"outputs to `{seal.RESULTS_DIR}/{order.id}/`. If the protocol cannot run as "
            "written, stop and report `needs_help` instead of changing it. Report every number "
            "it produced, including bad ones.")]
    if order.require_sources:
        lines += ["", "## Sources",
                  ("Base key design decisions on existing papers or code and list them in "
                   "`sources` (paper, repository, or URL). Respect licenses.")]
    lines += [
        "", "## Report",
        ("Finish with a JSON object: `status` (done | failed | needs_help), `summary` (what "
         "you did, results, open problems), `deliverables` (workspace-relative paths), "
         "`sources`."),
    ]
    return "\n".join(lines)
