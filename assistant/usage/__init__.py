"""Token and cost analytics: what each brain spent, and what Nova's cheaper routes saved.

    normalize.py  every backend's own usage report, in one shape
    ledger.py     data/usage.db: turns, free answers, browser calls (from bus events)
    backfill.py   the same, read from session logs and routes.json, so history starts full
    report.py     spent (measured), saved (estimated, with its method), where to save next
    __main__.py   `python main.py usage [--days 7 | --all] [--json]`

The canvas's Usage screen reads `UsageService.summary()` through GET /api/usage.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..events import EventBus
from . import backfill, report
from .ledger import UsageLedger


class UsageService:
    def __init__(self, db: Path, bus: EventBus | None, sessions_folder: Path, routes_file: Path,
                 automations: Any = None) -> None:
        self.ledger = UsageLedger(db, bus)
        self.automations = automations  # assistant.automation.service.AutomationService
        self.sessions_folder = sessions_folder
        self.routes_file = routes_file

    def start(self) -> None:
        """Backfill in the background (a few MB of logs), and listen to the browser."""
        threading.Thread(target=backfill.run, args=(self.ledger, self.sessions_folder, self.routes_file),
                         name="usage-backfill", daemon=True).start()
        from ..automation.extension import BRIDGE

        BRIDGE.on_measure = self.ledger.record_browser

    def _names(self) -> tuple[dict[str, str], set[str]]:
        """Automation display names -> file stems, which is what task types are called
        once a kind of request has been promoted to a script (automations/<task_type>.toml)."""
        macros = getattr(self.automations, "macros", None) or []
        aliases = {m.name: m.path.stem for m in macros}
        return aliases, set(aliases.values())

    def summary(self, days: float | None = 30) -> dict[str, Any]:
        aliases, automated = self._names()
        return report.summary(self.ledger, days, aliases, automated)
