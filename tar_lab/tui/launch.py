"""``tarlab`` without a subcommand: pick or create a project, then open the session."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Self

from prompt_toolkit.shortcuts import choice, prompt
from rich.console import Console
from rich.text import Text

from tar_lab.agents.main_agent import Question
from tar_lab.core.charter import Charter
from tar_lab.i18n import choose, set_language, tr
from tar_lab.orchestration import service
from tar_lab.orchestration.lab import Lab

from . import render
from .app import STYLE, Tui, _history_file
from .state import ProjectState
from .system import runner_pids, start_background

NEW = "\0new"


def find_projects(base: Path) -> list[Path]:
    found = [base] if (base / "ledger.sqlite").exists() else []
    for parent in (base, base / "projects"):
        if parent.is_dir():
            found += sorted(p.parent for p in parent.glob("*/ledger.sqlite"))
    return sorted(set(found), key=lambda p: (p / "ledger.sqlite").stat().st_mtime, reverse=True)


class _Spinner:
    """A Rich status line that can be paused while the user answers a question."""

    def __init__(self, console: Console, text: str) -> None:
        self.status = console.status(text, spinner="dots")

    def __enter__(self) -> Self:
        self.status.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.status.stop()


def _ask(console: Console, spinner: _Spinner) -> Callable[[Question], str]:
    def ask(q: Question) -> str:
        spinner.status.stop()
        try:
            if not q.options:
                return prompt(f"{q.text}\n[{tr('默认', 'default')}: {q.default}] › ", style=STYLE)
            options = [(o, o) for o in q.options] + [("\0custom", tr("自己输入…", "Type my own…"))]
            picked = choice(message=q.text, options=options,
                            default=q.default if q.default in q.options else None, style=STYLE)
            return prompt("› ", style=STYLE) if picked == "\0custom" else picked
        finally:
            spinner.status.update(tr("主 Agent 正在整理…", "The main agent is drafting…"))
            spinner.status.start()
    return ask


def create_project(console: Console, base: Path, lab_for: Callable[[Path], Lab],
                   init: Callable[[Path], None],
                   after_charter: Callable[[Lab, Charter], None]) -> Path | None:
    console.print(Text(tr("新建研究项目：用一句话说你想做什么，比如“帮我完成一篇世界模型的论文，"
                          "要求达到 CCF B”。",
                          "New research project: say in one sentence what you want, e.g. "
                          "\"write a world-model paper good enough for a CCF B venue\"."),
                       style="grey62"))
    idea = prompt(tr("想法 › ", "Idea › "), style=STYLE).strip()
    if not idea:
        return None
    default = f"projects/project-{datetime.now():%Y%m%d-%H%M}"
    where = prompt(tr("项目目录 › ", "Project directory › "), default=default, style=STYLE).strip() or default
    root = (base / where).resolve()
    if (root / "ledger.sqlite").exists():
        console.print(Text(tr(f"{root} 已经是一个项目。", f"{root} is already a project."), style="red"))
        return None
    init(root)
    lab = lab_for(root)

    with _Spinner(console, tr("主 Agent 正在准备立项问题…",
                                       "The main agent is preparing intake questions…")) as spinner:
        def confirm(charter: Charter) -> bool:
            spinner.status.stop()
            console.print(render.charter_panel(charter))
            return choice(message=tr("按这份章程立项？", "Start the project with this charter?"),
                          options=[("yes", tr("确认立项", "Start")), ("no", tr("取消", "Cancel"))],
                          style=STYLE) == "yes"
        charter = lab.start(idea, ask=_ask(console, spinner), confirm=confirm)
    if charter is None:
        console.print(Text(tr("未立项；再次运行 tarlab 可以重新开始。",
                              "Not started; run tarlab again to redo the intake."), style="grey62"))
        return None
    after_charter(lab, charter)
    return root


def main(project: Path | None, *, lab_for: Callable[[Path], Lab],
         models: Mapping[str, str], main_model: str, init: Callable[[Path], None],
         after_charter: Callable[[Lab, Charter], None], lang: str | None = None) -> int:
    set_language(choose(lang))
    console = Console(force_terminal=sys.stdout.isatty() or None, highlight=False)
    base = Path.cwd()
    if project is None:
        projects = find_projects(base)
        options = [(str(p), (f"{p.relative_to(base) if p.is_relative_to(base) else p}"
                    f"{tr('  · 后台运行中', '  · running') if runner_pids(p) else ''}")) for p in projects]
        try:
            picked = (choice(message=tr("打开哪个项目？", "Open which project?"),
                             options=[*options, (NEW, tr("新建项目…", "New project…"))],
                             style=STYLE) if options else NEW)
            project = (create_project(console, base, lab_for, init, after_charter)
                       if picked == NEW else Path(picked))
        except (KeyboardInterrupt, EOFError):
            return 130
        if project is None:
            return 1
        start = picked == NEW and choice(
            message=tr("现在在后台开始运行这个项目？", "Start running this project now?"),
            options=[("service", tr("开始，并设为常驻服务（崩溃或重启后自动恢复）",
                                    "Start as a service (restarts after a crash or reboot)")),
                     ("yes", tr("开始", "Start")),
                     ("no", tr("稍后用 /run 启动", "Later, with /run"))], style=STYLE)
        if start == "service":
            service.install(project)
        if start in ("service", "yes"):
            console.print(Text(start_background(project), style="grey62"))
    try:
        state = ProjectState(project, models, main_model)
    except FileNotFoundError as exc:
        console.print(Text(str(exc), style="red"))
        return 1
    charter = state.charter()
    set_language(choose(lang, charter.chat_language if charter else None))
    try:
        return Tui(state, lambda: lab_for(state.project.root), console,
                   history=_history_file()).run(patch=sys.stdout.isatty())
    finally:
        state.close()
