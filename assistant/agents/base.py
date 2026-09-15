"""Common interface for agent CLIs (Claude Code, Antigravity, ...)."""

from __future__ import annotations

import shutil
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .process import NdjsonProcess


@dataclass
class AgentEvent:
    kind: str  # text | tool | result | error
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    ok: bool
    summary: str
    session_id: str | None = None
    seconds: float = 0.0
    cancelled: bool = False


class AgentBackend(ABC):
    name: str = ""
    label: str = ""

    def __init__(self, executable: str, workspace: Path, continue_session: bool) -> None:
        self.executable = executable
        self.workspace = workspace
        self.continue_session = continue_session
        self.session_id: str | None = None

    def resolve_executable(self) -> str | None:
        return shutil.which(self.executable)

    def is_available(self) -> bool:
        return self.resolve_executable() is not None

    def reset_session(self) -> None:
        self.session_id = None

    def start(self) -> bool:
        """Optional: pay start-up cost before the first message."""
        return self.is_available()

    def close(self) -> None:
        """Optional: stop any long-lived process."""

    @abstractmethod
    def build_command(self, exe: str, task: str) -> list[str]: ...

    @abstractmethod
    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        """Translate one JSON line from the CLI into zero or more AgentEvents."""

    def run(
        self, task: str, on_event: Callable[[AgentEvent], None], cancel: threading.Event
    ) -> AgentResult:
        exe = self.resolve_executable()
        if not exe:
            return AgentResult(False, f"I can't find the {self.label} command '{self.executable}'.")

        started = time.time()
        process = NdjsonProcess(self.build_command(exe, task), self.workspace)
        final: AgentEvent | None = None
        try:
            for obj in process.lines(cancel):
                for event in self.parse(obj):
                    on_event(event)
                    if event.kind == "result":
                        final = event
        except OSError as exc:
            return AgentResult(False, f"{self.label} failed to start: {exc}")

        seconds = time.time() - started
        if cancel.is_set():
            return AgentResult(False, "Cancelled.", self.session_id, seconds, cancelled=True)
        if final is None:
            detail = process.stderr_tail[-1] if process.stderr_tail else f"exit code {process.returncode}"
            return AgentResult(False, f"{self.label} stopped without a result: {detail}", self.session_id, seconds)
        return AgentResult(
            ok=not final.data.get("is_error", False),
            summary=final.text.strip(),
            session_id=self.session_id,
            seconds=seconds,
        )


def short(value: Any, limit: int = 90) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
