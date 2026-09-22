"""Publishing: one gate, and the arms that reach each platform.

The service owns the gate and tells it who can send what. Every `draft_*` method here
*stages* something and returns the sentence Nova says about it -- none of them send.
Sending happens in exactly one place, `gate.approve`, and only after you say so.

Which arms are usable right now is a question about this machine, not about the code,
so `readiness()` asks each one and reports honestly: the GitHub CLI has to be installed,
LinkedIn and Instagram need credentials in .env.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import github, instagram, linkedin
from .gate import PublishGate, describe


class PublishService:
    def __init__(self, path: Path, bus: Any = None, workspace: Path | None = None) -> None:
        self.bus = bus
        self.workspace = Path(workspace) if workspace else None
        self.gate = PublishGate(path, bus)
        self.gate.register("linkedin_post", linkedin.sender)
        self.gate.register("instagram_post", instagram.sender)
        self.gate.register("github_release", self._send_release)
        self.gate.register("github_push", self._send_push)

    def close(self) -> None:
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
            "linkedin": linkedin.check() if linkedin.configured() else
            (False, f"No LinkedIn token: put {linkedin.TOKEN_ENV} in your .env file."),
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
