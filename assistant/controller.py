"""Decides what to do with each thing you say.

Order of handling for every utterance:
  1. wake word / follow-up window (speech only; noise was already dropped by audio/gate.py)
  2. instant local commands (stop, cancel, switch agent, open app, find file...)
     which use none of your Claude usage
  3. everything else goes straight into the live agent session, which does the
     work and replies; its reply is spoken
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .agent_runner import AgentRunner
from .agents import AgentRegistry
from .agents.base import AgentBackend, AgentResult, short  # noqa: F401  (AgentResult used in type hints)
from .audio.tts import Speaker
from .config import Settings
from .controller_capture import CaptureCommands
from .events import EventBus
from .intents import LOCAL_INTENTS, Intent, match_intent, normalize, strip_wake_word
from . import matching, persona, reader
from .persona import guard as persona_guard
from .classify import classify
from .intents import example_corpus as intent_examples
from .phone import PhoneError, bridge as phone_bridge
from .planning import Plan
from .planning.plan import PLAN_BLOCK, strip_markup
from .system.voice import LocalVoiceCommands
from .automation import promotion
from . import triage

if TYPE_CHECKING:
    from .automation.promotion import PromotionStore
    from .automation.service import AutomationService
    from .history import HistoryStore
    from .profile import ProfileService

ACKS = ["On it.", "Working on it.", "Okay, give me a moment.", "Sure, one sec."]
URGENT_INTENTS = {"stop_speaking", "cancel_agent", "quit"}
MAX_SPOKEN_SENTENCES = 3
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# What counts as answering a yes/no question. Anything else is a new request, not an
# answer: a question Nova asked must never eat the next thing you say.
_YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|alright|go ahead|do it|please do|"
                  r"send it|save it|sounds good|why not)\b", re.I)
_NO = re.compile(r"^(?:no|nope|nah|not now|don'?t|do not|skip|cancel|forget it|leave it|"
                 r"never ?mind|no thanks|not really)\b", re.I)


def yes_or_no(text: str) -> bool | None:
    """True for yes, False for no, None when this is not an answer at all."""
    said = normalize(text).strip()
    if _YES.match(said):
        return True
    if _NO.match(said):
        return False
    return None


class Controller(CaptureCommands):
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
        profile: "ProfileService | None" = None,
        history: "HistoryStore | None" = None,
        promotion_store: "PromotionStore | None" = None,
        phone: Any = None,
        goals: Any = None,
        recipes: Any = None,
        orchestrator: Any = None,
        capture: Any = None,
        publisher: Any = None,
        capture_settings: Any = None,
    ) -> None:
        self.profile = profile
        self.goals = goals
        self.recipes = recipes
        self.orchestrator = orchestrator
        self.capture = capture
        self.publisher = publisher
        self.capture_settings = capture_settings
        self.history = history
        self.automations = automations
        self.promotion = promotion_store
        self.phone = phone
        if self.automations is not None:
            bus.subscribe("macro_done", self._on_macro_done)
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
        self.waiting: list[str] = []  # said while every task slot was busy
        self.task_labels: dict[str, str] = {}
        self.task_commands: dict[str, str] = {}  # task id -> what you said, recorded once it succeeds
        # Where the request being answered came from, and where each running task's
        # answer will have to go when it finishes ("desktop" | "phone").
        self.reply_to = "desktop"
        self.task_origins: dict[str, str] = {}
        self.awaiting_reply = False  # Nova asked something (or heard just "Hey Nova"): no wake word needed
        self.awaiting_since = 0.0
        # Automation promotion (AGENTS.md section 12): a pending yes/no about saving an
        # automation, the automation currently running (for fallback on failure), fixes
        # queued for a background turn once its fallback succeeds, and what the last
        # successful agent request was (for "remember that").
        self._pending_promotion: tuple[str, str] | None = None
        # A phone action waiting on a yes: (action, args, how to describe it).
        self._pending_phone: tuple[str, dict, str] | None = None
        self._pending_guess = None   # a "did you mean ...?" awaiting an answer
        self._phone_queue: list[tuple[str, dict]] = []   # asked for at once, confirmed one by one
        # A text waiting on its words ("send a message to 555"), and the last body sent,
        # so "send the same message to <number>" needs no agent.
        self._pending_sms_number = ""
        self._last_sms_body = ""
        self._pending_macro: dict | None = None
        self._macro_fixes: dict[str, dict] = {}
        self._last_command = ""
        self._last_task_type = ""

    # -- entry points ---------------------------------------------------------
    def say(self, text: str, spoken: str | None = None) -> None:
        """`text` goes to the canvas whole; `spoken` is the shortened version read aloud.
        Speech has to be brief, but the conversation shouldn't lose the rest of the reply.

        A request that came from the phone is answered on the phone: the reply carries
        its origin so that canvas reads it out, and this PC stays quiet rather than
        talking to an empty room. Opening the desktop microphone for a follow-up would
        be wrong for the same reason.
        """
        heard = text if spoken is None else spoken
        self.bus.publish("transcript", role="assistant", text=text, origin=self.reply_to)
        if self.reply_to == "phone":
            return
        self.speaker.say(heard)
        policy = getattr(self.settings.assistant, "follow_up", "questions")
        if policy == "always" or (policy == "questions" and heard.rstrip().endswith("?")):
            self._expect_reply()

    def try_urgent(self, text: str, source: str = "keyboard", origin: str = "desktop") -> bool:
        """Called straight from the input thread so 'stop' works even mid-task."""
        self.reply_to = origin
        addressed, text = self._addressed(text, source)
        intent = match_intent(text) if addressed else None
        if intent and intent.name in URGENT_INTENTS:
            self.bus.publish("transcript", role="user", text=text)
            self._handle_intent(intent, text)
            return True
        return False

    def handle(self, text: str, source: str, origin: str = "desktop") -> None:
        self.reply_to = origin  # everything this request says goes back to where it came from
        addressed, text = self._addressed(text, source)  # typed "hey Nova, ..." works too
        if source == "voice":
            if not addressed:
                self.bus.publish("heard", text=text, accepted=False, reason="no wake word")
                self.bus.log(f"(ignored: no wake word) {short(text, 60)}")
                return
            self.awaiting_reply = False
            if not text.strip():
                self.say("Yes?")  # ends in "?", so the next phrase needs no wake word
                return
        self.bus.publish("transcript", role="user", text=text)

        # A yes/no answer to "want me to save that as an instant command?" — but only if
        # it really is one. "play karan aujla" is a request, not a "no".
        if self._pending_promotion:
            answer = yes_or_no(text)
            if answer is not None:
                self._resolve_pending_promotion(answer)
                return
            self._pending_promotion = None  # you moved on; the question lapses quietly

        # ...or an answer to "did you mean ...?" from the loose matcher.
        if self._pending_guess:
            answer = yes_or_no(text)
            if answer is not None:
                self._resolve_pending_guess(answer)
                return
            self._pending_guess = None   # you moved on; treat this as a fresh request

        # ...or the words for a text whose number we already have.
        if self._pending_sms_number:
            number, self._pending_sms_number = self._pending_sms_number, ""
            self._intent_phone_sms(text, number=number, body=text)
            return

        # ...or to "send that text?". Asked on the device that asked for it, answered there.
        if self._pending_phone:
            answer = yes_or_no(text)
            if answer is not None:
                self._resolve_pending_phone(answer)
                return
            # Never send something destructive because the next sentence was ambiguous.
            self._pending_phone = None

        # Scripted automations first: user-defined phrases, instant, no Claude usage.
        found = self.automations.match(text) if self.automations else None
        if found:
            macro, params = found
            self.bus.publish("route", utterance=text, route="automation", name=macro.name)
            if not self.automations.start(macro, params, text):
                self.say("Another automation is still running.")
            else:
                # If a step fails, _on_macro_done falls back to the agent for this request
                # and, once that succeeds, asks it to patch the automation (AGENTS.md section 12).
                self._pending_macro = {"utterance": text, "macro_name": macro.name, "macro_path": str(macro.path)}
                self._remember_command(text)
            return

        intent = match_intent(text)
        if intent is None:
            # Nothing matched strictly. Before paying for an agent turn, check whether
            # this is something Nova already has a script for, said differently.
            if self._try_loosely(text):
                return
        if intent and intent.name in LOCAL_INTENTS:
            self.bus.publish("route", utterance=text, route="local", name=intent.name)
            if self.local and self.local.handle(intent.name, intent.args):
                self._remember_command(text)
            else:
                self.dispatch(text, command=text)
        elif intent:
            self.bus.publish("route", utterance=text, route="intent", name=intent.name)
            self._handle_intent(intent, text)
            if intent.name not in URGENT_INTENTS:
                self._remember_command(text)
        else:
            self.bus.publish("route", utterance=text, route="agent", name="")
            self.dispatch(text, command=text)

    # -- the loose matcher (assistant/matching.py) ----------------------------------------
    def _script_corpus(self) -> list[tuple[str, str, str]]:
        """Only what already exists: automation phrases and intent examples."""
        corpus = list(intent_examples())
        if self.automations:
            corpus += self.automations.corpus()
        return corpus

    def _try_loosely(self, text: str) -> bool:
        """Did they mean a script we already have? Returns True if it was handled."""
        if not getattr(self.settings.assistant, "loose_matching", True):
            return False
        corpus = self._script_corpus()
        guess = matching.best_guess(text, corpus)
        if guess and guess.score >= matching.CONFIDENT:
            self.bus.publish("route", utterance=text, route="loose", name=guess.name,
                             score=round(guess.score, 2))
            return self._run_guess(guess, text)
        # The rules cannot decide. A free model is very good at "which of these is it?",
        # costs nothing and takes about half a second -- far less than the agent turn
        # this is trying to avoid. It decides between a script and handing over.
        if self._classifier_key():
            self._classify_later(text, corpus, guess)
            return True
        if guess:
            return self._ask_about(guess, text)
        return False

    def _classifier_key(self) -> str:
        if not getattr(self.settings.assistant, "loose_classifier", True):
            return ""
        groq = getattr(getattr(self.settings, "agents", None), "groq", None)
        if groq is None:
            return ""          # a Nova built without the chat-model backends at all
        return os.environ.get(getattr(groq, "api_key_env", "") or "", "").strip()

    def _ask_about(self, guess, text: str) -> bool:
        """Close, but not close enough to act on unasked."""
        self._pending_guess = (guess, text)
        self.bus.publish("route", utterance=text, route="loose-ask", name=guess.name)
        spoken = guess.phrase.format(**guess.args) if guess.args else guess.phrase
        self.say(f'Did you mean "{spoken}"?')
        return True

    def _classify_later(self, text: str, corpus: list, fallback) -> None:
        """Ask Groq off the work loop, then either run a script or hand it to the agent."""
        groq = getattr(getattr(self.settings, "agents", None), "groq", None)
        origin = self.reply_to

        def work() -> None:
            self.reply_to = origin
            picked = classify(text, corpus, api_key=self._classifier_key(),
                              model=getattr(groq, "model", ""),
                              timeout=float(getattr(groq, "timeout", 6) or 6))
            if picked is not None:
                self.bus.publish("route", utterance=text, route="classifier",
                                 name=picked.name, score=round(picked.score, 2))
                if self._run_guess(picked, text):
                    return
            if fallback is not None:
                # The model declined, but the rules had something worth asking about.
                self._ask_about(fallback, text)
                return
            self.bus.publish("route", utterance=text, route="agent", name="")
            self.dispatch(text, command=text)

        threading.Thread(target=work, name="classify", daemon=True).start()

    def _run_guess(self, guess, text: str) -> bool:
        if guess.route == "automation" and self.automations:
            macro = self.automations.by_name(guess.name)
            if not macro:
                return False
            if not self.automations.start(macro, guess.args, text):
                self.say("Another automation is still running.")
            else:
                self._pending_macro = {"utterance": text, "macro_name": macro.name,
                                       "macro_path": str(macro.path)}
                self._remember_command(text)
            return True
        handler = getattr(self, f"_intent_{guess.name}", None)
        if handler is None:
            return False
        handler(text, **guess.args)
        self._remember_command(text)
        return True

    def _resolve_pending_guess(self, yes: bool) -> None:
        (guess, said), self._pending_guess = self._pending_guess, None
        if not yes:
            # Guessed wrong: hand on what they actually said, not what Nova heard in it.
            self.say("Right, I'll pass it on instead.")
            self.dispatch(said, command=said)
            return
        if not self._run_guess(guess, said):
            self.say("I couldn't run that after all.")

    def _addressed(self, text: str, source: str) -> tuple[bool, str]:
        """Was this said to Nova? Typed text always is. Speech needs the wake word at the
        start, unless Nova is waiting for an answer."""
        a = self.settings.assistant
        heard, rest = strip_wake_word(text, a.wake_word, getattr(a, "wake_aliases", ()))
        if source != "voice":
            return True, rest
        return heard or self._reply_window_open(), rest

    def _expect_reply(self) -> None:
        self.awaiting_reply = True
        self.awaiting_since = time.time()

    def _reply_window_open(self) -> bool:
        if not self.awaiting_reply:
            return False
        # Count from when Nova stopped talking, not from when the reply was queued.
        finished = max(self.awaiting_since, getattr(self.speaker, "last_active_at", 0.0))
        return time.time() - finished < getattr(self.settings.assistant, "follow_up_seconds", 8)

    def on_agent_finished(self, backend: AgentBackend, result: AgentResult, task_id: str = "") -> None:
        """Runs on the main loop once an agent turn ends."""
        label = self.task_labels.pop(task_id, "")
        command = self.task_commands.pop(task_id, "")
        # A turn that started on the phone answers on the phone, however long it took
        # and whatever else has been said on the desktop meanwhile.
        self.reply_to = self.task_origins.pop(task_id, "desktop")
        fix_ctx = self._macro_fixes.pop(task_id, None) if task_id else None
        if result.cancelled:
            if self.orchestrator is not None and self.orchestrator.owns(task_id):
                self.orchestrator.cancel()   # one cancelled step abandons its group
            return
        # A step of an orchestrated group stays silent: the group answers once, at the
        # end. Three tasks each narrating themselves is three voices over each other,
        # and half-duplex speech turns that into a jumble rather than a result.
        if self.orchestrator is not None and self.orchestrator.owns(task_id):
            combined = self.orchestrator.finished(task_id, result.ok, result.summary)
            if not combined:
                self.bus.log("a step finished; waiting for the rest of the group")
                return
            # The group's single answer takes the ordinary path from here: guard, then
            # shortened for speech, so it is treated exactly like any other reply.
            result.summary = combined
        # Strip Claude's own [[TASK: type]] tag (AGENTS.md section 12) before anything
        # below speaks or shows the reply.
        task_type, result.summary = promotion.extract_task(result.summary)
        # The agent hands an action back when a phrasing missed the instant command
        # (agents/rules.py). The marker is an instruction to Nova, not words to read out.
        asked_for = phone_bridge.parse_markers(result.summary)
        if asked_for:
            result.summary = phone_bridge.strip_markers(result.summary)
        if result.ok and command:
            self._remember_command(command)  # only requests that worked teach the quick actions
            self._last_command, self._last_task_type = command, task_type
        # What worked, so the cheap tier can do this one next time (docs/recipes.md).
        # Recorded after the tag is extracted, because the tag is the key it is stored under.
        if self.recipes and task_type and command:
            if result.ok:
                self.recipes.remember(task_type, command, backend=backend.label,
                                      seconds=result.seconds, task_id=task_id)
            else:
                self.recipes.note_failure(task_type, result.detail or result.summary)
        decision = promotion.DECISION_NONE
        if result.ok and fix_ctx:
            # This turn was Claude finishing a request its own automation had just failed on;
            # now ask it to patch that automation using what it just did as the evidence.
            self._queue_automation_fix(fix_ctx)
        elif result.ok and command and task_type and self.promotion and self.settings.promotion.enabled:
            decision = self.promotion.record(task_type, command, result.data.get("cost_usd"),
                                             result.data.get("turns"), result.seconds)
            if decision == promotion.DECISION_AUTO:
                self._queue_automation_write(task_type, command)
            elif decision == promotion.DECISION_ASK and promotion.taught_itself(result.summary):
                # It just taught Nova something as part of this very turn. Asking to be
                # taught, one sentence later, is the question nobody wants.
                decision = promotion.DECISION_NONE
        # With several tasks in flight, say which one this was: they finish out of order.
        others_running = bool(self.runner.active)
        prefix = f"{label}: " if others_running and label else ""
        # Last line of defence: a backend that introduced itself, or explained a failure
        # by naming plumbing the user cannot act on (persona/guard.py).
        result.summary, tripped = persona_guard.scrub(
            result.summary, persona.identity_line(self._persona_name(), self._maker(), self._register()))
        if tripped:
            self.bus.log(f"persona guard: {tripped} from {backend.label}", "warn")
        spoken = spoken_reply(result.summary, self.profile.reply_sentences if self.profile else MAX_SPOKEN_SENTENCES)
        shown = full_reply(result.summary)
        # A document goes to the reader whole, and the voice says a summary of it rather
        # than its first three sentences. Speech was already discarding the rest; the
        # difference is that now the rest is in front of you instead of behind a window.
        if self._open_reader(shown):
            spoken = reader.gist(shown) or spoken
            spoken = f"{spoken} I've put the whole thing on screen." if spoken else                 "I've put that on screen; it was too long to read out."
        if PLAN_BLOCK.search(result.summary) and not self.plan.is_empty and "go ahead" not in spoken.lower():
            ask = "Say go ahead when you want me to run it."
            spoken, shown = f"{spoken} {ask}".strip(), f"{shown}\n\n{ask}".strip()
        if not spoken and not (result.ok and asked_for):
            # Never a bare "Done.", and never a vague failure: say the actual reason,
            # because a voice user may have no screen in front of them.
            spoken = self._failure_reason(backend, result) if not result.ok else (
                f"{backend.label} finished but didn't report anything. Check the canvas to see what it did.")
            shown = self._failure_reason(backend, result, short=False) if not result.ok else spoken
        self.say(prefix + shown, spoken=prefix + spoken)
        if asked_for:
            self._run_phone_markers(asked_for)
        elif decision == promotion.DECISION_ASK:
            self._pending_promotion = (task_type, command)
            self.say("That one took a bit. Want me to save it as an instant command for next time?")

        self._start_waiting()

    # -- automation promotion (AGENTS.md section 12) --------------------------------------
    def _on_macro_done(self, event) -> None:
        """Runs on the automation's own thread (see automation/macros.py), same as the
        existing `say` callback it's given, so it stays lightweight: hand real work to
        dispatch(), which the runner already guards with its own lock."""
        data = event.data
        ctx, self._pending_macro = self._pending_macro, None
        if data.get("ok", True) or not ctx or not self.settings.promotion.enabled:
            return
        macro_name = ctx["macro_name"]
        self.bus.log(f"'{macro_name}' failed; letting {self.registry.current.label} finish it, "
                     "then I'll ask it to patch the automation.", "warn")
        task_id = self.dispatch(ctx["utterance"], command=ctx["utterance"])
        if task_id:
            self._macro_fixes[task_id] = {**ctx, "step": data.get("step"), "error": data.get("error") or data.get("text", "")}

    def _resolve_pending_promotion(self, yes: bool) -> None:
        task_type, command = self._pending_promotion
        self._pending_promotion = None
        if yes:
            self._queue_automation_write(task_type, command)
            self.say("Okay, saving it.")
        else:
            if self.promotion:
                self.promotion.mark_asked(task_type)
            self.say("No problem, I'll keep asking each time for now.")

    def _queue_automation_write(self, task_type: str, command: str) -> None:
        if self.promotion:
            self.promotion.mark_promoted(task_type)
        self.dispatch(promotion.write_prompt(task_type, command), label=f"learning: {task_type}")

    def _queue_automation_fix(self, ctx: dict) -> None:
        prompt = promotion.fix_prompt(ctx["macro_name"], ctx["macro_path"], ctx["utterance"], ctx.get("step"),
                                      ctx.get("error", ""))
        self.dispatch(prompt, label=f"fixing: {ctx['macro_name']}")

    def restore_queue(self, items: list[str]) -> None:
        """Requests still waiting when Nova last closed. Not started on their own: a big job
        restarting by itself after a reboot would be a surprise."""
        if items:
            self.waiting = list(items)
            self.bus.publish("queue", items=list(self.waiting))

    def _start_waiting(self) -> int:
        """Start queued requests, in order, while they fit; the rest keep their place."""
        started = 0
        while self.waiting and self.runner.can_start(triage.weight(self.waiting[0])):
            queued = self.waiting.pop(0)
            self.bus.publish("queue", items=list(self.waiting))
            self.dispatch(queued, command=queued)
            started += 1
        return started

    def _remember_command(self, text: str) -> None:
        """Feed the canvas's quick actions: your automations plus requests that worked."""
        if self.local and self.local.system.settings.enabled:
            try:
                self.local.system.record_command(text)
            except Exception as exc:  # never let bookkeeping break a request
                self.bus.log(f"Couldn't record that command: {exc}", "warn")
        self.publish_quick_actions()

    def publish_quick_actions(self) -> None:
        items: list[dict[str, str]] = []
        for macro in (self.automations.macros if self.automations else [])[:4]:
            phrase = macro.phrases[0] if macro.phrases else macro.name
            if "{" not in phrase:  # phrases with placeholders need words we don't have
                items.append({"label": macro.name, "command": phrase})
        if self.local:
            for command in self.local.system.top_commands(6):
                if not any(i["command"] == command for i in items):
                    items.append({"label": command, "command": command})
        for fallback in ("status", "go ahead", "read the plan", "cancel the task"):
            if len(items) >= 8:
                break
            if not any(i["command"] == fallback for i in items):
                items.append({"label": fallback, "command": fallback})
        self.bus.publish("quick_actions", items=items[:8])

    @staticmethod
    def _failure_reason(backend: AgentBackend, result: AgentResult, short: bool = True) -> str:
        """Why the turn failed. `short` is for speech; the canvas gets the whole detail."""
        denied = result.data.get("denied") or []
        if denied:
            names = ", ".join(sorted(set(denied)))
            return (f"{backend.label} was blocked: it needed permission for {names}, which nobody can approve while "
                    "it runs in the background.")
        if result.detail:
            detail = spoken_reply(result.detail) if short else full_reply(result.detail)
            return f"{backend.label} stopped: {detail}"
        return f"{backend.label} ran into a problem. The canvas shows what happened."

    # -- talking to the agent -------------------------------------------------------
    def dispatch(self, text: str, label: str | None = None, executing_plan: bool = False,
                 command: str | None = None) -> str | None:
        """Returns the new task's id, or None when it was queued or blocked instead of starting."""
        # A running automation never blocks the agent: separate threads, separate work.
        # Errands run alongside anything; two big jobs at once never do.
        weight = triage.weight(text, executing_plan)
        blocker = self.runner.blocked_by(weight)
        if blocker:
            self.waiting.append(text)
            self.bus.publish("queue", items=list(self.waiting))
            if weight == triage.HEAVY and blocker.weight == triage.HEAVY:
                self.say(f"That's a big one, so I'll start it once {blocker.label} is done.")
            else:
                self.say(f"Got it. I'll start that when {blocker.label} finishes.")
            return None
        if executing_plan and self.runner.plan_is_running():
            self.say("The plan is already running.")
            return None
        backend = self.registry.current
        if not backend.is_available():
            self.say(backend.unavailable_reason())
            return None

        prompt = text
        if self.plan_mode and not executing_plan:
            prompt = f"Plan only, don't change anything yet. {text}"
        if self.profile:
            # Details of any project or person you mention, within what this agent may see.
            prompt = self.profile.with_context(prompt, text, backend.name)
        # How a job like this went last time. Silent unless a stored request really
        # resembles this one -- a hint about the wrong task is worse than no hint, since
        # a model given a confident irrelevant instruction follows it anyway.
        if self.recipes and getattr(self.settings.recipes, "hint_agents", True):
            hint = self.recipes.hint(text)
            if hint:
                prompt = f"{hint}\n\n{prompt}"
                self.bus.log("recipe: reusing what worked on a request like this")
        route = "execute_plan" if executing_plan else ("plan" if self.plan_mode else "task")
        # A task running alongside another gets its own agent session, so the two
        # conversations never mix.
        parallel = bool(self.runner.active)
        session = self.registry.spawn_current() if parallel else backend
        task_label = label or short(text, 60)
        task_id = self.runner.start(session, prompt, task_label, executing_plan, weight)
        self.task_labels[task_id or ""] = task_label
        self.task_origins[task_id or ""] = self.reply_to  # answered where it was asked, minutes later
        if command and task_id:
            self.task_commands[task_id] = command
        self.bus.publish("route", utterance=label or text, route="agent", name=route,
                         backend=backend.label, task=task_id)
        if parallel:
            self.say(f"Starting that alongside {self.runner.active[0].label}.")

        def acknowledge() -> None:
            if self.runner.running and not self.speaker.is_busy:
                self.speaker.say(random.choice(ACKS))

        threading.Timer(self.settings.assistant.filler_after_seconds, acknowledge).start()
        return task_id

    # -- local intents ------------------------------------------------------------
    def _handle_intent(self, intent: Intent, text: str) -> None:
        getattr(self, f"_intent_{intent.name}")(text, **intent.args)

    def _intent_quit(self, _text: str) -> None:
        self.say("Goodbye!")
        self.speaker.wait_idle(5)
        self.request_quit()

    def _intent_stop_speaking(self, _text: str) -> None:
        self.speaker.stop()

    def _intent_cancel_agent(self, text: str) -> None:
        self.waiting.clear()
        self.bus.publish("queue", items=[])
        self.speaker.stop()
        everything = bool(re.search(r"\b(all|both|everything)\b", text, re.I))
        stopped = self.runner.cancel(everything=everything)
        if not stopped:
            self.say("Nothing is running right now.")
        elif stopped == 1:
            self.say("Stopping it.")
        else:
            self.say(f"Stopping all {stopped} tasks.")

    def _intent_switch_backend(self, _text: str, backend: str) -> None:
        name = self.registry.resolve(backend)
        if not name:
            self.say("I only know Claude and Antigravity.")
            return
        chosen = self.registry.select(name)
        self.bus.publish("backend", name=chosen.label)
        note = "" if chosen.is_available() else f" But {chosen.unavailable_reason()}"
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

    def _intent_move_step(self, _text: str, number: str, to: str = "", direction: str = "") -> None:
        position = int(number)
        count = len(self.plan.steps)
        target = {"up": position - 1, "down": position + 1, "first": 1, "to the top": 1,
                  "last": count, "to the end": count}.get(direction, int(to) if to else position)
        moved = self.plan.move_step(position, max(1, min(count, target)))
        if not moved:
            self.say(f"I couldn't move step {number} there.")
            return
        self.bus.publish("plan", **self.plan.to_dict())
        new_position = next(i for i, s in enumerate(self.plan.steps, 1) if s is moved)
        self.say(f"Moved {moved.text} to step {new_position}.")

    def _intent_read_plan(self, _text: str) -> None:
        self.bus.publish("plan", **self.plan.to_dict())  # also bring it back onto the canvas
        self.say(self.plan.to_speech())

    def _intent_execute_plan(self, text: str) -> None:
        unfinished = any(s.status != "done" for s in self.plan.steps)
        if self.plan.is_empty or not unfinished:
            self.dispatch(text)  # "go ahead" as a reply to whatever Claude just asked
            return
        self.plan_mode = False
        self.dispatch(self.plan.to_prompt(), label=self.plan.title or "the plan", executing_plan=True)

    def _intent_resume_queue(self, _text: str) -> None:
        if not self.waiting:
            self.say("Nothing is waiting.")
            return
        if not self._start_waiting():
            self.say(f"They'll start once {self.runner.active[0].label} is done." if self.runner.active
                     else "I couldn't start them.")

    def _intent_clear_queue(self, _text: str) -> None:
        count = len(self.waiting)
        self.waiting.clear()
        self.bus.publish("queue", items=[])
        self.say(f"Cleared {count} waiting request{'s' if count != 1 else ''}." if count else "Nothing was waiting.")

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

    def _intent_remember_automation(self, _text: str) -> None:
        if not self._last_command:
            self.say("I haven't finished a request yet that I can turn into an automation.")
            return
        # The user is asking explicitly, so proceed even without a [[TASK]] tag (Claude may
        # not have thought this one was repeatable); fall back to a slug of the request itself.
        task_type = self._last_task_type or re.sub(r"[^a-z0-9]+", "_", self._last_command.lower()).strip("_")[:40]
        self._queue_automation_write(task_type or "remembered_task", self._last_command)
        self.say("On it, saving that as an instant command.")

    # -- the phone (assistant/phone/bridge.py) -------------------------------------------
    def _phone_do(self, action: str, args: dict | None = None, confirmed: bool = False) -> None:
        """Ask the phone, off the work loop: a phone on a slow link must not hold up
        everything else Nova is doing. The answer goes back to whoever asked."""
        origin = self.reply_to

        def work() -> None:
            self.reply_to = origin
            try:
                result = self.phone.call(action, args, confirmed=confirmed)
            except PhoneError as exc:
                self.bus.log(f"[{origin}] phone {action} failed: {exc}", "warn")
                self.say(str(exc))
                return
            detail = " ".join(str(result.get("output", "")).split())[:160]
            self.bus.log(f"[{origin}] phone {action}: {detail or 'ok'}")
            self.say(self.phone.phrase(action, result))

        threading.Thread(target=work, name=f"phone-{action}", daemon=True).start()

    def _phone_ready(self) -> bool:
        if self.phone is not None and self.phone.configured:
            return True
        self.say("My phone bridge isn't set up yet. See phone slash README for the steps.")
        return False

    def _intent_phone_torch(self, _text: str, state: str = "", state2: str = "") -> None:
        if not self._phone_ready():
            return
        self._phone_do("torch_on" if (state or state2) == "on" else "torch_off")

    def _intent_identity(self, _text: str) -> None:
        """Answered here so no model ever gets the chance to introduce itself as itself.

        Every backend carries its own identity from training, and a system prompt only
        argues with that. A fixed question with a fixed answer belongs in an intent.
        """
        self.say(persona.about(_text, self._persona_name(), self._maker(), self._register()))

    def _persona_name(self) -> str:
        return getattr(self.settings.assistant, "name", "Nova")

    def _maker(self) -> str:
        return getattr(self.settings.assistant, "maker", "")

    def _register(self) -> str:
        """Who is being spoken to. Only the formality changes, never the character."""
        return persona.CUSTOMER if self.reply_to == "customer" else persona.OWNER

    def _intent_phone_find(self, _text: str) -> None:
        """Lost in the room: the phone says where it is, which beats a ringtone."""
        if self._phone_ready():
            self._phone_do("find_phone")

    def _intent_phone_battery(self, _text: str) -> None:
        if self._phone_ready():
            self._phone_do("battery")

    def _intent_phone_buzz(self, _text: str) -> None:
        """"Buzz my phone" asks for a buzz. Finding it is a different request."""
        if self._phone_ready():
            self._phone_do("vibrate")

    def _intent_phone_open(self, _text: str, target: str = "") -> None:
        if self._phone_ready():
            self._phone_do("open", {"target": target.strip()})

    def _intent_phone_notify(self, _text: str, body: str = "") -> None:
        if self._phone_ready():
            self._phone_do("notify", {"title": "Nova", "text": body.strip()})

    def _intent_phone_status(self, _text: str) -> None:
        if not self._phone_ready():
            return

        def work() -> None:
            self.say("Your phone is there." if self.phone.reachable()
                     else "I can't reach your phone. Is Tailscale on and the bridge running?")

        threading.Thread(target=work, name="phone-status", daemon=True).start()

    def _intent_phone_sms(self, _text: str, number: str = "", body: str = "") -> None:
        """Sending a message is the one thing here that cannot be taken back, so it is
        always asked about first — on the device the request came from."""
        if not self._phone_ready():
            return
        number, body = number.strip(), body.strip() or self._last_sms_body
        if not body:
            self._pending_sms_number = number
            self.awaiting_reply = True
            self.say("What should the message say?")
            return
        self._last_sms_body = body
        if not getattr(self.settings, "phone", None) or getattr(self.settings.phone, "confirm_destructive", True):
            self._pending_phone = ("sms_send", {"number": number, "text": body}, f"text {number}")
            self.say(f"Send \"{body}\" to {number}?")
            return
        self._phone_do("sms_send", {"number": number, "text": body}, confirmed=True)

    def _phone_may_read(self, action: str) -> bool:
        """Reading anything off the phone is gated by the Phone section of the profile."""
        data = self.profile.get() if self.profile else None
        if phone_bridge.sensing_allowed(data, action):
            return True
        self.say("Reading that from your phone is switched off in your profile, under Phone.")
        return False

    def _phone_lookup(self, name: str, then) -> None:
        """Resolve a contact name to a number, off the work loop, then hand it on."""
        origin = self.reply_to

        def work() -> None:
            self.reply_to = origin
            try:
                # The phone filters by name where it can, so the reply is a few
                # lines rather than the whole address book (older bridges ignore it).
                result = self.phone.call("contacts", {"match": name})
            except PhoneError as exc:
                self.say(str(exc))
                return
            matched, number = phone_bridge.find_number(result.get("output", ""), name)
            if not number:
                self.say(f"I couldn't find exactly one {name} in your contacts. Give me the number instead.")
                return
            then(matched, number)

        threading.Thread(target=work, name="phone-contacts", daemon=True).start()

    def _intent_phone_call_name(self, _text: str, name: str = "") -> None:
        if not self._phone_ready() or not self._phone_may_read("contacts"):
            return

        def ask(matched: str, number: str) -> None:
            self._pending_phone = ("call_dial", {"number": number}, f"call {matched}")
            self.say(f"Call {matched} on {number}?")

        self._phone_lookup(name.strip(), ask)

    def _intent_phone_sms_name(self, _text: str, name: str = "", body: str = "") -> None:
        if not self._phone_ready() or not self._phone_may_read("contacts"):
            return

        def ask(matched: str, number: str) -> None:
            self._pending_phone = ("sms_send", {"number": number, "text": body.strip()},
                                   f"text {matched}")
            self.say(f"Send \"{body.strip()}\" to {matched} on {number}?")

        self._phone_lookup(name.strip(), ask)

    def _intent_phone_call(self, _text: str, number: str = "") -> None:
        """Dialling is louder than a text and just as final: same confirmation, asked
        wherever the request came from."""
        if not self._phone_ready():
            return
        number = number.strip()
        if getattr(self.settings, "phone", None) and not getattr(self.settings.phone, "confirm_destructive", True):
            self._phone_do("call_dial", {"number": number}, confirmed=True)
            return
        self._pending_phone = ("call_dial", {"number": number}, f"call {number}")
        self.say(f"Call {number}?")

    # -- what the agent hands back (AGENTS.md section 12) ---------------------------------
    def _run_phone_markers(self, actions: list[tuple[str, dict]]) -> None:
        """Do the harmless ones now; queue anything that needs a yes.

        This is the same path a spoken command takes -- same profile gate, same
        confirmation -- so an unrecognised phrasing costs a second, not a rebuild.
        """
        if not self._phone_ready():
            return
        for action, args in actions:
            if action in phone_bridge.SENSING:
                # A read: the profile already decides, and nothing leaves the phone.
                if self._phone_may_read(action):
                    self._phone_do(action, args)
            elif action in phone_bridge.MARKER_DIRECT:
                self._phone_do(action, args)
            else:
                # Everything else asks. A marker is text Nova found, not a thing you said.
                self._phone_queue.append((action, args))
        self._ask_next_phone()

    def _ask_next_phone(self) -> None:
        """Ask about the next queued action, resolving a name to a number first."""
        if self._pending_phone or not self._phone_queue:
            return
        action, args = self._phone_queue.pop(0)
        number = str(args.get("number", "")).strip()
        name = str(args.get("name", "")).strip()
        body = str(args.get("text", "")).strip()

        if action not in ("sms_send", "call_dial"):
            # Opening a URL, writing the clipboard, changing the volume: say what it is
            # and wait, because this may have come from a message somebody else wrote.
            detail = ", ".join(f"{key}={value}" for key, value in sorted(args.items()))
            described = f"{action.replace('_', ' ')}{' (' + detail + ')' if detail else ''}"
            self._pending_phone = (action, args, described)
            self.say(f"The agent wants to {described} on your phone. Shall I?")
            return

        def ask(who: str, dial: str) -> None:
            if action == "sms_send":
                self._pending_phone = ("sms_send", {"number": dial, "text": body}, f"text {who}")
                self.say(f'Send "{body}" to {who} on {dial}?')
            else:
                self._pending_phone = ("call_dial", {"number": dial}, f"call {who}")
                self.say(f"Call {who} on {dial}?")

        if number:
            ask(number, number)
        elif name and self._phone_may_read("contacts"):
            self._phone_lookup(name, ask)   # its thread calls ask() when it has a number
        else:
            self.say(f"I need a number for {name or 'that'}.")
            self._ask_next_phone()

    def _resolve_pending_phone(self, yes: bool) -> None:
        action, args, description = self._pending_phone
        self._pending_phone = None
        if yes:
            self._phone_do(action, args, confirmed=True)
        else:
            self.say(f"Okay, I won't {description}.")
        self._ask_next_phone()   # "text Aadidev and Anant" is two questions, not one

    def _open_reader(self, text: str) -> bool:
        """Put a long answer in the always-on-top window. True when it went there.

        Publishing is all this does: the window itself is opened by the shell, which is
        the only part of the app allowed to touch a window. When the reader is disabled
        the answer is still shown in full on the canvas, exactly as before.
        """
        settings = getattr(self.settings, "reader", None)
        if settings is None or not getattr(settings, "enabled", True):
            return False
        if not reader.is_document(text, settings.strong_chars, settings.min_chars,
                                  settings.long_chars, settings.unspeakable_chars):
            return False
        self.bus.publish("document", title=reader.title_of(text, self._persona_name()),
                         markdown=text, auto_open=bool(getattr(settings, "auto_open", True)))
        return True

    def _intent_show_reader(self, _text: str) -> None:
        """"Show me that" / "open the reader": re-open the window on the last document."""
        self.bus.publish("reader", action="show")
        self.say("On screen.")

    def _intent_hide_reader(self, _text: str) -> None:
        self.bus.publish("reader", action="hide")
        self.say("Closed it.")

    def _intent_goals_list(self, _text: str) -> None:
        if self.goals is None:
            self.say("Standing goals are turned off in the config.")
            return
        self.say(self.goals.spoken())

    def _intent_goal_add(self, _text: str, cadence: str = "", what: str = "",
                         cadence2: str = "", what2: str = "") -> None:
        """"Every morning, tell me what changed in my repos." -- stored as a request in
        your words, fired on its own schedule, and run through the ordinary pipeline."""
        if self.goals is None:
            self.say("Standing goals are turned off in the config.")
            return
        spoken, what = (cadence or cadence2).lower(), (what or what2).strip()
        every = {"hour": "hourly", "week": "weekly", "monday": "weekly"}.get(spoken, "daily")
        goal = self.goals.add(what, every, next_due=_next_due(spoken))
        if goal is None:
            self.say("I didn't catch what you want me to do.")
            return
        when = {"hourly": "every hour", "weekly": "once a week"}.get(every, "every morning"
                if spoken in ("morning", "monday") else "every day")
        self.say(f"Right. {when}, I'll {what}. Say what are your standing goals to hear them back.")

    def _intent_recap(self, _text: str, when: str = "") -> None:
        if self.history is None:
            self.say("The work history is turned off in the config.")
            return
        from .history import recap

        self.say(recap(self.history, when))

    def _intent_news_briefing(self, _text: str, topic: str = "") -> None:
        """Headlines straight from the news feeds: instant, and no agent turn.
        The rest of the briefing (weather, markets, written summaries) still goes
        through the agent, which is what the canvas's refresh button asks for."""
        from .ambient import news

        settings = self.settings.ambient
        topics = [topic] if topic else list(settings.topics)
        headlines, read = news.fetch(settings.headlines, topics, strict=bool(topic))
        if not headlines and topic:
            headlines, read = news.fetch(settings.headlines, [])
            if headlines:
                self.say(f"Nothing on {topic} in the feeds right now, so here's the top news. "
                         + news.to_speech(headlines))
                self._write_briefing(settings, headlines, read)
                return
        self.say(news.to_speech(headlines))
        if headlines:
            self._write_briefing(settings, headlines, read)

    def _write_briefing(self, settings, headlines: list, read: list) -> None:
        import os
        from pathlib import Path

        from .ambient import news

        cache = Path(os.path.expandvars(settings.cache_path)).expanduser()
        path = cache if cache.is_absolute() else self.settings.base_dir / cache
        try:
            news.write_cache(path, headlines, read)
        except OSError as exc:
            self.bus.log(f"[news] couldn't write the briefing cache: {exc}", level="warn")

    def _intent_time(self, _text: str) -> None:
        self.say(time.strftime("It's %I:%M %p.").replace(" 0", " "))


def full_reply(text: str) -> str:
    """The agent's reply exactly as it wrote it, for the canvas and the history. Nothing is
    cut or stripped here: only speech is shortened (`spoken_reply`)."""
    return text.strip()


def spoken_reply(text: str, max_sentences: int = MAX_SPOKEN_SENTENCES) -> str:
    """The first few sentences of the agent's reply, minus plan blocks and markers.
    Only for speech: `full_reply` is what the canvas shows."""
    text = re.sub(r"```.*?```", " ", strip_markup(text), flags=re.S)
    text = re.sub(r"[*`#]+", "", text)
    lines = [ln.strip(" -*#>") for ln in text.splitlines() if ln.strip()]
    sentences = _SENTENCE_END.split(" ".join(lines))
    spoken = " ".join(sentences[:max_sentences]).strip()
    return spoken if len(spoken) <= 450 else spoken[:447].rsplit(" ", 1)[0] + "..."


def _next_due(spoken: str) -> float:
    """When a spoken cadence should first fire. "Morning" means tomorrow morning, not now."""
    import time as _time

    hours = {"morning": 8, "evening": 19, "night": 21}
    if spoken not in hours:
        return _time.time()
    local = _time.localtime()
    due = _time.mktime((local.tm_year, local.tm_mon, local.tm_mday, hours[spoken], 0, 0, 0, 0, -1))
    return due if due > _time.time() else due + 86400
