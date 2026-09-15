"""Decides what to do with each thing you say.

Order of handling for every utterance:
  1. wake word / follow-up window
  2. instant local commands (stop, cancel, switch agent, open app, find file...)
     which use none of your Claude usage
  3. everything else goes straight into the live agent session, which does the
     work and replies; its reply is spoken
"""

from __future__ import annotations

import random
import re
import threading
import time
from typing import TYPE_CHECKING, Callable

from .agent_runner import AgentRunner
from .agents import AgentRegistry
from .agents.base import AgentBackend, AgentResult, short
from .audio.tts import Speaker
from .config import Settings
from .events import EventBus
from .intents import LOCAL_INTENTS, Intent, match_intent, strip_wake_word
from .planning import Plan
from .planning.plan import PLAN_BLOCK, strip_markup
from .system.voice import LocalVoiceCommands

if TYPE_CHECKING:
    from .automation.service import AutomationService

ACKS = ["On it.", "Working on it.", "Okay, give me a moment.", "Sure, one sec."]
URGENT_INTENTS = {"stop_speaking", "cancel_agent", "quit"}
MAX_SPOKEN_SENTENCES = 3
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Controller:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        speaker: Speaker,
        registry: AgentRegistry,
        plan: Plan,
        runner: AgentRunner,
        request_quit: Callable[[], None],
        show_canvas: Callable[[], bool],
        local: LocalVoiceCommands | None = None,
        automations: "AutomationService | None" = None,
    ) -> None:
        self.automations = automations
        self.settings = settings
        self.bus = bus
        self.speaker = speaker
        self.registry = registry
        self.plan = plan
        self.runner = runner
        self.request_quit = request_quit
        self.show_canvas = show_canvas
        self.local = local
        self.plan_mode = False
        self.waiting: list[str] = []  # said while the agent was busy
        self.last_addressed_at = 0.0

    # -- entry points ---------------------------------------------------------
    def say(self, text: str) -> None:
        self.bus.publish("transcript", role="assistant", text=text)
        self.speaker.say(text)

    def try_urgent(self, text: str) -> bool:
        """Called straight from the input thread so 'stop' works even mid-task."""
        intent = match_intent(strip_wake_word(text, self.settings.assistant.wake_word)[1])
        if intent and intent.name in URGENT_INTENTS:
            self.bus.publish("transcript", role="user", text=text)
            self._handle_intent(intent, text)
            return True
        return False

    def handle(self, text: str, source: str) -> None:
        if source == "voice":
            heard, text = strip_wake_word(text, self.settings.assistant.wake_word)
            in_window = time.time() - self.last_addressed_at < self.settings.assistant.follow_up_seconds
            if not heard and not in_window:
                self.bus.log(f"(ignored, no wake word) {short(text, 60)}")
                return
            if not text.strip():
                self.say("Yes?")
                self.last_addressed_at = time.time()
                return
        self.last_addressed_at = time.time()
        self.bus.publish("transcript", role="user", text=text)

        # Scripted automations first: user-defined phrases, instant, no Claude usage.
        found = self.automations.match(text) if self.automations else None
        if found:
            macro, params = found
            if not self.automations.start(macro, params, text):
                self.say("Another automation is still running.")
            return

        intent = match_intent(text)
        if intent and intent.name in LOCAL_INTENTS:
            self.bus.publish("route", utterance=text, route="local", name=intent.name)
            if not (self.local and self.local.handle(intent.name, intent.args)):
                self.dispatch(text)
        elif intent:
            self._handle_intent(intent, text)
        else:
            self.dispatch(text)

    def on_agent_finished(self, backend: AgentBackend, result: AgentResult) -> None:
        """Runs on the main loop once the agent's turn ends."""
        if result.cancelled:
            return
        spoken = spoken_reply(result.summary)
        if PLAN_BLOCK.search(result.summary) and not self.plan.is_empty and "go ahead" not in spoken.lower():
            spoken = f"{spoken} Say go ahead when you want me to run it.".strip()
        if not spoken:
            spoken = "Done." if result.ok else f"{backend.label} ran into a problem. Check the dashboard."
        self.say(spoken)

        if self.waiting:
            queued, self.waiting = "\n".join(self.waiting), []
            self.dispatch(queued)

    # -- talking to the agent -------------------------------------------------------
    def dispatch(self, text: str, label: str | None = None, executing_plan: bool = False) -> None:
        if self.runner.running:
            self.waiting.append(text)
            self.say(f"Got it. I'll pass that to {self.runner.backend.label} when it finishes this step.")
            return
        backend = self.registry.current
        if not backend.is_available():
            self.say(f"I can't find the {backend.label} command on this machine.")
            return

        prompt = text
        if self.plan_mode and not executing_plan:
            prompt = f"Plan only, don't change anything yet. {text}"
        route = "execute_plan" if executing_plan else ("plan" if self.plan_mode else "task")
        self.bus.publish("route", utterance=label or text, route="agent", name=route, backend=backend.label)
        self.runner.start(backend, prompt, label or short(text, 60), executing_plan)

        def acknowledge() -> None:
            if self.runner.running and not self.speaker.is_busy:
                self.speaker.say(random.choice(ACKS))

        threading.Timer(self.settings.assistant.filler_after_seconds, acknowledge).start()

    # -- local intents ------------------------------------------------------------
    def _handle_intent(self, intent: Intent, text: str) -> None:
        getattr(self, f"_intent_{intent.name}")(text, **intent.args)

    def _intent_quit(self, _text: str) -> None:
        self.say("Goodbye!")
        self.speaker.wait_idle(5)
        self.request_quit()

    def _intent_stop_speaking(self, _text: str) -> None:
        self.speaker.stop()

    def _intent_cancel_agent(self, _text: str) -> None:
        self.waiting.clear()
        self.speaker.stop()
        self.say("Stopping it." if self.runner.cancel() else "Nothing is running right now.")

    def _intent_switch_backend(self, _text: str, backend: str) -> None:
        name = self.registry.resolve(backend)
        if not name:
            self.say("I only know Claude and Antigravity.")
            return
        chosen = self.registry.select(name)
        self.bus.publish("backend", name=chosen.label)
        note = "" if chosen.is_available() else f" But I can't find the {chosen.executable} command."
        self.say(f"Switched to {chosen.label}.{note}")

    def _intent_delegate(self, _text: str, backend: str, task: str) -> None:
        name = self.registry.resolve(backend)
        if name and name != self.registry.current_name:
            self.bus.publish("backend", name=self.registry.select(name).label)
        self.dispatch(task)

    def _intent_new_plan(self, _text: str, topic: str = "") -> None:
        self.plan.clear()
        self.plan_mode = True
        self.bus.publish("plan", **self.plan.to_dict())
        if topic:
            self.dispatch(f"Make a plan for: {topic}", label=f"plan: {short(topic, 50)}")
        else:
            self.say("Okay, planning mode. Describe what you want and I'll draft a plan without changing anything.")

    def _intent_clear_plan(self, _text: str) -> None:
        self.plan.clear()
        self.plan_mode = False
        self.bus.publish("plan", **self.plan.to_dict())
        self.say("Plan cleared.")

    def _intent_remove_step(self, _text: str, number: str) -> None:
        removed = self.plan.remove_step(int(number))
        if removed:
            self.bus.publish("plan", **self.plan.to_dict())
            self.say(f"Removed step {number}, {removed.text}.")
        else:
            self.say(f"There's no step {number}.")

    def _intent_read_plan(self, _text: str) -> None:
        self.say(self.plan.to_speech())

    def _intent_execute_plan(self, text: str) -> None:
        unfinished = any(s.status != "done" for s in self.plan.steps)
        if self.plan.is_empty or not unfinished:
            self.dispatch(text)  # "go ahead" as a reply to whatever Claude just asked
            return
        self.plan_mode = False
        self.dispatch(self.plan.to_prompt(), label=self.plan.title or "the plan", executing_plan=True)

    def _intent_status(self, _text: str) -> None:
        if self.runner.running:
            queued = f" {len(self.waiting)} more message{'s' if len(self.waiting) != 1 else ''} queued." if self.waiting else ""
            self.say(self.runner.status_sentence() + queued)
        else:
            steps = len(self.plan.steps)
            mode = " We're in planning mode." if self.plan_mode else ""
            self.say(f"Nothing's running, and I'm using {self.registry.current.label}. "
                     f"The plan has {steps} step{'s' if steps != 1 else ''}.{mode}")

    def _intent_open_dashboard(self, _text: str) -> None:
        self.say("Opening the canvas." if self.show_canvas() else "The canvas is turned off in the config.")

    def _intent_new_session(self, _text: str) -> None:
        if self.runner.running:
            self.say("Wait for the current task to finish, or say cancel the task first.")
            return
        self.registry.current.reset_session()
        self.plan_mode = False
        self.say(f"Started a fresh {self.registry.current.label} conversation.")

    def _intent_list_automations(self, _text: str) -> None:
        macros = self.automations.macros if self.automations else []
        if not macros:
            self.say("There are no automations yet. Add TOML files to the automations folder.")
            return
        examples = "; ".join(re.sub(r"\{\w+\}", "something", m.phrases[0] if m.phrases else m.name) for m in macros[:5])
        self.say(f"You have {len(macros)} automations. You can say: {examples}.")

    def _intent_time(self, _text: str) -> None:
        self.say(time.strftime("It's %I:%M %p.").replace(" 0", " "))


def spoken_reply(text: str) -> str:
    """The first few sentences of the agent's reply, minus plan blocks and markers."""
    text = re.sub(r"```.*?```", " ", strip_markup(text), flags=re.S)
    text = re.sub(r"[*`#]+", "", text)
    lines = [ln.strip(" -*#>") for ln in text.splitlines() if ln.strip()]
    sentences = _SENTENCE_END.split(" ".join(lines))
    spoken = " ".join(sentences[:MAX_SPOKEN_SENTENCES]).strip()
    return spoken if len(spoken) <= 450 else spoken[:447].rsplit(" ", 1)[0] + "..."
