"""The probe: which window is in front, and whether you are still here.

Windows hands both of these over for free — the foreground window's title and owning
process, and how long since the last keyboard or mouse input. That is the whole of
what is collected: no contents, no keystrokes, no pixels. A screenshot only ever
happens when you ask for one, somewhere else entirely.

Everything below runs on one thread, wakes a couple of times a second, and does
nothing at all while the assistant is not watching.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Any, Callable

from .collapse import app_name, classify, context_of

IS_WINDOWS = sys.platform == "win32"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def idle_seconds() -> float:
        """Seconds since the last keyboard or mouse input, system-wide."""
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (_kernel32.GetTickCount() - info.dwTime) / 1000.0)

    def _process_name(pid: int) -> str:
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return buffer.value.rsplit("\\", 1)[-1]
        finally:
            _kernel32.CloseHandle(handle)

    def foreground() -> tuple[str, str] | None:
        """(process name, window title) for the window in front, or None."""
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = _user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _process_name(pid.value), buffer.value

else:  # the collector simply never sees anything to record off Windows
    def idle_seconds() -> float:
        return 0.0

    def foreground() -> tuple[str, str] | None:
        return None


def sample(idle_after: float) -> dict[str, Any]:
    """One observation: what is in front right now, or that nobody is here."""
    now = time.time()
    if idle_seconds() >= idle_after:
        return {"ts": now, "app": "idle", "category": "idle", "context": "no input", "title": ""}
    front = foreground()
    if not front:
        return {"ts": now, "app": "idle", "category": "idle", "context": "no window", "title": ""}
    process, title = front
    category = classify(process)
    return {
        "ts": now,
        "app": app_name(process),
        "category": category,
        "context": context_of(category, title),
        "title": title,
    }


class Collector:
    """Polls, and hands finished stretches of work to the store.

    A block only becomes a session row once it has lasted `dwell_seconds`, so
    Alt-Tabbing through five windows leaves nothing behind. While a block is open its
    row is extended on every poll, so the canvas can show it as it happens.
    """

    def __init__(self, store: Any, settings: Any, on_change: Callable[[], None] | None = None) -> None:
        self.store = store
        self.settings = settings
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._block: dict[str, Any] | None = None
        self._session_id: int | None = None
        self._last_prune = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="awareness", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._flush()
        self._thread = None

    def _run(self) -> None:
        poll = max(1.0, float(getattr(self.settings, "poll_seconds", 2)))
        while not self._stop.wait(poll):
            try:
                self.tick(sample(float(getattr(self.settings, "idle_seconds", 120))))
            except Exception:  # a probe failure must never take the assistant down
                continue

    # -- the part worth testing --------------------------------------------------------
    def tick(self, event: dict[str, Any]) -> None:
        """Fold one observation into the open block, closing the previous one if this
        is somewhere new."""
        key = (event["category"], event["app"], event["context"])
        if self._block and self._block["key"] == key:
            self._block["end"] = event["ts"]
        else:
            self._flush()
            self.store.add_event(event)
            self._block = {"key": key, "start": event["ts"], "end": event["ts"],
                           "app": event["app"], "category": event["category"], "context": event["context"]}
            self._session_id = None
        self._persist()
        self._maybe_prune(event["ts"])

    def _persist(self) -> None:
        """Write the open block once it has earned a row, then keep extending it."""
        block = self._block
        if not block:
            return
        dwell = float(getattr(self.settings, "dwell_seconds", 12))
        if block["category"] != "idle" and block["end"] - block["start"] < dwell:
            return
        if self._session_id is None:
            gap = float(getattr(self.settings, "merge_gap_seconds", 120))
            previous = self.store.merge_candidate(block["app"], block["context"], block["start"] - gap)
            if previous is not None:
                # Back in the same place after a short detour: one stretch, not two.
                self._session_id = int(previous["id"])
                block["start"] = float(previous["started_at"])
        self._session_id = self.store.upsert_session(self._session_id, block)
        if self.on_change:
            self.on_change()

    def _flush(self) -> None:
        self._persist()
        self._block = None
        self._session_id = None

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        self.store.prune(float(getattr(self.settings, "raw_days", 7)),
                         float(getattr(self.settings, "session_days", 19)))
