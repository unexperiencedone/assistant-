"""Publishing: the arms that reach GitHub, LinkedIn and Instagram, and the one gate in
front of all of them. Nothing here sends without an explicit yes. See docs/publishing.md."""

from .gate import PublishGate, describe
from .service import PublishService

__all__ = ["PublishGate", "PublishService", "describe"]
