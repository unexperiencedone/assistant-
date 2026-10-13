"""Chunk boundaries: idle for 30 seconds, or a switch of context, debounced.

Pure logic: it is fed observations and returns finished chunks, so it can be tested
without a desktop. A context is (app, what the window is on). Alt-Tabbing through a
window for less than `min_dwell` seconds is not a context switch, it's passing by.
"""

from __future__ import annotations

from typing import Any


class Chunker:
    def __init__(self, idle_boundary: float = 30.0, min_dwell: float = 20.0) -> None:
        self.idle_boundary = idle_boundary
        self.min_dwell = min_dwell
        self.open: dict[str, Any] | None = None

    def observe(self, ts: float, app: str, context: str, idle: float, process: str = "",
                category: str = "other") -> list[dict[str, Any]]:
        """One observation. Returns the chunks this closed (zero or one)."""
        closed: list[dict[str, Any]] = []
        key = (app, context)
        if idle >= self.idle_boundary or not app:
            closed += self._close(ts - idle if idle else ts)
            return closed
        if self.open and self.open["key"] == key:
            self.open["end"] = ts
            return closed
        closed += self._close(ts)
        self.open = {"key": key, "app": app, "context": context, "process": process, "category": category,
                     "start": ts, "end": ts, "clips": [], "text": ""}
        return closed

    def add_clip(self, text: str) -> None:
        if self.open is not None and text and text not in self.open["clips"]:
            self.open["clips"].append(text)

    def set_text(self, text: str) -> None:
        """The window's readable text, as last seen while this chunk was open."""
        if self.open is not None and text:
            self.open["text"] = text

    @property
    def settled(self) -> bool:
        """Has the open context lasted long enough to be a context, not a flicker?"""
        return bool(self.open) and self.open["end"] - self.open["start"] >= self.min_dwell

    def _close(self, end: float) -> list[dict[str, Any]]:
        chunk, self.open = self.open, None
        if chunk is None:
            return []
        chunk["end"] = max(chunk["end"], min(end, chunk["end"] + 2))
        if chunk["end"] - chunk["start"] < self.min_dwell and not chunk["clips"]:
            return []   # passed through: nothing to keep
        chunk.pop("key", None)
        return [chunk]

    def flush(self, ts: float) -> list[dict[str, Any]]:
        return self._close(ts)
