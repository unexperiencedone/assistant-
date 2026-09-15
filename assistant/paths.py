"""Where things live, both from source and inside a PyInstaller build.

    APP_DIR       writable folder holding config.toml and data/ (repo root, or the .exe's folder)
    RESOURCE_DIR  read-only bundled files (repo root, or PyInstaller's _internal folder)
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = APP_DIR


def resource_path(*parts: str) -> Path:
    return RESOURCE_DIR.joinpath(*parts)


def cli_command() -> str:
    """How an agent should invoke the local index CLI on this install."""
    if FROZEN:
        return f'"{APP_DIR / "nova-cli.exe"}" sysindex'
    return f'python "{APP_DIR / "sysindex.py"}"'


def main_command() -> str:
    """How an agent should invoke Nova's CLI (`ui ...`, `macro ...`) on this install."""
    if FROZEN:
        return f'"{APP_DIR / "nova-cli.exe"}"'
    return f'python "{APP_DIR / "main.py"}"'
