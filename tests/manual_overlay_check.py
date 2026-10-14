"""Manual check for the Ctrl+Alt+H overlay: python tests/manual_overlay_check.py

Needs Nova running (its canvas server). Builds the overlay through the real
AppShell code, summons it over each monitor and checks it covers exactly that
monitor, never goes "not responding", and hides again. Not part of unittest
discovery: it opens real windows.
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from assistant.config import load_settings  # noqa: E402
from assistant.ui import auth  # noqa: E402
from assistant.ui.shell import AppShell  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system-aware, as Nova's window process is
u = ctypes.windll.user32
from assistant.screen_context import physical_pixels  # noqa: E402
settings = load_settings()
from pathlib import Path
token = auth.read(Path(__file__).resolve().parents[1] / settings.ui.token_file)
base = f"http://127.0.0.1:{settings.ui.web_port}/"
url = f"{base}?{auth.QUERY_NAME}={token}" if token else base


class FakeApp:
    def __init__(self):
        self.settings = settings
        settings.assistant.name = "NovaTest"   # its own window titles, apart from the real Nova
        self.opened = []

    def open_overlay(self, context):
        self.opened.append(context)


app = FakeApp()
shell = AppShell(app, url, start_hidden=True)
TITLE = "NovaTest - here"


def rect(h):
    r = wintypes.RECT()
    with physical_pixels():
        u.GetWindowRect(h, ctypes.byref(r))
    return [r.left, r.top, r.right - r.left, r.bottom - r.top]


def monitors():
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)

    def on_monitor(_h, _dc, r, _l):
        rc = r.contents
        found.append([rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top])
        return 1

    with physical_pixels():
        u.EnumDisplayMonitors(None, None, proc(on_monitor), 0)
    return found


def scenario():
    time.sleep(2)
    t = time.perf_counter()
    shell._ensure_overlay()
    print(f"prewarm: hidden overlay built in {time.perf_counter() - t:.2f} s")
    time.sleep(4)   # let the page load, as the 8 s prewarm would
    import assistant.screen_context as sc
    real_rects = sc._foreground_rects
    results = []
    for label, monitor in [(f"monitor {i + 1}", m) for i, m in enumerate(monitors())] + [("monitor 1 again", monitors()[0])]:
        sc._foreground_rects = lambda m=monitor: (m, m)     # pretend the app in front is on that monitor
        t = time.perf_counter()
        shell.summon()
        took = time.perf_counter() - t
        h = u.FindWindowW(None, TITLE)
        hung = False
        for _ in range(15):
            hung |= bool(u.IsHungAppWindow(h))
            time.sleep(0.2)
        got = rect(h)
        frame = app.opened[-1].get("frame")
        lit = None
        if frame:
            from PIL import Image
            lit = Image.open(frame).convert("L").getextrema()[1] > 0   # not a pure black still
        ok = got == monitor and bool(u.IsWindowVisible(h)) and not hung and lit is not False
        print(f"   screen still: {frame or 'none (display asleep?)'} | has content: {lit}")
        print(f"[{label}] summon returned in {took:.2f} s (capture {app.opened[-1].get('took_ms')} ms) | rect {got} "
              f"expected {monitor} | visible {bool(u.IsWindowVisible(h))} | hung {hung} -> {'OK' if ok else 'FAIL'}")
        results.append(ok)
        shell.hide_overlay()
        time.sleep(0.5)
        print(f"   hidden after close: {not u.IsWindowVisible(h)}")
    sc._foreground_rects = real_rects
    print("ALL OK" if all(results) else "SOMETHING FAILED")
    shell.window.destroy()


import webview  # noqa: E402

shell.window = webview.create_window("NovaTest", url, hidden=True)
webview.start(lambda: threading.Thread(target=scenario, daemon=True).start(), gui="edgechromium")
