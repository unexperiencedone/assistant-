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

import threading
import time
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

    # -- your projects on LinkedIn (publish/showcase.py) -------------------------------
    # Scanning a dozen repos and phrasing a post takes 15-30 seconds, so each of these
    # says it has started and finishes on its own thread.
    def _showcase(self) -> Any:
        showcase = getattr(self.publisher, "showcase", None) if self.publisher else None
        if showcase is None:
            self.say("The project showcase is turned off in the config.")
        return showcase

    def _showcase_in_background(self, opening: str, work: Any) -> None:
        self.say(opening)

        def run() -> None:
            try:
                self.say(work())
            except Exception as error:
                self.say(f"I couldn't put that together: {error}")

        threading.Thread(target=run, name="showcase", daemon=True).start()

    def _intent_showcase_draft(self, _text: str) -> None:
        showcase = self._showcase()
        if showcase is not None:
            self._showcase_in_background("Looking through your projects for one that isn't on LinkedIn yet.",
                                         showcase.draft)

    def _intent_showcase_project(self, _text: str, project: str = "", name: str = "") -> None:
        showcase = self._showcase()
        wanted = (project or name).strip()
        if showcase is not None:
            self._showcase_in_background(f"Writing up {wanted}.", lambda: showcase.draft(wanted))

    def start_articles(self, name: str, path: str | Path | None = None, post_text: str = "",
                       repo: str = "") -> str:
        """A Claude Code session in the portfolio writes the project's Story and Technical
        pieces, then stages the push (publish/articles.py). Returns what to say.

        A heavy job already running makes this one wait its turn on a thread, rather than
        being dropped: the articles are a standing rule, not a request to forget."""
        from ..agents.claude_cli import ClaudeCliAgent
        from ..publish import articles

        settings = getattr(self.settings, "articles", None)
        if settings is None or not settings.enabled:
            return "Portfolio articles are turned off in the config, under articles."
        portfolio = Path(settings.portfolio)
        if not portfolio.is_dir():
            return f"I can't find your portfolio at {portfolio}."
        prompt = articles.brief(name, path, portfolio, post_text, repo or articles.repo_url(post_text))
        label = f"portfolio articles for {name}"

        def start() -> str | None:
            agent = ClaudeCliAgent(self.settings.agents.claude, portfolio, False, "")
            agent.recorder = getattr(self.registry.current, "recorder", None)
            task_id = self.runner.start(agent, prompt, label, weight="heavy")
            if task_id:
                self.task_labels[task_id] = label
                self.bus.publish("route", utterance=label, route="agent", name="task", backend=agent.label, task=task_id)
            return task_id

        if not self.runner.blocked_by("heavy") and start():
            return f"Writing the {name} story and technical articles for your portfolio. They'll wait in Studio."

        def later() -> None:
            for _ in range(240):            # up to four hours, checked every minute
                time.sleep(60)
                if not self.runner.blocked_by("heavy") and start():
                    self.bus.log(f"started the {name} portfolio articles")
                    return
            self.bus.log(f"gave up waiting to start the {name} portfolio articles", "warn")

        threading.Thread(target=later, name="articles-wait", daemon=True).start()
        return f"I'll write the {name} portfolio articles as soon as the job that's running finishes."

    def _intent_portfolio_articles(self, _text: str, project: str = "") -> None:
        from ..publish import video

        found = video.project_for(self._showcase(), project.strip())
        if found is None:
            self.say(f"I couldn't find a project called {project} in your project folders.")
            return
        self.say(self.start_articles(found.name, found.path))

    def _intent_video_project(self, text: str, project: str = "") -> None:
        """A Claude Code session in the Remotion studio makes the video and stages it."""
        from ..agents.claude_cli import ClaudeCliAgent
        from ..publish import video

        settings = getattr(self.settings, "video", None)
        if settings is None or not settings.enabled:
            self.say("Making videos is turned off in the config, under video.")
            return
        studio = Path(settings.folder)
        if not studio.is_dir():
            self.say(f"I can't find the video studio at {studio}.")
            return
        found = video.project_for(self._showcase(), project.strip())
        if found is None:
            self.say(f"I couldn't find a project called {project} in your project folders.")
            return
        weight = "heavy"
        blocker = self.runner.blocked_by(weight)
        if blocker:
            self.say(f"A video is a big job, so I'll wait until {blocker.label} is done. Ask me again then.")
            return
        agent = ClaudeCliAgent(self.settings.agents.claude, studio, False, "")
        agent.recorder = getattr(self.registry.current, "recorder", None)
        label = f"video about {found.name}"
        prompt = video.brief(found.name, found.path, studio, settings.render_concurrency)
        task_id = self.runner.start(agent, prompt, label, weight=weight)
        if task_id is None:
            self.say("I couldn't start the video right now.")
            return
        self.task_labels[task_id] = label
        self.task_origins[task_id] = self.reply_to
        self.bus.publish("route", utterance=text, route="agent", name="task", backend=agent.label, task=task_id)
        self.say(f"Starting a video about {found.name}. Script, narration and render take a while; "
                 "it'll wait in Studio when it's done.")

    def _intent_showcase_unposted(self, _text: str) -> None:
        showcase = self._showcase()
        if showcase is not None:
            self._showcase_in_background("Checking your project folders.", showcase.spoken_unposted)

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
