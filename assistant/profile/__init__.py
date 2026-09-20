"""Your profile: who you are, what you're working on and how you like to work,
edited in the canvas and used to personalize every agent, speech recognition and
local commands. See docs/personalization.md."""

from .service import ProfileService, audience_for

__all__ = ["ProfileService", "audience_for"]
