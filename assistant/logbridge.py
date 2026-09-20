"""Route Python logging into Nova's event bus (and so into the activity feed and log file).

Low-level modules (UI automation, browser) shouldn't know about the bus, but their
failures shouldn't vanish either: they log normally, and this puts warnings and
errors where the user can see them.
"""

from __future__ import annotations

import logging

from .events import EventBus

LOGGER = logging.getLogger("nova")


class _BusHandler(logging.Handler):
    def __init__(self, bus: EventBus) -> None:
        super().__init__(level=logging.WARNING)
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        level = "error" if record.levelno >= logging.ERROR else "warn"
        try:
            self.bus.log(f"{record.name.removeprefix('nova.')}: {record.getMessage()}", level)
        except Exception:
            pass


def install(bus: EventBus, debug: bool = False) -> None:
    LOGGER.setLevel(logging.DEBUG if debug else logging.INFO)
    LOGGER.propagate = False
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
    LOGGER.addHandler(_BusHandler(bus))
