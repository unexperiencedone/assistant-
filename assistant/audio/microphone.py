"""Continuously capture spoken requests and hand their transcripts to a callback.

A request is not the same as a phrase. People pause mid-sentence ("I want you
to... plan my week"), and ending the request at the first pause sends half a
sentence to the agent. So after each phrase:

  1. keep listening for `continuation_seconds`; if speech resumes, the new audio
     is appended to the same request (transcription of what we have so far runs
     in parallel, so this adds little delay)
  2. if the transcript still looks unfinished ("...", a trailing comma, or ends on
     a word like "to", "my", "the"), wait up to `incomplete_wait_seconds` more
  3. only then transcribe the whole request once, drop it if it looks like noise
     (see gate.py), and hand it on
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

import speech_recognition as sr

from ..config import SpeechSettings
from ..events import EventBus
from .gate import Transcript, rejection
from .transcriber import Transcriber
from .tts import Speaker

MAX_EXTENSIONS = 4          # how many times one request may be extended
MAX_REQUEST_SECONDS = 90    # hard cap on a single request's audio
THRESHOLD_CEILING = 3.0     # dynamic energy threshold may not exceed calibrated level x this

# Words a finished request almost never ends on. Deliberately excludes words that
# end real questions and answers ("what time is it", "yes I can", "open this").
_DANGLING = {
    "a", "an", "the", "to", "of", "for", "at", "by", "with", "from", "into",
    "and", "or", "but", "because", "than",
    "my", "your", "our", "their",
    "want", "wanna", "need", "gonna",
    "um", "uh", "er", "erm", "hmm",
}
_TRAILING_OFF = re.compile(r"(\.\.\.|…|,|-)\s*$")


def looks_unfinished(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if _TRAILING_OFF.search(text):
        return True
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return bool(words) and words[-1] in _DANGLING


def join_audio(pieces: list[sr.AudioData]) -> sr.AudioData:
    first = pieces[0]
    if len(pieces) == 1:
        return first
    raw = b"".join(p.get_raw_data(first.sample_rate, first.sample_width) for p in pieces)
    return sr.AudioData(raw, first.sample_rate, first.sample_width)


class MicrophoneListener:
    def __init__(
        self,
        settings: SpeechSettings,
        transcriber: Transcriber,
        speaker: Speaker,
        bus: EventBus,
        on_utterance: Callable[[str], None],
    ) -> None:
        self.settings = settings
        self.transcriber = transcriber
        self.speaker = speaker
        self.bus = bus
        self.on_utterance = on_utterance
        self.muted = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="microphone", daemon=True)
        self._transcribe_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcribe")
        self._calibrated_threshold = float(settings.energy_threshold)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def make_recognizer(self) -> sr.Recognizer:
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = self.settings.energy_threshold
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = self.settings.pause_threshold
        recognizer.non_speaking_duration = min(0.5, self.settings.pause_threshold)
        return recognizer

    # -- main loop --------------------------------------------------------------------
    def _run(self) -> None:
        recognizer = self.make_recognizer()
        try:
            source = sr.Microphone(sample_rate=16000)
        except Exception as exc:
            self.bus.log(f"No microphone available: {exc}. Type commands instead.", "error")
            return

        with source:
            self.bus.log("Calibrating microphone for background noise, stay quiet...")
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
            self._calibrated_threshold = recognizer.energy_threshold
            self.bus.log(f"Microphone ready (energy threshold {recognizer.energy_threshold:.0f}).")
            while not self._stop.is_set():
                # Half-duplex: never listen while the assistant is talking,
                # otherwise it transcribes its own voice.
                if self.muted.is_set() or self.speaker.is_busy:
                    time.sleep(0.1)
                    continue
                self.bus.status("listening")
                started = time.time()
                heard = self.capture_request(recognizer, source)
                if heard is None:
                    continue
                if self.speaker.last_active_at >= started:
                    continue  # the assistant spoke during this capture: likely an echo
                reason = rejection(heard, self.settings.min_avg_logprob)
                if reason:
                    self.bus.publish("heard", text=heard.text, accepted=False, reason=reason)
                    self.bus.log(f"(ignored: {reason}) {heard.text[:80]}")
                    continue
                self.on_utterance(heard.text)

    def capture_request(self, recognizer: sr.Recognizer, source: sr.AudioSource) -> Transcript | None:
        """Listen until the user has finished a whole request. None if nothing was said."""
        first = self._listen(recognizer, source, timeout=1)
        if first is None:
            return None
        pieces = [first]
        extensions = 0
        pending: Future[Transcript] = self._transcribe_pool.submit(self._safe_transcribe, first)

        while True:
            nxt = self._listen(recognizer, source, timeout=self.settings.continuation_seconds)
            if nxt is not None and self._room_for(pieces):
                pieces.append(nxt)
                pending = self._transcribe_pool.submit(self._safe_transcribe, join_audio(pieces))
                continue

            self.bus.status("transcribing")
            heard = pending.result()
            text = heard.text
            if extensions < MAX_EXTENSIONS and looks_unfinished(text) and self._room_for(pieces):
                extra_wait = max(0.0, self.settings.incomplete_wait_seconds - self.settings.continuation_seconds)
                self.bus.status("listening")
                self.bus.log(f'Waiting for the rest of: "{text}"')
                nxt = self._listen(recognizer, source, timeout=extra_wait)
                if nxt is not None:
                    pieces.append(nxt)
                    extensions += 1
                    pending = self._transcribe_pool.submit(self._safe_transcribe, join_audio(pieces))
                    continue
            return heard if text else None

    # -- helpers ------------------------------------------------------------------------
    def _listen(self, recognizer: sr.Recognizer, source: sr.AudioSource, timeout: float) -> sr.AudioData | None:
        if timeout <= 0:
            return None  # SpeechRecognition treats timeout=0 as "wait forever"
        # Dynamic adjustment happens between phrases; an echo or a door slam must not push
        # the threshold so high that quiet words count as silence.
        recognizer.energy_threshold = min(recognizer.energy_threshold, self._calibrated_threshold * THRESHOLD_CEILING)
        try:
            # Streaming gives us the phrase chunk by chunk, so the canvas can show a live
            # level while you speak instead of only a "listening" label.
            chunks = list(self._stream(recognizer, source, timeout))
        except sr.WaitTimeoutError:
            self.bus.publish("mic", level=0.0, speaking=False)
            return None
        if not chunks:
            return None
        audio = sr.AudioData(b"".join(c.frame_data for c in chunks), chunks[0].sample_rate, chunks[0].sample_width)
        self.bus.publish("mic", level=0.0, speaking=False)
        return audio if audio.frame_data else None

    def _stream(self, recognizer: sr.Recognizer, source: sr.AudioSource, timeout: float):
        last_published = 0.0
        for chunk in recognizer.listen(source, timeout=timeout,
                                       phrase_time_limit=self.settings.phrase_time_limit, stream=True):
            now = time.time()
            if now - last_published > 0.1:  # ~10 updates a second is plenty for a meter
                last_published = now
                self.bus.publish("mic", level=self._level(chunk, recognizer.energy_threshold), speaking=True)
            yield chunk

    @staticmethod
    def _level(chunk: sr.AudioData, threshold: float) -> float:
        """0..1, where ~0.5 is the level at which speech is detected."""
        import audioop

        rms = audioop.rms(chunk.frame_data, chunk.sample_width)
        return round(min(1.0, (rms / max(threshold, 1.0)) * 0.5), 3)

    @staticmethod
    def _room_for(pieces: list[sr.AudioData]) -> bool:
        seconds = sum(len(p.frame_data) / (p.sample_rate * p.sample_width) for p in pieces)
        return seconds < MAX_REQUEST_SECONDS

    def _safe_transcribe(self, audio: sr.AudioData) -> Transcript:
        try:
            return self.transcriber.transcribe(audio)
        except Exception as exc:
            self.bus.log(f"Transcription failed: {exc}", "warn")
            return Transcript("")
