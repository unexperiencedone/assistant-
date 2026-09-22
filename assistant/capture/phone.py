"""Taking a picture with the phone's camera and bringing it back here.

The bridge speaks JSON and nothing else -- there is no file server on the phone -- so
the photo comes home inside the reply as base64 and is written into the inbox. Capped
at 8 MB on the phone side, which a JPEG from a phone camera sits comfortably under.

Two things guard this, and both are deliberate:

- It is a **sensor**, so it is gated by the Phone section of the profile like the
  clipboard and location, and it is off until switched on (`assistant/phone/bridge.py`).
- Nova **says it took one**, every time. A camera that can be triggered quietly is a
  different and much worse product than this one.

Not yet run against the phone: `camera_photo` is new in `phone/nova_bridge.py` and the
listener has to be redeployed and restarted there before this can work.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from . import inbox


def photo(bridge: Any, base: Path, camera: str = "0") -> tuple[bool, str, Path | None]:
    """Take one picture. Returns (worked, what to say, where it landed)."""
    if bridge is None or not getattr(bridge, "configured", False):
        return False, "The phone bridge isn't set up, so I can't reach the camera.", None
    try:
        result = bridge.call("camera_photo", {"camera": camera})
    except Exception as error:  # PhoneError and anything urllib throws under it
        return False, f"I couldn't reach your phone: {error}", None

    payload = _payload(result)
    if payload is None:
        return False, "The phone answered, but not with a picture.", None
    if not payload.get("ok"):
        return False, f"The camera didn't work: {payload.get('error', 'no reason given')}", None
    try:
        raw = base64.b64decode(payload.get("jpeg_base64", ""), validate=True)
    except (ValueError, TypeError):
        return False, "The picture came back damaged, so I threw it away.", None
    if not raw:
        return False, "The picture came back empty.", None

    path = inbox.new_path(base, "photo", ".jpg")
    try:
        path.write_bytes(raw)
    except OSError as error:
        return False, f"I couldn't save the picture: {error}", None
    which = "front" if camera == "1" else "back"
    return True, f"Took a picture on the {which} camera: {inbox.describe(path)}.", path


def _payload(result: Any) -> dict[str, Any] | None:
    """The bridge hands back its own envelope; the picture is the JSON inside `output`."""
    if isinstance(result, dict):
        body = result.get("output", result)
        if isinstance(body, dict):
            return body
        try:
            parsed = json.loads(body or "")
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
