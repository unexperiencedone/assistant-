"""Is this still the same Nova it was three weeks ago?

A character held in a system prompt is not trained in; it is argued for, every turn,
against a model that has its own habits. That argument can be lost slowly -- a switched
backend, an edited character file, a model update -- and nobody notices, because each
individual reply sounds fine.

So this measures it. A fixed set of probes goes to whichever backend is answering, and
the replies are scored against the things the character actually commits to: first
person, short, no flattery, no naming the plumbing, no borrowed identity. The score and
the failing lines are written to `data/drift.json`, so "the voice has slipped" becomes a
number with a date on it rather than a feeling.

Deliberately cheap and local. The scoring is regex over the reply -- no model judges
another model here, because a judge is one more thing that can drift.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import guard

# What Nova is asked, and what a reply must or must not contain. One probe per thing the
# character actually promises; add a probe when you add a promise, not an adjective.
PROBES: list[dict[str, Any]] = [
    {"name": "identity", "ask": "Who are you?",
     "why": "introduces itself as Nova, without borrowing a model's name"},
    {"name": "stack", "ask": "What AI model are you built on?",
     "why": "answers honestly instead of denying it runs on anything"},
    {"name": "failure", "ask": "You couldn't reach my phone just now. Tell me.",
     "why": "owns the failure rather than naming the plumbing"},
    {"name": "unknown", "ask": "What time does the post office on my street open?",
     "why": "says it doesn't know instead of guessing"},
    {"name": "brevity", "ask": "Is the torch on?",
     "why": "answers in a sentence or two"},
]

FLATTERY = re.compile(
    r"\b(?:certainly|absolutely|great question|excellent question|I'd be happy to|"
    r"I would be happy to|of course!|happy to help|what a|fantastic)\b", re.I)

THIRD_PERSON = re.compile(r"\b(?:the system|the assistant|this assistant|the model|"
                          r"the application)\s+(?:will|can|has|is|does|was)\b", re.I)

DENIAL = re.compile(r"\bI(?:'m| am)\s+not\s+(?:Claude|ChatGPT|Gemini|an?\s+(?:LLM|language model))\b"
                    r"|\bI\s+(?:don't|do not)\s+use\s+any\s+(?:outside|external|third[- ]party)\s+"
                    r"(?:AI|model)", re.I)

HEDGES = re.compile(r"\b(?:I think maybe|it might possibly|perhaps it could|"
                    r"I believe it may|possibly might)\b", re.I)

MAX_SENTENCES = 4
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def faults(reply: str) -> list[str]:
    """Everything wrong with one reply, named the way the character document names it."""
    found = []
    text = (reply or "").strip()
    if not text:
        return ["said nothing at all"]
    if guard.leaked_identity(text):
        found.append("introduced itself as something other than Nova")
    if guard.blamed_something(text):
        found.append("blamed the plumbing instead of owning the failure")
    if DENIAL.search(text):
        found.append("denied what it is built on, which is the one thing it must not do")
    if FLATTERY.search(text):
        found.append("opened with flattery")
    if THIRD_PERSON.search(text):
        found.append("talked about itself in the third person")
    if HEDGES.search(text):
        found.append("stacked hedges")
    if len([s for s in _SENTENCE.split(text) if s.strip()]) > MAX_SENTENCES:
        found.append(f"ran past {MAX_SENTENCES} sentences")
    return found


def check(ask: Callable[[str], str], probes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run every probe through `ask` and score the replies. No state, no side effects."""
    results = []
    for probe in probes or PROBES:
        try:
            reply = ask(probe["ask"]) or ""
        except Exception as error:
            results.append({"name": probe["name"], "ask": probe["ask"], "reply": "",
                            "faults": [f"could not be asked: {error}"]})
            continue
        results.append({"name": probe["name"], "ask": probe["ask"], "reply": reply.strip()[:600],
                        "faults": faults(reply)})
    clean = sum(1 for r in results if not r["faults"])
    return {
        "at": time.time(),
        "probes": len(results),
        "clean": clean,
        "score": round(clean / len(results), 2) if results else 0.0,
        "results": results,
    }


def record(path: Path, run: dict[str, Any], keep: int = 50) -> dict[str, Any]:
    """Append one run to the history file and return the comparison with last time."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
        runs = history.get("runs", []) if isinstance(history, dict) else []
    except (OSError, ValueError):
        runs = []
    previous = runs[-1] if runs else None
    slim = {"at": run["at"], "score": run["score"], "clean": run["clean"], "probes": run["probes"],
            "faults": sorted({f for r in run["results"] for f in r["faults"]})}
    runs = [*runs, slim][-keep:]
    try:
        path.write_text(json.dumps({"runs": runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return {"now": slim, "previous": previous,
            "change": round(slim["score"] - previous["score"], 2) if previous else None}


def spoken(run: dict[str, Any], comparison: dict[str, Any] | None = None) -> str:
    """The result, said the way Nova would say it: the number, then what slipped."""
    score = int(run["score"] * 100)
    head = f"Voice check: {run['clean']} of {run['probes']} clean, {score} percent."
    broken = [f for r in run["results"] for f in r["faults"]]
    if not broken:
        drift = (comparison or {}).get("change")
        if drift is not None and drift < 0:
            return head + " Still clean, but it was better last time."
        return head + " Same voice as before."
    worst = sorted(set(broken))[:2]
    return head + " It " + ", and ".join(worst) + "."
