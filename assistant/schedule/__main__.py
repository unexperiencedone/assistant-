"""`python main.py schedule ...`: the schedule from a shell, for Claude sessions and you.

    schedule add "Standup" --at "tomorrow 10am" [--end "tomorrow 10:30"] [--note "..."]
    schedule list [today|tomorrow|friday|2026-10-09]
    schedule remove <id>

It writes the same SQLite file the running Nova reads every few seconds, so an item
added here gets its reminders without a restart.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

from ..config import load_settings
from ..events import EventBus
from .service import ScheduleService
from .when import parse_day, parse_when


def _service() -> ScheduleService:
    settings = load_settings()
    path = Path(os.path.expandvars(settings.schedule.db_path)).expanduser()
    path = path if path.is_absolute() else settings.base_dir / path
    calendar = None
    if settings.today.enabled:
        from ..today import TodayService

        def resolve(value: str) -> Path:
            p = Path(os.path.expandvars(value)).expanduser()
            return p if p.is_absolute() else settings.base_dir / p

        today = TodayService(settings.today, resolve(settings.today.cache_path),
                             resolve(settings.today.state_path), EventBus())
        calendar = today.personal_events
    return ScheduleService(path, EventBus(), settings.schedule, calendar=calendar)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="schedule")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("--at", required=True)
    add.add_argument("--end", default="")
    add.add_argument("--note", default="")
    show = sub.add_parser("list")
    show.add_argument("day", nargs="?", default="today")
    rm = sub.add_parser("remove")
    rm.add_argument("id", type=int)
    args = parser.parse_args(argv)

    service = _service()
    try:
        if args.cmd == "add":
            try:
                start = parse_when(args.at)
                end = parse_when(args.end) if args.end else None
            except ValueError as error:
                print(f"error: {error}")
                return 2
            item = service.add(args.title, start, end, args.note)
            print(f"added\t#{item['id']}\t{start:%Y-%m-%d %H:%M}\t{item['title']}")
        elif args.cmd == "list":
            day = parse_day(args.day)
            start = datetime(day.year, day.month, day.day)
            entries = service.agenda(start, start + timedelta(days=1))
            if not entries:
                print(f"nothing on {day.isoformat()}")
            for e in entries:
                ident = f"#{e['id']}" if e["id"] else "cal"
                at = "all-day" if e["all_day"] else e["start"].strftime("%H:%M")
                print(f"{ident}\t{at}\t{e['title']}")
        else:
            if not service.remove(args.id):
                print(f"no item #{args.id}")
                return 1
            print(f"removed\t#{args.id}")
        return 0
    finally:
        service.stop()


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
