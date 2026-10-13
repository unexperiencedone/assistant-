"""What is running, and what ran last. One live view, for whoever asks.

Nothing here is a new store. Every command is already written to `data/sessions/*.jsonl`
by `TurnLog.raw`, and the canvas can read a whole turn back through `/api/run`. That is
the durable record and it stays where it is.

What was missing is the *live* answer. Three specific gaps:

- `Task.last_activity` kept the phrase for a tool call -- "running things" -- and threw
  away the target. The bus event carries both; only the vaguer half was being kept.
- `AgentRunner._finish` pops a task from the pool, so the moment a job ended nothing in
  memory knew what it had just done. Answering "what did you just run" meant reading a
  file back.
- Status was assembled from whatever each caller happened to reach for, so the voice,
  the canvas and the phone could each describe the same moment differently.

So this subscribes to the bus and keeps two small things: the command each running task
is on, and a ring of the ones that finished. Every surface then reads the same object --
the `status` intent, the `nova_status` tool, the canvas slice, the phone. A question
about what Nova is doing should have exactly one answer, whoever asks and from wherever.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .events import Event, EventBus

RECENT = 24          # finished commands kept in memory; the files keep the rest
MAX_TARGET = 90


def ago(seconds: float) -> str:
    """How long ago, in the words a person would use out loud."""
    if seconds < 2:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(seconds // 3600)
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


@dataclass
class Command:
    """One thing that was run: the tool, and what it was run on."""
    tool: str
    target: str = ""
    backend: str = ""
    task: str = ""
    at: float = field(default_factory=time.time)
    ok: bool | None = None       # None while it is still going

    def spoken(self) -> str:
        name = self.tool.replace("_", " ") or "something"
        return f"{name} on {self.target}" if self.target else name

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "target": self.target, "backend": self.backend,
                "task": self.task, "at": round(self.at, 3), "ok": self.ok}


@dataclass
class Job:
    """A running task, and the command it is on."""
    task: str
    label: str
    backend: str
    started: float = field(default_factory=time.time)
    command: Command | None = None
    phase: str = ""              # what the agent said it was doing, if it said

    def elapsed(self) -> float:
        return time.time() - self.started


class Watcher:
    """The live answer to "what are you doing" and "what did you just run"."""

    def __init__(self, bus: EventBus, recent: int = RECENT) -> None:
        self.bus = bus
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.recent: deque[Command] = deque(maxlen=recent)
        # Automations run on their own thread and report separately, but to the person
        # asking they are the same kind of fact.
        self.macro: str = ""
        bus.subscribe("agent", self._on_agent)
        bus.subscribe("macro_step", self._on_macro_step)
        bus.subscribe("macro_done", self._on_macro_done)
        bus.subscribe("phase", lambda event: self.note_phase(
            str(event.data.get("task") or ""), str(event.data.get("phase") or "")))

    def _announce(self) -> None:
        """Broadcast the live view, so a session that joined late is not left guessing.

        Called outside the lock on purpose: a subscriber is free to call back in, and
        holding the lock across that would deadlock.
        """
        self.bus.publish("live", **self.state())

    # -- watching ----------------------------------------------------------------------
    def _on_agent(self, event: Event) -> None:
        data = event.data
        task = str(data.get("task") or "")
        kind = data.get("kind")
        backend = str(data.get("backend") or "")
        text = " ".join(str(data.get("text") or "").split())[:MAX_TARGET]

        with self._lock:
            if kind == "start":
                self.jobs[task] = Job(task=task, label=text, backend=backend)
            elif kind == "tool":
                # The target is the half that was being discarded: "web_search" says
                # much less than "web_search on lucknow web design".
                command = Command(tool=str(data.get("tool") or ""), target=text,
                                  backend=backend, task=task)
                job = self.jobs.get(task)
                if job is not None:
                    if job.command is not None and job.command.ok is None:
                        job.command.ok = True     # the previous call returned
                        self.recent.append(job.command)
                    job.command = command
                else:
                    self.recent.append(command)
            elif kind in ("result", "error"):
                job = self.jobs.get(task)
                if job is not None and job.command is not None:
                    job.command.ok = kind == "result"
                    self.recent.append(job.command)
                self.jobs.pop(task, None)
            else:
                # Streamed text and the rest change nothing here; announcing on every
                # chunk would push a slice per token.
                return
        self._announce()

    def _on_macro_step(self, event: Event) -> None:
        with self._lock:
            status = event.data.get("status")
            index = event.data.get("index")
            self.macro = "" if status in ("done", "failed") else f"automation step {index}"
        self._announce()

    def _on_macro_done(self, event: Event) -> None:
        with self._lock:
            self.macro = ""
            name = str(event.data.get("name") or "an automation")
            self.recent.append(Command(tool="automation", target=name,
                                       ok=bool(event.data.get("ok", True))))
        self._announce()

    def note_phase(self, task: str, phase: str) -> None:
        """A milestone the agent declared about its own work (assistant/progress.py)."""
        changed = False
        with self._lock:
            job = self.jobs.get(task or "")
            if job is not None:
                job.phase = " ".join((phase or "").split())[:MAX_TARGET]
                changed = True
        if changed:
            self._announce()

    # -- jobs that run in another app (automation/watch.py) ------------------------------
    def start_external(self, task: str, label: str, backend: str = "watching") -> None:
        """A job Nova handed to another app and is watching: it is running work too."""
        with self._lock:
            self.jobs[task] = Job(task=task, label=label, backend=backend)
        self._announce()

    def end_external(self, task: str, ok: bool = True) -> None:
        with self._lock:
            job = self.jobs.pop(task, None)
            if job is not None:
                self.recent.append(Command(tool=job.label, ok=ok))
        self._announce()

    # -- answering ---------------------------------------------------------------------
    def running(self) -> list[Job]:
        with self._lock:
            return list(self.jobs.values())

    def last(self, count: int = 1) -> list[Command]:
        with self._lock:
            return list(self.recent)[-max(1, count):][::-1]

    def last_sentence(self) -> str:
        """"The last thing I finished was X, a minute ago" -- or nothing, on a fresh run.

        Shared with the controller's idle answer so the two cannot drift into describing
        the same moment in two different ways.
        """
        with self._lock:
            latest = self.recent[-1] if self.recent else None
        if latest is None:
            return ""
        done = "finished" if latest.ok is not False else "failed at"
        return f"The last thing I {done} was {latest.spoken()}, {ago(time.time() - latest.at)}."

    def spoken(self) -> str:
        """The answer to "what are you doing", said in one or two sentences.

        Names the command, not a category: "running things" told you the mechanism and
        nothing about the work, which was the complaint that started this.
        """
        with self._lock:
            jobs = list(self.jobs.values())
            macro = self.macro

        parts: list[str] = []
        for job in jobs:
            if job.backend == "watching":
                # Another app's job: "Antigravity in school-sample is working, 4 minutes in".
                minutes = int(job.elapsed() // 60)
                running_for = "just started" if minutes < 1 else f"{minutes} minute{'s' if minutes != 1 else ''} in"
                parts.append(f"{job.label} is {job.phase or 'working'}, {running_for}")
                continue
            # A declared phase beats the command, and the command beats a category: the
            # complaint that started this was hearing "running things" five times.
            where = job.phase or (job.command.spoken() if job.command else "thinking it through")
            label = f"{job.backend} on {job.label}" if job.label else job.backend
            parts.append(f"{label} for {int(job.elapsed())} seconds, currently {where}")
        if macro:
            parts.append(macro)
        if parts:
            return ". ".join(parts) + "."
        last = self.last_sentence()
        if last:
            return f"Nothing is running. {last}"
        return "Nothing is running, and I haven't run anything yet this session."

    def state(self) -> dict[str, Any]:
        """For the canvas slice, so every connected session sees the same thing."""
        with self._lock:
            return {
                "running": [{"task": j.task, "label": j.label, "backend": j.backend,
                             "seconds": round(j.elapsed(), 1), "phase": j.phase,
                             "command": j.command.as_dict() if j.command else None}
                            for j in self.jobs.values()],
                "macro": self.macro,
                "recent": [c.as_dict() for c in list(self.recent)[-8:]][::-1],
            }
