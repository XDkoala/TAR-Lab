"""Host tools each stage needs, checked before the stage starts (``tarlab doctor``).

A missing tool used to surface only when a late stage hit it (FIX-002: ``pdftotext`` missing
at the paper stage, after the experiments had run). Each requirement names the roles that
need it; the lab checks a role's requirements when it dispatches an order for that role and
rejects the order with the tool, the stage and the fix instead of letting it fail midway.
Requirements of other stages never block an order, so a project without a paper stage is not
held up by a missing TeX installation. Optional requirements only warn.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

PAPER_ROLES = frozenset({"writer", "reviewer"})
FIGURE_ROLES = frozenset({"analyst", "writer", "reviewer"})
ALL_ROLES = frozenset({"researcher", "designer", "engineer", "analyst", "writer", "reviewer"})


@dataclass(frozen=True)
class Requirement:
    name: str
    roles: frozenset[str]
    check: Callable[[], str | None]  # None when satisfied, else what is wrong
    fix: str
    optional: bool = False


@dataclass(frozen=True)
class Finding:
    requirement: Requirement
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.problem is None

    def line(self) -> str:
        r = self.requirement
        stages = "all stages" if r.roles == ALL_ROLES else ", ".join(sorted(r.roles))
        state = "ok" if self.ok else ("WARN" if r.optional else "MISSING")
        text = f"{state:7} {r.name} ({stages})"
        return text if self.ok else f"{text}: {self.problem}. Fix: {r.fix}"


def _which(tool: str) -> Callable[[], str | None]:
    return lambda: None if shutil.which(tool) else f"`{tool}` not on PATH"


def _codex() -> str | None:
    from tar_lab.integrations.cli_agents.codex import find_codex

    try:
        find_codex()
    except FileNotFoundError as exc:
        return str(exc)
    return None


def _devin() -> str | None:
    from tar_lab.integrations.cli_agents.devin import clean_env, find_devin

    try:
        binary = find_devin()
    except FileNotFoundError as exc:
        return str(exc)
    try:
        out = subprocess.run([binary, "auth", "status"], capture_output=True, text=True,
                             env=clean_env(), timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"`devin auth status` failed: {exc}"
    return None if "Logged in" in out.stdout else "the devin CLI is not logged in"


def _tex() -> str | None:
    from tar_lab.tools.tex import select_engine

    try:
        engine = select_engine("main.tex")
    except FileNotFoundError as exc:
        return str(exc)
    return None if engine.name != "tectonic" else (
        "only Tectonic found: fonts and layout can differ from pdflatex venues")


def _figures_env() -> str | None:
    from tar_lab.figures.build import figures_python

    return None if figures_python() else "the figures environment is missing"


def _arxiv_texlive() -> str | None:
    from tar_lab.tools.arxiv import find_arxiv_texlive_bin

    return None if find_arxiv_texlive_bin() else "arXiv's TeX Live 2025 is not installed"


def requirements(executors: Iterable[str] = ("codex",),
                 roles_on: Mapping[str, frozenset[str]] | None = None) -> list[Requirement]:
    """What the configured executors and the stages need. ``roles_on`` maps an executor to the
    roles that run on it (default: every role)."""
    on = roles_on or {}
    reqs = [Requirement("git", ALL_ROLES, _which("git"),
                        "install git (workspace snapshots need it)")]
    used = set(executors)
    if "codex" in used:
        reqs.append(Requirement("codex CLI", on.get("codex", ALL_ROLES), _codex,
                                "install Codex and log in, or set TARLAB_CODEX_BIN"))
    if "devin" in used:
        reqs.append(Requirement("devin CLI, logged in", on.get("devin", ALL_ROLES), _devin,
                                "install Devin and run `devin auth login`, set "
                                "TARLAB_DEVIN_BIN, or move its tier to codex with "
                                "TARLAB_TIERS=high=codex"))
    reqs += [
        Requirement("TeX Live (latexmk, pdflatex)", PAPER_ROLES, _tex,
                    "install TeX Live into ../.tools/texlive (README) or put latexmk and "
                    "pdflatex on PATH"),
        *(Requirement(f"poppler {tool}", PAPER_ROLES, _which(tool),
                      "install poppler-utils (e.g. `apt install poppler-utils`)")
          for tool in ("pdftotext", "pdffonts", "pdfinfo", "pdfimages", "pdftoppm")),
        Requirement("figures environment", FIGURE_ROLES, _figures_env,
                    "run `tarlab figures setup`"),
        Requirement("Graphviz dot", frozenset({"writer"}), _which("dot"),
                    "install graphviz, or draw diagrams in TikZ (.tex)", optional=True),
        Requirement("arXiv TeX Live 2025", PAPER_ROLES, _arxiv_texlive,
                    "install it into ../.tools/texlive-arxiv (README); needed only for "
                    "`tex check --arxiv`", optional=True),
    ]
    return reqs


def run(reqs: Iterable[Requirement], role: str | None = None) -> list[Finding]:
    """Check ``reqs`` (only those ``role`` needs, when given)."""
    return [Finding(r, r.check()) for r in reqs if role is None or role in r.roles]


def blocking(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if not f.ok and not f.requirement.optional]
