"""Catching the same request said a different way.

An automation declares phrases like "play {query} on spotify". `macros.py` turns those
into strict regexes, which is right: a strict match is instant and certain. But speech
varies -- "could you put karan aujla on spotify please", "spotify, play karan aujla" --
and a near miss currently falls all the way through to an agent, which costs a model
turn to do something the machine already knows how to do for free.

This module is the layer between. It only ever resolves to a script that **already
exists**: it cannot invent an action, only recognise a clumsy way of asking for one.
Everything here is local stdlib -- no model, no network -- so the cost of trying is
microseconds.

Two thresholds, because being wrong in the two directions costs different amounts:

    >= CONFIDENT   just run it. A wrong guess here wastes one action.
    >= WORTH_ASKING  ask "did you mean ...?" first.
    below          say nothing; the agent takes it, as before.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

CONFIDENT = 0.86
WORTH_ASKING = 0.72

# Words people add that carry no meaning for matching. Stripping them is what makes
# "could you please play x for me" and "play x" the same request.
FILLER = {
    "please", "pls", "just", "kindly", "quickly", "now", "for", "me", "can", "could",
    "would", "you", "will", "hey", "ok", "okay", "so", "um", "uh", "like", "i", "want",
    "wanna", "need", "to", "the", "a", "an", "go", "ahead", "and", "my",
}
# ...except where dropping them changes which script matches. "my phone" is not "phone".
KEEP_WITH = {("my", "phone"), ("the", "song"), ("to", "sleep")}

# Same meaning, different word. This is the part string similarity cannot do: "locate
# my mobile" and "find my phone" share barely a letter. Each group collapses to its
# first member before anything is compared, so a script written with one wording answers
# to all of them. Kept small and hand-picked on purpose -- a sloppy synonym here makes
# two different scripts collide, which is worse than missing one.
SYNONYMS = {
    "phone": ("mobile", "cell", "cellphone", "handset", "device"),
    "find": ("locate", "where", "wheres", "search"),
    "play": ("put", "start", "queue", "stick"),
    "song": ("track", "tune", "number"),
    "music": ("songs", "audio"),
    "open": ("launch", "run", "bring", "fire"),
    "close": ("shut", "quit", "kill", "exit", "dismiss"),
    "torch": ("flashlight", "flash", "light"),
    "message": ("text", "sms", "msg", "whatsapp"),
    # "phone" deliberately does NOT appear here, though "phone Anant" is idiomatic: it
    # is already a base word for the device, and a word cannot be two things at once
    # without one group eating the other. The verb sense is caught by the strict intent.
    "call": ("ring", "dial"),
    "volume": ("sound", "audio"),
    "battery": ("charge", "power"),
    "turn": ("switch", "flip", "toggle"),
    "next": ("skip", "forward"),
    "previous": ("back", "last", "prev"),
    "pause": ("stop", "halt"),
    "picture": ("photo", "image", "screenshot"),
    "delete": ("remove", "erase", "bin"),
    "show": ("display", "pull", "bring"),
}
# Flattened once: word -> the word it stands in for.
CANONICAL = {variant: base for base, variants in SYNONYMS.items() for variant in variants}

# A word that is both a base and somebody else's variant silently destroys one of the
# two groups -- "phone" as a variant of "call" once turned "find my phone" into "find
# call". Cheap to check, and the check is the reason it cannot happen twice.
_COLLIDING = sorted(set(SYNONYMS) & set(CANONICAL))
assert not _COLLIDING, f"synonym groups collide on: {_COLLIDING}"


def canonical(word: str) -> str:
    """The word this one stands in for. Apostrophes are noise: "where's" is "wheres"."""
    bare = word.replace("'", "")
    return CANONICAL.get(bare, bare)


_WORD = re.compile(r"[a-z0-9'+-]+")
_SLOT = re.compile(r"^\{(\w+)\}$")


def tokens(text: str, strip_filler: bool = True) -> list[str]:
    """Words, lowercased, with the noise removed but the meaning kept."""
    words = _WORD.findall((text or "").lower())
    if not strip_filler:
        return [canonical(word) for word in words]
    kept: list[str] = []
    for index, word in enumerate(words):
        following = canonical(words[index + 1]) if index + 1 < len(words) else ""
        if word in FILLER and (word, following) not in KEEP_WITH:
            continue
        kept.append(canonical(word))
    return kept


def similar(a: str, b: str) -> float:
    """How alike two words are. Catches a mis-heard word, not a different one."""
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def segments(template: str) -> list[tuple[str, object]]:
    """"play {query} on spotify" -> [lit ['play'], slot 'query', lit ['on','spotify']]."""
    out: list[tuple[str, object]] = []
    for part in re.split(r"(\{\w+\})", template):
        slot = _SLOT.match(part.strip())
        if slot:
            out.append(("slot", slot.group(1)))
        elif part.strip():
            words = tokens(part)
            if words:
                out.append(("lit", words))
    return out


@dataclass
class Guess:
    route: str          # "automation" or "intent"
    name: str           # which one
    args: dict          # the slots it filled
    score: float
    phrase: str         # the template that matched, for "did you mean ...?"


def score_template(template: str, said: list[str]) -> tuple[float, dict] | None:
    """Align a template's anchor words against what was said, in order.

    Anything between two anchors belongs to the slot between them, which is how
    "spotify play some old punk" still yields query="some old punk".
    """
    parts = segments(template)
    if not parts:
        return None
    position, pending, slots, marks, skipped = 0, None, {}, [], 0
    for kind, value in parts:
        if kind == "slot":
            pending = str(value)
            continue
        for anchor in value:                      # type: ignore[union-attr]
            best_at, best = -1, 0.0
            for index in range(position, len(said)):
                rating = similar(anchor, said[index])
                if rating > best:
                    best, best_at = rating, index
                if rating == 1.0:
                    break
            if best_at < 0 or best < 0.78:        # an anchor word is simply absent
                return None
            if pending is not None:
                slots[pending] = " ".join(said[position:best_at])
                pending = None
            else:
                skipped += best_at - position    # words the template never asked for
            marks.append(best)
            position = best_at + 1
    trailing = said[position:]
    if pending is not None:
        slots[pending] = " ".join(trailing)
        trailing = []
    if any(not value.strip() for value in slots.values()):
        return None                               # a slot nobody filled is a different request
    if not marks:
        return None
    # Words left over that the template never accounted for mean this is probably a
    # longer, different request that happens to contain these words.
    score = sum(marks) / len(marks) - 0.09 * (len(trailing) + skipped)
    return (score, slots) if score > 0 else None


# Joining words a template happens to use but a person may not say: "spotify play x"
# means "play x on spotify". Missing one of these is a small penalty, not a refusal.
LOOSE = {"on", "in", "at", "with", "of", "from", "some", "this", "that", "it"}


def score_unordered(template: str, said: list[str]) -> tuple[float, dict] | None:
    """The same words in a different order.

    People reorder freely -- "spotify, play x" for "play x on spotify" -- and an ordered
    walk refuses that outright. So when order fails, match the anchors wherever they
    are, take what is left over as the slot, and charge for the disorder.
    """
    parts = segments(template)
    slots = [value for kind, value in parts if kind == "slot"]
    if len(slots) > 1:
        return None                       # two slots need order to tell them apart
    anchors = [word for kind, value in parts if kind == "lit" for word in value]  # type: ignore[union-attr]
    if not anchors:
        return None

    spare = list(range(len(said)))
    marks, missed = [], 0
    for anchor in anchors:
        best_at, best = -1, 0.0
        for index in spare:
            rating = similar(anchor, said[index])
            if rating > best:
                best, best_at = rating, index
        if best_at < 0 or best < 0.78:
            if anchor in LOOSE:
                missed += 1               # a word they simply did not bother saying
                continue
            return None
        marks.append(best)
        spare.remove(best_at)
    if not marks:
        return None

    leftover = [said[index] for index in sorted(spare)]
    filled = {}
    if slots:
        if not leftover:
            return None                   # nothing left to be the slot
        filled[str(slots[0])] = " ".join(leftover)
        leftover = []
    # Out of order is weaker evidence than in order, so it can never outrank it.
    score = sum(marks) / len(marks) - 0.10 - 0.05 * missed - 0.09 * len(leftover)
    return (score, filled) if score > 0 else None


def best_guess(said: str, corpus: list[tuple[str, str, str]]) -> Guess | None:
    """The closest script to what was said.

    `corpus` is (route, name, template) for everything that already exists -- automation
    phrases and intent examples. Nothing outside it can ever be returned.
    """
    words = tokens(said)
    if not words:
        return None
    best: Guess | None = None
    for route, name, template in corpus:
        found = score_template(template, words) or score_unordered(template, words)
        if not found:
            continue
        score, slots = found
        if best is None or score > best.score:
            best = Guess(route=route, name=name, args=slots, score=score, phrase=template)
    return best if best and best.score >= WORTH_ASKING else None
