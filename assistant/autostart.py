"""Start Nova automatically at logon via Windows Task Scheduler.

    nova-cli autostart install     # launch hidden in the tray at every logon
    nova-cli autostart status
    nova-cli autostart remove

(From source: `python main.py autostart ...`.)

Why Task Scheduler rather than the Startup folder or the Run key: it can delay
start until the desktop has settled, restart Nova if it crashes, and it runs as
the current user with normal privileges (no UAC prompt).
"""

from __future__ import annotations

import getpass
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import APP_DIR, FROZEN

TASK_NAME = "Nova Voice Assistant"


def launch_command() -> tuple[str, str]:
    """(program, arguments) that start Nova hidden in the tray, without a console window."""
    if FROZEN:
        return str(APP_DIR / "Nova.exe"), "--tray"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    program = pythonw if pythonw.exists() else Path(sys.executable)
    return str(program), f'"{APP_DIR / "main.py"}" --tray'


def task_xml(program: str, arguments: str, delay_seconds: int = 20) -> str:
    user = f"{_domain()}\\{getpass.getuser()}"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the Nova voice assistant in the system tray when you sign in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT{delay_seconds}S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(APP_DIR))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _domain() -> str:
    import os

    return os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or "."


def _schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def install(delay_seconds: int = 20) -> tuple[bool, str]:
    program, arguments = launch_command()
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "nova-task.xml"
        xml_path.write_text(task_xml(program, arguments, delay_seconds), encoding="utf-16")
        result = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F")
    message = (result.stdout or result.stderr).strip()
    return result.returncode == 0, message


def remove() -> tuple[bool, str]:
    result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def status() -> tuple[bool, str]:
    result = _schtasks("/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST")
    if result.returncode != 0:
        return False, "Autostart is not installed."
    wanted = ("Status:", "Next Run Time:", "Last Run Time:", "Last Result:", "Task To Run:", "Logon Mode:")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith(wanted)]
    return True, "\n".join(lines)


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("Autostart via Task Scheduler is Windows-only.")
        return 2
    command = argv[0] if argv else "status"
    if command == "install":
        delay = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 20
        ok, message = install(delay)
        program, arguments = launch_command()
        print(f"{message}\nAt logon (after {delay}s): {program} {arguments}" if ok else message)
    elif command == "remove":
        ok, message = remove()
        print(message)
    elif command == "status":
        ok, message = status()
        print(message)
        return 0
    else:
        print("usage: autostart install [delay_seconds] | remove | status")
        return 2
    return 0 if ok else 1
