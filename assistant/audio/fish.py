"""Fish Audio's S2.1 voice, with the local voice underneath it.

AGENTS.md has said from the beginning: speech is local on purpose, because free cloud
voices rate-limit mid-reply and leave the assistant silent halfway through a sentence.
That rule is about the *failure*, not about the cloud, and this is built so the failure
cannot happen: every sentence that Fish will not deliver is spoken by SAPI instead,
immediately, mid-reply. The worst case is that Nova's voice changes partway through a
sentence boundary -- which is strange for a moment and infinitely better than silence.

Three things make it usable rather than merely better-sounding:

**Sentence at a time.** Synthesising a whole three-sentence reply takes about 3.6
seconds before any sound at all; the first sentence alone takes about 1.1. So the reply
is split and each sentence is fetched while the previous one is still playing. Measured
on this machine, not assumed.

**Cached.** "On it.", "Torch is on.", every confirmation Nova repeats a hundred times a
week is synthesised once and then read off disk. That is instant, and it keeps the free
tier's allowance for the sentences that are actually new.

**Tagged.** The model accepts delivery tags -- [chuckle], [emphasis], [long pause] --
inline in the text. They are passed straight through, and stripped before the local
voice sees them, because SAPI would read the word "chuckle" out loud.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from ..config import VoiceSettings
from ..events import EventBus
from .tts import SapiSpeaker, Speaker, _for_speech

API_URL = "https://api.fish.audio/v1/tts"
# Sentence boundaries, with the tags left attached to the sentence they belong to.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Delivery tags the model understands. Anything in brackets is treated as one, since a
# tag Fish does not know is ignored by it and must still never be read aloud by SAPI.
TAG = re.compile(r"\[[^\]\n]{1,40}\]")
# A sentence longer than this is split further: the point of going sentence by sentence
# is that sound starts quickly, and one long clause defeats that.
MAX_CHARS = 240


def strip_tags(text: str) -> str:
    """The same line without delivery tags, for the local voice and for the screen."""
    return " ".join(TAG.sub(" ", text or "").split())


# Nothing Nova says in one sentence lasts longer than this. A cap is the difference
# between a wrong duration costing a moment and costing the rest of the session.
MAX_SECONDS = 90.0


def duration_of(path: Path) -> float:
    """How long this WAV actually plays for, measured from its bytes.

    The frame count in the header is not trustworthy. Fish returns a streaming WAV whose
    declared length is nonsense -- a 2.7 second clip announces itself as 48,695 seconds
    -- and playback here waits for the stated duration before returning. Believing the
    header meant the speaker thread blocking for thirteen hours after the first
    sentence, with every later reply queued silently behind it.

    So the duration comes from the size of the file and the format fields, which are
    reliable, and is capped regardless.
    """
    try:
        with wave.open(str(path)) as handle:
            rate = handle.getframerate() or 22050
            width = handle.getsampwidth() or 2
            channels = handle.getnchannels() or 1
    except Exception:
        return 8.0
    try:
        audio_bytes = max(0, path.stat().st_size - 44)   # past a standard RIFF header
    except OSError:
        return 8.0
    seconds = audio_bytes / float(rate * width * channels)
    return min(max(seconds, 0.2), MAX_SECONDS)


def sentences(text: str) -> list[str]:
    """The reply in speakable pieces, each fetched while the last one plays."""
    parts: list[str] = []
    for chunk in _SENTENCE.split(text or ""):
        chunk = chunk.strip()
        while len(chunk) > MAX_CHARS:
            # Break at the last comma inside the budget, else just at the budget.
            cut = chunk.rfind(",", 0, MAX_CHARS)
            cut = cut + 1 if cut > MAX_CHARS // 2 else MAX_CHARS
            parts.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            parts.append(chunk)
    return parts


class FishSpeaker(Speaker):
    """Fish Audio for the words, SAPI for anything Fish cannot deliver in time."""

    def __init__(self, settings: VoiceSettings, bus: EventBus) -> None:
        self._fallback: SapiSpeaker | None = None
        self._cache_dir: Path | None = None
        self._warned = False
        super().__init__(settings, bus)

    # -- setup -------------------------------------------------------------------------
    def _open(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._pump = comtypes.client.PumpEvents
        # The local voice is opened on this same thread, because it is the one that will
        # have to finish a reply Fish drops halfway through.
        self._voice = comtypes.client.CreateObject("SAPI.SpVoice")
        wanted = (self.settings.voice_contains or "").lower()
        if wanted:
            tokens = self._voice.GetVoices()
            for i in range(tokens.Count):
                if wanted in tokens.Item(i).GetDescription().lower():
                    self._voice.Voice = tokens.Item(i)
                    break
        self._voice.Rate = max(-10, min(10, self.settings.rate))
        self._voice.Volume = max(0, min(100, self.settings.volume))

        folder = getattr(self.settings, "fish_cache", "") or ""
        if folder:
            self._cache_dir = Path(os.path.expandvars(folder)).expanduser()
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self._cache_dir = None

    @property
    def api_key(self) -> str:
        return os.environ.get(getattr(self.settings, "fish_key_env", "") or "", "").strip()

    # -- speaking ----------------------------------------------------------------------
    def _speak(self, text: str) -> None:
        """Say the line, sentence by sentence, falling back the moment Fish will not."""
        if not self.api_key:
            self._say_locally(text)
            return
        for piece in sentences(text):
            if self._interrupt.is_set():
                return
            audio = self._audio_for(piece)
            if audio is None:
                # Not a reason to go quiet: say this sentence with the local voice and
                # carry on trying Fish for the next one.
                self._say_locally(piece)
                continue
            self._play(audio)

    def _audio_for(self, piece: str) -> Path | None:
        """The WAV for one sentence, from the cache or from the API. None means neither."""
        cached = self._cached_path(piece)
        if cached is not None and cached.exists():
            return cached
        body = json.dumps({"text": piece, "format": "wav",
                           **({"reference_id": self.settings.fish_voice}
                              if getattr(self.settings, "fish_voice", "") else {})}).encode("utf-8")
        request = urllib.request.Request(API_URL, data=body, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "model": getattr(self.settings, "fish_model", "s2.1-pro-free") or "s2.1-pro-free",
        })
        try:
            with urllib.request.urlopen(request, timeout=float(getattr(self.settings, "fish_timeout", 20) or 20)) as response:
                audio = response.read()
        except Exception as error:
            self._warn(error)
            return None
        if not audio.startswith(b"RIFF"):
            self._warn("the reply was not a WAV")
            return None
        target = cached or self._temp_path(piece)
        try:
            target.write_bytes(audio)
        except OSError:
            return None
        return target

    def _cached_path(self, piece: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = f"{getattr(self.settings, 'fish_voice', '')}|{piece}".encode("utf-8")
        return self._cache_dir / f"{hashlib.blake2b(key, digest_size=12).hexdigest()}.wav"

    def _temp_path(self, piece: str) -> Path:
        import tempfile

        name = hashlib.blake2b(piece.encode("utf-8"), digest_size=8).hexdigest()
        return Path(tempfile.gettempdir()) / f"nova-tts-{name}.wav"

    def _play(self, path: Path) -> None:
        """Play a WAV, stopping the moment an interruption is asked for.

        Asynchronous playback plus a poll, rather than a blocking call: "stop" has to cut
        Nova off mid-word, and a synchronous play cannot be interrupted at all.
        """
        import winsound

        seconds = duration_of(path)
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as error:
            self._warn(error)
            return
        deadline = time.time() + seconds + 0.25
        while time.time() < deadline:
            if self._interrupt.is_set():
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
                return
            time.sleep(0.05)

    def _say_locally(self, text: str) -> None:
        """SAPI, with the delivery tags removed so they are not read out as words."""
        spoken = strip_tags(text)
        if not spoken:
            return
        self._voice.Speak(spoken, SapiSpeaker.ASYNC | SapiSpeaker.NOT_XML)
        while not self._voice.WaitUntilDone(0):
            if self._interrupt.is_set():
                self._voice.Speak("", SapiSpeaker.ASYNC | SapiSpeaker.PURGE)
                return
            self._pump(0.05)

    def _warn(self, reason: object) -> None:
        """Say it once per run. A voice that complains every sentence is worse than one
        that quietly sounds different."""
        if self._warned:
            return
        self._warned = True
        self.bus.log(f"Fish Audio unavailable, using the local voice: {reason}", "warn")


def cache_line(settings: VoiceSettings, text: str) -> bool:
    """Synthesise one line into the cache without speaking it.

    For the lines Nova repeats constantly -- acknowledgements, confirmations -- so the
    first time a user hears them is already instant. `python main.py voices --warm` uses
    this.
    """
    speaker = FishSpeaker.__new__(FishSpeaker)
    speaker.settings = settings
    speaker.bus = EventBus()
    speaker._warned = False
    folder = getattr(settings, "fish_cache", "") or ""
    speaker._cache_dir = Path(os.path.expandvars(folder)).expanduser() if folder else None
    if speaker._cache_dir is not None:
        speaker._cache_dir.mkdir(parents=True, exist_ok=True)
    return all(speaker._audio_for(piece) is not None for piece in sentences(_for_speech(text)))
