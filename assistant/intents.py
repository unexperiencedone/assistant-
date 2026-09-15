"""Instant, offline command matching. Runs before anything is sent to the agent,
so control phrases ("stop", "cancel", "use antigravity", "open spotify")
respond in milliseconds and use none of your Claude usage. Anything that
doesn't match goes straight to the agent session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FILLER = re.compile(r"^(?:ok(?:ay)?|hey|so|um+|uh+|please|now|alright)[\s,]+", re.I)


@dataclass
class Intent:
    name: str
    args: dict[str, str] = field(default_factory=dict)


# (intent name, pattern). First match wins, so order matters: specific first.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pattern, re.I))
    for name, pattern in [
        ("quit", r"^(?:goodbye|good ?bye|exit|quit|shut ?down)(?: assistant)?[.!]?$"),
        ("cancel_agent", r"^(?:cancel|abort|kill|stop)(?: the)? (?:task|job|agent|work|run|execution|it all)\b"),
        ("stop_speaking", r"^(?:stop|quiet|shush|shut up|be quiet|enough|that's enough|never ?mind)[.!]?$"),
        ("switch_backend", r"^(?:use|switch to|change to|swap to|go with)\s+(?P<backend>claude(?: code)?|cloud|clod|anti[- ]?gravity|gravity|agy|gemini)\b"),
        ("delegate", r"^(?:tell|ask|have|get)\s+(?P<backend>claude(?: code)?|cloud|anti[- ]?gravity|gravity|agy)\s+(?:to\s+)?(?P<task>.+)$"),
        ("new_plan", r"^(?:let'?s|start|begin|new|make)(?: a)? (?:new )?plan(?:ning)?\b[\s,:]*(?:(?:for|to|about|out)\s+)?(?P<topic>.*)$"),
        ("clear_plan", r"^(?:clear|scrap|reset|delete|forget|throw away)(?: the)? plan\b"),
        ("remove_step", r"^(?:remove|delete|drop)(?: step)? (?:number )?(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"),
        ("read_plan", r"^(?:read|repeat|what'?s|what is|show|tell me)(?: me)?(?: back)?(?: the)? plan\b|^read it back\b"),
        ("execute_plan", r"^(?:go ahead|execute|run|start|kick off|ship|do)(?: it| the plan| this| that| work(?:ing)?)?[.!]?$|^(?:execute|run|start)(?: the)? plan\b|^let'?s do it\b"),
        ("status", r"^(?:status|progress|what'?s (?:it|the agent|claude|antigravity) doing|how'?s it going|are you (?:done|finished))\b"),
        ("open_dashboard", r"^(?:open|show)(?: me)?(?: the)? (?:dashboard|visuali[sz]ation|board|canvas|flow ?chart|graph)\b|^visuali[sz]e(?: it| the plan)?\b"),
        ("new_session", r"^(?:new|fresh|reset)(?: agent)? (?:session|conversation|context)\b"),
        ("time", r"^what(?:'s| is) the time\b|^what time is it\b"),
        ("list_automations", r"^(?:list|show|what are)(?: me)?(?: my| the)? (?:automations|macros|routines|scripts)\b"),
        # Local computer commands (handled by assistant/system/voice.py; they fall
        # through to the agent when nothing on disk matches confidently).
        ("open_result", r"^open (?:the )?(?:number |result |item )?(?P<number>\d+|one|two|three|four|five|first|second|third|fourth|fifth)(?: one| result)?$"),
        ("reveal_result", r"^(?:show|reveal|open) (?:it|that|this) in (?:the )?(?:file )?(?:explorer|folder)\b"),
        ("find_duplicates", r"^(?:find|show|look for|check for|scan for|any) (?:the |me )?duplicates?(?: files)?(?:\s+in\s+(?:my\s+|the\s+)?(?P<where>.+?))?(?: folder)?$"),
        ("reindex", r"^(?:re-?index|rebuild (?:the )?index|refresh (?:the )?index|update (?:the )?index|rescan (?:my )?files)\b"),
        ("most_used", r"^(?:what are |show )?(?:me )?my (?:most used|top|favou?rite) (?P<kind>apps|files|folders)\b"),
        ("find_item", r"^(?:find|search for|locate|where(?:'s| is| are))\s+(?:my\s+|the\s+|a\s+|an\s+)?(?:(?P<kind>file|folder|directory|app|application|program)\s+(?:called\s+|named\s+)?)?(?P<target>.+?)(?:\s+(?P<kind2>file|folder|directory|app))?$"),
        ("open_item", r"^(?:open|launch)\s+(?:up\s+)?(?:my\s+|the\s+|a\s+)?(?:(?P<kind>file|folder|directory|app|application|program)\s+(?:called\s+|named\s+)?)?(?P<target>.+?)(?:\s+(?P<kind2>file|folder|directory|app))?$"),
    ]
]

LOCAL_INTENTS = {"open_result", "reveal_result", "find_duplicates", "reindex", "most_used", "find_item", "open_item"}

_NUMBERS = {w: str(i) for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)}


def normalize(text: str) -> str:
    text = text.strip().strip("\"'")
    previous = None
    while previous != text:
        previous, text = text, _FILLER.sub("", text)
    return text.strip()


def match_intent(text: str) -> Intent | None:
    clean = normalize(text)
    for name, pattern in _RULES:
        m = pattern.search(clean)
        if m:
            args = {k: v.strip(" .?!") for k, v in m.groupdict().items() if v}
            if "number" in args:
                args["number"] = _NUMBERS.get(args["number"].lower(), args["number"])
            return Intent(name, args)
    return None


def strip_wake_word(text: str, wake_word: str) -> tuple[bool, str]:
    """Return (heard_wake_word, remaining_text)."""
    if not wake_word:
        return True, text
    m = re.match(rf"^\W*(?:hey |ok |okay )?{re.escape(wake_word)}\b[\s,.!?]*", text, re.I)
    return (True, text[m.end():]) if m else (False, text)
