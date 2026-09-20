"""Antigravity CLI backend: `agy -p <message> --output-format stream-json`.

Each thing you say is one `agy -p` run that continues the same conversation
via `--conversation`. agy streams `step_update` events per agent step and ends
with a `result` event carrying the full response.

Stream shape (captured from agy):
    {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE",
        "tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": "..."}}}}
    ... "state": "ERROR", "tool_info": {..., "error": {"message": "permission check failed ..."}}
    {"event": "result", "result": {"status": "SUCCESS", "response": "",
        "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}}

Headless agy can't ask for approval: anything not allowed in its settings is
auto-denied, and the run still reports status SUCCESS with an empty response.
So "success" alone means nothing; denials and tool errors are checked too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..config import AntigravityAgentSettings
from .base import AgentBackend, AgentEvent, short

_PARAM_KEYS = ("CommandLine", "AbsolutePath", "TargetFile", "FilePath", "Url", "Query", "SearchPath", "Pattern", "DirectoryPath")
_TEXT_KEYS = ("text", "message", "content")
AGY_SETTINGS = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


class AntigravityCliAgent(AgentBackend):
    name = "antigravity"
    label = "Antigravity"

    def __init__(self, settings: AntigravityAgentSettings, workspace: Path, continue_session: bool, instructions: str) -> None:
        super().__init__(settings.executable, workspace, continue_session)
        self.settings = settings
        self.base_instructions = instructions
        self.instructions = instructions
        self._profile_update = ""  # a changed profile, told once to a conversation already under way
        self._reset_turn()

    def spawn(self) -> "AntigravityCliAgent":
        twin = AntigravityCliAgent(self.settings, self.workspace, self.continue_session, self.base_instructions)
        twin.instructions = self.instructions
        twin.recorder = self.recorder
        return twin

    def set_profile(self, standing: str) -> None:
        instructions = f"{self.base_instructions}\n\n{standing}" if standing else self.base_instructions
        if instructions != self.instructions:
            self.instructions = instructions
            if self.session_id:
                self._profile_update = standing or "The user turned profile personalization off; ignore the earlier profile."

    def _reset_turn(self) -> None:
        self._text_buffer = ""
        self._announced_steps: set[int] = set()
        self._errors: list[str] = []
        self._full_text: list[str] = []

    def build_command(self, exe: str, task: str) -> list[str]:
        self._reset_turn()
        if self.instructions and not (self.continue_session and self.session_id):
            # agy has no system-prompt flag: give the voice rules once per conversation.
            task = f"{self.instructions}\n\n---\n\n{task}"
        elif self._profile_update:
            task = f"(The user's profile was updated.)\n{self._profile_update}\n\n---\n\n{task}"
            self._profile_update = ""
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

    # -- parsing ----------------------------------------------------------------------------
    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        event = obj.get("event")
        if event == "init":
            self.session_id = obj.get("conversation_id") or self.session_id
        elif event == "step_update":
            yield from self._parse_step(obj.get("step_update", {}))
        elif event == "result":
            yield from self._flush_text()
            yield self._result(obj.get("result", {}))
        elif "raw" in obj:
            yield AgentEvent("text", text=obj["raw"])

    def _parse_step(self, step: dict[str, Any]) -> Iterable[AgentEvent]:
        step_type = step.get("step_type", "")
        state = step.get("state", "")
        index = step.get("step_index", -1)

        if step_type == "agent_response":
            delta = step.get("text_delta", "")
            self._text_buffer += delta
            self._full_text.append(delta)
            # Emit whole lines so the activity feed isn't flooded with tokens.
            while "\n" in self._text_buffer:
                line, self._text_buffer = self._text_buffer.split("\n", 1)
                if line.strip():
                    yield AgentEvent("text", text=line)
            if state == "DONE":
                yield from self._flush_text()
            return

        if step_type == "tool":
            info = step.get("tool_info") or {}
            tool = step.get("tool_name") or info.get("name") or "tool"
            if index not in self._announced_steps:
                self._announced_steps.add(index)
                yield AgentEvent("tool", tool=tool, text=_describe(info.get("parameters") or {}))
            if state == "ERROR":
                message = " ".join(str((info.get("error") or {}).get("message", "failed")).split())
                self._errors.append(f"{tool}: {message}")
                blocked = "permission" in message.lower()
                yield AgentEvent("tool", tool="blocked" if blocked else "error", text=message)
            return

        if step_type not in ("user_input", ""):
            # system_message and other bookkeeping steps: surface any text they carry.
            text = next((step[k] for k in _TEXT_KEYS if isinstance(step.get(k), str) and step[k].strip()), "")
            if text and index not in self._announced_steps:
                self._announced_steps.add(index)
                yield AgentEvent("text", text=text)

    def _result(self, result: dict[str, Any]) -> AgentEvent:
        self.session_id = result.get("conversation_id") or self.session_id
        response = (result.get("response") or "").strip() or "".join(self._full_text).strip()
        denied = [d.get("display_name") or d.get("action", "?") for d in result.get("denied_actions") or []]
        status = result.get("status")
        failed = status != "SUCCESS"

        if denied and not response:
            failed = True
            response = (
                f"Antigravity was blocked: it needed permission for {', '.join(sorted(set(denied)))}, "
                "which it can't ask for when run by Nova. Say use Claude for tasks that run commands, "
                f"or allow it in {AGY_SETTINGS} or with skip_permissions in config.toml."
            )
        elif denied:
            response += f" (Some actions were blocked: {', '.join(sorted(set(denied)))}.)"
        elif not response and self._errors:
            failed = True
            response = f"Antigravity stopped after an error: {self._errors[-1]}"
        return AgentEvent("result", text=response,
                          data={"is_error": failed, "status": status, "denied": denied, "errors": list(self._errors)})

    def _flush_text(self) -> Iterable[AgentEvent]:
        if self._text_buffer.strip():
            yield AgentEvent("text", text=self._text_buffer)
        self._text_buffer = ""


def _describe(parameters: dict[str, Any]) -> str:
    """The tool's target in full; display code shortens it, this keeps the record complete."""
    for key in _PARAM_KEYS:
        if parameters.get(key):
            return " ".join(str(parameters[key]).split())
    first = next((v for v in parameters.values() if isinstance(v, str) and v.strip()), "")
    return " ".join(first.split())
