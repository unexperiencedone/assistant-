"""Claude Code backend: one live `claude -p` session that everything you say goes into.

The process runs in stream-json input mode, so each message is a new turn in
the same conversation: no start-up cost after the first turn, and Claude
remembers everything said so far. It runs on your Claude subscription through
the CLI's own login; no API key or API billing is involved.

Cancelling kills the process; the next message restarts it with `--resume`,
so the conversation carries on.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import ClaudeAgentSettings
from .base import AgentBackend, AgentEvent, AgentResult, short


class ClaudeCliAgent(AgentBackend):
    name = "claude"
    label = "Claude Code"

    def __init__(self, settings: ClaudeAgentSettings, workspace: Path, continue_session: bool, instructions: str) -> None:
        super().__init__(settings.executable, workspace, continue_session)
        self.settings = settings
        self.base_instructions = instructions
        self.instructions = instructions
        self._restart_for_instructions = False
        self._proc: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lock = threading.Lock()

    # -- process ------------------------------------------------------------------------
    def build_command(self, exe: str, task: str = "") -> list[str]:
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        if self.settings.permission_mode:
            cmd += ["--permission-mode", self.settings.permission_mode]
        if self.settings.allowed_tools:
            cmd += ["--allowedTools", *self.settings.allowed_tools]
        if self.settings.model:
            cmd += ["--model", self.settings.model]
        if self.instructions:
            cmd += ["--append-system-prompt", self.instructions]
        if self.continue_session and self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def start(self) -> bool:
        """Launch the session ahead of the first message (costs no usage until you speak)."""
        with self._lock:
            return self._ensure_process() is not None

    def spawn(self) -> "ClaudeCliAgent":
        """A second Claude Code session, for a task running alongside this one."""
        twin = ClaudeCliAgent(self.settings, self.workspace, self.continue_session, self.base_instructions)
        twin.instructions = self.instructions
        twin.recorder = self.recorder
        return twin

    def set_profile(self, standing: str) -> None:
        instructions = f"{self.base_instructions}\n\n{standing}" if standing else self.base_instructions
        if instructions != self.instructions:
            self.instructions = instructions
            # The system prompt is fixed when the process starts. Restart it before the next
            # turn (never mid-turn); --resume keeps the conversation.
            self._restart_for_instructions = self._proc is not None

    def _ensure_process(self) -> subprocess.Popen[str] | None:
        if self._proc and self._proc.poll() is None:
            return self._proc
        exe = self.resolve_executable()
        if not exe:
            return None
        self._events = queue.Queue()
        self._proc = subprocess.Popen(
            self.build_command(exe),
            cwd=self.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        threading.Thread(target=self._read, args=(self._proc, self._events), daemon=True).start()
        return self._proc

    @staticmethod
    def _read(proc: subprocess.Popen[str], events: queue.Queue[dict[str, Any]]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                events.put(json.loads(line))
            except json.JSONDecodeError:
                if line.strip():
                    events.put({"raw": line.strip()})
        events.put({"type": "_exit", "code": proc.wait()})

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                proc.kill()

    def reset_session(self) -> None:
        self.close()
        super().reset_session()

    # -- one turn -------------------------------------------------------------------------
    def run(self, prompt: str, on_event: Callable[[AgentEvent], None], cancel: threading.Event) -> AgentResult:
        # A spawned session starts with no conversation, so anything it was told to
        # carry goes in front of its first request (base.carry).
        prompt = self._with_carried(prompt)
        started = time.time()
        turn = self.start_turn_log(prompt)
        with self._lock:
            if self._restart_for_instructions and (self.continue_session or not self.session_id):
                self._restart_for_instructions = False
                self.close()
            proc = self._ensure_process()
            if proc is None:
                return AgentResult(False, f"I can't find the {self.label} command '{self.executable}'.")
            events = self._events
            try:
                assert proc.stdin is not None
                proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                self.close()
                return AgentResult(False, "The Claude Code session had stopped. Say that again and I'll restart it.")

        while True:
            if cancel.is_set():
                self.close()  # next turn resumes the same conversation
                turn.close("cancelled", False, time.time() - started)
                return AgentResult(False, "Cancelled.", self.session_id, time.time() - started, cancelled=True)
            try:
                obj = events.get(timeout=0.2)
            except queue.Empty:
                continue
            turn.raw(obj)
            if obj.get("type") == "_exit":
                self._proc = None
                detail = f"the claude process exited with code {obj.get('code')}"
                turn.close(detail, False, time.time() - started)
                return AgentResult(False, "Claude Code exited unexpectedly. Check that you're logged in with claude.",
                                   self.session_id, time.time() - started, detail=detail)
            for event in self.parse(obj):
                if event.kind == "result":
                    result = AgentResult(not event.data["is_error"], event.text, self.session_id,
                                         time.time() - started, data=event.data)
                    turn.close(result.summary, result.ok, result.seconds)
                    return result
                on_event(event)

    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self.session_id = obj.get("session_id") or self.session_id
        elif kind == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    yield AgentEvent("text", text=block["text"])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "tool")
                    yield AgentEvent("tool", tool=name, text=_describe_tool(name, block.get("input", {})))
        elif kind == "result":
            self.session_id = obj.get("session_id") or self.session_id
            denied = [d.get("tool_name", "tool") for d in obj.get("permission_denials") or []]
            for name in denied:
                yield AgentEvent("tool", tool="denied", text=f"{name} needs approval, which nobody can give in this mode")
            yield AgentEvent(
                "result",
                text=obj.get("result") or "",
                data={"is_error": bool(obj.get("is_error")), "cost_usd": obj.get("total_cost_usd"),
                      "denied": denied, "turns": obj.get("num_turns"), "subtype": obj.get("subtype")},
            )


def _describe_tool(name: str, args: dict[str, Any]) -> str:
    """The tool's target, in full. Shortening happens where things are displayed, not here:
    the whole command or path is what you want when looking back at what an agent did."""
    for key in ("file_path", "command", "pattern", "url", "query", "description", "prompt"):
        if args.get(key):
            return " ".join(str(args[key]).split())
    return ""
