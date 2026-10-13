"""Which projects are already on LinkedIn: the posted ledger.

The API can't tell us. Reading your own posts back needs a scope LinkedIn doesn't give
individual developers, so the ledger is kept here instead (data/linkedin_posted.json):

* seeded once from LinkedIn's data export (Settings -> Data privacy -> Get a copy of
  your data -> Shares.csv), matching project names against what you wrote, or by hand
  with `python main.py linkedin mark "<project>"`;
* after that, written by the gate every time a project post actually goes out.

An entry can also be `skip`: "never post this one". Seeding errs on the side of marking
a project as posted -- a missed post costs nothing, a duplicate post costs face.
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

DAY = 86400.0


def _norm(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- storage ----------------------------------------------------------------------
    def load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, key: str, name: str, path: str = "", source: str = "api", post: str = "",
               head: str = "", status: str = "posted", when: float | None = None) -> None:
        with self._lock:
            data = self.load()
            data[key] = {"name": name, "path": path, "status": status, "source": source,
                         "post": post, "head": head, "at": when or time.time()}
            self._write(data)

    def forget(self, key: str) -> bool:
        with self._lock:
            data = self.load()
            if key not in data:
                return False
            del data[key]
            self._write(data)
            return True

    # -- questions --------------------------------------------------------------------
    def covered(self, key: str) -> bool:
        return key in self.load()

    def last_posted_at(self) -> float:
        times = [float(e.get("at") or 0) for e in self.load().values()
                 if e.get("status") == "posted" and e.get("source") == "api"]
        return max(times, default=0.0)

    def days_since_last(self) -> float | None:
        last = self.last_posted_at()
        return (time.time() - last) / DAY if last else None

    def entries(self) -> list[dict[str, Any]]:
        return sorted(({"key": k, **v} for k, v in self.load().items()),
                      key=lambda e: -float(e.get("at") or 0))

    # -- seeding from LinkedIn's data export ------------------------------------------
    def seed_from_export(self, csv_path: Path, projects: Iterable[Any]) -> list[str]:
        """Mark every project whose name shows up in an exported post. Returns the names."""
        texts: list[tuple[str, float]] = []
        with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                text = " ".join(str(row.get(k) or "") for k in ("ShareCommentary", "SharedUrl", "ShareLink"))
                texts.append((_norm(text), _parse_date(row.get("Date") or "")))
        marked: list[str] = []
        for project in projects:
            if self.covered(project.key):
                continue
            names = {_norm(project.name), _norm(Path(project.path).name)}
            names = {n for n in names if len(n) >= 4}
            for text, when in texts:
                if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
                    self.record(project.key, project.name, project.path, source="export",
                                head=project.head, when=when or None)
                    marked.append(project.name)
                    break
        return marked


def _parse_date(text: str) -> float:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y, %I:%M %p"):
        try:
            return time.mktime(time.strptime(text.strip(), fmt))
        except ValueError:
            continue
    return 0.0
