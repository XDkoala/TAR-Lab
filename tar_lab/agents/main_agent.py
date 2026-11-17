"""Main agent (PI): intake, charter, and event-driven decisions, all as strict JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tar_lab.core.charter import CHARTER_SCHEMA, Charter
from tar_lab.core.workorder import Usage

from .roles import ROLES


class JsonModel(Protocol):
    def ask(self, prompt: str, schema: dict[str, Any], *, cwd: Path, log_dir: Path,
            timeout: float = 900) -> tuple[dict[str, Any], Usage]: ...


def _obj(required: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": required}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

QUESTION_SCHEMA = _obj({"key": _STR, "text": _STR, "options": _STRS, "default": _STR})
INTAKE_SCHEMA = _obj({"questions": {"type": "array", "items": QUESTION_SCHEMA}})
ORDER_SCHEMA = _obj({
    "role": {"type": "string", "enum": sorted(ROLES)},
    "goal": _STR, "deliverables": _STRS, "acceptance": _STR,
    "minutes": {"type": "number"}, "inputs": _STRS,
})
ASK_SCHEMA = _obj({"kind": {"type": "string", "enum": ["question", "approval"]},
                   "text": _STR, "options": _STRS, "default": _STR})
DECISION_SCHEMA = _obj({
    "rationale": _STR,
    "orders": {"type": "array", "items": ORDER_SCHEMA},
    "ask_user": {"type": "array", "items": ASK_SCHEMA},
    "finish": {"type": "boolean"},
    "final_summary": _STR,
})

PI_BRIEF = """\
You are the main agent (principal investigator) of a TAR-Lab research project. You never do
the work yourself: you plan, dispatch work orders to specialist roles, review their results,
and decide what happens next. You are woken only when something needs your judgement.

Rules:
- Default research skeleton: survey -> candidate topics -> topic review -> experiment design
  -> experiments (baseline, method, ablations) -> analysis -> paper -> final review. You may
  go back (e.g. the topic turns out to be taken), skip steps the user already settled, or
  stop early.
- Keep every work order small and bounded: one clear deliverable, a realistic time limit, an
  explicit acceptance criterion. Never ask for polishing; prefer a working result now.
- If an order failed, stalled or overran, do not resend it unchanged: change the approach,
  narrow the scope, or lower the bar.
- Ground designs in prior human work: designers and engineers must cite papers or code.
- Respect the charter: deadline, budget, constraints, and approval points. Before moving past
  an approval point listed in the charter, ask the user with kind "approval" and include a
  safe default. Ask nothing else unless it truly blocks progress.
- Work that does not depend on an open question may continue while you wait for the answer.
- Work orders run inside `workspace/`; give them paths relative to it. The user expects:
  `reports/survey.md`, `reports/topic_report.md` (candidates, review, recommendation; this is
  what the user approves when "topic" is an approval point), code and `results/`,
  `reports/analysis.md`, `paper/main.pdf`, and `reports/final_review.md`.
- Review happens twice: a topic review right after the candidates, and a final review of
  the paper. Act on major review issues before finishing, within the remaining time.
- Write user-facing text in the charter's chat language.
- You may read project files to judge results, but never modify anything yourself.

Roles you can dispatch:
"""


def _roles() -> str:
    return "\n".join(f"- {r.name}: {r.brief}" for r in ROLES.values())


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    options: tuple[str, ...]
    default: str


@dataclass(frozen=True)
class Decision:
    rationale: str
    orders: tuple[dict[str, Any], ...]
    ask_user: tuple[dict[str, Any], ...]
    finish: bool
    final_summary: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        return cls(data["rationale"], tuple(data["orders"]), tuple(data["ask_user"]),
                   bool(data["finish"]), data["final_summary"])

    def to_dict(self) -> dict[str, Any]:
        return {"rationale": self.rationale, "orders": list(self.orders),
                "ask_user": list(self.ask_user), "finish": self.finish,
                "final_summary": self.final_summary}


@dataclass
class MainAgent:
    model: JsonModel
    root: Path  # project root; the agent may read it
    machine: str = ""  # hardware and compute facts, see ``tar_lab.core.machine``

    def _brief(self) -> str:
        machine = f"\n\nMachine:\n{self.machine}" if self.machine else ""
        return f"{PI_BRIEF}{_roles()}{machine}"

    def intake_questions(self, idea: str, log_dir: Path) -> tuple[list[Question], Usage]:
        prompt = f"""\
{self._brief()}

A user just started a project with this request:
<request>
{idea}
</request>

Ask at most 4 intake questions, only the ones whose answers change the plan the most
(typically: budget tier, deadline, who picks the topic, target venue, hard constraints such
as GPU memory or required simulators). Skip anything the request already answers. Every
question needs a default the user can accept by pressing Enter: `default` must repeat the
exact text of one of the `options` (never empty). For budget, offer 2-3 tiers in US dollars
of model and compute spend; for the deadline, 2-3 tiers in hours or days; say briefly what
each tier makes likely or unlikely. Write the questions in the user's language. `key` is a
short snake_case identifier."""
        data, usage = self.model.ask(prompt, INTAKE_SCHEMA, cwd=self.root, log_dir=log_dir)
        questions = []
        for q in data["questions"][:4]:
            options = tuple(q["options"])
            default = q["default"].strip()
            if options and default not in options:
                default = options[0]  # models sometimes leave the default blank
            questions.append(Question(q["key"], q["text"], options, default))
        return questions, usage

    def draft_charter(self, idea: str, answers: Sequence[tuple[Question, str]],
                      log_dir: Path) -> tuple[Charter, Usage]:
        qa = "\n".join(f"- {q.text}\n  answer: {a}" for q, a in answers) or "- (none)"
        prompt = f"""\
{self._brief()}

Turn this request and the user's intake answers into a project charter.
<request>
{idea}
</request>
Intake answers:
{qa}

Fill every field. `budget_usd` (US dollars) and `deadline_hours` are hard limits taken from
the answers. `constraints` lists only the user's research constraints (hardware, data,
environments, must-use or must-avoid methods), not your own working rules. `approval_points` lists
where the user must confirm (use only what the user asked for; if unsure, just "topic").
`risk_notes` states plainly, in the user's language, what this budget and deadline make
unlikely, and anything in the request that is unrealistic. `on_timeout` is what happens when
the user does not answer an approval in time: "default" takes the safe default, "suspend"
pauses the project."""
        data, usage = self.model.ask(prompt, CHARTER_SCHEMA, cwd=self.root, log_dir=log_dir)
        return Charter.from_dict({**data, "idea": idea}), usage

    def decide(self, context: str, log_dir: Path) -> tuple[Decision, Usage]:
        prompt = f"""\
{self._brief()}

Project state (from the ledger):
{context}

Decide the next step. Dispatch only orders that can start now; leave dependent work for a
later wake-up. Set `finish` only when the deliverables are in place or the project must stop;
then write `final_summary` for the user."""
        data, usage = self.model.ask(prompt, DECISION_SCHEMA, cwd=self.root, log_dir=log_dir)
        return Decision.from_dict(data), usage


def compact(data: Any, limit: int = 600) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"
