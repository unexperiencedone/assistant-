"""Holds the available agent backends and which one is currently selected."""

from __future__ import annotations

from ..config import Settings
from .antigravity_cli import AntigravityCliAgent
from .base import AgentBackend
from .claude_cli import ClaudeCliAgent
from .rules import session_instructions

# Words a speech recognizer might produce for each backend.
ALIASES = {
    "claude": "claude",
    "cloud": "claude",
    "clod": "claude",
    "claude code": "claude",
    "antigravity": "antigravity",
    "anti gravity": "antigravity",
    "anti-gravity": "antigravity",
    "gravity": "antigravity",
    "agy": "antigravity",
    "gemini": "antigravity",
}


class AgentRegistry:
    def __init__(self, settings: Settings) -> None:
        workspace = settings.workspace
        cont = settings.agents.continue_session
        instructions = session_instructions(settings.assistant.name, workspace)
        self.backends: dict[str, AgentBackend] = {
            "claude": ClaudeCliAgent(settings.agents.claude, workspace, cont, instructions),
            "antigravity": AntigravityCliAgent(settings.agents.antigravity, workspace, cont, instructions),
        }
        self.current_name = settings.agents.default if settings.agents.default in self.backends else "claude"
        if not self.current.is_available():
            fallback = next((n for n, b in self.backends.items() if b.is_available()), None)
            if fallback:
                self.current_name = fallback

    @property
    def current(self) -> AgentBackend:
        return self.backends[self.current_name]

    def resolve(self, spoken: str) -> str | None:
        return ALIASES.get(spoken.lower().strip())

    def select(self, name: str) -> AgentBackend:
        self.current_name = name
        return self.current

    def availability(self) -> dict[str, bool]:
        return {name: backend.is_available() for name, backend in self.backends.items()}

    def close(self) -> None:
        for backend in self.backends.values():
            backend.close()
