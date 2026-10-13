"""A short name for a request, for citing it back.

Two places refer to earlier requests: the history rail on the canvas, and a reply
that finishes while other tasks are still running ("which one was this?"). Both used
to print the request as it was said, which is wrong in both: a transcript is full of
filler ("do one thing about th..."), mishearings, and sometimes an internal prompt
("You've now handled re...") that the user never said at all.

    short_title("hey nova can you please play big hailey for me")  -> "Play big hailey"
    short_title("Thought of day please")                           -> "Thought of day"
    short_title("", task_type="daily_quote")                       -> "Daily quote"

The full text is never lost: the canvas keeps it as the tooltip.
"""

from __future__ import annotations

import re

MAX_WORDS = 5

# Whole leading phrases that carry no meaning, removed repeatedly from the front.
_LEAD = re.compile(
    r"^(?:hey|hi|ok(?:ay)?|so|um+|uh+|well|and|also|now|then|please|kindly|just|"
    r"nova|jarvis|alright|right|"
    r"(?:can|could|would|will) you(?: please)?|"
    r"i (?:want|need|would like) you to|i(?:'d| would) like (?:you )?to|"
    r"do one thing(?: about)?|one more thing|"
    r"go ahead and|let's|let us|"
    r"(?:delegate|hand|pass|send) (?:this |it )?to (?:claude|groq|antigravity|openrouter)(?: to)?|"
    r"(?:ask|use|tell) (?:claude|groq|antigravity|openrouter)(?: to)?)"
    r"[\s,.:;!-]+", re.I)

_TRAIL = re.compile(r"[\s,.:;!?-]+(?:please|for me|now|right now|thanks|thank you|okay|ok)?[\s,.:;!?-]*$", re.I)

# Prompts Nova writes to an agent itself, named for what they are rather than quoted.
_INTERNAL = (
    (re.compile(r"^you've now handled", re.I), "Saving an automation"),
    (re.compile(r"^learning:\s*(.+)", re.I), "Learning {0}"),
    (re.compile(r"^fixing:\s*(.+)", re.I), "Fixing {0}"),
    (re.compile(r"^(?:plan:|make a plan for:?)\s*(.+)", re.I), "Plan: {0}"),
    (re.compile(r"^plan only, don't change anything yet\.\s*(.+)", re.I), "{0}"),
)


def humanize(task_type: str) -> str:
    return " ".join(task_type.replace("-", "_").split("_")).strip()


def short_title(text: str, task_type: str = "", max_words: int = MAX_WORDS) -> str:
    """A few meaningful words naming the request; "" when there's nothing to name."""
    text = " ".join((text or "").split())
    cut = text.endswith(("...", "…"))
    text = text.rstrip(".… ")
    if cut and " " in text:
        text = text.rsplit(" ", 1)[0]   # the last word was cut mid-way: "but be..."
    for pattern, template in _INTERNAL:
        match = pattern.match(text)
        if match:
            inner = [humanize(short_title(g, max_words=max_words)) for g in match.groups()]
            if template.startswith("{0}"):
                return _cap(template.format(*inner))
            return _cap(template.format(*[g[:1].lower() + g[1:] for g in inner]))
    for _ in range(6):
        stripped = _LEAD.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = _TRAIL.sub("", text).strip(" \"'")
    if not text:
        return _cap(humanize(task_type)) if task_type else ""
    words = text.split()
    longer = len(words) > max_words or cut
    words = words[:max_words]
    # End on a content word, never on "the", "of" or "but".
    while len(words) > 1 and words[-1].lower().strip(",") in _WEAK:
        words.pop()
    return _cap(" ".join(words)) + ("…" if longer else "")


def cite(text: str, task_type: str = "") -> str:
    """The name a reply is attributed to when several tasks are in flight: the task
    type the agent itself gave the request if there is one (it names the job, not the
    words), else the short title."""
    if task_type:
        return _cap(humanize(task_type))
    return short_title(text)


_WEAK = {"the", "a", "an", "of", "to", "for", "in", "on", "at", "and", "or", "with", "my", "about", "from", "by", "but", "so", "if", "is"}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
