#!/data/data/com.termux/files/usr/bin/python
"""Nova's hands on the phone: a tiny HTTP listener for Termux.

Runs on the phone, inside Termux, and does exactly what Nova asks of it — turn the
torch on, open a link, read the battery, send a text — by calling the `termux-*`
commands that Termux:API installs. Nothing else.

Three things keep it honest:

* **It listens on Tailscale only.** The socket is bound to the phone's own 100.x
  address, never 0.0.0.0, so a cafe network cannot see it at all.
* **Every request carries a shared secret** (`X-Nova-Token`), kept in
  `~/.nova_bridge_token` and generated on first run.
* **Only named actions run.** There is no "run this shell command" endpoint; the
  actions below are the whole vocabulary, and the destructive one refuses to fire
  without `"confirmed": true`.

    python nova_bridge.py                 # finds the Tailscale address itself
    python nova_bridge.py --host 100.x.x.x --port 8766
    python nova_bridge.py --print-token   # what to paste into Nova's config

Setup, autostart and the full list of actions: README.md next to this file.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOKEN_FILE = Path.home() / ".nova_bridge_token"
HEADER = "X-Nova-Token"
TIMEOUT = 20
MAX_BODY = 64 * 1024   # a command is a line of text; anything larger is not one

# How much of a command's output comes back. A status line is short, but a contact
# list is a few hundred names -- cutting it at 2 KB used to hide everyone past the
# letter B, so a lookup for a real contact quietly found nothing. The reads that are
# genuinely lists get room for all of it; everything else stays small on purpose.
OUTPUT_LIMIT = 2000
OUTPUT_LIMITS = {
    "contacts": 400_000,
    "sms_list": 60_000,
    "call_log": 60_000,
    "notifications": 20_000,
}

# action -> the command it runs. `None` means the handler below builds it, because it
# needs an argument. Adding to this dict is the only way to add a capability.
ACTIONS = {
    "health": None,
    "battery": ["termux-battery-status"],
    "torch_on": ["termux-torch", "on"],
    "torch_off": ["termux-torch", "off"],
    "vibrate": ["termux-vibrate", "-d", "800"],
    "notify": None,     # {"title", "text"}
    "open": None,       # {"target": url or package}
    "clipboard_get": ["termux-clipboard-get"],
    "clipboard_set": None,   # {"text"}
    "volume": None,     # {"stream", "level"}
    "location": ["termux-location", "-p", "network"],
    "find_phone": None,      # {"times"} -- says where it is, loudly
    # Reading, rather than doing. Each one is gated by the Phone section of the
    # profile on the PC before Nova ever asks for it.
    "contacts": ["termux-contact-list"],
    "sms_list": None,        # {"limit"}
    "call_log": None,        # {"limit"}
    "notifications": ["termux-notification-list"],
    # The camera. Gated on the PC like every other sensor, and the picture comes back
    # in the reply rather than being left on the phone -- there is no file server here.
    "camera_photo": None,    # {"camera": "0" back | "1" front}
    "sms_send": None,   # {"number", "text"} — destructive: needs "confirmed": true
    "call_dial": None,  # {"number"}          — the same, and louder
}

# Dual-SIM: which card a text goes out on when the request does not say (0 is SIM 1).
# A call cannot be aimed this way -- termux-telephony-call uses the phone's default
# calling SIM, so set that in Android's SIM settings.
SIM_SLOT = 0

# Anything here refuses to run unless the request says the human confirmed it.
NEEDS_CONFIRMATION = {"sms_send", "call_dial"}

# Apps worth opening by name. An app's own scheme is what Android hands to it, so
# these are links rather than package names — and an unknown name is refused rather
# than guessed at.
APP_LINKS = {
    "spotify": "spotify://",
    "youtube": "https://www.youtube.com",
    "maps": "geo:0,0",
    "gmail": "https://mail.google.com",
    "whatsapp": "https://wa.me",
    "camera": "termux-camera://",
    "settings": "https://www.google.com/android/settings",
}

# Where a photo is written before it is read back and deleted. Termux's own folder, so
# it needs no storage permission beyond the one the camera API already asked for.
PHOTO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova-photo.jpg")
MAX_PHOTO_BYTES = 8 * 1024 * 1024

SAFE_TARGET = re.compile(r"^[A-Za-z0-9 _.:/?=&%#+~@,-]{1,400}$")
# Digits, spaces, brackets, dashes and a leading +. Nothing else reaches the dialler.
PHONE_NUMBER = re.compile(r"^\+?[0-9][0-9 ()-]{2,19}$")


def load_token() -> str:
    """The shared secret, generated on first run and kept readable by you alone."""
    try:
        existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token


def tailscale_address() -> str:
    """The phone's own 100.x address, so the socket binds to Tailscale and nothing else."""
    for command in (["ip", "-4", "addr"], ["ifconfig"]):
        if not shutil.which(command[0]):
            continue
        try:
            output = subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = re.search(r"\b(100\.(?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3}))\b", output)
        if found:
            return found.group(1)
    return ""


def run(command: list[str]) -> tuple[bool, str]:
    if not shutil.which(command[0]):
        return False, f"{command[0]} is not installed. Is Termux:API set up?"
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"{command[0]} took too long"
    except OSError as exc:
        return False, str(exc)
    text = (done.stdout or done.stderr or "").strip()
    return done.returncode == 0, text


# Exit code 0 only means Android took the intent, not that it acted on it. Anything
# that starts an activity can be dropped silently -- most often because the screen is
# locked and a background app may not start one -- so a call is checked, never assumed.
CALL_STATES = {"0": "idle", "1": "ringing", "2": "in a call"}


def call_state() -> str:
    """What the phone is doing: "idle", "ringing", "in a call", or "" if it won't say."""
    ok, output = run(["dumpsys", "telephony.registry"])
    if ok:
        found = re.search(r"mCallState=(\d)", output)
        if found:
            return CALL_STATES.get(found.group(1), "")
    ok, output = run(["termux-telephony-deviceinfo"])
    if ok:
        try:
            state = str(json.loads(output or "{}").get("call_state", "")).lower()
        except ValueError:
            state = ""
        if state in ("ringing", "idle"):
            return state
        if state == "offhook":
            return "in a call"
    return ""


def confirm_dial() -> tuple[bool, str]:
    """Did the call actually start? Reported honestly, "don't know" included."""
    for _ in range(4):        # the dialler needs a moment to come up
        time.sleep(1)
        state = call_state()
        if not state:
            return True, "dialled, but this phone won't say whether the call started"
        if state != "idle":
            return True, f"calling ({state})"
    return False, ("the dialler never left idle, so Android dropped the call -- most likely "
                   "the screen is locked and Termux may not start an activity. Grant Termux "
                   "'Display over other apps' and the Phone permission, then try again")


def stream_max(stream: str = "music") -> tuple[int, int]:
    """(current, max) for a volume stream. Devices differ, so ask rather than assume 15."""
    ok, output = run(["termux-volume"])
    if not ok:
        return -1, 0
    try:
        for row in json.loads(output or "[]"):
            if isinstance(row, dict) and row.get("stream") == stream:
                return int(row.get("volume", -1)), int(row.get("max_volume", 0))
    except (ValueError, TypeError):
        pass
    return -1, 0


def tts_engines() -> list:
    """Which speech engines the phone has. An empty list means it cannot speak.

    `termux-tts-speak` exits 0 with no engine and no voice data, and simply makes no
    sound -- exactly the failure this file learned about with call_dial. So ask first
    rather than believe an exit code.
    """
    ok, output = run(["termux-tts-engines"])
    if not ok:
        return []
    try:
        found = json.loads(output or "[]")
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def ring_out(line: str) -> bool:
    """Make a noise without speech: a notification with its sound, plus a long buzz."""
    ok, _ = run(["termux-notification", "--title", "Nova", "--content", line,
                 "--priority", "high", "--sound", "--id", "nova-find-phone"])
    run(["termux-vibrate", "-d", "1500"])
    return ok


def find_phone(times: int = 3) -> tuple[bool, str]:
    """Start shouting, and answer at once.

    Speaking three sentences takes several seconds, and holding the HTTP request open
    for all of it just means the caller times out while the phone is mid-sentence. The
    only thing worth waiting for is whether this phone can speak at all, which is quick.
    """
    engines = tts_engines()
    threading.Thread(target=_shout, args=(times, bool(engines)), daemon=True).start()
    return True, ("Shouting now, listen for it." if engines else
                  "I can't find a speech engine on your phone, so it's ringing and "
                  "buzzing as well. Listen for it.")


def _shout(times: int, can_speak: bool) -> None:
    """The noisy part, on its own thread."""
    was, loudest = stream_max("music")
    if loudest:
        run(["termux-volume", "music", str(loudest)])
    run(["termux-vibrate", "-d", "1000"])
    rounds = max(1, min(5, int(times)))

    for turn in range(rounds):
        # Said as if answering you, and a little different each time: the same sentence
        # on a loop is harder to place by ear than a voice that keeps going.
        line = ("Here is your phone.", "Over here. Here is your phone.",
                "Still here. Here is your phone.")[turn % 3]
        # Always try to speak, whatever the engine probe said. The probe is one more
        # command that can be slow or flaky, and it must never be the thing that leaves
        # the phone silent -- speaking with no engine costs nothing but a wasted call.
        run(["termux-tts-speak", "-s", "MUSIC", line])
        if not can_speak:
            ring_out(line)           # belt and braces when we think it cannot speak
        time.sleep(1.2)              # long enough for the sentence to finish

    if loudest and was >= 0:
        run(["termux-volume", "music", str(was)])   # a torch, not a new setting


def shape(action: str, args: dict, output: str) -> str:
    """Trim a command's output on the phone, where the whole list still exists.

    Only `contacts` with a `match` does anything: it keeps every entry whose name
    contains the wanted text, so a person with three numbers comes back with all
    three and the reply stays a few lines instead of the whole address book.
    """
    if action == "camera_photo":
        # termux-camera-photo prints nothing; the picture is the file it wrote. Read it
        # back, hand it over inside the reply, and delete it -- a photo Nova took should
        # not quietly accumulate on the phone.
        try:
            with open(PHOTO_PATH, "rb") as handle:
                raw = handle.read(MAX_PHOTO_BYTES + 1)
        except OSError as error:
            return json.dumps({"ok": False, "error": f"no photo was written: {error}"})
        finally:
            try:
                os.remove(PHOTO_PATH)
            except OSError:
                pass
        if len(raw) > MAX_PHOTO_BYTES:
            return json.dumps({"ok": False, "error": "that photo is larger than 8 MB"})
        return json.dumps({"ok": True, "bytes": len(raw),
                           "jpeg_base64": base64.b64encode(raw).decode("ascii")})
    if action != "contacts":
        return output
    wanted = " ".join(str(args.get("match", "")).split()).lower()
    if not wanted:
        return output
    try:
        contacts = json.loads(output or "[]")
    except ValueError:
        return output
    if not isinstance(contacts, list):
        return output
    hit = [c for c in contacts if isinstance(c, dict)
           and wanted in str(c.get("name", "")).lower()]
    # Nothing matched: hand back the full list rather than an empty one, so the PC
    # can still try a fuzzy match of its own.
    return json.dumps(hit, ensure_ascii=False) if hit else output


def build(action: str, args: dict) -> tuple[list[str] | None, str]:
    """Turn a named action into the exact command to run, or say why it cannot."""
    fixed = ACTIONS.get(action)
    if fixed:
        return fixed, ""
    if action == "notify":
        title = str(args.get("title", "Nova"))[:80]
        text = str(args.get("text", ""))[:400]
        return ["termux-notification", "--title", title, "--content", text], ""
    if action == "open":
        target = str(args.get("target", "")).strip()
        if not SAFE_TARGET.match(target):
            return None, "that target has characters I will not pass on"
        if "://" in target:
            return ["termux-open-url", target], ""
        known = APP_LINKS.get(target.lower())
        if known:
            return ["termux-open-url", known], ""
        if "." in target:                      # looks like a domain
            return ["termux-open-url", f"https://{target}"], ""
        # An app nobody taught me: say so rather than opening a guess.
        return None, f"I do not know how to open {target}; add it to APP_LINKS in nova_bridge.py"
    if action == "camera_photo":
        camera = str(args.get("camera", "0")).strip()
        if camera not in ("0", "1"):       # 0 is the back camera, 1 the front one
            return None, "camera must be 0 (back) or 1 (front)"
        return ["termux-camera-photo", "-c", camera, PHOTO_PATH], ""
    if action == "clipboard_set":
        return ["termux-clipboard-set", str(args.get("text", ""))[:2000]], ""
    if action == "volume":
        stream = str(args.get("stream", "music"))
        if stream not in ("music", "call", "system", "ring", "alarm", "notification"):
            return None, "unknown volume stream"
        level = max(0, min(15, int(args.get("level", 5))))
        return ["termux-volume", stream, str(level)], ""
    if action == "sms_send":
        number = str(args.get("number", "")).strip()
        text = str(args.get("text", "")).strip()
        if not PHONE_NUMBER.match(number):
            return None, "that does not look like a number"
        if not text:
            return None, "nothing to send"
        slot = args.get("sim", SIM_SLOT)
        try:
            slot = max(0, min(3, int(slot)))
        except (TypeError, ValueError):
            slot = SIM_SLOT
        return ["termux-sms-send", "-s", str(slot), "-n", number, text[:600]], ""
    if action == "sms_list":
        limit = max(1, min(50, int(args.get("limit", 10))))
        return ["termux-sms-list", "-l", str(limit)], ""
    if action == "call_log":
        limit = max(1, min(50, int(args.get("limit", 10))))
        return ["termux-call-log", "-l", str(limit)], ""
    if action == "call_dial":
        number = str(args.get("number", "")).strip()
        if not PHONE_NUMBER.match(number):
            return None, "that does not look like a number"
        return ["termux-telephony-call", number], ""
    return None, f"I do not know how to {action}"


class Handler(BaseHTTPRequestHandler):
    server_version = "NovaBridge/1.0"
    token = ""

    def log_message(self, fmt, *args):  # one readable line per request, not two
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def reply(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get(HEADER, ""), self.token)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if not self.authorised():
            self.reply(401, {"ok": False, "error": "bad or missing token"})
            return
        if self.path.startswith("/health"):
            self.reply(200, {"ok": True, "device": os.environ.get("HOSTNAME", "phone"),
                             "actions": sorted(ACTIONS)})
            return
        self.reply(404, {"ok": False, "error": "no such path"})

    def do_POST(self) -> None:  # noqa: N802
        # Read the body before judging the request: answering 401 with the body still
        # in flight resets the connection, and the caller sees a socket error instead
        # of being told its token was wrong.
        try:
            length = min(int(self.headers.get("Content-Length", 0) or 0), MAX_BODY)
            raw = self.rfile.read(length) if length else b"{}"
        except (ValueError, OSError):
            self.reply(400, {"ok": False, "error": "send me JSON"})
            return
        if not self.authorised():
            self.reply(401, {"ok": False, "error": "bad or missing token"})
            return
        if not self.path.startswith("/run"):
            self.reply(404, {"ok": False, "error": "no such path"})
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self.reply(400, {"ok": False, "error": "send me JSON"})
            return

        action = str(body.get("action", "")).strip()
        args = body.get("args") or {}
        if action not in ACTIONS:
            self.reply(400, {"ok": False, "error": f"I do not know how to {action or '(nothing)'}"})
            return
        if action in NEEDS_CONFIRMATION and not body.get("confirmed"):
            self.reply(409, {"ok": False, "error": f"{action} needs confirming first", "needs_confirmation": True})
            return

        args = args if isinstance(args, dict) else {}
        if action == "find_phone":
            # Not one command but a performance, so it never goes through build()/run().
            ok, output = find_phone(args.get("times", 3))
        else:
            command, problem = build(action, args)
            if command is None:
                self.reply(400, {"ok": False, "error": problem})
                return
            ok, output = run(command)
            if ok:
                output = shape(action, args, output)
            if ok and action == "call_dial":
                ok, output = confirm_dial()   # never claim a call Android quietly dropped
        limit = OUTPUT_LIMITS.get(action, OUTPUT_LIMIT)
        self.reply(200 if ok else 500, {"ok": ok, "action": action, "output": output[:limit]})


def main() -> int:
    parser = argparse.ArgumentParser(description="Nova's Termux bridge")
    parser.add_argument("--host", default="", help="address to bind (default: this phone's Tailscale address)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--print-token", action="store_true", help="show the shared secret and exit")
    options = parser.parse_args()

    token = load_token()
    if options.print_token:
        print(token)
        return 0

    host = options.host or tailscale_address()
    if not host:
        print("No Tailscale address found. Is Tailscale connected? Pass --host to override.", file=sys.stderr)
        return 1
    if not host.startswith("100.") and host not in ("127.0.0.1", "localhost"):
        # Refusing here is the whole point: a bridge on every interface is a bridge for
        # every cafe as well.
        print(f"Refusing to bind {host}: use the Tailscale address (100.x.x.x).", file=sys.stderr)
        return 1

    Handler.token = token
    server = HTTPServer((host, options.port), Handler)
    print(f"Nova bridge listening on http://{host}:{options.port} (Tailscale only)")
    print(f"Token is in {TOKEN_FILE}. Put it in Nova's [phone] token on the PC.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
