"""Publishing: one gate, and the arms that reach each platform.

The service owns the gate and tells it who can send what. Every `draft_*` method here
*stages* something and returns the sentence Nova says about it -- none of them send.
Sending happens in exactly one place, `gate.approve`, and only after you say so.

Which arms are usable right now is a question about this machine, not about the code,
so `readiness()` asks each one and reports honestly: the GitHub CLI has to be installed,
LinkedIn and Instagram need credentials in .env.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import github, instagram, linkedin, youtube
from .gate import PublishGate, describe


class PublishService:
    def __init__(self, path: Path, bus: Any = None, workspace: Path | None = None) -> None:
        self.bus = bus
        self.workspace = Path(workspace) if workspace else None
        self.gate = PublishGate(path, bus)
        # Called with (draft, detail) after a platform confirmed a send -- never before,
        # and never for a failure. The showcase ledger is written from here.
        self.after_send: list[Callable[[dict[str, Any], str], None]] = []
        self.showcase: Any = None   # publish/showcase.py, attached by the app when enabled
        self.notify: Callable[[str], None] | None = None   # tray note when a scheduled post goes out
        self.gate.register("linkedin_post", self._send_linkedin)
        self.gate.register("instagram_post", instagram.sender)
        self.gate.register("github_release", self._send_release)
        self.gate.register("github_push", self._send_push)
        self.gate.register("youtube_video", youtube.sender)
        # Staged outreach has nowhere to go yet: there is no email arm. Registered all
        # the same, so approving one gives a straight answer instead of a broken
        # sentence about an unknown draft kind, and the draft stays for later.
        self.gate.register("outreach_email", self._send_email)

    # -- scheduled posts: checked every few minutes while Nova runs -------------------
    def start(self, every: float = 300) -> None:
        if getattr(self, "_ticker", None) is not None:
            return
        self._halt = threading.Event()

        def loop() -> None:
            while not self._halt.wait(every if self._ran_once else 30):
                self._ran_once = True
                self.send_due()

        self._ran_once = False
        self._ticker = threading.Thread(target=loop, name="publish-schedule", daemon=True)
        self._ticker.start()

    def send_due(self) -> list[str]:
        said = []
        for draft, ok, detail in self.gate.send_due():
            line = detail if ok else f"A scheduled post didn't go out. {detail}"
            said.append(line)
            if self.bus is not None:
                self.bus.log(line)
            if self.notify is not None:
                try:
                    self.notify(line)
                except Exception:
                    pass
        return said

    def schedule(self, draft_id: int, when: str) -> str:
        from ..schedule.when import parse_when

        try:
            moment = parse_when(when)
        except Exception:
            return f"I couldn't tell when '{when}' is."
        if moment.timestamp() < time.time():
            return f"{when} is in the past."
        return self.gate.schedule(draft_id, moment.timestamp())[1]

    def close(self) -> None:
        halt = getattr(self, "_halt", None)
        if halt is not None:
            halt.set()
        self.gate.close()

    # -- staging (safe: nothing leaves the machine) -----------------------------------
    def draft_linkedin(self, text: str) -> str:
        return self._staged(self.gate.stage("linkedin_post", text, target="your feed"))

    def draft_instagram(self, caption: str, media: list[str] | None = None,
                        media_url: str = "", kind: str = "image") -> str:
        extra = {"kind": kind}
        if media_url:
            extra["media_url"] = media_url
        return self._staged(self.gate.stage("instagram_post", caption, target="your account",
                                            media=media, extra=extra))

    def draft_release(self, repo: str, tag: str, notes: str = "", title: str = "") -> str:
        return self._staged(self.gate.stage("github_release", notes, target=repo,
                                            extra={"tag": tag, "title": title}))

    def draft_push(self, folder: str, message: str) -> str:
        return self._staged(self.gate.stage("github_push", message, target=folder))

    def draft_youtube(self, package: str) -> str:
        """A finished video's package (publish/youtube.py) staged for approval."""
        try:
            draft = youtube.stage(self.gate, package)
        except youtube.PackageError as error:
            return f"That video isn't ready to stage: {error}."
        return (f"The video {draft['extra']['title']!r} is waiting in Studio for you to watch. "
                "Nothing has gone up. Say post it, or drop it.")

    def _staged(self, draft: dict[str, Any]) -> str:
        return f"Drafted {describe(draft)} Nothing has gone out. Say post it, or drop it."

    # -- deciding ---------------------------------------------------------------------
    def approve(self, draft_id: int | None = None) -> str:
        ok, detail = self.gate.approve(draft_id)
        return detail if ok else f"It didn't go out. {detail}"

    def discard(self, draft_id: int | None = None) -> str:
        return self.gate.discard(draft_id)[1]

    def waiting(self) -> str:
        return self.gate.spoken()

    # -- senders ----------------------------------------------------------------------
    def _send_linkedin(self, draft: dict[str, Any]) -> tuple[bool, str]:
        ok, detail = linkedin.sender(draft)
        # A draft that replaces an earlier post deletes it only after the new one is up,
        # so a failure leaves the old post standing rather than leaving nothing.
        old = (draft.get("extra") or {}).get("replaces", "")
        if ok and old:
            gone, said = linkedin.delete_post(old)
            detail = f"{detail} {said}" if gone else f"{detail} But I couldn't delete the old post: {said}"
        if ok:
            for hook in self.after_send:
                try:
                    hook(draft, detail)
                except Exception:
                    pass   # the post is out; a bookkeeping slip must not call it failed
        return ok, detail

    def _send_email(self, draft: dict[str, Any]) -> tuple[bool, str]:
        """No email arm yet, said without pretending otherwise.

        The wording matters: an earlier version said the draft was "saved, so it'll go
        out once there is one", which was not true. A refused send is recorded as failed
        and leaves the pending queue, so promising it would send itself later was a
        promise the gate does not keep. It is in the record, and that is all.
        """
        return False, ("I can't send email yet -- there's no mailbox set up. Nothing was "
                       "sent. The draft is in the record but no longer queued, so once "
                       "email works, ask me to write it again.")

    def _send_release(self, draft: dict[str, Any]) -> tuple[bool, str]:
        extra = draft.get("extra") or {}
        tag = extra.get("tag", "")
        if not tag:
            return False, "That release draft has no tag, so I can't create it."
        ok, out = github.release(draft["target"], tag, extra.get("title", ""), draft["text"])
        return ok, (f"Released {tag} on {draft['target']}." if ok else out)

    def _send_push(self, draft: dict[str, Any]) -> tuple[bool, str]:
        folder = Path(draft["target"]) if draft["target"] else self.workspace
        if folder is None or not folder.is_dir():
            return False, f"I can't find the folder {draft['target']}."
        return github.commit_and_push(folder, draft["text"])

    # -- what works right now ---------------------------------------------------------
    def readiness(self) -> dict[str, tuple[bool, str]]:
        return {
            "github": github.signed_in(),
            "linkedin": linkedin.check(),
            "instagram": instagram.check() if instagram.configured() else
            (False, f"Instagram isn't set up: {instagram.TOKEN_ENV} and {instagram.USER_ENV} "
                    "need to be in your .env file."),
        }

    def spoken_readiness(self) -> str:
        """Which arms actually work on this machine, said in one breath."""
        state = self.readiness()
        ready = [name for name, (ok, _) in state.items() if ok]
        missing = [f"{name}: {detail}" for name, (ok, detail) in state.items() if not ok]
        head = ("Ready: " + ", ".join(ready) + ". ") if ready else "None of the publishing arms work yet. "
        return head + (" ".join(missing) if missing else "")

    def state(self) -> dict[str, Any]:
        return {
            "pending": len(self.gate.pending()),
            "recent": self.gate.recent(10),
            "arms": {name: {"ok": ok, "detail": detail} for name, (ok, detail) in self.readiness().items()},
        }
