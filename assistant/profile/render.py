"""Turn the profile into text for agents and words for speech recognition.

Two kinds of context, so prompts stay small:
  standing   a compact summary in every session's instructions (who you are, how you
             work, your machine, what's active)
  relevant   full details of the projects, people and past work a request mentions,
             added to that request only

Both respect each section's visibility for the agent that will read them.
"""

from __future__ import annotations

import re
from typing import Any

STANDING_BUDGET = 2400  # characters; the summary is sent with every session
REPLY_SENTENCES = {"brief": 2, "normal": 3, "detailed": 5}


def visible(profile: dict[str, Any], section: str, audience: str) -> bool:
    """audience is "all" (any agent) or "trusted" (Claude Code, Antigravity)."""
    if not profile["privacy"]["personalize"]:
        return False
    level = profile["visibility"].get(section)
    return level == "all" or (level == "trusted" and audience == "trusted")


def standing_context(profile: dict[str, Any], audience: str) -> str:
    see = lambda section: visible(profile, section, audience)  # noqa: E731
    lines: list[str] = []

    if see("identity"):
        i = profile["identity"]
        who = i["name"] + (f' (call them "{i["preferred_name"]}")' if i["preferred_name"] and i["preferred_name"] != i["name"] else "")
        parts = [p.rstrip(" .") for p in (who, i["headline"], i["education"], "; ".join(i["organizations"])) if p]
        if parts:
            lines.append("Who: " + ". ".join(parts) + ".")
        place = ", ".join(p for p in (i["location"], f"time zone {i['timezone']}" if i["timezone"] else "") if p)
        if place:
            lines.append(f"Where: {place}.")
        if i["languages"]:
            lines.append(f"Speaks: {', '.join(i['languages'])}; transcripts may mix them.")

    if see("goals"):
        g = profile["goals"]
        for label, value in (("Career goals", g["career"]), ("Research interests", g["research_interests"]),
                             ("Currently learning", g["learning_now"])):
            if value:
                lines.append(f"{label}: {'; '.join(value)}.")
        if g["looking_for"]:
            lines.append(f"Looking for: {g['looking_for']}.")

    if see("skills"):
        s = profile["skills"]
        by_level: dict[str, list[str]] = {}
        for item in s["items"]:
            by_level.setdefault(item["level"], []).append(item["name"] + (f" ({item['notes']})" if item["notes"] else ""))
        for level in ("advanced", "strong", "comfortable", "rusty", "learning"):
            if by_level.get(level):
                lines.append(f"Skills, {level}: {', '.join(by_level[level])}.")
        if s["avoid"]:
            lines.append(f"Don't steer toward (unless asked): {'; '.join(s['avoid'])}.")

    if see("work_style"):
        w = profile["work_style"]
        for label, value in (("Code", w["code"]), ("Docs", w["docs"])):
            if value:
                lines.append(f"{label}: {value}")
        lines.extend(f"Rule: {rule}" for rule in w["rules"])
        if w["spoken_replies"] != "normal":
            n = REPLY_SENTENCES[w["spoken_replies"]]
            lines.append(f"Spoken replies: {w['spoken_replies']}, at most {n} sentences.")

    if see("devices"):
        d = profile["devices"]
        for m in d["items"]:
            owner = "" if m["yours"] else " (not theirs; don't assume they can use it)"
            gpu = f", GPU: {m['gpu']}" if m["gpu"] else ""
            lines.append(f"Machine: {m['name']}{': ' + m['specs'] if m['specs'] else ''}{gpu}{owner}."
                         + (f" {m['notes']}" if m["notes"] else ""))
        if d["constraints"]:
            lines.append(f"Compute: {d['constraints']}")

    optional: list[str] = []  # dropped first when over budget
    if see("projects"):
        current = [p for p in profile["projects"]["items"] if p["status"] in ("active", "paused")]
        if current:
            lines.append("Current projects (details come with requests that mention them): "
                         + "; ".join(_project_label(p) for p in current) + ".")
    if see("people") and profile["people"]["items"]:
        optional.append("People: " + "; ".join(
            p["name"] + (f" ({p['relation']})" if p["relation"] else "") for p in profile["people"]["items"]) + ".")
    if see("portfolio") and profile["portfolio"]["items"]:
        optional.append("Past work: " + ", ".join(p["name"] for p in profile["portfolio"]["items"]) + ".")

    if profile["privacy"]["personalize"] and profile["privacy"]["shared_account"]:
        lines.append("Privacy: this Claude account is shared with someone else, so keep these personal details "
                     "out of files, commits and anything published unless the user asks.")

    if not lines and not optional:
        return ""
    head = "About the user (from their Nova profile; use it to tailor your work, don't recite it back):"
    text = "\n".join([head, *(f"- {line}" for line in lines)])
    for extra in optional:
        if len(text) + len(extra) + 3 <= STANDING_BUDGET:
            text += f"\n- {extra}"
    return text if len(text) <= STANDING_BUDGET else text[:STANDING_BUDGET - 3].rsplit("\n", 1)[0] + "\n- ..."


def relevant_context(profile: dict[str, Any], request: str, audience: str) -> str:
    """Details of every project, person and past work entry the request names."""
    lines: list[str] = []
    for section, describe in (("projects", _project_detail), ("people", _person_detail), ("portfolio", _portfolio_detail)):
        if not visible(profile, section, audience):
            continue
        for item in profile[section]["items"]:
            if mentions(request, item):
                lines.append(describe(item))
    if not lines:
        return ""
    return "[From the user's profile, for this request]\n" + "\n".join(f"- {line}" for line in lines) + "\n[End of profile]"


def mentions(text: str, item: dict[str, Any]) -> bool:
    for name in (item["name"], *item.get("aliases", [])):
        if len(name) >= 3 and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.I):
            return True
    return False


def speech_words(profile: dict[str, Any], limit: int = 40) -> list[str]:
    """Names speech recognition should expect: vocabulary, then projects, people, past work.
    Used on this PC only, whatever the sections' visibility. Long descriptive names are
    skipped: Whisper's hint space is small, and ordinary words need no hint."""
    words: dict[str, str] = {}
    candidates = [*profile["vocabulary"]["words"]]
    for section in ("projects", "people", "portfolio"):
        for item in profile[section]["items"]:
            candidates += [item["name"], *item.get("aliases", [])]
    for word in candidates:
        if len(word.split()) <= 3:
            words.setdefault(word.lower(), word)
    return list(words.values())[:limit]


def _project_label(p: dict[str, Any]) -> str:
    extra = ", ".join(x for x in (p["context"], p["status"] if p["status"] != "active" else "") if x)
    return p["name"] + (f" ({extra})" if extra else "")


def _project_detail(p: dict[str, Any]) -> str:
    bits = [f"Project {_project_label(p)}"]
    if p["aliases"]:
        bits.append(f"also called {', '.join(p['aliases'])}")
    if p["role"]:
        bits.append(f"their role: {p['role']}")
    if p["team"]:
        bits.append(f"team: {', '.join(p['team'])}")
    if p["stack"]:
        bits.append(f"stack: {', '.join(p['stack'])}")
    if p["deadline"]:
        bits.append(f"deadline: {p['deadline']}")
    if p["folder"]:
        bits.append(f"folder: {p['folder']}")
    if p["links"]:
        bits.append(f"links: {', '.join(p['links'])}")
    return "; ".join(bits) + (f". {p['summary']}" if p["summary"] else ".")


def _person_detail(p: dict[str, Any]) -> str:
    bits = [f"Person {p['name']}"]
    if p["relation"]:
        bits.append(p["relation"])
    if p["projects"]:
        bits.append(f"works with them on {', '.join(p['projects'])}")
    return "; ".join(bits) + (f". {p['notes']}" if p["notes"] else ".")


def _portfolio_detail(p: dict[str, Any]) -> str:
    stack = f" ({', '.join(p['stack'])})" if p["stack"] else ""
    return f"Past work {p['name']}{stack}" + (f": {p['summary']}." if p["summary"] else ".")
