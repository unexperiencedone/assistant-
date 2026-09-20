"""Load, save and apply the profile.

The profile lives in data/profile.json (data/ is git-ignored: it stays on this PC).
Saving from the canvas validates it against the schema, writes it atomically,
publishes a "profile" bus event and tells every part that uses it (agents, speech
recognition) to pick up the new version.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable

from .. import store
from ..events import EventBus
from . import render
from .schema import normalize, schema

# Which audience each agent is. Anything not listed (the free cloud models) is "all".
TRUSTED_BACKENDS = {"claude", "antigravity"}


def audience_for(backend_name: str) -> str:
    return "trusted" if backend_name in TRUSTED_BACKENDS else "all"


class ProfileService:
    def __init__(self, path: Path, bus: EventBus | None = None) -> None:
        self.path = path
        self.bus = bus
        self._lock = threading.Lock()
        self._listeners: list[Callable[["ProfileService"], None]] = []
        saved = store.load(path)
        self.profile = normalize(saved)
        self.revision = 0
        # A profile written before a section existed has no opinion about it and
        # normalize() supplies the default. Write that back, or the file and the running
        # assistant disagree -- and anything reading the file (an agent, another tool)
        # concludes a feature is off when it is on.
        if saved and self.profile != saved:
            store.save(path, self.profile)

    # -- reading ----------------------------------------------------------------------
    def get(self) -> dict[str, Any]:
        with self._lock:
            return self.profile

    def document(self) -> dict[str, Any]:
        """Everything the canvas form needs in one response."""
        profile = self.get()
        return {"profile": profile, "schema": schema(), "revision": self.revision, "preview": preview(profile)}

    @property
    def preferred_name(self) -> str:
        i = self.get()["identity"]
        return i["preferred_name"] or (i["name"].split()[0] if i["name"] else "")

    @property
    def reply_sentences(self) -> int:
        return render.REPLY_SENTENCES[self.get()["work_style"]["spoken_replies"]]

    def standing(self, audience: str) -> str:
        return render.standing_context(self.get(), audience)

    def with_context(self, prompt: str, request: str, backend_name: str) -> str:
        """The prompt, with profile details for anything the request mentions put in front."""
        context = render.relevant_context(self.get(), request, audience_for(backend_name))
        return f"{context}\n\n{prompt}" if context else prompt

    def projects_in(self, text: str) -> list[str]:
        """Names of your projects this text mentions (used locally, to tag history sessions)."""
        return [p["name"] for p in self.get()["projects"]["items"] if render.mentions(text, p)]

    def hotwords(self, wake_word: str = "") -> str:
        words = render.speech_words(self.get())
        if wake_word:
            words = [wake_word.title(), *(w for w in words if w.lower() != wake_word.lower())]
        return ", ".join(words)

    def folder_for(self, spoken: str) -> str | None:
        """ "open drishtikon" -> that project's folder, when it's set and exists."""
        spoken = spoken.strip().lower()
        for item in self.get()["projects"]["items"]:
            names = [item["name"], *item["aliases"]]
            if item["folder"] and any(spoken in (n.lower(), f"{n.lower()} project", f"{n.lower()} folder") for n in names):
                folder = os.path.expandvars(item["folder"])
                if os.path.isdir(folder):
                    return folder
        return None

    # -- writing ----------------------------------------------------------------------
    def update(self, raw: Any) -> dict[str, Any]:
        profile = normalize(raw)
        with self._lock:
            self.profile = profile
            self.revision += 1
        store.save(self.path, profile)
        self._announce()
        return profile

    def on_change(self, listener: Callable[["ProfileService"], None]) -> None:
        self._listeners.append(listener)

    def announce(self) -> None:
        """Publish the current profile once at start-up."""
        self._announce()

    def _announce(self) -> None:
        if self.bus:
            self.bus.publish("profile", revision=self.revision, name=self.preferred_name)
        for listener in list(self._listeners):
            try:
                listener(self)
            except Exception as exc:  # one consumer failing must not block the save
                if self.bus:
                    self.bus.log(f"Couldn't apply the profile: {exc}", "warn")


def preview(profile: dict[str, Any]) -> dict[str, str]:
    """What each kind of agent would be told, so the form can show it before saving."""
    return {
        "trusted": render.standing_context(profile, "trusted"),
        "all": render.standing_context(profile, "all"),
        "speech": ", ".join(render.speech_words(profile)),
    }
