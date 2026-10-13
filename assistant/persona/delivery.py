"""How Nova's voice should sound: when to laugh, smile, soften or stress a word.

Fish's S2 voice performs delivery tags written in square brackets -- [chuckles],
[warmly], [emphasis] -- placed before the words they colour (audio/fish.py). A loose
"use a tag where a person would" got used rarely and unevenly: a model forgets a rule
it has to interpret every time. So this is a rule with structure -- a table from the
moment to the tag, a count, a format, and examples -- given to every brain that writes
a spoken reply, at two strengths:

    talk   conversation modes: most replies carry one, at most two
    task   everything else: none or one, for warmth or the one fact that matters

And a safety net in code: models still write stage directions the way people do in
chat -- *laughs*, (smiles), "haha", an emoji -- which the voice would read out or skip.
`normalise` turns those into the tags the voice actually performs.
"""

from __future__ import annotations

import re

# The moment -> the tag(s) for it. The left side is what the model looks for in its own
# reply; the right side is exactly what to write.
MOMENTS = (
    ("something funny, or teasing them a little", "[chuckles] or [laughs softly]; [laughs] only when it's genuinely funny"),
    ("a greeting, good news for them, reassurance", "[warmly]"),
    ("the one word, number or name they must not miss", "[emphasis] just before it"),
    ("a great idea, a win, real enthusiasm", "[excited]"),
    ("bad news, a mistake, something hard for them", "[gently]"),
    ("weighing something up before an opinion", "[thoughtful]"),
    ("irony, sarcasm or teasing (at them too, if their profile welcomes it)", "[dryly], [sighs] or [teasing]"),
    ("an aside or a small secret", "[whispering]"),
)

FORMAT = ("Write a tag in square brackets right before the words it colours: lowercase, one to four words. "
          "It lasts until the next tag. Never at the very end of a reply, never inside a word.")

NEVER = ("Never a tag on every sentence; never [laughs] at bad news or an error; never a tag that contradicts "
         "the words. Don't write stage directions or emotes instead (*smiles*, (laughs), haha, emoji): use the tag.")

EXAMPLES = (
    "[warmly] Morning. [chuckles] You're up early for once.",
    "Done. The report is in Documents, and it's due [emphasis] Friday.",
    "[excited] Oh, that's a good one. [thoughtful] Though what happens when two people book the same slot?",
    "[gently] That didn't go through: the site timed out. I'll try once more.",
)

COUNT = {
    "talk": "Most replies carry one tag; never more than two. A reply with no feeling in it at all sounds flat.",
    "task": "None or one per reply: [warmly] for a greeting or good news, [emphasis] on the fact that matters, "
            "[gently] for bad news. Skip it for plain answers.",
}


def guide(level: str = "task") -> str:
    """The delivery rule for a prompt. `talk` carries the full table; `task` is shorter,
    because it goes into every free-model turn, where tokens are rationed per minute."""
    head = "Your voice performs delivery tags; they are never shown or read out."
    if level == "talk":
        table = "\n".join(f"  - {moment}: {tag}" for moment, tag in MOMENTS)
        examples = "\n".join(f"  {e}" for e in EXAMPLES)
        return (f"{head}\n{FORMAT}\nWhich tag, by the moment:\n{table}\n{COUNT['talk']}\n{NEVER}\n"
                f"Examples:\n{examples}")
    return TASK_GUIDE


# The same rule in one sentence, for every task turn: about 65 tokens instead of 190, since
# a free-model turn resends its prompt on every tool round within a per-minute budget.
TASK_GUIDE = ("Delivery tags (your voice performs them; never shown): put one in square brackets right before "
              "the words it colours -- [warmly] for a greeting or good news, [emphasis] before the one fact that "
              "matters, [chuckles] when something is funny, [gently] for bad news. At most one per reply; none for "
              "plain answers. Never write *smiles*, haha or emoji instead.")


# -- the safety net -------------------------------------------------------------------------
_EMOTE_WORDS = {
    "laughs": "laughs", "laugh": "laughs", "laughing": "laughs", "chuckles": "chuckles", "chuckle": "chuckles",
    "giggles": "laughs softly", "grins": "warmly", "smiles": "warmly", "smile": "warmly", "smiling": "warmly",
    "sighs": "sighs", "sigh": "sighs", "whispers": "whispering", "winks": "playfully", "gasps": "excited",
}
_EMOTE = re.compile(r"[*(]\s*(" + "|".join(sorted(_EMOTE_WORDS, key=len, reverse=True)) + r")(?:\s+\w+){0,2}\s*[*)]", re.I)
_HAHA = re.compile(r"\b(?:ha(?:ha)+|he(?:he)+|lol|lmao)\b[!.,]*", re.I)
_EMOJI = {"😂": "laughs", "🤣": "laughs", "😄": "chuckles", "😆": "chuckles", "😊": "warmly", "🙂": "warmly",
          "😉": "playfully", "😅": "laughs softly", "😮‍💨": "sighs", "🙄": "dryly", "🤫": "whispering", "🎉": "excited"}


def normalise(text: str) -> str:
    """Stage directions and emotes -> the tags the voice performs. Other emoji are dropped:
    the voice can't say them and the screen doesn't need them in a spoken reply."""
    if not text:
        return text
    text = _EMOTE.sub(lambda m: f"[{_EMOTE_WORDS[m.group(1).lower()]}]", text)
    text = _HAHA.sub("[laughs]", text)
    for emoji, tag in _EMOJI.items():
        text = text.replace(emoji, f" [{tag}] ")
    text = _OTHER_EMOJI.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    # A tag at the very end ("That's brilliant haha") colours nothing, so it moves to the
    # start of the last sentence, where it does.
    end = re.search(r"\s*(\[[^\]\n]{1,40}\])\s*$", text)
    if end:
        body = text[: end.start()].rstrip()
        starts = [m.end() for m in re.finditer(r"[.!?]\s+", body)]
        cut = starts[-1] if starts else 0
        text = f"{body[:cut]}{end.group(1)} {body[cut:]}".strip() if body else ""
    return text


_OTHER_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️‍]")
