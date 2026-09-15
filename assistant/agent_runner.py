"""Runs one agent turn at a time in the background, streaming progress to the
event bus, updating the plan from agent output, and narrating sparingly."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .agents.base import AgentBackend, AgentEvent, AgentResult, short
from .audio.tts import Speaker
from .events import EventBus
from .planning import Plan

NARRATE_EVERY_SECONDS = 30
_TOOL_PHRASES = {
    "edit": "editing {}", "write": "writing {}", "multiedit": "editing {}", "read": "reading {}",
    "bash": "running a command", "powershell": "running a command", "run_command": "running a command",
    "grep": "searching the code", "glob": "looking through files", "websearch": "searching the web",
    "webfetch": "reading a web page", "search_web": "searching the web", "write_to_file": "writing {}",
    "replace_file_content": "editing {}", "view_file": "reading {}", "task": "starting a helper agent",
}

Finished = Callable[[AgentBackend, AgentResult], None]


class AgentRunner:
    def __init__(self, bus: EventBus, plan: Plan, speaker: Speaker, on_finished: Finished) -> None:
        self.bus = bus
        self.plan = plan
        self.speaker = speaker
        self.on_finished = on_finished
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.backend: AgentBackend | None = None
        self.label = ""
        self.started_at = 0.0
        self.last_activity = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, backend: AgentBackend, prompt: str, label: str, executing_plan: bool = False) -> None:
        self._cancel = threading.Event()
        self.backend, self.label, self.started_at, self.last_activity = backend, label, time.time(), ""
        if executing_plan:
            self.plan.begin_execution()
            self.bus.publish("plan", **self.plan.to_dict())
        self.bus.publish("agent", kind="start", backend=backend.label, text=label)
        self._thread = threading.Thread(
            target=self._work, args=(backend, prompt, executing_plan, self._cancel), name="agent", daemon=True
        )
        self._thread.start()

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancel.set()
        return True

    def status_sentence(self) -> str:
        if not self.running or not self.backend:
            return ""
        minutes, seconds = divmod(int(time.time() - self.started_at), 60)
        elapsed = f"{minutes} minutes {seconds} seconds" if minutes else f"{seconds} seconds"
        last = f" Latest: {self.last_activity}." if self.last_activity else ""
        return f"{self.backend.label} has been working on {self.label} for {elapsed}.{last}"

    # -- background work ------------------------------------------------------
    def _work(self, backend: AgentBackend, prompt: str, executing_plan: bool, cancel: threading.Event) -> None:
        last_spoken = time.time()

        def on_event(event: AgentEvent) -> None:
            nonlocal last_spoken
            if executing_plan and event.kind == "tool" and self.plan.infer_from_action(event.text):
                # Update the plan first so the canvas attaches this action to the right step.
                self.bus.publish("plan", **self.plan.to_dict())
            self.bus.publish("agent", kind=event.kind, backend=backend.label, tool=event.tool, text=short(event.text, 400))
            if event.kind == "tool":
                self.last_activity = _phrase(event)
                if time.time() - last_spoken > NARRATE_EVERY_SECONDS and not self.speaker.is_busy:
                    self.speaker.say(self.last_activity.capitalize() + ".")
                    last_spoken = time.time()
            elif event.kind == "text":
                self._update_plan(event.text)

        result = backend.run(prompt, on_event, cancel)
        self._update_plan(result.summary)
        if executing_plan and not result.cancelled:
            self.plan.finish(result.ok)
            self.bus.publish("plan", **self.plan.to_dict())

        self.bus.publish("agent", kind="result" if result.ok else "error", backend=backend.label,
                         text=f"{short(result.summary, 600)} ({result.seconds:.0f}s)")
        self.on_finished(backend, result)

    def _update_plan(self, text: str) -> None:
        if self.plan.replace_from_block(text) | self.plan.apply_markers(text):
            self.bus.publish("plan", **self.plan.to_dict())


def _phrase(event: AgentEvent) -> str:
    template = _TOOL_PHRASES.get(event.tool.lower())
    if template:
        target = Path(event.text).name if "{}" in template and event.text else "a file"
        return template.format(target)
    return f"using {event.tool.replace('_', ' ')}" if event.tool else "working"
