"""Automations for the running assistant: matching spoken phrases, reloading
edited files, and running them in the background."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..events import EventBus
from .macros import Macro, MacroRunner, load_macros


class AutomationService:
    def __init__(self, folder: Path, bus: EventBus, open_app: Callable[[str], bool], say: Callable[[str], None]) -> None:
        self.folder = folder
        self.bus = bus
        self.runner = MacroRunner(bus, open_app, say)
        self._macros: list[Macro] = []
        self._stamp: tuple = ()
        self.reload_if_changed()

    def reload_if_changed(self) -> None:
        """Pick up new or edited automation files without restarting Nova."""
        if not self.folder.is_dir():
            return
        stamp = tuple(sorted((p.name, p.stat().st_mtime) for p in self.folder.glob("*.toml")))
        if stamp != self._stamp:
            self._macros = load_macros(self.folder)
            self._stamp = stamp

    @property
    def macros(self) -> list[Macro]:
        self.reload_if_changed()
        return self._macros

    def match(self, utterance: str) -> tuple[Macro, dict[str, str]] | None:
        for macro in self.macros:
            params = macro.match(utterance)
            if params is not None:
                return macro, params
        return None

    def start(self, macro: Macro, params: dict[str, str], utterance: str) -> bool:
        return self.runner.start(macro, params, utterance)
