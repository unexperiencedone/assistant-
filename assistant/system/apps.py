"""Discover installed applications on Windows.

`Get-StartApps` is the authoritative list: it covers classic Win32 programs and
Microsoft Store apps, and every AppID it returns launches through
`shell:AppsFolder\\<AppID>`. Start Menu and Desktop shortcuts are added as a
fallback in case PowerShell is unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_DOC_EXTENSIONS = {".pdf", ".txt", ".htm", ".html", ".chm", ".rtf", ".url", ".ini", ".log", ".md"}

# Things people say that aren't Start Menu entries.
BUILTIN_APPS = {
    "Settings": "ms-settings:",
    "Task Manager": "taskmgr.exe",
    "Command Prompt": "cmd.exe",
    "PowerShell": "powershell.exe",
    "File Explorer": "explorer.exe",
    "Control Panel": "control.exe",
    "Registry Editor": "regedit.exe",
    "Device Manager": "devmgmt.msc",
    "Disk Management": "diskmgmt.msc",
    "Recycle Bin": "shell:RecycleBinFolder",
    "Downloads": "shell:Downloads",
    "Startup Apps": "ms-settings:startupapps",
    "Windows Update": "ms-settings:windowsupdate",
    "Bluetooth Settings": "ms-settings:bluetooth",
    "Display Settings": "ms-settings:display",
    "Sound Settings": "ms-settings:sound",
    "Wi-Fi Settings": "ms-settings:network-wifi",
}


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str  # what to pass to os.startfile


def discover_apps() -> list[AppEntry]:
    if sys.platform != "win32":
        return []
    found: dict[str, AppEntry] = {}
    for entry in [*_start_apps(), *_shortcuts(), *(AppEntry(n, t) for n, t in BUILTIN_APPS.items())]:
        found.setdefault(entry.name.lower(), entry)  # first source wins
    return list(found.values())


def _start_apps() -> list[AppEntry]:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    if isinstance(rows, dict):
        rows = [rows]
    apps = []
    for row in rows:
        name, app_id = (row.get("Name") or "").strip(), (row.get("AppID") or "").strip()
        if not name or not app_id or app_id.lower().startswith(("http:", "https:")):
            continue
        if len(app_id) > 2 and app_id[1] == ":" and Path(app_id).suffix.lower() in _DOC_EXTENSIONS:
            continue  # manuals and readmes pinned to the Start Menu
        apps.append(AppEntry(name, f"shell:AppsFolder\\{app_id}"))
    return apps


def _shortcuts() -> list[AppEntry]:
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
        Path.home() / "OneDrive/Desktop",
        Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop",
    ]
    apps = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.lnk"):
            name = path.stem
            if any(w in name.lower() for w in ("uninstall", "readme", "help", "documentation", "release notes")):
                continue
            apps.append(AppEntry(name, str(path)))
    return apps
