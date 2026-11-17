"""The interactive session: scrollback feed, live sub-agent panel, input line, status bar.

Like Claude Code, finished items (messages, order results, questions) are printed into the
terminal's own scrollback; only the live area at the bottom is redrawn. The interface never
runs the project itself: it reads the ledger and writes user events, so it can be closed and
reopened at any time while ``tarlab run`` keeps working in the background.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import choice, prompt
from prompt_toolkit.styles import Style
from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tar_lab.orchestration.lab import Lab

from . import render
from .state import ProjectState, ProjectView
from .system import GpuInfo, gpu_info, runner_pids, start_runner

STYLE = Style.from_dict({
    "accent": "ansicyan", "role": "bold", "dim": "#8a8a8a", "ok": "ansigreen",
    "warn": "ansiyellow", "fail": "ansired", "rule": "#4e4e4e",
    "bottom-toolbar": "noreverse bg:default", "bottom-toolbar.text": "",
})

COMMANDS: dict[str, str] = {
    "/help": "命令和快捷键",
    "/status": "项目章程与进度",
    "/agents": "所有工单（子 Agent）",
    "/agent": "某个子 Agent 的详情：/agent <工单号>",
    "/approve": "回答待审批的问题（Ctrl+A）",
    "/inbox": "列出待你回答的问题",
    "/now": "紧急留言，立刻叫醒主 Agent：/now <消息>",
    "/cost": "花费明细（按 API 标价估算）",
    "/reports": "列出报告",
    "/report": "查看报告：/report <文件名>",
    "/pause": "当前工单完成后暂停",
    "/stop": "立刻停止当前工单并暂停",
    "/resume": "恢复已暂停的项目",
    "/run": "在后台启动项目运行器",
    "/clear": "清屏",
    "/quit": "退出界面（项目继续在后台运行）",
}
REFRESH_SECONDS = 1.0
GPU_SECONDS = 5.0
HISTORY_ITEMS = 12


def _history_file() -> History:
    path = Path.home() / ".local" / "state" / "tar-lab" / "history"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(path))
    except OSError:
        return InMemoryHistory()


class TuiCompleter(Completer):
    def __init__(self, tui: Tui) -> None:
        self.tui = tui

    def get_completions(self, document: Document, complete_event: CompleteEvent
                        ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for name, meta in COMMANDS.items():
                if name.startswith(text):
                    yield Completion(name, -len(text), display_meta=meta)
            return
        if text.startswith("/") and text.count(" ") == 1:
            cmd, arg = text.split(" ")
            yield from (Completion(c, -len(arg), display_meta=m)
                        for c, m in self.tui.arguments(cmd) if c.startswith(arg))
            return
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("@"):
            yield from (Completion("@" + p, -len(word))
                        for p in self.tui.workspace_paths(word[1:]))


@dataclass
class Tui:
    state: ProjectState
    lab_factory: Callable[[], Lab]
    console: Console
    history: History | None = None
    refresh_seconds: float = REFRESH_SECONDS
    gpu: Callable[[], GpuInfo | None] = gpu_info
    runners: Callable[[Path], list[int]] = runner_pids

    def __post_init__(self) -> None:
        self.view: ProjectView | None = None
        self.verbose = False
        self.tick = 0
        self.flash = ""
        self._flash_until = 0.0
        self._seq = 0
        self._lab: Lab | None = None
        self._print_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu: GpuInfo | None = None
        self._gpu_at = 0.0
        self._runner_alive = False
        self._last_interrupt = 0.0
        self.session: PromptSession[Any] = PromptSession(
            message=self._message, bottom_toolbar=self._toolbar, style=STYLE,
            completer=TuiCompleter(self), complete_while_typing=True,
            history=self.history or InMemoryHistory(), key_bindings=self._bindings(),
            refresh_interval=0.5, erase_when_done=True, reserve_space_for_menu=0)

    # ---- data ---------------------------------------------------------------------------

    @property
    def lab(self) -> Lab:
        if self._lab is None:
            self._lab = self.lab_factory()
        return self._lab

    @property
    def root(self) -> Path:
        return self.state.project.root

    def refresh(self) -> None:
        now = time.monotonic()
        if now - self._gpu_at >= GPU_SECONDS:
            self._gpu, self._gpu_at = self.gpu(), now
            self._runner_alive = bool(self.runners(self.root))
        self.view = self.state.snapshot(self._gpu)
        fresh = [e for e in self.state.ledger.events() if e.seq > self._seq]
        if fresh:
            self._seq = fresh[-1].seq
            self.emit(*(r for e in fresh
                        if (r := render.feed_item(e, self.view.orders)) is not None))

    def _refresher(self) -> None:
        while not self._stop.wait(self.refresh_seconds):
            try:
                self.refresh()
                self.tick += 1
            except Exception as exc:  # noqa: BLE001 - a bad read must not kill the interface
                self.say_flash(f"刷新失败：{exc}")

    def arguments(self, cmd: str) -> list[tuple[str, str]]:
        view = self.view
        if view is None:
            return []
        if cmd == "/agent":
            return [(oid, f"{render.role(s.order.role)} · {render.STATUSES.get(s.status)}")
                    for oid, s in view.orders.items()]
        if cmd == "/approve":
            return [(qid, render.clip(str(q.get("text", "")), 40))
                    for qid, q in view.questions.items()]
        if cmd == "/report":
            return [(p, "") for p in self.reports()]
        return []

    def reports(self) -> list[str]:
        d = self.state.project.workspace / "reports"
        return sorted(str(p.relative_to(d)) for p in d.rglob("*.md")) if d.is_dir() else []

    def workspace_paths(self, prefix: str) -> list[str]:
        ws = self.state.project.workspace
        base = ws / prefix if prefix.endswith("/") else (ws / prefix).parent
        try:
            entries = sorted(base.iterdir())[:200]
        except OSError:
            return []
        out = []
        for p in entries:
            if p.name.startswith("."):
                continue
            rel = str(p.relative_to(ws)) + ("/" if p.is_dir() else "")
            if rel.startswith(prefix):
                out.append(rel)
        return out[:50]

    # ---- output -------------------------------------------------------------------------

    def emit(self, *items: RenderableType) -> None:
        with self._print_lock:
            for item in items:
                self.console.print(item)

    def say_flash(self, text: str, seconds: float = 4.0) -> None:
        self.flash, self._flash_until = text, time.monotonic() + seconds

    def _width(self) -> int:
        try:
            return get_app().output.get_size().columns - 1
        except Exception:  # noqa: BLE001 - no running app (e.g. while printing)
            return self.console.width - 1

    def _message(self) -> FormattedText:
        if self.flash and time.monotonic() > self._flash_until:
            self.flash = ""
        return FormattedText(render.panel(self.view, self.tick, self._width(), self.verbose,
                                          self.flash))

    def _toolbar(self) -> FormattedText:
        return FormattedText(render.toolbar(self.view, self._runner_alive, self._width()))

    # ---- keys ---------------------------------------------------------------------------

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()
        has_menu = Condition(lambda: get_app().current_buffer.complete_state is not None)

        @kb.add("escape", filter=has_menu, eager=True)
        def _close_menu(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        @kb.add("escape", filter=~has_menu, eager=True)
        def _urgent(event: KeyPressEvent) -> None:
            text = event.current_buffer.text.strip()
            if text:
                event.app.exit(result=("urgent", text))
            else:
                self.say_flash("先输入消息，再按 Esc 紧急发送（立刻叫醒主 Agent）")

        @kb.add("c-a")
        def _approve(event: KeyPressEvent) -> None:
            event.app.exit(result=("command", "/approve"))

        @kb.add("c-o")
        def _toggle(event: KeyPressEvent) -> None:
            self.verbose = not self.verbose

        @kb.add("c-j")
        def _newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("enter")
        def _enter(event: KeyPressEvent) -> None:
            b = event.current_buffer
            if b.complete_state and b.complete_state.current_completion:
                b.apply_completion(b.complete_state.current_completion)
                return
            if b.document.text_before_cursor.endswith("\\"):
                b.delete_before_cursor()
                b.insert_text("\n")
                return
            b.validate_and_handle()

        return kb

    # ---- main loop ----------------------------------------------------------------------

    def header(self) -> RenderableType:
        view = self.view
        t = Text()
        t.append("TAR-Lab", style="bold cyan")
        t.append(f"  {view.title if view else self.root.name}\n", style="bold")
        t.append(f"{self.root}\n", style="grey62")
        t.append("直接输入和主 Agent 对话 · / 命令 · @ 引用文件 · Esc 紧急发送 · "
                 "Ctrl+A 审批 · Ctrl+O 展开子 Agent · Ctrl+J 换行", style="grey62")
        return Panel(t, border_style="cyan", expand=False)

    def start(self) -> None:
        self.view = self.state.snapshot()
        events = self.state.ledger.events()
        self._seq = events[-1].seq if events else 0
        self.emit(self.header())
        history = [r for e in events if (r := render.feed_item(e, self.view.orders)) is not None]
        if len(history) > HISTORY_ITEMS:
            self.emit(Text(f"… 更早的 {len(history) - HISTORY_ITEMS} 条记录用 /status、/agents "
                           "查看", style="grey50"))
        self.emit(*history[-HISTORY_ITEMS:])
        self._gpu_at = 0.0
        self.refresh()
        self._thread = threading.Thread(target=self._refresher, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Stop the refresher; the ledger must not be closed while it is reading."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def run(self, patch: bool = True) -> int:
        self.start()
        try:
            if patch:
                with patch_stdout(raw=True):
                    self._loop()
            else:
                self._loop()
        finally:
            self.close()
        alive = bool(self.runners(self.root))
        self.console.print(Text("已退出界面。" + ("项目在后台继续运行，随时用 tarlab 重新打开。"
                                                 if alive else ""), style="grey62"))
        return 0

    def _loop(self) -> None:
        while True:
            try:
                result = self.session.prompt()
            except KeyboardInterrupt:
                if time.monotonic() - self._last_interrupt < 2.0:
                    return
                self._last_interrupt = time.monotonic()
                self.say_flash("再按一次 Ctrl+C 退出界面（项目不会停止）", 2.0)
                continue
            except EOFError:
                return
            if isinstance(result, tuple):
                kind, text = result
                if kind == "urgent":
                    self.send(text, now=True)
                    continue
                result = text
            if not self.handle(str(result)):
                return

    # ---- actions ------------------------------------------------------------------------

    def handle(self, text: str) -> bool:
        """Process one input line; returns False to quit."""
        text = text.strip()
        if not text:
            return True
        if not text.startswith("/"):
            urgent = text.endswith("!") and not text.endswith("\\!")
            self.send(text[:-1].rstrip() if urgent else text, now=urgent)
            return True
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd in ("/quit", "/exit", "/q"):
            return False
        handler = getattr(self, "cmd_" + cmd[1:].replace("-", "_"), None)
        if handler is None or cmd not in COMMANDS:
            self.emit(Text(f"未知命令 {cmd}，输入 /help 查看命令", style="red"))
            return True
        try:
            handler(arg)
        except (KeyError, RuntimeError, OSError, ValueError) as exc:
            self.emit(Text(f"{cmd} 失败：{exc}", style="red"))
        return True

    def send(self, text: str, *, now: bool) -> None:
        if not text:
            return
        self.lab.say(text, now=now)
        self.refresh()
        busy = bool(self.view and any(a.status == "running" for a in self.view.agents))
        if not self._runner_alive:
            note = "已记录。后台未运行，用 /run 启动后主 Agent 才会处理。"
        elif now:
            note = "已紧急发送，正在叫醒主 Agent；当前工单继续运行，由它决定是否停下。"
        elif busy:
            note = "已发送，主 Agent 会在当前工单完成后读到。急的话用 Esc 或在末尾加 ! 紧急发送。"
        else:
            note = "已发送，主 Agent 马上会读到。"
        self.emit(render._sub(note))

    def _choose(self, message: str, options: list[tuple[str, str]],
                default: str | None = None) -> str | None:
        try:
            return choice(message=message, options=options, default=default, style=STYLE)
        except KeyboardInterrupt:
            return None

    def cmd_help(self, _: str) -> None:
        t = Table(box=None, padding=(0, 2), show_header=False)
        t.add_column(style="cyan", no_wrap=True)
        t.add_column()
        for name, meta in COMMANDS.items():
            t.add_row(name, meta)
        for key, meta in (("Enter", "发送（主 Agent 在当前工单完成后读到）"),
                          ("Esc / 末尾加 !", "紧急发送，立刻叫醒主 Agent"),
                          ("Ctrl+A", "回答待审批的问题"), ("Ctrl+O", "展开/收起子 Agent 最近步骤"),
                          ("Ctrl+J 或 \\ + Enter", "换行"), ("@路径", "引用工作区里的文件"),
                          ("↑ / ↓", "历史输入"), ("Ctrl+C 两次 / Ctrl+D", "退出界面")):
            t.add_row(key, meta)
        self.emit(t)

    def cmd_status(self, _: str) -> None:
        charter = self.lab.charter()
        if charter is not None:
            self.emit(render.charter_panel(charter))
        if self.view is not None:
            self.emit(Text(f"阶段：{render.PHASES.get(self.view.phase, self.view.phase)}"),
                      render.orders_table(self.view, self._costs()))

    def _costs(self) -> dict[str, float | None]:
        assert self.view is not None
        return {oid: self.state.order_cost(s) for oid, s in self.view.orders.items()}

    def cmd_agents(self, _: str) -> None:
        if self.view is not None:
            self.emit(render.orders_table(self.view, self._costs()))

    def cmd_agent(self, arg: str) -> None:
        assert self.view is not None
        if not arg:
            running = [a.id for a in self.view.agents]
            arg = running[-1] if running else ""
        state = self.view.orders.get(arg)
        if state is None:
            raise KeyError(f"没有工单 {arg or '（无运行中的工单）'}，用 /agents 查看")
        self.emit(render.agent_detail(self.state.order_view(arg, state, self._gpu), state,
                                      self.console.width))

    def cmd_inbox(self, _: str) -> None:
        assert self.view is not None
        if not self.view.questions:
            self.emit(Text("没有待你回答的问题。", style="grey62"))
        self.emit(*(render.question_panel(q, p) for q, p in self.view.questions.items()))

    def cmd_approve(self, arg: str) -> None:
        self.refresh()
        assert self.view is not None
        questions = self.view.questions
        if not questions:
            self.emit(Text("没有待你回答的问题。", style="grey62"))
            return
        qid = arg or next(iter(questions))
        if not arg and len(questions) > 1:
            qid = self._choose("回答哪个问题？", [(q, f"{q}  {render.clip(str(p['text']), 60)}")
                                              for q, p in questions.items()]) or ""
        if qid not in questions:
            raise KeyError(f"没有待回答的问题 {qid}")
        q = questions[qid]
        options = [(o, o) for o in q.get("options", [])]
        options += [("\0custom", "自己输入回答…"), ("\0discuss", "先和主 Agent 讨论（暂不回答）"),
                    ("\0cancel", "取消")]
        picked = self._choose(f"{qid} · {q['text']}", options)
        if picked in (None, "\0cancel"):
            return
        if picked == "\0discuss":
            self.emit(render._sub("直接输入你的问题或意见发给主 Agent；审批会一直等你回答。"))
            return
        if picked == "\0custom":
            try:
                picked = prompt("你的回答 › ", style=STYLE).strip()
            except (KeyboardInterrupt, EOFError):
                return
            if not picked:
                return
        self.lab.reply(qid, picked)

    def cmd_now(self, arg: str) -> None:
        self.send(arg, now=True)

    def cmd_cost(self, _: str) -> None:
        assert self.view is not None
        t = Table(box=None, padding=(0, 2), header_style="grey62")
        t.add_column("项")
        t.add_column("token", justify="right")
        t.add_column("≈ 美元", justify="right")
        by_role: dict[str, list[float]] = {}
        for s in self.view.orders.values():
            row = by_role.setdefault(render.role(s.order.role), [0.0, 0.0])
            row[0] += s.usage.total_tokens
            row[1] += self.state.order_cost(s) or 0.0
        for name, (tok, usd) in by_role.items():
            t.add_row(name, render.tokens(int(tok)), f"{usd:.2f}")
        main = self.view.spent_usd - sum(v[1] for v in by_role.values())
        t.add_row("主 Agent（立项与决策）", "", f"{main:.2f}")
        t.add_row(Text("合计", style="bold"), "",
                  Text(f"{self.view.spent_usd:.2f} / {self.view.budget_usd:g}", style="bold"))
        self.emit(t, Text("按 API 标价估算。走订阅时不另外付费，只消耗订阅额度；运行中的工单"
                          "结束后才计入。", style="grey62"))

    def cmd_reports(self, _: str) -> None:
        names = self.reports()
        self.emit(Text("\n".join(names) or "还没有报告。", style="cyan" if names else "grey62"))

    def cmd_report(self, arg: str) -> None:
        if not arg:
            self.cmd_reports("")
            return
        d = self.state.project.workspace / "reports"
        path = (d / arg).resolve()
        if not path.is_relative_to(d.resolve()) or not path.is_file():
            raise KeyError(f"没有报告 {arg}，用 /reports 查看")
        text = path.read_text(errors="replace")
        limit = 30000
        self.emit(Panel(Markdown(text[:limit]), title=arg, title_align="left",
                        border_style="grey50"))
        if len(text) > limit:
            self.emit(render._sub(f"只显示了前 {limit} 个字符，全文：{path}"))

    def cmd_pause(self, _: str) -> None:
        self.lab.pause()
        self.emit(render._sub("当前工单完成后暂停。"))

    def cmd_stop(self, _: str) -> None:
        if self._choose("立刻停止正在运行的工单并暂停项目？",
                        [("yes", "停止"), ("no", "取消")], "no") == "yes":
            stopped = self.lab.stop()
            self.emit(render._sub(f"已停止 {', '.join(stopped) or '（无运行中的工单）'}，项目将暂停。"))

    def cmd_resume(self, _: str) -> None:
        self.lab.resume()
        self.emit(render._sub("已恢复。"))
        if not self.runners(self.root):
            self.cmd_run("")

    def cmd_run(self, _: str) -> None:
        pids = self.runners(self.root)
        if pids:
            self.emit(render._sub(f"后台已在运行（PID {', '.join(map(str, pids))}）。"))
            return
        pid, log = start_runner(self.root)
        self._runner_alive = True
        self.emit(render._sub(f"已在后台启动（PID {pid}），日志：{log}"))

    def cmd_clear(self, _: str) -> None:
        self.console.clear()
