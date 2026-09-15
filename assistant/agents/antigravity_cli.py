"""Antigravity CLI backend: `agy -p <message> --output-format stream-json`.

Each thing you say is one `agy -p` run that continues the same conversation
via `--conversation`. agy streams `step_update` events per agent step and ends
with a `result` event carrying the full response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..config import AntigravityAgentSettings
from .base import AgentBackend, AgentEvent, short

_TEXT_STEPS = {"agent_response", "user_input"}
_DETAIL_KEYS = ("tool_name", "name", "command", "command_line", "file", "path", "absolute_path", "url", "query")


class AntigravityCliAgent(AgentBackend):
    name = "antigravity"
    label = "Antigravity"

    def __init__(self, settings: AntigravityAgentSettings, workspace: Path, continue_session: bool, instructions: str) -> None:
        super().__init__(settings.executable, workspace, continue_session)
        self.settings = settings
        self.instructions = instructions
        self._text_buffer = ""
        self._announced_steps: set[int] = set()

    def build_command(self, exe: str, task: str) -> list[str]:
        self._text_buffer = ""
        self._announced_steps.clear()
        if self.instructions and not (self.continue_session and self.session_id):
            # agy has no system-prompt flag: give the voice rules once per conversation.
            task = f"{self.instructions}\n\n---\n\n{task}"
        cmd = [exe, "-p", task, "--output-format", "stream-json"]
        if self.settings.mode:
            cmd += ["--mode", self.settings.mode]
        if self.settings.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if self.settings.model:
            cmd += ["--model", self.settings.model]
        if self.settings.print_timeout:
            cmd += ["--print-timeout", self.settings.print_timeout]
        if self.continue_session and self.session_id:
            cmd += ["--conversation", self.session_id]
        return cmd

    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        event = obj.get("event")
        if event == "init":
            self.session_id = obj.get("conversation_id") or self.session_id
        elif event == "step_update":
            yield from self._parse_step(obj.get("step_update", {}))
        elif event == "result":
            yield from self._flush_text()
            result = obj.get("result", {})
            self.session_id = result.get("conversation_id") or self.session_id
            yield AgentEvent(
                "result",
                text=result.get("response", ""),
                data={"is_error": result.get("status") != "SUCCESS", "status": result.get("status")},
            )
        elif "raw" in obj:
            yield AgentEvent("text", text=obj["raw"])

    def _parse_step(self, step: dict[str, Any]) -> Iterable[AgentEvent]:
        step_type = step.get("step_type", "")
        if step_type == "agent_response":
            self._text_buffer += step.get("text_delta", "")
            # Emit whole lines so the activity feed isn't flooded with tokens.
            while "\n" in self._text_buffer:
                line, self._text_buffer = self._text_buffer.split("\n", 1)
                if line.strip():
                    yield AgentEvent("text", text=line)
            if step.get("state") == "DONE":
                yield from self._flush_text()
        elif step_type and step_type not in _TEXT_STEPS:
            index = step.get("step_index", -1)
            if index in self._announced_steps:
                return
            self._announced_steps.add(index)
            detail = next((short(step[k], 80) for k in _DETAIL_KEYS if step.get(k)), "")
            yield AgentEvent("tool", tool=step_type, text=detail)

    def _flush_text(self) -> Iterable[AgentEvent]:
        if self._text_buffer.strip():
            yield AgentEvent("text", text=self._text_buffer)
        self._text_buffer = ""
