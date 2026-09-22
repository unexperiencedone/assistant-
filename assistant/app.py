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
from . import logbridge
from . import store
from .planning import Plan
from .planning.plan import PlanStep
from .planning.graph import GraphTracker
from .sessions import SessionRecorder
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
        self._saved_queue: list[str] = []
        self.shell = None  # native window + tray, in window/tray modes
        self.mic = None

        # Before anything else: AgentRegistry builds its system prompt in its own
        # constructor, so the character must already know its name and its maker by then.
        from . import persona

        persona.configure(settings.assistant.name, getattr(settings.assistant, "maker", ""))

        self.bus = EventBus()
        logbridge.install(self.bus)  # module warnings (UI automation, browser) reach the activity feed
        self.registry = AgentRegistry(settings)
        self.state = AppState(self.bus, settings.assistant.name, self.registry.current.label, str(settings.workspace))
        from .profile import ProfileService

        self.profile = ProfileService(settings.base_dir / "data" / "profile.json", self.bus)
        self.transcriber = None
        self.history = None
        if settings.history.enabled:
            from .history import HistoryRecorder, HistoryStore

            db = Path(os.path.expandvars(settings.history.db_path)).expanduser()
            store = HistoryStore(db if db.is_absolute() else settings.base_dir / db)
            store.prune(settings.history.keep_days)
            self.history = HistoryRecorder(store, self.bus, settings.history.idle_minutes * 60,
                                           projects_in=self.profile.projects_in)
        self.plan = Plan()
        self.graph = GraphTracker(self.bus, self.plan)  # publishes the live "graph" topic
        self._restore_previous_session(settings)
        self.recorder = SessionRecorder(
            Path(settings.sessions.folder) if Path(settings.sessions.folder).is_absolute()
            else settings.base_dir / settings.sessions.folder,
            settings.sessions.enabled, settings.sessions.keep_days)
        for backend in self.registry.backends.values():
            backend.recorder = self.recorder
        self.speaker = make_speaker(settings.voice, self.bus)
        self.runner = AgentRunner(
            self.bus, self.plan, self.speaker,
            on_finished=lambda backend, result, task: self.inbox.put(("agent_done", (backend, result, task), "")),
            max_parallel=settings.agents.max_parallel)

        # The briefing panel's cache, and the activity record. Both are opt-in-ish:
        # the briefing never fetches until asked, and awareness never watches until
        # told to (config, or the button on the Activity screen).
        self.ambient = None
        if settings.ambient.enabled:
            from .ambient import AmbientService

            cache = Path(os.path.expandvars(settings.ambient.cache_path)).expanduser()
            self.ambient = AmbientService(cache if cache.is_absolute() else settings.base_dir / cache,
                                          self.bus, settings.ambient,
                                          on_request=lambda text: self.submit(text, "canvas"))
        self.awareness = None
        if settings.awareness.enabled:
            from .awareness import AwarenessService

            db = Path(os.path.expandvars(settings.awareness.db_path)).expanduser()
            self.awareness = AwarenessService(db if db.is_absolute() else settings.base_dir / db,
                                              self.bus, settings.awareness, narrate=self._narrate)

        # Nova's own record of its days. It reads the two stores above and writes one
        # entry per finished day; those entries go back into the agents' instructions,
        # which is where continuity between sessions actually comes from.
        self.journal = None
        if settings.journal.enabled:
            from .journal import JournalService

            db = Path(os.path.expandvars(settings.journal.db_path)).expanduser()
            self.journal = JournalService(db if db.is_absolute() else settings.base_dir / db,
                                          self.bus, settings.journal,
                                          history=self.history.store if self.history else None,
                                          awareness=self.awareness, narrate=self._narrate)
            # A new entry only lands once a day, so refreshing the instructions then is free.
            self.bus.subscribe("journal", lambda event: self._apply_profile(self.profile))

        # The one place Nova starts something itself. A due goal is submitted exactly as
        # if you had said it, so nothing here routes around a confirmation.
        self.goals = None
        if settings.goals.enabled:
            from .goals import GoalsService

            db = Path(os.path.expandvars(settings.goals.db_path)).expanduser()
            self.goals = GoalsService(db if db.is_absolute() else settings.base_dir / db,
                                      self.bus, settings.goals,
                                      submit=lambda text, source: self.submit(text, source),
                                      busy=lambda: self.runner.running or not self.inbox.empty())

        # Capture and publishing: the hands. The recorder writes into the inbox folder;
        # the publish service stages drafts and sends none of them without your word.
        self.capture = None
        if settings.capture.enabled:
            from .capture import ScreenRecorder

            folder = Path(os.path.expandvars(settings.capture.inbox)).expanduser()
            self.capture = ScreenRecorder(folder if folder.is_absolute() else settings.base_dir / folder)
        self.publisher = None
        if settings.publish.enabled:
            from .publish import PublishService

            db = Path(os.path.expandvars(settings.publish.db_path)).expanduser()
            self.publisher = PublishService(db if db.is_absolute() else settings.base_dir / db,
                                            self.bus, workspace=settings.workspace)

        self.server = None
        if settings.ui.web_dashboard or self.mode != "browser":
            from .ui import auth
            from .ui.server import DashboardServer

            # The shared secret exists before the server does: there is never a moment
            # when the canvas answers a request without checking it.
            token = ""
            if settings.ui.require_token:
                token_path = Path(os.path.expandvars(settings.ui.token_file)).expanduser()
                token = auth.load_or_create(
                    token_path if token_path.is_absolute() else settings.base_dir / token_path)

            self.server = DashboardServer(self.state, settings.ui.web_port,
                                          on_text=lambda text, origin="desktop": self.submit(text, "canvas", origin),
                                          on_show=self.show_canvas,
                                          graph_tracker=self.graph, profile=self.profile, history=self.history,
                                          ambient=self.ambient, awareness=self.awareness,
                                          host=settings.ui.host, token=token)

        # The phone, when it is set up: a client for the Termux bridge over Tailscale.
        from .phone import PhoneBridge

        self.phone = PhoneBridge(settings.phone)

        self.local_system = None
        local_commands = None
        if settings.local.enabled:
            from .system import LocalSystem
            from .system.voice import LocalVoiceCommands

            self.local_system = LocalSystem(settings.local, settings.base_dir,
                                            log=lambda text, level="info": self.bus.log(text, level))
            local_commands = LocalVoiceCommands(self.local_system, self.bus, say=lambda text: self.controller.say(text),
                                                profile=self.profile)

        self.automations = automations = None
        if settings.automations.enabled:
            from .automation.service import AutomationService

            folder = Path(os.path.expandvars(settings.automations.folder)).expanduser()
            self.automations = automations = AutomationService(
                folder if folder.is_absolute() else settings.base_dir / folder, self.bus,
                open_app=lambda name: bool(self.local_system and self.local_system.open(name, kind="app").ok),
                phone=self.phone,
                say=lambda text: self.controller.say(text),
            )

        # Counts which route answered each request, so the claim that Nova gets cheaper
        # over time is a measurement rather than an intention (assistant/routes.py).
        from .routes import RouteCounter

        self.routes = RouteCounter(settings.base_dir / "data" / "routes.json", self.bus)

        self.promotion = None
        if settings.promotion.enabled:
            from .automation.promotion import PromotionStore

            store_path = Path(os.path.expandvars(settings.promotion.store_path)).expanduser()
            self.promotion = PromotionStore(
                store_path if store_path.is_absolute() else settings.base_dir / store_path,
                auto_after=settings.promotion.auto_after, ask_min_turns=settings.promotion.ask_min_turns,
                ask_min_cost_usd=settings.promotion.ask_min_cost_usd,
                ask_min_seconds=settings.promotion.ask_min_seconds)

        # Model-based agents (Groq, OpenRouter) act through Nova's own abilities.
        from .agents.tools import ToolContext

        # How past jobs went, fed back on the next similar request (docs/recipes.md).
        self.recipes = None
        if settings.recipes.enabled:
            from .recipes import RecipeService

            db = Path(os.path.expandvars(settings.recipes.db_path)).expanduser()
            self.recipes = RecipeService(db if db.is_absolute() else settings.base_dir / db,
                                         self.bus, settings.recipes)

        # Several pieces of work for one request, answered once (assistant/orchestrate).
        # The dispatcher is the one thing it needs from here: a spawned backend per step,
        # started through the existing task pool so each step gets its own canvas lane.
        self.orchestrator = None
        if settings.orchestrate.enabled:
            from .orchestrate import Orchestrator

            def dispatch(prompt: str, label: str) -> str | None:
                backend = self.registry.backends.get(settings.orchestrate.backend)
                if backend is None or not backend.is_available():
                    backend = self.registry.current
                return self.runner.start(backend.spawn(), prompt, label)

            narrator = None
            if settings.narrator.enabled and settings.narrator.groups:
                from .persona.narrator import Narrator

                voice = Narrator(ask=self._narrate, settings=settings.narrator,
                                 log=lambda text: self.bus.log(text))
                narrator = lambda plain: voice.say_it_better(plain, settings.narrator.max_chars)
            self.orchestrator = Orchestrator(dispatch, max_fanout=settings.orchestrate.max_fanout,
                                             narrate=narrator)

        # Skills are read from disk once: only the frontmatter, so eighteen of them cost
        # a few milliseconds. The documents themselves are loaded on demand by read_skill.
        installed_skills: dict = {}
        if settings.skills.enabled:
            from .agents import skills as skill_registry

            roots = [Path(os.path.expandvars(p)).expanduser() for p in settings.skills.paths]
            installed_skills = skill_registry.discover(
                [r if r.is_absolute() else settings.base_dir / r for r in roots])
            if installed_skills:
                self.bus.log(f"{len(installed_skills)} skills available: {skill_registry.names(installed_skills)}")

        self.registry.attach_tools(ToolContext(
            local_system=self.local_system, automations=automations,
            delegate=self._delegate_to_claude, workspace=settings.workspace,
            runner=self.runner, capture=self.capture, publisher=self.publisher, goals=self.goals,
            skills=installed_skills, skill_limit=settings.skills.read_limit,
            capture_settings=settings.capture, orchestrator=self.orchestrator,
        ))

        self.controller = Controller(
            settings, self.bus, self.speaker, self.registry, self.plan, self.runner,
            request_quit=self.quit, show_canvas=self.show_canvas, local=local_commands, automations=automations,
            profile=self.profile, history=self.history.store if self.history else None,
            promotion_store=self.promotion,
            phone=self.phone,
            goals=self.goals,
            recipes=self.recipes,
            orchestrator=self.orchestrator,
            capture=self.capture,
            publisher=self.publisher,
            capture_settings=settings.capture,
        )
        self.profile.on_change(self._apply_profile)
        self.profile.announce()  # agents, speech words and the canvas get the saved profile
        self.controller.restore_queue(self._saved_queue)
        if self.local_system:
            self.controller.publish_quick_actions()  # the chips are there before the first request

    def _restore_previous_session(self, settings: Settings) -> None:
        """Conversation and plan survive a restart (Nova is meant to run for days)."""
        if not settings.sessions.enabled:
            return
        path = settings.base_dir / "data" / "state.json"
        saved = store.load(path)
        if saved:
            self._saved_queue = self.state.restore(saved)
            steps = saved.get("plan", {}).get("steps") or []
            if steps:
                self.plan.title = saved["plan"].get("title", "")
                self.plan.steps = [PlanStep(s["text"], s.get("status", "pending"), s.get("after", []))
                                   for s in steps]
                self.bus.publish("plan", **self.plan.to_dict())  # draw it on the canvas right away
        self.state.saver = store.Saver(path, self.state.persisted)

    def _delegate_to_claude(self, task: str) -> str:
        """Run a task in the live Claude Code session and return its reply."""
        claude = self.registry.backends["claude"]
        if not claude.is_available():
            return "Claude Code isn't installed on this machine."

        def on_event(event) -> None:
            self.bus.publish("agent", kind=event.kind, backend=claude.label, tool=event.tool, text=event.text[:400])

        result = claude.run(task, on_event, self.runner.cancel_event)
        if result.cancelled:
            return "Cancelled."
        return result.summary or ("Claude Code finished." if result.ok else "Claude Code hit a problem.")

    # -- input ------------------------------------------------------------------
    def submit(self, text: str, source: str, origin: str = "desktop") -> None:
        """`source` is how it arrived (voice, keyboard, canvas); `origin` is which
        machine it came from, so the answer knows where to go back to."""
        text = text.strip()
        if not text:
            return
        if self.controller.try_urgent(text, source, origin):
            return
        self.inbox.put((source, text, origin))

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
                source, payload, origin = self.inbox.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if source == "agent_done":
                    self.controller.on_agent_finished(*payload)
                else:
                    self.controller.handle(payload, source, origin)
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
            webbrowser.open(self.server.open_url)
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
        self.controller.handle("cancel all tasks" if len(self.runner.active) > 1 else "cancel the task", "canvas")

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

                self.shell = AppShell(self, self.server.open_url, start_hidden=self.mode == "tray")
                self.shell.run()  # blocks on the main thread until quit
                self._quit.set()
            else:
                while not self._quit.wait(0.25):
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            self.runner.cancel()
            if self.state.saver:
                self.state.saver.flush()
            if self.history:
                self.history.stop()  # write what's queued and end the session
            self.registry.close()
            if self.automations:
                self.automations.runner.close()  # disconnect from Edge (its window stays open)
            if dashboard:
                dashboard.stop()
            if self.ambient:
                self.ambient.stop()
            if self.awareness:
                self.awareness.stop()
            if self.journal:
                self.journal.stop()
            if self.goals:
                self.goals.stop()
            if self.capture and self.capture.running:
                self.capture.stop()  # a half-written recording is worse than a short one
            if self.publisher:
                self.publisher.close()
            if self.recipes:
                self.recipes.close()
            if self.server:
                self.server.stop()

    def _narrate(self, prompt: str) -> str:
        """Ask a free chat backend to say an activity summary in plain words.

        It is handed the same compact rows the canvas shows and nothing else. When no
        free backend is configured this returns "" and the factual summary stands.
        """
        for name in ("groq", "openrouter"):
            backend = self.registry.backends.get(name)
            if backend is None or not backend.is_available():
                continue
            result = backend.spawn().run(prompt, lambda event: None, threading.Event())
            if result.ok and result.summary.strip():
                return result.summary.strip()
        return ""

    def _apply_profile(self, profile) -> None:
        self.registry.apply_profile(profile, self.journal.context() if self.journal else "")
        if self.transcriber is not None and hasattr(self.transcriber, "hotwords"):
            self.transcriber.hotwords = profile.hotwords(self.settings.assistant.wake_word) or None

    def _greet(self) -> None:
        name = self.settings.assistant.name
        wake = self.settings.assistant.wake_word
        hint = f" Say {wake} to get my attention." if wake else ""
        self.bus.log(f"Ready in {time.perf_counter() - STARTED_AT:.1f}s after launch.")
        waiting = len(self.controller.waiting)
        queued = (f" {waiting} request{'s were' if waiting != 1 else ' was'} still waiting from last time: "
                  "say resume to start, or clear the queue.") if waiting else ""
        user = f" {self.profile.preferred_name}" if self.profile.preferred_name else ""
        self.controller.say(f"Hi{user}, I'm {name}. Tell me what to do.{hint}{queued}")

    def _start_outputs(self):
        dashboard = None
        if self.text_mode or self.mode != "browser" or not self.settings.ui.terminal_dashboard:
            from .ui.console import LinePrinter

            LinePrinter(self.bus, self.settings.assistant.name, self.console)
        else:
            from .ui.console import TerminalDashboard

            dashboard = TerminalDashboard(self.state, self.console)
            dashboard.start()

        if self.ambient:
            self.ambient.start()
        if self.awareness:
            self.awareness.start()
        if self.journal:
            self.journal.start()
        if self.goals:
            self.goals.start()
        if self.server:
            try:
                self.server.start()
                self.bus.log(f"Canvas: {self.server.url}")
                if self.mode == "browser" and self.settings.ui.open_browser:
                    webbrowser.open(self.server.open_url)
            except OSError as exc:
                self.bus.log(f"Canvas server failed to start on port {self.settings.ui.web_port}: {exc}", "warn")
                self.server = None
        return dashboard

    def _start_microphone(self) -> None:
        from .audio.microphone import MicrophoneListener
        from .audio.transcriber import make_transcriber

        transcriber = make_transcriber(self.settings.speech, self.bus,
                                       hotwords=self.profile.hotwords(self.settings.assistant.wake_word) or None)
        self.transcriber = transcriber
        self.mic = MicrophoneListener(self.settings.speech, transcriber, self.speaker, self.bus,
                                      on_utterance=lambda t: self.submit(t, "voice"))
        self.mic.start()
        self._greet()

    def _report_environment(self) -> None:
        available = ", ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in self.registry.availability().items())
        mode = self.settings.agents.claude.permission_mode
        self.bus.log(f"Agent CLIs: {available}. Using {self.registry.current.label} (Claude permission mode: {mode}).")
        self.bus.log(f"Agents work in {self.settings.workspace}")
