"""The nightly pass: short-term chunks become long-term knowledge, or are let go.

1. **Group** the day's unconsolidated chunks by meaning and by shared entities
   (greedy: a chunk joins a group whose centroid it is close to, or whose tags it
   shares two of).
2. **Keep** a group if it is more than a one-off (two or more chunks), or important
   (something you copied, asked Nova to remember, or referenced). A lone, unimportant
   chunk is a NO-OP: it simply expires after seven days.
3. **Resolve** each kept group against the knowledge base (Mem0's write logic): close
   to an existing entry -> UPDATE it with the new sources; otherwise ADD a new entry.
   DELETE is yours: any entry can be forgotten from the Memory screen or by voice.
4. **Expire** short-term chunks past their TTL that were never referenced twice.

Summaries are extractive -- lines and sentences that were really there -- so this
makes zero network calls, and nothing in the knowledge base was written by a model.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np

from .entities import overlap

# Calibrated for MiniLM (see rank.py): related text lands at 0.5-0.65.
JOIN_SIMILARITY = 0.5
UPDATE_SIMILARITY = 0.7
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def group(chunks: list[dict[str, Any]], vectors: dict[int, np.ndarray]) -> list[list[dict[str, Any]]]:
    groups: list[dict[str, Any]] = []
    for chunk in sorted(chunks, key=lambda c: c["start"]):
        vector = vectors.get(chunk["id"])
        best, best_sim = None, 0.0
        for g in groups:
            sim = float(_unit(g["sum"]) @ vector) if vector is not None else 0.0
            tags = [t for t, _ in g["tags"].most_common(8)]
            if (sim >= JOIN_SIMILARITY or overlap(chunk["entities"], tags) >= 2) and sim >= best_sim:
                best, best_sim = g, sim
        if best is None:
            best = {"members": [], "sum": np.zeros_like(vector) if vector is not None else np.zeros(384, np.float32),
                    "tags": Counter()}
            groups.append(best)
        best["members"].append(chunk)
        if vector is not None:
            best["sum"] = best["sum"] + vector
        best["tags"].update(chunk["entities"])
    return [g["members"] for g in groups]


def worth_keeping(members: list[dict[str, Any]]) -> bool:
    return (len(members) >= 2 or max(float(m.get("importance") or 0) for m in members) >= 0.6
            or any(int(m.get("refs") or 0) >= 1 for m in members))


def summarise(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Title, text, entities and sources for a group, from what was really there."""
    contexts = Counter(m["context"] or m["title"] for m in members if (m["context"] or m["title"]))
    tags = Counter(t for m in members for t in m["entities"])
    top_tags = [t for t, _ in tags.most_common(12)]
    title = contexts.most_common(1)[0][0] if contexts else ", ".join(top_tags[:3])
    lines, seen = [], set()
    for m in sorted(members, key=lambda m: m["start"]):
        line = f"{datetime.fromtimestamp(m['start']):%d %b %H:%M} · {m['app']}: {m['context'] or m['title']}"
        if (m["app"], m["context"]) not in seen:
            seen.add((m["app"], m["context"]))
            lines.append(line)
    sentences: list[tuple[int, str]] = []
    for m in members:
        for sentence in _SENTENCE.split(m.get("text") or ""):
            sentence = sentence.strip()
            if 25 <= len(sentence) <= 400:
                hits = sum(1 for t in top_tags if t in sentence.lower())
                if hits:
                    sentences.append((hits, sentence[:220]))
    picked: list[str] = []
    for _, sentence in sorted(sentences, key=lambda s: -s[0]):
        if sentence not in picked:
            picked.append(sentence)
        if len(picked) == 3:
            break
    if len(members) == 1 and not picked and members[0].get("text"):
        picked = [members[0]["text"][:600]]   # a single note or clip: keep its words, not just its title
    text = "\n".join(lines[:6] + ([""] + picked if picked else []))
    sources: list[dict[str, Any]] = []
    for m in members:
        sources += m.get("sources") or [{"kind": m["kind"], "app": m["app"], "title": m["context"] or m["title"],
                                          "at": m["start"]}]
    return {
        "title": title[:160], "text": text[:1500], "entities": top_tags,
        "sources": _dedupe_sources(sources)[:30],
        "importance": max(float(m.get("importance") or 0) for m in members),
        "start": min(m["start"] for m in members), "end": max(m["end"] for m in members),
        "refs": sum(int(m.get("refs") or 0) for m in members),
    }


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for s in sorted(sources, key=lambda s: -(s.get("at") or 0)):
        key = (s.get("kind"), s.get("app"), s.get("title"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def resolve(store: Any, entry: dict[str, Any], vector: np.ndarray) -> tuple[str, int]:
    """ADD or UPDATE against the long-term entries. Returns (action, long-term id)."""
    best_id, best_sim = None, 0.0
    for memory in store.listing(tier="long", limit=5000):
        other = store.vector(memory["id"])
        if other is None:
            continue
        sim = float(other @ vector)
        if sim > best_sim:
            best_id, best_sim = memory["id"], sim
    if best_id is not None and best_sim >= UPDATE_SIMILARITY:
        old = store.get(best_id)
        merged_text = (entry["text"] + "\n\n" + old["text"])[:1500]
        store.update(best_id, vector=_unit(store.vector(best_id) + vector),
                     text=merged_text, end=max(old["end"], entry["end"]),
                     entities=list(dict.fromkeys(entry["entities"] + old["entities"]))[:24],
                     sources=_dedupe_sources(entry["sources"] + old["sources"])[:30],
                     importance=max(old["importance"], entry["importance"]))
        return "update", best_id
    new_id = store.add({**entry, "tier": "long", "kind": "summary", "app": "", "context": entry["title"],
                        "created_at": time.time()}, vector)
    return "add", new_id


def run(store: Any, now: float, short_term_days: float = 7, promote_refs: int = 2,
        min_age_hours: float = 6) -> dict[str, int]:
    report = {"add": 0, "update": 0, "noop": 0, "expired": 0}
    chunks = [m for m in store.listing(tier="short", limit=20000, unmerged=True)
              if m["end"] <= now - min_age_hours * 3600]
    vectors = {m["id"]: v for m in chunks if (v := store.vector(m["id"])) is not None}
    for members in group(chunks, vectors):
        if not worth_keeping(members):
            report["noop"] += 1
            continue
        vecs = [vectors[m["id"]] for m in members if m["id"] in vectors]
        if not vecs:
            continue
        action, long_id = resolve(store, summarise(members), _unit(np.sum(vecs, axis=0)))
        report[action] += 1
        for m in members:
            store.update(m["id"], merged_into=long_id)
    report["expired"] = store.expire(now - short_term_days * 86400, promote_refs)
    return report


def promote(store: Any, memory_id: int) -> tuple[str, int] | None:
    """Early promotion: a chunk referenced twice goes to long-term now, not tonight."""
    memory, vector = store.get(memory_id), store.vector(memory_id)
    if memory is None or vector is None or memory["tier"] != "short" or memory.get("merged_into"):
        return None
    action, long_id = resolve(store, summarise([memory]), vector)
    store.update(memory_id, merged_into=long_id)
    return action, long_id
