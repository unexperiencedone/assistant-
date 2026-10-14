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
        # Watches the bus and keeps the live answer to "what is running" and "what ran
        # last". Not a store: every command is already written to data/sessions by
        # TurnLog. This is the part that was missing, which is the live view.
        from .watcher import Watcher

        self.watcher = Watcher(self.bus)
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
                                      submit=lambda text, source, brain="": self.submit(text, source, brain=brain),
                                      busy=lambda: self.runner.running or not self.inbox.empty())

        # What today is (weather, markets, news, holidays, calendars) and what's on it.
        # Both bring things up unasked: the greeting, the reminders, the evening check-in.
        def data_path(value: str) -> Path:
            path = Path(os.path.expandvars(value)).expanduser()
            return path if path.is_absolute() else settings.base_dir / path

        self.today = None
        if settings.today.enabled:
            from .today import TodayService

            self.today = TodayService(settings.today, data_path(settings.today.cache_path),
                                      data_path(settings.today.state_path), self.bus,
                                      briefing_path=data_path(settings.ambient.cache_path))
        self.schedule = None
        if settings.schedule.enabled:
            from .schedule import ScheduleService

            self.schedule = ScheduleService(
                data_path(settings.schedule.db_path), self.bus, settings.schedule,
                announce=self._remind,
                offer=lambda question, request, answered: self.controller.offer(question, request, answered),
                calendar=self.today.personal_events if self.today else None,
                busy=lambda: self.runner.running or not self.inbox.empty() or self.controller.talk is not None)
            if self.today:
                self.today.agenda = self.schedule.lines

        # AfterThought: remembers what you work on (in the apps you opt in) and brings a past
        # memory up when what's in front of you connects to it. On-device; docs/afterthought.md.
        self.afterthought = None
        if settings.afterthought.enabled:
            from .afterthought import AfterThoughtService

            self.afterthought = AfterThoughtService(
                data_path(settings.afterthought.db_path), self.bus, settings.afterthought,
                notify=self._tray_note,
                offer=lambda question, answered: self.controller.offer(question, "", answered),
                can_speak=self._free_to_speak,
                known=lambda: [w for w in self.profile.hotwords().split(", ") if w])

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
            # Unposted projects -> LinkedIn drafts. Hung off the publisher so the voice
            # handlers and tools reach it the same way they reach the gate.
            if settings.showcase.enabled:
                from .publish.showcase import Showcase, free_writer

                self.publisher.showcase = Showcase(settings.showcase, self.publisher, settings.base_dir,
                                                   self.bus, ask=free_writer(self.registry),
                                                   notify=self._tray_note)

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

        # The Chrome extension connects to the server above; this says whether it may drive.
        from .automation.extension import BRIDGE

        BRIDGE.driver = settings.browser.driver
        BRIDGE.on_call = lambda line, ok: self.bus.log(f"Chrome extension: {line}", "info" if ok else "warn")

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
            # Jobs handed to other apps (the Antigravity IDE, the Claude app), watched in the
            # background so they never hold anything up (assistant/automation/watch.py).
            from .automation.watch import WatchService

            self.watches = WatchService(say=lambda text: self.controller.say(text), live=self.watcher)
            self.automations = automations = AutomationService(
                folder if folder.is_absolute() else settings.base_dir / folder, self.bus,
                open_app=lambda name: bool(self.local_system and self.local_system.open(name, kind="app").ok),
                phone=self.phone,
                say=lambda text: self.controller.say(text),
                watches=self.watches,
            )

        # Counts which route answered each request, so the claim that Nova gets cheaper
        # over time is a measurement rather than an intention (assistant/routes.py).
        from .routes import RouteCounter

        self.routes = RouteCounter(settings.base_dir / "data" / "routes.json", self.bus)

        # The order to try things in, per kind of request, and what has worked (ladders.toml).
        self.ladders = None
        if settings.ladders.enabled:
            from .ladders import LadderService
            from .paths import cli_command, main_command

            def from_base(value: str) -> Path:
                path = Path(os.path.expandvars(value)).expanduser()
                return path if path.is_absolute() else settings.base_dir / path

            self.ladders = LadderService(from_base(settings.ladders.file), from_base(settings.ladders.memory),
                                         self.bus, main=main_command(), cli=cli_command())

        # What every brain spent and what the cheaper routes saved (assistant/usage).
        self.usage = None
        if settings.usage.enabled:
            from .usage import UsageService

            def under_base(value: str) -> Path:
                path = Path(os.path.expandvars(value)).expanduser()
                return path if path.is_absolute() else settings.base_dir / path

            self.usage = UsageService(under_base(settings.usage.db_path), self.bus,
                                      under_base(settings.sessions.folder),
                                      settings.base_dir / "data" / "routes.json", automations)
            if self.server:
                self.server.usage = self.usage

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
            delegate=self._delegate_to_claude, delegate_agy=self._delegate_to_agy,
            workspace=settings.workspace,
            runner=self.runner, capture=self.capture, publisher=self.publisher, goals=self.goals,
            schedule=self.schedule, afterthought=self.afterthought,
            watcher=self.watcher,
            skills=installed_skills, skill_limit=settings.skills.read_limit,
            capture_settings=settings.capture, orchestrator=self.orchestrator, plan=self.plan,
            profile=self.profile, assistant_name=settings.assistant.name,
            browser=settings.browser.enabled,
        ))

        # Whose voice: only yours, once it has learned it (assistant/audio/voiceid.py).
        # Building this loads nothing; the model is only opened for a voiceprint.
        self.voice_id = None
        if settings.voice_id.enabled:
            from .audio.voiceid import VoiceID

            def from_data(value: str) -> Path:
                path = Path(os.path.expandvars(value)).expanduser()
                return path if path.is_absolute() else settings.base_dir / path

            self.voice_id = VoiceID(from_data(settings.voice_id.model), from_data(settings.voice_id.profile),
                                    from_data(settings.voice_id.log), threshold=settings.voice_id.threshold,
                                    enforce=settings.voice_id.enforce)

        self.controller = Controller(
            settings, self.bus, self.speaker, self.registry, self.plan, self.runner,
            request_quit=self.quit, show_canvas=self.show_canvas, local=local_commands, automations=automations,
            profile=self.profile, history=self.history.store if self.history else None,
            promotion_store=self.promotion,
            phone=self.phone,
            goals=self.goals,
            recipes=self.recipes,
            ladders=self.ladders,
            voice_id=self.voice_id,
            orchestrator=self.orchestrator,
            watcher=self.watcher,
            capture=self.capture,
            publisher=self.publisher,
            capture_settings=settings.capture,
        )
        if self.voice_id is not None:
            self.voice_id.on_enrol_progress = self.controller.voice_progress
            self.voice_id.on_enrolled = self.controller.say
        self.profile.on_change(self._apply_profile)
        self.profile.announce()  # agents, speech words and the canvas get the saved profile
        self.controller.restore_queue(self._saved_queue)
        self.controller.today, self.controller.schedule = self.today, self.schedule
        self.controller.afterthought = self.afterthought
        if self.server is not None:
            self.server.afterthought = self.afterthought
        # Needs you: one list of every decision waiting on the owner (assistant/inbox.py).
        from .inbox import InboxService
        from .intents import match_intent as _match_intent

        # Not self.inbox: that name is the request queue every input goes through.
        self.needs_you = InboxService(self.bus, publisher=self.publisher,
                                  automations=getattr(self.automations, "folder", None) if self.automations else None,
                                  matches_intent=_match_intent)
        if self.server:
            self.server.inbox = self.needs_you
            self.server.publisher = self.publisher
            self.server.schedule = self.schedule
            self.server.overlay = self
            from .connections import ConnectionsService
            from .paths import APP_DIR

            # Keys go where load_settings reads them from: next to config.toml.
            env_dir = getattr(settings, "base_dir", None) or APP_DIR
            self.server.connections = ConnectionsService(settings, Path(env_dir) / ".env")
        self._greeting = threading.Lock()
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

    def _delegate(self, backend_name: str, task: str, missing: str) -> str:
        """Run a task in one of the CLI agents and return its reply.

        Shared by both delegates, because the only real difference between them is
        which brain is better at the job: Antigravity for reading around a subject and
        pulling content off the web, Claude Code for writing code and automations.
        Splitting the two is what stops every errand costing a Claude turn.
        """
        backend = self.registry.backends.get(backend_name)
        if backend is None or not backend.is_available():
            return missing

        # A session already in a turn gets no second message: the two would share one
        # reply stream, and one job would wait forever for an answer the other took.
        spawned = getattr(backend, "busy", False)
        if spawned:
            backend = backend.spawn()

        def on_event(event) -> None:
            self.bus.publish("agent", kind=event.kind, backend=backend.label,
                             tool=event.tool, text=event.text[:400])

        try:
            result = backend.run(task, on_event, self.runner.cancel_event)
        finally:
            if spawned:
                backend.close()   # a one-job session: don't leave its process running
        # Not through the runner, so no result event: say what it cost, or the usage
        # ledger never sees a Claude turn a free model handed over (assistant/usage).
        self.bus.publish("usage", backend=backend.label, data=result.data, request=task,
                         ok=result.ok, text=result.summary or "", seconds=result.seconds)
        if result.cancelled:
            return "Cancelled."
        return result.summary or (f"{backend.label} finished." if result.ok
                                  else f"{backend.label} hit a problem.")

    def _delegate_to_claude(self, task: str) -> str:
        return self._delegate("claude", task, "Claude Code isn't installed on this machine.")

    def _delegate_to_agy(self, task: str) -> str:
        return self._delegate("antigravity", task, "Antigravity isn't installed on this machine.")

    # -- input ------------------------------------------------------------------
    # -- Nova over another app (assistant/screen_context.py) ---------------------------
    def open_overlay(self, context: dict) -> None:
        """Called by the shell's hotkey with what was on screen, before Nova shows."""
        self.overlay_context = context
        shown = {k: context.get(k) for k in ("process", "title", "rects", "focus_rect", "monitor", "private")}
        shown["has_selection"] = bool(context.get("selection"))
        shown["selection"] = (context.get("selection") or "")[:400]
        self.bus.publish("overlay", open=True, rev=int(time.time() * 1000), context=shown,
                         question="", answer="", choices=[])

    def overlay_ask(self, text: str) -> None:
        from .screen_context import describe

        text = (text or "").strip()
        if not text:
            return
        context = getattr(self, "overlay_context", None) or {}
        self.controller.screen_context = (text, describe(context)) if context else None
        self.bus.publish("overlay", question=text, answer="", choices=[])
        self.submit(text, "overlay")

    def close_overlay(self) -> None:
        self.bus.publish("overlay", open=False)
        shell = getattr(self, "shell", None)
        if shell is not None:
            shell.hide_overlay()

    def submit(self, text: str, source: str, origin: str = "desktop", brain: str = "") -> None:
        """`source` is how it arrived (voice, keyboard, canvas); `origin` is which
        machine it came from, so the answer knows where to go back to. `brain` pins
        the agent that takes it, for a standing goal only one of them can do."""
        text = text.strip()
        if not text:
            return
        if self.controller.try_urgent(text, source, origin):
            return
        self.inbox.put((source, text, origin, brain))

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
                source, payload, origin, *pinned = self.inbox.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if source == "agent_done":
                    self.controller.on_agent_finished(*payload)
                else:
                    self.controller.handle(payload, source, origin, brain=pinned[0] if pinned else "")
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
            if self.schedule:
                self.schedule.stop()
            if self.afterthought:
                self.afterthought.stop()
            if self.today:
                self.today.stop()
            if self.capture and self.capture.running:
                self.capture.stop()  # a half-written recording is worse than a short one
            if getattr(self, "needs_you", None):
                self.needs_you.stop()
            if self.publisher:
                if self.publisher.showcase is not None:
                    self.publisher.showcase.stop()
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
            twin = backend.spawn()
            twin.key_lane = "background"   # narration never spends the conversation's key
            result = twin.run(prompt, lambda event: None, threading.Event())
            if result.ok and result.summary.strip():
                return result.summary.strip()
        return ""

    def _apply_profile(self, profile) -> None:
        self.registry.apply_profile(profile, self.journal.context() if self.journal else "")
        if self.transcriber is not None and hasattr(self.transcriber, "hotwords"):
            self.transcriber.hotwords = profile.hotwords(self.settings.assistant.wake_word) or None

    def _greet(self, reason: str = "start") -> None:
        """Say hello the way a person would: today's weather, news, markets and plans,
        written fresh by a free model every time (assistant/today/greeting.py).

        Runs on its own thread: fetching and wording take a second or two, and the
        window and microphone must not wait for them."""
        self.bus.log(f"Ready in {time.perf_counter() - STARTED_AT:.1f}s after launch.")
        threading.Thread(target=self._say_greeting, args=(reason,), name="greeting", daemon=True).start()

    def on_window_shown(self) -> None:
        """The window came up from the tray: greet again if it has been a while."""
        if self.today and self.settings.today.greet:
            last = float(self.today.state().get("greeted_at") or 0)
            if time.time() - last >= self.settings.today.greet_again_hours * 3600:
                self._greet("shown")

    def _say_greeting(self, reason: str) -> None:
        if not self._greeting.acquire(blocking=False):
            return   # one greeting at a time: startup and a window restore can race
        try:
            text, mail = self._compose_greeting()
            waiting = len(self.controller.waiting)
            if waiting:
                text += (f" {waiting} request{'s were' if waiting != 1 else ' was'} still waiting from last time: "
                         "say resume to start, or clear the queue.")
            self.controller.say(text)
            if mail:
                self._mail_digest()
        except Exception as error:   # a greeting must never stop Nova starting
            self.bus.log(f"greeting failed: {error}", "warn")
            name = f" {self.profile.preferred_name}" if self.profile.preferred_name else ""
            self.controller.say(f"Hi{name}. What do you need?")
        finally:
            self._greeting.release()

    def _compose_greeting(self) -> tuple[str, bool]:
        """(words to say, whether the day's mail check follows)."""
        user = self.profile.preferred_name or ""
        if not (self.today and self.settings.today.greet):
            wake = self.settings.assistant.wake_word
            return (f"Hi{' ' + user if user else ''}, I'm {self.settings.assistant.name}. Tell me what to do."
                    + (f" Say {wake} to get my attention." if wake else "")), False
        from datetime import datetime, timedelta

        from .today import greeting

        self.today.refresh_if_stale(10 * 60)
        state = self.today.state()
        now = datetime.now()
        today = now.date().isoformat()
        claude = self.registry.backends.get("claude")
        mail = bool(self.settings.today.mail_on_first_open and state.get("mail_day") != today
                    and claude is not None and claude.is_available())
        schedule = self.schedule.lines(now, datetime(now.year, now.month, now.day) + timedelta(days=1), 5) \
            if self.schedule else []
        facts = self.today.facts()
        recent = list(state.get("recent", []))
        text = self._compose(greeting.instructions(user, recent, mail), greeting.facts_prompt(facts, schedule))
        if not text:
            text = greeting.fallback(facts, user, schedule, mail)
        self.today.remember(greeted_at=time.time(), recent=(recent + [text])[-6:],
                            **({"mail_day": today} if mail else {}))
        return text, mail

    def _compose(self, instructions: str, prompt: str) -> str:
        """One tool-free turn on a free model, on the background key. "" if none answers."""
        for name in ("groq", "openrouter"):
            backend = self.registry.backends.get(name)
            if backend is None or not backend.is_available() or not hasattr(backend, "talk"):
                continue
            twin = backend.spawn()
            twin.key_lane = "background"
            twin.talk = instructions
            result = twin.run(prompt, lambda event: None, threading.Event())
            if result.ok and result.summary.strip():
                return result.summary.strip()
        return ""

    def _mail_digest(self) -> None:
        """The day's first look at the mail, on Claude (it holds the Gmail connector).
        The hourly mail goal then carries on from here instead of re-reading the same mail."""
        state = self.today.state()
        since = int(state.get("mail_digest_at") or time.time() - 86400)
        self.today.remember(mail_digest_at=time.time())
        if self.goals:
            self.goals.defer_matching("Gmail")
        self.submit(
            f"Go through my Gmail for everything that arrived since {since} (search: after:{since}). "
            "Tell me the important ones in two to four spoken sentences: who it's from and what it needs, "
            "most urgent first. Skip newsletters, promotions and automated noise. If nothing important came "
            "in, say so in one relaxed sentence. Only read: don't reply, send, archive, label or delete anything.",
            "goal", brain="claude")

    def _tray_note(self, text: str) -> None:
        tray = getattr(getattr(self, "shell", None), "tray", None)
        if tray is not None:
            try:
                tray.notify(text, self.settings.assistant.name)
            except Exception:
                pass

    def _free_to_speak(self) -> bool:
        """An unprompted memory may only be spoken into silence: not over a reply, a task,
        a conversation, or a question Nova is still waiting on."""
        c = self.controller
        return not (self.speaker.is_busy or self.runner.running or c.talk is not None
                    or c._pending_offer is not None or not self.inbox.empty())

    def _remind(self, text: str) -> None:
        """A reminder goes everywhere at once: out loud, the tray, the phone."""
        self.controller.announce(text)
        tray = getattr(getattr(self, "shell", None), "tray", None)
        if tray is not None:
            try:
                tray.notify(text, self.settings.assistant.name)
            except Exception:
                pass
        if self.settings.schedule.phone and self.phone is not None and self.phone.configured:
            threading.Thread(target=self._remind_phone, args=(text,), daemon=True).start()

    def _remind_phone(self, text: str) -> None:
        try:
            self.phone.call("notify", {"title": self.settings.assistant.name, "text": text})
        except Exception as error:
            self.bus.log(f"reminder didn't reach the phone: {error}", "warn")

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
        if self.today:
            self.today.start()
        self.needs_you.start()
        if self.publisher is not None:
            self.publisher.notify = self._tray_note
            self.publisher.start()          # sends scheduled posts when their time comes
            if self.publisher.showcase is not None:
                self.publisher.showcase.start()
        if self.schedule:
            self.schedule.start()
        if self.afterthought:
            self.afterthought.start()
        if self.usage:
            self.usage.start()
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
                                      on_utterance=lambda t: self.submit(t, "voice"), voice=self.voice_id)
        self.mic.start()
        self._greet()

    def _report_environment(self) -> None:
        available = ", ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in self.registry.availability().items())
        mode = self.settings.agents.claude.permission_mode
        self.bus.log(f"Agent CLIs: {available}. Using {self.registry.current.label} (Claude permission mode: {mode}).")
        self.bus.log(f"Agents work in {self.settings.workspace}")
