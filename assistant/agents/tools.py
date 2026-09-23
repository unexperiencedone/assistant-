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
    delegate_agy: Callable[[str], str] | None = None  # ...or to Antigravity, for research
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
    orchestrator: Any = None          # assistant.orchestrate.Orchestrator
    plan: Any = None                  # assistant.planning.Plan -- this conversation's plan
    profile: Any = None               # assistant.profile.ProfileService -- for the owner's name
    assistant_name: str = "Nova"      # so an outbound draft can be checked for signing as Nova
    browser: bool = False             # drive the real browser (config [browser] enabled)


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
    """Saved voice shortcuts. NOT the plan -- see read_plan for that."""
    if not ctx.automations:
        return "Automations are disabled."
    macros = ctx.automations.macros
    listed = "\n".join(f"{m.name}: say '{m.phrases[0] if m.phrases else m.name}'" for m in macros)
    return listed or "No automations yet."


def read_plan(ctx: ToolContext) -> str:
    """The plan drafted in this conversation. Nothing to do with saved automations.

    These were being confused, and the confusion produced a confidently wrong answer:
    asked to clear the plans, Nova reported that it could list but not delete the saved
    automations -- which was true, and about the wrong thing entirely.
    """
    if ctx.plan is None:
        return "Plans aren't available."
    if ctx.plan.is_empty:
        return "There is no plan right now. (Saved automations are a different thing: use list_automations.)"
    return ctx.plan.to_speech()


def clear_plan(ctx: ToolContext) -> str:
    """Throw away the plan drafted in this conversation. Never touches automations."""
    if ctx.plan is None:
        return "Plans aren't available."
    if ctx.plan.is_empty:
        return "There was no plan to clear."
    steps = len(ctx.plan.steps)
    ctx.plan.clear()
    return f"Cleared the plan ({steps} steps). Saved automations are untouched."


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
    if ctx.plan is not None and not ctx.plan.is_empty:
        done = sum(1 for step in ctx.plan.steps if step.status == "done")
        lines.append(f"There is a plan open: {ctx.plan.title or 'untitled'}, "
                     f"{done} of {len(ctx.plan.steps)} steps done.")
    if ctx.orchestrator is not None:
        group_state = ctx.orchestrator.spoken()
        if group_state:
            lines.append(group_state)
    if ctx.goals is not None:
        try:
            spoken = ctx.goals.spoken()
        except Exception:
            spoken = ""
        if spoken:
            lines.append(spoken)
    return "\n".join(lines) or "Nothing is running; I'm idle."


def _browser(ctx: ToolContext):
    from ..automation.browser import thread_browser

    return thread_browser()


def browse_open(ctx: ToolContext, url: str, new_tab: bool = False) -> str:
    """Open a page in the real browser and report what is on it.

    Unlike read_page, this is a browser: JavaScript runs, logins apply, and pages that
    refuse a scripted fetch work here. It costs seconds and a visible window, so it is
    the second choice -- read_page first, this when that was not enough.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Give a full http or https URL."
    try:
        browser = _browser(ctx)
        title = browser.goto(url, new_tab=bool(new_tab))
        return f"{title}\n\n{browser.text(1800)}"
    except Exception as error:
        return f"I couldn't open that in the browser ({type(error).__name__}: {error})."


def browse_read(ctx: ToolContext, limit: int = 3000, text: str = "", selector: str = "") -> str:
    """Read the page already open: all of it, or one element named by text or selector."""
    try:
        browser = _browser(ctx)
        if text or selector:
            return browser.read(text=text, selector=selector) or "(that element is empty)"
        return browser.text(max(200, min(int(limit or 3000), 8000)))
    except Exception as error:
        return f"I couldn't read the page ({type(error).__name__}: {error})."


def browse_click(ctx: ToolContext, text: str = "", selector: str = "", role: str = "",
                 name: str = "") -> str:
    """Click something on the open page, by its visible text, role or selector."""
    try:
        _browser(ctx).click(text=text, selector=selector, role=role, name=name)
        return f"Clicked {text or name or selector}. Read the page again to see what changed."
    except Exception as error:
        return f"I couldn't click that ({type(error).__name__}: {error})."


def browse_fill(ctx: ToolContext, value: str, text: str = "", selector: str = "",
                label: str = "", placeholder: str = "", press_enter: bool = False) -> str:
    """Type into a field on the open page. Does not submit unless press_enter is true."""
    try:
        _browser(ctx).fill(value, press_enter=bool(press_enter), text=text,
                           selector=selector, label=label, placeholder=placeholder)
        return f"Filled in {len(value)} characters" + (" and pressed Enter." if press_enter else ".")
    except Exception as error:
        return f"I couldn't fill that in ({type(error).__name__}: {error})."


def draft_outreach(ctx: ToolContext, recipient: str, subject: str, body: str) -> str:
    """Stage an outreach message for the user's approval. Sends nothing.

    Checked before it is staged (`persona/outreach.py`). A live test produced a draft
    that signed itself as Nova and offered a stranger a free month of work, so a draft
    naming a price, a discount, a deadline or a guarantee is handed back with the reason
    instead of being stored.
    """
    if ctx.publisher is None:
        return "Publishing is turned off in the config."
    from ..persona import outreach

    owner = getattr(ctx.profile, "preferred_name", "") if ctx.profile is not None else ""
    refusal = outreach.verdict(f"{subject}\n\n{body}", nova=ctx.assistant_name, owner=owner)
    if refusal:
        return refusal
    draft = ctx.publisher.gate.stage("outreach_email", body.strip(),
                                     target=recipient.strip() or "(no address yet)",
                                     extra={"subject": subject.strip()})
    return (f"Drafted an email to {draft['target']}, subject \"{subject.strip()}\". "
            "Nothing has been sent; it is waiting for the user to approve it.")


def start_task(ctx: ToolContext, task: str, label: str = "") -> str:
    """Start one job in the background and carry on talking.

    The point of this over delegate_to_claude is that it returns at once. Delegation
    runs a whole Claude turn inline, which freezes the turn it was called from -- fine
    for "do this one thing", useless when the user asked for three.
    """
    if ctx.orchestrator is None:
        return "Background tasks aren't available."
    group = ctx.orchestrator.start(task, [(task, [])])
    if group is None:
        return "I'm already at my limit for parallel work; say it again in a moment."
    return f"Started that in the background ({group.id}). Say so, and carry on answering the rest."


def run_steps(ctx: ToolContext, steps: list, request: str = "") -> str:
    """Several pieces of work for one request, run together, answered once.

    A step may name what it waits for -- "write it up (after 1, 2)" -- and steps with no
    dependencies run at the same time. That is the only difference between working
    through a list and fanning it out, so both come through here.
    """
    if ctx.orchestrator is None:
        return "Running work in parallel isn't available."
    from ..planning.plan import parse_step_line

    parsed: list[tuple[str, list[int]]] = []
    for number, raw in enumerate(steps or [], 1):
        text, after = parse_step_line(str(raw), number)
        if text:
            parsed.append((text, after))
    if not parsed:
        return "I need the steps as a list of short sentences."
    group = ctx.orchestrator.start(request or "; ".join(t for t, _a in parsed), parsed)
    if group is None:
        return "I couldn't start any of that; something else is using the task slots."
    running = sum(1 for s in group.steps if s.status == "running")
    return (f"Running {len(group.steps)} steps ({running} at once). I'll give you one answer "
            "when they're all done, so don't repeat any of this back yet.")


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


def delegate_to_agy(ctx: ToolContext, task: str) -> str:
    """Reading around a subject, and pulling content off the web.

    A separate brain from Claude on purpose. Research, scraping and ideation are the
    bulk of what gets asked for and the cheapest kind of work to get wrong, so sending
    them to Antigravity keeps Claude for the jobs where being wrong is expensive --
    code, files, automations.
    """
    if not ctx.delegate_agy:
        return "Antigravity isn't available."
    return ctx.delegate_agy(task) or "Antigravity finished without a reply."


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
        "find_items", "Find apps, files or folders by name, ranked by use.",
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
        "list_controls", "List an app window clickable controls with names and AutomationIds.",
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
        "list_automations", "Saved voice shortcuts (files). NOT the plan: for plans use read_plan/clear_plan.", {}, [])),
    "read_plan": (read_plan, _schema(
        "read_plan", "Read back this conversation plan. Not the saved automations.",
        {}, [])),
    "clear_plan": (clear_plan, _schema(
        "clear_plan", "Throw away this conversation plan. Never touches automations.", {}, [])),
    "run_automation": (run_automation, _schema(
        "run_automation", "Run a saved automation by one of its trigger phrases.", {"phrase": STRING}, ["phrase"])),
    "write_word": (write_word, _schema(
        "write_word", "Write and open a Word document. Content is Markdown.",
        {"title": STRING, "markdown": STRING}, ["title", "markdown"])),
    "web_search": (web_search, _schema(
        "web_search", "Search the web for anything current or unknown. Snippets usually answer it.",
        {"query": STRING, "count": {"type": "integer"}}, ["query"])),
    "read_page": (read_page, _schema(
        "read_page", "Read one web page as text. Only when search snippets were not enough.",
        {"url": STRING, "limit": {"type": "integer"}}, ["url"])),
    "nova_status": (nova_status, _schema(
        "nova_status", "What you are doing now: tasks, recording, drafts, goals, plan.",
        {}, [])),
    "browse_open": (browse_open, _schema(
        "browse_open", "Open a URL in the real browser and read it. For pages needing JavaScript or a "
        "login. Slower than read_page; try that first.",
        {"url": STRING, "new_tab": {"type": "boolean"}}, ["url"])),
    "browse_read": (browse_read, _schema(
        "browse_read", "Read the open page, or one element by text or CSS selector.",
        {"limit": {"type": "integer"}, "text": STRING, "selector": STRING}, [])),
    "browse_click": (browse_click, _schema(
        "browse_click", "Click on the open page by text, role or CSS selector.",
        {"text": STRING, "selector": STRING, "role": STRING, "name": STRING}, [])),
    "browse_fill": (browse_fill, _schema(
        "browse_fill", "Type into a field on the open page; press_enter submits.",
        {"value": STRING, "text": STRING, "selector": STRING, "label": STRING,
         "placeholder": STRING, "press_enter": {"type": "boolean"}}, ["value"])),
    "draft_outreach": (draft_outreach, _schema(
        "draft_outreach", "Stage an outreach email for approval; sends nothing. Write as the USER in "
        "first person, never as yourself. No prices, discounts, free offers, deadlines or guarantees.",
        {"recipient": STRING, "subject": STRING, "body": STRING}, ["recipient", "subject", "body"])),
    "start_task": (start_task, _schema(
        "start_task", "Start one slow job in the background and keep answering. You are told when it ends.",
        {"task": STRING, "label": STRING}, ["task"])),
    "run_steps": (run_steps, _schema(
        "run_steps", "Run several parts of one request, answering once. Add '(after 1, 2)' to a step that "
        "must wait. Only when the request genuinely has separate parts.",
        {"steps": {"type": "array", "items": STRING}, "request": STRING}, ["steps"])),
    "start_recording": (start_recording, _schema(
        "start_recording", "Start recording the screen.",
        {"fps": {"type": "integer"}}, [])),
    "stop_recording": (stop_recording, _schema(
        "stop_recording", "Stop and save the screen recording.", {}, [])),
    "draft_post": (draft_post, _schema(
        "draft_post", "Write a linkedin or instagram post and stage it for approval. Does not publish.",
        {"platform": {"type": "string", "enum": ["linkedin", "instagram"]}, "text": STRING},
        ["platform", "text"])),
    "add_goal": (add_goal, _schema(
        "add_goal", "Add a standing goal Nova starts by itself, e.g. every morning.",
        {"task": STRING, "cadence": {"type": "string", "enum": ["hourly", "daily", "weekly"]}}, ["task"])),
    "list_goals": (list_goals, _schema(
        "list_goals", "The standing goals Nova runs on its own.", {}, [])),
    "list_skills": (list_skills, _schema(
        "list_skills", "What each skill is for, when the names are not enough.", {"query": STRING}, [])),
    "read_skill": (read_skill, _schema(
        "read_skill", "Load a skill instructions by name, then follow them.", {"name": STRING, "limit": {"type": "integer"}}, ["name"])),
    "delegate_to_agy": (delegate_to_agy, _schema(
        "delegate_to_agy", "Research and content: read around a subject, gather from several pages, "
        "scrape a site, compare options, ideate. Cheaper than Claude; prefer it for prose over code.", {"task": STRING}, ["task"])),
    "delegate_to_claude": (delegate_to_claude, _schema(
        "delegate_to_claude", "Code, files, commands, git, multi-step work on this machine. Slowest and "
        "costliest, so last resort. Pass 'skill' if one fits.",
        {"task": STRING, "skill": STRING}, ["task"])),
}


# Always offered, because these are the ones a turn reaches for without being asked to:
# looking something up, finding something on the machine, saying what it is doing, and
# handing the job on when it cannot finish.
CORE = {
    "web_search", "read_page", "nova_status", "find_items", "open_item",
    "delegate_to_agy", "delegate_to_claude", "read_skill", "list_skills",
}
# The rest are offered only when the request is about them. Every schema costs tokens on
# every turn, and the free tier's per-minute budget is small enough that the whole set
# was eating a fifth of it before a word of the request was sent. Matching locally is
# the same trick that made skills work: a model will not reliably ask what it has, but
# it does use what it is given.
WHEN_MENTIONED: dict[str, tuple[str, ...]] = {
    "browse_open": ("browser", "chrome", "website", "site", "page", "scrape", "directory", "listing", "form", "login"),
    "browse_read": ("browser", "chrome", "website", "site", "page", "scrape", "directory", "listing"),
    "browse_click": ("browser", "chrome", "click", "button", "link", "form", "submit"),
    "browse_fill": ("browser", "chrome", "type", "fill", "form", "search box", "field"),
    "draft_post": ("post", "linkedin", "instagram", "publish", "social", "draft"),
    "draft_outreach": ("email", "mail", "outreach", "customer", "client", "prospect", "draft", "reach out"),
    "start_recording": ("record", "recording", "screen", "capture", "demo", "video"),
    "stop_recording": ("record", "recording", "stop", "screen", "capture"),
    "add_goal": ("goal", "every morning", "every day", "daily", "weekly", "remind", "standing"),
    "list_goals": ("goal", "standing", "unprompted", "by yourself"),
    "read_plan": ("plan", "step", "steps"),
    "clear_plan": ("plan", "step", "steps", "clear", "drop", "scrap"),
    "run_steps": ("and then", "also", "at the same time", "both", "several", "steps", "parallel"),
    "start_task": ("background", "while", "meanwhile", "in the background", "carry on"),
    "write_word": ("word", "document", "docx", "article", "report", "write up", "whitepaper"),
    "find_duplicates": ("duplicate", "duplicates", "copies", "space"),
    "list_automations": ("automation", "automations", "macro", "shortcut", "saved"),
    "run_automation": ("automation", "macro", "shortcut", "run the"),
    "list_windows": ("window", "windows", "app", "open apps"),
    "list_controls": ("window", "button", "control", "click", "app", "field"),
    "click_control": ("window", "button", "control", "click", "app"),
    "type_in_app": ("window", "type", "app", "into"),
    "press_keys": ("keys", "keyboard", "shortcut", "ctrl", "press"),
    "read_control": ("window", "read", "field", "app", "result"),
}


def relevant(ctx: ToolContext, said: str = "") -> list[dict]:
    """The usable tools worth sending for *this* request.

    With nothing to match against, everything usable is offered -- an empty request is
    not evidence that a tool is unwanted. Given a request, the core set plus whatever it
    mentions goes out, which is most often about ten schemas instead of twenty-four.

    A miss costs capability rather than correctness: the delegates are always present,
    so the worst case is that a job Groq could have done itself is handed on.
    """
    usable = schemas(ctx)
    if not (said or "").strip():
        return usable
    lowered = f" {' '.join((said or '').lower().split())} "
    kept = []
    for schema in usable:
        name = schema["function"]["name"]
        if name in CORE:
            kept.append(schema)
            continue
        words = WHEN_MENTIONED.get(name)
        if words is None or any(word in lowered for word in words):
            kept.append(schema)
    return kept


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
        if name in ("start_task", "run_steps") and ctx.orchestrator is None:
            continue
        if name.endswith("_plan") and ctx.plan is None:
            continue
        if name.startswith("browse_") and not ctx.browser:
            continue
        if name == "draft_outreach" and ctx.publisher is None:
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
        if name == "delegate_to_agy" and not ctx.delegate_agy:
            continue
        available.append(schema)
    return available


# Tools that change something on the machine. When a *model* calls these (as opposed to
# the user saying "open spotify"), it's worth a visible log line: the prompt asks agents to
# be careful, but a prompt is a convention, not a boundary.
CHANGES_THINGS = {"open_item", "click_control", "type_in_app", "press_keys", "run_automation", "write_word",
                  "start_recording", "stop_recording", "draft_post", "add_goal",
                  "start_task", "run_steps", "clear_plan",
                  "browse_open", "browse_click", "browse_fill", "draft_outreach"}


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
