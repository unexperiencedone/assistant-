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
        # -- the phone (assistant/phone, bridged through Termux over Tailscale) --------
        # "text 555 saying hi", "send a message to 555", "send the same message on 555":
        # the number is the only required part -- an empty body reuses the last one, and
        # if there is no last one the handler asks what to say.
        ("phone_sms", r"^(?:text|sms|send)\s+(?:(?:a|an|the|another|same)\s+)*(?:text|sms|message)?\s*"
                      r"(?:from\s+(?:my\s+)?phone\s+)?(?:to|on|at)?\s*(?P<number>\+?[\d][\d ()-]{2,19})"
                      r"(?:[,:]?\s+(?:saying|that says|that|with)?\s*(?P<body>.+))?$"),
        # By name, resolved against your contacts before anything is dialled or sent.
        ("phone_sms_name", r"^(?:text|message|sms)\s+(?P<name>(?!it\b|its\b|the\b|a\b|an\b|this\b|that\b|them\b|him\b|her\b|his\b|us\b|me\b|my\b|you\b|your\b|back\b|off\b|out\b|in\b|up\b|on\b|for\b|about\b|later\b|now\b|again\b|someone\b|somebody\b|everyone\b)[a-z][a-z.'-]{1,20}(?:\s+[a-z][a-z.'-]{1,20}){0,2})\s+"
                           r"(?:saying|that|with)\s+(?P<body>.+)$"),
        ("identity", r"^(?:who|what)\s+(?:are|r)\s+(?:you|u)\b"
                     r"|^what(?:.?s| is)\s+your\s+name\b"
                     r"|^introduce\s+yourself\b"
                     r"|^are\s+you\s+(?:an?\s+)?(?:ai|bot|robot|human|real|claude|chatgpt|gemini)\b"),
        ("phone_find", r"^(?:find|locate|ping|ring|where.s|where\s+is)\s+(?:my\s+)?phone\b"),
        ("phone_call_name", r"^(?:call|ring|dial|phone)\s+(?P<name>(?!it\b|its\b|the\b|a\b|an\b|this\b|that\b|them\b|him\b|her\b|his\b|us\b|me\b|my\b|you\b|your\b|back\b|off\b|out\b|in\b|up\b|on\b|for\b|about\b|later\b|now\b|again\b|someone\b|somebody\b|everyone\b)[a-z][a-z.'-]{1,20}(?:\s+[a-z][a-z.'-]{1,20}){0,2})\s*$"),
        # A number, never a bare word: "call it a day" must not reach the dialler. The
        # phone can be named either side of the number ("call through my phone on 555",
        # "dial 555 on my phone") because that is how people actually say it out loud.
        ("phone_call", r"^(?:call|ring|dial|phone)\s+"
                       r"(?:(?:through|from|on|using|via|with)\s+)?(?:(?:my|the)\s+)?(?:phone\s+)?"
                       r"(?:(?:to|on|at)\s+)?(?P<number>\+?[\d][\d ()-]{5,19})"
                       r"(?:\s+(?:on|from|through|using|via|with)\s+(?:my\s+)?phone)?\s*$"),
        ("phone_torch", r"^(?:turn\s+)?(?:(?P<state>on|off)\s+(?:the\s+)?(?:torch|flashlight)"
                        r"|(?:the\s+)?(?:torch|flashlight)\s+(?P<state2>on|off))\b"),
        ("phone_battery", r"^(?:what'?s|how'?s|check)?\s*(?:my |the )?phone(?:'s)?\s*battery\b"
                          r"|^battery\s+(?:on|of)\s+(?:my\s+)?phone\b"),
        ("phone_buzz", r"^(?:buzz|vibrate)\s+my\s+phone\b"),
        ("phone_open", r"^open\s+(?P<target>.+?)\s+on\s+(?:my\s+)?phone\b"),
        ("phone_notify", r"^(?:notify|ping)\s+my\s+phone\s+(?:saying|with|that)?\s*(?P<body>.+)$"),
        ("phone_status", r"^(?:is\s+)?my\s+phone\s+(?:there|on(?:line)?|connected|reachable)\b"),
        ("cancel_agent", r"^(?:cancel|abort|kill|stop)(?:\s+(?:the|all|both|every))*\s*(?:task|tasks|job|jobs|agent|work|run|execution|everything|it all)\b"),
        ("stop_speaking", r"^(?:stop|quiet|shush|shut up|be quiet|enough|that's enough|never ?mind)[.!]?$"),
        ("switch_backend", r"^(?:use|switch to|change to|swap to|go with)\s+(?:the\s+)?(?P<backend>claude(?: code)?|cloud|clod|anti[- ]?gravity|gravity|agy|gemini|open ?router|router|free models?|groq|gro[ck]k?|rock|llama|lama|fast model)\b"),
        ("delegate", r"^(?:tell|ask|have|get)\s+(?P<backend>claude(?: code)?|cloud|anti[- ]?gravity|gravity|agy|groq|gro[ck]k?|llama)\s+(?:to\s+)?(?P<task>.+)$"),
        ("new_plan", r"^(?:let'?s|start|begin|new|make)(?: a)? (?:new )?plan(?:ning)?\b[\s,:]*(?:(?:for|to|about|out)\s+)?(?P<topic>.*)$"),
        ("clear_plan", r"^(?:clear|scrap|reset|delete|drop|forget|throw away)(?: all)?(?: the)? plans?\b"),
        ("remove_step", r"^(?:remove|delete|drop)(?: step)? (?:number )?(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"),
        ("move_step", r"^(?:move|shift)(?: step)? (?:number )?(?P<number>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:to (?:position |step )?(?P<to>\d+|one|two|three|four|five|six|seven|eight|nine|ten)|(?P<direction>up|down|first|last|to the top|to the end))\b"),
        ("read_plan", r"^(?:read|repeat|what'?s|what is|show|tell me)(?: me)?(?: back)?(?: the)? plan\b|^read it back\b"),
        ("resume_queue", r"^resume(?: the)?(?: (?:queue|queued|waiting)(?: (?:requests|tasks|ones))?)?[.!]?$|^(?:start|run|continue)(?: the)? (?:queue|(?:queued|waiting) (?:requests|tasks|ones))[.!]?$"),
        ("clear_queue", r"^(?:clear|empty|forget|drop|cancel)(?: the)? (?:queue|queued (?:requests|tasks)|waiting (?:requests|tasks))\b"),
        ("execute_plan", r"^(?:go ahead|execute|run|start|kick off|ship|do)(?: it| the plan| this| that| work(?:ing)?)?[.!]?$|^(?:execute|run|start)(?: the)? plan\b|^let'?s do it\b"),
        ("status", r"^(?:status|progress|what'?s (?:it|the agent|claude|antigravity) doing|how'?s it going|are you (?:done|finished))\b"),
        ("open_dashboard", r"^(?:open|show)(?: me)?(?: the)? (?:dashboard|visuali[sz]ation|board|canvas|flow ?chart|graph)\b|^visuali[sz]e(?: it| the plan)?\b"),
        ("new_session", r"^(?:new|fresh|reset)(?: agent)? (?:session|conversation|context)\b"),
        ("time", r"^what(?:'s| is) the time\b|^what time is it\b"),
        ("news_briefing", r"^(?:what'?s|what is|pull|get|give|read|run|fetch|show|any)?\s*(?:me)?\s*(?:up)?\s*(?:a|the|my)?\s*(?:news(?:\s+(?:briefing|headlines|update|summary))?|headlines|briefing)(?:\s+(?:briefing|update))?(?:\s+(?:for|on|about)\s+(?P<topic>.+?))?(?:\s+(?:today|now|right now|this morning|please))?[.!?]*$"),
        ("recap", r"^(?:what did (?:we|i|you) (?:do|work on|get done)|(?:give me a |quick )?recap|summari[sz]e (?:the |this |my |our )?(?:session|work|day))(?:\s+(?:in |for |from |of )?(?P<when>today|yesterday|this session|(?:the )?(?:last|previous) session))?\b"),
        ("list_automations", r"^(?:list|show|what are)(?: me)?(?: my| the)? (?:automations|macros|routines|scripts)\b"),
        ("remember_automation", r"^(?:remember|teach yourself|learn from)\s+(?:how you (?:did|do)\s+)?(?:that|this|it)\b.*$"
                                 r"|^(?:save|turn)\s+(?:that|this|it)(?:\s+one)?\s+(?:as|into)\s+(?:an?\s+)?(?:automation|instant\s+command|macro|script)\b.*$"),
        # Local computer commands (handled by assistant/system/voice.py; they fall
        # through to the agent when nothing on disk matches confidently).
        ("wrong_result", r"^(?:no[,.!]?\s+)?(?:(?:not|wrong)\s+(?:that|this|the)?\s*(?:one|file|folder|app)|(?:(?:open|i meant|i mean)\s+)?the other (?:one|file|folder|app)|that'?s (?:the )?wrong(?: one| file| folder| app)?)[.!]?$"),
        ("open_result", r"^open (?:the )?(?:number |result |item )?(?P<number>\d+|one|two|three|four|five|first|second|third|fourth|fifth)(?: one| result)?$"),
        ("reveal_result", r"^(?:show|reveal|open) (?:it|that|this) in (?:the )?(?:file )?(?:explorer|folder)\b"),
        ("find_duplicates", r"^(?:find|show|look for|check for|scan for|any) (?:the |me )?duplicates?(?: files)?(?:\s+in\s+(?:my\s+|the\s+)?(?P<where>.+?))?(?: folder)?$"),
        ("reindex", r"^(?:re-?index|rebuild (?:the )?index|refresh (?:the )?index|update (?:the )?index|rescan (?:my )?files)\b"),
        ("most_used", r"^(?:what are |show )?(?:me )?my (?:most used|top|favou?rite) (?P<kind>apps|files|folders)\b"),
        ("find_item", r"^(?:find|search for|locate|where(?:'s| is| are))\s+(?:my\s+|the\s+|a\s+|an\s+)?(?:(?P<kind>file|folder|directory|app|application|program)\s+(?:called\s+|named\s+)?)?(?P<target>.+?)(?:\s+(?P<kind2>file|folder|directory|app))?$"),
        ("open_item", r"^(?:open|launch)\s+(?:up\s+)?(?:my\s+|the\s+|a\s+)?(?:(?P<kind>file|folder|directory|app|application|program)\s+(?:called\s+|named\s+)?)?(?P<target>.+?)(?:\s+(?P<kind2>file|folder|directory|app))?$"),
    ]
]

LOCAL_INTENTS = {"wrong_result", "open_result", "reveal_result", "find_duplicates", "reindex", "most_used", "find_item", "open_item"}

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
            for key in ("number", "to"):
                if key in args:
                    args[key] = _NUMBERS.get(args[key].lower(), args[key])
            return Intent(name, args)
    return None


# How speech recognition tends to spell a short name it doesn't know.
_WAKE_MISHEARINGS = {
    "nova": ["novah", "nover", "novo", "nava", "noba", "nuova", "noah", "noaa", "no va"],
}
# Only greetings and fillers may come before the wake word: "okay, hey Nova, ..."
_WAKE_LEAD = r"(?:(?:hey|hi|hello|okay|ok|yo|oh|so|um+|uh+|alright|and)\b[\s,.!]*)*"
_wake_patterns: dict[tuple[str, tuple[str, ...]], re.Pattern[str]] = {}


def _wake_pattern(wake_word: str, aliases: tuple[str, ...]) -> re.Pattern[str]:
    key = (wake_word.lower(), aliases)
    if key not in _wake_patterns:
        names = {key[0], *(a.lower() for a in aliases), *_WAKE_MISHEARINGS.get(key[0], [])}
        spelled = (re.escape(n).replace(r"\ ", r"[\s-]?") for n in sorted(names, key=len, reverse=True))
        _wake_patterns[key] = re.compile(rf"^\W*{_WAKE_LEAD}(?:{'|'.join(spelled)})(?:'s)?\b[\s,.!?:;-]*", re.I)
    return _wake_patterns[key]


def strip_wake_word(text: str, wake_word: str, aliases: list[str] | tuple[str, ...] = ()) -> tuple[bool, str]:
    """Return (heard_wake_word, remaining_text). The wake word has to open the sentence,
    after at most a greeting or filler, so "I told Nova..." on a video doesn't count."""
    if not wake_word:
        return True, text
    m = _wake_pattern(wake_word, tuple(aliases)).match(text)
    return (True, text[m.end():]) if m else (False, text)


# One plain phrasing per intent worth catching loosely, for assistant/matching.py. The
# strict rules above stay the only fast path; these are what a near miss is compared
# against before a request is given to an agent. Slot names must match the handler's
# keyword arguments, because that is how the guess is called.
EXAMPLES: tuple[tuple[str, str], ...] = (
    ("phone_find", "find my phone"),
    ("phone_find", "where is my phone"),
    ("phone_torch", "turn on the torch"),
    ("phone_torch_off", "turn off the torch"),
    ("phone_battery", "how much battery is on my phone"),
    ("phone_call_name", "call {name}"),
    ("phone_sms_name", "text {name} saying {body}"),
    ("phone_notify", "send a note to my phone saying {body}"),
)


def example_corpus() -> list[tuple[str, str, str]]:
    return [("intent", name, phrase) for name, phrase in EXAMPLES]
