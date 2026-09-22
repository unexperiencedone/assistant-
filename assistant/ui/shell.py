"""Native app shell: the canvas in a WebView2 window plus a system tray icon.

- Closing the window hides it; Nova keeps running in the tray.
- Tray menu: Show canvas, Mute microphone, Cancel task, Quit.
- Global hotkey (default Ctrl+Alt+N) shows or hides the window.
- The tray icon's ring color follows Nova's status (listening, thinking, working...).

pywebview must own the main thread on Windows, so `run()` blocks there; the
tray icon and hotkey run on their own threads.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from ..events import Event
from ..paths import resource_path
from .icon import make_image
from .win32 import GlobalHotkey, system_prefers_dark

log = logging.getLogger("nova.shell")

if TYPE_CHECKING:
    from ..app import VoiceAssistant


class AppShell:
    def __init__(self, app: "VoiceAssistant", url: str, start_hidden: bool) -> None:
        self.app = app
        self.url = url
        self.start_hidden = start_hidden
        self.name = app.settings.assistant.name
        self.window = None
        self.reader = None   # the always-on-top reader, created on demand
        self.tray = None
        self.hotkey: GlobalHotkey | None = None
        self.visible = not start_hidden
        self._quitting = False
        self._icon_state = ""
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------------
    def run(self) -> None:
        import webview

        self.window = webview.create_window(
            self.name,
            self.url,
            width=1400,
            height=880,
            min_size=(760, 520),
            hidden=self.start_hidden,
            background_color="#131312" if system_prefers_dark() else "#f5f4f0",
        )
        self.window.events.closing += self._on_closing
        self._start_tray()
        self._watch_documents()
        self._start_hotkey()
        icon = resource_path("assistant", "ui", "nova.ico")
        webview.start(gui="edgechromium", icon=str(icon) if icon.exists() else None)
        # webview.start returns once the window is destroyed.
        if self.tray:
            self.tray.stop()
        if self.hotkey:
            self.hotkey.stop()

    # -- the reader ---------------------------------------------------------------------
    def _watch_documents(self) -> None:
        """Open the always-on-top reader when a long answer arrives.

        The window is deliberately a *second* window rather than a view inside the main
        one. The canvas is a live activity graph, which is the right thing while work
        happens and the wrong thing to read a report in; and a reader has to sit over
        whatever you were working in, which a maximised canvas cannot do.

        Resizable with no remembered size, on purpose: how much of the screen a report
        deserves depends on the report, and guessing it back wrongly each time is worse
        than opening at a sensible default.
        """
        settings = getattr(self.app.settings, "reader", None)
        if settings is None or not getattr(settings, "enabled", True):
            return

        def on_document(event) -> None:
            if event.topic == "reader":
                action = event.data.get("action")
                if action == "hide":
                    self.close_reader()
                else:
                    self.show_reader()
                return
            if event.data.get("auto_open", True):
                self.show_reader()

        self.app.bus.subscribe("document", on_document)
        self.app.bus.subscribe("reader", on_document)

    def show_reader(self) -> None:
        """Bring up the reader, or focus it if it is already there."""
        import webview

        settings = self.app.settings.reader
        if self.reader is not None:
            try:
                self.reader.show()
                return
            except Exception:
                self.reader = None      # it was closed under us; make a new one
        try:
            self.reader = webview.create_window(
                f"{self.name} - reader",
                f"{self.url.rstrip('/')}/#reader",
                width=settings.width,
                height=settings.height,
                min_size=(360, 320),
                on_top=True,
                resizable=True,
                background_color="#131312" if system_prefers_dark() else "#f5f4f0",
            )
            self.reader.events.closed += self._on_reader_closed
        except Exception as exc:
            log.warning("couldn't open the reader window: %s", exc)
            self.reader = None

    def close_reader(self) -> None:
        if self.reader is None:
            return
        try:
            self.reader.destroy()
        except Exception:
            pass
        self.reader = None

    def _on_reader_closed(self) -> None:
        self.reader = None

    def stop(self) -> None:
        self._quitting = True
        self.close_reader()
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass

    # -- window ------------------------------------------------------------------------
    def show(self) -> None:
        if not self.window:
            return
        self.window.show()
        self.window.restore()
        # Nudge it to the foreground: Windows won't let background apps steal focus otherwise.
        self.window.on_top = True
        self.window.on_top = False
        self.visible = True

    def hide(self) -> None:
        if self.window:
            self.window.hide()
            self.visible = False

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def _on_closing(self) -> bool:
        if self._quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        if not getattr(self, "_told_about_tray", False) and self.tray:
            self._told_about_tray = True
            try:
                self.tray.notify(f"{self.name} is still running in the tray.", self.name)
            except Exception:
                pass
        return False  # cancel the close: keep running in the tray

    # -- tray ----------------------------------------------------------------------------
    def _start_tray(self) -> None:
        try:
            import pystray
        except ImportError:
            self.app.bus.log("pystray not installed: no tray icon.", "warn")
            return

        app = self.app
        menu = pystray.Menu(
            pystray.MenuItem("Show canvas", lambda: self.show(), default=True),
            pystray.MenuItem("Mute microphone", lambda: self._toggle_mute(),
                             checked=lambda item: bool(app.mic and app.mic.muted.is_set()),
                             enabled=lambda item: app.mic is not None),
            pystray.MenuItem(lambda item: f"Cancel {len(app.runner.active)} tasks" if len(app.runner.active) > 1
                             else "Cancel task",
                             lambda: app.cancel_task(), enabled=lambda item: app.runner.running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Quit {self.name}", lambda: app.quit()),
        )
        self.tray = pystray.Icon("nova", make_image("idle"), f"{self.name}: starting", menu)
        self.tray.run_detached()
        for topic in ("status", "agent"):
            app.bus.subscribe(topic, self._on_bus_event)

    def _toggle_mute(self) -> None:
        self.app.toggle_mute()
        self._refresh_icon()

    def _on_bus_event(self, _event: Event) -> None:
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        if not self.tray:
            return
        app = self.app
        muted = bool(app.mic and app.mic.muted.is_set())
        state = "working" if app.runner.running else app.state.status
        key = f"{state}:{muted}"
        with self._lock:
            if key == self._icon_state:
                return
            self._icon_state = key
        label = "muted" if muted else ("working on a task" if app.runner.running else state)
        self.tray.icon = make_image(state, muted=muted)
        self.tray.title = f"{self.name}: {label}"

    # -- hotkey ---------------------------------------------------------------------------
    def _start_hotkey(self) -> None:
        combo = self.app.settings.ui.hotkey
        if not combo:
            return
        self.hotkey = GlobalHotkey(combo, self.toggle)
        if self.hotkey.start():
            self.app.bus.log(f"Press {combo} to show or hide the canvas.")
        else:
            self.app.bus.log(f"Couldn't register hotkey {combo} (in use or invalid).", "warn")
