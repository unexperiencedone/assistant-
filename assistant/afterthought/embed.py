"""Text to vectors, on this machine.

all-MiniLM-L6-v2 through sentence-transformers (already installed, model already in
the Hugging Face cache): 384 dimensions, ~80 MB, tens of milliseconds a sentence on
CPU. It loads lazily on the watcher's thread, so Nova starts as fast as before.

If it cannot load, a hashing embedder stands in: word and character-trigram buckets.
Much weaker at meaning, but honest about it (`name` says which is in use), and it
keeps the pipeline working.
"""

from __future__ import annotations

import hashlib
import re
import threading

import numpy as np

DIM = 384


class HashEmbedder:
    name = "hashing (fallback)"
    dim = DIM

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            words = re.findall(r"[a-z0-9]+", (text or "").lower())
            grams = words + [w[i:i + 3] for w in words for i in range(max(1, len(w) - 2))]
            for gram in grams:
                digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                out[row, index] += 1.0 if digest[4] & 1 else -1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class MiniLMEmbedder:
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model
        self.name = model.split("/")[-1]
        self.dim = DIM
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            try:   # offline first: the model is cached, and capture must make no network calls
                self._model = SentenceTransformer(self.model_name, device="cpu", local_files_only=True)
            except Exception:
                self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            model = self._load()
            vectors = model.encode(list(texts), batch_size=16, normalize_embeddings=True,
                                   show_progress_bar=False, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


def make(model: str) -> "MiniLMEmbedder | HashEmbedder":
    if model == "hashing":
        return HashEmbedder()
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return HashEmbedder()
    return MiniLMEmbedder(model)
