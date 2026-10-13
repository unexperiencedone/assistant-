"""Click buttons and type into native Windows apps via UI Automation (pywinauto).

Controls are found by their accessible name (what a screen reader says, e.g.
Calculator's "Five" or "Equals") or AutomationId, the same way for classic
Win32, WinUI/Store apps and most Chromium/Electron apps.

Clicks prefer the Invoke pattern, which presses the control without moving the
mouse; real mouse clicks are only used when a control doesn't support it.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("nova.desktop")

INTERACTIVE_TYPES = {
    "Button", "MenuItem", "ListItem", "Hyperlink", "Edit", "CheckBox", "RadioButton", "TabItem",
    "ComboBox", "TreeItem", "SplitButton", "Slider", "Document",
}
MAX_WALK = 4000  # element cap per search, so huge apps can't stall a lookup


class AutomationError(RuntimeError):
    pass


@dataclass
class ControlInfo:
    control_type: str
    name: str
    auto_id: str

    def row(self) -> str:
        return f"{self.control_type}\t{self.name}\t{self.auto_id}"


def _attach_default_desktop() -> None:
    try:
        import ctypes
        u = ctypes.windll.user32
        hdesk = u.OpenDesktopW("default", 0, False, 0x01FF)
        if hdesk:
            u.SetThreadDesktop(hdesk)
    except Exception:
        pass


def _desktop():
    from pywinauto import Desktop

    _attach_default_desktop()
    return Desktop(backend="uia")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


# -- windows ----------------------------------------------------------------------------
def list_windows(filter_text: str = "") -> list[tuple[str, int]]:
    rows = []
    for win in _desktop().windows():
        try:
            title = win.window_text()
            if title and win.is_visible() and (not filter_text or _norm(filter_text) in _norm(title)):
                rows.append((title, win.process_id()))
        except Exception:
            continue
    return rows


WINDOW_CACHE_SIZE = 32  # Nova runs for days: this cache must not grow without limit
_window_cache: OrderedDict[str, Any] = OrderedDict()


def find_window(title: str, timeout: float = 0):
    """Best visible top-level window for `title`: exact, then prefix, then substring, then regex."""
    cached = _window_cache.get(title)
    if cached is not None:
        try:
            if cached.is_visible():  # still open: skip re-scanning every window on the desktop
                _window_cache.move_to_end(title)
                return cached
        except Exception as exc:
            log.debug("cached window %r no longer usable: %s", title, exc)
        _window_cache.pop(title, None)
    win = _search_window(title, timeout)
    _window_cache[title] = win
    _window_cache.move_to_end(title)
    while len(_window_cache) > WINDOW_CACHE_SIZE:
        _window_cache.popitem(last=False)
    return win


def _exe_name(win) -> str:
    """Basename of the window's process, lowercased and without .exe ("" if it can't be read)."""
    try:
        from pywinauto.application import process_module

        return process_module(win.element_info.process_id).rsplit("\\", 1)[-1].lower().removesuffix(".exe")
    except Exception:
        return ""


def _search_window(title: str, timeout: float):
    deadline = time.time() + timeout
    wanted = _norm(title)
    # "exe:spotify" matches by process instead of title, for apps that rename their window constantly.
    exe = wanted[4:].strip().removesuffix(".exe") if wanted.startswith("exe:") else ""
    while True:
        candidates = []
        for win in _desktop().windows():
            try:
                text = win.window_text()
                if not text or not win.is_visible():
                    continue
            except Exception:
                continue
            t = _norm(text)
            if exe:
                if _exe_name(win) != exe:
                    continue
                rank = 0
            elif t == wanted:
                rank = 0
            elif t.startswith(wanted):
                rank = 1
            elif wanted in t:
                rank = 2
            elif _safe_regex(title, text):
                rank = 3
            else:
                continue
            candidates.append((rank, len(text), win))
        if candidates:
            return min(candidates, key=lambda c: (c[0], c[1]))[2]
        if time.time() >= deadline:
            raise AutomationError(f"No window matching '{title}'.")
        time.sleep(0.3)


def _safe_regex(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.I) is not None
    except re.error:
        return False


def focus(title: str):
    win = find_window(title)
    try:
        if win.get_show_state() == 2:  # minimized
            win.restore()
        win.set_focus()
    except Exception as exc:
        # Windows refuses focus changes in some states; the action often still works.
        log.warning("couldn't focus %r: %s", title, exc)
    return win


# -- controls ---------------------------------------------------------------------------
_NAME, _CONTROL_TYPE, _AUTOMATION_ID = 30005, 30003, 30011  # UIA property ids
_TREE_SCOPE_DESCENDANTS = 4


@dataclass
class _Element:
    raw: Any  # IUIAutomationElement with cached properties
    control_type: str
    name: str
    auto_id: str


_truncated_walk = False  # set when a window has more elements than MAX_WALK


def _snapshot(win) -> list[_Element]:
    """Every descendant with name/type/id fetched in ONE cross-process call.

    Reading properties element by element costs a round trip each (Calculator: ~8 s);
    a UIA cache request returns them all at once (~0.1 s).
    """
    from pywinauto.uia_defines import IUIA

    uia = IUIA()
    type_names = _control_type_names(uia)
    request = uia.iuia.CreateCacheRequest()
    for prop in (_NAME, _CONTROL_TYPE, _AUTOMATION_ID):
        request.AddProperty(prop)
    found = win.element_info.element.FindAllBuildCache(_TREE_SCOPE_DESCENDANTS, uia.true_condition, request)
    global _truncated_walk
    _truncated_walk = found.Length > MAX_WALK
    if _truncated_walk:
        log.warning("%r has %d elements; only the first %d were searched", win.window_text(), found.Length, MAX_WALK)
    elements = []
    for i in range(min(found.Length, MAX_WALK)):
        raw = found.GetElement(i)
        try:
            elements.append(_Element(raw, type_names.get(raw.CachedControlType, ""),
                                     (raw.CachedName or "").strip(), raw.CachedAutomationId or ""))
        except Exception as exc:
            log.debug("skipped an element: %s", exc)
            continue
    return elements


_TYPE_NAMES: dict[int, str] = {}


def _control_type_names(uia) -> dict[int, str]:
    if not _TYPE_NAMES:
        _TYPE_NAMES.update({v: k for k, v in uia.known_control_types.items()})
    return _TYPE_NAMES


def list_controls(title: str, filter_text: str = "", control_type: str = "", limit: int = 60,
                  interactive_only: bool = True) -> list[ControlInfo]:
    win = find_window(title)
    wanted = _norm(filter_text)
    rows: list[ControlInfo] = []
    seen: set[tuple[str, str, str]] = set()
    for el in _snapshot(win):
        ctype, name, auto_id = el.control_type, el.name, el.auto_id
        if control_type and ctype.lower() != control_type.lower():
            continue
        if interactive_only and not control_type and ctype not in INTERACTIVE_TYPES:
            continue
        if not (name or auto_id):
            continue
        if wanted and wanted not in _norm(name) and wanted not in auto_id.lower():
            continue
        key = (ctype, name, auto_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(ControlInfo(ctype, name, auto_id))
        if len(rows) >= limit:
            break
    return rows


def find_control(title: str, name: str = "", auto_id: str = "", control_type: str = "",
                 index: int = 0, timeout: float = 5, exact: bool = False):
    """A control by exact name or AutomationId, falling back to case-insensitive prefix/substring.

    `exact` turns that fallback off, for names like "Play" that would otherwise match a longer
    button ("Play Album - Ed Sheeran") once the one you meant is gone.
    """
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    if not (name or auto_id):
        raise AutomationError("Give a control name or auto_id.")
    deadline = time.time() + timeout
    wanted = _norm(name)
    exact_only = exact
    while True:
        win = find_window(title)
        exact, prefix, contains = [], [], []
        for el in _snapshot(win):
            if control_type and el.control_type.lower() != control_type.lower():
                continue
            if auto_id:
                if el.auto_id == auto_id:
                    exact.append(el)
                continue
            n = _norm(el.name)
            if not n:
                continue
            if n == wanted:
                exact.append(el)
            elif exact_only:
                continue
            elif n.startswith(wanted):
                prefix.append(el)
            elif wanted in n:
                contains.append(el)
        matches = exact or prefix or contains
        if len(matches) > index:
            return UIAWrapper(UIAElementInfo(matches[index].raw))
        if time.time() >= deadline:
            what = f"auto_id '{auto_id}'" if auto_id else f"'{name}'"
            # "Not found" and "didn't look at all of it" are different answers.
            limit = (f" This window has more than {MAX_WALK} elements, so the search was incomplete."
                     if _truncated_walk else "")
            raise AutomationError(f"No control {what} in '{win.window_text()}'.{limit} "
                                  f"Try: nova-cli ui controls \"{title}\"")
        time.sleep(0.3)


def click(title: str, name: str = "", auto_id: str = "", control_type: str = "", index: int = 0,
          timeout: float = 5, exact: bool = False) -> str:
    control = find_control(title, name, auto_id, control_type, index, timeout, exact)
    label = control.element_info.name or auto_id
    for pattern in ("invoke", "toggle", "select"):
        method = getattr(control, pattern, None)
        if method is None:
            continue
        try:
            method()
            return label
        except Exception as exc:
            log.debug("%s() failed on %r, trying the next way to press it: %s", pattern, label, exc)
            continue
    focus(title)
    control.click_input()  # last resort: a real mouse click at the control's position
    return label


# Words that say what kind of thing was asked for, not which one.
_KIND_WORDS = {"song", "songs", "music", "track", "tracks", "playlist", "album", "by", "the", "of", "some",
               "a", "an", "and", "on", "spotify", "latest", "best", "top", "all", "hits"}


def _sounds_like(word: str, words: set[str]) -> bool:
    """A spoken, transliterated name is spelt differently every time: "didarva" is
    Spotify's "didarwa", "deedar" its "didar". Close enough counts."""
    from difflib import SequenceMatcher

    return word in words or any(len(w) > 3 and SequenceMatcher(None, word, w).ratio() >= 0.8 for w in words)


def best_match(names: list[str], query: str, prefix: str = "", exclude: str = "",
               fallback_first: bool = False) -> str | None:
    """The first result that has something to do with what was asked for, or None.

    A results list is already ranked by the app, which knows far more about relevance
    and misheard names than counting words does: for "gulzar ali songs", most shared
    words picked "Zindagi Gulzar Hai - Written by Ali Zafar", a TV soundtrack, over
    Spotify's own top result. So the order is the app's, and the words only rule out
    what is not a result at all. A name that is only the prefix ("Play" -- a player
    bar) or matches `exclude` (a sidebar entry) never wins.
    """
    wanted = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _KIND_WORDS]
    if not wanted:
        # Nothing to go on ("play the song": both words only say what kind of thing).
        return None
    eligible = [name for name in names
                if not (prefix and (not name.startswith(prefix) or not name[len(prefix):].strip()))
                and not (exclude and re.search(exclude, name))]
    for name in eligible:
        words = set(re.findall(r"[a-z0-9]+", name[len(prefix):].lower()))
        if any(_sounds_like(w, words) for w in wanted):
            return name
    # Only safe once the page is known to show *this* search (wait_value): then the app's
    # own top result is its best guess at a name we heard, which beats failing.
    return eligible[0] if fallback_first and eligible else None


def wait_value(title: str, name: str, contains: str, timeout: float = 8,
               reopen: Callable[[], None] | None = None) -> str:
    """Wait for a control (a search box) to show `contains`, re-sending once if it doesn't.

    Spotify sometimes ignores a spotify:search: link when it's already open, and keeps
    showing the previous search: "play tera didarva" then looked through the results for
    "didarwa", found nothing, and failed. Reading the box says which search is on screen.
    """
    want = " ".join(contains.lower().split())
    deadline = time.time() + timeout
    halfway = time.time() + timeout / 2
    value = ""
    while time.time() <= deadline:
        try:
            value = read(title, name)
        except Exception:
            value = ""
        if want in " ".join(value.lower().split()):
            return value
        if reopen is not None and time.time() >= halfway:
            reopen()
            reopen = None
        time.sleep(0.4)
    raise AutomationError(f"'{name}' in {title} still shows '{value}', not '{contains}'")


def click_best(title: str, query: str, prefix: str = "", control_type: str = "", exclude: str = "",
               timeout: float = 5, fallback_first: bool = False) -> str:
    """Click the control that best matches `query`: the search result someone asked for,
    rather than the first control with the right word in it.

    Spotify's "click the button named Play" pressed the player bar whenever playback was
    paused, which resumed the old track; the results are named "Play <title>".
    """
    deadline = time.time() + timeout
    missed = ""
    while True:
        names = [c.name for c in list_controls(title, prefix.strip(), control_type, limit=200)]
        name = best_match(names, query, prefix, exclude, fallback_first)
        if name:
            try:
                return click(title, name, control_type=control_type, timeout=1.5, exact=True)
            except AutomationError as exc:
                # Gone between the listing and the click: the results were being redrawn
                # (a new search replacing the last one). List again rather than fail.
                missed = f" ('{name}' disappeared: {exc})"
        if time.time() > deadline:
            break
        time.sleep(0.4)
    raise AutomationError(f"nothing in {title} matches '{query}'{missed}")


def wait_title(title: str, changed_from: str = "", ignore: str = "", timeout: float = 10) -> str:
    """Wait for a window's title to become something new, and return it.

    Players put what is playing in their title ("Ghulam Ali - Chupke Chupke"), so this is
    how an automation checks it worked instead of announcing success it never saw.
    """
    deadline = time.time() + timeout
    current = ""
    while time.time() <= deadline:
        try:
            current = find_window(title).window_text()
        except Exception:
            current = ""
        if current and current != changed_from and not (ignore and re.search(ignore, current)):
            return current
        time.sleep(0.4)
    raise AutomationError(f"{title} still says '{current}' after {timeout:.0f}s")


def type_text(title: str, text: str, into: str = "", auto_id: str = "") -> None:
    if into or auto_id:
        control = find_control(title, into, auto_id, control_type="Edit")
        try:
            control.set_edit_text(text)
            return
        except Exception:
            control.set_focus()
            control.type_keys(text, with_spaces=True, with_newlines=True, pause=0.01)
            return
    win = focus(title)
    win.type_keys(text, with_spaces=True, with_newlines=True, pause=0.01)


def send_keys(title: str, keys: str) -> None:
    """pywinauto key syntax: ^ Ctrl, % Alt, + Shift, {ENTER}, {TAB}... (media keys: use press_vk)."""
    from pywinauto.keyboard import send_keys as _send

    if title:
        focus(title)
        time.sleep(0.15)
    _send(keys, pause=0.02)


def press_vk(code: int) -> None:
    """Tap a virtual key system-wide (media and volume keys go to whatever is playing, no focus needed)."""
    import ctypes

    _attach_default_desktop()
    keyup = 0x0002
    ctypes.windll.user32.keybd_event(code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(code, 0, keyup, 0)


def read(title: str, name: str = "", auto_id: str = "") -> str:
    control = find_control(title, name, auto_id)
    for getter in ("get_value", "window_text"):
        try:
            value = getattr(control, getter)()
            if value:
                return str(value)
        except Exception:
            continue
    return control.element_info.name or ""
