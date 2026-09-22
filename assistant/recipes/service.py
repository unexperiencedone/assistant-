"""Collecting what worked, and handing it back on the next similar request.

Two jobs, and the split matters. Collecting is passive: the service listens on the bus
for the tool calls an agent makes, so nothing else has to be instrumented -- the events
were already being published for the canvas (`agent_runner.py`). Handing back is
deliberate: a hint goes in only when a stored request really resembles the new one.

The matching threshold is the whole design. A hint about the wrong task is worse than
no hint, because a model given a confident irrelevant instruction follows it. So this
reuses the same conservative word matching the skills suggester uses, and stays silent
whenever it isn't sure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..events import EventBus
from .store import RecipeStore

# Words too ordinary to mean two requests are alike.
_WEAK = {"please", "could", "would", "there", "thing", "just", "that", "this", "with",
         "have", "what", "when", "some", "into", "make", "give", "want", "need", "then"}
MIN_OVERLAP = 2        # distinctive words two requests must share before one informs the other
MAX_TOOLS_TRACKED = 24


class RecipeService:
    def __init__(self, path: Path, bus: EventBus, settings: Any = None) -> None:
        self.store = RecipeStore(path)
        self.bus = bus
        self.settings = settings
        # task id -> the tools that task has called, in order.
        self._tools: dict[str, list[str]] = {}
        bus.subscribe("agent", self._on_agent)

    def close(self) -> None:
        self.store.close()

    # -- collecting -------------------------------------------------------------------
    def _on_agent(self, event) -> None:
        """Accumulate each task's tool calls. Already-published events, so this costs
        nothing extra; the task id is what keeps parallel tasks from mixing."""
        data = event.data
        task = str(data.get("task") or "")
        if not task:
            return
        if data.get("kind") == "tool":
            tool = str(data.get("tool") or "").strip()
            if tool:
                calls = self._tools.setdefault(task, [])
                # A tool called twice in a row is one step, not two.
                if not calls or calls[-1] != tool:
                    calls.append(tool)
                del calls[MAX_TOOLS_TRACKED:]
        elif data.get("kind") in ("result", "error"):
            self._tools.setdefault(task, [])

    def tools_for(self, task_id: str) -> list[str]:
        return list(self._tools.get(task_id or "", []))

    def finished(self, task_id: str) -> None:
        """Drop a task's collected calls once they have been recorded."""
        self._tools.pop(task_id or "", None)

    def remember(self, task_type: str, utterance: str, backend: str = "",
                 seconds: float = 0.0, task_id: str = "", skill: str = "") -> None:
        tools = self.tools_for(task_id)
        self.store.remember(task_type, utterance, tools=tools, backend=backend,
                            seconds=seconds, skill=skill)
        self.finished(task_id)
        self.bus.publish("recipes", **self.store.state())

    def note_failure(self, task_type: str, note: str) -> None:
        self.store.note_failure(task_type, note)
        self.bus.publish("recipes", **self.store.state())

    # -- handing back -----------------------------------------------------------------
    def best(self, text: str) -> dict[str, Any] | None:
        """The stored recipe whose original request most resembles this one."""
        from ..matching import tokens

        said = {w for w in tokens(text or "") if len(w) > 3 and w not in _WEAK}
        if not said:
            return None
        best, best_score = None, 0
        for row in self.store.all():
            if not row["utterance"]:
                continue
            theirs = {w for w in tokens(row["utterance"]) if len(w) > 3 and w not in _WEAK}
            shared = len(said & theirs)
            if shared < MIN_OVERLAP or shared <= best_score:
                continue
            best, best_score = row, shared
        return best

    def hint(self, text: str) -> str:
        """The block put in front of a model for a request like one done before.

        Phrased as evidence rather than instruction on purpose: "this worked" invites a
        model to adapt it, where "do this" invites it to replay steps that may not fit.
        A recipe is a lead, not a script.
        """
        row = self.best(text)
        if row is None:
            return ""
        lines = [f"You have done this kind of thing before (\"{row['utterance'][:120]}\")."]
        if row["tools"]:
            lines.append("What worked, in order: " + ", ".join(row["tools"]) + ".")
        if row["skill"]:
            lines.append(f"The '{row['skill']}' skill applied.")
        if row["backend"] and row["seconds"]:
            lines.append(f"It took {row['backend']} about {row['seconds']:.0f} seconds.")
        if row["pitfalls"]:
            lines.append("Known dead ends, don't repeat them: " + " | ".join(
                row["pitfalls"].splitlines()[-3:]))
        lines.append("Treat this as evidence, not a script: do what fits this request.")
        return "\n".join(lines)
