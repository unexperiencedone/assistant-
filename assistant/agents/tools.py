"""The hands any model-based agent gets: Nova's own local abilities as tools.

A chat model can only talk. These functions are what turn OpenRouter into an
assistant that acts: find and open things, click inside apps, run automations,
write Word documents, and hand real coding work to Claude Code.

Every tool returns a short string that goes straight back into the model's
context, so answers stay terse on purpose.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..automation import desktop
from ..automation.office import write_document
from ..system.duplicates import human_size

log = logging.getLogger("nova.model-tools")


@dataclass
class ToolContext:
    """What the tools act on. Anything missing simply disables its tools."""
    local_system: Any = None          # assistant.system.LocalSystem
    automations: Any = None           # assistant.automation.service.AutomationService
    delegate: Callable[[str], str] | None = None  # hand a task to Claude Code, return its reply
    workspace: Path | None = None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


# -- tool implementations ---------------------------------------------------------------
def find_items(ctx: ToolContext, query: str, kind: str = "", limit: int = 5) -> str:
    if not ctx.local_system:
        return "The local index is disabled."
    hits = ctx.local_system.find(query, kind=kind or None, limit=min(int(limit), 10))
    if not hits:
        return f"Nothing matching '{query}'."
    return "\n".join(f"{h.kind}: {h.name} — {h.path}" for h in hits)


def open_item(ctx: ToolContext, query: str, kind: str = "") -> str:
    if not ctx.local_system:
        return "The local index is disabled."
    result = ctx.local_system.open(query, kind=kind or None)
    if result.ok and result.hit:
        return f"Opened {result.hit.name} ({result.hit.path})."
    alternatives = ", ".join(h.name for h in result.alternatives[:3])
    return f"{result.message}{' Closest: ' + alternatives if alternatives else ''}"


def find_duplicates(ctx: ToolContext, folder: str, min_size_kb: int = 100) -> str:
    if not ctx.local_system:
        return "The local index is disabled."
    groups = ctx.local_system.duplicates(folder, min_size=int(min_size_kb) * 1024)
    if not groups:
        return f"No duplicates in {folder}."
    wasted = human_size(sum(g.wasted for g in groups))
    lines = [f"{len(groups)} duplicate sets, {wasted} reclaimable. Biggest:"]
    lines += [f"  {human_size(g.size)} x{len(g.paths)}: {Path(g.paths[0]).name}" for g in groups[:5]]
    return "\n".join(lines)


def list_windows(ctx: ToolContext, filter: str = "") -> str:
    rows = desktop.list_windows(filter)
    return "\n".join(title for title, _pid in rows[:20]) or "No open windows match."


def list_controls(ctx: ToolContext, window: str, filter: str = "", limit: int = 25) -> str:
    try:
        rows = desktop.list_controls(window, filter, limit=min(int(limit), 40))
    except desktop.AutomationError as exc:
        return str(exc)
    return "\n".join(f"{r.control_type}: {r.name or '(no name)'} [{r.auto_id}]" for r in rows) or "No matching controls."


def click_control(ctx: ToolContext, window: str, name: str = "", auto_id: str = "") -> str:
    try:
        return f"Clicked {desktop.click(window, name, auto_id)}."
    except desktop.AutomationError as exc:
        return str(exc)


def type_in_app(ctx: ToolContext, window: str, text: str, into: str = "") -> str:
    try:
        desktop.type_text(window, text, into)
        return f"Typed {len(text)} characters into {window}."
    except desktop.AutomationError as exc:
        return str(exc)


def press_keys(ctx: ToolContext, window: str, keys: str) -> str:
    try:
        desktop.send_keys(window, keys)
        return f"Sent {keys} to {window}."
    except desktop.AutomationError as exc:
        return str(exc)


def read_control(ctx: ToolContext, window: str, name: str = "", auto_id: str = "") -> str:
    try:
        return desktop.read(window, name, auto_id) or "(empty)"
    except desktop.AutomationError as exc:
        return str(exc)


def list_automations(ctx: ToolContext) -> str:
    if not ctx.automations:
        return "Automations are disabled."
    macros = ctx.automations.macros
    return "\n".join(f"{m.name}: say '{m.phrases[0] if m.phrases else m.name}'" for m in macros) or "No automations yet."


def run_automation(ctx: ToolContext, phrase: str) -> str:
    if not ctx.automations:
        return "Automations are disabled."
    found = ctx.automations.match(phrase)
    if not found:
        return f"No automation matches '{phrase}'. Use list_automations to see them."
    macro, params = found
    ok, summary = ctx.automations.runner.run(macro, params, phrase)
    return summary


def write_word(ctx: ToolContext, title: str, markdown: str) -> str:
    try:
        path = write_document(markdown, title)
    except Exception as exc:
        return f"Word failed: {exc}"
    return f"Wrote and opened '{title}' in Word, saved at {path}."


def delegate_to_claude(ctx: ToolContext, task: str) -> str:
    """Anything needing a terminal, file edits or multi-step coding goes to Claude Code."""
    if not ctx.delegate:
        return "Claude Code isn't available."
    return ctx.delegate(task) or "Claude Code finished without a reply."


# -- schema ---------------------------------------------------------------------------------
def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


STRING = {"type": "string"}
TOOLS: dict[str, tuple[Callable[..., str], dict]] = {
    "find_items": (find_items, _schema(
        "find_items", "Find apps, files or folders on this PC by name, ranked by how often the user uses them.",
        {"query": STRING, "kind": {"type": "string", "enum": ["app", "file", "folder"]}, "limit": {"type": "integer"}}, ["query"])),
    "open_item": (open_item, _schema(
        "open_item", "Open an app, file or folder on this PC.",
        {"query": STRING, "kind": {"type": "string", "enum": ["app", "file", "folder"]}}, ["query"])),
    "find_duplicates": (find_duplicates, _schema(
        "find_duplicates", "Report duplicate files in a folder. Never deletes anything.",
        {"folder": STRING, "min_size_kb": {"type": "integer"}}, ["folder"])),
    "list_windows": (list_windows, _schema(
        "list_windows", "List titles of open windows, optionally filtered.", {"filter": STRING}, [])),
    "list_controls": (list_controls, _schema(
        "list_controls", "List clickable controls (buttons, fields) of an open app window, with their names and AutomationIds.",
        {"window": STRING, "filter": STRING, "limit": {"type": "integer"}}, ["window"])),
    "click_control": (click_control, _schema(
        "click_control", "Click a control in an app window by its name or AutomationId. List controls first if unsure.",
        {"window": STRING, "name": STRING, "auto_id": STRING}, ["window"])),
    "type_in_app": (type_in_app, _schema(
        "type_in_app", "Type text into an app window, optionally into a named field.",
        {"window": STRING, "text": STRING, "into": STRING}, ["window", "text"])),
    "press_keys": (press_keys, _schema(
        "press_keys", "Send keystrokes to a window. Syntax: ^ Ctrl, % Alt, + Shift, {ENTER}, {TAB}.",
        {"window": STRING, "keys": STRING}, ["window", "keys"])),
    "read_control": (read_control, _schema(
        "read_control", "Read the text of a control in an app window (e.g. a result field).",
        {"window": STRING, "name": STRING, "auto_id": STRING}, ["window"])),
    "list_automations": (list_automations, _schema(
        "list_automations", "List the user's saved automations and the phrases that trigger them.", {}, [])),
    "run_automation": (run_automation, _schema(
        "run_automation", "Run a saved automation by one of its trigger phrases.", {"phrase": STRING}, ["phrase"])),
    "write_word": (write_word, _schema(
        "write_word", "Write a Microsoft Word document and open it. Content is Markdown: # headings, - bullets, blank lines between paragraphs.",
        {"title": STRING, "markdown": STRING}, ["title", "markdown"])),
    "delegate_to_claude": (delegate_to_claude, _schema(
        "delegate_to_claude", "Hand a task to Claude Code, which has a terminal, file editing and web access. "
        "Use for coding, editing or creating files, running commands, git, and anything multi-step or open-ended. "
        "Give it the full request in one self-contained sentence. It may take a minute.",
        {"task": STRING}, ["task"])),
}


def schemas(ctx: ToolContext) -> list[dict]:
    """Tool definitions for the tools that are usable right now."""
    available = []
    for name, (_func, schema) in TOOLS.items():
        if name.startswith(("find_", "open_")) and not ctx.local_system:
            continue
        if name.endswith("automations") or name == "run_automation":
            if not ctx.automations:
                continue
        if name == "delegate_to_claude" and not ctx.delegate:
            continue
        available.append(schema)
    return available


# Tools that change something on the machine. When a *model* calls these (as opposed to
# the user saying "open spotify"), it's worth a visible log line: the prompt asks agents to
# be careful, but a prompt is a convention, not a boundary.
CHANGES_THINGS = {"open_item", "click_control", "type_in_app", "press_keys", "run_automation", "write_word"}


def call(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
    entry = TOOLS.get(name)
    if not entry:
        return f"Unknown tool '{name}'."
    func = entry[0]
    detail = describe_call(name, arguments)
    if name in CHANGES_THINGS:
        log.warning("model action: %s(%s)", name, detail)
    else:
        log.info("model tool: %s(%s)", name, detail)
    try:
        return _text(func(ctx, **arguments))
    except TypeError as exc:
        return f"Wrong arguments for {name}: {exc}"
    except Exception as exc:  # a failing tool must not kill the turn
        return f"{name} failed: {exc}"


def describe_call(name: str, arguments: dict[str, Any]) -> str:
    """One-line summary for the canvas."""
    for key in ("query", "phrase", "task", "title", "window", "folder", "filter"):
        if arguments.get(key):
            value = str(arguments[key])
            return value if len(value) <= 90 else value[:87] + "..."
    return ", ".join(f"{k}={v}" for k, v in list(arguments.items())[:2])[:90]
