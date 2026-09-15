"""Small Windows helpers via ctypes (no extra dependencies): single-instance
lock, global hotkey, and the system light/dark preference."""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable

IS_WINDOWS = sys.platform == "win32"

MODIFIERS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None  # kept alive for the life of the process


def acquire_single_instance(name: str = "Local\\NovaVoiceAssistant") -> bool:
    """True if this is the only running instance."""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def system_prefers_dark() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def parse_hotkey(text: str) -> tuple[int, int]:
    """'ctrl+alt+n' -> (modifiers, virtual key code)."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"unknown modifier '{part}'")
        mods |= MODIFIERS[part]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key == "space":
        vk = 0x20
    else:
        raise ValueError(f"unsupported key '{key}'")
    return mods | MOD_NOREPEAT, vk


class GlobalHotkey:
    """Registers a system-wide hotkey on its own thread with a Win32 message loop."""

    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        self.combo = combo
        self.callback = callback
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self) -> bool:
        if not IS_WINDOWS:
            return False
        threading.Thread(target=self._run, name="hotkey", daemon=True).start()
        self._ready.wait(2)
        return self.registered

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            mods, vk = parse_hotkey(self.combo)
        except ValueError:
            self._ready.set()
            return
        self.registered = bool(user32.RegisterHotKey(None, 1, mods, vk))
        self._ready.set()
        if not self.registered:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.callback()
                except Exception:
                    pass
        user32.UnregisterHotKey(None, 1)
