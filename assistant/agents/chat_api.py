"""Chat-API backends: a chat model as the brain, Nova's tools as its hands.

A provider here supplies only a model behind an OpenAI-compatible chat API, so
this agent supplies the loop: the model answers or asks for tool calls, Nova runs
them and feeds the results back, until the model replies with text. Anything
needing a terminal, file edits or multi-step coding goes to Claude Code through
the delegate_to_claude tool instead of being reinvented here.

Two providers, same loop:

- **Groq** (`groq`) runs open models on its own hardware and is very fast; free
  tier, key in `GROQ_API_KEY`. Llama 3.3 70B is the default and supports tool
  calling, which is what makes the tools usable.
- **OpenRouter** (`openrouter`) routes to many models; `openrouter/free` takes
  whatever free model is up, key in `OPENROUTER_API_KEY`.

Whichever model you pick must support tool calling, or it can only talk.

No new dependency: the API is plain HTTPS over urllib.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from ..config import ChatAgentSettings
from . import tools as tool_registry
from .base import AgentBackend, AgentEvent, AgentResult, short

USER_AGENT = "Nova-voice-assistant/1.0"
SYSTEM_PROMPT = """{persona}

You are {name}, a voice assistant on the user's Windows PC. The user speaks; your reply is read aloud.

- Reply in one to three short spoken sentences. No markdown, lists, code or long file paths.
- Write for the ear: commas where a person would pause, a full stop at the end of every sentence, and symbols as words ("50 percent", "and").
- Act with your tools instead of explaining what could be done. Never claim you did something a tool didn't do.
- Chat, questions and quick reasoning: just answer, no tools.
- Opening apps or files, clicking inside apps, saved automations, Word documents: use the matching tool.
- Transcripts can contain misheard words ("cloud" for Claude). Interpret them sensibly.
- If a tool reports an error, say what failed in one sentence; don't retry the same call twice.
- These are the only tools you have: {tools}. Never call anything else; if a job needs something that isn't there, say so in one sentence.{delegate}{skills}"""

# Only the names, never the descriptions: all of them together cost about fifty tokens,
# while the descriptions would cost more every single turn than loading one costs once.
SKILLS_RULE = ("\n- Skills you can load with read_skill, each a document on how to do one kind of job well: "
               "{names}. When a skill's name covers the subject you were asked about, read it before "
               "answering, even if you think you already know -- this overrides the rule about answering "
               "questions without tools, because the skill is more specific and more current than you are. "
               "Call list_skills if the names alone don't say which fits.")

DELEGATE_RULE = (
    "\n- Anything you need to look up, including news, prices and current events: search the web yourself "
    "with web_search, and read_page only if the snippets weren't enough. Do not delegate a lookup."
    "\n- Reading around a subject, gathering material from several pages, scraping a site, comparing "
    "options, or thinking through how something might work: call delegate_to_agy. Research and writing "
    "prose is what it is for, and it is the cheaper of the two agents."
    "\n- Coding, editing or creating files, running commands, git, or a job that needs several careful steps: "
    "call delegate_to_claude with the whole request. Then tell the user what it did. It is the slowest and "
    "costliest thing you can do, so it is the last resort -- your own tools first, then agy for reading and "
    "writing, and Claude only for work on the machine itself."
    "\n- If a tool fails in a way that leaves you unable to finish at all, hand the whole request to "
    "delegate_to_claude rather than reporting a dead end. Only say you couldn't do it when the reason is "
    "something the user can fix in a word, like a name you need them to confirm.")


# A reply that says the job went to Claude, written by a model that never called the
# tool. Small models do this readily: describing the handover reads, to them, like
# performing it. Caught rather than trusted, because "I've passed that to Claude" when
# nothing was passed is the one failure the user cannot detect for themselves.
CLAIMED_HANDOVER = re.compile(
    r"\b(?:handed|handing|passed|passing|sent|sending|forwarded|delegated|delegating)\b[^.]{0,40}\bclaude\b"
    r"|\bclaude\s+(?:will|is going to|is now|has been asked|should)\b"
    r"|\bI(?:'| a|’)?(?:ll|m going to| will)\s+(?:have|get|ask|hand)\s+[^.]{0,20}claude\b"
    r"|\basked\s+claude\s+to\b",
    re.IGNORECASE,
)

ESCALATION_NOTE = (
    "Nova's fast model was asked this and could not finish it: {reason}. "
    "It already tried these tools, so don't just repeat them: {tried}. "
    "Do the job properly and answer the user directly."
)


class ChatAgent(AgentBackend):
    """The shared loop. A provider subclass sets the three class attributes below."""

    name = "chat"
    label = "Chat model"
    api_url = ""
    headers: dict[str, str] = {}

    def __init__(self, settings: ChatAgentSettings, workspace, continue_session: bool, assistant_name: str) -> None:
        super().__init__("", workspace, continue_session)
        self.settings = settings
        self.assistant_name = assistant_name
        self.context = tool_registry.ToolContext(workspace=workspace)
        self.history: list[dict[str, Any]] = []
        self.profile = ""  # the user's profile summary, visible to every agent
        self._offered: list[dict] = []   # schemas chosen for the current turn

    # -- availability ------------------------------------------------------------------
    @property
    def api_key(self) -> str:
        return os.environ.get(self.settings.api_key_env, "").strip()

    def resolve_executable(self) -> str | None:
        return self.name if self.api_key else None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        return f"{self.label} needs an API key: put {self.settings.api_key_env} in the .env file next to config.toml."

    def reset_session(self) -> None:
        self.history.clear()
        super().reset_session()

    # Plain turns carried into a parallel task, so a follow-up still knows what was
    # just discussed. Tool messages are deliberately left behind: each one belongs to a
    # specific assistant tool_calls message, and a copied tail that starts mid-exchange
    # is rejected by the API outright.
    CARRIED_TURNS = 6

    def spawn(self) -> "ChatAgent":
        """An independent conversation, but not an amnesiac one.

        A parallel task used to start with nothing at all. That is right for its tool
        history -- two tasks interleaving tool results would confuse both -- and wrong
        for the conversation, because while a long job runs *every* new request becomes
        a parallel task. Asked to open a site, then to fill in the form on it, Nova
        answered "could you tell me the website URL": the second request was a fresh
        mind that had never seen the first.
        """
        twin = type(self)(self.settings, self.workspace, self.continue_session, self.assistant_name)
        twin.context = self.context
        twin.profile = self.profile
        twin.recorder = self.recorder
        twin.history = [dict(message) for message in self.history
                        if message.get("role") in ("user", "assistant")
                        and not message.get("tool_calls")][-self.CARRIED_TURNS:]
        return twin

    def carry(self, recent: str) -> None:
        """Nothing to do: `spawn` copies the real messages, which is better than a
        summary pasted into the prompt."""

    def build_command(self, exe: str, task: str) -> list[str]:  # not a subprocess backend
        return []

    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        return ()

    def _system_prompt(self, said: str = "") -> str:
        """Name the tools that exist right now. A model told about a tool it wasn't given
        will try to call it, and Groq rejects the whole turn when it does."""
        # Only the tools worth sending for this request, so the schemas do not eat a
        # fifth of the free tier per-minute budget before the request is even in.
        self._offered = tool_registry.relevant(self.context, said)
        names = [schema["function"]["name"] for schema in self._offered]
        from .. import persona

        from . import skills as skill_registry

        installed = getattr(self.context, "skills", None) or {}
        prompt = SYSTEM_PROMPT.format(
            persona=persona.seed(self.assistant_name),
            name=self.assistant_name,
            tools=", ".join(names) or "none",
            delegate=DELEGATE_RULE if "delegate_to_claude" in names else "",
            skills=SKILLS_RULE.format(names=skill_registry.names(installed))
            if installed and "read_skill" in names else "")
        # Naming the one skill that fits, rather than leaving the model to notice it.
        # Without this the rule above is simply ignored on questions it feels sure of.
        if installed and "read_skill" in names:
            fits = skill_registry.suggest(installed, said)
            if fits:
                prompt += (f"\n\nFor this request in particular: the '{fits}' skill covers it. "
                           f"Call read_skill with name='{fits}' and follow it before you answer, "
                           "even if you believe you already know the answer.")
        return f"{prompt}\n\n{self.profile}" if self.profile else prompt

    def set_profile(self, standing: str) -> None:
        self.profile = standing  # the system prompt is rebuilt every turn

    # -- escalation ---------------------------------------------------------------------
    def _tried_so_far(self) -> str:
        """The tools already called this turn, so Claude doesn't redo dead work."""
        names = [str(m.get("name") or "") for m in self.history if m.get("role") == "tool"]
        return ", ".join(dict.fromkeys(n for n in names if n)) or "nothing that worked"

    def _escalate(self, prompt: str, reason: str, on_event: Callable[[AgentEvent], None],
                  started: float, turn: Any) -> AgentResult | None:
        """Hand the whole request to Claude when the fast tier cannot finish it.

        The cascade is only honest if its bottom tier failing means the job moves up,
        not that the job stops. Before this, running out of tool rounds ended the turn
        with "say use claude" -- asking the user to do the routing by hand, which is the
        one decision they should never have to make.

        Returns None when escalation isn't possible or didn't help, and the caller then
        reports the original failure rather than inventing a second one.
        """
        delegate = getattr(self.context, "delegate", None)
        if not delegate or not getattr(self.settings, "escalate_to_claude", True):
            return None
        # Reported as a tool call so the canvas shows the handover and the recipe store
        # records that this kind of request needed Claude (assistant/recipes).
        on_event(AgentEvent("tool", tool="delegate_to_claude", text=f"handing over: {reason}"))
        task = f"{prompt}\n\n({ESCALATION_NOTE.format(reason=reason, tried=self._tried_so_far())})"
        try:
            answer = (delegate(task) or "").strip()
        except Exception as exc:          # a failed handover must not replace the real reason
            turn.raw({"escalation_failed": str(exc)})
            return None
        if not answer:
            return None
        turn.close(answer, True, time.time() - started)
        return AgentResult(True, answer, None, time.time() - started,
                           data={"escalated": reason, "backend_finished": "claude"})

    # -- one turn -----------------------------------------------------------------------
    def run(self, prompt: str, on_event: Callable[[AgentEvent], None], cancel: threading.Event) -> AgentResult:
        if not self.api_key:
            return AgentResult(False, f"No {self.label} key: set {self.settings.api_key_env} in your .env file.")
        started = time.time()
        turn = self.start_turn_log(prompt)
        system = self._system_prompt(prompt)
        self.history.append({"role": "user", "content": prompt})
        self._trim_history()

        retried = False
        delegated = False   # did a real handover actually happen this turn?
        for _ in range(max(1, self.settings.max_tool_calls)):
            if cancel.is_set():
                turn.close("cancelled", False, time.time() - started)
                return AgentResult(False, "Cancelled.", None, time.time() - started, cancelled=True)
            messages = [{"role": "system", "content": system}, *self.history]
            try:
                message = self._chat(messages, cancel)
            except Cancelled:
                turn.close("cancelled", False, time.time() - started)
                return AgentResult(False, "Cancelled.", None, time.time() - started, cancelled=True)
            except ChatApiError as exc:
                if not (exc.retry_without_tools and not retried):
                    # Groq being down or rate-limited is not a reason for the user to get
                    # nothing: the job moves up a tier instead.
                    moved = self._escalate(prompt, "the fast model couldn't be reached",
                                           on_event, started, turn)
                    if moved is not None:
                        return moved
                    turn.close(str(exc), False, time.time() - started)
                    return AgentResult(False, str(exc), None, time.time() - started, detail=str(exc))
                retried = True  # the model garbled a tool call: let it answer in words instead
                try:
                    message = self._chat(messages, cancel, with_tools=False)
                except (Cancelled, ChatApiError) as second:
                    detail = "Cancelled." if isinstance(second, Cancelled) else str(second)
                    turn.close(detail, False, time.time() - started)
                    return AgentResult(False, detail, None, time.time() - started, detail=detail,
                                       cancelled=isinstance(second, Cancelled))

            turn.raw({"assistant": message})
            self.history.append(message)
            calls = message.get("tool_calls") or []
            text = (message.get("content") or "").strip()
            if text and calls:
                on_event(AgentEvent("text", text=text))
            if not calls:
                # It described a handover instead of making one. Make it true rather
                # than letting the user be told work started that never did.
                if not delegated and text and CLAIMED_HANDOVER.search(text):
                    turn.raw({"claimed_handover_without_calling": text})
                    moved = self._escalate(prompt, "the fast model said it would pass this to Claude",
                                           on_event, started, turn)
                    if moved is not None:
                        return moved
                turn.close(text, True, time.time() - started)
                return AgentResult(True, text or "", None, time.time() - started)

            for call in calls:
                if cancel.is_set():
                    turn.close("cancelled", False, time.time() - started)
                    return AgentResult(False, "Cancelled.", None, time.time() - started, cancelled=True)
                name = call.get("function", {}).get("name", "")
                arguments = _parse_arguments(call.get("function", {}).get("arguments"))
                on_event(AgentEvent("tool", tool=name, text=tool_registry.describe_call(name, arguments)))
                if name == "delegate_to_claude":
                    delegated = True
                result = tool_registry.call(self.context, name, arguments)
                turn.raw({"tool": name, "arguments": arguments, "result": result})
                on_event(AgentEvent("text", text=result))
                self.history.append({"role": "tool", "tool_call_id": call.get("id", name), "name": name, "content": result})

        # Out of tool rounds. This is the commonest "too complex for the fast tier"
        # signal there is, so it is the one that most needs to move up rather than come
        # back as a request for the user to re-route the job themselves.
        moved = self._escalate(prompt, f"it used all {self.settings.max_tool_calls} of its steps "
                               "without finishing", on_event, started, turn)
        if moved is not None:
            return moved
        summary = (f"I worked through {self.settings.max_tool_calls} steps without finishing. Say it again with more "
                   "detail, or say use claude for the harder parts.")
        turn.close(summary, True, time.time() - started)
        return AgentResult(True, summary, None, time.time() - started,
                           detail=f"hit max_tool_calls ({self.settings.max_tool_calls})")

    # -- HTTP ----------------------------------------------------------------------------
    def _chat(self, messages: list[dict[str, Any]], cancel: threading.Event | None = None,
              with_tools: bool = True) -> dict[str, Any]:
        body = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
        }
        if with_tools:
            body["tools"] = getattr(self, "_offered", None) or tool_registry.schemas(self.context)
        request = urllib.request.Request(self.api_url, data=json.dumps(body).encode("utf-8"), headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Groq's edge blocks the default "Python-urllib/3.x" agent with a bare
            # Cloudflare 403 (error code 1010), so every request names itself.
            "User-Agent": USER_AGENT,
            **self.headers,
        })
        payload = self._send(request, cancel)

        if "error" in payload and not payload.get("choices"):
            raise ChatApiError(f"{self.label} error: {payload['error'].get('message', payload['error'])}")
        choices = payload.get("choices") or []
        if not choices:
            raise ChatApiError(f"{self.label} returned no answer.")
        return choices[0].get("message") or {}

    def _send(self, request: urllib.request.Request, cancel: threading.Event | None) -> dict[str, Any]:
        """Run the request on a worker thread so "cancel the task" doesn't have to wait
        out a reply that can take a minute. The abandoned request is closed in the
        background; nothing further is done with its answer."""
        holder: dict[str, Any] = {}

        def call() -> None:
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    holder["payload"] = json.load(response)
            except BaseException as exc:  # re-raised on the caller's thread below
                holder["error"] = exc

        worker = threading.Thread(target=call, name=f"{self.name}-request", daemon=True)
        worker.start()
        while worker.is_alive():
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            worker.join(0.1)

        error = holder.get("error")
        if isinstance(error, urllib.error.HTTPError):
            raise self._http_error(error) from error
        if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
            raise ChatApiError(f"Couldn't reach {self.label}: {error}") from error
        if isinstance(error, json.JSONDecodeError):
            raise ChatApiError(f"{self.label} sent a reply I couldn't read.") from error
        if error is not None:
            raise ChatApiError(f"{self.label} request failed: {error}") from error
        return holder.get("payload") or {}

    def _http_error(self, exc: urllib.error.HTTPError) -> "ChatApiError":
        """Turn an HTTP failure into one sentence a listener can act on."""
        detail = ""
        try:
            detail = (json.loads(exc.read().decode("utf-8", "replace")).get("error") or {}).get("message", "")
        except Exception:
            pass
        error = ChatApiError(self._http_message(exc.code, detail))
        # Some models now and then emit a tool call the provider won't accept (bad JSON,
        # or a tool that isn't in the list). That's the model's slip, not the user's, so
        # the turn is retried once without tools rather than lost.
        error.retry_without_tools = exc.code == 400 and "tool call" in detail.lower()
        return error

    def _http_message(self, code: int, detail: str) -> str:
        if code == 401:
            return f"{self.label} rejected the API key."
        if code == 402:
            return f"That {self.label} model needs credit. Pick a free model in config.toml."
        if code == 429:
            return f"{self.label} is rate limiting me right now. {detail}".strip()
        if code == 403:
            return f"{self.label} refused that model. {detail}".strip()
        if code == 404:
            return f"{self.label} doesn't have the model {self.settings.model}. {detail}".strip()
        return f"{self.label} error {code}. {detail}".strip()

    def _trim_history(self) -> None:
        """Keep the last N turns; never start on a tool result, which some models reject."""
        limit = max(4, self.settings.history_messages)
        if len(self.history) > limit:
            self.history = self.history[-limit:]
            while self.history and self.history[0].get("role") in ("tool", "assistant"):
                self.history.pop(0)


class GroqAgent(ChatAgent):
    """Groq: open models (Llama by default) on very fast hardware, free tier."""

    name = "groq"
    label = "Groq"
    api_url = "https://api.groq.com/openai/v1/chat/completions"


class OpenRouterAgent(ChatAgent):
    """OpenRouter: one key, many models, including free ones."""

    name = "openrouter"
    label = "OpenRouter"
    api_url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {  # OpenRouter asks apps to identify themselves
        "HTTP-Referer": "https://github.com/nova-assistant",
        "X-Title": "Nova voice assistant",
    }


def list_models(agent: ChatAgent) -> str:
    """`python main.py models`: what this key can actually run, newest first."""
    if not agent.api_key:
        return f"No {agent.label} key: set {agent.settings.api_key_env} in your .env file."
    url = agent.api_url.rsplit("/chat/completions", 1)[0] + "/models"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {agent.api_key}", "User-Agent": USER_AGENT, **agent.headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            models = json.load(response)["data"]
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        return f"Couldn't list {agent.label} models: {exc}"
    rows = sorted(m.get("id", "") for m in models)
    current = f"\n\nSet one as model under [agents.{agent.name}] in config.toml (now: {agent.settings.model})."
    return "\n".join(rows) + current


class ChatApiError(RuntimeError):
    retry_without_tools = False


class Cancelled(RuntimeError):
    """The user cancelled while a request was in flight."""


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
