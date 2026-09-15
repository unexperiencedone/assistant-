"""Voice handlers for local computer commands.

Each handler returns True when it dealt with the request, or False to let the
agent handle it (e.g. "find the bug in login" isn't a file name).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from ..events import EventBus
from .duplicates import human_size
from .search import Hit
from .service import MIN_CONFIDENCE, LocalSystem

KINDS = {"file": "file", "folder": "folder", "directory": "folder", "app": "app", "apps": "app",
         "application": "app", "program": "app", "files": "file", "folders": "folder"}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


class LocalVoiceCommands:
    def __init__(self, system: LocalSystem, bus: EventBus, say: Callable[[str], None]) -> None:
        self.system = system
        self.bus = bus
        self.say = say
        self.last_hit: Hit | None = None

    def handle(self, name: str, args: dict[str, str]) -> bool:
        return getattr(self, f"_{name}")(**args)

    # -- handlers ---------------------------------------------------------------------
    def _open_item(self, target: str, kind: str = "", kind2: str = "") -> bool:
        kind_name = KINDS.get((kind or kind2).lower())
        result = self.system.open(target, kind=kind_name)
        if result.ok and result.hit:
            self.last_hit = result.hit
            self._activity("open", result.hit.path)
            self.say(f"Opening {_spoken_name(result.hit)}.")
            return True
        if result.alternatives and kind_name:
            self.system.last_results = result.alternatives
            self._list(result.alternatives, "open")
            self.say(f"I'm not sure which {kind_name} you mean. Closest is {_spoken_name(result.alternatives[0])}. "
                     "Say open 1 to open it.")
            return True
        return False  # probably not a local thing: send it to the agent

    def _find_item(self, target: str, kind: str = "", kind2: str = "") -> bool:
        kind_name = KINDS.get((kind or kind2).lower())
        hits = [h for h in self.system.find(target, kind=kind_name, limit=5) if h.score >= MIN_CONFIDENCE]
        if not hits:
            if kind_name:
                self.say(f"I couldn't find a {kind_name} matching {target}.")
                return True
            return False
        self.system.last_results = hits
        self.last_hit = hits[0]
        self._list(hits, "find")
        where = f" in {Path(hits[0].path).parent.name}" if hits[0].kind != "app" else ""
        more = f" and {len(hits) - 1} more on the dashboard" if len(hits) > 1 else ""
        self.say(f"Top match is {_spoken_name(hits[0])}{where}{more}. Say open 1 to open it.")
        return True

    def _open_result(self, number: str) -> bool:
        index = ORDINALS.get(number.lower()) or int({"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(number, number))
        results = self.system.last_results
        if not 1 <= index <= len(results):
            self.say(f"There's no result {index}. Ask me to find something first.")
            return True
        hit = results[index - 1]
        self.system.open_hit(hit)
        self.last_hit = hit
        self._activity("open", hit.path)
        self.say(f"Opening {_spoken_name(hit)}.")
        return True

    def _reveal_result(self) -> bool:
        if not self.last_hit or self.last_hit.kind == "app":
            self.say("There's nothing to show in Explorer yet.")
            return True
        self.system.reveal(self.last_hit.path)
        self.say("Showing it in File Explorer.")
        return True

    def _most_used(self, kind: str) -> bool:
        hits = self.system.top(KINDS.get(kind.lower()), limit=5)
        if not hits:
            self.say("I haven't seen you open anything through me yet.")
            return True
        self.system.last_results = hits
        self._list(hits, "top")
        names = ", ".join(_spoken_name(h) for h in hits[:3])
        self.say(f"Your most used {kind} lately: {names}.")
        return True

    def _find_duplicates(self, where: str = "") -> bool:
        folder = self._resolve_folder(where or "downloads")
        if not folder:
            self.say(f"I couldn't find a folder called {where}.")
            return True
        self.say(f"Scanning {Path(folder).name} for duplicates. I'll tell you what I find.")
        threading.Thread(target=self._duplicates_worker, args=(folder,), daemon=True).start()
        return True

    def _reindex(self) -> bool:
        self.say("Refreshing the file index in the background.")

        def work() -> None:
            apps = self.system.reindex_apps()
            stats = self.system.reindex_files(full=True)
            self.bus.log(f"Reindex: {apps} apps; {stats.summary()}")
            self.say(f"Index refreshed: {apps} apps and {stats.files} files.")

        threading.Thread(target=work, daemon=True).start()
        return True

    # -- helpers ------------------------------------------------------------------------
    def _duplicates_worker(self, folder: str) -> None:
        try:
            groups = self.system.duplicates(folder)
        except Exception as exc:
            self.say(f"The duplicate scan failed: {exc}")
            return
        if not groups:
            self.say(f"No duplicate files in {Path(folder).name}.")
            return
        wasted = sum(g.wasted for g in groups)
        for g in groups[:10]:
            self._activity("duplicate", f"{human_size(g.size)} x{len(g.paths)}: " + " | ".join(g.paths))
        biggest = Path(groups[0].paths[0]).name
        self.say(f"Found {len(groups)} sets of duplicates using {human_size(wasted)} extra. "
                 f"The biggest is {biggest}. They're listed on the dashboard. I won't delete anything unless you ask.")

    def _resolve_folder(self, spoken: str) -> str | None:
        spoken = spoken.strip()
        expanded = os.path.expandvars(spoken)
        if os.path.isdir(expanded):
            return expanded
        hits = self.system.find(spoken, kind="folder", limit=1)
        return hits[0].path if hits and hits[0].score >= MIN_CONFIDENCE else None

    def _list(self, hits: list[Hit], verb: str) -> None:
        for i, hit in enumerate(hits, 1):
            self._activity(verb, f"{i}. {hit.kind}: {hit.path}")

    def _activity(self, tool: str, text: str) -> None:
        self.bus.publish("agent", kind="local", backend="local", tool=tool, text=text)


def _spoken_name(hit: Hit) -> str:
    return hit.name if hit.kind != "file" else Path(hit.name).stem.replace("_", " ")
