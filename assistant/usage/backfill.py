"""Fill the ledger from what was already on disk, so the numbers start with history.

Two sources, each read once per file:

- data/sessions/<day>/<backend>-*/turn-NN.jsonl: every agent turn's raw stream, which
  includes the provider's own usage figures. Kept two weeks by sessions.py, so this is
  how far back the turn-level numbers can reach. Each file is keyed by its path, so
  running this again only adds turns it hasn't seen.
- data/routes.json: requests per route per ISO week, from the start of the project.
  Imported once, as one row per week and route, dated the Monday of that week.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import normalize
from .ledger import FREE_ROUTES, UsageLedger

log = logging.getLogger("nova.usage")


def turn_file(path: Path) -> dict[str, Any] | None:
    """One turn log -> what record_turn needs, or None when it holds no finished turn."""
    meta: dict[str, Any] = {}
    claude = agy = chat = None
    closed: dict[str, Any] = {}
    last_ts = 0.0
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last_ts = float(entry.get("ts") or last_ts)
                kind, data = entry.get("kind"), entry.get("data")
                if kind == "meta" and isinstance(data, dict):
                    meta = data
                elif kind == "result" and isinstance(data, dict):
                    closed = data
                elif kind == "raw" and isinstance(data, dict):
                    if data.get("type") == "result" and "usage" in data:
                        claude = data
                    elif data.get("event") == "result" and isinstance(data.get("result"), dict):
                        agy = data["result"]
                    elif isinstance(data.get("usage"), dict) and "prompt_tokens" in data["usage"]:
                        chat = data["usage"]  # written once at the end of a turn; the last one wins
    except OSError:
        return None

    backend = normalize.backend_key(str(meta.get("backend") or path.parent.name.split("-")[0]))
    if backend == "claude" and claude:
        data = dict(claude)  # cost_usd is filled in by sessions(), from the running total
        text = str(claude.get("result") or closed.get("summary") or "")
    elif backend == "antigravity" and agy:
        data = {"usage": agy.get("usage") or {}}
        text = str(agy.get("response") or closed.get("summary") or "")
    elif backend in normalize.FREE_BACKENDS and chat:
        data = {"usage": chat}
        text = str(closed.get("summary") or "")
    else:
        return None
    return {
        "backend": backend, "data": data, "request": str(meta.get("prompt") or ""), "text": text,
        "ok": bool(closed.get("ok", not (claude or {}).get("is_error"))),
        "seconds": float(closed.get("seconds") or 0), "ts": last_ts,
    }


def sessions(ledger: UsageLedger, folder: Path) -> int:
    added = 0
    if not folder.is_dir():
        return 0
    # Turns after live recording began are already in the ledger, from their events.
    cutoff = float(ledger.meta("live_since") or time.time())
    running: dict[Path, float] = {}  # Claude's running total, per session folder (one process)
    for path in sorted(folder.glob("*/*/turn-*.jsonl"), key=_turn_order):
        key = "log:" + path.relative_to(folder).as_posix()
        turn = turn_file(path)
        if turn and turn["backend"] == "claude":
            total = float(turn["data"].get("total_cost_usd") or 0.0)
            before = running.get(path.parent, 0.0)
            turn["data"]["cost_usd"] = total - before if total >= before else total
            running[path.parent] = total
        if turn and turn["ts"] < cutoff and ledger.record_turn(turn["backend"], turn["data"], request=turn["request"], ok=turn["ok"],
                                       text=turn["text"], seconds=turn["seconds"], ts=turn["ts"],
                                       source="backfill", key=key):
            added += 1
    return added


def _turn_order(path: Path) -> tuple[str, int]:
    """turn-9 before turn-10: by folder, then by number, not by spelling."""
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return path.parent.as_posix(), int(digits or 0)


def routes(ledger: UsageLedger, path: Path) -> int:
    """routes.json, once. Afterwards the ledger counts free requests itself."""
    if ledger.meta("routes_imported"):
        return 0
    try:
        weeks = json.loads(path.read_text(encoding="utf-8")).get("weeks", {})
    except (OSError, ValueError):
        weeks = {}
    added = 0
    for week, counts in weeks.items():
        try:
            year, number = week.split("-W")
            monday = datetime.combine(date.fromisocalendar(int(year), int(number), 1), datetime.min.time())
        except ValueError:
            continue
        for route, count in counts.items():
            if route in FREE_ROUTES and count:
                if ledger.record_free(route, count=int(count), ts=monday.timestamp(), source="backfill",
                                      key=f"routes:{week}:{route}"):
                    added += 1
    ledger.set_meta("routes_imported", datetime.now().isoformat(timespec="seconds"))
    return added


def run(ledger: UsageLedger, sessions_folder: Path, routes_file: Path) -> None:
    try:
        turns = sessions(ledger, sessions_folder)
        weeks = routes(ledger, routes_file)
        if turns or weeks:
            log.info("usage ledger: backfilled %d turns and %d weekly route counts", turns, weeks)
    except Exception as exc:  # history is a bonus; a bad file must not stop Nova
        log.warning("usage backfill stopped: %s", exc)
