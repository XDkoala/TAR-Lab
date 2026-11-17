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
from tar_lab.orchestration.lab import Lab

from . import render
from .app import STYLE, Tui, _history_file
from .state import ProjectState
from .system import runner_pids, start_runner

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
                return prompt(f"{q.text}\n[默认：{q.default}] › ", style=STYLE)
            options = [(o, o) for o in q.options] + [("\0custom", "自己输入…")]
            picked = choice(message=q.text, options=options,
                            default=q.default if q.default in q.options else None, style=STYLE)
            return prompt("› ", style=STYLE) if picked == "\0custom" else picked
        finally:
            spinner.status.update("主 Agent 正在整理…")
            spinner.status.start()
    return ask


def create_project(console: Console, base: Path, lab_for: Callable[[Path], Lab],
                   init: Callable[[Path], None],
                   after_charter: Callable[[Lab, Charter], None]) -> Path | None:
    console.print(Text("新建研究项目：用一句话说你想做什么，比如“帮我完成一篇世界模型的论文，"
                       "要求达到 CCF B”。", style="grey62"))
    idea = prompt("想法 › ", style=STYLE).strip()
    if not idea:
        return None
    default = f"projects/project-{datetime.now():%Y%m%d-%H%M}"
    where = prompt("项目目录 › ", default=default, style=STYLE).strip() or default
    root = (base / where).resolve()
    if (root / "ledger.sqlite").exists():
        console.print(Text(f"{root} 已经是一个项目。", style="red"))
        return None
    init(root)
    lab = lab_for(root)

    with _Spinner(console, "主 Agent 正在准备立项问题…") as spinner:
        def confirm(charter: Charter) -> bool:
            spinner.status.stop()
            console.print(render.charter_panel(charter))
            return choice(message="按这份章程立项？",
                          options=[("yes", "确认立项"), ("no", "取消")], style=STYLE) == "yes"
        charter = lab.start(idea, ask=_ask(console, spinner), confirm=confirm)
    if charter is None:
        console.print(Text("未立项；再次运行 tarlab 可以重新开始。", style="grey62"))
        return None
    after_charter(lab, charter)
    return root


def main(project: Path | None, *, lab_for: Callable[[Path], Lab],
         models: Mapping[str, str], main_model: str, init: Callable[[Path], None],
         after_charter: Callable[[Lab, Charter], None]) -> int:
    console = Console(force_terminal=sys.stdout.isatty() or None, highlight=False)
    base = Path.cwd()
    if project is None:
        projects = find_projects(base)
        options = [(str(p), (f"{p.relative_to(base) if p.is_relative_to(base) else p}"
                    f"{'  · 后台运行中' if runner_pids(p) else ''}")) for p in projects]
        try:
            picked = (choice(message="打开哪个项目？", options=[*options, (NEW, "新建项目…")],
                             style=STYLE) if options else NEW)
            project = (create_project(console, base, lab_for, init, after_charter)
                       if picked == NEW else Path(picked))
        except (KeyboardInterrupt, EOFError):
            return 130
        if project is None:
            return 1
        if picked == NEW and choice(message="现在在后台开始运行这个项目？",
                                    options=[("yes", "开始"), ("no", "稍后用 /run 启动")],
                                    style=STYLE) == "yes":
            pid, log = start_runner(project)
            console.print(Text(f"已在后台启动（PID {pid}），日志：{log}", style="grey62"))
    try:
        state = ProjectState(project, models, main_model)
    except FileNotFoundError as exc:
        console.print(Text(str(exc), style="red"))
        return 1
    try:
        return Tui(state, lambda: lab_for(state.project.root), console,
                   history=_history_file()).run(patch=sys.stdout.isatty())
    finally:
        state.close()
