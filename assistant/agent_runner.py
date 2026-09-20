"""Runs agent turns in the background: several at once, each on its own thread.

Each task has its own id, its own agent session (a separate `claude -p --resume`
process, for instance) and its own graph lane, so two tasks never write into each
other's context or picture.

Speech can't be parallel, so only the *focused* task narrates: the others post to
the canvas silently and get a short spoken line when they finish.

Errands (open, play, search, send) may run alongside anything; only one *heavy*
task runs at a time, so two big jobs never fight over the same windows and files.
See `triage.py`.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agents.base import AgentBackend, AgentEvent, AgentResult, short
from .audio.tts import Speaker
from .events import EventBus
from .planning import Plan
from . import triage

NARRATE_EVERY_SECONDS = 30
_TOOL_PHRASES = {
    "edit": "editing {}", "write": "writing {}", "multiedit": "editing {}", "read": "reading {}",
    "bash": "running a command", "powershell": "running a command", "run_command": "running a command",
    "grep": "searching the code", "glob": "looking through files", "websearch": "searching the web",
    "webfetch": "reading a web page", "search_web": "searching the web", "write_to_file": "writing {}",
    "replace_file_content": "editing {}", "view_file": "reading {}", "task": "starting a helper agent",
}

Finished = Callable[[AgentBackend, AgentResult, str], None]


@dataclass
class Task:
    id: str
    backend: AgentBackend
    prompt: str
    label: str
    executing_plan: bool
    weight: str = triage.HEAVY
    cancel: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.time)
    last_activity: str = ""
    thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def elapsed_phrase(self) -> str:
        minutes, seconds = divmod(int(time.time() - self.started), 60)
        return f"{minutes} minutes {seconds} seconds" if minutes else f"{seconds} seconds"


class AgentRunner:
    def __init__(self, bus: EventBus, plan: Plan, speaker: Speaker, on_finished: Finished,
                 max_parallel: int = 2) -> None:
        self.bus = bus
        self.plan = plan
        self.speaker = speaker
        self.on_finished = on_finished
        self.max_parallel = max(1, max_parallel)
        self.tasks: dict[str, Task] = {}
        self.focus: str | None = None      # the task Nova talks about
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # -- state ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return any(t.running for t in self.tasks.values())

    @property
    def active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.running]

    @property
    def has_capacity(self) -> bool:
        return len(self.active) < self.max_parallel

    @property
    def heavy_task(self) -> Task | None:
        """The one big job allowed at a time, if it's running."""
        return next((t for t in self.active if t.weight == triage.HEAVY), None)

    def blocked_by(self, weight: str) -> Task | None:
        """The task that stops a `weight` request starting now, or None if it can start."""
        if not self.has_capacity:
            return self.active[0] if self.active else None
        return self.heavy_task if weight == triage.HEAVY else None

    def can_start(self, weight: str) -> bool:
        return self.blocked_by(weight) is None

    @property
    def backend(self) -> AgentBackend | None:
        task = self.focused
        return task.backend if task else None

    @property
    def focused(self) -> Task | None:
        task = self.tasks.get(self.focus or "")
        return task if task and task.running else next(iter(self.active), None)

    @property
    def label(self) -> str:
        task = self.focused
        return task.label if task else ""

    @property
    def cancel_event(self) -> threading.Event:
        """So work started from inside a turn (OpenRouter delegating to Claude) stops too."""
        task = self.focused
        return task.cancel if task else threading.Event()

    def plan_is_running(self) -> bool:
        return any(t.executing_plan for t in self.active)

    # -- starting and stopping ----------------------------------------------------------
    def start(self, backend: AgentBackend, prompt: str, label: str, executing_plan: bool = False,
              weight: str | None = None) -> str | None:
        """Returns the new task's id, or None when it has to wait for a running task."""
        weight = weight or triage.weight(label or prompt, executing_plan)
        with self._lock:
            if not self.can_start(weight):
                return None
            task_id = f"t{next(self._ids)}"
            task = Task(task_id, backend, prompt, label, executing_plan, weight)
            self.tasks[task_id] = task
            self.focus = task_id
        if executing_plan:
            self.plan.begin_execution()
            self.bus.publish("plan", **self.plan.to_dict())
        self.bus.publish("agent", kind="start", backend=backend.label, text=label, task=task_id)
        task.thread = threading.Thread(target=self._work, args=(task,), name=f"agent-{task_id}", daemon=True)
        task.thread.start()
        return task_id

    def cancel(self, task_id: str | None = None, everything: bool = False) -> int:
        """Cancel every task, one by id, or (by default) the focused one. Returns how many."""
        if everything:
            targets = self.active
        elif task_id:
            task = self.tasks.get(task_id)
            targets = [task] if task and task.running else []
        else:
            targets = [t for t in [self.focused] if t]
        for task in targets:
            task.cancel.set()
        return len(targets)

    def status_sentence(self) -> str:
        active = self.active
        if not active:
            return ""
        if len(active) == 1:
            task = active[0]
            last = f" Latest: {task.last_activity}." if task.last_activity else ""
            return f"{task.backend.label} has been working on {task.label} for {task.elapsed_phrase()}.{last}"
        parts = [f"{t.label} for {t.elapsed_phrase()}" for t in active]
        return f"{len(active)} tasks are running: " + "; ".join(parts) + "."

    # -- background work ------------------------------------------------------------------
    def _work(self, task: Task) -> None:
        last_spoken = time.time()

        def on_event(event: AgentEvent) -> None:
            nonlocal last_spoken
            if task.executing_plan and event.kind == "tool" and self.plan.infer_from_action(event.text):
                # Update the plan first so the canvas attaches this action to the right step.
                self.bus.publish("plan", **self.plan.to_dict())
            self.bus.publish("agent", kind=event.kind, backend=task.backend.label, tool=event.tool,
                             text=event.text, task=task.id)
            if event.kind == "tool":
                task.last_activity = _phrase(event)
                # Only the focused task narrates; a second voice over the first would be noise.
                if (self.focus == task.id and time.time() - last_spoken > NARRATE_EVERY_SECONDS
                        and not self.speaker.is_busy):
                    self.speaker.say(task.last_activity.capitalize() + ".")
                    last_spoken = time.time()
            elif event.kind == "text":
                self._update_plan(event.text, task)

        result = task.backend.run(task.prompt, on_event, task.cancel)
        self._update_plan(result.summary, task)
        self._finish(task, result)

    def _finish(self, task: Task, result: AgentResult) -> None:
        if task.executing_plan and not result.cancelled:
            self.plan.finish(result.ok)
            self.bus.publish("plan", **self.plan.to_dict())

        self.bus.publish("agent", kind="result" if result.ok else "error", backend=task.backend.label,
                         text=f"{result.summary or result.detail} ({result.seconds:.0f}s)",
                         data=result.data, task=task.id)
        if task.backend is not None and task.id != self.focus:
            task.backend.close()  # a spawned session for a background task isn't reused
        self.on_finished(task.backend, result, task.id)
        with self._lock:
            self.tasks.pop(task.id, None)
            if self.focus == task.id:
                self.focus = next((t.id for t in self.active), None)

    def _update_plan(self, text: str, task: Task) -> None:
        """A [[PLAN]] block is how plans are made, so always read those; step markers only
        mean something while that plan is being executed."""
        changed = self.plan.replace_from_block(text)
        if task.executing_plan:
            changed = self.plan.apply_markers(text) or changed
        if changed:
            self.bus.publish("plan", **self.plan.to_dict())


def _phrase(event: AgentEvent) -> str:
    template = _TOOL_PHRASES.get(event.tool.lower())
    if template:
        target = Path(event.text).name if "{}" in template and event.text else "a file"
        return template.format(target)
    return f"using {event.tool.replace('_', ' ')}" if event.tool else "working"
