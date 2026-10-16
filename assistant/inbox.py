"""Needs you: every decision waiting on the owner, in one list, each with its actions.

Before this, a draft to approve, a proposed automation or a sign-in about to lapse was
said once, out loud, and then existed nowhere a person could see it. This is the one
place they wait. Voice still works ("post it", "keep that automation"); every action
here calls the same function the voice path calls, so a decision has one code path
whichever way it is made.

Items are rebuilt from their sources, never stored: the publish gate's SQLite rows, the
automations/pending folder, the LinkedIn token file. A restart loses nothing, and an
item disappears the moment its source no longer needs anyone.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .events import EventBus

SIGNIN_WARN_DAYS = 7


def _action(action_id: str, label: str, primary: bool = False, enabled: bool = True) -> dict[str, Any]:
    return {"id": action_id, "label": label, "primary": primary, "enabled": enabled}


class InboxService:
    def __init__(self, bus: EventBus, publisher: Any = None, automations: Path | None = None,
                 matches_intent: Callable[[str], Any] | None = None, poll_seconds: float = 30.0) -> None:
        self.bus = bus
        self.publisher = publisher
        self.automations = Path(automations) if automations else None
        self.matches_intent = matches_intent
        self.poll = poll_seconds
        self._acknowledged: set[str] = set()   # "looks good" on a scheduled post: seen, keep it out of the way
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last: list[dict[str, Any]] = []
        for topic in ("publish", "automation", "goal"):
            bus.subscribe(topic, lambda _event: self.refresh())

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self.refresh()
        threading.Thread(target=self._loop, name="inbox", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Proposals arrive as files and sign-ins age with the clock; neither raises an
        # event, so a slow poll catches them.
        while not self._stop.wait(self.poll):
            self.refresh()

    # -- reading ----------------------------------------------------------------------
    def items(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        found += self._drafts()
        found += self._proposals()
        found += self._signin()
        return found

    def refresh(self) -> list[dict[str, Any]]:
        try:
            items = self.items()
        except Exception as error:   # the inbox must never take Nova down
            self.bus.log(f"Inbox: {error}", "warn")
            return self._last
        with self._lock:
            changed = items != self._last
            self._last = items
        if changed:
            self.bus.publish("inbox", items=items, count=len(items))
        return items

    def _drafts(self) -> list[dict[str, Any]]:
        if self.publisher is None:
            return []
        from .publish.gate import describe

        gate = self.publisher.gate
        out = []
        for draft in gate.pending(20):
            extra = draft.get("extra") or {}
            out.append({
                "id": f"draft:{draft['id']}", "kind": "draft", "tag": "Draft",
                "title": extra.get("name") and f"{extra['name']} · ready to post" or describe(draft),
                "detail": self._draft_detail(draft), "when": draft["created_at"],
                "text": draft["text"][:600], "media": [Path(m).name for m in draft["media"]],
                "actions": [_action("approve", "Post it", primary=True), _action("drop", "Drop it")],
            })
        for draft in gate.scheduled():
            item_id = f"draft:{draft['id']}"
            if item_id in self._acknowledged:
                continue
            extra = draft.get("extra") or {}
            when = float(draft.get("send_at") or 0)
            out.append({
                "id": item_id, "kind": "scheduled", "tag": "Scheduled post",
                "title": extra.get("name") or describe(draft),
                "detail": self._draft_detail(draft), "when": when,
                "at": time.strftime("%a %d %b · %H:%M", time.localtime(when)) if when else "",
                "text": draft["text"][:600], "media": [Path(m).name for m in draft["media"]],
                "actions": [_action("ok", "Looks good", primary=True), _action("approve", "Post now"),
                            _action("unschedule", "Unschedule")],
            })
        return out

    @staticmethod
    def _draft_detail(draft: dict[str, Any]) -> str:
        kind = {"linkedin_post": "LinkedIn", "instagram_post": "Instagram", "github_release": "GitHub release",
                "github_push": "Commit and push", "outreach_email": "Email", "youtube_video": "YouTube"}.get(draft["kind"], draft["kind"])
        files = len(draft["media"])
        return f"{kind}" + (f" · {files} image{'s' if files != 1 else ''}" if files else "")

    def _proposals(self) -> list[dict[str, Any]]:
        if self.automations is None:
            return []
        from .automation import staging

        out = []
        for proposal in staging.waiting(self.automations, self.matches_intent):
            out.append({
                "id": f"automation:{proposal.name}", "kind": "automation", "tag": "Automation",
                "title": f"Save “{proposal.name.replace('_', ' ')}” as an instant command",
                "detail": "Reviewed · runs without a model" if proposal.clean else f"Problem: {proposal.problems[0]}",
                "when": proposal.path.stat().st_mtime if proposal.path.exists() else 0,
                "actions": [_action("keep", "Keep it", primary=True, enabled=proposal.clean),
                            _action("discard", "Discard")],
            })
        return out

    def _signin(self) -> list[dict[str, Any]]:
        from .publish import linkedin_auth

        data = linkedin_auth.load()
        expires = float(data.get("expires_at") or 0)
        if not expires:
            return []
        days = (expires - time.time()) / 86400
        if days > SIGNIN_WARN_DAYS:
            return []
        title = "LinkedIn sign-in has lapsed" if days <= 0 else f"LinkedIn sign-in lapses in {days:.0f} days"
        return [{"id": "signin:linkedin", "kind": "signin", "tag": "Sign-in", "title": title,
                 "detail": "Say “sign in to LinkedIn”, or run: python main.py linkedin auth",
                 "when": expires, "actions": []}]

    # -- acting -----------------------------------------------------------------------
    def act(self, item_id: str, action: str) -> tuple[bool, str]:
        kind, _, ref = (item_id or "").partition(":")
        try:
            if kind == "draft" and self.publisher is not None and ref.isdigit():
                ok, said = self._act_draft(int(ref), action)
            elif kind == "automation" and self.automations is not None:
                ok, said = self._act_automation(ref, action)
            else:
                ok, said = False, "That item isn't something I can act on."
        finally:
            self.refresh()
        return ok, said

    def _act_draft(self, draft_id: int, action: str) -> tuple[bool, str]:
        gate = self.publisher.gate
        if action == "approve":
            ok, detail = gate.approve(draft_id)
            return ok, detail if ok else f"It didn't go out. {detail}"
        if action == "drop":
            return gate.discard(draft_id)
        if action == "unschedule":
            return gate.unschedule(draft_id)
        if action == "ok":
            self._acknowledged.add(f"draft:{draft_id}")
            return True, "Fine, it goes out as scheduled."
        return False, f"I can't {action} a draft."

    def _act_automation(self, name: str, action: str) -> tuple[bool, str]:
        from .automation import staging

        if action == "keep":
            ok, said = staging.approve(self.automations, name, matches_intent=self.matches_intent)
        elif action == "discard":
            ok, said = staging.reject(self.automations, name)
        else:
            return False, f"I can't {action} an automation."
        self.bus.publish("automation", name=name, action=action, ok=ok)
        return ok, said
