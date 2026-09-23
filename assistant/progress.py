"""Saying what an agent is doing -- from the agent, not from guessing at its tools.

The first version of this inferred the work from tool names: five Bash calls became
"running things". That is still describing the mechanism, one level up. An agent reading
its way around a repo and an agent running a test suite both call Bash, and only the
agent knows which it is doing.

So the agent says. `agents/rules.py` asks it to print [[DOING: ...]] when it starts a
distinct phase and [[DONE: ...]] when it finishes one -- the same marker convention the
plan already uses for [[STEP n DONE]]. Those are what get spoken, and they are the
agent's own words about its own work.

Inference stays underneath as a fallback, because a marker may not come: a short task
never reaches a phase boundary, and a model sometimes forgets. Something true and vague
beats silence while a minute passes. But when a milestone exists it wins.

The fallback groups tool calls into kinds of work and says the kinds, with repeats of
one kind counted once. Vaguer, so it is spoken half as often -- hearing "still writing
code" every thirty seconds is the noise that started this.

Everything here is local string handling on purpose. A line goes out while a task runs
whether or not anything interesting happened, so a model call in this path would mean
paying for the commentary as well as the work.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Tools grouped by the kind of work they represent, rather than by what they are called.
# The names span Claude Code, Antigravity and Nova's own tools, since all three report
# through the same event.
KINDS: dict[str, str] = {
    # finding out where things are
    "read": "reading", "view_file": "reading", "cat": "reading", "notebookread": "reading",
    "grep": "searching the code", "glob": "looking through files", "ls": "looking through files",
    "find_items": "looking through files", "codebase_search": "searching the code",
    # changing things
    "edit": "writing code", "write": "writing code", "multiedit": "writing code",
    "write_to_file": "writing code", "replace_file_content": "writing code",
    "notebookedit": "writing code", "apply_patch": "writing code",
    # doing things
    "bash": "running things", "powershell": "running things", "run_command": "running things",
    "run_terminal_cmd": "running things", "run_automation": "running an automation",
    # the outside world
    "websearch": "searching the web", "web_search": "searching the web",
    "webfetch": "reading a page", "read_page": "reading a page",
    "browse_open": "in the browser", "browse_read": "in the browser",
    "browse_click": "in the browser", "browse_fill": "in the browser",
    # working with others
    "task": "with a helper agent", "delegate_to_claude": "handing work to Claude",
    "delegate_to_agy": "handing work to Antigravity",
    "start_task": "starting something in the background", "run_steps": "running several things at once",
    # the rest
    "write_word": "writing a document", "read_skill": "reading up on how to do this",
    "list_skills": "reading up on how to do this", "todowrite": "planning the steps",
    "exitplanmode": "planning the steps", "read_plan": "checking the plan",
}
# Said when nothing recognisable has happened. "Thinking" is honest: between tool calls
# a model is generating, and that is genuinely what it is doing.
DEFAULT_KIND = "thinking it through"
MAX_KINDS_SPOKEN = 2


MILESTONE = re.compile(r"\[\[\s*(DOING|DONE|PHASE)\s*:\s*([^\]]{2,80}?)\s*\]\]", re.I)


def milestones_in(text: str) -> list[tuple[str, str]]:
    """The (kind, phrase) markers an agent printed in this chunk of output."""
    found = []
    for kind, phrase in MILESTONE.findall(text or ""):
        phrase = " ".join(phrase.split())
        if phrase:
            found.append((kind.upper(), phrase))
    return found


def strip_milestones(text: str) -> str:
    """The same text without the markers, for anything that shows it to a person."""
    return MILESTONE.sub("", text or "")


@dataclass
class Tracker:
    """One task's activity, between one spoken line and the next."""

    label: str = ""
    started: float = field(default_factory=time.time)
    spoken_once: bool = False
    _seen: list[str] = field(default_factory=list)   # kinds since the last line, in order
    # What the agent said it was doing, newest last. These beat anything inferred.
    _said: list[tuple[str, str]] = field(default_factory=list)
    # The last marker as (kind, phrase). Both halves matter: finishing the phase you
    # were in repeats the phrase, and dropping it as a duplicate loses the one event
    # the user most wants -- that something is done.
    _last_said: tuple[str, str] = ("", "")

    def saw(self, tool: str) -> str:
        """Record a tool call. Returns the phrase for it, for the canvas."""
        kind = KINDS.get((tool or "").strip().lower(), "")
        if kind:
            # Consecutive calls of the same kind are one thing happening.
            if not self._seen or self._seen[-1] != kind:
                self._seen.append(kind)
            del self._seen[:-6]
        return kind or (f"using {tool.replace('_', ' ')}" if tool else DEFAULT_KIND)

    def declared(self, text: str) -> list[tuple[str, str]]:
        """Record the milestones in a chunk of the agent's output."""
        found = milestones_in(text)
        for kind, phrase in found:
            marker = (kind, phrase.lower())
            if marker != self._last_said:
                self._said.append((kind, phrase))
                self._last_said = marker
        del self._said[:-4]
        return found

    @property
    def latest(self) -> str:
        """For the canvas: what it last said it was doing, else the last tool kind."""
        if self._said:
            kind, phrase = self._said[-1]
            return f"finished {phrase}" if kind in ("DONE", "PHASE") else phrase
        return self._seen[-1] if self._seen else DEFAULT_KIND

    def activity(self) -> str:
        """The kinds of work seen since the last line, newest last, at most two."""
        ordered: list[str] = []
        for kind in self._seen:
            if kind not in ordered:
                ordered.append(kind)
        if not ordered:
            return DEFAULT_KIND
        kept = ordered[-MAX_KINDS_SPOKEN:]
        return kept[0] if len(kept) == 1 else " and ".join(kept)

    def line(self) -> str:
        """The sentence to speak now, and start a fresh window.

        A milestone the agent declared is used verbatim -- it is the agent's own account
        of its own work, which is the whole point, and rewording it would put the
        guessing back. Only when nothing was declared does this fall back to the kinds
        of tool call seen, which is true but vague.
        """
        if self._said:
            said = self._milestone_line()
            self._said.clear()
            self._seen.clear()
            self.spoken_once = True
            return said
        activity = self.activity()
        label = " ".join((self.label or "").split())[:60]
        if not self.spoken_once:
            self.spoken_once = True
            said = f"Working on {label} -- {activity}." if label else f"{activity.capitalize()}."
        else:
            said = f"Still on {label} -- {activity}." if label else f"Still {activity}."
        self._seen.clear()
        return said[0].upper() + said[1:] if said else said

    def _milestone_line(self) -> str:
        """What the agent said, in the fewest words that still carry it.

        Finishing something is the more interesting event, so a completed phase is
        reported even when the agent has already moved on to the next one.
        """
        done = [phrase for kind, phrase in self._said if kind in ("DONE", "PHASE")]
        doing = [phrase for kind, phrase in self._said if kind == "DOING"]
        if done and doing:
            return f"Finished {done[-1]}. Now {doing[-1]}."
        if done:
            return f"Finished {done[-1]}."
        return f"Now {doing[-1]}." if doing else f"{DEFAULT_KIND.capitalize()}."

    def elapsed(self) -> float:
        return time.time() - self.started
