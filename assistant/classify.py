"""The third tier: when the rules cannot decide, ask a free model which script it is.

The cascade in docs/overview.md ends with an agent turn, which is the expensive answer.
But a great many misses are not hard problems -- they are the same request worded in a
way nobody wrote a phrase for. Deciding *which* of a known list of scripts somebody
meant is a tiny classification job, and a free Groq model does it in about half a second.

So this sits between `matching.py` and the agent:

    rules are sure        -> run the script                  (microseconds, free)
    rules are unsure      -> ask Groq, grounded in the list  (~0.5s, free)
    Groq says "none"      -> hand it to Claude               (the expensive path)

Two things make this safe rather than a second place for a model to be creative:

1. **It chooses, it does not compose.** The reply is validated against the corpus. A
   script name that is not in the list, an argument the template never declared, or a
   slot left empty is thrown away and treated as "none". The model cannot invent an
   action, only point at one.
2. **"None" is a first-class answer**, and the prompt says so plainly. A router that
   feels obliged to pick something is worse than no router, because the cost of a wrong
   pick is running the wrong action while the cost of "none" is only the turn we were
   going to spend anyway.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .matching import Guess, segments

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq's edge answers the default urllib agent with a bare Cloudflare 403.
USER_AGENT = "Nova/1.0 (+https://github.com/)"

MIN_CONFIDENCE = 0.6

SYSTEM = """You route a voice assistant's requests to scripts it already has.

You are given a numbered list of scripts. Each has a name and a phrase template; {slot}
marks a value the user supplies.

Choose the ONE script whose meaning matches the request, and fill its slots from the
user's own words. The wording will often differ from the template -- that is the whole
point of asking you. Meaning is what matters, not phrasing.

Answer "none" whenever:
- no script means the same thing as the request;
- the request is a question, a conversation, or asks for something to be written,
  explained, found out, or built;
- you are not sure.

"none" is a good answer and costs nothing. Picking the wrong script makes the assistant
do the wrong thing, which is much worse.

Reply with JSON only, no prose:
{"script": "<name or none>", "args": {"<slot>": "<value>"}, "confidence": <0 to 1>}

Never invent a script name. Never invent an argument name. Use only slots that appear in
that script's template."""


def slots_of(template: str) -> set[str]:
    return {str(value) for kind, value in segments(template) if kind == "slot"}


def catalogue(corpus: list[tuple[str, str, str]]) -> str:
    """The list the model must choose from, one script per line.

    Several phrases for the same script are folded together: the name is what it answers
    with, so showing it twice only invites it to think they are different things.
    """
    by_name: dict[str, list[str]] = {}
    for _route, name, template in corpus:
        by_name.setdefault(name, []).append(template)
    lines = []
    for index, (name, templates) in enumerate(sorted(by_name.items()), start=1):
        shown = " | ".join(templates[:3])
        lines.append(f"{index}. {name} — {shown}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """The model was asked for bare JSON; take it even if it wrapped it in prose."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        return {}
    try:
        return json.loads(found.group(0))
    except ValueError:
        return {}


# Words that mean the value swallowed the request rather than naming its subject. A slot
# is a noun phrase -- "finance", "artificial intelligence" -- and never starts like an
# instruction.
_NOT_A_SUBJECT = {
    "brief", "tell", "give", "show", "pull", "open", "find", "get", "fetch", "read",
    "play", "run", "make", "do", "let", "can", "could", "would", "please",
    "me", "my", "i", "you", "your", "us", "we", "it", "that", "this", "a", "an", "the",
    "some", "any", "about", "on", "for", "up", "of", "with", "and", "to",
}
MAX_SLOT_WORDS = 6


def slots_are_sane(said: str, template: str, args: dict[str, str]) -> bool:
    """Do these slot values look like they came out of what was actually said?

    Four cheap checks, each for a way the model got this wrong in practice:

    1. **Nothing invented.** Every word of the value has to appear in the request.
    2. **Not a whole sentence.** A slot names a subject; past a few words it has stopped
       naming one and started repeating the request.
    3. **Not an instruction.** A value beginning "brief", "tell me", "show" is the
       request wearing the slot's clothes.
    4. **Not the template's own words.** If the script is "{topic} news", a topic
       containing "news" means the split went in the wrong place.
    """
    from .matching import tokens

    said_words = set(tokens(said or "", strip_filler=False))
    literals = {word for word in re.findall(r"[A-Za-z']+", re.sub(r"\{[^}]*\}", " ", template or ""))}
    literals = {word.lower() for word in literals}
    for value in args.values():
        words = [word.lower() for word in re.findall(r"[A-Za-z0-9']+", value)]
        if not words or len(words) > MAX_SLOT_WORDS:
            return False
        if not set(words) <= said_words:
            return False
        if words[0] in _NOT_A_SUBJECT:
            return False
        if set(words) & literals:
            return False
    return True


def validate(answer: dict, corpus: list[tuple[str, str, str]],
             min_confidence: float = MIN_CONFIDENCE, said: str | None = None) -> Guess | None:
    """Turn the model's reply into a Guess, or into nothing.

    Everything is checked against the corpus. The model is a pointer, not a source.
    """
    name = str(answer.get("script", "") or "").strip()
    if not name or name.lower() in ("none", "null", "no", "n/a"):
        return None
    entry = next(((route, known, template) for route, known, template in corpus if known == name), None)
    if entry is None:
        return None                                   # a script that does not exist
    route, _name, template = entry

    try:
        confidence = float(answer.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence:
        return None

    wanted = slots_of(template)
    raw = answer.get("args") or {}
    if not isinstance(raw, dict):
        return None
    args = {key: str(value).strip() for key, value in raw.items()
            if key in wanted and str(value).strip()}
    if wanted - set(args):
        return None                                   # a slot it failed to fill
    # The name was checked against the corpus; the values were not, and that gap showed.
    # Asked "brief me on recent news", the model picked the right script and handed back
    # topic="brief me on recent", which the automation then said back out loud: "Here's
    # the latest on brief me on recent." A pointer, not a source, has to mean the
    # arguments too.
    if said is not None and not slots_are_sane(said, template, args):
        return None
    return Guess(route=route, name=name, args=args, score=confidence, phrase=template)


def classify(said: str, corpus: list[tuple[str, str, str]], *, api_key: str,
             model: str, timeout: float = 6.0,
             min_confidence: float = MIN_CONFIDENCE) -> Guess | None:
    """Ask the model which script this is. Any failure means "none"."""
    if not api_key or not corpus or not said.strip():
        return None
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Scripts:\n{catalogue(corpus)}\n\nRequest: {said}"},
        ],
    }
    request = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None                                   # unreachable, rate-limited, garbled
    choices = payload.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content") or ""
    return validate(_extract_json(content), corpus, min_confidence, said=said)
