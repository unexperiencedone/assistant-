"""Is this something to do, or something said? One judgement per turn, no modes.

Talk modes ("let's chat" ... "back to work") made the owner say which kind of thing he
was about to say, and while one was on, every sentence was talk: asking for something
meant leaving the conversation first. That broke the thread both ways. This replaces the
switch with a judgement made on every turn that strict matching did not already claim:

    act   he wants something done, found out, or answered (the existing cascade:
          loose matching, the classifier, an agent with tools)
    talk  conversation: chatting, reacting, sharing a thought or a feeling, thinking out
          loud together (a free model, no tools, the same thread)

Cheapest first. Rules decide most turns instantly; a free model (Groq, the background
key lane) decides the rest with the last few turns in view; and when nothing decides,
the answer leans on what was happening: mid-conversation, an unclear sentence is talk.
That keeps the one safety property the modes had -- a bare name said while chatting
("gulzar ali" once became a phone call) never becomes an action -- without the switch.

Commands the strict matchers recognise never reach this module: "play tera didarva"
runs mid-conversation exactly as it would anywhere else.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

ACT, TALK = "act", "talk"

_ACT_START = re.compile(
    r"^(?:(?:ok(?:ay)?|so|and|also|then|now|just|alright|right)[\s,]+)?"
    r"(?:please\s+|can you\s+|could you\s+|would you\s+|will you\s+|go ahead and\s+|i need you to\s+|i want you to\s+)?"
    r"(?:open|close|launch|start|stop|play|pause|resume|skip|send|email|mail|message|text|call|dial|ring|schedule|"
    r"remind|set|add|remove|delete|cancel|create|make|build|write|draft|post|publish|search|find|look up|google|"
    r"check|show|list|read|summari[sz]e|translate|install|uninstall|update|upgrade|run|fix|debug|deploy|commit|"
    r"push|pull|download|upload|record|take|move|copy|rename|turn (?:on|off)|switch|enable|disable|book|order|"
    r"buy|pay|plan|explain|convert|calculate|compare|clean|organi[sz]e|save|export|print|share|open up|bring up|"
    r"pull up|go to|navigate|mute|unmute|volume|increase|decrease|lower|raise|minimi[sz]e|maximi[sz]e|"
    r"note|log|track|analy[sz]e|review|test|generate|design|draw|fill|submit|reply|forward|archive)\b", re.I)

_FACT_QUESTION = re.compile(
    r"^(?:what(?:'s| is| are| was| were)? (?:the |a |an )?(?:time|date|weather|price|score|news|status|capital|meaning|"
    r"difference|population|latest|best way|definition)|who (?:is|was|won|made|invented)|when (?:is|was|does|did)|"
    r"where (?:is|are|can)|how (?:much|many|long|far|do i|do you|to|does)|which )", re.I)

_TALK = re.compile(
    r"^(?:hi|hello|hey there|hey|hiya|yo|sup|good (?:morning|afternoon|evening|night)|gm|gn|"
    r"thanks?|thank you|thx|ty|cheers|"
    r"ok(?:ay)?|cool|nice|great|awesome|amazing|lol|lmao|haha+|hehe|wow|whoa|really|right|true|exactly|"
    r"yeah|yep|yup|nope|nah|no way|interesting|hmm+|oh+|ah+|fair(?: enough)?|makes sense|i see|got it|"
    r"same|me too|agreed|totally|definitely|of course|sure)[\s!.?,]*$"
    r"|^(?:(?:honestly|so|well|man|bro|ugh|yeah|hey|hi|hello|oh|ok(?:ay)?|anyway)[\s,!.]+)*"
    r"(?:how are you|how(?:'s| is) (?:it going|your day|life)|what(?:'s| is) up|what are you up to|"
    r"what do you think|what(?:'s| is) your (?:opinion|take|favou?rite)|do you (?:like|think|believe|ever|remember)|"
    r"are you (?:happy|ok|okay|real|alive|bored|tired)|tell me (?:a joke|something (?:interesting|fun|new)|about yourself)|"
    r"let'?s (?:talk|chat)|i(?:'m| am) (?:so |really |kind of |kinda |pretty |a bit |a little |very |super |quite |just )*(?:tired|bored|sad|happy|stressed|excited|"
    r"anxious|nervous|worried|confused|done|hungry|sleepy|exhausted|annoyed|proud)|i feel|i(?: just)? (?:think|guess|"
    r"believe|wonder|wish|love|hate|miss)|that(?:'s| is) (?:funny|cool|great|interesting|sad|crazy|wild|nice|true|fair))\b",
    re.I)

_BRAINSTORM = re.compile(r"^(?:let'?s |can we |we should )?brainstorm(?:ing)?(?: (?:some )?ideas?)?(?: (?:about|on|for) (?P<topic>.+))?$", re.I)

SYSTEM = """You decide, for one thing a person said to their voice assistant, whether it is a REQUEST or TALK.

REQUEST ("act"): they want something done on their computer or online, something found out or looked up,
a factual question answered, something written, made, sent, scheduled, opened, played, fixed.
TALK ("talk"): conversation. Chatting, reacting, joking, sharing a thought, a feeling or an opinion,
thinking out loud, asking the assistant's opinion, or continuing the conversation in the recent turns.

Use the recent turns: a short reply to something conversational is talk. A name or a fragment said
in the middle of a conversation is talk unless it clearly asks for an action.

Reply with JSON only: {"kind": "act" or "talk", "confidence": 0 to 1}"""


@dataclass
class Judgement:
    kind: str                     # ACT | TALK
    source: str                   # "rule" | "model" | "lean"
    style: str = ""               # "brainstorm" when asked for one
    topic: str = ""
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = " ".join((text or "").split()).strip()
    return re.sub(r"^(?:nova|hey nova|ok nova|okay nova)[\s,!.]+", "", text, flags=re.I).strip()


def by_rules(text: str, conversing: bool) -> Judgement | None:
    said = _clean(text)
    low = said.lower().rstrip(" .!?")
    if not said:
        return Judgement(TALK, "rule")
    brainstorm = _BRAINSTORM.match(low)
    if brainstorm:
        return Judgement(TALK, "rule", style="brainstorm", topic=(brainstorm.group("topic") or "").strip())
    if _TALK.match(said):
        return Judgement(TALK, "rule")
    if _ACT_START.match(said) or _FACT_QUESTION.match(said):
        return Judgement(ACT, "rule")
    if conversing and len(low.split()) <= 4:
        # "gulzar ali", "the blue one", "maybe tomorrow": in a conversation, a fragment
        # is a reply, never an order.
        return Judgement(TALK, "rule", confidence=0.7)
    return None


def by_model(text: str, recent: list[tuple[str, str]], *, api_key: str, model: str,
             timeout: float = 4.0, url: str = "https://api.groq.com/openai/v1/chat/completions") -> Judgement | None:
    if not api_key:
        return None
    turns = "\n".join(f"{who}: {what}" for who, what in recent[-6:]) or "(none)"
    # No strict JSON mode and room to think: Groq's reasoning models spend tokens before
    # answering, and a 40-token cap with json_object left them nothing, every time.
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 400, "reasoning_effort": "low",
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": f"Recent turns:\n{turns}\n\nThey just said: {text}"}],
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
        "User-Agent": "Nova/1.0 (+https://github.com/)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = json.loads(response.read())["choices"][0]["message"]["content"] or ""
        found = re.search(r"\{.*\}", content, re.S)
        answer = json.loads(found.group(0)) if found else {}
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    kind = str(answer.get("kind", "")).lower()
    if kind not in (ACT, TALK):
        return None
    try:
        confidence = float(answer.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return Judgement(kind, "model", confidence=confidence)


def judge(text: str, recent: list[tuple[str, str]], conversing: bool,
          ask_model: Callable[[str, list[tuple[str, str]]], Judgement | None] | None = None) -> Judgement:
    """The decision for one turn. Never raises; always answers."""
    ruled = by_rules(text, conversing)
    if ruled is not None:
        return ruled
    if ask_model is not None:
        try:
            modelled = ask_model(text, recent)
        except Exception:
            modelled = None
        if modelled is not None and (modelled.confidence >= 0.55 or modelled.kind == (TALK if conversing else ACT)):
            return modelled
    # Nothing decided: lean on what was happening.
    return Judgement(TALK if conversing else ACT, "lean", confidence=0.5)
