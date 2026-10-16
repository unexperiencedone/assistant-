"""Claude Code and Antigravity stand in for each other.

There is no check before a request: it goes straight to the brain it was meant for,
and only a turn that came back without an answer (not installed, not signed in, out
of usage, crashed, blocked) is handed to the other one. Whatever comes back from
there is the reply. One hand-over per request, so the two never bounce it between
them; a cancelled turn is never handed over.
"""

from __future__ import annotations

import threading
from typing import Callable, Mapping

from .base import AgentBackend, AgentEvent, AgentResult

PARTNERS = {"claude": "antigravity", "antigravity": "claude"}


def needs_partner(result: AgentResult) -> bool:
    """A turn that didn't produce an answer, for whatever reason, except being stopped."""
    return not result.ok and not result.cancelled


def partner_of(backend: AgentBackend, backends: Mapping[str, AgentBackend]) -> AgentBackend | None:
    other = backends.get(PARTNERS.get(backend.name, ""))
    if other is None or other.resolve_executable() is None:
        return None
    # Its own session: the partner's main conversation may be busy with something else.
    return other.spawn()


def handover_prompt(prompt: str, failed: AgentBackend, result: AgentResult, acted: bool) -> str:
    """The request as the partner gets it. Only when the first brain had already started
    doing things is it told so, so nothing done twice goes unnoticed."""
    if not acted:
        return prompt
    reason = " ".join((result.detail or result.summary or "it stopped").split())[:200]
    return (f"{failed.label} started on this request and stopped partway ({reason}). "
            "Check what it already did before repeating anything.\n\n" + prompt)


def run(backend: AgentBackend, prompt: str, on_event: Callable[[AgentEvent], None],
        cancel: threading.Event, backends: Mapping[str, AgentBackend],
        on_handover: Callable[[AgentBackend, AgentBackend, AgentResult], None] | None = None,
        ) -> tuple[AgentBackend, AgentResult]:
    """Run the turn, and on the partner if it came back empty. Returns (who answered, result)."""
    acted = False

    def watch(event: AgentEvent) -> None:
        nonlocal acted
        acted = acted or event.kind == "tool"
        on_event(event)

    result = backend.run(prompt, watch, cancel)
    if not needs_partner(result) or cancel.is_set():
        return backend, result
    partner = partner_of(backend, backends)
    if partner is None:
        return backend, result
    if on_handover is not None:
        on_handover(backend, partner, result)
    second = partner.run(handover_prompt(prompt, backend, result, acted), on_event, cancel)
    if needs_partner(second):
        # Neither could do it: say both reasons rather than only the last one.
        second.summary = (f"{backend.label} couldn't take it ({result.summary or result.detail}), "
                          f"and neither could {partner.label} ({second.summary or second.detail}).")
    return partner, second
