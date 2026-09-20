"""Keep the conversation and plan across restarts.

Nova is meant to run for days (tray, autostart), but everything the UI shows lives
in bounded in-memory deques. A small JSON file means a restart doesn't erase what
you asked it this morning.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

SAVE_EVERY = 3.0  # seconds; writes only happen when something changed


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: a crash mid-save must not leave a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)


class Saver:
    """Calls `collect()` on a timer and writes the result when it has changed."""

    def __init__(self, path: Path, collect: Callable[[], dict[str, Any]]) -> None:
        self.path = path
        self.collect = collect
        self._dirty = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, name="state-saver", daemon=True).start()

    def touch(self) -> None:
        self._dirty.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(SAVE_EVERY)
            if self._dirty.is_set():
                self._dirty.clear()
                try:
                    save(self.path, self.collect())
                except Exception:
                    pass  # persistence must never break the assistant

    def flush(self) -> None:
        self._stop.set()
        try:
            save(self.path, self.collect())
        except Exception:
            pass
