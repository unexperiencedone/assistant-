"""The greeting Nova says when it opens: written fresh every time.

A free model writes it from today's facts. Variety is engineered, not hoped for:
the last few greetings go in with an instruction not to echo them, and each time a
different angle and length is drawn at random. If no model answers, a greeting is
still assembled at random from parts, so even the fallback rarely repeats.
"""

from __future__ import annotations

import json
import re
import random
from typing import Any

ANGLES = [
    "open with the time of day and how it feels outside",
    "open with something specific about the weather that matters later today",
    "open casually, the way a friend who just walked in would",
    "open with what's on their plate today, then the weather",
    "open warmly and briefly, then go straight to the useful bits",
    "open with a light observation about the day (a holiday, the weekday, the heat)",
]
CLOSERS = [  # examples only: the model is told to invent its own
    "What's next?", "What are we doing?", "Where do you want to start?", "What do you need?",
    "Anything I can take off your hands?", "What else is on your mind?",
]

INSTRUCTIONS = """You are writing what you say out loud the moment {user} opens you. A natural voice speaks it, so write speech, not text.

Cover these, in this order this time: {order}. Then end by inviting the next request.
- greeting: say hello by name in a way that fits the time of day. At night say hey or evening; "good night" is a goodbye, never a hello.
- weather: how it is right now, and what's coming later today or tomorrow if it changes anything (rain, heat, a cooler night). Air quality only if it is poor or worse.
- news: ONE Indian headline, in a short clause, in your own words.
- money: which way Nifty and Sensex went and roughly how much ("down about one and a half percent"), with the reason if a finance headline gives one. No index levels, no decimals.
- plans: the next thing on their schedule, using the "in N minutes/hours" given (never call something "now" unless it says so), plus a holiday today or tomorrow if any. Skip if there's nothing.
{mail}
Rules:
- Five to seven short sentences. Conversational and warm, like a sharp friend, never like a report. No lists, emoji or markdown.
- Never invent anything that isn't in the facts. Skip a part whose facts are missing.
- This time, {angle}.
- Do not reuse the openings, sentence shapes or closing questions of these earlier greetings:
{recent}
- The closing invitation must be new; not any of: {closers}.
- At most one delivery tag such as [warmly] or [cheerful], right before the words it colours.
Reply with the spoken words only."""


def _closing(text: str) -> str:
    """The last sentence of a greeting: the sign-off the next one must not repeat."""
    sentences = [part.strip() for part in re.split(r"(?<=[.?!])\s+", text or "") if part.strip()]
    return sentences[-1][-90:] if sentences else ""


def instructions(user: str, recent: list[str], mail: bool) -> str:
    shown = "\n".join(f"  - {line[:160]}" for line in recent[-5:]) or "  (none yet)"
    closers = "; ".join(f'"{_closing(line)}"' for line in recent[-5:] if _closing(line)) or "(none yet)"
    middle = ["weather", "news", "money", "plans"]
    random.shuffle(middle)
    if random.random() < 0.6:   # weather usually leads, as people expect -- not always
        middle.remove("weather")
        middle.insert(0, "weather")
    mail_line = ("- mail: say you're going through their mail now and will tell them what matters in a minute "
                 "(just before the closing invitation).\n" if mail else "")
    return INSTRUCTIONS.format(user=user or "them", recent=shown, closers=closers,
                               order=", ".join(["greeting"] + middle), angle=random.choice(ANGLES), mail=mail_line)


def facts_prompt(facts: dict[str, Any], schedule: list[str]) -> str:
    payload = {**facts, "schedule": schedule or ["nothing scheduled"]}
    return "Today's facts:\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def fallback(facts: dict[str, Any], user: str, schedule: list[str], mail: bool) -> str:
    """No model reachable: assembled from parts, at random, so it still varies."""
    name = f" {user}" if user else ""
    part = facts.get("part_of_day", "day")
    hello = random.choice([f"Hey{name}.", f"Hi{name}, good {part}." if part != "night" else f"Hi{name}.",
                           f"Good {part}{name}." if part not in ("night", "early morning") else f"Hey there{name}."])
    out = [hello]
    w = facts.get("weather")
    if w:
        out.append(random.choice([f"It's {w['now']} in {facts.get('place')} right now.",
                                  f"Outside it's {w['now']}.", f"Right now it's {w['now']}."]))
        if w.get("next_hours"):
            out.append(f"Later on, {w['next_hours'][-1]}.")
    if facts.get("india_news"):
        out.append(random.choice(["In the news, ", "Headlines: ", "Making news, "]) + facts["india_news"][0] + ".")
    if facts.get("markets"):
        out.append(random.choice(["Markets: ", "On the markets, "]) + "; ".join(facts["markets"][:2]) + ".")
    if schedule:
        out.append(f"Next up, {schedule[0]}.")
    if mail:
        out.append("I'm going through your mail now.")
    out.append(random.choice(CLOSERS))
    return " ".join(out)
