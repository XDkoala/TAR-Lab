"""Role cards and the work-order prompt shared by every executor."""

from __future__ import annotations

from dataclasses import dataclass

from tar_lab.core.workorder import WorkOrder


@dataclass(frozen=True)
class RoleCard:
    name: str
    tier: str  # "high" | "medium" | "low"; mapped to a concrete model by configuration
    brief: str


ROLES: dict[str, RoleCard] = {card.name: card for card in (
    RoleCard("researcher", "medium",
             "You survey literature, code and benchmarks, check novelty and find baselines."),
    RoleCard("designer", "high",
             "You propose research questions and experiment designs grounded in prior human "
             "work; every design choice must cite where it comes from."),
    RoleCard("engineer", "low",
             "You set up environments, write and run code, and fix bugs."),
    RoleCard("analyst", "medium",
             "You analyse results, compute statistics and judge significance."),
    RoleCard("writer", "medium", "You write research reports and papers."),
    RoleCard("reviewer", "high",
             "You review work against the standards of the target venue and judge its value."),
)}

_PERMISSIONS = {
    "small": "You may edit files and install Python packages inside the workspace and download "
             "data. Do not use sudo, do not modify files outside the workspace, do not delete "
             "files you did not create.",
    "large": "You may additionally install system software and modify files outside the "
             "workspace; list every such change in your summary so it can be reverted.",
    "approval": "This order was approved by the user; the same rules as 'large' apply.",
}


def render_prompt(order: WorkOrder, *, resume: bool = False) -> str:
    role = ROLES[order.role]
    if resume:
        return (
            f"You were interrupted while working on work order {order.id}. Inspect the current "
            "workspace state, continue from where you stopped, and finish the order as "
            "originally specified. Reply with the same final JSON report.")
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
