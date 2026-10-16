"""Automations: scripted action sequences in automations/*.toml, run by voice.

    name = "Calculator demo"
    description = "Adds 5 + 3 by clicking Calculator's buttons"
    phrases = ["calculator demo", "search wikipedia for {query}"]   # {name} captures words

    [[steps]]
    do = "open"            # open | wait_window | focus | click | click_best | type | keys | read
                           # title | wait_title | wait | say
    app = "Calculator"     # browser_goto | browser_click | browser_fill | browser_press
                           # browser_wait | browser_read | media
    optional = false       # true: skip this step instead of stopping if it fails
    ...

Running one costs no Claude usage and takes seconds. Each step is published to
the event bus, so the canvas lights steps up as they run and shows exactly which
step failed and why. String fields may use {placeholders} from the phrase,
earlier `save_as` results, and `{name_url}` (URL-encoded) variants.
"""

from __future__ import annotations

import re
import threading
import time
import tomllib
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Any, Callable

from ..events import EventBus

STEP_LABELS = {
    "open": "Open {app}{target}",
    "wait_window": "Wait for the {title} window",
    "focus": "Focus {window}",
    "click": "Click '{name}{auto_id}' in {window}",
    "find": "Find the {kind} '{query}'",
    "watch": "Keep an eye on it in the background",
    "click_best": "Click the best match for '{query}' in {window}",
    "wait_value": "Wait for '{name}' to show '{contains}'",
    "title": "Note what {window} is showing",
    "wait_title": "Check {window} changed",
    "type": "Type '{text}'",
    "keys": "Press {keys}",
    "read": "Read {name}{auto_id} from {window}",
    "wait": "Wait {seconds}s",
    "say": "Say: {text}",
    "phone": "On the phone: {action}",
    "browser_goto": "Open {url}",
    "browser_click": "Click '{text}{name}{selector}' on the page",
    "browser_fill": "Fill '{label}{placeholder}{selector}' with '{value}'",
    "browser_press": "Press {key} on the page",
    "browser_wait": "Wait for '{text}{selector}' on the page",
    "browser_read": "Read '{text}{selector}' from the page",
    "media": "Media key: {key}",
}
# Windows virtual-key codes. pywinauto's send_keys doesn't know the media keys, so these go through press_vk.
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
              "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}
_FILLER = re.compile(r"^(?:please\s+|can you\s+|could you\s+|hey\s+|ok(?:ay)?\s+|run\s+(?:the\s+)?|do\s+(?:the\s+)?)+", re.I)


class MacroError(RuntimeError):
    pass


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class Macro:
    name: str
    description: str
    phrases: list[str]
    steps: list[dict[str, Any]]
    path: Path
    # Words dropped from every captured value ("play perfect music" -> "perfect"). A value
    # left empty means the phrase didn't match: "play the music" is not a search for "the".
    ignore_words: list[str] = field(default_factory=list)
    # Another automation (its file name, without .toml) that gets the same request when
    # this one fails: Spotify that doesn't start playing hands the song to YT Music.
    fallback: str = ""
    _patterns: list[re.Pattern[str]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        for phrase in self.phrases + [self.name, self.path.stem.replace("_", " ")]:
            parts = re.split(r"(\{\w+\})", _normalize(phrase))
            regex = "".join(f"(?P<{p[1:-1]}>.+?)" if re.fullmatch(r"\{\w+\}", p) else re.escape(p) for p in parts)
            self._patterns.append(re.compile(rf"^{regex}(?:\s+(?:automation|macro|script|routine))?$", re.I))

    def match(self, utterance: str) -> dict[str, str] | None:
        text = _FILLER.sub("", _normalize(utterance))
        for pattern in self._patterns:
            m = pattern.match(text)
            if m:
                values = {k: self._clean(v) for k, v in m.groupdict().items()}
                if all(values.values()):
                    return values
        return None

    def _clean(self, value: str) -> str:
        drop = {w.lower() for w in self.ignore_words}
        return " ".join(w for w in value.split() if w.lower() not in drop)

    def describe(self, step: dict[str, Any], values: dict[str, str]) -> str:
        template = STEP_LABELS.get(step.get("do", ""), step.get("do", "step"))
        fields = _SafeDict({k: _render(str(v), values) for k, v in step.items()})
        fields["target"] = f" {fields['target']}" if fields.get("target") and not fields.get("app") else ""
        if step.get("label"):
            return step["label"]
        label = " ".join(template.format_map(fields).replace("''", "").split())
        # Values saved by earlier steps don't exist yet when the plan is drawn.
        return re.sub(r"\{(\w+)\}", lambda m: m.group(1).replace("_", " "), label)


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s{}'-]", " ", text.lower()).split())


def _render(template: str, values: dict[str, str]) -> str:
    """Fill {placeholders} we have values for; leave everything else, e.g. key names like {ENTER}."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def load_macros(folder: Path) -> list[Macro]:
    macros = []
    for path in sorted(folder.glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            steps = data.get("steps", [])
            unknown = [s.get("do") for s in steps if s.get("do") not in STEP_LABELS]
            if unknown:
                raise MacroError(f"unknown step types {unknown}")
            macros.append(Macro(data.get("name", path.stem), data.get("description", ""),
                                list(data.get("phrases", [])), steps, path,
                                list(data.get("ignore_words", [])), str(data.get("fallback", ""))))
        except (OSError, tomllib.TOMLDecodeError, MacroError) as exc:
            print(f"[automations] skipping {path.name}: {exc}")
    return macros


class MacroRunner:
    """Runs one automation at a time on its own thread (UI Automation and Playwright
    both need a consistent thread), publishing progress for the canvas."""

    def __init__(self, bus: EventBus, open_app: Callable[[str], bool], say: Callable[[str], None],
                 phone: Any = None, watches: Any = None) -> None:
        self.bus = bus
        self.open_app = open_app
        self.say = say
        self.phone = phone  # assistant.phone.PhoneBridge, for do = "phone" steps
        self.watches = watches  # assistant.automation.watch.WatchService, for do = "watch" steps
        self._busy = threading.Lock()
        self._browser = None  # kept between runs: connecting costs a driver process launch
        self._local = None    # the local index, for `find` steps
        # Every run happens on this one thread. Playwright's sync API is bound to the thread
        # that started it, so a kept browser used from a fresh thread per run fails with
        # "cannot switch to a different thread (which happens to have exited)".
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="automation")
        self._worker_ident: int | None = None

    @property
    def running(self) -> bool:
        return self._busy.locked()

    def start(self, macro: Macro, params: dict[str, str], utterance: str = "") -> bool:
        if not self._busy.acquire(blocking=False):
            return False
        self._worker.submit(self._thread_main, macro, params, utterance)
        return True

    def _on_worker(self, fn: Callable[..., Any], *args: Any) -> Any:
        if threading.get_ident() == self._worker_ident:
            return fn(*args)
        return self._worker.submit(fn, *args).result()

    def _thread_main(self, macro: Macro, params: dict[str, str], utterance: str) -> None:
        self._worker_ident = threading.get_ident()
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            self._run(macro, params, utterance)
        finally:
            self._busy.release()

    def run(self, macro: Macro, params: dict[str, str], utterance: str = "") -> tuple[bool, str]:
        return self._on_worker(self._run, macro, params, utterance)

    def _run(self, macro: Macro, params: dict[str, str], utterance: str = "",
             fell_from: str = "") -> tuple[bool, str]:
        self._worker_ident = threading.get_ident()
        values: dict[str, str] = {}
        for key, value in params.items():
            values[key] = value
            values[f"{key}_url"] = urllib.parse.quote_plus(value)
        labels = [macro.describe(step, values) for step in macro.steps]
        self.bus.publish("route", utterance=utterance or macro.name, route="macro", name=macro.name, steps=labels)
        started = time.time()
        browser = None  # chosen at the first browser step, then kept for the whole run
        said = ""
        try:
            for index, step in enumerate(macro.steps):
                self.bus.publish("macro_step", index=index, status="running", detail="")
                try:
                    if step["do"].startswith("browser_") and browser is None:
                        browser = self._pick_browser()
                    detail = self._execute(step, values, browser) or ""
                except Exception as exc:  # report which step failed and why, then stop
                    message = str(exc).splitlines()[0][:200] or exc.__class__.__name__
                    if step.get("optional"):  # e.g. a button that only exists at some window sizes
                        self.bus.publish("macro_step", index=index, status="done", detail=f"skipped: {message}")
                        continue
                    self.bus.publish("macro_step", index=index, status="failed", detail=message)
                    summary = f"{macro.name} stopped at step {index + 1}: {message}"
                    backup = self._fallback(macro) if not fell_from else None
                    if backup is not None:
                        # No announcement: the request quietly goes to the other automation,
                        # and only if that fails too does anyone hear about it.
                        self.bus.log(f"{summary}; trying {backup.name} instead", "warn")
                        return self._run(backup, params, utterance, fell_from=macro.name)
                    # Structured fields (name/path/step/error) let Controller fall back to the
                    # agent for this one request, then ask it to patch the automation file.
                    self.bus.publish("macro_done", ok=False, text=summary, name=macro.name,
                                     path=str(macro.path), step=index + 1, error=message)
                    self.bus.log(summary, "warn")
                    self.say(f"{macro.name} failed at step {index + 1}. The canvas shows why.")
                    return False, summary
                self.bus.publish("macro_step", index=index, status="done", detail=detail)
                if step["do"] == "say":
                    said = detail
            summary = f"{macro.name} finished in {time.time() - started:.1f}s."
            # What it ended up saying is the proof of what happened ("Playing Pritam - Tera
            # Deedar Hua"). Without it, a model that ran this as a tool went looking for a
            # "Spotify" window, found none -- the title is the song -- and played it again.
            if said:
                summary += f' It said: "{said}". That is done; don\'t repeat it.'
            self.bus.publish("macro_done", ok=True, text=summary, name=macro.name)
            return True, summary
        finally:
            if self._browser is not None:
                self._browser.release()  # keep the connection for the next automation

    @staticmethod
    def _fallback(macro: Macro) -> Macro | None:
        if not macro.fallback:
            return None
        path = macro.path.parent / f"{macro.fallback}.toml"
        found = [m for m in load_macros(macro.path.parent) if m.path == path] if path.exists() else []
        if not found:
            print(f"[automations] {macro.name} names a fallback {path.name} that doesn't load")
        return found[0] if found else None

    def _find(self, query: str, kind: str = "folder") -> str | None:
        """The best indexed match for `query`, opened on first use (the index is local)."""
        if self._local is None:
            from ..config import load_settings
            from ..system import LocalSystem

            settings = load_settings()
            self._local = LocalSystem(settings.local, settings.base_dir)
        hits = self._local.find(query, kind=kind or None, limit=1)
        return str(hits[0].path) if hits else None

    def _pick_browser(self):
        """Your own Chrome through the Nova extension when it's connected; otherwise the
        Playwright browser, kept between runs because connecting costs a driver launch."""
        from .extension import BRIDGE, ExtensionBrowser

        if BRIDGE.usable():
            return ExtensionBrowser()
        if BRIDGE.driver == "extension":
            raise RuntimeError("The Nova extension isn't connected, and [browser] driver says to use only it.")
        if self._browser is None:
            from .browser import Browser

            self._browser = Browser()
        return self._browser

    def close(self) -> None:
        def detach() -> None:
            if self._browser is not None:
                self._browser.detach()
                self._browser = None

        self._on_worker(detach)
        self._worker.shutdown(wait=False)

    def _execute(self, step: dict[str, Any], values: dict[str, str], browser) -> str:
        from . import desktop

        s = {k: _render(v, values) if isinstance(v, str) else v for k, v in step.items()}
        kind = s["do"]
        timeout = float(s.get("timeout", 10))
        if kind == "watch":
            # Hand the rest to the watch service and return: the app does the work in the
            # background, and Nova says when it is done or needs you (automation/watch.py).
            if self.watches is None:
                return "not watched (no watch service in this run)"
            from .watch import WatchSpec

            def listed(key: str) -> list[str]:
                return [_render(str(p), values) for p in s.get(key, [])]

            job = self.watches.start(WatchSpec(
                label=s.get("job") or s.get("window", "the app"),   # how it is named out loud
                window=s["window"], working=listed("working"), done=listed("done"),
                needs_you=listed("needs_you"), review=listed("review"), folder=s.get("folder", ""),
                max_minutes=float(s.get("max_minutes", 45))))
            return f"watching in the background ({job.id})"
        if kind == "find":
            # A folder or file by the name it was called, from the local index: what
            # "open the school website in antigravity" needs before anything opens it.
            path = self._find(s["query"], s.get("kind", "folder"))
            if not path:
                raise MacroError(f"couldn't find a {s.get('kind', 'folder')} called {s['query']}")
            name = s.get("save_as", "found")
            values[name] = path
            values[f"{name}_name"] = Path(path).name
            return path
        if kind == "open" and s.get("program"):
            # A program with arguments ("antigravity-ide -n <folder>"); never a shell, so a
            # slot value can only ever be an argument, not a second command.
            import os
            import subprocess

            program = os.path.expandvars(s["program"])
            args = [_render(str(a), values) for a in s.get("args", [])]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen([program, *args], creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"started {Path(program).stem} {' '.join(args)}"
        if kind == "open":
            if s.get("app"):
                if not self.open_app(s["app"]):
                    raise MacroError(f"couldn't find an app called {s['app']}")
                return f"opened {s['app']}"
            from ..system.service import launch

            launch(s["target"])
            return f"opened {s['target']}"
        if kind == "phone":
            # The phone, over Tailscale, without a model in the loop. Destructive actions
            # (a text, a call) are not reachable from here: an automation runs unattended,
            # and those are exactly the things that must be asked about first.
            if self.phone is None or not self.phone.configured:
                raise MacroError("the phone bridge isn't set up (phone/README.md)")
            action = s.get("action", "")
            from ..phone import NEEDS_CONFIRMATION, PhoneError

            if action in NEEDS_CONFIRMATION:
                raise MacroError(f"{action} needs confirming, so it cannot run from an automation")
            args = {k: v for k, v in s.items() if k not in ("do", "action", "label", "optional", "timeout")}
            try:
                result = self.phone.call(action, args)
            except PhoneError as exc:
                raise MacroError(str(exc)) from exc
            return " ".join(str(result.get("output", "")).split())[:200]
        if kind == "wait_window":
            win = desktop.find_window(s["title"], timeout=timeout)
            return win.window_text()
        if kind == "focus":
            return desktop.focus(s["window"]).window_text()
        if kind == "click":
            label = desktop.click(s["window"], s.get("name", ""), s.get("auto_id", ""), s.get("control_type", ""),
                                  int(s.get("index", 0)), timeout, bool(s.get("exact", False)))
            return f"clicked {label}"
        if kind == "click_best":
            # The control that best matches what was asked for (a search result), never
            # a bare prefix-only one like a player bar's "Play" (desktop.best_match).
            label = desktop.click_best(s["window"], s["query"], s.get("prefix", ""), s.get("control_type", ""),
                                       s.get("exclude", ""), timeout, bool(s.get("fallback_first", False)))
            return f"clicked {label}"
        if kind == "wait_value":
            # Proof the screen shows what was asked for (a search box holding the query),
            # re-sending `reopen` once halfway if it doesn't.
            from ..system.service import launch

            again = (lambda: launch(s["reopen"])) if s.get("reopen") else None
            return desktop.wait_value(s["window"], s["name"], s["contains"], timeout, again)
        if kind == "title":
            return self._save(s, values, desktop.find_window(s["window"], timeout=timeout).window_text())
        if kind == "wait_title":
            # Success is what the window says afterwards, not that a click went through.
            value = desktop.wait_title(s["window"], s.get("changed_from", ""), s.get("ignore", ""), timeout)
            return self._save(s, values, value)
        if kind == "type":
            desktop.type_text(s.get("window", ""), s["text"], s.get("into", ""), s.get("auto_id", ""))
            return ""
        if kind == "keys":
            desktop.send_keys(s.get("window", ""), s["keys"])
            return ""
        if kind == "read":
            value = desktop.read(s["window"], s.get("name", ""), s.get("auto_id", ""))
            return self._save(s, values, value)
        if kind == "wait":
            time.sleep(float(s.get("seconds", 1)))
            return ""
        if kind == "say":
            self.say(s["text"])
            return s["text"]
        if kind == "media":
            if s["key"] not in MEDIA_KEYS:
                raise MacroError(f"unknown media key {s['key']}; use {', '.join(MEDIA_KEYS)}")
            desktop.press_vk(MEDIA_KEYS[s["key"]])
            return ""
        target = {k: s[k] for k in ("text", "selector", "role", "name", "label", "placeholder") if s.get(k)}
        if kind == "browser_goto":
            return browser.goto(s["url"], bool(s.get("new_tab", False)))
        if kind == "browser_click":
            if s.get("trusted") and hasattr(browser, "act_click"):
                # A real mouse click through the debugger: a page only starts audio or
                # video for a user gesture, and a scripted click isn't one.
                browser.wait_for(timeout=timeout, **target)
                browser.act_click(trusted=True, guard=False, **target)
                return ""
            browser.click(timeout=timeout, **target)
            return ""
        if kind == "browser_fill":
            browser.fill(s["value"], bool(s.get("press_enter", False)), timeout=timeout, **target)
            return ""
        if kind == "browser_press":
            browser.press(s["key"])
            return ""
        if kind == "browser_wait":
            browser.wait_for(timeout=timeout, **target)
            return ""
        if kind == "browser_read":
            return self._save(s, values, browser.read(timeout=timeout, **target))
        raise MacroError(f"unknown step {kind}")

    def _save(self, step: dict[str, Any], values: dict[str, str], value: str) -> str:
        if step.get("save_as"):
            values[step["save_as"]] = value
        return value[:120]
