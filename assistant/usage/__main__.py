"""`python main.py usage`: tokens spent and saved, in the terminal.

    python main.py usage              # last 30 days
    python main.py usage --days 7
    python main.py usage --all
    python main.py usage --json       # the same numbers the canvas's Usage screen shows

Works whether or not Nova is running: it reads data/usage.db, after importing any
session logs it hasn't seen yet.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main(argv: list[str]) -> int:
    from ..automation.macros import load_macros
    from ..config import load_settings
    from . import backfill, report
    from .ledger import UsageLedger

    parser = argparse.ArgumentParser(prog="usage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=30)
    parser.add_argument("--all", action="store_true", help="everything recorded")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()

    def under_base(value: str) -> Path:
        path = Path(os.path.expandvars(value)).expanduser()
        return path if path.is_absolute() else settings.base_dir / path

    ledger = UsageLedger(under_base(settings.usage.db_path))
    backfill.run(ledger, under_base(settings.sessions.folder), settings.base_dir / "data" / "routes.json")
    folder = under_base(settings.automations.folder)
    macros = load_macros(folder) if folder.is_dir() else []
    aliases = {m.name: m.path.stem for m in macros}
    summary = report.summary(ledger, None if args.all else args.days, aliases, set(aliases.values()))
    print(json.dumps(summary, indent=1, default=str) if args.json else report.text(summary))
    return 0
