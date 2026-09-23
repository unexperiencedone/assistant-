"""Checking an automation the assistant wrote for itself, before it takes effect.

Nova patches itself: three repeats of the same kind of request and it asks Claude to
save the steps as an automation; a failing automation gets a background turn asking
Claude to repair it (AGENTS.md section 12). Nothing reviewed the result, so a bad patch
went straight into a tracked file and started running.

Three patches were produced on 23 September 2026 and two of them made things worse,
which is what this module is built from. Each check below is one of those failures:

- It added `"{ignore} havent tell me proper {topic}"` as a trigger phrase for the news
  briefing. That sentence was a *complaint* about a briefing, not a request for one, and
  the file's own comment -- written earlier by the same loop -- said so explicitly.
- It made the first browser step optional and added an unconditional duplicate of it,
  so every briefing loaded the page twice. Its own comment admitted this.
- Separately it added an intents rule for the same complaint, which broke a test.

So: a proposal is checked against what it would actually do, and only a clean one is
offered for approval. The checks are local and cheap, in the same spirit as everything
else here that turned out to work -- asking a model to be careful is not a boundary.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Callable

# Words that mean the phrase is a complaint or a refusal rather than a request. A
# trigger built from one of these fires when the user is telling Nova it got something
# wrong, which is the worst possible moment to do the same thing again.
NEGATIONS = re.compile(
    r"\b(?:haven'?t|hasn'?t|hadn'?t|didn'?t|doesn'?t|don'?t|won'?t|wouldn'?t|can'?t|"
    r"couldn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|never|nothing|not|no)\b", re.I)
SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}", re.I)
# A phrase needs some fixed words of its own; one that is mostly placeholders matches
# almost anything said.
MIN_LITERAL_WORDS = 2
MAX_PHRASES = 24


def _literal_words(phrase: str) -> list[str]:
    return [word for word in re.split(r"\W+", SLOT.sub(" ", phrase or "")) if word]


def problems(text: str, matches_intent: Callable[[str], Any] | None = None) -> list[str]:
    """Everything wrong with this proposed automation, in plain words.

    Empty means it is safe to offer for approval. `matches_intent` is normally
    `intents.match_intent`, so a phrase that an instant command already answers can be
    caught -- two things claiming the same sentence is a coin toss at runtime.
    """
    found: list[str] = []
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as error:
        return [f"it is not valid TOML: {error}"]
    if not isinstance(data, dict):
        return ["it is not a table of settings"]

    phrases = data.get("phrases")
    if not isinstance(phrases, list) or not phrases:
        found.append("it has no phrases, so nothing would ever trigger it")
        phrases = []
    elif len(phrases) > MAX_PHRASES:
        found.append(f"it has {len(phrases)} phrases, which is more than anyone said")

    declared: set[str] = set()
    for phrase in phrases:
        if not isinstance(phrase, str) or not phrase.strip():
            found.append("one of the phrases is empty")
            continue
        declared |= {name.lower() for name in SLOT.findall(phrase)}
        if NEGATIONS.search(phrase):
            found.append(f'the phrase "{phrase}" is a complaint, not a request -- '
                         "it would fire exactly when the user says this went wrong")
        if len(_literal_words(phrase)) < MIN_LITERAL_WORDS:
            found.append(f'the phrase "{phrase}" is nearly all placeholders, '
                         "so it would match almost anything")
        if matches_intent is not None:
            spoken = SLOT.sub("something", phrase)
            existing = matches_intent(spoken)
            if existing is not None:
                name = getattr(existing, "name", str(existing))
                found.append(f'the phrase "{phrase}" is already answered instantly by '
                             f"{name}, so the two would compete")

    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        found.append("it has no steps, so it would do nothing")
        steps = []
    found.extend(_step_problems(steps, declared))
    return found


def _step_problems(steps: list[Any], declared: set[str]) -> list[str]:
    """What is wrong with the steps: duplicates, unknown slots, missing actions."""
    found: list[str] = []
    previous: dict[str, Any] | None = None
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            found.append(f"step {index} is not a set of settings")
            continue
        if not step.get("do"):
            found.append(f"step {index} does not say what to do")
        # The duplicate-retry bug: an identical step that always runs means the work
        # happens twice every time, rather than once with a retry when it fails.
        if previous is not None and _same_work(previous, step) and not step.get("when"):
            found.append(f"step {index} repeats step {index - 1} unconditionally, "
                         "so it would run the same thing twice every time")
        for name in _slots_used(step):
            if name.lower() not in declared:
                found.append(f'step {index} uses {{{name}}}, which no phrase provides')
        previous = step
    return found


def _same_work(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Two steps that do the same thing to the same target. Labels are ignored, since
    renaming a duplicate does not stop it being one."""
    def shape(step: dict[str, Any]) -> tuple:
        return tuple(sorted((key, str(value)) for key, value in step.items()
                            if key not in ("label", "optional", "timeout", "when")))
    return bool(first.get("do")) and first.get("do") == second.get("do") and shape(first) == shape(second)


def _slots_used(step: dict[str, Any]) -> set[str]:
    """Slot names a step refers to, including the `_url` form the runner provides."""
    names: set[str] = set()
    for value in step.values():
        if isinstance(value, str):
            names |= {name[:-4] if name.lower().endswith("_url") else name
                      for name in SLOT.findall(value)}
    # A step may refer to a value an earlier step saved; those are not phrase slots.
    return {name for name in names if name not in ("top",)}


def verdict(text: str, matches_intent: Callable[[str], Any] | None = None) -> str:
    """"" when the proposal is safe to offer, otherwise why it is not."""
    found = problems(text, matches_intent)
    if not found:
        return ""
    listed = "; ".join(f"{i}. {reason}" for i, reason in enumerate(found, 1))
    return f"This automation would misbehave -- {listed}."
