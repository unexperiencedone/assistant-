"""Knowing when a job handed to another app is finished, without checking all the time.

An automation can type a prompt into the Antigravity IDE or the Claude app in a few
seconds; the work itself takes minutes. Asking every second wastes the machine, asking
every minute makes you wait, and either way Nova would be guessing. So Windows tells us:

- **WinEvents.** Every app with an accessibility tree (Electron apps included) pushes an
  event when a control appears, vanishes or is renamed. A hook on the app's process marks
  the job "something changed" and costs nothing in between: the Claude app produced 0
  events idle, ~250 while it answered, then 0 again.
- **A look only when the events settle.** The window's controls are read once the burst
  has been quiet for a second (or every 10 s through a long burst), not per event.
- **A slow fallback.** With no events at all, a look at 5 s, then 10, 20, 40, 60: it
  catches a missed event, and it is how a stall is noticed.
- **The folder.** ReadDirectoryChangesW reports each file the agent touches, so the
  report can say what changed.

What counts as working, done, waiting for you, or changes to review is per app -- the
names its controls carry -- and comes from the automation's `watch` step. "Done" is a
*new* done-marker: a reused window still shows the last job's "Worked for 21s".

The job is reported through Nova's live view (watcher.py), so "what are you doing" names
it, and spoken once at each change that matters: waiting for you, finished, stuck.
Nothing here goes through the task runner, so a watched job never blocks a request.
"""

from __future__ import annotations

import ctypes
import itertools
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("nova.watch")

SETTLE = 1.0          # look once events have been quiet this long
BURST_LOOK = 10.0     # ...or at least this often through a long burst
FALLBACK = (5, 10, 20, 40, 60)   # looks with no events at all
STALL = 12.0          # no events this long while working: maybe it is waiting for you
IGNORED_PARTS = {".git", "node_modules", "__pycache__", ".venv", ".antigravity", ".vscode"}


@dataclass
class WatchSpec:
    label: str                     # "Antigravity in school-sample"
    window: str                    # a title, or exe:name
    working: list[str] = field(default_factory=list)    # regexes over control names
    done: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    folder: str = ""
    max_minutes: float = 45


@dataclass
class WatchJob:
    id: str
    spec: WatchSpec
    started: float = field(default_factory=time.time)
    state: str = "working"         # working | needs_you | done | stuck | closed | stopped
    baseline_done: int = 0
    seen_working: bool = False
    dirty: bool = False
    last_event: float = 0.0
    last_look: float = 0.0
    next_fallback: float = 0.0
    backoff: int = 0
    signature: int = 0
    asked: str = ""                # the approval button already reported
    changed: set[str] = field(default_factory=set)
    overdue_said: bool = False
    stop: Callable[[], None] | None = None

    def minutes(self) -> str:
        m = int((time.time() - self.started) // 60)
        return "under a minute" if m < 1 else f"{m} minute{'s' if m != 1 else ''}"


def _count(patterns: list[str], names: list[str]) -> int:
    compiled = [re.compile(p, re.I) for p in patterns]
    return sum(1 for name in names if any(c.search(name) for c in compiled))


def _first(patterns: list[str], names: list[str]) -> str:
    compiled = [re.compile(p, re.I) for p in patterns]
    return next((name for name in names if any(c.search(name) for c in compiled)), "")


# -- Windows: events for one process, changes under one folder ------------------------------
def hook_events(pid: int, on_event: Callable[[], None]) -> Callable[[], None]:
    """Call `on_event` whenever the process's accessibility tree changes. Returns stop()."""
    import ctypes.wintypes as wt

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    proc_type = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)
    callback = proc_type(lambda *args: on_event())
    started = threading.Event()
    holder: dict[str, int] = {}

    def pump() -> None:
        holder["thread"] = kernel32.GetCurrentThreadId()
        # EVENT_OBJECT_CREATE .. EVENT_OBJECT_END: create, destroy, show, hide, name change...
        hook = user32.SetWinEventHook(0x8000, 0x80FF, 0, callback, pid, 0, 0)  # out of context
        started.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWinEvent(hook)

    thread = threading.Thread(target=pump, name=f"winevents-{pid}", daemon=True)
    thread.start()
    started.wait(2)

    def stop() -> None:
        if "thread" in holder:
            user32.PostThreadMessageW(holder["thread"], 0x0012, 0, 0)  # WM_QUIT
        _ = callback  # kept alive until the hook is gone

    return stop


def watch_folder(path: str, on_change: Callable[[str], None]) -> Callable[[], None]:
    """Call `on_change(relative path)` for each file changed under `path`. Returns stop()."""
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = wt.HANDLE
    handle = kernel32.CreateFileW(str(path), 0x0001, 0x7, None, 3, 0x02000000, None)
    if handle in (None, wt.HANDLE(-1).value):
        return lambda: None
    stopping = threading.Event()

    def loop() -> None:
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = wt.DWORD()
        flags = 0x1 | 0x2 | 0x8 | 0x10   # file name, dir name, size, last write
        while not stopping.is_set():
            ok = kernel32.ReadDirectoryChangesW(handle, buffer, len(buffer), True, flags,
                                                ctypes.byref(returned), None, None)
            if not ok or stopping.is_set():
                break
            offset = 0
            while returned.value:
                next_offset, _action, length = (int.from_bytes(buffer.raw[offset + i:offset + i + 4], "little")
                                                for i in (0, 4, 8))
                name = buffer.raw[offset + 12:offset + 12 + length].decode("utf-16-le", "replace")
                if not (set(Path(name).parts) & IGNORED_PARTS):
                    on_change(name)
                if not next_offset:
                    break
                offset += next_offset
        kernel32.CloseHandle(handle)

    threading.Thread(target=loop, name="folder-watch", daemon=True).start()

    def stop() -> None:
        stopping.set()
        kernel32.CancelIoEx(handle, None)

    return stop


# -- the service ------------------------------------------------------------------------------
class WatchService:
    def __init__(self, say: Callable[[str], None], live: Any = None,
                 names: Callable[[str], list[str] | None] | None = None,
                 pid_of: Callable[[str], int | None] | None = None) -> None:
        self.say = say
        self.live = live                   # assistant.watcher.Watcher: "what are you doing"
        self._names = names or _window_names
        self._pid_of = pid_of or _window_pid
        self.jobs: dict[str, WatchJob] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # -- starting and stopping ------------------------------------------------------------
    def start(self, spec: WatchSpec) -> WatchJob:
        job = WatchJob(id=f"w{next(self._ids)}", spec=spec)
        names = self._names(spec.window) or []
        job.baseline_done = _count(spec.done, names)
        job.seen_working = bool(_count(spec.working, names))
        job.signature = hash(tuple(names))
        job.last_look = job.started
        job.next_fallback = job.started + FALLBACK[0]
        stops = []
        pid = self._pid_of(spec.window)
        if pid:
            stops.append(hook_events(pid, lambda j=job: self._event(j)))
        if spec.folder and Path(spec.folder).is_dir():
            stops.append(watch_folder(spec.folder, lambda name, j=job: j.changed.add(name)))
        job.stop = lambda: [s() for s in stops]
        with self._lock:
            self.jobs[job.id] = job
        if self.live is not None:
            self.live.start_external(job.id, spec.label, "watching")
        self._ensure_thread()
        return job

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is not None:
            self._finish(job, "stopped", "")

    def active(self) -> list[WatchJob]:
        with self._lock:
            return [j for j in self.jobs.values() if j.state in ("working", "needs_you")]

    def _event(self, job: WatchJob) -> None:
        job.dirty = True
        job.last_event = time.time()

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="watch", daemon=True)
            self._thread.start()

    # -- the loop: cheap ticks, a real look only when it is worth one ---------------------
    def _loop(self) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()   # UI Automation from this thread
        except Exception:
            pass
        while self.active():
            now = time.time()
            for job in self.active():
                if self._due(job, now):
                    try:
                        self.look(job)
                    except Exception as exc:     # one bad look must not end the watch
                        log.debug("watch look failed: %s", exc)
            time.sleep(0.5)

    @staticmethod
    def _due(job: WatchJob, now: float) -> bool:
        if job.dirty and (now - job.last_event >= SETTLE or now - job.last_look >= BURST_LOOK):
            return True
        return now >= job.next_fallback

    def look(self, job: WatchJob) -> None:
        """Read the window once and decide what state the job is in."""
        spec, now = job.spec, time.time()
        job.dirty, job.last_look = False, now
        names = self._names(spec.window)
        if names is None:
            self._finish(job, "closed", f"{spec.label}: the window was closed before it finished.")
            return
        signature = hash(tuple(names))
        changed, job.signature = signature != job.signature, signature
        job.backoff = 0 if changed else min(job.backoff + 1, len(FALLBACK) - 1)
        job.next_fallback = now + FALLBACK[job.backoff]

        working = _count(spec.working, names) > 0
        job.seen_working = job.seen_working or working
        if _count(spec.done, names) > job.baseline_done and not working:
            self._done(job, names)
            return
        stalled = now - max(job.last_event, job.started) >= STALL
        asking = _first(spec.needs_you, names) if working and stalled else ""
        if asking and asking != job.asked:
            job.asked, job.state = asking, "needs_you"
            self._phase(job, f"waiting for you: {asking}")
            self.say(f"{spec.label} is waiting for you. It wants an answer: {_plain(asking)}.")
        elif job.state == "needs_you" and not asking and changed:
            job.state, job.asked = "working", ""
            self._phase(job, "working again")
        if not job.overdue_said and now - job.started > spec.max_minutes * 60:
            job.overdue_said = True
            self.say(f"{spec.label} has been going for {job.minutes()}. I'll keep watching.")
        if now - job.started > spec.max_minutes * 180:
            self._finish(job, "stuck", f"{spec.label} still hasn't finished after {job.minutes()}, "
                                       "so I've stopped watching it.")

    def _done(self, job: WatchJob, names: list[str]) -> None:
        files = sorted(job.changed)
        sentence = f"{job.spec.label} finished, after {job.minutes()}."
        if files:
            shown = ", ".join(Path(f).name for f in files[:3])
            more = f" and {len(files) - 3} more" if len(files) > 3 else ""
            sentence += f" It changed {len(files)} file{'s' if len(files) != 1 else ''}: {shown}{more}."
        if _first(job.spec.review, names):
            sentence += " Its changes are waiting for you to accept them."
        self._finish(job, "done", sentence)

    def _finish(self, job: WatchJob, state: str, sentence: str) -> None:
        with self._lock:
            if job.state in ("done", "closed", "stuck", "stopped"):
                return
            job.state = state
        if job.stop:
            job.stop()
        if self.live is not None:
            self.live.end_external(job.id, ok=state == "done")
        if sentence:
            self.say(sentence)

    def _phase(self, job: WatchJob, phase: str) -> None:
        if self.live is not None:
            self.live.note_phase(job.id, phase)


def _plain(name: str) -> str:
    """A button's name as words: "Accept Changes Ctrl+⏎" -> "Accept Changes"."""
    return re.sub(r"\s*(?:Ctrl|Alt|Shift)\+\S*", "", name).strip() or name


def _window_names(title: str) -> list[str] | None:
    """Every control name in the window, in order, duplicates kept; None if it is gone."""
    from . import desktop

    try:
        win = desktop.find_window(title)
    except Exception:
        return None
    try:
        return [el.name for el in desktop._snapshot(win) if el.name]
    except Exception:
        return []


def _window_pid(title: str) -> int | None:
    from . import desktop

    try:
        return int(desktop.find_window(title).process_id())
    except Exception:
        return None
