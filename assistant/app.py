"""Wires the modules together and runs the app.

Inputs (microphone, keyboard, canvas) and agent-finished notifications all feed
a single queue, handled one at a time on a worker thread. The main thread is
left free for the native window and tray icon (window/tray modes), which on
Windows must own the main thread.
"""

from __future__ import annotations

if __name__ == "__main__" and not __package__:
    # Started as a script (`python app.py`): relative imports need the package, so hand off to main.py.
    import runpy
    import sys
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parent.parent / "main.py"), run_name="__main__")
    sys.exit()

import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from rich.console import Console

from .agent_runner import AgentRunner
from .agents import AgentRegistry
from .audio.tts import make_speaker
from .config import Settings
from .controller import Controller
from .events import EventBus
from .planning import Plan
from .planning.graph import GraphTracker
from .state import AppState

STARTED_AT = time.perf_counter()


class VoiceAssistant:
    def __init__(self, settings: Settings, text_mode: bool = False) -> None:
        self.settings = settings
        self.text_mode = text_mode
        self.mode = settings.ui.mode
        # Plain text when writing to a log file (windowed .exe / autostart), even if FORCE_COLOR is set.
        interactive = sys.stdout is not None and sys.stdout.isatty()
        self.console = Console() if interactive else Console(force_terminal=False, no_color=True, width=120)
        self.inbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._quit = threading.Event()
        self.shell = None  # native window + tray, in window/tray modes
        self.mic = None

        self.bus = EventBus()
        self.registry = AgentRegistry(settings)
        self.state = AppState(self.bus, settings.assistant.name, self.registry.current.label, str(settings.workspace))
        self.plan = Plan()
        GraphTracker(self.bus, self.plan)  # publishes the live "graph" topic
        self.speaker = make_speaker(settings.voice, self.bus)
        self.runner = AgentRunner(self.bus, self.plan, self.speaker,
                                  on_finished=lambda backend, result: self.inbox.put(("agent_done", (backend, result))))

        self.server = None
        if settings.ui.web_dashboard or self.mode != "browser":
            from .ui.server import DashboardServer

            self.server = DashboardServer(self.state, settings.ui.web_port,
                                          on_text=lambda t: self.submit(t, "canvas"), on_show=self.show_canvas)

        self.local_system = None
        local_commands = None
        if settings.local.enabled:
            from .system import LocalSystem
            from .system.voice import LocalVoiceCommands

            self.local_system = LocalSystem(settings.local, settings.base_dir,
                                            log=lambda text, level="info": self.bus.log(text, level))
            local_commands = LocalVoiceCommands(self.local_system, self.bus, say=lambda text: self.controller.say(text))

        automations = None
        if settings.automations.enabled:
            from .automation.service import AutomationService

            folder = Path(os.path.expandvars(settings.automations.folder)).expanduser()
            automations = AutomationService(
                folder if folder.is_absolute() else settings.base_dir / folder, self.bus,
                open_app=lambda name: bool(self.local_system and self.local_system.open(name, kind="app").ok),
                say=lambda text: self.controller.say(text),
            )

        self.controller = Controller(
            settings, self.bus, self.speaker, self.registry, self.plan, self.runner,
            request_quit=self.quit, show_canvas=self.show_canvas, local=local_commands, automations=automations,
        )

    # -- input ------------------------------------------------------------------
    def submit(self, text: str, source: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.controller.try_urgent(text):
            return
        self.inbox.put((source, text))

    def _keyboard_loop(self) -> None:
        while not self._quit.is_set():
            try:
                line = self.console.input("[bold green]you>[/] ")
            except (EOFError, KeyboardInterrupt):
                self.quit()
                return
            self.submit(line, "keyboard")

    def _work_loop(self) -> None:
        while not self._quit.is_set():
            try:
                source, payload = self.inbox.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if source == "agent_done":
                    self.controller.on_agent_finished(*payload)
                else:
                    self.controller.handle(payload, source)
            except Exception as exc:  # one bad request must not stop the assistant
                self.bus.log(f"Error handling '{payload}': {exc}", "error")
            if self.text_mode:
                self.bus.status("idle")

    # -- actions used by the UI shell and voice commands ---------------------------
    def show_canvas(self) -> bool:
        if self.shell:
            self.shell.show()
            return True
        if self.server:
            webbrowser.open(self.server.url)
            return True
        return False

    def toggle_mute(self) -> bool:
        """Returns True when the microphone is now muted."""
        if not self.mic:
            return False
        if self.mic.muted.is_set():
            self.mic.muted.clear()
            self.bus.log("Microphone on.")
        else:
            self.mic.muted.set()
            self.bus.status("idle")
            self.bus.log("Microphone muted.")
        return self.mic.muted.is_set()

    def cancel_task(self) -> None:
        self.controller.handle("cancel the task", "canvas")

    def quit(self) -> None:
        self._quit.set()
        if self.shell:
            self.shell.stop()

    # -- lifecycle --------------------------------------------------------------
    def run(self) -> None:
        dashboard = self._start_outputs()
        self._report_environment()
        if self.local_system:
            self.local_system.start_background_indexing()

        threading.Thread(target=self._work_loop, name="controller", daemon=True).start()
        # Launch the agent session now so the first request doesn't wait for start-up.
        threading.Thread(target=self.registry.current.start, name="agent-start", daemon=True).start()
        if self.text_mode and sys.stdin is not None:
            threading.Thread(target=self._keyboard_loop, name="keyboard", daemon=True).start()
        if self.text_mode or not self.settings.speech.enabled:
            self._greet()
        else:
            # Loading Whisper takes seconds: do it off the main thread so the UI appears at once.
            threading.Thread(target=self._start_microphone, name="mic-start", daemon=True).start()

        try:
            if self.mode in ("window", "tray") and self.server:
                from .ui.shell import AppShell

                self.shell = AppShell(self, self.server.url, start_hidden=self.mode == "tray")
                self.shell.run()  # blocks on the main thread until quit
                self._quit.set()
            else:
                while not self._quit.wait(0.25):
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            self.runner.cancel()
            self.registry.close()
            if dashboard:
                dashboard.stop()
            if self.server:
                self.server.stop()

    def _greet(self) -> None:
        name = self.settings.assistant.name
        wake = self.settings.assistant.wake_word
        hint = f" Say {wake} to get my attention." if wake else ""
        self.bus.log(f"Ready in {time.perf_counter() - STARTED_AT:.1f}s after launch.")
        self.controller.say(f"Hi, I'm {name}. Tell me what to do.{hint}")

    def _start_outputs(self):
        dashboard = None
        if self.text_mode or self.mode != "browser" or not self.settings.ui.terminal_dashboard:
            from .ui.console import LinePrinter

            LinePrinter(self.bus, self.settings.assistant.name, self.console)
        else:
            from .ui.console import TerminalDashboard

            dashboard = TerminalDashboard(self.state, self.console)
            dashboard.start()

        if self.server:
            try:
                self.server.start()
                self.bus.log(f"Canvas: {self.server.url}")
                if self.mode == "browser" and self.settings.ui.open_browser:
                    webbrowser.open(self.server.url)
            except OSError as exc:
                self.bus.log(f"Canvas server failed to start on port {self.settings.ui.web_port}: {exc}", "warn")
                self.server = None
        return dashboard

    def _start_microphone(self) -> None:
        from .audio.microphone import MicrophoneListener
        from .audio.transcriber import make_transcriber

        transcriber = make_transcriber(self.settings.speech, self.bus)
        self.mic = MicrophoneListener(self.settings.speech, transcriber, self.speaker, self.bus,
                                      on_utterance=lambda t: self.submit(t, "voice"))
        self.mic.start()
        self._greet()

    def _report_environment(self) -> None:
        available = ", ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in self.registry.availability().items())
        mode = self.settings.agents.claude.permission_mode
        self.bus.log(f"Agent CLIs: {available}. Using {self.registry.current.label} (Claude permission mode: {mode}).")
        self.bus.log(f"Agents work in {self.settings.workspace}")
