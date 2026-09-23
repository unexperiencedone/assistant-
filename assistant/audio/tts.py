"""Text-to-speech with a background queue and instant interruption.

Speech is local: the Windows voice (SAPI) on this machine, pyttsx3 elsewhere. A
cloud voice sounds better but the free tiers rate-limit within a few sentences,
which leaves Nova silent mid-reply, so naturalness comes from punctuation instead
(see `_for_speech`). Local speech also costs nothing and never waits on a network.

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


_SPOKEN_SYMBOLS = {"&": " and ", "%": " percent ", "@": " at ", "+": " plus ", "=": " equals ", "×": " times "}


def _for_speech(text: str) -> str:
    """Make text sound like speech: no markdown or symbols, and punctuation where a
    person would pause. Speech engines take their rhythm from commas and full stops,
    so text without them is read as one breathless run-on."""
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#>|~]+", "", text)
    # Square brackets are left alone: a delivery tag like [long pause] is meaningful to
    # a neural voice and is stripped further down the chain for one that isn't.
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)        # list bullets read as pauses, not dashes
    text = re.sub(r"\s+[-–—]\s+", ", ", text)                   # dashes become commas
    text = re.sub(r"\.{3,}|…", ",", text)                       # trailing off -> a short pause
    for symbol, spoken in _SPOKEN_SYMBOLS.items():
        text = text.replace(symbol, spoken)
    # A line break is a sentence boundary; without punctuation the voice runs straight on.
    # Lines that already end in punctuation just get a space.
    text = re.sub(r"(\S)[ \t]*\n+[ \t]*", lambda m: m.group(1) + (" " if m.group(1) in ".,:;!?" else ". "), text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)                # no space before punctuation
    text = re.sub(r"([,!?;:])(?=\S)", r"\1 ", text)              # space after, except inside file.ext
    text = re.sub(r"\.(?=[A-Z])", ". ", text)                    # sentence break, not "notes.txt"
    text = re.sub(r"\s+", " ", text).strip(" ,;:")
    if text and text[-1] not in ".!?":
        text += "."
    return text


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


def list_installed_voices() -> str:
    """`python main.py voices`: the voices on this machine, for voice_contains."""
    if sys.platform != "win32":
        import pyttsx3

        return "\n".join(v.name for v in pyttsx3.init().getProperty("voices"))
    import comtypes.client

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    tokens = voice.GetVoices()
    names = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
    return "\n".join(names) + "\n\nSet part of a name as voice_contains under [voice] in config.toml."


def make_speaker(settings: VoiceSettings, bus: EventBus) -> Speaker:
    """`engine` picks the voice: fish (neural, falls back to SAPI), sapi, pyttsx3, auto."""
    if settings.engine == "fish" and sys.platform == "win32":
        from .fish import FishSpeaker

        return FishSpeaker(settings, bus)
    if settings.engine == "pyttsx3" or sys.platform != "win32":
        return Pyttsx3Speaker(settings, bus)
    return SapiSpeaker(settings, bus)
