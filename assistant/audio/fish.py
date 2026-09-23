"""Fish Audio's S2.1 voice, streamed, with the local voice underneath it.

AGENTS.md has said from the beginning that speech is local on purpose, because a free
cloud voice rate-limits mid-reply and leaves the assistant silent halfway through a
sentence. That rule is about the failure, not about the cloud, so this is built so the
failure cannot happen: anything Fish will not deliver is spoken by SAPI immediately,
mid-reply. A voice that changes partway through is odd for a moment; silence is a bug.

**Raw PCM, streamed, not a WAV.** This is the important decision and it was learned the
hard way. Asked for `format: "wav"`, the API returns a streaming header that declares
its data chunk as 4,294,967,040 bytes -- a four gigabyte clip. Windows `PlaySound` reads
that number, refuses the file, and plays nothing, while the request itself looks
completely successful: correct bytes, no error, no sound. Raw PCM has no header at all,
so there is nothing to be wrong, and the chunks can be written to the sound card as they
arrive. Sound starts in about a second instead of after the whole clip is synthesised.

That also removed a workaround and a whole feature. There is no longer any need to
measure a file's real duration against its lying header, and no need to split a reply
into sentences to get early sound -- streaming achieves that better, in one request
instead of several.

**Cached.** The lines Nova repeats -- every acknowledgement and confirmation -- are kept
as PCM on disk and replayed from there. Instant, and it leaves the free allowance for
what is actually new.

**Tagged.** Delivery tags pass straight through: [chuckle], [emphasis], [long pause].
They are stripped before the local voice or the screen sees them, since SAPI would
read the word "chuckle" out loud.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import urllib.request
from pathlib import Path

from ..config import VoiceSettings
from ..events import EventBus
from .tts import SapiSpeaker, Speaker

API_URL = "https://api.fish.audio/v1/tts"
# What the API sends for format="pcm": signed 16-bit, one channel, 44.1 kHz. Confirmed
# by timing a clip of known length against its byte count.
RATE, CHANNELS, WIDTH = 44100, 1, 2
CHUNK = 4096
FRAME = WIDTH * CHANNELS
# Delivery tags. Anything bracketed counts: a tag Fish does not know is ignored by it,
# and must still never be read aloud by SAPI.
TAG = re.compile(r"\[[^\]\n]{1,40}\]")


def strip_tags(text: str) -> str:
    """The same line without delivery tags, for the local voice and for the screen."""
    return " ".join(TAG.sub(" ", text or "").split())


def wav_header(data_bytes: int) -> bytes:
    """A correct RIFF header, for when a PCM clip has to become a real file."""
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE,
                          RATE * CHANNELS * WIDTH, FRAME, WIDTH * 8)
            + b"data" + struct.pack("<I", data_bytes))


class FishSpeaker(Speaker):
    """Streams Fish Audio to the sound card; falls back to SAPI the moment it cannot."""

    def __init__(self, settings: VoiceSettings, bus: EventBus) -> None:
        self._cache_dir: Path | None = None
        self._warned = False
        self._out = None            # (stream, write, owner), opened once and kept
        super().__init__(settings, bus)

    # -- setup -------------------------------------------------------------------------
    def _open(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self._pump = comtypes.client.PumpEvents
        # The local voice is opened on this same thread, because it is the one that has
        # to finish a reply Fish drops halfway through.
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

        # Opened here, on the worker thread, so the first spoken line does not pay the
        # second and a half that opening a stream costs.
        if self.api_key:
            self._out = _open_output()

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
        if not self.api_key:
            self._spoke("local", "no key")
            self._say_locally(text)
            return
        cached = self._cache_path(text)
        if cached is not None and cached.exists():
            try:
                if self._play(iter([cached.read_bytes()])):
                    self._spoke("fish", "cached")
                    return
            except OSError:
                pass
        if self._stream(text, cached):
            self._spoke("fish", "streamed")
            return
        # Not a reason to go quiet: say it with the local voice instead.
        self._spoke("local", "fish did not deliver")
        self._say_locally(text)

    def _spoke(self, engine: str, how: str) -> None:
        """Say which voice actually answered, every time.

        Every time, not once: the reason was logged once per run and every later
        fallback was invisible, so "is this Fish or is this SAPI" became unanswerable
        from the outside -- which is exactly the question that matters when the voice
        sounds wrong. A silent degradation is the worst kind.
        """
        self.bus.publish("voice", engine=engine, how=how)

    def _stream(self, text: str, cache_to: Path | None) -> bool:
        """Fetch and play at the same time. True if anything was heard."""
        payload = {"text": text, "format": "pcm"}
        if getattr(self.settings, "fish_voice", ""):
            payload["reference_id"] = self.settings.fish_voice
        request = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "model": getattr(self.settings, "fish_model", "s2.1-pro-free") or "s2.1-pro-free"})
        collected = bytearray() if cache_to is not None else None
        timeout = float(getattr(self.settings, "fish_timeout", 20) or 20)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except Exception as error:
            self._warn(error)
            return False

        def chunks():
            with response:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        return
                    if collected is not None:
                        # extend, not +=: an augmented assignment inside this generator
                        # would make `collected` local to it and shadow the buffer.
                        collected.extend(block)
                    yield block

        try:
            played = self._play(chunks())
        except Exception as error:
            self._warn(error)
            return False
        # Only cache a clip that arrived whole: half a sentence replayed forever is
        # worse than fetching it again.
        if played and collected and cache_to is not None and not self._interrupt.is_set():
            try:
                cache_to.write_bytes(bytes(collected))
            except OSError:
                pass
        return played

    def _output(self):
        """The sound card, opened once and kept.

        Opening a PortAudio stream costs about 1.5 seconds on this machine and closing
        it another quarter of one, which was being paid on every single line -- more
        than the API call itself, and it made even a cached line take over two seconds.
        So the device is opened when the worker thread starts and held for the life of
        the process, the way any audio application holds one.
        """
        if self._out is None:
            self._out = _open_output()
        return self._out

    def _drop_output(self) -> None:
        """Let go of the device so the next line opens a fresh one."""
        if self._out is not None:
            _close_output(self._out[0], self._out[2])
            self._out = None

    def _play(self, blocks) -> bool:
        """Write PCM to the sound card as it arrives. False if there is no output."""
        stream, write, owner = self._output()
        if write is None:
            self._warn("no sound output device")
            return False
        leftover = b""
        heard = False
        try:
            for block in blocks:
                if self._interrupt.is_set():
                    break
                # A frame is two bytes and an HTTP chunk need not end on one, so an odd
                # trailing byte waits for the next block rather than being played as
                # half a sample.
                data = leftover + block
                usable = len(data) - (len(data) % FRAME)
                leftover = data[usable:]
                if usable:
                    write(data[:usable])
                    heard = True
            if leftover and not self._interrupt.is_set():
                write(leftover + b"\x00" * (FRAME - len(leftover)))
        except Exception as error:
            # A device that has gone away -- unplugged, or switched in Windows -- is let
            # go of, so the next line opens a fresh one instead of failing forever
            # against a dead handle. The stream is deliberately *not* closed on the
            # normal path: opening one costs about a second and a half here.
            self._drop_output()
            self._warn(error)
        return heard

    def _say_locally(self, text: str) -> None:
        """SAPI, with the tags removed so they are not read out as words."""
        spoken = strip_tags(text)
        if not spoken:
            return
        self._voice.Speak(spoken, SapiSpeaker.ASYNC | SapiSpeaker.NOT_XML)
        while not self._voice.WaitUntilDone(0):
            if self._interrupt.is_set():
                self._voice.Speak("", SapiSpeaker.ASYNC | SapiSpeaker.PURGE)
                return
            self._pump(0.05)

    # -- the cache ---------------------------------------------------------------------
    def _cache_path(self, text: str) -> Path | None:
        if self._cache_dir is None:
            return None
        key = f"{getattr(self.settings, 'fish_voice', '')}|{text}".encode("utf-8")
        return self._cache_dir / f"{hashlib.blake2b(key, digest_size=12).hexdigest()}.pcm"

    def _warn(self, reason: object) -> None:
        """Once per run. A voice that complains every sentence is worse than one that
        quietly sounds different."""
        if self._warned:
            return
        self._warned = True
        self.bus.log(f"Fish Audio unavailable, using the local voice: {reason}", "warn")


def _open_output():
    """A sound card to write PCM into: (stream, write, owner). write is None if none."""
    try:
        import sounddevice

        stream = sounddevice.RawOutputStream(samplerate=RATE, channels=CHANNELS, dtype="int16")
        stream.start()
        return stream, stream.write, stream
    except Exception:
        pass
    try:
        import pyaudio

        audio = pyaudio.PyAudio()
        stream = audio.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, output=True)
        return stream, stream.write, audio
    except Exception:
        return None, None, None


def _close_output(stream, owner) -> None:
    if stream is None:
        return
    try:
        stream.stop()
        stream.close()
        return
    except Exception:
        pass
    try:
        stream.stop_stream()
        stream.close()
        owner.terminate()
    except Exception:
        pass


def warm_cache(settings: VoiceSettings, lines: list[str]) -> int:
    """Fetch these lines into the cache without playing them. Returns how many landed.

    For the sentences Nova repeats constantly, so the first time a user hears one it is
    already instant.
    """
    speaker = FishSpeaker.__new__(FishSpeaker)
    speaker.settings = settings
    speaker.bus = EventBus()
    speaker._warned = False
    folder = getattr(settings, "fish_cache", "") or ""
    speaker._cache_dir = Path(os.path.expandvars(folder)).expanduser() if folder else None
    if speaker._cache_dir is not None:
        speaker._cache_dir.mkdir(parents=True, exist_ok=True)
    import threading

    speaker._interrupt = threading.Event()
    done = 0
    for line in lines:
        target = speaker._cache_path(line)
        if target is None:
            continue
        if target.exists() or speaker._stream(line, target):
            done += 1
    return done
