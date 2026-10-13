"""Which memory, if any, is worth bringing up right now.

score = (0.5·recency + 3·relevance + 2·importance + 1.5·agreement) / 7
(the Generative Agents weighting, Park et al. 2023, plus the behavioural-agreement
term from the deck: what you accepted rises, what you dismissed sinks).

The gate comes first and is not part of the score: a memory is only a candidate when
its cosine similarity to what is in front of you clears the threshold, or comes within
0.1 of it while sharing at least two entity tags (a name and a project, say). Below
that, the answer is silence, never a lower-confidence guess.

The threshold is calibrated, not copied: the deck's 0.75 assumed a stronger model. With
all-MiniLM-L6-v2, measured on this machine, a follow-up email and the standup note it
depends on score 0.60-0.65 while unrelated text scores 0.13-0.24. So the default gate
is 0.55 (0.45 with two shared entities), and a match is spoken at 0.65.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from .entities import overlap

W_RECENCY, W_RELEVANCE, W_IMPORTANCE, W_AGREEMENT = 0.5, 3.0, 2.0, 1.5
HALF_LIFE_HOURS = 72.0


def recency(memory: dict[str, Any], now: float) -> float:
    last = max(float(memory.get("last_ref") or 0), float(memory.get("end") or 0))
    hours = max(0.0, (now - last) / 3600)
    return math.exp(-hours * math.log(2) / HALF_LIFE_HOURS)


def agreement(memory: dict[str, Any]) -> float:
    """Beta(1,1) prior: 0.5 for an unseen memory, towards 1 with accepts, 0 with dismissals."""
    accepted, dismissed = int(memory.get("accepted") or 0), int(memory.get("dismissed") or 0)
    return (accepted + 1) / (accepted + dismissed + 2)


def score(memory: dict[str, Any], similarity: float, now: float) -> float:
    total = (W_RECENCY * recency(memory, now) + W_RELEVANCE * max(0.0, similarity)
             + W_IMPORTANCE * float(memory.get("importance") or 0) + W_AGREEMENT * agreement(memory))
    return total / (W_RECENCY + W_RELEVANCE + W_IMPORTANCE + W_AGREEMENT)


def passes(similarity: float, shared_entities: int, threshold: float) -> bool:
    return similarity >= threshold or (similarity >= threshold - 0.1 and shared_entities >= 2)


def search(query: np.ndarray, ids: np.ndarray, matrix: np.ndarray, top: int = 20) -> list[tuple[int, float]]:
    """(memory id, cosine) for the nearest rows. Vectors are unit length, so a dot product."""
    if not len(ids):
        return []
    sims = matrix @ np.asarray(query, dtype=np.float32)
    order = np.argsort(-sims)[:top]
    return [(int(ids[i]), float(sims[i])) for i in order]


def rank(candidates: list[tuple[dict[str, Any], float]], query_entities: list[str], threshold: float,
         now: float | None = None) -> list[dict[str, Any]]:
    """Gate, then score. Returns [{"memory", "similarity", "shared", "score"}], best first."""
    now = now or time.time()
    out = []
    for memory, similarity in candidates:
        shared = overlap(memory.get("entities", []), query_entities)
        if not passes(similarity, shared, threshold):
            continue
        out.append({"memory": memory, "similarity": similarity, "shared": shared,
                    "score": score(memory, similarity, now)})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out
