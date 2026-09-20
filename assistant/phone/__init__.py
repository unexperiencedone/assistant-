"""Nova's control of your phone, over Tailscale.

`bridge.py` is the client; the listener it talks to lives in `phone/nova_bridge.py`
and runs inside Termux on the phone itself. Setup: `phone/README.md`.
"""

from .bridge import ACTIONS, NEEDS_CONFIRMATION, PhoneBridge, PhoneError

__all__ = ["ACTIONS", "NEEDS_CONFIRMATION", "PhoneBridge", "PhoneError"]
