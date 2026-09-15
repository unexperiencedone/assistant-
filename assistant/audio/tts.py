"""Text-to-speech with a background queue and instant interruption.

The speech engine is created and used only inside the worker thread, which is
what both SAPI (COM) and pyttsx3 require.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
from abc import ABC, abstractmethod

from ..config import VoiceSettings
from ..events import EventBus


def _for_speech(text: str) -> str:
    """Strip markdown and code so the voice doesn't read symbols aloud."""
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#>|~]+", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker(ABC):
    def __init__(self, settings: VoiceSettings, bus: EventBus) -> None:
        self.settings = settings
        self.bus = bus
        self._queue: queue.Queue[str] = queue.Queue()
        self._interrupt = threading.Event()
        self._speaking = threading.Event()
        self.last_active_at = 0.0  # used by the mic to discard self-heard audio
        self._thread = threading.Thread(target=self._worker, name="tts", daemon=True)
        self._thread.start()

    # -- public API -------------------------------------------------------
    def say(self, text: str) -> None:
        text = _for_speech(text)
        if text:
            self._queue.put(text)

    def stop(self) -> None:
        """Drop anything queued and cut off the current sentence."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._interrupt.set()

    @property
    def is_busy(self) -> bool:
        return self._speaking.is_set() or not self._queue.empty()

    def wait_idle(self, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        while self.is_busy and time.time() < deadline:
            time.sleep(0.05)

    # -- worker -----------------------------------------------------------
    def _worker(self) -> None:
        try:
            self._open()
        except Exception as exc:
            self.bus.log(f"Text-to-speech failed to start: {exc}", "error")
            return
        while True:
            text = self._queue.get()
            self._interrupt.clear()
            self._speaking.set()
            self.last_active_at = time.time()
            self.bus.status("speaking")
            try:
                self._speak(text)
            except Exception as exc:
                self.bus.log(f"Speech error: {exc}", "warn")
            finally:
                self.last_active_at = time.time()
                if self._queue.empty():
                    self._speaking.clear()
                    self.bus.status("idle")  # the microphone switches this to "listening" when it resumes

    @abstractmethod
    def _open(self) -> None: ...

    @abstractmethod
    def _speak(self, text: str) -> None:
        """Speak synchronously, returning early once self._interrupt is set."""


class SapiSpeaker(Speaker):
    """Windows SAPI via COM: asynchronous speech that can be purged mid-word."""

    ASYNC, PURGE, NOT_XML = 1, 2, 16

    def _open(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._pump = comtypes.client.PumpEvents
        self._voice = comtypes.client.CreateObject("SAPI.SpVoice")
        wanted = self.settings.voice_contains.lower()
        if wanted:
            tokens = self._voice.GetVoices()
            for i in range(tokens.Count):
                if wanted in tokens.Item(i).GetDescription().lower():
                    self._voice.Voice = tokens.Item(i)
                    break
        self._voice.Rate = max(-10, min(10, self.settings.rate))
        self._voice.Volume = max(0, min(100, self.settings.volume))

    def _speak(self, text: str) -> None:
        self._voice.Speak(text, self.ASYNC | self.NOT_XML)
        # SAPI signals completion through window messages on this (single-threaded COM)
        # thread; without pumping them WaitUntilDone never reports done, is_busy stays
        # True forever, and the half-duplex microphone never gets to listen.
        while not self._voice.WaitUntilDone(0):
            if self._interrupt.is_set():
                self._voice.Speak("", self.ASYNC | self.PURGE)
                return
            self._pump(0.05)


class Pyttsx3Speaker(Speaker):
    """Cross-platform fallback (espeak / nsss / sapi5 under the hood)."""

    def _open(self) -> None:
        import pyttsx3

        self._engine = pyttsx3.init()
        wanted = self.settings.voice_contains.lower()
        if wanted:
            for voice in self._engine.getProperty("voices"):
                if wanted in voice.name.lower():
                    self._engine.setProperty("voice", voice.id)
                    break
        if self.settings.rate > 20:
            self._engine.setProperty("rate", self.settings.rate)
        self._engine.setProperty("volume", self.settings.volume / 100)
        # Checked between words, on the engine's own loop.
        self._engine.connect("started-word", lambda **_: self._interrupt.is_set() and self._engine.stop())

    def _speak(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()


def make_speaker(settings: VoiceSettings, bus: EventBus) -> Speaker:
    engine = settings.engine
    if engine == "auto":
        engine = "sapi" if sys.platform == "win32" else "pyttsx3"
    return SapiSpeaker(settings, bus) if engine == "sapi" else Pyttsx3Speaker(settings, bus)
