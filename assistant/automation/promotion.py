"""Turns requests that keep going through the live agent into instant automations,
so routine tasks stop costing tokens over the life of the assistant.

How it works, end to end (see AGENTS.md section 12 for the rule this implements):

  1. Every turn's final reply may end with a hidden [[TASK: task_type]] marker
     (see assistant/agents/rules.py). Claude adds one when a request is a general,
     repeatable shape of task ("play_song", "search_wikipedia", "open_settings_page")
     and leaves it off for one-off or personal requests. Markers are stripped before
     anything is spoken or shown (`extract_task`).
  2. `PromotionStore` counts how often each task type has gone through the agent
     rather than an existing automation. Once a type is frequent (`auto_after`), or
     the user explicitly says "remember that" / "teach yourself that", Controller
     fires a background turn asking Claude to write automations/<type>.toml itself,
     from what it just did. Below that count, if a turn was expensive (many tool
     calls or real cost), Nova asks once by voice before writing anything.
  3. When a saved automation later fails a step, Controller falls back to the agent
     for that one turn (see Controller._on_macro_done) and then fires another
     background turn asking Claude to patch the automation file, using the failure
     and its own successful fallback run as the fix's evidence.

This module only tracks counts and builds prompts; Controller does the dispatching
and Claude does the actual writing/patching, using its normal file tools.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

TASK_MARKER = re.compile(r"\[\[TASK:\s*(?P<type>[a-z][a-z0-9_]{1,40})\s*\]\]", re.I)

DECISION_NONE = "none"
DECISION_ASK = "ask"
DECISION_AUTO = "auto"


def strip_task_marker(text: str) -> str:
    return TASK_MARKER.sub("", text).strip()


# A reply that says it already taught Nova something. Asking "want me to save that?"
# straight after it has just saved something is the one question never worth asking.
SELF_TAUGHT = re.compile(
    r"\b(?:taught|learned)\b|\binstant command\b|\bworks instantly\b|\bwithout asking me\b",
    re.I)


def taught_itself(summary: str) -> bool:
    """True when the agent's own reply says this turn already taught Nova something."""
    return bool(SELF_TAUGHT.search(summary or ""))


def extract_task(text: str) -> tuple[str, str]:
    """Pull the [[TASK: type]] marker (if any) out of an agent reply.
    Returns (task_type or "", the reply with the marker removed)."""
    m = TASK_MARKER.search(text)
    if not m:
        return "", text
    return m.group("type").lower(), strip_task_marker(text)


@dataclass
class _TaskStats:
    count: int = 0
    promoted: bool = False
    asked: bool = False
    total_cost_usd: float = 0.0
    total_turns: int = 0
    last_utterance: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"count": self.count, "promoted": self.promoted, "asked": self.asked,
                "total_cost_usd": self.total_cost_usd, "total_turns": self.total_turns,
                "last_utterance": self.last_utterance, "first_seen": self.first_seen, "last_seen": self.last_seen}

    @classmethod
    def from_dict(cls, data: dict) -> "_TaskStats":
        return cls(count=data.get("count", 0), promoted=data.get("promoted", False), asked=data.get("asked", False),
                    total_cost_usd=data.get("total_cost_usd", 0.0), total_turns=data.get("total_turns", 0),
                    last_utterance=data.get("last_utterance", ""), first_seen=data.get("first_seen", time.time()),
                    last_seen=data.get("last_seen", time.time()))


class PromotionStore:
    """How often each task type has gone through the agent, persisted to a small JSON
    file next to the other per-install data (data/task_frequency.json)."""

    def __init__(self, path: Path, auto_after: int = 3, ask_min_turns: int = 4,
                 ask_min_cost_usd: float = 0.01, ask_min_seconds: float = 90) -> None:
        self.path = path
        self.auto_after = max(1, auto_after)
        self.ask_min_turns = ask_min_turns
        self.ask_min_cost_usd = ask_min_cost_usd
        self.ask_min_seconds = ask_min_seconds
        self._tasks: dict[str, _TaskStats] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._tasks = {k: _TaskStats.from_dict(v) for k, v in data.items()}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({k: v.to_dict() for k, v in self._tasks.items()}, indent=2),
                                  encoding="utf-8")
        except OSError:
            pass

    def record(self, task_type: str, utterance: str, cost_usd: float | None, turns: int | None,
               seconds: float | None = None) -> str:
        """Log one more agent turn of this type and say what Nova should do about it:
        DECISION_NONE, DECISION_ASK (confirm by voice first) or DECISION_AUTO (just write it)."""
        stats = self._tasks.setdefault(task_type, _TaskStats())
        if stats.promoted:
            return DECISION_NONE  # already has (or is getting) an automation
        stats.count += 1
        stats.total_cost_usd += cost_usd or 0.0
        stats.total_turns += turns or 0
        stats.last_utterance = utterance
        stats.last_seen = time.time()
        self._save()
        if stats.count >= self.auto_after:
            return DECISION_AUTO
        # Expensive in any of the three currencies that matter: money, tool calls, or
        # your time sitting there waiting for it.
        expensive = ((cost_usd or 0) >= self.ask_min_cost_usd
                     or (turns or 0) >= self.ask_min_turns
                     or (seconds or 0) >= self.ask_min_seconds)
        if expensive and not stats.asked:
            return DECISION_ASK
        return DECISION_NONE

    def mark_promoted(self, task_type: str) -> None:
        self._tasks.setdefault(task_type, _TaskStats()).promoted = True
        self._save()

    def mark_asked(self, task_type: str) -> None:
        self._tasks.setdefault(task_type, _TaskStats()).asked = True
        self._save()

    def forget(self, task_type: str) -> None:
        """The automation for this type turned out wrong (or the user asked): go back
        to asking the agent every time, and let it be promoted again later."""
        self._tasks.pop(task_type, None)
        self._save()


def write_prompt(task_type: str, utterance: str) -> str:
    """Ask Claude to turn what it just did into a saved automation."""
    return (
        f'You\'ve now handled requests shaped like "{utterance}" (task type: {task_type}) as full turns several '
        f"times. Per AGENTS.md's automation promotion rule, save this as automations/{task_type}.toml so it runs "
        "instantly next time without a model. Follow the format in automations/README.md exactly: a `phrases` "
        "list with a {placeholder} for whatever part of the request varies (a song name, a search term, ...), "
        "and [[steps]] using the existing step vocabulary (open, click, browser_goto, browser_fill, media, "
        f'say, ...). Base the steps on how you actually completed "{utterance}" earlier in this conversation. '
        "If a similarly named automation already exists, improve it instead of overwriting something unrelated. "
        "Reply with exactly one short spoken sentence confirming what you saved."
    )


def fix_prompt(macro_name: str, macro_path: str, utterance: str, step: int | None, error: str) -> str:
    """Ask Claude to repair an automation that just failed, using its own successful
    fallback run of the same request as the evidence for what should have happened."""
    where = f"step {step}" if step else "a step"
    return (
        f'The automation "{macro_name}" ({macro_path}) just failed at {where}: {error or "no detail"}, while '
        f'trying to do: "{utterance}". You completed that exact request yourself just now, successfully. Open '
        f"{macro_path} and patch it (fix the selector or control name, add an optional fallback step, or adjust "
        "the phrase capture) so this case works next time without a model. Keep the change minimal, follow "
        "automations/README.md's format, and don't break its other phrases. Reply with exactly one short spoken "
        "sentence confirming what you changed."
    )
