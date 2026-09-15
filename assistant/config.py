"""Typed settings loaded from config.toml, with defaults for every key."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .paths import APP_DIR

DEFAULT_CONFIG = APP_DIR / "config.toml"


@dataclass
class AssistantSettings:
    name: str = "Nova"
    wake_word: str = ""
    follow_up_seconds: float = 20
    filler_after_seconds: float = 1.5


@dataclass
class SpeechSettings:
    enabled: bool = True  # false: no microphone (type in the canvas instead)
    engine: str = "whisper"
    whisper_model: str = "base.en"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    language: str = "en"
    energy_threshold: int = 300
    pause_threshold: float = 1.0          # silence that ends a phrase
    continuation_seconds: float = 0.7     # after a phrase, keep listening this long for more
    incomplete_wait_seconds: float = 4.0  # total wait when the sentence sounds unfinished
    phrase_time_limit: float = 30


@dataclass
class VoiceSettings:
    engine: str = "auto"
    rate: int = 0
    volume: int = 100
    voice_contains: str = ""


@dataclass
class ClaudeAgentSettings:
    executable: str = "claude"
    permission_mode: str = "auto"
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class AntigravityAgentSettings:
    executable: str = "agy"
    mode: str = "accept-edits"
    skip_permissions: bool = False
    model: str = ""
    print_timeout: str = "30m"


@dataclass
class AgentSettings:
    default: str = "claude"
    workspace: str = "."
    continue_session: bool = True
    claude: ClaudeAgentSettings = field(default_factory=ClaudeAgentSettings)
    antigravity: AntigravityAgentSettings = field(default_factory=AntigravityAgentSettings)


@dataclass
class UISettings:
    # browser: canvas in your browser | window: native app window | tray: start hidden in the tray
    mode: str = "browser"
    hotkey: str = "ctrl+alt+n"  # toggles the canvas window in window/tray mode
    terminal_dashboard: bool = True
    web_dashboard: bool = True  # the canvas server; needed by every mode
    web_port: int = 8765
    open_browser: bool = True


@dataclass
class LocalSettings:
    enabled: bool = True
    db_path: str = "data/index.db"
    roots: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    max_depth: int = 10
    index_hidden: bool = False
    reindex_minutes: float = 30
    full_rescan_hours: float = 24
    half_life_days: float = 14


@dataclass
class AutomationSettings:
    enabled: bool = True
    folder: str = "automations"  # TOML automation files; relative to config.toml


@dataclass
class Settings:
    assistant: AssistantSettings = field(default_factory=AssistantSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)
    ui: UISettings = field(default_factory=UISettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    automations: AutomationSettings = field(default_factory=AutomationSettings)
    # Folder containing config.toml; relative paths in the config resolve against it.
    base_dir: Path = field(default_factory=lambda: APP_DIR)

    @property
    def workspace(self) -> Path:
        path = Path(os.path.expandvars(self.agents.workspace)).expanduser()
        return (path if path.is_absolute() else self.base_dir / path).resolve()


def _merge(target: Any, values: dict[str, Any]) -> None:
    """Copy known keys from a TOML table onto a dataclass, recursing into sub-tables."""
    known = {f.name for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            print(f"[config] ignoring unknown key: {key}")
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(target, key, value)


def load_settings(path: str | Path = DEFAULT_CONFIG) -> Settings:
    """Load settings; works the same no matter which folder you launch from."""
    settings = Settings()
    path = Path(path).expanduser().resolve()
    if path.exists():
        with path.open("rb") as fh:
            _merge(settings, tomllib.load(fh))
        settings.base_dir = path.parent
    else:
        print(f"[config] {path} not found, using defaults")
    return settings
