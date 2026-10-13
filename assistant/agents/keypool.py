"""One pool of API keys per provider, shared by everything that talks to it.

Several free Groq keys are worth more than the sum of their limits only if two
things hold at once:

* **Lanes.** Each kind of work leans on its own key first -- the conversation on
  one, parallel tasks on the next, background chores (the loose-command classifier,
  the narrator) on a third -- so a long job hammering its key never rate-limits the
  reply you're waiting for.
* **One pool.** Every caller shares the same cooldown record. When a key is rate
  limited, *everyone* learns it at once and moves to the next free key, instead of
  each caller discovering the same 429 for itself.

Keys come from the environment (`.env`): the base name, then numbered ones, then an
optional comma-separated list, duplicates dropped:

    GROQ_API_KEY=...     GROQ_API_KEY_2=...   GROQ_API_KEY_3=...   GROQ_API_KEY_4=...
    GROQ_API_KEYS=key_a,key_b

Nothing here prints a key; `status()` names them by position only.
"""

from __future__ import annotations

import os
import re
import threading
import time

# Which key a kind of work tries first: lane i starts at key i (mod the pool size) and
# falls through the rest in order, so with four keys each lane owns one and the fourth
# is spare capacity every lane overflows into.
LANES = ("chat", "parallel", "background", "spare")

DEFAULT_COOLDOWN = 30.0     # seconds, when the provider doesn't say how long
MAX_NUMBERED = 9            # GROQ_API_KEY_2 .. GROQ_API_KEY_9

_WAIT = re.compile(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.I)


def retry_seconds(detail: str, header: str | None = None) -> float:
    """How long the provider asked us to wait: the Retry-After header, else the
    "Please try again in 1m23.5s" sentence Groq puts in the error, else a default."""
    try:
        if header:
            return max(1.0, float(header))
    except ValueError:
        pass
    match = _WAIT.search(detail or "")
    if match and any(match.groups()):
        hours, minutes, seconds = match.groups()
        total = int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
        return max(1.0, total)
    return DEFAULT_COOLDOWN


def keys_from_env(env_name: str) -> list[str]:
    if not env_name:
        return []
    found = [os.environ.get(env_name, "")]
    found += [os.environ.get(f"{env_name}_{i}", "") for i in range(2, MAX_NUMBERED + 1)]
    found += os.environ.get(f"{env_name}S", "").split(",")
    return list(dict.fromkeys(k.strip() for k in found if k.strip()))


class KeyPool:
    def __init__(self, env_name: str) -> None:
        self.env_name = env_name
        self._lock = threading.Lock()
        self._cooling: dict[str, float] = {}   # key -> monotonic time it is usable again

    @property
    def keys(self) -> list[str]:
        # Read every time: .env may be edited while Nova runs, and it costs microseconds.
        return keys_from_env(self.env_name)

    def __bool__(self) -> bool:
        return bool(self.keys)

    def _order(self, lane: str) -> list[str]:
        keys = self.keys
        if not keys:
            return []
        start = LANES.index(lane) % len(keys) if lane in LANES else 0
        return keys[start:] + keys[:start]

    def pick(self, lane: str = "chat", exclude: set[str] | frozenset[str] = frozenset()) -> str:
        """The lane's own key if it is free, else the next free one; "" when every key
        is cooling down (see `wait_seconds`)."""
        now = time.monotonic()
        with self._lock:
            for key in self._order(lane):
                if key not in exclude and self._cooling.get(key, 0.0) <= now:
                    return key
        return ""

    def cool(self, key: str, seconds: float) -> None:
        """Mark a key rate limited for everyone using this pool."""
        with self._lock:
            self._cooling[key] = max(self._cooling.get(key, 0.0), time.monotonic() + seconds)

    def wait_seconds(self) -> float:
        """How long until the soonest key is free again (0 if one is free now)."""
        keys = self.keys
        if not keys:
            return 0.0
        now = time.monotonic()
        with self._lock:
            return max(0.0, min(self._cooling.get(k, 0.0) for k in keys) - now)

    def status(self) -> str:
        """e.g. "4 keys: #1 free, #2 cooling 12s, ..." -- positions, never the keys."""
        keys, now = self.keys, time.monotonic()
        with self._lock:
            parts = []
            for i, key in enumerate(keys, 1):
                left = self._cooling.get(key, 0.0) - now
                parts.append(f"#{i} cooling {left:.0f}s" if left > 0 else f"#{i} free")
        return f"{len(keys)} key{'s' * (len(keys) != 1)}: " + ", ".join(parts) if keys else "no keys"


_POOLS: dict[str, KeyPool] = {}
_POOLS_LOCK = threading.Lock()


def pool(env_name: str) -> KeyPool:
    """The one shared pool for this environment variable."""
    with _POOLS_LOCK:
        if env_name not in _POOLS:
            _POOLS[env_name] = KeyPool(env_name)
        return _POOLS[env_name]
