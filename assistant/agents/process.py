"""Run a CLI that prints newline-delimited JSON, with cancellation support."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Iterator


class NdjsonProcess:
    def __init__(self, cmd: list[str], cwd: Path) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.proc: subprocess.Popen[str] | None = None
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self.returncode: int | None = None

    def lines(self, cancel: threading.Event) -> Iterator[dict[str, Any]]:
        """Yield each JSON object the process prints; non-JSON lines become {"raw": line}."""
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            start_new_session=sys.platform != "win32",
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        watcher = threading.Thread(target=self._watch_cancel, args=(cancel,), daemon=True)
        watcher.start()

        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"raw": line}
        self.returncode = self.proc.wait()

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            if line.strip():
                self.stderr_tail.append(line.rstrip())

    def _watch_cancel(self, cancel: threading.Event) -> None:
        while self.proc and self.proc.poll() is None:
            if cancel.wait(0.2):
                self.kill()
                return

    def kill(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        if sys.platform == "win32":
            # Kill the whole tree: agent CLIs spawn shells and language servers.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                capture_output=True,
            )
        else:
            os.killpg(self.proc.pid, signal.SIGTERM)
