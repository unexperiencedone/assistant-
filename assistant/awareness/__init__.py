"""What you were doing on this PC: app and window titles, collapsed into sessions.

Opt-in, local, and deliberately shallow — see `collector.py` for exactly what is
collected, `collapse.py` for how a stream of focus events becomes a record worth
keeping, and `service.py` for the questions it can answer.
"""

from .service import AwarenessService
from .store import AwarenessStore

__all__ = ["AwarenessService", "AwarenessStore"]
