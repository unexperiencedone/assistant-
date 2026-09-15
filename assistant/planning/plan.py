"""The plan you build by talking: a small DAG of steps the agent executes.

The agent writes plans as a block; steps may name the steps they depend on:

    [[PLAN: Notes app]]
    1. Create the project
    2. Build the API (after 1)
    3. Build the UI (after 1)
    4. Write tests (after 2, 3)
    [[/PLAN]]

Steps without "(after ...)" depend on the previous step. While executing, the
agent prints `[[STEP n START]]` and `[[STEP n DONE]]` / `[[STEP n FAILED]]`,
which drive the live status colors on the canvas.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field

STEP_MARKER = re.compile(r"\[\[STEP (\d+) (START|DONE|FAILED)\]\]", re.I)
PLAN_BLOCK = re.compile(r"\[\[PLAN:?\s*(?P<title>[^\]]*)\]\](?P<body>.*?)\[\[/PLAN\]\]", re.I | re.S)
_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(?P<text>.+?)\s*$", re.M)
_AFTER = re.compile(r"\s*\((?:after|depends on|needs|requires)\s*:?\s*(?P<deps>[\d,\s&and]+)\)\s*$", re.I)


def strip_markup(text: str) -> str:
    """Remove plan blocks and step markers so they aren't read aloud."""
    return STEP_MARKER.sub("", PLAN_BLOCK.sub("", text)).strip()


def parse_step_line(text: str, number: int) -> tuple[str, list[int]]:
    """'Write tests (after 2, 3)' -> ('Write tests', [2, 3]). Default: the previous step."""
    m = _AFTER.search(text)
    if not m:
        return text.strip(), ([number - 1] if number > 1 else [])
    deps = sorted({int(d) for d in re.findall(r"\d+", m.group("deps")) if 0 < int(d) < number})
    return text[: m.start()].strip(), deps


_ACTION_STOPWORDS = {
    "powershell", "bash", "python", "item", "path", "force", "file", "files", "write", "new", "type",
    "directory", "folder", "the", "and", "with", "into", "from", "this", "that", "echo", "content",
    "users", "kumar", "appdata", "local", "temp", "command", "run",
}


def _action_tokens(detail: str) -> dict[str, int]:
    """'C:\\x\\site\\index.html' -> {'index.html': 3, 'index': 2, 'site': 1}."""
    weights: dict[str, int] = {}
    for word in re.findall(r"[\w.\-]+", detail.lower()):
        base = word.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        if "." in base.strip("."):
            stem = base.rsplit(".", 1)[0]
            weights[base] = max(weights.get(base, 0), 3)
            if len(stem) >= 3 and stem not in _ACTION_STOPWORDS:
                weights[stem] = max(weights.get(stem, 0), 2)
            for part in re.split(r"[_\-.]", stem):  # test_login.py -> test, login
                if len(part) >= 4 and part != stem and part not in _ACTION_STOPWORDS:
                    weights.setdefault(part, 1)
        elif len(base) >= 4 and base not in _ACTION_STOPWORDS and not base.isdigit():
            weights.setdefault(base, 1)
    return weights


@dataclass
class PlanStep:
    text: str
    status: str = "pending"  # pending | running | done | failed
    after: list[int] = field(default_factory=list)  # 1-based step numbers this step waits for


@dataclass
class Plan:
    title: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    # -- editing ----------------------------------------------------------
    def add_steps(self, texts: list[str], title: str = "") -> int:
        with self._lock:
            if title and not self.title:
                self.title = title
            seen = {s.text.lower() for s in self.steps}
            added = 0
            for raw in texts:
                text, after = parse_step_line(raw, len(self.steps) + 1)
                if text and text.lower() not in seen:
                    seen.add(text.lower())
                    self.steps.append(PlanStep(text, after=after))
                    added += 1
            return added

    def remove_step(self, number: int) -> PlanStep | None:
        """Remove a step and rewire dependencies so the graph stays connected."""
        with self._lock:
            if not 1 <= number <= len(self.steps):
                return None
            removed = self.steps.pop(number - 1)
            for step in self.steps:
                deps = set(step.after)
                if number in deps:
                    deps.discard(number)
                    deps.update(removed.after)  # inherit the removed step's prerequisites
                step.after = sorted(d - 1 if d > number else d for d in deps)
            return removed

    def clear(self) -> None:
        with self._lock:
            self.title = ""
            self.steps.clear()

    @property
    def is_empty(self) -> bool:
        return not self.steps

    def replace_from_block(self, text: str) -> bool:
        """Adopt the last [[PLAN]] block in agent output. Returns True if the plan changed."""
        blocks = list(PLAN_BLOCK.finditer(text))
        if not blocks:
            return False
        block = blocks[-1]
        lines = [m.group("text") for m in _STEP_LINE.finditer(block.group("body"))]
        if not lines:
            return False
        parsed = [parse_step_line(line, i) for i, line in enumerate(lines, 1)]
        title = block.group("title").strip() or self.title
        with self._lock:
            if title == self.title and parsed == [(s.text, s.after) for s in self.steps]:
                return False  # same plan seen again (streamed text, then the final result)
            self.title = title
            self.steps = [PlanStep(text, after=after) for text, after in parsed]
        return True

    # -- execution tracking -------------------------------------------------
    def begin_execution(self) -> None:
        with self._lock:
            for step in self.steps:
                if step.status in ("failed", "running"):
                    step.status = "pending"
            self._advance()

    def apply_markers(self, text: str) -> bool:
        """Update step statuses from agent output. Returns True if anything changed."""
        changed = False
        with self._lock:
            for number, event in STEP_MARKER.findall(text):
                index = int(number) - 1
                if not 0 <= index < len(self.steps):
                    continue
                new = {"START": "running", "DONE": "done", "FAILED": "failed"}[event.upper()]
                if self.steps[index].status != new:
                    self.steps[index].status = new
                    changed = True
            if changed:
                self._advance()
        return changed

    def infer_from_action(self, detail: str) -> bool:
        """Guess which step an agent action belongs to from its target (file name, command).

        Models often run several steps' tool calls back to back and only print
        [[STEP n]] markers at the end, so markers alone can't light steps up live.
        If an action clearly matches a later step, earlier running steps are
        considered finished and that step becomes the running one.
        """
        weights = _action_tokens(detail)
        if not weights:
            return False
        with self._lock:
            best, best_score = None, 0
            for i, step in enumerate(self.steps):
                if step.status in ("done", "failed"):
                    continue
                text = step.text.lower()
                # A token near the start is the step's subject ("Write style.css ..."),
                # not a passing mention ("... linked to style.css").
                score = sum(w + (2 if text.find(token) < 30 else 0) for token, w in weights.items() if token in text)
                if score > best_score:  # ties keep the earliest step
                    best, best_score = i, score
            if best is None or self.steps[best].status == "running":
                return False
            for step in self.steps[:best]:
                if step.status in ("running", "pending") and self._is_prerequisite(step, best):
                    step.status = "done"
            for step in self.steps:
                if step.status == "running":
                    step.status = "done"
            self.steps[best].status = "running"
            return True

    def _is_prerequisite(self, step: PlanStep, target_index: int) -> bool:
        index = self.steps.index(step) + 1
        stack, seen = list(self.steps[target_index].after), set()
        while stack:
            dep = stack.pop()
            if dep == index:
                return True
            if dep not in seen and 0 < dep <= len(self.steps):
                seen.add(dep)
                stack.extend(self.steps[dep - 1].after)
        return False

    def finish(self, ok: bool) -> None:
        with self._lock:
            for step in self.steps:
                if ok and step.status != "failed":
                    step.status = "done"
                elif not ok and step.status == "running":
                    step.status = "failed"

    def _advance(self) -> None:
        """If nothing is marked running, assume the agent moved on to the next ready step."""
        if any(s.status == "running" for s in self.steps):
            return
        for step in self.steps:
            if step.status == "pending" and all(self.steps[d - 1].status == "done" for d in step.after):
                step.status = "running"
                return

    # -- rendering ----------------------------------------------------------
    def to_prompt(self) -> str:
        """The plan exactly as the user currently sees it (they may have edited it by voice)."""
        lines = [f"Go ahead and execute this plan now: {self.title or 'the plan'}"]
        for i, s in enumerate(self.steps, 1):
            deps = f" (after {', '.join(map(str, s.after))})" if s.after and s.after != [i - 1] else ""
            lines.append(f"{i}. {s.text}{deps}")
        lines.append("Print [[STEP n START]] when you begin a step and [[STEP n DONE]] or [[STEP n FAILED]] when it ends.")
        return "\n".join(lines)

    def to_speech(self) -> str:
        if self.is_empty:
            return "The plan is empty."
        parts = [f"step {i}, {s.text}" for i, s in enumerate(self.steps, 1)]
        head = f"The {self.title + ' ' if self.title else ''}plan has {len(self.steps)} steps: "
        return head + "; ".join(parts) + "."

    def to_dict(self) -> dict:
        with self._lock:
            return {"title": self.title, "steps": [asdict(s) for s in self.steps]}
