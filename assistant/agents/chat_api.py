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
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from ..config import ChatAgentSettings
from . import tools as tool_registry
from .base import AgentBackend, AgentEvent, AgentResult, short

USER_AGENT = "Nova-voice-assistant/1.0"
SYSTEM_PROMPT = """You are {name}, a voice assistant on the user's Windows PC. The user speaks; your reply is read aloud.

- Reply in one to three short spoken sentences. No markdown, lists, code or long file paths.
- Write for the ear: commas where a person would pause, a full stop at the end of every sentence, and symbols as words ("50 percent", "and").
- Act with your tools instead of explaining what could be done. Never claim you did something a tool didn't do.
- Chat, questions and quick reasoning: just answer, no tools.
- Opening apps or files, clicking inside apps, saved automations, Word documents: use the matching tool.
- Transcripts can contain misheard words ("cloud" for Claude). Interpret them sensibly.
- If a tool reports an error, say what failed in one sentence; don't retry the same call twice.
- These are the only tools you have: {tools}. Never call anything else; if a job needs something that isn't there, say so in one sentence.{delegate}"""

DELEGATE_RULE = ("\n- Coding, editing or creating files, running commands, git, web research, or anything multi-step: "
                 "call delegate_to_claude with the whole request. Then tell the user what it did.")


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

    def spawn(self) -> "ChatAgent":
        """A parallel task gets its own conversation, not a shared history."""
        twin = type(self)(self.settings, self.workspace, self.continue_session, self.assistant_name)
        twin.context = self.context
        twin.profile = self.profile
        twin.recorder = self.recorder
        return twin

    def build_command(self, exe: str, task: str) -> list[str]:  # not a subprocess backend
        return []

    def parse(self, obj: dict[str, Any]) -> Iterable[AgentEvent]:
        return ()

    def _system_prompt(self) -> str:
        """Name the tools that exist right now. A model told about a tool it wasn't given
        will try to call it, and Groq rejects the whole turn when it does."""
        names = [schema["function"]["name"] for schema in tool_registry.schemas(self.context)]
        prompt = SYSTEM_PROMPT.format(
            name=self.assistant_name,
            tools=", ".join(names) or "none",
            delegate=DELEGATE_RULE if "delegate_to_claude" in names else "")
        return f"{prompt}\n\n{self.profile}" if self.profile else prompt

    def set_profile(self, standing: str) -> None:
        self.profile = standing  # the system prompt is rebuilt every turn

    # -- one turn -----------------------------------------------------------------------
    def run(self, prompt: str, on_event: Callable[[AgentEvent], None], cancel: threading.Event) -> AgentResult:
        if not self.api_key:
            return AgentResult(False, f"No {self.label} key: set {self.settings.api_key_env} in your .env file.")
        started = time.time()
        turn = self.start_turn_log(prompt)
        system = self._system_prompt()
        self.history.append({"role": "user", "content": prompt})
        self._trim_history()

        retried = False
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
                turn.close(text, True, time.time() - started)
                return AgentResult(True, text or "", None, time.time() - started)

            for call in calls:
                if cancel.is_set():
                    turn.close("cancelled", False, time.time() - started)
                    return AgentResult(False, "Cancelled.", None, time.time() - started, cancelled=True)
                name = call.get("function", {}).get("name", "")
                arguments = _parse_arguments(call.get("function", {}).get("arguments"))
                on_event(AgentEvent("tool", tool=name, text=tool_registry.describe_call(name, arguments)))
                result = tool_registry.call(self.context, name, arguments)
                turn.raw({"tool": name, "arguments": arguments, "result": result})
                on_event(AgentEvent("text", text=result))
                self.history.append({"role": "tool", "tool_call_id": call.get("id", name), "name": name, "content": result})

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
            body["tools"] = tool_registry.schemas(self.context)
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
