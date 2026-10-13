"""Whose voice was that? Phase 1: the owner's voice, and nobody else's, can speak to Nova.

The wake word keeps out most of the room, but not a TV that says "Nova", a video, or
someone else in the house. A voiceprint does: a 256-number summary of how a voice
sounds, compared against each utterance before anything acts on it.

    enrol   "learn my voice" -> the next four sentences you say become the voiceprint,
            and the threshold is set from how alike those four are (within safe bounds)
    check   each captured utterance -> a cosine score against the voiceprint; below the
            threshold it is dropped like noise, with the score shown on the canvas
    log     every score goes to data/voice_scores.jsonl, which is how phase 2 gets
            decided: from evidence that it is reliable, not from hope

Nothing leaves the machine. The model is WeSpeaker's ResNet34 (VoxCeleb, ONNX, 26 MB),
run by onnxruntime, which faster-whisper already installs; the features are computed
here with numpy. Only typed input bypasses this, which is the point: the canvas and the
phone are already yours.

With no voiceprint, nothing is checked and the model is never loaded.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger("nova.voiceid")

MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34.onnx")
SAMPLE_RATE = 16000
ENROL_SAMPLES = 4
MIN_ENROL_SECONDS = 1.5      # shorter clips make a poor voiceprint
SHORT_SECONDS = 1.0          # below this, an embedding is shakier...
SHORT_LENIENCY = 0.08        # ...so the bar is lowered a little ("stop" must still work)
# The bar sits ENROL_MARGIN below how alike your enrolment samples were, within these
# bounds. Enrolment samples are said together, in one sitting, so they agree more than a
# request does an hour later with music on: a 0.15 margin capped at 0.62 turned the owner
# away at 0.57. Different voices measured at most 0.37.
THRESHOLD_BOUNDS = (0.35, 0.50)
ENROL_MARGIN = 0.20
DEFAULT_THRESHOLD = 0.45
SPEECH_COVERAGE = 0.6        # below this share of speech in a clip, score the speech alone
SPAN_PAD = 0.15              # seconds kept either side of each speech segment
LOG_LIMIT = 5000             # score lines kept


# -- features: Kaldi-style log mel filterbank, what WeSpeaker was trained on ----------------
def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)


def _mel_banks(n_mels: int = 80, n_fft: int = 512, rate: int = SAMPLE_RATE, low: float = 20.0) -> np.ndarray:
    high = rate / 2
    mel_low, mel_high = _mel(low), _mel(high)
    centres = np.linspace(mel_low, mel_high, n_mels + 2)
    bins = _mel(np.arange(n_fft // 2 + 1) * rate / n_fft)
    banks = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(n_mels):
        left, centre, right = centres[m], centres[m + 1], centres[m + 2]
        up = (bins - left) / (centre - left)
        down = (right - bins) / (right - centre)
        banks[m] = np.clip(np.minimum(up, down), 0, None)
    return banks


_BANKS = None


def fbank(samples: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
    """(frames, 80) log mel energies, mean-normalised over time. `samples` at int16 scale."""
    global _BANKS
    if _BANKS is None:
        _BANKS = _mel_banks()
    frame, shift, n_fft = int(0.025 * rate), int(0.010 * rate), 512
    x = samples.astype(np.float32)
    if len(x) < frame:
        x = np.pad(x, (0, frame - len(x)))
    count = 1 + (len(x) - frame) // shift
    idx = np.arange(frame)[None, :] + shift * np.arange(count)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)                    # remove DC
    frames = np.concatenate([frames[:, :1] * 0.03, frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)  # pre-emphasis
    frames = frames * np.hamming(frame).astype(np.float32)
    power = np.abs(np.fft.rfft(frames, n_fft)) ** 2
    feats = np.log(np.maximum(power @ _BANKS.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


def speech_only(pcm: bytes, spans, rate: int = SAMPLE_RATE, width: int = 2) -> bytes | None:
    """Just the stretches Whisper heard speech in, when they are a small part of the clip.

    A 12-second capture holding one second of "play the song" over music scored 0.57
    against its owner: the voiceprint was mostly of the music. None when the clip is
    mostly speech already, so the voiceprint computed beside Whisper can be used as is.
    """
    total = len(pcm) / (rate * width)
    if not spans or total <= 0:
        return None
    speech = sum(max(0.0, end - start) for start, end in spans)
    if speech / total >= SPEECH_COVERAGE:
        return None
    frame = rate * width
    parts = []
    for start, end in spans:
        a = max(0, int((start - SPAN_PAD) * rate)) * width
        b = min(len(pcm), int((end + SPAN_PAD) * rate) * width)
        if b > a:
            parts.append(pcm[a:b])
    cropped = b"".join(parts)
    return cropped if len(cropped) >= frame // 2 else None


@dataclass
class Verdict:
    kind: str            # "accept" | "reject" | "enrol" | "off"
    score: float | None = None
    reason: str = ""


class VoiceID:
    def __init__(self, model_path: Path, profile_path: Path, log_path: Path,
                 threshold: float = 0.0, enforce: bool = True) -> None:
        self.model_path = model_path
        self.profile_path = profile_path
        self.log_path = log_path
        self.fixed_threshold = threshold      # 0: use the one measured at enrolment
        self.enforce = enforce                # False: score and log, never drop
        self._session = None
        self._lock = threading.Lock()
        self.profile = self._load_profile()
        self._enrolling: list[np.ndarray] | None = None
        self.on_enrol_progress: Callable[[int, int], None] | None = None
        self.on_enrolled: Callable[[str], None] | None = None

    # -- state ------------------------------------------------------------------------
    @property
    def enrolled(self) -> bool:
        return bool(self.profile.get("embedding"))

    @property
    def threshold(self) -> float:
        """Worked out from the stored consistency each time, so a change to the margin or
        the bounds applies to a voiceprint made before it, without enrolling again."""
        if self.fixed_threshold:
            return self.fixed_threshold
        consistency = self.profile.get("consistency")
        if consistency is None:
            return float(self.profile.get("threshold") or DEFAULT_THRESHOLD)
        return float(np.clip(float(consistency) - ENROL_MARGIN, *THRESHOLD_BOUNDS))

    def _load_profile(self) -> dict:
        try:
            return json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # -- the model --------------------------------------------------------------------
    def _model(self):
        if self._session is None:
            if not self.model_path.exists():
                self.model_path.parent.mkdir(parents=True, exist_ok=True)
                log.info("downloading the speaker model (26 MB, once)")
                partial = self.model_path.with_suffix(".part")
                urllib.request.urlretrieve(MODEL_URL, partial)
                partial.replace(self.model_path)
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 2   # leave the CPU to Whisper
            self._session = ort.InferenceSession(str(self.model_path), options, providers=["CPUExecutionProvider"])
        return self._session

    def embed(self, pcm: bytes, rate: int = SAMPLE_RATE, width: int = 2) -> np.ndarray:
        samples = np.frombuffer(pcm, dtype=np.int16 if width == 2 else np.int8).astype(np.float32)
        feats = fbank(samples, rate)[None, :, :]
        with self._lock:
            vec = self._model().run(None, {"feats": feats})[0][0]
        return vec / (np.linalg.norm(vec) + 1e-9)

    # -- enrolment --------------------------------------------------------------------
    def start_enrolment(self) -> None:
        self._enrolling = []

    def cancel_enrolment(self) -> None:
        self._enrolling = None

    @property
    def enrolling(self) -> bool:
        return self._enrolling is not None

    def _take_sample(self, vec: np.ndarray) -> None:
        assert self._enrolling is not None
        self._enrolling.append(vec)
        done = len(self._enrolling)
        if done < ENROL_SAMPLES:
            if self.on_enrol_progress:
                self.on_enrol_progress(done, ENROL_SAMPLES)
            return
        samples, self._enrolling = self._enrolling, None
        mean = np.mean(samples, axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-9)
        # How alike your own samples are, each against the others: the threshold sits a
        # margin below the least alike, so your own voice on an off day still passes.
        loo = []
        for i, vec in enumerate(samples):
            rest = np.mean([s for j, s in enumerate(samples) if j != i], axis=0)
            loo.append(float(vec @ (rest / (np.linalg.norm(rest) + 1e-9))))
        threshold = float(np.clip(min(loo) - ENROL_MARGIN, *THRESHOLD_BOUNDS))
        self.profile = {"embedding": mean.round(6).tolist(), "threshold": round(threshold, 3),
                        "consistency": round(min(loo), 3), "samples": len(samples),
                        "created": time.strftime("%Y-%m-%d %H:%M")}
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        if self.on_enrolled:
            self.on_enrolled(f"I know your voice now. From here on I'll only take spoken requests from you "
                             f"(your samples matched each other at {min(loo):.2f}; the bar is {threshold:.2f}).")

    def forget(self) -> None:
        self.profile = {}
        self._enrolling = None
        try:
            self.profile_path.unlink()
        except OSError:
            pass

    # -- every utterance ---------------------------------------------------------------
    @property
    def wanted(self) -> bool:
        """Is there any point computing a voiceprint for the next utterance?"""
        return self.enrolled or self.enrolling

    def hear(self, pcm: bytes, rate: int = SAMPLE_RATE, width: int = 2, text: str = "",
             vec: np.ndarray | None = None) -> Verdict:
        """Judge one utterance. `vec` is its voiceprint when it was computed ahead of time
        (microphone.py runs it alongside Whisper, so the check adds no wait)."""
        seconds = len(pcm) / (rate * width)
        if self._enrolling is not None:
            if seconds < MIN_ENROL_SECONDS:
                return Verdict("enrol", None, "too short for a sample; say the whole sentence")
            self._take_sample(vec if vec is not None else self.embed(pcm, rate, width))
            return Verdict("enrol")
        if not self.enrolled:
            return Verdict("off")
        try:
            vec = vec if vec is not None else self.embed(pcm, rate, width)
        except Exception as exc:          # never lose a request to a model problem
            log.warning("voice check failed, letting it through: %s", exc)
            return Verdict("accept", None, "check failed")
        score = float(vec @ np.asarray(self.profile["embedding"], dtype=np.float32))
        bar = self.threshold - (SHORT_LENIENCY if seconds < SHORT_SECONDS else 0.0)
        accepted = score >= bar or not self.enforce
        self._log(score, bar, seconds, score >= bar, text)
        if accepted:
            return Verdict("accept", score)
        return Verdict("reject", score, f"not your voice ({score:.2f} < {bar:.2f})")

    def _log(self, score: float, bar: float, seconds: float, matched: bool, text: str) -> None:
        line = json.dumps({"ts": round(time.time(), 1), "score": round(score, 3), "bar": round(bar, 3),
                           "seconds": round(seconds, 2), "matched": matched, "text": text[:60]})
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if self.log_path.stat().st_size > LOG_LIMIT * 160:
                lines = self.log_path.read_text(encoding="utf-8").splitlines()[-LOG_LIMIT:]
                self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def report(self) -> str:
        """How reliable it has been: the evidence phase 2 is decided on."""
        if not self.enrolled:
            return "No voiceprint yet. Say \"learn my voice\"."
        try:
            rows = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError):
            rows = []
        head = (f"Voiceprint from {self.profile.get('created')}: {self.profile.get('samples')} samples, "
                f"consistency {self.profile.get('consistency')}, bar {self.threshold:.2f}"
                f"{'' if self.enforce else ' (log only, nothing dropped)'}.")
        if not rows:
            return head + " Nothing checked yet."
        matched = [r for r in rows if r["matched"]]
        dropped = [r for r in rows if not r["matched"]]
        near = [r for r in rows if abs(r["score"] - r["bar"]) < 0.05]
        lines = [head, f"{len(rows)} utterances checked: {len(matched)} matched, {len(dropped)} dropped, "
                       f"{len(near)} within 0.05 of the bar."]
        if matched:
            scores = sorted(r["score"] for r in matched)
            lines.append(f"Matched scores: lowest {scores[0]:.2f}, median {scores[len(scores) // 2]:.2f}.")
        if dropped:
            scores = sorted(r["score"] for r in dropped)
            lines.append(f"Dropped scores: highest {scores[-1]:.2f}. Recent drops:")
            lines += [f"  {r['score']:.2f}  {r['seconds']:.1f}s  {r['text']}" for r in dropped[-5:]]
        return "\n".join(lines)
