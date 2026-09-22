"""Talking to the phone.

The other half of `phone/nova_bridge.py`: Nova asks the phone to do one of a fixed set
of things, over Tailscale, with a shared secret. It never sends shell — only a named
action and its arguments, because the phone only understands named actions.

Anything destructive (a text message, right now) is refused by the phone unless the
request says a human confirmed it, and Nova only sets that flag after asking — on the
device the request came from, which is what `controller.reply_to` is for.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

# What Nova can ask for, and how to say it afterwards. Keeping the phrasing here means
# the controller stays a router rather than a script.
ACTIONS = {
    "battery": "Battery is {output}.",
    "torch_on": "Torch on.",
    "torch_off": "Torch off.",
    "vibrate": "Buzzing your phone.",
    "notify": "Sent it to your phone.",
    "open": "Opening it on your phone.",
    "clipboard_get": "Your phone's clipboard says: {output}",
    "clipboard_set": "Copied to your phone.",
    "volume": "Volume set.",
    "location": "Location: {output}",
    "find_phone": "{output}",   # the phone says what it actually managed to do
    "sms_send": "Sent.",
    "call_dial": "{output}.",   # the phone reports what really happened, so repeat it
    "contacts": "{output}",
    "sms_list": "{output}",
    "call_log": "{output}",
    "notifications": "{output}",
    "camera_photo": "Took a picture.",   # the caller says where it landed
}

NEEDS_CONFIRMATION = {"sms_send", "call_dial"}

# Reading, rather than doing: each of these is off unless the Phone section of the
# profile says otherwise (assistant/profile/schema.py). "Doing" actions are not here,
# because turning a torch on reveals nothing.
SENSING = {
    "contacts": "contacts",
    "sms_list": "messages",
    "call_log": "call_log",
    "notifications": "notifications",
    # Both of these were "doing" for a while, which was wrong: where you are and what
    # you last copied are as personal as a text message, and the clipboard routinely
    # holds a password on its way somewhere.
    "location": "location",
    "clipboard_get": "clipboard",
    # A camera is the most invasive of these, so it is off unless you switch it on.
    "camera_photo": "camera",
}


# What the agent may act on straight from a marker: things whose worst case is a
# wasted second. Anything that reaches outward -- opening a URL, writing the clipboard,
# a text, a call -- is confirmed first, because a marker may have come from a message
# somebody else wrote. See defuse() and controller._run_phone_markers.
MARKER_DIRECT = {"torch_on", "torch_off", "vibrate", "battery", "notify", "find_phone"}


def defuse(text: str) -> str:
    """Make any marker inside read content inert before it can be quoted back.

    Nova cannot tell the agent's own words from something the agent is repeating, so
    content never gets to carry a live instruction in the first place.
    """
    return (text or "").replace("[[", "[\u200b[")


def sensing_allowed(profile: dict | None, action: str) -> bool:
    """Whether the profile lets Nova read this from the phone.

    `profile` is the profile dict itself (ProfileService.get()), so this stays a pure
    function and the tests can hand it one.
    """
    if action not in SENSING:
        return True                       # doing something is not reading something
    section = (profile or {}).get("phone") or {}
    if not section.get("sensing", False):
        return False                      # the master switch is off, or there is no profile
    return bool(section.get(SENSING[action], False))


class PhoneError(RuntimeError):
    """The phone could not be reached, or refused."""


class PhoneBridge:
    """A thin client for the listener running in Termux."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def token(self) -> str:
        """The phone's shared secret. It lives in .env, never in config.toml, because
        config.toml is committed and this is a credential."""
        return os.environ.get(getattr(self.settings, "token_env", "") or "", "").strip()

    @property
    def configured(self) -> bool:
        return bool(getattr(self.settings, "enabled", False) and getattr(self.settings, "host", "") and self.token)

    @property
    def url(self) -> str:
        host = getattr(self.settings, "host", "")
        port = getattr(self.settings, "port", 8766)
        return f"http://{host}:{port}"

    def call(self, action: str, args: dict | None = None, confirmed: bool = False) -> dict:
        """Ask the phone to do one thing. Raises PhoneError with something sayable."""
        if not self.configured:
            raise PhoneError("The phone bridge isn't set up yet: see phone/README.md.")
        if action not in ACTIONS:
            raise PhoneError(f"I don't know how to {action} on your phone.")
        args = dict(args or {})
        # Dual-SIM: say which card, rather than letting the phone pick. Only a text can
        # be aimed at a slot -- a call goes out on whichever SIM Android dials with.
        if action == "sms_send" and "sim" not in args:
            args["sim"] = int(getattr(self.settings, "sim_slot", 0) or 0)
        body = json.dumps({"action": action, "args": args, "confirmed": confirmed}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/run", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Nova-Token": self.token})
        try:
            with urllib.request.urlopen(request, timeout=getattr(self.settings, "timeout", 15)) as response:
                answer = json.loads(response.read() or b"{}")
            if isinstance(answer.get("output"), str):
                # Everything the phone reports is content, and content never instructs.
                answer["output"] = defuse(answer["output"])
            return answer
        except urllib.error.HTTPError as exc:
            detail = self._detail(exc)
            if exc.code == 401:
                raise PhoneError("Your phone rejected the token. Re-copy it from the bridge.") from exc
            if exc.code == 409:
                raise PhoneError(detail or "That needs confirming first.") from exc
            raise PhoneError(detail or f"Your phone said no ({exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise PhoneError("I can't reach your phone. Is Tailscale on and the bridge running?") from exc
        except (OSError, ValueError) as exc:
            raise PhoneError(f"The phone bridge went wrong: {exc}") from exc

    def reachable(self) -> bool:
        """A quick yes/no, for "is my phone there" rather than for doing anything."""
        if not self.configured:
            return False
        request = urllib.request.Request(f"{self.url}/health", headers={"X-Nova-Token": self.token})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read() or b"{}").get("ok", False)
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @staticmethod
    def _detail(exc: urllib.error.HTTPError) -> str:
        try:
            return str(json.loads(exc.read() or b"{}").get("error", ""))
        except (ValueError, OSError):
            return ""

    @staticmethod
    def phrase(action: str, result: dict) -> str:
        """What to say once it worked."""
        template = ACTIONS.get(action, "Done.")
        output = " ".join(str(result.get("output", "")).split())[:200]
        return template.format(output=output) if "{output}" in template else template


def find_number(contacts_json: str, name: str) -> tuple[str, str]:
    """(matched name, number) for the closest contact, or ("", "") when nothing is close.

    `termux-contact-list` returns [{"name": ..., "number": ...}]. Matching is deliberate
    about being sure: an exact or prefix match first, then one close spelling, and
    nothing at all when two contacts are equally close — Nova asks rather than dials a
    guess.
    """
    try:
        contacts = json.loads(contacts_json or "[]")
    except ValueError:
        return "", ""
    wanted = " ".join((name or "").split()).lower()
    if not wanted or not isinstance(contacts, list):
        return "", ""

    rows = [(str(c.get("name", "")), str(c.get("number", ""))) for c in contacts
            if isinstance(c, dict) and c.get("number")]
    exact = [r for r in rows if r[0].lower() == wanted]
    if exact:
        return exact[0]
    starts = [r for r in rows if r[0].lower().startswith(wanted)]
    if len(starts) == 1:
        return starts[0]
    if len(starts) > 1:
        # Several rows, but one person with a work and a home line is not a tie: the
        # phone lists every number under the same name. Two different Sams still is.
        if len({r[0].lower() for r in starts}) == 1:
            return starts[0]
        return "", ""                       # "call sam" with two Sams: ask, do not guess
    close = difflib.get_close_matches(wanted, [r[0].lower() for r in rows], n=2, cutoff=0.8)
    if len(close) == 1:
        return next(r for r in rows if r[0].lower() == close[0])
    return "", ""


def find_numbers(contacts_json: str, name: str) -> list[tuple[str, str]]:
    """Every number filed under the matched person, best match first.

    `find_number` answers "who do I dial"; this answers "what else is under that
    name", for when the first line does not pick up.
    """
    matched, _ = find_number(contacts_json, name)
    if not matched:
        return []
    try:
        contacts = json.loads(contacts_json or "[]")
    except ValueError:
        return []
    if not isinstance(contacts, list):
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entry in contacts:
        if not isinstance(entry, dict) or str(entry.get("name", "")).lower() != matched.lower():
            continue
        number = str(entry.get("number", "")).strip()
        key = "".join(ch for ch in number if ch.isdigit())[-10:]
        if not number or key in seen:
            continue
        seen.add(key)
        out.append((matched, number))
    return out


# What the agent writes when a phrase missed the instant command: one action per line.
MARKER = re.compile(r"\[\[PHONE:\s*(?P<action>[a-z_]+)(?P<args>[^\]]*)\]\]", re.I)
_ARG = re.compile(r"(?P<key>[a-z_]+)\s*=\s*\"(?P<value>[^\"]*)\"", re.I)


def parse_markers(text: str) -> list[tuple[str, dict]]:
    """Every [[PHONE: ...]] in an agent reply, as (action, args)."""
    found = []
    for hit in MARKER.finditer(text or ""):
        action = hit.group("action").lower()
        if action not in ACTIONS:
            continue
        found.append((action, {m.group("key").lower(): m.group("value") for m in _ARG.finditer(hit.group("args"))}))
    return found


def strip_markers(text: str) -> str:
    """The reply without them: they are instructions to Nova, not words to read out."""
    return " ".join(MARKER.sub("", text or "").split())
