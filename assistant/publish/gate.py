"""The one gate in front of everything that leaves this machine.

Nothing Nova writes goes out unread. A post, a release, a pushed commit is *staged*
here as a draft, described back to you, and sent only after you say so. The speed comes
from Nova doing all the work up to that moment -- not from removing the moment.

Three properties this has to have, and each one is a line of code someone will later be
tempted to delete:

1. **Drafts survive a restart.** They are rows in SQLite, not objects in memory, so
   "I'll approve that in the morning" still works after the laptop sleeps.
2. **Approval is per draft, never a mode.** There is no "yes to everything" switch
   here, because that is the switch that eventually posts the wrong thing.
3. **A send is recorded honestly.** If the platform refuses, the draft is marked failed
   with what it said, and Nova says it failed. A draft is never reported as sent
   because the request was made -- only because the platform confirmed it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

Sender = Callable[[dict[str, Any]], "tuple[bool, str]"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id         INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    kind       TEXT NOT NULL,              -- github_release | github_push | linkedin_post | instagram_post
    target     TEXT NOT NULL DEFAULT '',   -- repo, account, folder: whatever the sender needs
    text       TEXT NOT NULL DEFAULT '',
    media      TEXT NOT NULL DEFAULT '[]', -- local file paths, as JSON
    extra      TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed | discarded
    decided_at REAL,
    result     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status, created_at);
"""

PREVIEW = 160


class PublishGate:
    def __init__(self, path: Path, bus: Any = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()
        self._senders: dict[str, Sender] = {}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def register(self, kind: str, sender: Sender) -> None:
        """Who actually performs a draft of this kind, when and if it is approved."""
        self._senders[kind] = sender

    # -- staging ----------------------------------------------------------------------
    def stage(self, kind: str, text: str, target: str = "", media: list[str] | None = None,
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO drafts (created_at, kind, target, text, media, extra) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), kind, target, text.strip(),
                 json.dumps(list(media or []), ensure_ascii=False),
                 json.dumps(extra or {}, ensure_ascii=False, default=str)))
            self._db.commit()
            draft_id = int(cursor.lastrowid)
        draft = self.get(draft_id)
        if self.bus is not None:
            self.bus.publish("publish", id=draft_id, kind=kind, status="pending")
            self.bus.log(f"Drafted, waiting on you: {describe(draft)}")
        return draft

    # -- deciding ---------------------------------------------------------------------
    def approve(self, draft_id: int | None = None) -> tuple[bool, str]:
        """Send one draft. With no id, the oldest thing still waiting."""
        draft = self.get(draft_id) if draft_id else self.oldest_pending()
        if draft is None:
            return False, "There's nothing waiting to go out."
        if draft["status"] != "pending":
            return False, f"That one is already {draft['status']}."
        sender = self._senders.get(draft["kind"])
        if sender is None:
            self._decide(draft["id"], "failed", f"Nothing is wired up to send a {draft['kind']}.")
            return False, f"I have no way to send a {draft['kind']} yet, so it stays here."
        try:
            ok, detail = sender(draft)
        except Exception as error:  # a sender that throws must not look like a send
            ok, detail = False, f"{type(error).__name__}: {error}"
        self._decide(draft["id"], "sent" if ok else "failed", detail)
        if self.bus is not None:
            self.bus.publish("publish", id=draft["id"], kind=draft["kind"],
                             status="sent" if ok else "failed")
        return ok, detail

    def discard(self, draft_id: int | None = None) -> tuple[bool, str]:
        draft = self.get(draft_id) if draft_id else self.oldest_pending()
        if draft is None:
            return False, "There's nothing waiting."
        self._decide(draft["id"], "discarded", "You said no.")
        if self.bus is not None:
            self.bus.publish("publish", id=draft["id"], kind=draft["kind"], status="discarded")
        return True, f"Dropped it. {describe(draft)}"

    def _decide(self, draft_id: int, status: str, result: str) -> None:
        with self._lock:
            self._db.execute("UPDATE drafts SET status = ?, decided_at = ?, result = ? WHERE id = ?",
                             (status, time.time(), result[:2000], draft_id))
            self._db.commit()

    # -- reading ----------------------------------------------------------------------
    def get(self, draft_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return _draft(row) if row else None

    def pending(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM drafts WHERE status = 'pending' ORDER BY created_at LIMIT ?",
                (limit,)).fetchall()
        return [_draft(r) for r in rows]

    def oldest_pending(self) -> dict[str, Any] | None:
        waiting = self.pending(1)
        return waiting[0] if waiting else None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM drafts ORDER BY created_at DESC LIMIT ?",
                                    (limit,)).fetchall()
        return [_draft(r) for r in rows]

    def spoken(self) -> str:
        """What Nova says when asked what is waiting to go out."""
        waiting = self.pending()
        if not waiting:
            return "Nothing is waiting to go out."
        if len(waiting) == 1:
            return f"One thing waiting: {describe(waiting[0])} Say post it, or drop it."
        lines = "; ".join(describe(d) for d in waiting[:3])
        return f"{len(waiting)} waiting: {lines} Say post it to send the oldest."


KINDS = {
    "github_release": "a GitHub release",
    "github_push": "a commit and push",
    "linkedin_post": "a LinkedIn post",
    "instagram_post": "an Instagram post",
}


def describe(draft: dict[str, Any]) -> str:
    """One line a person can judge without opening anything."""
    what = KINDS.get(draft["kind"], draft["kind"].replace("_", " "))
    where = f" on {draft['target']}" if draft["target"] else ""
    text = " ".join(draft["text"].split())
    cut = "..." if len(text) > PREVIEW else ""
    files = len(draft["media"])
    media = f" with {files} file{'s' if files != 1 else ''}" if files else ""
    return f'{what}{where}{media}: "{text[:PREVIEW]}{cut}"' if text else f"{what}{where}{media}."


def _draft(row: sqlite3.Row) -> dict[str, Any]:
    draft = dict(row)
    for key, empty in (("media", "[]"), ("extra", "{}")):
        try:
            draft[key] = json.loads(draft[key] or empty)
        except ValueError:
            draft[key] = [] if key == "media" else {}
    return draft
