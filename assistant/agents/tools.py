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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import skills as skill_registry, web
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
    # Nova's own state, so the cheap tier can answer "what are you doing" and act on
    # what is already in flight instead of handing the question to Claude.
    runner: Any = None                # assistant.agent_runner.TaskPool
    capture: Any = None               # assistant.capture.ScreenRecorder
    publisher: Any = None             # assistant.publish.PublishService
    goals: Any = None                 # assistant.goals.GoalsService
    skills: dict = field(default_factory=dict)   # name -> agents.skills.Skill
    skill_limit: int = 6000
    capture_settings: Any = None      # assistant.config.CaptureSettings (fps, monitor, audio)


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


def web_search(ctx: ToolContext, query: str, count: int = 5) -> str:
    """Search the web. The snippets are often the whole answer, so this is usually enough."""
    rows = web.search(query, count=max(1, min(int(count or 5), 8)))
    if not rows:
        return "The search returned nothing. Say that you couldn't find it rather than guessing."
    return "\n".join(f"{i}. {row['title']} - {row['snippet']} [{row['url']}]"
                     for i, row in enumerate(rows, 1))


def read_page(ctx: ToolContext, url: str, limit: int = 2500) -> str:
    """Read one web page as text. Only when the search snippets weren't enough."""
    title, text = web.read(url, limit=max(200, min(int(limit or 2500), 6000)))
    return f"{title}\n\n{text}" if title else text


def nova_status(ctx: ToolContext) -> str:
    """What Nova itself is doing right now: running tasks, recording, drafts, goals.

    This exists so "what are you working on" is answerable by the cheap tier from real
    state, instead of being guessed at or handed to Claude. Every line is read from the
    live object, so it cannot describe a task that isn't running.
    """
    lines: list[str] = []
    if ctx.runner is not None:
        lines.append(ctx.runner.status_sentence() or "No background tasks are running.")
    if ctx.capture is not None and getattr(ctx.capture, "running", False):
        lines.append(f"A screen recording has been going for {ctx.capture.elapsed()}.")
    if ctx.publisher is not None:
        try:
            waiting = ctx.publisher.waiting()
        except Exception:
            waiting = ""
        if waiting:
            lines.append(waiting)
    if ctx.goals is not None:
        try:
            spoken = ctx.goals.spoken()
        except Exception:
            spoken = ""
        if spoken:
            lines.append(spoken)
    return "\n".join(lines) or "Nothing is running; I'm idle."


def start_recording(ctx: ToolContext, fps: int = 0) -> str:
    """Begin recording this screen. Footage lands in the capture inbox."""
    if ctx.capture is None:
        return "Screen recording is turned off in the config."
    settings = ctx.capture_settings
    ok, message = ctx.capture.start(fps=int(fps) or getattr(settings, "fps", 25),
                                    audio=getattr(settings, "audio", False),
                                    monitor=getattr(settings, "monitor", 1))
    return message if not ok else f"Recording. {message}"


def stop_recording(ctx: ToolContext) -> str:
    if ctx.capture is None:
        return "Screen recording is turned off in the config."
    if not ctx.capture.running:
        return "Nothing is recording."
    _ok, message = ctx.capture.stop()
    return message


def draft_post(ctx: ToolContext, platform: str, text: str) -> str:
    """Write a post and stage it for approval. This never sends anything.

    There is deliberately no tool for approving a draft. The gate in front of everything
    outward-facing exists precisely so a model cannot decide to publish (see
    assistant/publish/gate.py) -- the user approves each draft by voice, one at a time.
    """
    if ctx.publisher is None:
        return "Publishing is turned off in the config."
    wanted = (platform or "").strip().lower()
    if wanted.startswith("linkedin"):
        return ctx.publisher.draft_linkedin(text)
    if wanted.startswith("insta"):
        return ctx.publisher.draft_instagram(text)
    return "I can draft for linkedin or instagram."


def add_goal(ctx: ToolContext, task: str, cadence: str = "daily") -> str:
    """Add a standing goal: something Nova starts on its own from then on."""
    if ctx.goals is None:
        return "Standing goals are turned off in the config."
    goal = ctx.goals.add(task, (cadence or "daily").strip().lower())
    if goal is None:
        return "That goal needs a clearer description of what to do."
    return f"Added a {cadence} goal: {task}."


def list_goals(ctx: ToolContext) -> str:
    if ctx.goals is None:
        return "Standing goals are turned off in the config."
    return ctx.goals.spoken()


def list_skills(ctx: ToolContext, query: str = "") -> str:
    """What each skill is for, when the names in the instructions weren't enough."""
    if not ctx.skills:
        return "No skills are installed."
    return skill_registry.catalog(ctx.skills, query)


def read_skill(ctx: ToolContext, name: str, limit: int = 0) -> str:
    """Load one skill's instructions, then follow them for this job."""
    if not ctx.skills:
        return "No skills are installed."
    return skill_registry.read(ctx.skills, name, limit=int(limit) or ctx.skill_limit)


def delegate_to_claude(ctx: ToolContext, task: str, skill: str = "") -> str:
    """Anything needing a terminal, file edits or multi-step coding goes to Claude Code.

    `skill` is the point of naming one here: Nova's cheap tier is better placed to pick
    the right skill than Claude is to guess from a one-line request, and a delegation
    that names the skill saves Claude the turn it would spend choosing.
    """
    if not ctx.delegate:
        return "Claude Code isn't available."
    if skill and skill in ctx.skills:
        task = f"Use the '{skill}' skill at {ctx.skills[skill].folder} for this.\n\n{task}"
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
    "web_search": (web_search, _schema(
        "web_search", "Search the web for current information, facts, news or anything you don't know. "
        "Returns titles, snippets and links. The snippets are usually enough to answer from directly.",
        {"query": STRING, "count": {"type": "integer"}}, ["query"])),
    "read_page": (read_page, _schema(
        "read_page", "Read one web page as plain text, given its URL. "
        "Use only when the search snippets did not answer the question.",
        {"url": STRING, "limit": {"type": "integer"}}, ["url"])),
    "nova_status": (nova_status, _schema(
        "nova_status", "What you yourself are doing right now: background tasks, screen recording, "
        "drafts waiting for approval, standing goals. Use this whenever the user asks what you are up to.",
        {}, [])),
    "start_recording": (start_recording, _schema(
        "start_recording", "Start recording the screen. Footage is saved and never sent anywhere.",
        {"fps": {"type": "integer"}}, [])),
    "stop_recording": (stop_recording, _schema(
        "stop_recording", "Stop the screen recording that is running and save the file.", {}, [])),
    "draft_post": (draft_post, _schema(
        "draft_post", "Write a post for linkedin or instagram and stage it for the user's approval. "
        "This does NOT publish: the user approves the draft themselves afterwards.",
        {"platform": {"type": "string", "enum": ["linkedin", "instagram"]}, "text": STRING},
        ["platform", "text"])),
    "add_goal": (add_goal, _schema(
        "add_goal", "Add a standing goal Nova starts by itself from then on, e.g. every morning.",
        {"task": STRING, "cadence": {"type": "string", "enum": ["hourly", "daily", "weekly"]}}, ["task"])),
    "list_goals": (list_goals, _schema(
        "list_goals", "The standing goals Nova runs on its own.", {}, [])),
    "list_skills": (list_skills, _schema(
        "list_skills", "What each installed skill is for. Use when the skill names in your instructions "
        "aren't enough to tell which one fits.", {"query": STRING}, [])),
    "read_skill": (read_skill, _schema(
        "read_skill", "Load one skill's full instructions by name, then follow them. Use when a skill "
        "covers the job you've been asked to do.", {"name": STRING, "limit": {"type": "integer"}}, ["name"])),
    "delegate_to_claude": (delegate_to_claude, _schema(
        "delegate_to_claude", "Hand a task to Claude Code, which has a terminal, file editing and web access. "
        "Use for coding, editing or creating files, running commands, git, and anything multi-step or open-ended. "
        "Give it the full request in one self-contained sentence. It may take a minute. "
        "Pass 'skill' with the name of the skill that fits the job, if one does.",
        {"task": STRING, "skill": STRING}, ["task"])),
}


def schemas(ctx: ToolContext) -> list[dict]:
    """Tool definitions for the tools that are usable right now."""
    available = []
    for name, (_func, schema) in TOOLS.items():
        if name.startswith(("find_", "open_")) and not ctx.local_system:
            continue
        # Status is only honest if something can actually report state. With no runner
        # attached it would answer "I'm idle" to everything, which is worse than absent.
        if name == "nova_status" and ctx.runner is None:
            continue
        if name.endswith("_recording") and ctx.capture is None:
            continue
        if name == "draft_post" and ctx.publisher is None:
            continue
        if name.endswith("_goal") or name == "list_goals":
            if ctx.goals is None:
                continue
        if name.endswith("_skill") or name == "list_skills":
            if not ctx.skills:      # naming a tool with nothing behind it wastes a turn
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
CHANGES_THINGS = {"open_item", "click_control", "type_in_app", "press_keys", "run_automation", "write_word",
                  "start_recording", "stop_recording", "draft_post", "add_goal"}


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
