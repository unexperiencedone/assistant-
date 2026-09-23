"""Saying what an agent is doing, rather than which tool it just called.

While a task runs, Nova narrates every so often. It used to speak the phrase for the
single most recent tool call, which produced "Running a command." over and over --
true, and useless. It tells you the mechanism and not the work, and a command is the
one thing an agent does constantly whatever it is actually up to.

What a person wants to hear is the *shape* of the work: that it is still reading its way
around the repo, that it has started writing, that it is off searching the web. So this
keeps a short window of the tools called since it last spoke, groups them into kinds of
work, and says the kinds. Two tools of the same kind are one thing happening, not two.

Everything here is local string handling on purpose. A progress line is spoken every
half-minute while a task runs, so putting a model call in this path would mean paying
for the commentary as well as the work -- and the commentary is the part that has to be
cheap, since it happens whether or not anything interesting occurred.
"""

from __future__ import annotations

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


@dataclass
class Tracker:
    """One task's activity, between one spoken line and the next."""

    label: str = ""
    started: float = field(default_factory=time.time)
    spoken_once: bool = False
    _seen: list[str] = field(default_factory=list)   # kinds since the last line, in order

    def saw(self, tool: str) -> str:
        """Record a tool call. Returns the phrase for it, for the canvas."""
        kind = KINDS.get((tool or "").strip().lower(), "")
        if kind:
            # Consecutive calls of the same kind are one thing happening.
            if not self._seen or self._seen[-1] != kind:
                self._seen.append(kind)
            del self._seen[:-6]
        return kind or (f"using {tool.replace('_', ' ')}" if tool else DEFAULT_KIND)

    @property
    def latest(self) -> str:
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

        The first line says what the job is, because that is the useful thing at the
        start; later ones say "still", because by then you know what the job is and want
        to know whether it has moved on.
        """
        activity = self.activity()
        label = " ".join((self.label or "").split())[:60]
        if not self.spoken_once:
            self.spoken_once = True
            said = f"Working on {label} -- {activity}." if label else f"{activity.capitalize()}."
        else:
            said = f"Still on {label} -- {activity}." if label else f"Still {activity}."
        self._seen.clear()
        return said[0].upper() + said[1:] if said else said

    def elapsed(self) -> float:
        return time.time() - self.started
