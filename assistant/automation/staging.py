"""Where an automation the assistant wrote for itself waits to be looked at.

`macros.py` loads `automations/*.toml` with a top-level glob, so anything in
`automations/pending/` is invisible to it. That is the whole mechanism: a proposal
cannot run until it is moved, and moving it is a decision somebody makes out loud.

Why it needs one. The promotion loop already writes automations unprompted -- three
repeats of a request and Claude is asked to save the steps; a failing automation gets a
turn asking Claude to repair it. Until now the result went straight into a tracked file
and started running. On 23 September 2026 it produced three patches and two made things
worse, including a trigger phrase built from a complaint about the very thing it would
then do again.

The asymmetry that made this obvious: a LinkedIn post needs approval before it goes
out, and an edit to the assistant's own behaviour did not. That is the wrong way round.
A bad post is embarrassing; a bad automation is wrong every time it fires, silently,
until someone notices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import review

FOLDER = "pending"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$", re.I)


@dataclass
class Proposal:
    name: str                  # the file name without .toml
    path: Path
    text: str
    problems: list[str]

    @property
    def clean(self) -> bool:
        return not self.problems

    def spoken(self) -> str:
        if self.clean:
            return f"{self.name}, which looks fine"
        return f"{self.name}, which has a problem: {self.problems[0]}"


def pending_dir(automations: Path) -> Path:
    return Path(automations) / FOLDER


def waiting(automations: Path, matches_intent: Callable[[str], Any] | None = None) -> list[Proposal]:
    """Every proposal in the staging folder, checked, newest first."""
    folder = pending_dir(automations)
    try:
        files = sorted(folder.glob("*.toml"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    found = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found.append(Proposal(path.stem, path, text, review.problems(text, matches_intent)))
    return found


def approve(automations: Path, name: str = "",
            matches_intent: Callable[[str], Any] | None = None) -> tuple[bool, str]:
    """Move a proposal into service. Without a name, the newest one.

    A proposal that failed review is refused rather than moved: approving something
    while being told it is broken is a mistake worth making hard to make by accident.
    """
    proposals = waiting(automations, matches_intent)
    if not proposals:
        return False, "There's no automation waiting to be looked at."
    chosen = _pick(proposals, name)
    if chosen is None:
        return False, f"I have nothing waiting called {name}."
    if not chosen.clean:
        return False, (f"I'd rather not put {chosen.name} in as it is: "
                       f"{chosen.problems[0]} Say drop it, or fix it yourself first.")
    target = Path(automations) / f"{chosen.name}.toml"
    replaced = target.exists()
    try:
        target.write_text(chosen.text, encoding="utf-8")
        chosen.path.unlink()
    except OSError as error:
        return False, f"I couldn't move it into place ({type(error).__name__})."
    return True, (f"{'Replaced' if replaced else 'Saved'} {chosen.name}. "
                  "It runs without a model from now on.")


def reject(automations: Path, name: str = "") -> tuple[bool, str]:
    proposals = waiting(automations)
    if not proposals:
        return False, "There's nothing waiting."
    chosen = _pick(proposals, name)
    if chosen is None:
        return False, f"I have nothing waiting called {name}."
    try:
        chosen.path.unlink()
    except OSError as error:
        return False, f"I couldn't remove it ({type(error).__name__})."
    return True, f"Dropped {chosen.name}."


def spoken(automations: Path, matches_intent: Callable[[str], Any] | None = None) -> str:
    """What is waiting, for nova_status and for being asked out loud."""
    proposals = waiting(automations, matches_intent)
    if not proposals:
        return ""
    if len(proposals) == 1:
        return f"One automation is waiting for you: {proposals[0].spoken()}."
    clean = sum(1 for p in proposals if p.clean)
    return (f"{len(proposals)} automations are waiting for you, {clean} of them fine: "
            + ", ".join(p.name for p in proposals) + ".")


def path_for(automations: Path, name: str) -> Path | None:
    """Where a proposal with this name should be written. None if the name is unsafe."""
    name = (name or "").strip()
    if not SAFE_NAME.match(name):
        return None
    folder = pending_dir(automations)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return folder / f"{name}.toml"


def _pick(proposals: list[Proposal], name: str) -> Proposal | None:
    name = (name or "").strip().lower()
    if not name:
        return proposals[0]
    wanted = re.sub(r"\W+", "", name)
    for proposal in proposals:
        if re.sub(r"\W+", "", proposal.name.lower()) == wanted:
            return proposal
    for proposal in proposals:
        if wanted in re.sub(r"\W+", "", proposal.name.lower()):
            return proposal
    return None
