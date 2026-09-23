"""Scripted web actions with Playwright, driving your installed Chrome (or Edge).

Chrome is preferred because it is the browser you actually use; Edge is the fallback
when Chrome isn't installed. Either is started once with a DevTools port and a
dedicated Nova profile (%LOCALAPPDATA%\\Nova\\browser-profile), Playwright attaches
over CDP, acts, and detaches: the window stays open afterwards like a normal browser.

**Why a separate profile rather than your own.** Modern Chrome refuses to enable the
DevTools port when it is pointed at the default user-data directory -- a deliberate
measure against cookie theft, since anything that can speak CDP to your real profile
can read every session you are signed into. So automation cannot attach to your Default
profile at all, and a dedicated one is the only option here. Sign in once inside the
Nova profile and those logins persist for automations.

That leaves the two jobs split, which is the right split anyway: *showing* you a page
belongs in your real Chrome with all your logins (a plain `Start-Process chrome <url>`,
no DevTools port needed), while *reading and clicking* a page happens here.

Playwright's sync API is bound to the thread that started it, so create and use a
Browser from one thread (the automation runner does).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("nova.browser")

# Trusted-user tool: the browser runs with a normal (unsandboxed) profile under this account,
# and automations can act on any page it can reach. Not for multi-user or remote use.

CDP_PORT = 9333
# Chrome first: it is the browser actually in use here. Edge is the fallback, since it
# is on every Windows machine and speaks the same DevTools protocol.
BROWSER_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Nova" / "browser-profile"


class BrowserError(RuntimeError):
    pass


def _cdp_ready() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1)
        return True
    except OSError:
        return False


def _launch_browser() -> None:
    exe = next((p for p in BROWSER_CANDIDATES if p.exists()), None)
    if not exe:
        raise BrowserError("Neither Chrome nor Edge could be found.")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(
        [str(exe), f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while not _cdp_ready():
        if time.time() > deadline:
            raise BrowserError(f"{exe.stem} started but its DevTools port never opened.")
        time.sleep(0.25)


class Browser:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    # -- connection -----------------------------------------------------------------------
    @property
    def page(self):
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception as exc:  # the browser was closed under us
                log.debug("previous page is gone (%s); reconnecting", exc)
        self._connect()
        return self._page

    def _connect(self) -> None:
        from playwright.sync_api import sync_playwright

        if not _cdp_ready():
            _launch_browser()
        if self._playwright is None:
            self._playwright = sync_playwright().start()  # spawns a driver process: reused after this
        self._browser = self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        # Internal pages are not the page the user means, whichever browser this is.
        pages = [p for p in context.pages
                 if not p.url.startswith(("devtools://", "edge://", "chrome://", "chrome-extension://"))]
        self._page = pages[-1] if pages else context.new_page()
        self._page.bring_to_front()

    def release(self) -> None:
        """Finish a run. The Playwright driver stays connected for the next one:
        starting it costs a process launch, and the browser window is unaffected either way."""
        self._page = None

    def detach(self) -> None:
        """Fully disconnect (on shutdown). The browser window stays open."""
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:
                log.debug("Playwright didn't stop cleanly: %s", exc)
        self._playwright = self._browser = self._page = None

    # -- actions --------------------------------------------------------------------------
    def goto(self, url: str, new_tab: bool = False) -> str:
        if new_tab and self._page is not None:
            self._page = self._page.context.new_page()
        page = self.page
        page.goto(url, wait_until="domcontentloaded")
        return page.title()

    def _locate(self, text: str = "", selector: str = "", role: str = "", name: str = "",
                label: str = "", placeholder: str = ""):
        page = self.page
        if selector:
            return page.locator(selector).first
        if role:
            return page.get_by_role(role, name=name or None).first
        if label:
            return page.get_by_label(label).first
        if placeholder:
            return page.get_by_placeholder(placeholder).first
        if text:
            return page.get_by_text(text).first
        raise BrowserError("Give text, selector, role, label or placeholder.")

    def click(self, timeout: float = 10, **target: str) -> None:
        self._locate(**target).click(timeout=timeout * 1000)

    def fill(self, value: str, press_enter: bool = False, timeout: float = 10, **target: str) -> None:
        locator = self._locate(**target)
        locator.fill(value, timeout=timeout * 1000)
        if press_enter:
            locator.press("Enter")

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    def wait_for(self, timeout: float = 15, **target: str) -> None:
        self._locate(**target).wait_for(state="visible", timeout=timeout * 1000)

    def read(self, timeout: float = 10, **target: str) -> str:
        return self._locate(**target).inner_text(timeout=timeout * 1000).strip()

    def text(self, limit: int = 4000) -> str:
        """Everything readable on the page, for when there is no selector to aim at.

        This is what a model needs to answer "what is on this page" -- a directory of
        businesses, a contact page -- where the useful thing is the prose and not one
        known element. Capped, because a search results page can run to tens of
        thousands of characters and none of the tail is the answer.
        """
        body = self.page.inner_text("body") or ""
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n\s*\n+", "\n", body).strip()
        if len(body) > limit:
            body = body[:limit].rsplit("\n", 1)[0] + "\n..."
        return body


# One Browser per thread. Playwright's sync API is bound to the thread that started it,
# and each agent task runs on its own thread, so a shared instance would be used from
# the wrong one and fail in a way that looks like the browser being broken.
_local = threading.local()


def thread_browser() -> "Browser":
    existing = getattr(_local, "browser", None)
    if existing is None:
        existing = Browser()
        _local.browser = existing
    return existing
