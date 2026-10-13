"""Fallback ladders: the order to try things in, per kind of request (ladders.toml).

Two halves:

* **Handing out.** `hint(text, backend)` picks the ladder(s) whose words match the request
  (at most two, and a second only when it is nearly as strong) and writes them as a
  short block in front of the prompt -- the command form for Claude Code and
  Antigravity, the tool form for the chat models. A request no ladder matches gets
  nothing, which is the common case for small talk and costs nothing.
* **Learning.** Every agent tool event is already on the bus. For a task that was given a
  ladder, the rungs it actually used are recognised by their `signs`, and when the task
  ends the last rung used is credited (it worked) and the ones before it debited (they
  were tried and abandoned). Kept per target -- the site or window the commands named --
  in data/ladders.json, and fed back as "here, X worked 3 of 3 times: start there".

The ladders are preferences, not scripts: a confident instruction that doesn't fit is
worse than none, so the hint says so, and learned advice appears only with evidence.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import tomllib
from pathlib import Path
from typing import Any

from .events import EventBus

log = logging.getLogger("nova.ladders")

MAX_LADDERS = 2
MIN_EVIDENCE = 2          # successes before a rung is recommended as the place to start
CHAT_BACKENDS = ("groq", "openrouter")
_HOST = re.compile(r"https?://([a-z0-9.-]+)", re.I)
_WINDOW = re.compile(r"\bui (?:click|type|keys|controls|read) \"([^\"]+)\"", re.I)


class LadderService:
    def __init__(self, path: Path, memory_path: Path, bus: EventBus | None = None,
                 main: str = "python main.py", cli: str = "python sysindex.py") -> None:
        self.path = path
        self.memory_path = memory_path
        self.main, self.cli = main, cli
        self._stamp = 0.0
        self.ladders: dict[str, dict[str, Any]] = {}
        self.rules = ""
        self._lock = threading.Lock()
        self._tasks: dict[str, list[str]] = {}        # task id -> ladder kinds it was given
        self._seen: dict[str, list[str]] = {}         # task id -> "<tool> <text>" of each tool call
        self.memory: dict[str, dict[str, dict[str, dict[str, int]]]] = self._load_memory()
        self._reload()
        if bus is not None:
            bus.subscribe("agent", self._on_agent)

    # -- the file -----------------------------------------------------------------------
    def _reload(self) -> None:
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            return
        if stamp == self._stamp:
            return
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("ladders.toml can't be read, keeping the last good one: %s", exc)
            self._stamp = stamp
            return
        self.rules = " ".join(str((data.pop("rules", {}) or {}).get("text", "")).split())
        ladders = {}
        for kind, body in data.items():
            if not isinstance(body, dict) or not body.get("rungs"):
                continue
            words = [w for w in body.get("when", []) if isinstance(w, str) and w.strip()]
            ladders[kind] = {
                "when": [re.compile(rf"\b{w}\b", re.I) for w in words],
                "done": str(body.get("done", "")),
                "stop": str(body.get("stop", "")),
                "rungs": [{"name": str(r.get("name", f"rung {i + 1}")), "cli": str(r.get("cli", "")),
                           "tool": str(r.get("tool", "")),
                           "signs": [re.compile(s, re.I) for s in r.get("signs", []) if s]}
                          for i, r in enumerate(body["rungs"])],
            }
        self.ladders = ladders
        self._stamp = stamp

    # -- handing out --------------------------------------------------------------------
    def kinds_for(self, text: str) -> list[str]:
        self._reload()
        scored = []
        for order, (kind, ladder) in enumerate(self.ladders.items()):
            score = sum(1 for pattern in ladder["when"] if pattern.search(text or ""))
            if score:
                scored.append((-score, order, kind))
        scored.sort()
        if not scored:
            return []
        kinds = [scored[0][2]]
        # A second ladder only when it is about as strongly meant: "open the form and fill
        # it" is both, "find out the latest news" is research and not a file search.
        if len(scored) > 1 and -scored[1][0] >= 2:
            kinds.append(scored[1][2])
        return kinds[:MAX_LADDERS]

    def hint(self, text: str, backend: str = "") -> tuple[str, list[str]]:
        kinds = self.kinds_for(text)
        if not kinds:
            return "", []
        chat = backend in CHAT_BACKENDS
        blocks = [self.rules] if self.rules else []
        for kind in kinds:
            ladder = self.ladders[kind]
            lines = [f"Ladder ({kind}):"]
            for i, rung in enumerate(ladder["rungs"], 1):
                how = (rung["tool"] if chat else rung["cli"]) or rung["cli"]
                how = how.replace("{main}", self.main).replace("{cli}", self.cli)
                lines.append(f"{i}. {rung['name']}: {how}")
            if ladder["done"]:
                lines.append(f"Done when: {ladder['done']}.")
            if ladder["stop"]:
                lines.append(f"At the top: {ladder['stop']}.")
            learned = self.advice(kind, text)
            if learned:
                lines.append(learned)
            blocks.append("\n".join(lines))
        return "\n".join(blocks), kinds

    def advice(self, kind: str, text: str = "") -> str:
        """What has worked here before, when there is enough of it to say so."""
        with self._lock:
            targets = dict(self.memory.get(kind, {}))
        words = {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}
        # The request's own words pick the target: "whatsapp" finds web.whatsapp.com.
        named = [t for t in targets if t and any(w in t.lower() for w in words)]
        for target in named + [""]:
            rungs = targets.get(target, {})
            ranked = sorted(rungs.items(), key=lambda kv: (-kv[1].get("ok", 0), kv[1].get("fail", 0)))
            if ranked and ranked[0][1].get("ok", 0) >= MIN_EVIDENCE:
                name, stats = ranked[0]
                where = f"On {target}" if target else "Here"
                failing = [n for n, s in rungs.items() if s.get("fail", 0) >= MIN_EVIDENCE and not s.get("ok")]
                tail = f"; '{failing[0]}' keeps failing, skip it" if failing else ""
                return f"{where}, '{name}' worked {stats['ok']} of {stats['ok'] + stats.get('fail', 0)} times: start there{tail}."
        return ""

    def track(self, task_id: str | None, kinds: list[str]) -> None:
        if task_id and kinds:
            with self._lock:
                self._tasks[task_id] = list(kinds)

    # -- learning ----------------------------------------------------------------------
    def _on_agent(self, event) -> None:
        d = event.data
        task = str(d.get("task") or "")
        if not task:
            return
        kind = d.get("kind")
        if kind == "tool":
            with self._lock:
                self._seen.setdefault(task, []).append(f"{d.get('tool') or ''} {d.get('text') or ''}")
                del self._seen[task][:-60]
        elif kind in ("result", "error"):
            with self._lock:
                kinds = self._tasks.pop(task, [])
                seen = self._seen.pop(task, [])
            if kinds and seen:
                self.learn(kinds, seen, ok=kind == "result")

    def learn(self, kinds: list[str], seen: list[str], ok: bool) -> None:
        target = _target(seen)
        changed = False
        with self._lock:
            for kind in kinds:
                ladder = self.ladders.get(kind)
                if not ladder:
                    continue
                used: list[str] = []  # rung names in the order first used
                last = ""
                for line in seen:
                    for rung in ladder["rungs"]:
                        if any(sign.search(line) for sign in rung["signs"]):
                            last = rung["name"]
                            if last not in used:
                                used.append(last)
                            break
                if not used:
                    continue
                for where in {target, ""}:
                    stats = self.memory.setdefault(kind, {}).setdefault(where, {})
                    for name in used:
                        entry = stats.setdefault(name, {"ok": 0, "fail": 0})
                        if ok and name == last:
                            entry["ok"] += 1
                        else:
                            entry["fail"] += 1
                changed = True
        if changed:
            self._save_memory()

    def _load_memory(self) -> dict:
        try:
            return json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_memory(self) -> None:
        with self._lock:
            text = json.dumps(self.memory, indent=1, sort_keys=True)
        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.memory_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            log.debug("couldn't save ladder memory: %s", exc)


def _target(seen: list[str]) -> str:
    """The site or window a task worked on: the last one its commands named."""
    target = ""
    for line in seen:
        host = _HOST.findall(line)
        window = _WINDOW.findall(line)
        if host:
            target = host[-1].lower().removeprefix("www.")
        elif window:
            target = window[-1].strip()
    return target
