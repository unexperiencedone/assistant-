"""Automations: scripted action sequences in automations/*.toml, run by voice.

    name = "Calculator demo"
    description = "Adds 5 + 3 by clicking Calculator's buttons"
    phrases = ["calculator demo", "search wikipedia for {query}"]   # {name} captures words

    [[steps]]
    do = "open"            # open | wait_window | focus | click | type | keys | read | wait | say
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus

STEP_LABELS = {
    "open": "Open {app}{target}",
    "wait_window": "Wait for the {title} window",
    "focus": "Focus {window}",
    "click": "Click '{name}{auto_id}' in {window}",
    "type": "Type '{text}'",
    "keys": "Press {keys}",
    "read": "Read {name}{auto_id} from {window}",
    "wait": "Wait {seconds}s",
    "say": "Say: {text}",
    "browser_goto": "Open {url}",
    "browser_click": "Click '{text}{name}{selector}' on the page",
    "browser_fill": "Fill '{label}{placeholder}{selector}' with '{value}'",
    "browser_press": "Press {key} on the page",
    "browser_wait": "Wait for '{text}{selector}' on the page",
    "browser_read": "Read '{text}{selector}' from the page",
    "media": "Media key: {key}",
}
MEDIA_KEYS = {"play_pause": "{VK_MEDIA_PLAY_PAUSE}", "next": "{VK_MEDIA_NEXT_TRACK}",
              "previous": "{VK_MEDIA_PREV_TRACK}", "stop": "{VK_MEDIA_STOP}",
              "volume_up": "{VK_VOLUME_UP}", "volume_down": "{VK_VOLUME_DOWN}", "mute": "{VK_VOLUME_MUTE}"}
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
                return {k: v.strip() for k, v in m.groupdict().items()}
        return None

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
                                list(data.get("phrases", [])), steps, path))
        except (OSError, tomllib.TOMLDecodeError, MacroError) as exc:
            print(f"[automations] skipping {path.name}: {exc}")
    return macros


class MacroRunner:
    """Runs one automation at a time on its own thread (UI Automation and Playwright
    both need a consistent thread), publishing progress for the canvas."""

    def __init__(self, bus: EventBus, open_app: Callable[[str], bool], say: Callable[[str], None]) -> None:
        self.bus = bus
        self.open_app = open_app
        self.say = say
        self._busy = threading.Lock()

    @property
    def running(self) -> bool:
        return self._busy.locked()

    def start(self, macro: Macro, params: dict[str, str], utterance: str = "") -> bool:
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._thread_main, args=(macro, params, utterance), name="automation", daemon=True).start()
        return True

    def _thread_main(self, macro: Macro, params: dict[str, str], utterance: str) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            self.run(macro, params, utterance)
        finally:
            self._busy.release()

    def run(self, macro: Macro, params: dict[str, str], utterance: str = "") -> tuple[bool, str]:
        values: dict[str, str] = {}
        for key, value in params.items():
            values[key] = value
            values[f"{key}_url"] = urllib.parse.quote_plus(value)
        labels = [macro.describe(step, values) for step in macro.steps]
        self.bus.publish("route", utterance=utterance or macro.name, route="macro", name=macro.name, steps=labels)
        browser = None
        started = time.time()
        try:
            for index, step in enumerate(macro.steps):
                self.bus.publish("macro_step", index=index, status="running", detail="")
                try:
                    if step["do"].startswith("browser_") and browser is None:
                        from .browser import Browser

                        browser = Browser()
                    detail = self._execute(step, values, browser) or ""
                except Exception as exc:  # report which step failed and why, then stop
                    message = str(exc).splitlines()[0][:200] or exc.__class__.__name__
                    if step.get("optional"):  # e.g. a button that only exists at some window sizes
                        self.bus.publish("macro_step", index=index, status="done", detail=f"skipped: {message}")
                        continue
                    self.bus.publish("macro_step", index=index, status="failed", detail=message)
                    summary = f"{macro.name} stopped at step {index + 1}: {message}"
                    self.bus.publish("macro_done", ok=False, text=summary)
                    self.bus.log(summary, "warn")
                    self.say(f"{macro.name} failed at step {index + 1}. The canvas shows why.")
                    return False, summary
                self.bus.publish("macro_step", index=index, status="done", detail=detail)
            summary = f"{macro.name} finished in {time.time() - started:.1f}s."
            self.bus.publish("macro_done", ok=True, text=summary)
            return True, summary
        finally:
            if browser is not None:
                browser.detach()

    def _execute(self, step: dict[str, Any], values: dict[str, str], browser) -> str:
        from . import desktop

        s = {k: _render(v, values) if isinstance(v, str) else v for k, v in step.items()}
        kind = s["do"]
        timeout = float(s.get("timeout", 10))
        if kind == "open":
            if s.get("app"):
                if not self.open_app(s["app"]):
                    raise MacroError(f"couldn't find an app called {s['app']}")
                return f"opened {s['app']}"
            from ..system.service import launch

            launch(s["target"])
            return f"opened {s['target']}"
        if kind == "wait_window":
            win = desktop.find_window(s["title"], timeout=timeout)
            return win.window_text()
        if kind == "focus":
            return desktop.focus(s["window"]).window_text()
        if kind == "click":
            label = desktop.click(s["window"], s.get("name", ""), s.get("auto_id", ""), s.get("control_type", ""),
                                  int(s.get("index", 0)), timeout)
            return f"clicked {label}"
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
            desktop.send_keys("", MEDIA_KEYS[s["key"]])
            return ""
        target = {k: s[k] for k in ("text", "selector", "role", "name", "label", "placeholder") if s.get(k)}
        if kind == "browser_goto":
            return browser.goto(s["url"], bool(s.get("new_tab", False)))
        if kind == "browser_click":
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
