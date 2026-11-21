"""Main agent (PI): intake, charter, and event-driven decisions, all as strict JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tar_lab.core.charter import APPROVAL_POINTS, CHARTER_SCHEMA, Charter
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
# Reviewer orders: topic and final reviews run on the high tier, interim ones on the medium.
REVIEW_KINDS = ("topic", "interim", "final")
ORDER_SCHEMA = _obj({
    "role": {"type": "string", "enum": sorted(ROLES)},
    "goal": _STR, "deliverables": _STRS, "acceptance": _STR,
    "minutes": {"type": "number"}, "inputs": _STRS,
    "kind": {"type": "string", "enum": ["work", "engine", "final_eval"]},
    "task_dir": _STR,
    "review": {"type": "string", "enum": ["none", *REVIEW_KINDS]},
})
ASK_SCHEMA = _obj({"kind": {"type": "string", "enum": ["question", "approval"]},
                   "point": {"type": "string", "enum": ["none", *APPROVAL_POINTS]},
                   "text": _STR, "options": _STRS, "default": _STR})
CHARTER_FIELDS = ("budget_usd", "deadline_hours", "approval_points", "target_venue",
                  "constraints", "on_timeout", "approval_timeout_hours", "max_deep_searches",
                  "chat_language", "report_language", "paper_language")
CHANGE_SCHEMA = _obj({"field": {"type": "string", "enum": list(CHARTER_FIELDS)},
                      "value": _STR})
DECISION_SCHEMA = _obj({
    "rationale": _STR,
    "message_to_user": _STR,
    "orders": {"type": "array", "items": ORDER_SCHEMA},
    "cancel_orders": _STRS,
    "ask_user": {"type": "array", "items": ASK_SCHEMA},
    "charter_changes": {"type": "array", "items": CHANGE_SCHEMA},
    "finish": {"type": "boolean"},
    "final_summary": _STR,
})
# The dispatcher's answer: a decision plus why the main agent must take over ("" if not).
DISPATCH_SCHEMA = _obj({**DECISION_SCHEMA["properties"], "escalate": _STR})

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
- Orders run in parallel: up to three at once, engineer orders (which may use the GPU) one at
  a time. Orders dispatched together must not depend on each other. You are woken whenever
  one finishes, also while others are still running.
- The budget is enforced: at 80 % the user is warned; at 100 % new orders are held and the
  platform asks the user whether to raise it.
- Work orders run inside `workspace/`; give them paths relative to it. The user expects:
  `reports/survey.md`, `reports/topic_report.md` (candidates, review, recommendation; this is
  what the user approves when "topic" is an approval point), code and `results/`,
  `reports/analysis.md`, `paper/main.pdf`, and `reports/final_review.md`.
- Review happens twice: a topic review right after the candidates, and a final review of
  the paper. Act on major review issues before finishing, within the remaining time. Tag
  every reviewer order with `review`: "topic" (candidates and topic report), "final" (the
  paper and `reports/final_review.md`), or "interim" for anything in between (experiment
  designs, intermediate results, drafts), which runs on a cheaper model. Other roles take
  "none".
- Before asking for topic approval, dispatch a design check for the recommended candidate:
  an engineer order (at most 15 minutes, no training, no downloads over 1 GB) that shows on
  real or estimated numbers that the compared arms actually run different things. It is part
  of topic review, not an experiment, so it may run before approval; put its result in the
  topic report. If the check fails, redesign before asking the user.
- Also before topic approval: the novelty of the recommended candidate must rest on at least
  one Undermind deep search on its closest prior work (a researcher or reviewer order), and
  a writer order must write `reports/topic_summary.md`, the plain-language page the user
  reads first. Tag each approval question with the charter approval point it gates in
  `point` ("none" otherwise). The platform puts the summary at the top of a "topic" question
  and holds the question back, waking you with the reason, while either is missing.
- Order kinds (`kind`): "work" is a normal order for the role. "engine" runs the
  AI-Scientist-v3 engine on `task_dir`: coding agents propose ideas, implement them and keep
  the program that scores best on the task's evaluator, for `minutes` (about one idea per 10
  minutes). Use it for sub-problems with an automatic score worth searching over (a method
  variant, an architecture or hyperparameter search, beating a baseline on a benchmark), not
  for analysis questions without a score to improve. First dispatch an engineer "work" order
  that builds the task directory (see the engineer's guide); the platform rejects an engine
  order whose task is incomplete. Engine runs execute on this machine like other orders
  (GPU included, one GPU order at a time); the engine scores on its own evaluator, so the result is a candidate for the final evaluation, not a
  final number. Leave `task_dir` empty for other kinds.
- Sealed final evaluation: the plan fixes, before development starts, a held-out split or
  seeds and a protocol that development never touches. When the candidate methods are
  frozen, an engineer writes `eval/final/protocol.md`, the script and `eval/final/frozen.txt`
  (paths of the code, configs and checkpoints to evaluate); then dispatch a "final_eval"
  order. The platform fingerprints all of it, the order runs the protocol once into
  `results/final/<order id>/`, and any change to sealed files is reported. Headline numbers
  in the paper come only from there. A second final evaluation needs the user's approval
  (the platform asks) and is disclosed; prefer reporting the first one honestly.
- If the research direction fails, scale the recovery to the remaining time and budget:
  explore two or three alternative directions in parallel (separate designer orders),
  review them, and offer the user a choice. Giving up on the charter's goal is the user's
  decision, not yours.
- `finish` does not end the project by itself: the platform asks the user to confirm. Write
  `final_summary` as that request: what was delivered against the charter's goal and, if the
  goal (e.g. a submittable paper) is not met, concrete options to continue with estimated
  hours and dollars. If the user declines, their reply wakes you to continue.
- Write user-facing text in the charter's chat language.
- You may read project files to judge results, but never modify anything yourself.
- The user can message you at any time ("Messages from the user" in the state). Answer every
  new message in `message_to_user` (empty string when there is nothing to say) and act on
  it: change plans, cancel running or queued orders by id in `cancel_orders` when they no
  longer make sense, or keep them and say why.
- Change the charter only through `charter_changes`, and only after the user has explicitly
  confirmed that exact change (ask with kind "approval" first and restate old -> new). For
  `approval_points` give a comma-separated list.

Roles you can dispatch:
"""


DISPATCHER_NOTE = """\
You are the dispatcher: the main agent's assistant for routine steps, using the same rules.
The platform judged this wake-up routine (work orders finished normally). Carry on with the
current plan: dispatch the orders it calls for next, or wait. `message_to_user` may hold a
short progress note. You may not ask the user, change the charter, cancel orders, dispatch a
final evaluation, or finish. If the step needs any of that, or needs judgement (results that
look wrong or surprising or change the plan, compared arms that may not differ, anything you
are unsure about), set `escalate` to the reason in one sentence and leave the rest empty; the
main agent then decides. Otherwise set `escalate` to an empty string."""


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
    message_to_user: str = ""
    cancel_orders: tuple[str, ...] = ()
    charter_changes: tuple[dict[str, str], ...] = ()
    escalate: str = ""  # dispatcher only

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        return cls(data["rationale"], tuple(data["orders"]), tuple(data["ask_user"]),
                   bool(data["finish"]), data["final_summary"],
                   data.get("message_to_user", ""), tuple(data.get("cancel_orders", ())),
                   tuple(data.get("charter_changes", ())), data.get("escalate", ""))

    def to_dict(self) -> dict[str, Any]:
        return {"rationale": self.rationale, "message_to_user": self.message_to_user,
                "orders": list(self.orders), "cancel_orders": list(self.cancel_orders),
                "ask_user": list(self.ask_user), "charter_changes": list(self.charter_changes),
                "finish": self.finish, "final_summary": self.final_summary}


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

    def decide(self, context: str, log_dir: Path, *,
               dispatcher: bool = False) -> tuple[Decision, Usage]:
        note = f"\n\n{DISPATCHER_NOTE}" if dispatcher else ""
        prompt = f"""\
{self._brief()}{note}

Project state (from the ledger):
{context}

Decide the next step. Dispatch only orders that can start now; leave dependent work for a
later wake-up. Set `finish` only when the deliverables are in place or you propose to stop;
then write `final_summary` for the user, who must confirm."""
        schema = DISPATCH_SCHEMA if dispatcher else DECISION_SCHEMA
        data, usage = self.model.ask(prompt, schema, cwd=self.root, log_dir=log_dir)
        return Decision.from_dict(data), usage


def compact(data: Any, limit: int = 600) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"
