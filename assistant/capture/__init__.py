"""Capture and edit: screen recordings, phone photos, and a local ffmpeg pipeline that
turns them into something postable. See docs/standing_agent.md."""

from . import edit, inbox, phone
from .screen import ScreenRecorder

__all__ = ["ScreenRecorder", "edit", "inbox", "phone"]
