"""Speech-to-text engines. Each turns a captured phrase into a string."""

from __future__ import annotations

from abc import ABC, abstractmethod

import speech_recognition as sr

from ..config import SpeechSettings
from ..events import EventBus

# Whisper tends to "hear" these in silence or background noise.
_HALLUCINATIONS = {"", "you", "thank you.", "thank you", "thanks for watching!", "bye.", "."}


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, audio: sr.AudioData) -> str: ...


class WhisperTranscriber(Transcriber):
    """Local, private and fast on CPU with the int8 base.en model."""

    def __init__(self, settings: SpeechSettings, bus: EventBus) -> None:
        from faster_whisper import WhisperModel  # heavy import, only when selected

        self.settings = settings
        bus.log(f"Loading Whisper model '{settings.whisper_model}' (first run downloads it)...")
        self.model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        bus.log("Whisper ready.")

    def transcribe(self, audio: sr.AudioData) -> str:
        import numpy as np

        pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            samples,
            language=self.settings.language or None,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        return "" if text.lower() in _HALLUCINATIONS else text


class GoogleTranscriber(Transcriber):
    """Online recognizer bundled with SpeechRecognition; no model download."""

    def __init__(self, settings: SpeechSettings) -> None:
        self.settings = settings
        self.recognizer = sr.Recognizer()

    def transcribe(self, audio: sr.AudioData) -> str:
        language = self.settings.language
        if language and "-" not in language:
            language = {"en": "en-US"}.get(language, language)
        try:
            return self.recognizer.recognize_google(audio, language=language or "en-US").strip()
        except sr.UnknownValueError:
            return ""


def make_transcriber(settings: SpeechSettings, bus: EventBus) -> Transcriber:
    if settings.engine == "google":
        return GoogleTranscriber(settings)
    try:
        return WhisperTranscriber(settings, bus)
    except Exception as exc:
        bus.log(f"Whisper unavailable ({exc}); falling back to Google speech.", "warn")
        return GoogleTranscriber(settings)
