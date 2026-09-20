"""The shared secret that stands between Nova and the network.

The canvas server answers on every interface once the phone needs to reach it, so
every request carries a token. It is a real credential: the endpoint behind it can
run commands, open apps and (later) send messages from your phone.

Where the token comes from, in order:

1. `NOVA_TOKEN` in the environment (or `.env` next to config.toml) — for a device or
   script that should not read the file.
2. `data/nova_token` — generated on first start, 32 random url-safe bytes.

Both live outside version control (`.env` and `data/` are git-ignored). Rotating is
deleting the file and restarting Nova; every device then needs the new token, which is
the point — a lost phone stops working the moment you rotate.

A request proves itself with any one of:

    X-Nova-Token: <token>     header — what the Termux bridge and scripts send
    ?token=<token>            query — what you paste once on a new device
    Cookie: nova_token=...    set automatically after a successful ?token= visit
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

COOKIE_NAME = "nova_token"
HEADER_NAME = "x-nova-token"
QUERY_NAME = "token"
ENV_NAME = "NOVA_TOKEN"

# A year: the phone should not have to be re-paired every week.
COOKIE_MAX_AGE = 365 * 24 * 3600


def load_or_create(path: Path) -> str:
    """The token for this machine, generating and saving one the first time."""
    from_env = (os.environ.get(ENV_NAME) or "").strip()
    if from_env:
        return from_env
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        # Readable by this account only, as far as the platform allows.
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return token


def read(path: Path) -> str:
    """The token as it stands, without creating one. Empty when there is none."""
    from_env = (os.environ.get(ENV_NAME) or "").strip()
    if from_env:
        return from_env
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def rotate(path: Path) -> str:
    """Throw the old token away and issue a new one. Every device must be re-paired."""
    try:
        path.unlink()
    except OSError:
        pass
    return load_or_create(path)


def supplied_token(headers: dict, query: dict, cookies: dict) -> str:
    """Whatever the caller offered, from the three places we accept it."""
    return (
        headers.get(HEADER_NAME)
        or headers.get(HEADER_NAME.title())
        or query.get(QUERY_NAME)
        or cookies.get(COOKIE_NAME)
        or ""
    )


def matches(expected: str, supplied: str) -> bool:
    """Constant-time comparison: a token is a credential, not a name."""
    if not expected:
        return True  # the token is switched off in config; nothing to check against
    return secrets.compare_digest(str(expected), str(supplied or ""))
