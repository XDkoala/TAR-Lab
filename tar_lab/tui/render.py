"""Rendering: the live panel and status bar (prompt_toolkit) and scrollback items (Rich)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from prompt_toolkit.utils import get_cwidth
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tar_lab.core.charter import Charter
from tar_lab.core.ledger import Event, OrderState

from .activity import Step
from .state import MAIN_AGENT, AgentView, ProjectView

Fragments = list[tuple[str, str]]

ROLES = {"researcher": "调研员", "designer": "设计者", "engineer": "工程师",
         "analyst": "分析员", "writer": "写作者", "reviewer": "审稿人", "main": "主 Agent"}
PHASES = {"intake": "立项中", "finished": "已完成", "suspended": "已暂停",
          "approval": "等待你审批", "question": "等待你回答", "thinking": "主 Agent 思考中",
          "working": "工作中", "idle": "空闲"}
STATUSES = {"running": "运行中", "pending": "排队中", "interrupted": "中断待续接",
            "done": "完成", "failed": "失败", "needs_help": "需要帮助",
            "limit_exceeded": "超限", "crashed": "崩溃", "cancelled": "已取消",
            "thinking": "思考中"}
DETAIL_STEPS = 12
DEGRADED = {"quota": "额度用完", "rate_limited": "持续限流", "unavailable": "无法连接"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STEP_MARK = {"command": "$", "tool": "⚙", "edit": "✎", "note": "·", "search": "⌕"}


def role(name: str) -> str:
    return ROLES.get(name, name)


def clip(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` terminal cells (CJK characters take two)."""
    text = " ".join(text.split())
    if get_cwidth(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = get_cwidth(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f}m" if seconds >= 60 else f"{seconds:.0f}s"


def tokens(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def bar(fraction: float, cells: int = 10) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * cells)
    return "█" * filled + "░" * (cells - filled)


def local(ts: datetime) -> str:
    return ts.astimezone().strftime("%H:%M")


def step_line(step: Step) -> tuple[str, str]:
    style = {"running": "class:accent", "failed": "class:fail"}.get(step.status, "class:dim")
    return style, f"{STEP_MARK.get(step.kind, '·')} {step.text}"


# ---- live panel (prompt_toolkit fragments) ---------------------------------------------

def agent_lines(a: AgentView, tick: int, width: int, verbose: bool) -> Fragments:
    spin = SPINNER[tick % len(SPINNER)]
    head = f"{role(a.role)} · {a.id if a.id != MAIN_AGENT else ''}".rstrip(" ·")
    out: Fragments = [("class:accent", f" {spin} "), ("class:role", head),
                      ("class:dim", f" · {a.model}  ")]
    out.append(("class:ok" if a.status in ("running", "thinking") else "class:warn",
                STATUSES.get(a.status, a.status)))
    if a.limit:
        frac = a.elapsed / a.limit
        out += [("class:dim", "  "),
                ("class:warn" if frac > 0.8 else "class:dim", (f"{minutes(a.elapsed)}/"
                 f"{minutes(a.limit)} {bar(frac, 8)}"))]
    else:
        out.append(("class:dim", f"  {minutes(a.elapsed)}"))
    if a.attempt > 1:
        out.append(("class:warn", f"  第 {a.attempt} 次尝试"))
    if a.usage.total_tokens:
        out.append(("class:dim", f"  {tokens(a.usage.total_tokens)} tok"))
    if a.cost_usd:
        out.append(("class:dim", f" ≈${a.cost_usd:.2f}"))
    if a.gpu_mib:
        out.append(("class:dim", f"  显存 {a.gpu_mib / 1024:.1f}G"))
    out.append(("", "\n"))
    steps = a.steps[-6:] if verbose else a.steps[-1:]
    if not steps:
        out += [("class:dim", "   ⎿ 等待第一步…\n")]
    for s in steps:
        style, text = step_line(s)
        out += [("class:dim", "   ⎿ "), (style, clip(text, width - 6) + "\n")]
    return out


def panel(view: ProjectView | None, tick: int, width: int, verbose: bool,
          flash: str = "") -> Fragments:
    out: Fragments = []
    if view is not None:
        for a in view.agents:
            out += agent_lines(a, tick, width, verbose)
        if view.unread and not any(a.id == MAIN_AGENT for a in view.agents):
            busy = any(a.status == "running" for a in view.agents)
            out.append(("class:warn", (f" ✉ {view.unread} 条留言待主 Agent 处理"
                        f"{'（当前工单完成后读取）' if busy else ''}\n")))
    if flash:
        out.append(("class:warn", f" {flash}\n"))
    out += [("class:rule", "─" * width + "\n"), ("class:accent bold", "› ")]
    return out


def toolbar(view: ProjectView | None, runner_alive: bool, width: int) -> Fragments:
    out: Fragments = [("class:rule", "─" * width + "\n")]
    if view is None:
        return [*out, ("class:dim", " 加载中…")]
    phase_style = "class:warn" if view.phase in ("approval", "question", "suspended") else "class:ok"
    out += [(phase_style, f" {PHASES.get(view.phase, view.phase)}"),
            ("class:dim", (f" · ≈${view.spent_usd:.2f}/${view.budget_usd:g}"
             f" · {view.elapsed_h:.1f}h/{view.deadline_h:g}h"
             f" · 深搜 {view.deep_used}/{view.deep_max}"))]
    if view.undermind_calls or view.literature_calls:
        errors = f"，错 {view.undermind_errors}" if view.undermind_errors else ""
        out.append(("class:dim", f" · 文献 Undermind {view.undermind_calls}{errors}"
                    + (f" / 开放源 {view.literature_calls}" if view.literature_calls else "")))
    if view.degraded:
        label = DEGRADED.get(view.degraded, view.degraded)
        out.append(("class:warn", f" · Undermind {label}，已改用开放文献源"))
    if view.gpu is not None:
        out.append(("class:dim", (f" · GPU {view.gpu.util_percent}% "
                    f"{view.gpu.used_mib / 1024:.1f}/{view.gpu.total_mib / 1024:.0f}G")))
    if view.questions:
        out.append(("class:warn", f" · 待你回答 {len(view.questions)} 项（Ctrl+A）"))
    if not runner_alive and view.phase not in ("finished", "suspended", "intake"):
        out.append(("class:fail", " · 后台未运行（/run 启动）"))
    elif runner_alive:
        out.append(("class:dim", " · 后台运行中"))
    out.append(("class:dim", "  /help"))
    return out


# ---- scrollback (Rich) -----------------------------------------------------------------

def _sub(text: str, style: str = "grey62") -> Table:
    """An indented ``⎿`` line whose wrapped lines stay under the text."""
    t = Table.grid()
    t.add_column(width=4, no_wrap=True)
    t.add_column()
    t.add_row(Text("  ⎿ ", style="grey50"), Text(text, style=style))
    return t


def question_panel(qid: str, payload: Mapping[str, Any]) -> Panel:
    lines = [Text(str(payload.get("text", "")))]
    for o in payload.get("options", []):
        lines.append(Text(f"  • {o}", style="cyan"))
    if payload.get("default"):
        lines.append(Text(f"超时默认：{payload['default']}", style="grey62"))
    lines.append(Text("按 Ctrl+A 或输入 /approve 回答；也可以直接打字先和主 Agent 讨论。",
                      style="grey62"))
    title = "需要你审批" if payload.get("kind") == "approval" else "主 Agent 提问"
    return Panel(Group(*lines), title=f"{title} · {qid}", title_align="left",
                 border_style="yellow")


def feed_item(e: Event, orders: Mapping[str, OrderState]) -> RenderableType | None:
    p, oid = e.payload, e.order_id or ""
    state = orders.get(oid)
    who = role(state.order.role if state else oid.rsplit("-", 1)[-1])
    stamp = Text(f"  {local(e.ts)}", style="grey50")
    if e.kind == "user.message":
        t = Text("› ", style="bold grey70")
        t.append(str(p.get("text", "")), style="bold")
        if p.get("now"):
            t.append("  [紧急]", style="yellow")
        return t + stamp
    if e.kind == "pi.message":
        return Group(Text("● 主 Agent", style="bold cyan") + stamp,
                     Padding(Markdown(str(p.get("text", "")), hyperlinks=False), (0, 0, 0, 2)))
    if e.kind == "user.question":
        return question_panel(str(p["qid"]), p)
    if e.kind == "user.reply":
        how = "超时未回复，按默认处理" if p.get("auto") else "你已回答"
        return _sub(f"{how} {p['qid']}：{p['answer']}")
    if e.kind == "order.created":
        goal = str(p.get("order", {}).get("goal", ""))
        return Group(Text(f"● 派出 {who} · {oid}", style="bold") + stamp, _sub(clip(goal, 200)))
    if e.kind == "order.finished":
        r = p.get("result", {})
        ok = r.get("status") == "done"
        head = Text(f"● {who} · {oid} {STATUSES.get(r.get('status', ''), r.get('status'))}",
                    style="bold green" if ok else "bold red")
        head.append(f"（用时 {minutes(float(r.get('wall_seconds') or 0))}）", style="grey62")
        body = [_sub(clip(str(r.get("summary", "")), 400))]
        if r.get("error"):
            body.append(_sub(clip(str(r["error"]), 300), "red"))
        return Group(head + stamp, *body)
    if e.kind == "order.cancelled":
        return Text(f"● {who} · {oid} 已取消：{p.get('reason', '')}", style="yellow") + stamp
    if e.kind == "order.interrupted":
        return _sub(f"{who} · {oid} 被中断，后台重启后会续接", "yellow")
    if e.kind == "project.charter_changed":
        lines: list[RenderableType] = [Text("● 章程已修改", style="bold yellow") + stamp]
        lines += [_sub(f"{c['field']} → {c['value']}") for c in p.get("changes", [])]
        lines += [_sub(f"已拒绝：{c['field']} = {c['value']}", "red")
                  for c in p.get("rejected", [])]
        return Group(*lines)
    if e.kind == "project.suspended":
        return Text(f"● 项目已暂停：{p.get('reason', '')}  （/resume 继续）",
                    style="bold yellow") + stamp
    if e.kind == "project.resumed":
        return Text("● 项目已恢复", style="bold green") + stamp
    if e.kind == "project.finished":
        return Group(Text("● 项目完成", style="bold green") + stamp,
                     Padding(Markdown(str(p.get("summary", "")), hyperlinks=False), (0, 0, 0, 2)))
    if e.kind == "undermind.degraded":
        reason = DEGRADED.get(str(p.get("reason")), str(p.get("reason")))
        return Group(Text(f"● Undermind {reason}，已改用 OpenAlex / Semantic Scholar / arXiv",
                          style="bold yellow") + stamp,
                     _sub(clip(str(p.get("detail", "")), 300)))
    if e.kind == "lab.deadline":
        return Text("● 已到截止时间，主 Agent 开始收尾", style="bold yellow") + stamp
    if e.kind == "lab.error":
        return Text(f"● 主 Agent 出错（会重试）：{clip(str(p.get('error', '')), 300)}",
                    style="red") + stamp
    if e.kind == "notify.failed":
        return _sub(f"邮件发送失败：{clip(str(p.get('error', '')), 200)}", "red")
    return None


def charter_panel(c: Charter) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="grey62", no_wrap=True)
    t.add_column()
    for k, v in (("目标", c.goal), ("会议", c.target_venue), ("预算", f"${c.budget_usd:g}"),
                 ("期限", f"{c.deadline_hours:g} 小时"),
                 ("审批点", ", ".join(c.approval_points) or "无"),
                 ("超时", (f"{c.approval_timeout_hours:g} 小时后"
                  f"{'暂停' if c.on_timeout == 'suspend' else '按默认处理'}")),
                 ("深度检索", f"最多 {c.max_deep_searches} 次"), ("约束", c.constraints),
                 ("风险", c.risk_notes)):
        t.add_row(k, v)
    return Panel(t, title=c.title, title_align="left", border_style="cyan")


def orders_table(view: ProjectView, cost: Mapping[str, float | None]) -> Table:
    t = Table(box=None, padding=(0, 1), header_style="grey62")
    for col in ("工单", "角色", "状态", "用时", "token", "≈$", "结果"):
        t.add_column(col, no_wrap=col != "结果", overflow="fold")
    for oid, s in view.orders.items():
        c = cost.get(oid)
        t.add_row(oid, role(s.order.role), STATUSES.get(s.status, s.status),
                  f"{minutes(s.used_seconds)}/{minutes(s.order.limits.wall_seconds)}",
                  tokens(s.usage.total_tokens), f"{c:.2f}" if c else "-",
                  clip(s.result.summary, 60) if s.result else "")
    return t


def agent_detail(a: AgentView, state: OrderState | None, width: int = 100) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="grey62", no_wrap=True)
    t.add_column()
    t.add_row("执行者", f"{a.executor} · {a.model}")
    t.add_row("状态", f"{STATUSES.get(a.status, a.status)} · 第 {a.attempt} 次尝试 · "
              f"{minutes(a.elapsed)}/{minutes(a.limit)}")
    t.add_row("用量", "运行中不可见，结束后统计" if not a.usage.total_tokens else
              f"{tokens(a.usage.total_tokens)} token（缓存 "
              f"{tokens(a.usage.cached_input_tokens)}）"
              + (f" ≈${a.cost_usd:.2f}" if a.cost_usd else ""))
    t.add_row("目标", a.goal)
    if state is not None:
        t.add_row("交付物", "\n".join(state.order.deliverables) or "-")
        if state.result is not None:
            t.add_row("结果", state.result.summary)
            if state.result.deliverables:
                t.add_row("已交付", "\n".join(state.result.deliverables))
            if state.result.error:
                t.add_row("错误", Text(state.result.error, style="red"))
    steps = Text()
    for s in a.steps[-DETAIL_STEPS:]:
        style = {"running": "cyan", "failed": "red"}.get(s.status, "grey70")
        steps.append(f"{STEP_MARK.get(s.kind, '·')} {clip(s.text, width - 22)}\n", style=style)
    if a.steps:
        t.add_row(f"最近 {min(DETAIL_STEPS, len(a.steps))} 步", steps)
    return Panel(t, title=f"{role(a.role)} · {a.id}", title_align="left", border_style="cyan")
