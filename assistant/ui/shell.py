"""Native app shell: the canvas in a WebView2 window plus a system tray icon.

- Closing the window hides it; Nova keeps running in the tray.
- Tray menu: Show canvas, Mute microphone, Cancel task, Quit.
- Global hotkey (default Ctrl+Alt+N) shows or hides the window.
- The tray icon's ring color follows Nova's status (listening, thinking, working...).

pywebview must own the main thread on Windows, so `run()` blocks there; the
tray icon and hotkey run on their own threads.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ..events import Event
from ..paths import resource_path
from .icon import make_image
from .win32 import GlobalHotkey, system_prefers_dark

if TYPE_CHECKING:
    from ..app import VoiceAssistant


class AppShell:
    def __init__(self, app: "VoiceAssistant", url: str, start_hidden: bool) -> None:
        self.app = app
        self.url = url
        self.start_hidden = start_hidden
        self.name = app.settings.assistant.name
        self.window = None
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
        self._start_hotkey()
        icon = resource_path("assistant", "ui", "nova.ico")
        webview.start(gui="edgechromium", icon=str(icon) if icon.exists() else None)
        # webview.start returns once the window is destroyed.
        if self.tray:
            self.tray.stop()
        if self.hotkey:
            self.hotkey.stop()

    def stop(self) -> None:
        self._quitting = True
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
