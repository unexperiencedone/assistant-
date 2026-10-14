"""Connections: where a person who installed Nova signs in to its brains and gives it keys.

Two kinds, because that is how the providers work:

    sign-in   Claude Code (your Claude plan) and Antigravity (your Google plan) are CLIs
              that keep their own login. Nova checks it (`claude auth status`,
              `agy models`) and, to sign in, opens the CLI's own sign-in in a console.
    key       Groq, OpenRouter and Fish Audio take an API key. A pasted key is tried
              against the provider first, then written to .env next to config.toml and
              into this process's environment, so it works on the next request with no
              restart (the key pools read the environment every time).

A key never leaves this module again: status shows its last four characters only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECK_TIMEOUT = 30.0           # a CLI that hasn't answered by then is reported as unknown
FRESH_SECONDS = 120.0          # a sign-in check younger than this is reused
_CREATE_NEW_CONSOLE = 0x00000010
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str              # "signin" | "key"
    role: str              # what Nova uses it for, in a sentence
    link: str              # where to get a key, or how to install the CLI
    env: str = ""          # key providers: the .env name
    check_url: str = ""    # key providers: a cheap authenticated GET
    brain: bool = True     # can answer requests on its own (Fish is a voice, not a brain)


def providers(settings: Any) -> list[Provider]:
    agents = settings.agents
    return [
        Provider("claude", "Claude", "signin",
                 "The big jobs: coding, files, research and long tasks. Uses your Claude plan through Claude Code.",
                 "https://claude.com/claude-code"),
        Provider("antigravity", "Antigravity", "signin",
                 "Google's coding agent, for work you send its way. Uses your Google account.",
                 "https://antigravity.google"),
        Provider("groq", "Groq", "key",
                 "Fast, free answers and conversation. The first brain most requests reach.",
                 "https://console.groq.com/keys", agents.groq.api_key_env or "GROQ_API_KEY",
                 "https://api.groq.com/openai/v1/models"),
        Provider("openrouter", "OpenRouter", "key",
                 "Free models as a fallback when Groq is busy or rate-limited.",
                 "https://openrouter.ai/settings/keys", agents.openrouter.api_key_env or "OPENROUTER_API_KEY",
                 "https://openrouter.ai/api/v1/key"),
        Provider("fish", "Fish Audio", "key",
                 "Nova's natural voice. Optional: without it Nova speaks with the Windows voice.",
                 "https://fish.audio/app/api-keys/", getattr(settings.voice, "fish_key_env", "") or "FISH_API_KEY",
                 "https://api.fish.audio/wallet/self/api-credit", brain=False),
    ]


# -- .env --------------------------------------------------------------------------------
def write_env(path: Path, name: str, value: str | None) -> None:
    """Set (or with None, remove) one NAME=value line, keeping every other line as it was."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept = [line for line in lines if line.split("=", 1)[0].strip() != name]
    if value is not None:
        kept.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def masked(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 8 else "set"


def try_key(url: str, key: str, timeout: float = 10.0) -> tuple[bool | None, str]:
    """True if the provider accepted the key, False if it refused it, None if it couldn't
    be asked (offline, the provider down): then the key is kept and said so."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Nova"})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True, ""
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return False, "it refused that key"
        return None, f"it answered {error.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, f"couldn't reach it ({getattr(error, 'reason', error)})"


# -- the CLIs ----------------------------------------------------------------------------
def _run(args: list[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    except OSError as error:
        return -2, str(error)


def check_claude(executable: str) -> dict[str, Any]:
    path = shutil.which(executable)
    if not path:
        return {"installed": False, "connected": False, "detail": "Claude Code isn't installed."}
    code, out = _run([path, "auth", "status"], CHECK_TIMEOUT)
    try:
        info = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"installed": True, "connected": None, "detail": "Couldn't read Claude's sign-in status."}
    if not info.get("loggedIn"):
        return {"installed": True, "connected": False, "detail": "Installed, not signed in."}
    plan = str(info.get("subscriptionType") or "").capitalize()
    who = info.get("email") or ""
    detail = " · ".join(x for x in (f"Claude {plan} plan" if plan else "Signed in", who) if x)
    if info.get("authMethod") not in (None, "claude.ai"):
        detail += f" ({info.get('authMethod')})"
    return {"installed": True, "connected": True, "detail": detail}


def check_antigravity(executable: str) -> dict[str, Any]:
    path = shutil.which(executable)
    if not path:
        return {"installed": False, "connected": False, "detail": "Antigravity isn't installed."}
    code, out = _run([path, "models"], CHECK_TIMEOUT)
    models = [line for line in out.splitlines() if "\t" in line]
    if code == 0 and models:
        return {"installed": True, "connected": True, "detail": f"Signed in · {len(models)} models"}
    if code == -1:
        return {"installed": True, "connected": None, "detail": "Antigravity didn't answer in time."}
    return {"installed": True, "connected": False, "detail": "Installed, not signed in."}


class ConnectionsService:
    def __init__(self, settings: Any, env_path: Path) -> None:
        self.settings = settings
        self.env_path = env_path
        self._lock = threading.Lock()
        self._signin: dict[str, dict[str, Any]] = {}   # id -> last check, with "at"
        self._checking: set[str] = set()

    def _provider(self, pid: str) -> Provider | None:
        return next((p for p in providers(self.settings) if p.id == pid), None)

    def _executable(self, pid: str) -> str:
        return getattr(self.settings.agents, pid).executable

    # -- status ------------------------------------------------------------------------
    def status(self, refresh: bool = False) -> dict[str, Any]:
        """Everything the Connections screen shows. Key status is instant; sign-in checks
        run in the background and are reported as checking until they finish."""
        rows = []
        for p in providers(self.settings):
            row: dict[str, Any] = {"id": p.id, "name": p.name, "kind": p.kind, "role": p.role,
                                   "link": p.link, "brain": p.brain}
            if p.kind == "key":
                key = os.environ.get(p.env, "").strip()
                row.update(connected=bool(key), detail=f"Key {masked(key)}" if key else "No key yet.", env=p.env)
            else:
                self._maybe_check(p.id, refresh)
                with self._lock:
                    row.update(self._signin.get(p.id) or {"connected": None, "detail": "Checking…"})
                    row["checking"] = p.id in self._checking
                row.pop("at", None)
            rows.append(row)
        ready = any(r["brain"] and r.get("connected") for r in rows)
        return {"providers": rows, "ready": ready}

    def _maybe_check(self, pid: str, refresh: bool) -> None:
        with self._lock:
            last = self._signin.get(pid)
            fresh = last is not None and time.monotonic() - last["at"] < FRESH_SECONDS
            if pid in self._checking or (fresh and not refresh):
                return
            self._checking.add(pid)
        threading.Thread(target=self._check, args=(pid,), name=f"connections-{pid}", daemon=True).start()

    def _check(self, pid: str) -> None:
        try:
            check = check_claude if pid == "claude" else check_antigravity
            result = check(self._executable(pid))
        except Exception as error:   # a check must never take the screen down
            result = {"installed": None, "connected": None, "detail": f"Couldn't check: {error}"}
        with self._lock:
            self._signin[pid] = {**result, "at": time.monotonic()}
            self._checking.discard(pid)

    # -- actions -----------------------------------------------------------------------
    def save_key(self, pid: str, key: str) -> tuple[bool, str]:
        p = self._provider(pid)
        key = (key or "").strip().strip('"').strip("'")
        if p is None or p.kind != "key":
            return False, "That isn't something that takes a key."
        if len(key) < 8 or any(c.isspace() for c in key):
            return False, "That doesn't look like a key. Paste the whole thing."
        accepted, why = try_key(p.check_url, key) if p.check_url else (None, "")
        if accepted is False:
            return False, f"{p.name} said no: {why}. Check you copied the whole key."
        write_env(self.env_path, p.env, key)
        os.environ[p.env] = key
        if accepted:
            return True, f"{p.name} is connected."
        return True, f"Saved, but I couldn't check it with {p.name}: {why}. It'll be tried on the next request."

    def remove_key(self, pid: str) -> tuple[bool, str]:
        p = self._provider(pid)
        if p is None or p.kind != "key":
            return False, "That isn't something that takes a key."
        write_env(self.env_path, p.env, None)
        os.environ.pop(p.env, None)
        return True, f"{p.name}'s key is removed."

    def sign_in(self, pid: str) -> tuple[bool, str]:
        """Open the CLI's own sign-in in a console window: the browser step and any
        prompts belong to the provider, not to Nova."""
        p = self._provider(pid)
        if p is None or p.kind != "signin":
            return False, "That isn't something you sign in to."
        path = shutil.which(self._executable(pid))
        if not path:
            return False, f"{p.name} isn't installed yet: install it from {p.link}, then sign in here."
        args = [path, "auth", "login"] if pid == "claude" else [path]
        try:
            subprocess.Popen(args, creationflags=_CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)
        except OSError as error:
            return False, f"Couldn't start {p.name}'s sign-in: {error}"
        with self._lock:
            self._signin.pop(pid, None)   # check again once they come back
        if pid == "claude":
            return True, "A window opened for Claude's sign-in. Finish it in the browser, then come back here."
        return True, "Antigravity opened in a window. Sign in with Google there, then close it and come back here."
