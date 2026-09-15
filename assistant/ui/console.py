"""Terminal output: a live full-screen dashboard (voice mode) or a scrolling
transcript (keyboard mode, where a live screen would fight with input())."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..events import Event, EventBus
from ..state import AppState

STATUS_STYLE = {
    "idle": ("○", "dim"),
    "listening": ("●", "bold green"),
    "transcribing": ("◐", "bold cyan"),
    "thinking": ("◑", "bold yellow"),
    "speaking": ("◉", "bold blue"),
}
STEP_ICON = {"pending": ("□", "dim"), "running": ("▶", "yellow"), "done": ("✔", "green"), "failed": ("✘", "red")}
ACTIVITY_STYLE = {"tool": "magenta", "text": "white", "start": "bold cyan", "result": "bold green",
                  "error": "bold red", "log": "dim", "local": "cyan"}


class TerminalDashboard:
    def __init__(self, state: AppState, console: Console | None = None) -> None:
        self.state = state
        self.console = console or Console()
        self.live = Live(get_renderable=self._render, console=self.console,
                         refresh_per_second=8, screen=True, redirect_stderr=False)

    def start(self) -> None:
        self.live.start()

    def stop(self) -> None:
        self.live.stop()

    # -- rendering ----------------------------------------------------------
    def _render(self) -> Layout:
        snap = self.state.snapshot()
        layout = Layout()
        layout.split_column(Layout(self._header(snap), size=3), Layout(name="body"))
        layout["body"].split_row(Layout(self._conversation(snap), ratio=5), Layout(name="side", ratio=4))
        layout["body"]["side"].split_column(Layout(self._plan(snap), ratio=2), Layout(self._activity(snap), ratio=3))
        return layout

    def _header(self, snap: dict[str, Any]) -> Panel:
        icon, style = STATUS_STYLE.get(snap["status"], ("○", "dim"))
        line = Text.assemble(
            (f" {snap['name']} ", "bold reverse"), "  ",
            (f"{icon} {snap['status']}", style), "   agent: ", (snap["backend"], "bold"),
            ("  (working)" if snap["agent_running"] else "", "yellow"),
            "   workspace: ", (snap["workspace"], "dim"),
        )
        return Panel(line, border_style="grey37")

    def _conversation(self, snap: dict[str, Any]) -> Panel:
        rows = []
        for msg in snap["conversation"][-18:]:
            who = ("you", "bold green") if msg["role"] == "user" else (snap["name"].lower(), "bold blue")
            rows.append(Text.assemble((f"{who[0]:>6} ", who[1]), msg["text"]))
        hint = Text("Say something like: \"let's plan a todo app\", \"go ahead\", \"use antigravity\", \"status\".", style="dim")
        return Panel(Group(*rows) if rows else hint, title="conversation", border_style="green")

    def _plan(self, snap: dict[str, Any]) -> Panel:
        plan = snap["plan"]
        if not plan["steps"]:
            return Panel(Text("No plan yet.", style="dim"), title="plan", border_style="yellow")
        table = Table.grid(padding=(0, 1))
        for i, step in enumerate(plan["steps"], 1):
            icon, style = STEP_ICON.get(step["status"], ("□", "dim"))
            table.add_row(Text(icon, style=style), Text(f"{i}.", style="dim"), Text(step["text"]))
        done = sum(s["status"] == "done" for s in plan["steps"])
        title = f"plan: {plan['title'] or 'untitled'}  [{done}/{len(plan['steps'])}]"
        return Panel(table, title=title, border_style="yellow")

    def _activity(self, snap: dict[str, Any]) -> Panel:
        rows = []
        for item in snap["activity"][-20:]:
            kind = item.get("kind", "log")
            stamp = time.strftime("%H:%M:%S", time.localtime(item["ts"]))
            label = item.get("tool") or kind
            body = item.get("text", "")
            rows.append(Text.assemble((stamp + " ", "dim"), (f"{label:<10} ", ACTIVITY_STYLE.get(kind, "white")),
                                      body.splitlines()[0] if body else ""))
        return Panel(Group(*rows) if rows else Text("Idle.", style="dim"), title="agent activity", border_style="magenta")


class LinePrinter:
    """Plain scrolling output for keyboard mode."""

    def __init__(self, bus: EventBus, assistant_name: str, console: Console | None = None) -> None:
        self.console = console or Console()
        self.name = assistant_name
        for topic in ("transcript", "agent", "log", "plan", "backend"):
            bus.subscribe(topic, self._print)

    def _print(self, event: Event) -> None:
        d = event.data
        c = self.console
        if event.topic == "transcript" and d["role"] == "assistant":
            c.print(f"[bold blue]{self.name}>[/] {d['text']}")
        elif event.topic == "agent":
            style = ACTIVITY_STYLE.get(d["kind"], "white")
            label = d.get("tool") or d["kind"]
            c.print(f"  [{style}]{label}[/] [dim]{d.get('text', '')[:300]}[/]")
        elif event.topic == "log":
            c.print(f"[dim]· {d['text']}[/]")
        elif event.topic == "backend":
            c.print(f"[dim]· agent backend: {d['name']}[/]")
        elif event.topic == "plan" and d["steps"]:
            c.print(f"[yellow]plan: {d['title'] or 'untitled'}[/]")
            for i, step in enumerate(d["steps"], 1):
                icon, style = STEP_ICON.get(step["status"], ("□", "dim"))
                c.print(f"  [{style}]{icon}[/] {i}. {step['text']}")
