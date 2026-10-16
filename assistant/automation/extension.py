"""Driving your real Chrome through the Nova extension (extension/ in this repo).

The Playwright path in browser.py works, but in a separate Nova profile -- Chrome won't
open a DevTools port on your own -- so none of your logins are there, and a model reads
pages as walls of text. The extension fixes both:

* It runs inside your own Chrome profile and connects *out* to Nova's server
  (`/ext` on the canvas port, same token), so there is no debugging port at all.
* A page reaches the model as a short numbered list of what can be acted on --
  `[7] textbox "Email" *` -- and the model answers with the number. After an action
  only what changed comes back. A form that took several thousand characters of page
  text per step now takes a few hundred.

`BRIDGE` is the one connection, filled in by the canvas server when the extension
connects. `ExtensionBrowser` has the same methods as `browser.Browser`, so automations
and the browse_* tools use whichever is available (`active_browser`), and the extension
adds the ref-based calls on top.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import json
import logging
import re
import threading
import time
from typing import Any, Callable

log = logging.getLogger("nova.extension")


class ExtensionError(RuntimeError):
    pass


class ExtensionBridge:
    """The extension's WebSocket, callable from any thread.

    Requests go out with an id and wait on a Future; the server's receive loop
    resolves it. Only one extension is listened to at a time: a newer connection (Chrome
    restarted, the worker woke up) replaces the old one.
    """

    def __init__(self) -> None:
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[int, concurrent.futures.Future] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.info: dict[str, Any] = {}
        self.driver = "auto"          # [browser] driver: auto | extension | playwright
        # Told about every call, so you can see the extension doing the work (app.py puts
        # it in the activity feed). Gets one line, e.g. "click [7] (0.4s)", and ok.
        self.on_call: Callable[[str, bool], None] | None = None
        # Told the size of what went back to the caller and of the page's full text, so
        # the usage ledger can count what the map saved (assistant/usage).
        self.on_measure: Callable[[str, int, int], None] | None = None
        self.calls = 0
        self.last = ""

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def usable(self) -> bool:
        return self.connected and self.driver != "playwright"

    # -- server side (runs on the server's event loop) ------------------------------
    async def serve(self, websocket: Any) -> None:
        """Own an accepted WebSocket until it closes."""
        previous = self._ws
        self._ws, self._loop = websocket, asyncio.get_running_loop()
        if previous is not None:
            try:
                await previous.close(code=1000)
            except Exception:
                pass
        log.info("Chrome extension connected")
        try:
            while True:
                self.receive(await websocket.receive_text())
        except Exception as exc:  # WebSocketDisconnect, or a broken frame
            log.debug("extension socket ended: %s", exc)
        finally:
            if self._ws is websocket:
                self._ws = None
                self._fail_all("The Nova extension disconnected.")
                log.info("Chrome extension disconnected")

    def receive(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if message.get("type") == "hello":
            self.info = {"version": message.get("version"), "agent": message.get("agent"), "since": time.time()}
            return
        if "id" not in message:
            return  # ping
        with self._lock:
            future = self._pending.pop(message["id"], None)
        if future is None or future.done():
            return
        if message.get("ok"):
            future.set_result(message.get("result") or {})
        else:
            future.set_exception(ExtensionError(str(message.get("error") or "the extension failed")))

    def _fail_all(self, reason: str) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(ExtensionError(reason))

    # -- caller side (any thread except the server's loop) ---------------------------
    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 45) -> dict[str, Any]:
        ws, loop = self._ws, self._loop
        if ws is None or loop is None:
            raise ExtensionError("The Nova extension isn't connected. Open Chrome, or check the extension's popup.")
        ident = next(self._ids)
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            self._pending[ident] = future
        payload = json.dumps({"id": ident, "method": method, "params": params or {}})
        asyncio.run_coroutine_threadsafe(ws.send_text(payload), loop)
        started, ok, failure = time.time(), False, ""
        try:
            result = future.result(timeout)
            ok = True
            self._measure(method, result)
            return result
        except concurrent.futures.TimeoutError:
            failure = f"Chrome didn't answer '{method}' within {timeout:.0f}s."
            raise ExtensionError(failure) from None
        except ExtensionError as exc:
            failure = str(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(ident, None)
            self._report(method, params or {}, ok, time.time() - started, failure)

    def _measure(self, method: str, result: dict[str, Any]) -> None:
        page = result.get("pageChars")
        if self.on_measure is None or not isinstance(page, int):
            return
        sent = len(str(result.get("page") or result.get("text") or ""))
        try:
            self.on_measure(method, sent, page)
        except Exception as exc:
            log.debug("on_measure failed: %s", exc)

    def _report(self, method: str, params: dict[str, Any], ok: bool, seconds: float, failure: str) -> None:
        if method in ("status",):
            return
        line = f"{method} {describe(params)}".strip() + f" ({seconds:.1f}s)"
        if not ok:
            line += f": {failure[:120]}"
        self.calls += 1
        self.last = line
        if self.on_call:
            try:
                self.on_call(line, ok)
            except Exception as exc:  # reporting must never break the call
                log.debug("on_call failed: %s", exc)


BRIDGE = ExtensionBridge()


def describe(params: dict[str, Any]) -> str:
    """A few words for the feed: the URL, the ref, or the words it went by."""
    if params.get("url"):
        return str(params["url"])[:80]
    if params.get("fields"):
        return f"{len(params['fields'])} fields" + (f", submit [{params['submit_ref']}]" if params.get("submit_ref") else "")
    target = params.get("target") or params
    for key in ("ref", "text", "label", "placeholder", "selector", "query", "key"):
        if isinstance(target, dict) and target.get(key) not in (None, ""):
            value = target[key]
            return f"[{value}]" if key == "ref" else f'"{str(value)[:50]}"'
    return ""


def ref_number(ref: Any) -> int | None:
    """Models write refs as 7, "7", "[7]" or "ref 7". All the same element."""
    if ref is None or ref == "":
        return None
    digits = re.sub(r"\D", "", str(ref))
    return int(digits) if digits else None


def _target(ref: Any = None, **target: str) -> dict[str, Any]:
    out: dict[str, Any] = {k: v for k, v in target.items() if v}
    number = ref_number(ref)
    if number is not None:
        out = {"ref": number}
    return out


class RiskyAction(ExtensionError):
    """A click that could pay, send or delete, asked for without the user's go-ahead."""

    def __init__(self, line: str) -> None:
        super().__init__(f"{line} looks like it pays, sends, submits or deletes something. "
                         "Ask the user first; call again with confirmed=true once they say yes.")
        self.line = line


class ExtensionBrowser:
    """`browser.Browser`'s interface, carried out by the extension in your own Chrome."""

    maps_pages = True  # the browse_* tools return element maps instead of page text

    def __init__(self, bridge: ExtensionBridge | None = None) -> None:
        self.bridge = bridge or BRIDGE

    def _call(self, method: str, _wait: float = 45, **params: Any) -> dict[str, Any]:
        result = self.bridge.call(method, params, timeout=_wait)
        if result.get("risky"):
            raise RiskyAction(str(result.get("page", "That")))
        return result

    # -- browser.Browser's interface (automations use these) ------------------------
    def goto(self, url: str, new_tab: bool = False) -> str:
        page = self._call("open", url=url, new_tab=new_tab).get("page", "")
        return page.split("\n", 1)[0]

    def click(self, timeout: float = 10, **target: str) -> None:
        self.wait_for(timeout, **target)
        self._call("click", target=_target(**target))

    def fill(self, value: str, press_enter: bool = False, timeout: float = 10, **target: str) -> None:
        self.wait_for(timeout, **target)
        self._call("fill", target=_target(**target), value=value, submit=press_enter)

    def press(self, key: str) -> None:
        self._call("press", key=key)

    def upload(self, files: list[str], selector: str = "input[type=file]", timeout: float = 30) -> str:
        """Local files into the page's file input (through shadow roots), as if picked by hand."""
        return self._call("upload", _wait=timeout, files=[str(f) for f in files], selector=selector).get("page", "")

    def wait_for(self, timeout: float = 15, **target: str) -> None:
        self._call("wait", _wait=timeout + 10, target=_target(**target), timeout=timeout)

    def read(self, timeout: float = 10, **target: str) -> str:
        self.wait_for(timeout, **target)
        return self._call("read", **_target(**target)).get("text", "").strip()

    def text(self, limit: int = 4000) -> str:
        return self._call("read", limit=limit).get("text", "")

    def release(self) -> None:
        pass

    def detach(self) -> None:
        pass

    # -- what the extension adds: maps, refs, diffs ----------------------------------
    def open_page(self, url: str, new_tab: bool = False) -> str:
        return self._call("open", url=url, new_tab=new_tab).get("page", "")

    def snapshot(self, all_elements: bool = False) -> str:
        return self._call("snapshot", all=all_elements).get("page", "")

    def act_click(self, ref: Any = None, guard: bool = True, trusted: bool = False, **target: str) -> str:
        t = _target(ref, **target)
        if trusted:
            t["trusted"] = True
        result = self._call("click", target=t, guard=guard)
        return finish_click(result, lambda: self.bridge.call("changes")).get("page", "")

    def act_fill(self, value: str, ref: Any = None, submit: bool = False, **target: str) -> str:
        return self._call("fill", target=_target(ref, **target), value=value, submit=submit).get("page", "")

    def act_form(self, fields: list[dict[str, Any]], submit_ref: Any = None, guard: bool = True) -> str:
        rows = []
        for field in fields:
            number = ref_number(field.get("ref"))
            if number is None:
                raise ExtensionError(f"A form field needs a ref: {field}")
            rows.append({"ref": number, "value": str(field.get("value", ""))})
        return self._call("form", fields=rows, submit_ref=ref_number(submit_ref), guard=guard).get("page", "")

    def act_press(self, key: str, trusted: bool = False) -> str:
        return self._call("press", key=key, trusted=trusted).get("page", "")

    def scroll(self, direction: str = "down", ref: Any = None) -> str:
        return self._call("scroll", direction=direction, target=_target(ref) or None).get("page", "")

    def find(self, query: str) -> str:
        return self._call("find", query=query).get("text", "")

    def read_text(self, limit: int = 3000, ref: Any = None, **target: str) -> str:
        return self._call("read", limit=limit, **_target(ref, **target)).get("text", "")

    def back(self) -> str:
        return self._call("back").get("page", "")

    def tabs(self) -> str:
        return self._call("tabs").get("text", "")

    def use_tab(self, tab: int) -> str:
        return self._call("use_tab", tab=int(tab)).get("page", "")


# App links a page may hand to Windows. Anything else (search-ms:, ms-settings:, file:...)
# is refused: a web page should not be able to launch arbitrary handlers on this PC.
APP_SCHEMES = {"whatsapp", "mailto", "tel", "sms", "spotify", "zoommtg", "zoomus", "msteams", "slack",
               "tg", "skype", "discord", "facetime", "callto"}
NO_CHANGE = "No visible change."


def open_app_link(url: str) -> str:
    scheme = url.split(":", 1)[0].lower()
    if scheme not in APP_SCHEMES:
        raise ExtensionError(f"I won't hand a {scheme}: link from a web page to Windows.")
    import os

    os.startfile(url)  # Windows' own handler: the WhatsApp app, the mail app, ...
    return f"Opened it in the {scheme} app."


def finish_click(result: dict[str, Any], changes: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """The click ladder's last rungs, which need Windows rather than Chrome.

    The extension has already tried a scripted click and a real mouse click
    (background.js). What is left: an app link opened through Windows, and a click
    through UI Automation, which Chrome treats as a person -- the one that worked when a
    WhatsApp button ignored both of the others. Free of tokens: the model only ever sees
    the outcome.
    """
    if result.get("external"):
        try:
            result["page"] = f"{result.get('page', '')} {open_app_link(result['external'])}".strip()
            result["method"] = "app"
        except (ExtensionError, OSError) as exc:
            result["page"] = f"{result.get('page', '')} {exc}".strip()
        return result
    if not result.get("unchanged") or result.get("sensitive") or not result.get("name"):
        return result
    try:
        from . import desktop

        desktop.click(str(result.get("tabTitle") or "Chrome"), str(result["name"]), timeout=3)
    except Exception as exc:
        result["page"] = (f"{result.get('page', '')}\nA page click, a real mouse click and Windows accessibility "
                          f"all failed ({str(exc)[:120]}). Try another element, or ask the user.")
        return result
    result["method"] = "uia"
    after = changes()
    if after.get("full") or (after.get("page") and after.get("page") != NO_CHANGE):
        return {**result, "unchanged": False,
                "page": f"The page ignored scripted and real clicks, so I pressed '{result['name']}' through "
                        f"Windows accessibility.\n{after.get('page', '')}"}
    result["page"] = (f"{result.get('page', '')}\nWindows accessibility pressed it too. Nothing changed on the "
                      "page, so it may have opened an app or started a download; check before trying again.")
    return result


def active_browser():
    """The extension when it's connected (and allowed), otherwise the Playwright browser
    for this thread. Callers that need refs check `getattr(browser, "maps_pages", False)`."""
    if BRIDGE.usable():
        return ExtensionBrowser(BRIDGE)
    if BRIDGE.driver == "extension":
        raise ExtensionError("The Nova extension isn't connected, and [browser] driver says to use only it.")
    from .browser import thread_browser

    return thread_browser()
