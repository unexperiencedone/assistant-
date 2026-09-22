"""The voice handlers for capture and publishing.

Kept out of `controller.py` because that file is already the busiest thing in the repo,
and these six handlers form one story: record, photograph, draft, approve. They are
mixed into the controller as `CaptureCommands`, so `_intent_<name>` lookup still finds
them exactly as if they were written there.

Every one of them is deliberately thin. The judgement lives in `assistant/capture` and
`assistant/publish`; what happens here is turning a spoken phrase into one call and
saying the honest answer back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .phone import bridge as phone_bridge


class CaptureCommands:
    # These attributes are set by Controller.__init__; declared here for the reader.
    capture: Any
    publisher: Any
    capture_settings: Any
    profile: Any
    phone: Any

    # -- recording ---------------------------------------------------------------------
    def _intent_record_screen(self, _text: str) -> None:
        if self.capture is None:
            self.say("Screen recording is turned off in the config.")
            return
        fps = int(getattr(self.capture_settings, "fps", 25) or 25)
        monitor = int(getattr(self.capture_settings, "monitor", 1) or 0) or None
        audio = bool(getattr(self.capture_settings, "audio", False))
        ok, detail = self.capture.start(fps=fps, audio=audio, monitor=monitor)
        self.say(detail if ok else f"I couldn't start recording. {detail}")

    def _intent_stop_recording(self, _text: str) -> None:
        if self.capture is None:
            self.say("Screen recording is turned off in the config.")
            return
        ok, detail = self.capture.stop()
        self.say(detail if ok else detail)

    # -- the phone's camera ------------------------------------------------------------
    def _intent_phone_photo(self, _text: str, which: str = "") -> None:
        """A camera is a sensor, so the profile decides -- and Nova says it took one."""
        if not self._phone_may_read("camera_photo"):
            return
        if self.capture is None:
            self.say("Capture is turned off in the config, so I have nowhere to put a photo.")
            return
        from .capture import phone as phone_camera

        camera = "1" if which.lower() in ("front", "selfie") else "0"
        ok, detail, _path = phone_camera.photo(self.phone, self.capture.base, camera)
        self.say(detail)

    # -- publishing --------------------------------------------------------------------
    def _intent_publish_waiting(self, _text: str) -> None:
        if self.publisher is None:
            self.say("Publishing is turned off in the config.")
            return
        self.say(self.publisher.waiting())

    def _intent_publish_approve(self, _text: str) -> None:
        if self.publisher is None:
            self.say("Publishing is turned off in the config.")
            return
        self.say(self.publisher.approve())

    def _intent_publish_discard(self, _text: str) -> None:
        if self.publisher is None:
            self.say("Publishing is turned off in the config.")
            return
        self.say(self.publisher.discard())

    # -- provided by Controller --------------------------------------------------------
    def say(self, text: str) -> None:  # pragma: no cover - the real one lives on Controller
        raise NotImplementedError

    def _phone_may_read(self, action: str) -> bool:  # pragma: no cover - same
        data = self.profile.get() if self.profile else None
        if phone_bridge.sensing_allowed(data, action):
            return True
        self.say("Reading that from your phone is switched off in your profile, under Phone.")
        return False


def inbox_folder(settings: Any, base_dir: Path) -> Path:
    """Where captures land, resolved the same way every other configured path is."""
    import os

    folder = Path(os.path.expandvars(getattr(settings, "inbox", "data/inbox"))).expanduser()
    return folder if folder.is_absolute() else base_dir / folder
