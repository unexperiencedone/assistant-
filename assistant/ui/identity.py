"""Who Nova is to Windows: the name and icon on its taskbar button and its notifications.

Run from source, Nova is python.exe to Windows, so the taskbar groups it as "Python
3.11" with Python's logo. Windows asks two things instead of the executable:

    the process's AppUserModelID   set first thing, before any window exists
    a Start Menu shortcut          carrying that same ID, the name "Nova" and the icon

With both, the taskbar button, its right-click menu, pinning and notifications all say
Nova with the N. The shortcut also launches Nova (tray mode), so it is a real Start Menu
entry rather than a stub. A built Nova.exe gets the same treatment.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("nova.identity")

APP_ID = "Kaiketsu.Nova"
SHORTCUT_NAME = "Nova.lnk"


def set_app_id() -> bool:
    """Before any window: from here on this process is Nova to the taskbar."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID) == 0
    except Exception as error:
        log.warning("couldn't set the app id: %s", error)
        return False


def _launcher() -> tuple[str, str, str]:
    """(target, arguments, working folder) that start Nova in the tray."""
    from ..paths import APP_DIR

    if getattr(sys, "frozen", False):
        return sys.executable, "--tray", str(APP_DIR)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else Path(sys.executable)
    return str(target), f'"{APP_DIR / "main.py"}" --tray', str(APP_DIR)


def shortcut_path() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT_NAME


def ensure_shortcut() -> bool:
    """Write the Start Menu shortcut if it is missing or out of date. Cheap when current."""
    if sys.platform != "win32":
        return False
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell

        from ..paths import resource_path

        icon = resource_path("assistant", "ui", "nova.ico")
        target, arguments, folder = _launcher()
        path = shortcut_path()

        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        if path.exists():
            try:
                persist.Load(str(path))
                store = link.QueryInterface(propsys.IID_IPropertyStore)
                current = store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
                if (link.GetPath(0)[0].lower() == target.lower() and link.GetArguments() == arguments
                        and current == APP_ID and link.GetIconLocation()[0].lower() == str(icon).lower()):
                    return True
            except Exception:
                pass   # unreadable or someone else's: write ours over it
            link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
            persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        link.SetPath(target)
        link.SetArguments(arguments)
        link.SetWorkingDirectory(folder)
        link.SetDescription("Nova, your personal butler")
        if icon.exists():
            link.SetIconLocation(str(icon), 0)
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
        store.Commit()
        path.parent.mkdir(parents=True, exist_ok=True)
        persist.Save(str(path), True)
        return True
    except Exception as error:   # identity is cosmetic: never stop Nova over it
        log.warning("couldn't write the Start Menu shortcut: %s", error)
        return False
