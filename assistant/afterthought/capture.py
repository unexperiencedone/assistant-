"""The probes: what is in front, what was just copied, what text the window shows.

All three are Windows calls and nothing else: no screenshots, no keystrokes, no
network. Each one fails soft (None), because a probe that throws must never stop the
watcher. The text probe uses UI Automation (the same accessibility tree a screen
reader reads) and is bounded in time and length, since some apps answer slowly.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from ..awareness.collector import foreground, idle_seconds  # noqa: F401  (re-exported)

_UIA_TEXT_PATTERN = 10014
_UIA_VALUE_PATTERN = 10002
_UIA_DOCUMENT = 50030
_UIA_EDIT = 50004

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _CF_UNICODETEXT = 13
    # Password managers mark what they copy with these; clipboard history tools honour them, so do we.
    _EXCLUDE = _user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    _NO_HISTORY = _user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")

    def clipboard_sequence() -> int:
        return int(_user32.GetClipboardSequenceNumber())

    def clipboard_text(limit: int = 4000) -> str | None:
        if _user32.IsClipboardFormatAvailable(_EXCLUDE) or _user32.IsClipboardFormatAvailable(_NO_HISTORY):
            return None
        if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return None
        if not _user32.OpenClipboard(None):
            return None
        try:
            handle = _user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)[:limit]
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()
else:  # pragma: no cover - the watcher only runs on Windows
    def clipboard_sequence() -> int:
        return 0

    def clipboard_text(limit: int = 4000) -> str | None:
        return None


def _focused_text(limit: int) -> str | None:
    """The text of whatever has focus, or of the document around it."""
    import comtypes.client  # noqa: F401  (loads the generated UIA module)
    from pywinauto.uia_defines import IUIA

    uia = IUIA()
    element = uia.iuia.GetFocusedElement()
    walker = uia.iuia.ControlViewWalker
    for _ in range(6):   # the focused control, then up to the nearest document
        if element is None:
            return None
        try:
            pattern = element.GetCurrentPattern(_UIA_TEXT_PATTERN)
            if pattern:
                text_pattern = pattern.QueryInterface(uia.UIA_dll.IUIAutomationTextPattern)
                text = text_pattern.DocumentRange.GetText(limit)
                if text and text.strip():
                    return text
        except Exception:
            pass
        try:
            pattern = element.GetCurrentPattern(_UIA_VALUE_PATTERN)
            if pattern:
                value = pattern.QueryInterface(uia.UIA_dll.IUIAutomationValuePattern).CurrentValue
                if value and len(value.strip()) > 20:
                    return value[:limit]
        except Exception:
            pass
        try:
            element = walker.GetParentElement(element)
        except Exception:
            return None
    return None


def window_text(limit: int = 4000, timeout: float = 1.5) -> str | None:
    """Bounded: a hung app costs at most `timeout` seconds, never the watcher."""
    result: dict[str, Any] = {}

    def probe() -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
            try:
                result["text"] = _focused_text(limit)
            finally:
                comtypes.CoUninitialize()
        except Exception:
            result["text"] = None

    worker = threading.Thread(target=probe, name="afterthought-text", daemon=True)
    worker.start()
    worker.join(timeout)
    text = result.get("text")
    return " ".join(text.split())[:limit] if text else None
