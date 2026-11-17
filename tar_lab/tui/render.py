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
from tar_lab.i18n import Terms, tr

from .activity import Step
from .state import MAIN_AGENT, AgentView, ProjectView

Fragments = list[tuple[str, str]]

ROLES = Terms({"researcher": ("调研员", "researcher"), "designer": ("设计者", "designer"),
               "engineer": ("工程师", "engineer"), "analyst": ("分析员", "analyst"),
               "writer": ("写作者", "writer"), "reviewer": ("审稿人", "reviewer"),
               "main": ("主 Agent", "main agent")})
PHASES = Terms({"intake": ("立项中", "intake"), "finished": ("已完成", "finished"),
                "suspended": ("已暂停", "suspended"), "approval": ("等待你审批", "awaiting approval"),
                "question": ("等待你回答", "awaiting your answer"),
                "thinking": ("主 Agent 思考中", "main agent thinking"),
                "working": ("工作中", "working"), "idle": ("空闲", "idle")})
STATUSES = Terms({"running": ("运行中", "running"), "pending": ("排队中", "queued"),
                  "interrupted": ("中断待续接", "interrupted"), "done": ("完成", "done"),
                  "failed": ("失败", "failed"), "needs_help": ("需要帮助", "needs help"),
                  "limit_exceeded": ("超限", "over limit"), "crashed": ("崩溃", "crashed"),
                  "cancelled": ("已取消", "cancelled"), "thinking": ("思考中", "thinking")})
DETAIL_STEPS = 12
DEGRADED = Terms({"quota": ("额度用完", "out of quota"),
                  "rate_limited": ("持续限流", "rate limited"),
                  "unavailable": ("无法连接", "unreachable")})
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
        out.append(("class:warn", tr(f"  第 {a.attempt} 次尝试", f"  attempt {a.attempt}")))
    if a.usage.total_tokens:
        out.append(("class:dim", f"  {tokens(a.usage.total_tokens)} tok"))
    if a.cost_usd:
        out.append(("class:dim", f" ≈${a.cost_usd:.2f}"))
    if a.gpu_mib:
        out.append(("class:dim", tr(f"  显存 {a.gpu_mib / 1024:.1f}G", f"  VRAM {a.gpu_mib / 1024:.1f}G")))
    out.append(("", "\n"))
    steps = a.steps[-6:] if verbose else a.steps[-1:]
    if not steps:
        out += [("class:dim", tr("   ⎿ 等待第一步…\n", "   ⎿ waiting for the first step…\n"))]
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
            later = tr("（当前工单完成后读取）", " (read when the current order ends)")
            out.append(("class:warn", tr(f" ✉ {view.unread} 条留言待主 Agent 处理",
                                         f" ✉ {view.unread} message(s) for the main agent")
                        + (later if busy else "") + "\n"))
    if flash:
        out.append(("class:warn", f" {flash}\n"))
    out += [("class:rule", "─" * width + "\n"), ("class:accent bold", "› ")]
    return out


def toolbar(view: ProjectView | None, runner_alive: bool, width: int) -> Fragments:
    out: Fragments = [("class:rule", "─" * width + "\n")]
    if view is None:
        return [*out, ("class:dim", tr(" 加载中…", " loading…"))]
    phase_style = "class:warn" if view.phase in ("approval", "question", "suspended") else "class:ok"
    out += [(phase_style, f" {PHASES.get(view.phase, view.phase)}"),
            ("class:dim", (f" · ≈${view.spent_usd:.2f}/${view.budget_usd:g}"
             f" · {view.elapsed_h:.1f}h/{view.deadline_h:g}h"
             + tr(" · 深搜 ", " · deep ") + f"{view.deep_used}/{view.deep_max}"))]
    if view.undermind_calls or view.literature_calls:
        errors = (tr(f"，错 {view.undermind_errors}", f", {view.undermind_errors} failed")
                  if view.undermind_errors else "")
        out.append(("class:dim", tr(" · 文献 Undermind ", " · literature Undermind ")
                    + f"{view.undermind_calls}{errors}"
                    + (tr(" / 开放源 ", " / open ") + str(view.literature_calls)
                       if view.literature_calls else "")))
    if view.degraded:
        label = DEGRADED.get(view.degraded, view.degraded)
        out.append(("class:warn", tr(f" · Undermind {label}，已改用开放文献源",
                                    f" · Undermind {label}, using open sources")))
    if view.gpu is not None:
        out.append(("class:dim", (f" · GPU {view.gpu.util_percent}% "
                    f"{view.gpu.used_mib / 1024:.1f}/{view.gpu.total_mib / 1024:.0f}G")))
    if view.questions:
        out.append(("class:warn", tr(f" · 待你回答 {len(view.questions)} 项（Ctrl+A）",
                                    f" · {len(view.questions)} to answer (Ctrl+A)")))
    if not runner_alive and view.phase not in ("finished", "suspended", "intake"):
        out.append(("class:fail", tr(" · 后台未运行（/run 启动）", " · runner stopped (/run)")))
    elif runner_alive:
        out.append(("class:dim", tr(" · 后台运行中", " · runner on")))
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
        lines.append(Text(tr("超时默认：", "Default on timeout: ") + str(payload["default"]),
                          style="grey62"))
    lines.append(Text(tr("按 Ctrl+A 或输入 /approve 回答；也可以直接打字先和主 Agent 讨论。",
                         "Answer with Ctrl+A or /approve, or just type to discuss it first."),
                      style="grey62"))
    title = (tr("需要你审批", "Approval needed") if payload.get("kind") == "approval"
             else tr("主 Agent 提问", "Question from the main agent"))
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
            t.append(tr("  [紧急]", "  [urgent]"), style="yellow")
        return t + stamp
    if e.kind == "pi.message":
        return Group(Text(tr("● 主 Agent", "● Main agent"), style="bold cyan") + stamp,
                     Padding(Markdown(str(p.get("text", "")), hyperlinks=False), (0, 0, 0, 2)))
    if e.kind == "user.question":
        return question_panel(str(p["qid"]), p)
    if e.kind == "user.reply":
        how = (tr("超时未回复，按默认处理", "No reply in time; default taken for") if p.get("auto")
               else tr("你已回答", "You answered"))
        return _sub(f"{how} {p['qid']}{tr('：', ': ')}{p['answer']}")
    if e.kind == "order.created":
        goal = str(p.get("order", {}).get("goal", ""))
        return Group(Text(tr(f"● 派出 {who} · {oid}", f"● Dispatched {who} · {oid}"), style="bold") + stamp, _sub(clip(goal, 200)))
    if e.kind == "order.finished":
        r = p.get("result", {})
        ok = r.get("status") == "done"
        head = Text(f"● {who} · {oid} {STATUSES.get(r.get('status', ''), r.get('status'))}",
                    style="bold green" if ok else "bold red")
        took = minutes(float(r.get("wall_seconds") or 0))
        head.append(tr(f"（用时 {took}）", f" ({took})"), style="grey62")
        body = [_sub(clip(str(r.get("summary", "")), 400))]
        if r.get("error"):
            body.append(_sub(clip(str(r["error"]), 300), "red"))
        return Group(head + stamp, *body)
    if e.kind == "order.cancelled":
        return Text(tr(f"● {who} · {oid} 已取消：", f"● {who} · {oid} cancelled: ")
                    + str(p.get("reason", "")), style="yellow") + stamp
    if e.kind == "order.interrupted":
        return _sub(tr(f"{who} · {oid} 被中断，后台重启后会续接",
                   f"{who} · {oid} interrupted; it resumes when the runner restarts"), "yellow")
    if e.kind == "project.charter_changed":
        lines: list[RenderableType] = [Text(tr("● 章程已修改", "● Charter changed"), style="bold yellow") + stamp]
        lines += [_sub(f"{c['field']} → {c['value']}") for c in p.get("changes", [])]
        lines += [_sub(tr("已拒绝：", "Rejected: ") + f"{c['field']} = {c['value']}", "red")
                  for c in p.get("rejected", [])]
        return Group(*lines)
    if e.kind == "project.suspended":
        return Text(tr(f"● 项目已暂停：{p.get('reason', '')}  （/resume 继续）",
                       f"● Project suspended: {p.get('reason', '')}  (/resume)"),
                    style="bold yellow") + stamp
    if e.kind == "project.resumed":
        return Text(tr("● 项目已恢复", "● Project resumed"), style="bold green") + stamp
    if e.kind == "project.finished":
        return Group(Text(tr("● 项目完成", "● Project finished"), style="bold green") + stamp,
                     Padding(Markdown(str(p.get("summary", "")), hyperlinks=False), (0, 0, 0, 2)))
    if e.kind == "undermind.degraded":
        reason = DEGRADED.get(str(p.get("reason")), str(p.get("reason")))
        return Group(Text(tr(f"● Undermind {reason}，已改用 OpenAlex / Semantic Scholar / arXiv",
                             f"● Undermind {reason}; using OpenAlex / Semantic Scholar / arXiv"),
                          style="bold yellow") + stamp,
                     _sub(clip(str(p.get("detail", "")), 300)))
    if e.kind == "lab.deadline":
        return Text(tr("● 已到截止时间，主 Agent 开始收尾", "● Deadline reached; the main agent wraps up"),
                    style="bold yellow") + stamp
    if e.kind == "lab.error":
        return Text(tr("● 主 Agent 出错（会重试）：", "● Main agent error (will retry): ")
                    + clip(str(p.get("error", "")), 300),
                    style="red") + stamp
    if e.kind == "notify.failed":
        return _sub(tr("邮件发送失败：", "Email failed: ") + clip(str(p.get("error", "")), 200), "red")
    return None


def charter_panel(c: Charter) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="grey62", no_wrap=True)
    t.add_column()
    hours = c.approval_timeout_hours
    timeout = (tr(f"{hours:g} 小时后暂停", f"suspend after {hours:g} h")
               if c.on_timeout == "suspend" else
               tr(f"{hours:g} 小时后按默认处理", f"default after {hours:g} h"))
    for k, v in ((tr("目标", "Goal"), c.goal), (tr("会议", "Venue"), c.target_venue),
                 (tr("预算", "Budget"), f"${c.budget_usd:g}"),
                 (tr("期限", "Deadline"), tr(f"{c.deadline_hours:g} 小时", f"{c.deadline_hours:g} h")),
                 (tr("审批点", "Approvals"), ", ".join(c.approval_points) or tr("无", "none")),
                 (tr("超时", "Timeout"), timeout),
                 (tr("深度检索", "Deep search"),
                  tr(f"最多 {c.max_deep_searches} 次", f"at most {c.max_deep_searches}")),
                 (tr("约束", "Constraints"), c.constraints), (tr("风险", "Risks"), c.risk_notes)):
        t.add_row(k, v)
    return Panel(t, title=c.title, title_align="left", border_style="cyan")


def orders_table(view: ProjectView, cost: Mapping[str, float | None]) -> Table:
    t = Table(box=None, padding=(0, 1), header_style="grey62")
    cols = (tr("工单", "Order"), tr("角色", "Role"), tr("状态", "Status"), tr("用时", "Time"),
            "token", "≈$", tr("结果", "Result"))
    for col in cols:
        t.add_column(col, no_wrap=col != cols[-1], overflow="fold")
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
    t.add_row(tr("执行者", "Executor"), f"{a.executor} · {a.model}")
    t.add_row(tr("状态", "Status"), f"{STATUSES.get(a.status, a.status)} · "
              + tr(f"第 {a.attempt} 次尝试", f"attempt {a.attempt}")
              + f" · {minutes(a.elapsed)}/{minutes(a.limit)}")
    cached = tokens(a.usage.cached_input_tokens)
    t.add_row(tr("用量", "Usage"), tr("运行中不可见，结束后统计", "counted when the order ends")
              if not a.usage.total_tokens else
              f"{tokens(a.usage.total_tokens)} token" + tr(f"（缓存 {cached}）", f" ({cached} cached)")
              + (f" ≈${a.cost_usd:.2f}" if a.cost_usd else ""))
    t.add_row(tr("目标", "Goal"), a.goal)
    if state is not None:
        t.add_row(tr("交付物", "Deliverables"), "\n".join(state.order.deliverables) or "-")
        if state.result is not None:
            t.add_row(tr("结果", "Result"), state.result.summary)
            if state.result.deliverables:
                t.add_row(tr("已交付", "Delivered"), "\n".join(state.result.deliverables))
            if state.result.error:
                t.add_row(tr("错误", "Error"), Text(state.result.error, style="red"))
    steps = Text()
    for s in a.steps[-DETAIL_STEPS:]:
        style = {"running": "cyan", "failed": "red"}.get(s.status, "grey70")
        steps.append(f"{STEP_MARK.get(s.kind, '·')} {clip(s.text, width - 22)}\n", style=style)
    if a.steps:
        n = min(DETAIL_STEPS, len(a.steps))
        t.add_row(tr(f"最近 {n} 步", f"Last {n} steps"), steps)
    return Panel(t, title=f"{role(a.role)} · {a.id}", title_align="left", border_style="cyan")
