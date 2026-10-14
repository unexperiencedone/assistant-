"""What is on the screen right now, captured the moment Nova is summoned over an app.

Called by the overlay hotkey *before* Nova's own window takes focus, so it describes
the app the owner was in, not Nova:

    app / title         the foreground window (awareness.collector's own lookup)
    selection           the selected text, through UI Automation's TextPattern
    rects               where that selection is on screen, for the ink lock brackets
    focus_rect          the focused control's box, when nothing is selected
    excerpt             visible text around the focus (AfterThought's reader), bounded
    monitor / frame     the monitor it is on, and a still of it for the overlay to draw on

Privacy is AfterThought's: a denied app or title (password managers, banking, private
windows, WhatsApp, Instagram) yields the app name and nothing read from it, and every
piece of text kept goes through the same secret stripping.

Windows only. Every probe is bounded and wrapped: a slow or hostile app costs at most a
second and never the overlay.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Any

SELECTION_LIMIT = 4000
EXCERPT_LIMIT = 2500
_UIA_TEXT_PATTERN = 10014


def _bounded(fn, timeout: float = 1.2) -> Any:
    """Run a UI Automation probe on its own COM thread, giving up after `timeout`."""
    result: dict[str, Any] = {}

    def probe() -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
            try:
                with physical_pixels():   # selection rectangles in the same pixels as the monitor
                    result["value"] = fn()
            finally:
                comtypes.CoUninitialize()
        except Exception:
            result["value"] = None

    worker = threading.Thread(target=probe, name="screen-context", daemon=True)
    worker.start()
    worker.join(timeout)
    return result.get("value")


def _rects_from(array: Any) -> list[list[int]]:
    """UIA bounding rectangles come as a flat [left, top, width, height, ...] array."""
    values = list(array or [])
    out = []
    for i in range(0, len(values) - 3, 4):
        left, top, width, height = (int(v) for v in values[i:i + 4])
        if width > 0 and height > 0:
            out.append([left, top, width, height])
    return out[:40]


def _selection() -> dict[str, Any] | None:
    import comtypes.client  # noqa: F401  (loads the generated UIA module)
    from pywinauto.uia_defines import IUIA

    uia = IUIA()
    element = uia.iuia.GetFocusedElement()
    if element is None:
        return None
    found: dict[str, Any] = {}
    try:
        box = element.CurrentBoundingRectangle
        found["focus_rect"] = [box.left, box.top, box.right - box.left, box.bottom - box.top]
    except Exception:
        pass
    walker = uia.iuia.ControlViewWalker
    for _ in range(6):  # the focused control, then up to the document that holds it
        if element is None:
            break
        try:
            pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN)
            if pattern:
                text_pattern = pattern.QueryInterface(uia.UIA_dll.IUIAutomationTextPattern)
                ranges = text_pattern.GetSelection()
                texts, rects = [], []
                for i in range(ranges.Length if ranges else 0):
                    text_range = ranges.GetElement(i)
                    texts.append(text_range.GetText(SELECTION_LIMIT))
                    rects += _rects_from(text_range.GetBoundingRectangles())
                text = "\n".join(t for t in texts if t)
                if text.strip():
                    found.update(selection=text[:SELECTION_LIMIT], rects=rects)
                    return found
        except Exception:
            pass
        try:
            element = walker.GetParentElement(element)
        except Exception:
            break
    return found or None


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _PER_MONITOR_V2 = ctypes.c_void_p(-4)

    @contextmanager
    def physical_pixels():
        """Measure and place in real pixels on this thread.

        Nova's window process is only system-DPI aware, so a 100% monitor next to the
        200% laptop is reported at twice its size and position ([5760, 0, 3840, 2160]
        for a 1920x1080 at x=2880). The screen grab and UI Automation use real pixels,
        so the grab of that rectangle was pure black."""
        old = _user32.SetThreadDpiAwarenessContext(_PER_MONITOR_V2)
        try:
            yield
        finally:
            if old:
                _user32.SetThreadDpiAwarenessContext(old)

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    def _foreground_rects() -> tuple[list[int] | None, list[int] | None]:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None, None
        rect = wintypes.RECT()
        window = None
        if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            window = [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
        monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        screen = None
        if monitor and _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcMonitor
            screen = [r.left, r.top, r.right - r.left, r.bottom - r.top]
        return window, screen
else:  # pragma: no cover - the overlay only exists on Windows
    @contextmanager
    def physical_pixels():
        yield

    def _foreground_rects() -> tuple[list[int] | None, list[int] | None]:
        return None, None


def _grab(frame_path: Path, monitor: list[int], out: dict[str, str]) -> None:
    """A still of one monitor, as JPEG: a 2880x1800 PNG took half a second to encode,
    the JPEG takes about fifty milliseconds and looks the same under the overlay's dim."""
    try:
        with physical_pixels():
            _grab_into(frame_path, monitor, out)
    except Exception:
        pass


def _grab_into(frame_path: Path, monitor: list[int], out: dict[str, str]) -> None:
    try:
        from PIL import ImageGrab

        frame_path.parent.mkdir(parents=True, exist_ok=True)
        left, top, width, height = monitor
        image = ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)
        image.convert("RGB").save(frame_path, "JPEG", quality=85)
        out["path"] = str(frame_path)
    except Exception:
        pass


def capture(frame_path: Path | None = None, deny_apps: set[str] | None = None,
            deny_titles: list[str] | None = None) -> dict[str, Any]:
    """Everything the overlay and the agent need about the screen, right now."""
    from .afterthought import privacy
    from .afterthought.capture import window_text
    from .awareness.collector import foreground

    started = time.monotonic()
    front = foreground() or ("", "")
    process, title = front
    with physical_pixels():
        window, monitor = _foreground_rects()
    # The still is grabbed beside the UI Automation probes rather than after them: the
    # two don't touch each other and the overlay waits for both.
    grabbed: dict[str, str] = {}
    grabber = None
    if frame_path is not None and monitor:
        grabber = threading.Thread(target=_grab, args=(frame_path, monitor, grabbed), name="screen-grab", daemon=True)
        grabber.start()
    context: dict[str, Any] = {"process": process, "title": title, "window": window, "monitor": monitor,
                               "selection": "", "rects": [], "focus_rect": None, "excerpt": "",
                               "private": False, "frame": "", "at": time.time()}
    if privacy.denied(process, title, deny_apps or set(), deny_titles or []):
        context["private"] = True   # the name only: nothing is read from a private window
    else:
        found = _bounded(_selection) or {}
        context.update({k: v for k, v in found.items() if v})
        context["selection"] = privacy.redact(context.get("selection", ""))
        if not context["selection"]:
            context["excerpt"] = privacy.redact(window_text(EXCERPT_LIMIT, timeout=1.0) or "")
    if grabber is not None:
        grabber.join(3.0)
        context["frame"] = grabbed.get("path", "")
    context["took_ms"] = int((time.monotonic() - started) * 1000)
    return context


def describe(context: dict[str, Any]) -> str:
    """The context line an agent gets in front of the question."""
    from .awareness.collapse import app_name

    app = app_name(context.get("process", "")) if context.get("process") else "an app"
    lines = [f"The user summoned you over {app}, window titled “{context.get('title', '')}”, "
             "and is asking about what is on their screen."]
    if context.get("private"):
        lines.append("That window is private (a password manager, banking or similar): nothing was read from it.")
    if context.get("selection"):
        lines.append(f"They have this selected:\n'''\n{context['selection']}\n'''")
    elif context.get("excerpt"):
        lines.append(f"Visible text around the cursor:\n'''\n{context['excerpt']}\n'''")
    lines.append("Answer about that, briefly. Then ALWAYS end with one line "
                 "[[CHOICES: first | second | third]]: two or three short, imperative next steps the user "
                 "could take from here (for a pure explanation, follow-ups such as 'Explain it simpler' or "
                 "'Show an example'). It is shown as numbered buttons and never read aloud.")
    return "\n".join(lines)
