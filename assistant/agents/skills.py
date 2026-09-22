"""Agent skills, shared with Claude Code instead of reinvented.

A skill is a folder with a `SKILL.md` in it: YAML frontmatter naming and describing it,
then a document telling a model how to do one kind of job well. Claude Code reads them
from its own folders; this reads the same files, so a skill written once serves every
brain Nova has.

The whole design problem here is size. These documents run to fifteen hundred lines, and
Groq answers in one short turn -- putting even a few of them in the system prompt would
cost more than the answer. So this works the way Claude Code's own loader does, in two
stages:

1. **The names, always.** Eighteen hyphenated names cost about fifty tokens and are
   self-describing enough for a model to know something relevant exists.
2. **The body, on request.** `read_skill` fetches one document only once the model has
   decided it needs it, and truncates it to a budget.

What a skill cannot do is make a small model big. Most of these are construction skills
whose output is a landing page or a design system, and Groq's reply is capped at around
a thousand tokens. Their real value at the cheap tier is the other two uses: answering
the lookup-shaped ones outright, and picking the right skill to name when handing the
job to Claude -- which is a better delegation than one that starts from nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A skill's frontmatter is the first thing in the file, fenced by --- lines.
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.M)
# Folders that are checkouts or caches rather than skills.
_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}


@dataclass
class Skill:
    name: str
    description: str
    path: Path                      # the SKILL.md itself
    extras: list[str] = field(default_factory=list)  # other files in the folder

    @property
    def folder(self) -> Path:
        return self.path.parent


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONT.match(text)
    if not match:
        return {}
    found = dict(_FIELD.findall(match.group(1)))
    # A quoted description keeps its quotes through this regex; strip them.
    return {key: value.strip().strip("'\"") for key, value in found.items()}


def discover(roots: list[Path]) -> dict[str, Skill]:
    """Every skill under these folders, keyed by name.

    Only the frontmatter is read here -- a few hundred bytes per skill -- so this stays
    cheap enough to run at startup. A folder without a readable name is skipped rather
    than guessed at: a skill the model cannot name reliably is a skill it cannot load.
    """
    found: dict[str, Skill] = {}
    for root in roots:
        try:
            if not root.is_dir():
                continue
            candidates = sorted(root.iterdir())
        except OSError:
            continue
        for folder in candidates:
            if not folder.is_dir() or folder.name in _IGNORE or folder.name.startswith("."):
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                head = skill_file.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            meta = _frontmatter(head)
            name = (meta.get("name") or folder.name).strip()
            if not name or name in found:   # first root wins, so a project skill beats a shared one
                continue
            extras = []
            try:
                extras = sorted(p.relative_to(folder).as_posix()
                                for p in folder.rglob("*")
                                if p.is_file() and p.name != "SKILL.md")[:40]
            except OSError:
                pass
            found[name] = Skill(name=name, description=" ".join((meta.get("description") or "").split()),
                                path=skill_file, extras=extras)
    return found


def names(skills: dict[str, Skill]) -> str:
    """The one line that goes in the system prompt. Names only: descriptions are 400
    characters each and would cost more every turn than loading one costs once."""
    return ", ".join(sorted(skills))


def catalog(skills: dict[str, Skill], query: str = "", limit: int = 12) -> str:
    """Name and description per skill, for when the names alone weren't enough.

    A query filters on whole words in the name and description, so "animation" finds the
    motion skills without also returning everything that happens to contain an "a".
    """
    wanted = [word for word in re.split(r"\W+", (query or "").lower()) if len(word) > 2]
    rows: list[tuple[int, str]] = []
    for name, skill in sorted(skills.items()):
        haystack = f"{name} {skill.description}".lower()
        hits = sum(1 for word in wanted if word in haystack)
        if wanted and not hits:
            continue
        summary = skill.description
        if len(summary) > 220:                      # the first sentence or two is the point
            summary = summary[:217].rsplit(" ", 1)[0] + "..."
        rows.append((-hits, f"{name}: {summary}"))
    if not rows:
        return "No skill matches that. The skills are: " + (names(skills) or "none")
    rows.sort()
    return "\n".join(row for _rank, row in rows[:max(1, limit)])


def read(skills: dict[str, Skill], name: str, limit: int = 6000) -> str:
    """One skill's document, truncated to a budget.

    Truncation is honest rather than silent: the model is told the document was cut and
    how, so it can ask for a different part instead of assuming it saw everything.
    """
    skill = skills.get(name) or _closest(skills, name)
    if skill is None:
        return f"There's no skill called '{name}'. The ones there are: " + (names(skills) or "none")
    try:
        text = skill.path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"I couldn't read that skill ({type(error).__name__})."
    text = _FRONT.sub("", text, count=1).strip()    # the frontmatter is already in the index
    budget = max(500, min(int(limit or 6000), 20000))
    note = ""
    if len(text) > budget:
        text = text[:budget].rsplit("\n", 1)[0]
        note = f"\n\n[cut here: this skill is longer than {budget} characters]"
    extras = ""
    if skill.extras:
        extras = ("\n\nOther files in this skill, readable from disk at "
                  f"{skill.folder}: " + ", ".join(skill.extras[:12]))
    return f"# skill: {skill.name}\n\n{text}{note}{extras}"


# Words too common across these documents to mean anything when they match.
_WEAK = {"skill", "this", "that", "when", "with", "your", "from", "into", "user", "agent",
         "them", "then", "they", "want", "wants", "asks", "asked", "make", "makes", "have",
         "uses", "using", "code", "work", "works", "used", "like", "only", "also", "more"}


def suggest(skills: dict[str, Skill], text: str, minimum: int = 2) -> str:
    """The skill that covers this request, by name, or "" when none clearly does.

    This exists because asking a model to notice a relevant skill does not work. Told
    "read a skill if one fits", a confident small model answers from memory instead --
    verified against `animation-vocabulary`, which was skipped for the exact question
    its own description quotes as an example. Matching here instead turns the model's
    judgement call into an instruction naming one skill, which it does follow.

    Deliberately strict: a wrong suggestion spends a tool call and drags the answer
    off-course, so two distinctive words must match before anything is suggested.
    """
    from ..matching import tokens

    said = {word for word in tokens(text or "") if len(word) > 3 and word not in _WEAK}
    if not said:
        return ""
    best, best_score = "", 0.0
    for name, skill in skills.items():
        title = {w for w in re.split(r"\W+", name.lower()) if len(w) > 3}
        body = {w for w in re.split(r"\W+", skill.description.lower())
                if len(w) > 3 and w not in _WEAK}
        hits = said & (title | body)
        if len(hits) < minimum:
            continue
        # A word in the name counts double: "animation-vocabulary" matching "animation"
        # says more than the same word buried in a four-line description.
        score = len(hits) + len(said & title)
        if score > best_score:
            best, best_score = name, score
    return best


def _closest(skills: dict[str, Skill], name: str) -> Skill | None:
    """A model that says "animation vocabulary" means `animation-vocabulary`."""
    key = re.sub(r"\W+", "", (name or "").lower())
    if not key:
        return None
    for candidate, skill in skills.items():
        if re.sub(r"\W+", "", candidate.lower()) == key:
            return skill
    return None
