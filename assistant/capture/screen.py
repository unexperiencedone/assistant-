"""Recording this laptop's screen, through ffmpeg.

Windows has a screen capture source built into ffmpeg (`gdigrab`), so this needs no
driver, no overlay and no extra dependency -- ffmpeg is already on this machine.

Stopping matters more than starting. A recording killed with terminate() leaves an
unplayable file, because the container never gets its index written. So a stop sends
`q` on stdin, which is ffmpeg's own "finish cleanly" signal, and only escalates if that
is ignored. Anything else produces footage that looks fine in the folder and fails when
you open it -- the exact kind of silent failure this assistant is not allowed to have.

There is no discrete GPU here, so capture encodes on CPU with `ultrafast`: the point at
capture time is to not drop frames. Making it small is `edit.py`'s job, later, once.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import inbox

STOP_GRACE = 8  # seconds to let ffmpeg close the file properly before escalating


def available() -> bool:
    return shutil.which("ffmpeg") is not None


class ScreenRecorder:
    """One recording at a time. Starting a second one while the first runs is refused."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._started: float = 0.0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self, fps: int = 25, audio: bool = False, monitor: int | None = None) -> tuple[bool, str]:
        """`monitor` picks one screen (1 = primary). Without it, gdigrab grabs the whole
        desktop -- which on a two-screen setup is one very wide file nobody wants."""
        if not available():
            return False, "I don't have ffmpeg here, so I can't record the screen."
        if self.running:
            return False, f"I'm already recording. That's {self.elapsed()} so far."
        path = inbox.new_path(self.base, "screen", ".mp4")
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "gdigrab", "-framerate", str(fps)]
        if monitor is not None:
            screens = monitors()
            if not 1 <= monitor <= len(screens):
                return False, (f"I can see {len(screens)} screen{'s' if len(screens) != 1 else ''}, "
                               f"so screen {monitor} isn't one of them.")
            left, top, width, height = screens[monitor - 1]
            args += ["-offset_x", str(left), "-offset_y", str(top),
                     "-video_size", f"{width}x{height}"]
        args += ["-i", "desktop"]
        if audio:
            # Whatever Windows currently calls the default recording device. If it is
            # wrong, ffmpeg says so immediately rather than recording silence.
            args += ["-f", "dshow", "-i", "audio=virtual-audio-capturer"]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
        except OSError as error:
            return False, f"I couldn't start ffmpeg: {error}"
        time.sleep(0.6)  # long enough for an immediate failure (bad device, locked file) to surface
        if proc.poll() is not None:
            detail = (proc.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()
            return False, f"ffmpeg stopped straight away: {detail[-1] if detail else 'no reason given'}"
        with self._lock:
            self._proc, self._path, self._started = proc, path, time.time()
        return True, "Recording your screen. Say stop recording when you're done."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            proc, path, started = self._proc, self._path, self._started
            self._proc, self._path = None, None
        if proc is None or path is None:
            return False, "I wasn't recording anything."
        elapsed = _span(time.time() - started)
        try:
            if proc.poll() is None:
                proc.stdin.write(b"q")     # ffmpeg's own clean finish: writes the index
                proc.stdin.flush()
            proc.wait(timeout=STOP_GRACE)
        except (OSError, ValueError):
            pass
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            return False, (f"ffmpeg wouldn't close cleanly, so {path.name} may not play. "
                           "I stopped it anyway.")
        if not path.exists() or path.stat().st_size < 1024:
            return False, "The recording came out empty. Nothing usable was written."
        return True, f"Stopped after {elapsed}. Saved as {inbox.describe(path)}."

    def elapsed(self) -> str:
        with self._lock:
            started = self._started
        return _span(time.time() - started) if started else "nothing"

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": available(),
                "recording": self._proc is not None and self._proc.poll() is None,
                "file": self._path.name if self._path else "",
                "seconds": round(time.time() - self._started, 1) if self._started else 0.0,
            }


def monitors() -> list[tuple[int, int, int, int]]:
    """Each screen as (left, top, width, height), primary first where Windows says so.

    Straight from user32 rather than from a library: this is two calls, and the answer
    has to be the real virtual-desktop coordinates gdigrab expects, negative ones and all.
    """
    import ctypes
    from ctypes import wintypes

    rects: list[tuple[int, int, int, int]] = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                              ctypes.POINTER(RECT), ctypes.c_double)

    def collect(_handle, _dc, rect, _data):
        box = rect.contents
        rects.append((box.left, box.top, box.right - box.left, box.bottom - box.top))
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, None, proc(collect), 0)
    except (AttributeError, OSError):
        return []
    # The primary screen is the one whose top-left is the origin; put it first.
    rects.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))
    return rects


def _span(seconds: float) -> str:
    seconds = max(0, round(seconds))
    minutes, rest = divmod(seconds, 60)
    if not minutes:
        return f"{rest} second{'s' if rest != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''} {rest} second{'s' if rest != 1 else ''}"
