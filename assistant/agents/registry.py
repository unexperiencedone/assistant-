"""Holds the available agent backends and which one is currently selected."""

from __future__ import annotations

from ..config import Settings
from .antigravity_cli import AntigravityCliAgent
from .base import AgentBackend
from .claude_cli import ClaudeCliAgent
from .chat_api import GroqAgent, OpenRouterAgent
from .rules import session_instructions

# Words a speech recognizer might produce for each backend.
ALIASES = {
    "groq": "groq",
    "grok": "groq",          # what the recognizer usually hears
    "grock": "groq",
    "rock": "groq",
    "llama": "groq",
    "lama": "groq",
    "fast model": "groq",
    "openrouter": "openrouter",
    "open router": "openrouter",
    "router": "openrouter",
    "free model": "openrouter",
    "free models": "openrouter",
    "open-router": "openrouter",
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
            "openrouter": OpenRouterAgent(settings.agents.openrouter, workspace, cont, settings.assistant.name),
            "groq": GroqAgent(settings.agents.groq, workspace, cont, settings.assistant.name),
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

    def attach_tools(self, context) -> None:
        """Give model-based agents (Groq, OpenRouter) Nova's local abilities."""
        for backend in self.backends.values():
            if hasattr(backend, "context"):
                backend.context = context

    def apply_profile(self, profile) -> None:
        """Give each agent the profile summary it's allowed to see (assistant/profile)."""
        from ..profile import audience_for

        for name, backend in self.backends.items():
            backend.set_profile(profile.standing(audience_for(name)))

    def spawn_current(self) -> AgentBackend:
        """An independent instance of the selected agent, for a parallel task."""
        return self.current.spawn()

    def close(self) -> None:
        for backend in self.backends.values():
            backend.close()
