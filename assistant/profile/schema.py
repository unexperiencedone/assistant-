"""The profile's shape, declared once.

The same declaration validates what gets saved and drives the canvas form, so the
two can't drift apart: `GET /api/profile` sends `schema()` and the React form draws
whatever fields it describes.

Field types: text, textarea, select, tags (list of short strings), bool,
list (repeated items, each with its own fields).

Every section has a visibility, which decides which agents may see it in a prompt:
  all       every agent, including the free cloud models (Groq, OpenRouter)
  trusted   Claude Code and Antigravity only
  local     never sent anywhere: used on this PC only (speech words, opening folders)
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
VISIBILITY = [
    {"value": "all", "label": "All agents",
     "help": "Claude Code, Antigravity and the free cloud models (Groq, OpenRouter)."},
    {"value": "trusted", "label": "Claude & Antigravity",
     "help": "Only the coding agents that run under your own accounts."},
    {"value": "local", "label": "This PC only",
     "help": "Never put in a prompt. Used for speech recognition and opening folders."},
]
VISIBILITY_VALUES = {v["value"] for v in VISIBILITY}

MAX_TEXT = 300
MAX_LONG_TEXT = 2000
MAX_TAGS = 40
MAX_ITEMS = 60

LEVELS = ["learning", "rusty", "comfortable", "strong", "advanced"]
STATUSES = ["active", "paused", "done", "idea"]


def _f(key: str, label: str, type_: str = "text", **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "type": type_, **extra}


SECTIONS: list[dict[str, Any]] = [
    {
        "key": "identity", "label": "About you", "visibility": "trusted",
        "help": "Who Nova is working for. The name is also used in greetings.",
        "fields": [
            _f("name", "Full name"),
            _f("preferred_name", "What Nova calls you", placeholder="First name or nickname"),
            _f("headline", "What you do", placeholder="e.g. 3rd-year B.Tech student in AI"),
            _f("education", "Education", "textarea"),
            _f("organizations", "Roles and organizations", "tags", placeholder="VP Tech at ..."),
            _f("location", "Location"),
            _f("timezone", "Time zone", placeholder="Asia/Kolkata"),
            _f("languages", "Languages you speak", "tags",
               help="Helps agents read mixed-language transcripts sensibly."),
        ],
    },
    {
        "key": "goals", "label": "Goals", "visibility": "all",
        "help": "Where you're heading, so suggestions point the same way.",
        "fields": [
            _f("career", "Career goals", "tags"),
            _f("research_interests", "Research interests", "tags"),
            _f("learning_now", "Currently learning", "tags"),
            _f("looking_for", "Looking for", placeholder="e.g. internships and jobs, preferably at startups"),
        ],
    },
    {
        "key": "skills", "label": "Skills", "visibility": "all",
        "help": "Agents pitch explanations and pick tools to match.",
        "fields": [
            _f("items", "Skills", "list", item_label="skill", fields=[
                _f("name", "Skill", required=True),
                _f("level", "Level", "select", options=LEVELS, default="comfortable"),
                _f("notes", "Notes"),
            ]),
            _f("avoid", "Steer away from", "tags",
               help="Topics or kinds of problems agents shouldn't suggest unless you ask."),
        ],
    },
    {
        "key": "work_style", "label": "How to work with you", "visibility": "all",
        "help": "Standing instructions every agent follows.",
        "fields": [
            _f("code", "Code delivery", "textarea"),
            _f("docs", "Documentation", "textarea"),
            _f("spoken_replies", "Spoken reply length", "select", options=["brief", "normal", "detailed"],
               default="normal"),
            _f("rules", "Other rules", "tags", placeholder="Always ..."),
        ],
    },
    {
        "key": "devices", "label": "Computers", "visibility": "all",
        "help": "What your work actually runs on, so agents don't suggest what it can't do.",
        "fields": [
            _f("items", "Machines", "list", item_label="machine", fields=[
                _f("name", "Name", required=True),
                _f("specs", "Specs"),
                _f("gpu", "GPU", placeholder="none, or model and VRAM"),
                _f("yours", "This is yours", "bool", default=True),
                _f("notes", "Notes"),
            ]),
            _f("constraints", "Compute constraints", "textarea",
               placeholder="e.g. no discrete GPU: prefer CPU-friendly models or cloud notebooks"),
        ],
    },
    {
        "key": "projects", "label": "Projects", "visibility": "trusted",
        "help": "Mention a project by name or alias and its details come along with the request. "
                "Set a folder and \"open <project>\" opens it.",
        "fields": [
            _f("items", "Projects", "list", item_label="project", fields=[
                _f("name", "Name", required=True),
                _f("aliases", "Also called", "tags", help="Other names you say out loud."),
                _f("status", "Status", "select", options=STATUSES, default="active"),
                _f("context", "Event or client", placeholder="e.g. SIH 2026, OpenCV AI Competition 2026"),
                _f("role", "Your role"),
                _f("team", "Team", "tags"),
                _f("summary", "What it is", "textarea"),
                _f("stack", "Stack", "tags"),
                _f("folder", "Folder on this PC", placeholder="C:\\path\\to\\project"),
                _f("links", "Links", "tags"),
                _f("deadline", "Deadline"),
            ]),
        ],
    },
    {
        "key": "people", "label": "People", "visibility": "trusted",
        "help": "Teammates and contacts. Their names also help speech recognition.",
        "fields": [
            _f("items", "People", "list", item_label="person", fields=[
                _f("name", "Name", required=True),
                _f("aliases", "Also called", "tags"),
                _f("relation", "Relation", placeholder="e.g. teammate, team lead"),
                _f("projects", "Projects together", "tags"),
                _f("notes", "Notes"),
            ]),
        ],
    },
    {
        "key": "portfolio", "label": "Past work", "visibility": "trusted",
        "help": "Finished projects and experience, for resumes, applications and \"like what I did in ...\".",
        "fields": [
            _f("items", "Entries", "list", item_label="entry", fields=[
                _f("name", "Name", required=True),
                _f("aliases", "Also called", "tags"),
                _f("summary", "What it was"),
                _f("stack", "Stack", "tags"),
            ]),
        ],
    },
    {
        "key": "vocabulary", "label": "Speech words", "visibility": "local",
        "help": "Names and terms speech recognition should expect. Project and people names are added automatically.",
        "fields": [
            _f("words", "Words", "tags"),
        ],
    },
    {
        "key": "phone", "label": "Phone", "visibility": "local",
        "help": "What Nova may read from your phone through the Termux bridge. Doing things "
                "on the phone (torch, calls, opening apps) is separate and always allowed; "
                "this is only about reading.",
        "fields": [
            _f("sensing", "Let Nova read from my phone", "bool", default=True,
               help="Off switches every source below off at once."),
            _f("contacts", "Contacts", "bool", default=True,
               help="Needed to say \"call mum\" instead of a number."),
            _f("messages", "Text messages", "bool", default=False,
               help="Reading recent SMS. Off by default: it is the most personal of these."),
            _f("call_log", "Call log", "bool", default=False),
            _f("notifications", "Notifications", "bool", default=False),
            _f("location", "Where the phone is", "bool", default=True,
               help="Needed for \"where am I\". Off means Nova cannot locate the phone at all."),
            _f("clipboard", "Clipboard", "bool", default=False,
               help="Off by default: whatever you copied last is often a password."),
            _f("camera", "Camera", "bool", default=False,
               help="Letting Nova take a photo on the phone. Off by default, and it says "
                    "so out loud every time it does."),
        ],
    },
    {
        "key": "privacy", "label": "Privacy", "visibility": None,
        "help": "How the profile is used at all.",
        "fields": [
            _f("personalize", "Personalize agents with this profile", "bool", default=True),
            _f("shared_account", "Someone else also uses this Claude account", "bool", default=False,
               help="Agents are told to keep your personal details out of files and repos unless you ask."),
        ],
    },
]
SECTION_KEYS = [s["key"] for s in SECTIONS]


def schema() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "sections": SECTIONS, "visibility": VISIBILITY}


def empty_profile() -> dict[str, Any]:
    return normalize({})


def normalize(raw: Any) -> dict[str, Any]:
    """Coerce anything (a saved file, a form post) into a valid profile. Unknown keys are
    dropped, wrong types become defaults, long values are capped."""
    raw = raw if isinstance(raw, dict) else {}
    saved_visibility = raw.get("visibility") if isinstance(raw.get("visibility"), dict) else {}
    profile: dict[str, Any] = {"version": SCHEMA_VERSION, "visibility": {}}
    for section in SECTIONS:
        key = section["key"]
        if section["visibility"] is not None:
            chosen = saved_visibility.get(key)
            profile["visibility"][key] = chosen if chosen in VISIBILITY_VALUES else section["visibility"]
        profile[key] = _fields(section["fields"], raw.get(key))
    return profile


def _fields(fields: list[dict[str, Any]], raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {f["key"]: _value(f, raw.get(f["key"])) for f in fields}


def _value(field: dict[str, Any], value: Any) -> Any:
    kind = field["type"]
    if kind in ("text", "textarea"):
        limit = MAX_LONG_TEXT if kind == "textarea" else MAX_TEXT
        return value.strip()[:limit] if isinstance(value, str) else ""
    if kind == "select":
        return value if value in field["options"] else field.get("default", field["options"][0])
    if kind == "bool":
        return value if isinstance(value, bool) else field.get("default", False)
    if kind == "tags":
        return _tags(value)
    if kind == "list":
        items = []
        for item in value if isinstance(value, list) else []:
            clean = _fields(field["fields"], item)
            required = [f["key"] for f in field["fields"] if f.get("required")]
            if all(clean[k] for k in required):
                items.append(clean)
        return items[:MAX_ITEMS]
    raise ValueError(f"unknown field type {kind}")


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    seen: dict[str, str] = {}
    for tag in value if isinstance(value, list) else []:
        if isinstance(tag, str) and tag.strip():
            text = " ".join(tag.split())[:MAX_TEXT]
            seen.setdefault(text.lower(), text)
    return list(seen.values())[:MAX_TAGS]
