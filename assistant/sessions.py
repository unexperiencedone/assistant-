"""Raw, untruncated record of every agent turn, written to disk as it happens.

The canvas and the activity feed shorten things to stay readable, and they only
hold the current run in memory. This is the opposite: one JSON-lines file per
turn with the agent's complete stream (every tool call, every argument, every
result), so "what actually happened" survives restarts and truncation.

    data/sessions/2026-09-16/claude-143017-3f2a/turn-01.jsonl

Each line is one event: {"ts": 1789..., "kind": "raw"|"meta"|"result", ...}.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TurnLog:
    path: Path | None
    _lock: threading.Lock

    def write(self, kind: str, payload: Any) -> None:
        if self.path is None:
            return
        line = json.dumps({"ts": round(time.time(), 3), "kind": kind, "data": payload},
                          ensure_ascii=False, default=str)
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                self.path = None  # disk problem: stop trying, never break the turn

    def raw(self, obj: Any) -> None:
        self.write("raw", obj)

    def close(self, summary: str, ok: bool, seconds: float) -> None:
        self.write("result", {"ok": ok, "seconds": round(seconds, 2), "summary": summary})


_NULL_TURN = TurnLog(None, threading.Lock())


class SessionRecorder:
    """Creates a folder per agent session and a file per turn inside it."""

    def __init__(self, folder: Path, enabled: bool = True, keep_days: int = 14) -> None:
        self.folder = folder
        self.enabled = enabled
        self._lock = threading.Lock()
        self._turns: dict[str, int] = {}
        if enabled:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                self._prune(keep_days)
            except OSError:
                self.enabled = False

    def start_turn(self, backend: str, prompt: str, session_id: str | None = None) -> TurnLog:
        if not self.enabled:
            return _NULL_TURN
        key = f"{backend}-{session_id or 'session'}"
        with self._lock:
            self._turns[key] = self._turns.get(key, 0) + 1
            number = self._turns[key]
            directory = self.folder / time.strftime("%Y-%m-%d") / _safe(key)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return _NULL_TURN
        log = TurnLog(directory / f"turn-{number:02d}.jsonl", threading.Lock())
        log.write("meta", {"backend": backend, "session_id": session_id, "prompt": prompt})
        return log

    def _prune(self, keep_days: int) -> None:
        cutoff = time.time() - keep_days * 86400
        for day in self.folder.iterdir():
            if day.is_dir() and day.stat().st_mtime < cutoff:
                shutil.rmtree(day, ignore_errors=True)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60]
