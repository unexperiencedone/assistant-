"""Running several pieces of work for one request, and answering once.

Nova already had the hard half of this: `agent_runner` runs tasks on their own
threads with their own canvas lanes, and `planning.Plan` is a DAG whose steps carry
`after` dependencies. What was missing was the part that uses both -- something that
dispatches the steps whose dependencies are met, waits, dispatches the ones that just
became ready, and produces **one** answer at the end.

That `after` list is why this is one mechanism rather than three. The difference between
working through steps one at a time and firing them all at once is not a different
orchestrator; it is whether the steps declare dependencies:

    1. find what changed              }  no dependencies: both start at once
    2. check the open pull requests   }
    3. write it up (after 1, 2)          waits for both

So a "sequential plan" is a chain of `after`s and a "parallel plan" is their absence,
and a single walker handles both.

Three rules, each of which exists because breaking it makes this worse than not having
it at all:

1. **One request, one answer.** A group speaks once, at the end. Three tasks narrating
   themselves is three voices talking over each other, and half-duplex speech means the
   user hears a jumble rather than a result.
2. **Fan-out is capped.** A small model asked to decompose will decompose anything. The
   cap is what stops "what time is it" becoming four background tasks, and it is why
   this cannot quietly undo the cost cascade it sits on top of.
3. **A failed step is reported, not swallowed.** Steps waiting on it are marked blocked
   and named in the answer. A group that half-worked and says "done" is the worst
   outcome available here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# dispatch(prompt, label) -> task id, or None when the runner had no capacity.
Dispatch = Callable[[str, str], "str | None"]

PENDING, RUNNING, DONE, FAILED, BLOCKED = "pending", "running", "done", "failed", "blocked"


@dataclass
class Step:
    text: str
    after: list[int] = field(default_factory=list)   # 1-based step numbers this waits for
    status: str = PENDING
    task_id: str = ""
    answer: str = ""


@dataclass
class Group:
    """One request's worth of work: several steps and a single answer."""
    id: str
    request: str
    steps: list[Step]
    origin: str = "desktop"
    started_at: float = field(default_factory=time.time)

    @property
    def finished(self) -> bool:
        return all(s.status in (DONE, FAILED, BLOCKED) for s in self.steps)

    @property
    def running(self) -> list[Step]:
        return [s for s in self.steps if s.status == RUNNING]


class Orchestrator:
    """Walks a step DAG, dispatching what is ready and collecting what comes back."""

    def __init__(self, dispatch: Dispatch, max_fanout: int = 3,
                 narrate: Callable[[str], str] | None = None) -> None:
        self.dispatch = dispatch
        self.max_fanout = max(1, int(max_fanout))
        # Turns the stapled-together step answers into one spoken line. Optional, and
        # returns its input unchanged on any doubt, so a group can never end up saying
        # less than it knew (assistant/persona/narrator.py). A group already takes tens
        # of seconds, which is the only reason a model call is affordable here at all.
        self.narrate = narrate
        self.groups: dict[str, Group] = {}
        self._by_task: dict[str, tuple[str, int]] = {}   # task id -> (group id, step index)
        self._lock = threading.RLock()
        self._ids = 0

    # -- starting ---------------------------------------------------------------------
    def start(self, request: str, steps: list[tuple[str, list[int]]], origin: str = "desktop") -> Group | None:
        """Begin a group. Returns it, or None when there is nothing runnable in it.

        Steps beyond the fan-out cap are dropped rather than queued: a model that wanted
        nine steps has misunderstood the request, and running the first three is a more
        useful answer than running nine badly.
        """
        steps = [(text, after) for text, after in steps if (text or "").strip()]
        if not steps:
            return None
        if len(steps) > self.max_fanout:
            steps = steps[: self.max_fanout]
        with self._lock:
            self._ids += 1
            group = Group(id=f"g{self._ids}", request=request, origin=origin,
                          steps=[Step(text.strip(), [n for n in after if 1 <= n <= len(steps)])
                                 for text, after in steps])
            self.groups[group.id] = group
        if not self._dispatch_ready(group):
            with self._lock:
                self.groups.pop(group.id, None)
            return None
        return group

    def _ready(self, group: Group) -> list[int]:
        """Indices of steps whose dependencies have all finished successfully."""
        ready = []
        for index, step in enumerate(group.steps):
            if step.status != PENDING:
                continue
            blockers = [group.steps[n - 1] for n in step.after if n - 1 < len(group.steps)]
            if any(b.status in (FAILED, BLOCKED) for b in blockers):
                step.status = BLOCKED          # its prerequisite failed; it cannot run
                continue
            if all(b.status == DONE for b in blockers):
                ready.append(index)
        return ready

    def _dispatch_ready(self, group: Group) -> bool:
        """Start every step that can run now. True if anything is in flight."""
        with self._lock:
            for index in self._ready(group):
                step = group.steps[index]
                task_id = self.dispatch(step.text, step.text[:48])
                if task_id is None:
                    break      # no capacity; whatever finishes next will try again
                step.status, step.task_id = RUNNING, task_id
                self._by_task[task_id] = (group.id, index)
            return bool(group.running)

    # -- collecting -------------------------------------------------------------------
    def owns(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._by_task

    def finished(self, task_id: str, ok: bool, answer: str) -> str:
        """Record a finished step. Returns the group's answer once it is complete, else "".

        Returning "" is what keeps the group quiet: the controller speaks only when this
        hands it something, so a step finishing mid-group says nothing at all.
        """
        with self._lock:
            found = self._by_task.pop(task_id, None)
            if found is None:
                return ""
            group_id, index = found
            group = self.groups.get(group_id)
            if group is None:
                return ""
            step = group.steps[index]
            step.status = DONE if ok else FAILED
            step.answer = (answer or "").strip()

        # Newly unblocked steps start now; a blocked one is marked as we walk.
        self._dispatch_ready(group)
        with self._lock:
            if group.running or not group.finished:
                return ""
            self.groups.pop(group.id, None)
            return self.summarise(group)

    def summarise(self, group: Group) -> str:
        """One answer for the whole group, saying plainly what did and didn't happen."""
        plain = self._plain(group)
        if self.narrate is None or len(group.steps) < 2:
            return plain
        return self.narrate(plain) or plain

    def _plain(self, group: Group) -> str:
        """The facts, assembled locally. Always correct, never graceful."""
        done = [s for s in group.steps if s.status == DONE]
        failed = [s for s in group.steps if s.status == FAILED]
        blocked = [s for s in group.steps if s.status == BLOCKED]

        parts = [s.answer for s in done if s.answer]
        lines = parts[:]
        if failed:
            lines.append("I couldn't finish: " + "; ".join(s.text for s in failed) + ".")
        if blocked:
            # Named rather than dropped: a step that never ran because something else
            # broke is the thing the user most needs told.
            lines.append("That left " + "; ".join(s.text for s in blocked) + " undone.")
        if not lines:
            # Distinguished on purpose: a step that worked and reported nothing is not
            # the same event as a step that failed, and saying so would be a lie.
            if done:
                return ("That's done, though nothing came back to tell you about. "
                        "The canvas has what each step did.")
            return "None of that worked."
        return "\n".join(lines)

    # -- visibility -------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            return {"groups": [
                {"id": g.id, "request": g.request[:120],
                 "steps": [{"text": s.text, "status": s.status} for s in g.steps]}
                for g in self.groups.values()]}

    def spoken(self) -> str:
        """What the groups are doing, for nova_status."""
        with self._lock:
            groups = list(self.groups.values())
        if not groups:
            return ""
        lines = []
        for group in groups:
            done = sum(1 for s in group.steps if s.status == DONE)
            running = ", ".join(s.text[:40] for s in group.running)
            lines.append(f"{done} of {len(group.steps)} steps done"
                         + (f"; now on {running}" if running else ""))
        return " ".join(lines)

    def cancel(self, group_id: str = "") -> int:
        """Forget a group (the runner cancels the tasks themselves)."""
        with self._lock:
            ids = [group_id] if group_id else list(self.groups)
            dropped = 0
            for gid in ids:
                group = self.groups.pop(gid, None)
                if group is None:
                    continue
                dropped += 1
                for task_id in [t for t, (g, _i) in self._by_task.items() if g == gid]:
                    self._by_task.pop(task_id, None)
            return dropped
