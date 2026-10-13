"""AfterThought: Nova remembers what you worked on, and speaks up when it matters again.

The pipeline, one stage per module:

    watchers (capture.py) -> chunk boundaries (chunker.py) -> garbage filter + secret
    stripping (privacy.py, here) -> embed + entity-tag (embed.py, entities.py) ->
    short-term memory (store.py) -> nightly consolidation (consolidate.py) -> long-term
    knowledge base

and, on the other side, every time you settle into a new context:

    context builder -> salience gate (rank.py) -> ranked memory + its source ->
    tray, the Memory screen, and -- when it is a strong match and you are free -- Nova's
    voice, asking "useful?". Your yes or no moves that memory's agreement score.

Nothing is watched until you opt an app in. Capture, embedding and retrieval make no
network calls. Short-term memory expires after seven days unless it earns its keep.
"""

from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..awareness.collapse import app_name, classify, context_of
from ..events import EventBus
from . import capture, consolidate, embed, entities, privacy, rank
from .chunker import Chunker
from .store import MemoryStore

# Nova's own window is where you talk to Nova, not where you work.
_SELF = {"python", "pythonw", "nova", "nova-cli"}
# What people call an app -> what the watcher calls it (awareness.collapse.app_name).
_ALIASES = {
    # values are process names; app_name() turns them into what the watcher reports
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "google chrome": "chrome", "the browser": "chrome", "browser": "chrome", "edge": "msedge",
    "microsoft edge": "msedge", "word": "winword", "microsoft word": "winword", "powerpoint": "powerpnt",
    "outlook": "olk", "new outlook": "olk", "file explorer": "explorer", "files": "explorer",
    "terminal": "windowsterminal", "windows terminal": "windowsterminal", "anti gravity": "antigravity",
}

_ASKS = [
    "From {when}, {source}: {snippet}. Useful?",
    "This might connect: {when}, {source} had \"{snippet}\". Useful?",
    "Remember this from {when}? {source}: {snippet}. Helpful?",
    "Something related from {when} in {source}: {snippet}. Want it?",
]


def _when(ts: float, now: float) -> str:
    then, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now).date()
    days = (today - then.date()).days
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"on {then:%A}"
    return f"on {then:%d %B}".replace(" 0", " ")


def _snippet(memory: dict[str, Any], limit: int = 150) -> str:
    """The most telling line: a sentence that was there, else the title."""
    for line in (memory.get("text") or "").splitlines()[::-1]:
        line = line.strip()
        if len(line) > 25 and "·" not in line[:20]:
            return line[:limit].rstrip(" ,.;") + ("…" if len(line) > limit else "")
    return (memory.get("title") or memory.get("context") or "")[:limit]


_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(memory: dict[str, Any]) -> list[str]:
    parts = [p.strip() for p in _SENTENCES.split(memory.get("text") or "")]
    return [p for p in parts if 20 <= len(p) <= 300 and "·" not in p[:20]][:12]


def _source_name(memory: dict[str, Any]) -> str:
    source = (memory.get("sources") or [{}])[0]
    app, title = source.get("app") or memory.get("app") or "", source.get("title") or memory.get("context") or ""
    if memory.get("kind") == "nova":
        return "our conversation"
    if memory.get("kind") == "note":
        return "a note you asked me to keep"
    return f"{app}, {title[:60]}" if app and title else (title or app or "your notes")


class AfterThoughtService:
    def __init__(self, path: Path, bus: EventBus, settings: Any,
                 notify: Callable[[str], None] | None = None,
                 offer: Callable[[str, Callable[[bool], None]], None] | None = None,
                 can_speak: Callable[[], bool] | None = None,
                 known: Callable[[], list[str]] | None = None,
                 embedder: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.bus = bus
        self.settings = settings
        self.notify = notify
        self.offer = offer
        self.can_speak = can_speak or (lambda: True)
        self.known = known or (lambda: [])
        self.clock = clock
        self.embedder = embedder or embed.make(getattr(settings, "model", "sentence-transformers/all-MiniLM-L6-v2"))
        self.store = MemoryStore(path, getattr(self.embedder, "dim", 384))
        self.chunker = Chunker(float(getattr(settings, "idle_boundary_seconds", 30)),
                               float(getattr(settings, "min_dwell_seconds", 20)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._clip_seq = 0
        self._text_at = 0.0
        self._triggered_for: tuple[str, str] | None = None
        self._pending: dict[str, Any] | None = None   # the last suggestion, for "that's useful"
        self._seen_apps: dict[str, float] = {}
        self._last_user: str = ""
        self._ready = False
        self._revision = 0
        if self.store.meta("consent") is None:
            self.store.set_meta("consent", sorted({a.lower() for a in getattr(settings, "apps", [])}))
        bus.subscribe("transcript", self._on_transcript)

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None and getattr(self.settings, "enabled", True):
            self._thread = threading.Thread(target=self._run, name="afterthought", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3)
        with self._lock:
            for chunk in self.chunker.flush(self.clock()):
                self._ingest(chunk)
        self.store.close()

    def _run(self) -> None:
        try:
            self.embedder.encode(["warm up"])   # load the model off the critical path
        except Exception as error:
            self.bus.log(f"AfterThought: the embedding model didn't load ({error}); using the fallback.", "warn")
            self.embedder = embed.HashEmbedder()
        self._ready = True
        self._clip_seq = capture.clipboard_sequence()   # what was copied before Nova started isn't new
        self._publish()
        poll =max(1.0, float(getattr(self.settings, "poll_seconds", 2)))
        while not self._stop.wait(poll):
            try:
                self.tick()
            except Exception as error:   # the watcher must never take Nova down
                self.bus.log(f"AfterThought: {error}", "warn")

    # -- watching ---------------------------------------------------------------------
    def consent(self) -> list[str]:
        return list(self.store.meta("consent", []))

    def watched(self, app: str) -> bool:
        return app.lower() in self.consent()

    def set_consent(self, app: str, on: bool) -> str:
        """Opt an app in or out. Returns the name as Nova will say it."""
        name = self._resolve_app(app)
        apps = set(self.consent())
        (apps.add if on else apps.discard)(name.lower())
        self.store.set_meta("consent", sorted(apps))
        self._publish()
        return name

    def _resolve_app(self, spoken: str) -> str:
        """'vs code', 'google chrome', 'word' -> the app name the watcher reports."""
        spoken = " ".join((spoken or "").lower().replace(".exe", "").strip(" .?!").split())
        spoken = _ALIASES.get(spoken, spoken)
        for name in self._seen_apps:
            if name.lower() == spoken or name.lower().replace(" ", "") == spoken.replace(" ", ""):
                return name
        return app_name(spoken)

    def tick(self, observation: dict[str, Any] | None = None) -> None:
        """One poll. `observation` (ts, process, title, idle) is injected by tests."""
        obs = observation or self._observe()
        now, process, title, idle = obs["ts"], obs.get("process", ""), obs.get("title", ""), obs.get("idle", 0.0)
        app = context = ""
        category = "other"
        name = (process or "").lower().removesuffix(".exe")
        if process and name not in _SELF and not privacy.denied(
                process, title, set(getattr(self.settings, "deny_apps", [])), list(getattr(self.settings, "deny_titles", []))):
            pretty = app_name(process)
            self._seen_apps[pretty] = now
            if self.watched(pretty):
                category = classify(process)
                app, context = pretty, context_of(category, title)
        with self._lock:
            was_settled = self.chunker.settled
            closed = self.chunker.observe(now, app, context, idle, process, category)
            # The clipboard counter is read every poll, so a copy made in an unwatched app is
            # consumed there and never read later in a watched one.
            seq = obs["clip_seq"] if "clip_seq" in obs else capture.clipboard_sequence()
            copied, self._clip_seq = seq != self._clip_seq, seq
            if app:
                self._capture_extras(now, obs, copied)
            for chunk in closed:
                self._ingest(chunk)
            if app and self.chunker.settled and not was_settled and self._triggered_for != (app, context):
                self._triggered_for = (app, context)
                self._trigger(self.chunker.open)
        self._maybe_consolidate(now)

    def _observe(self) -> dict[str, Any]:
        front = capture.foreground()
        process, title = front if front else ("", "")
        return {"ts": self.clock(), "process": process, "title": title, "idle": capture.idle_seconds()}

    def _capture_extras(self, now: float, obs: dict[str, Any], copied: bool) -> None:
        if copied and getattr(self.settings, "capture_clipboard", True):
            text = obs.get("clip") if "clip" in obs else capture.clipboard_text()
            if text and len(text.strip()) >= 8:
                self.chunker.add_clip(privacy.redact(text.strip())[:2000])
        if getattr(self.settings, "capture_text", True) and self.chunker.settled and now - self._text_at >= 15:
            self._text_at = now
            text = obs.get("text") if "text" in obs else capture.window_text(int(getattr(self.settings, "text_chars", 4000)))
            if text:
                self.chunker.set_text(privacy.redact(text))

    # -- keeping ----------------------------------------------------------------------
    def _ingest(self, chunk: dict[str, Any]) -> int | None:
        """Garbage filter, then embed and store. Returns the memory id, or None if dropped."""
        clips = chunk.get("clips") or []
        body = privacy.redact("\n".join(clips + ([chunk["text"]] if chunk.get("text") else [])))
        dwell = chunk["end"] - chunk["start"]
        tags = entities.extract(f"{chunk['context']}\n{body}", self.known())
        if len(body) < int(getattr(self.settings, "min_chars", 40)) and (dwell < 120 or not tags):
            return None   # too little happened here to be worth remembering
        kind = "clipboard" if clips and not chunk.get("text") else \
            {"browser": "browser", "editor": "file"}.get(chunk.get("category", ""), "window")
        importance = 0.3 + (0.2 if len(tags) >= 3 else 0) + min(0.2, dwell / 600) + (0.15 if clips else 0)
        memory = {
            "tier": "short", "kind": kind, "start": chunk["start"], "end": chunk["end"], "app": chunk["app"],
            "context": chunk["context"], "title": chunk["context"] or chunk["app"],
            "text": body[: int(getattr(self.settings, "text_chars", 4000))], "entities": tags,
            "sources": [{"kind": kind, "app": chunk["app"], "title": chunk["context"], "at": chunk["start"]}],
            "importance": round(min(1.0, importance), 3), "created_at": self.clock(),
        }
        return self._keep(memory, f"{chunk['context']}\n{body[:2000]}")

    def _keep(self, memory: dict[str, Any], text: str) -> int | None:
        vector = self.embedder.encode([text])[0]
        # Dedup against the last few: the same thing again extends it (NO-OP), not a copy.
        for memory_id, other in self.store.recent_vectors(5):
            if float(other @ vector) >= 0.95:
                old = self.store.get(memory_id)
                if old and old["app"] == memory["app"]:
                    self.store.update(memory_id, end=max(old["end"], memory["end"]),
                                      entities=list(dict.fromkeys(old["entities"] + memory["entities"]))[:24])
                    return memory_id
        memory_id = self.store.add(memory, vector)
        self._publish()
        return memory_id

    def _on_transcript(self, event) -> None:
        """What you asked Nova and what it answered is memory too (it's already Nova's)."""
        if not getattr(self.settings, "remember_conversations", True):
            return
        role, text = event.data.get("role"), (event.data.get("text") or "").strip()
        if role == "user":
            self._last_user = text
            return
        if role != "assistant" or not self._last_user or len(text) < 30:
            return
        asked, self._last_user = self._last_user, ""
        body = privacy.redact(f"You asked: {asked}\nNova: {text}")
        now = self.clock()
        memory = {"tier": "short", "kind": "nova", "start": now, "end": now, "app": "Nova", "context": asked[:160],
                  "title": asked[:160], "text": body[:3000], "entities": entities.extract(body, self.known()),
                  "sources": [{"kind": "nova", "app": "Nova", "title": asked[:160], "at": now}],
                  "importance": 0.45, "created_at": now}
        threading.Thread(target=self._keep_quietly, args=(memory, body), daemon=True).start()

    def _keep_quietly(self, memory: dict[str, Any], text: str) -> None:
        try:
            with self._lock:
                self._keep(memory, text)
        except Exception as error:
            self.bus.log(f"AfterThought couldn't keep that exchange: {error}", "warn")

    def remember_now(self, note: str = "") -> str:
        """"Remember this": what is in front of you, kept straight into long-term memory."""
        front = capture.foreground()
        process, title = front if front else ("", "")
        if process and privacy.denied(process, title):
            return "That window is on the private list, so I won't keep it."
        text = capture.window_text(int(getattr(self.settings, "text_chars", 4000))) if process else None
        clip = capture.clipboard_text()
        app = app_name(process) if process else "Nova"
        context = context_of(classify(process), title) if process else (note[:160] or "a note")
        body = privacy.redact("\n".join(part for part in (note, clip, text) if part))[:4000]
        if not body and not context:
            return "There's nothing on screen I can read to remember."
        now = self.clock()
        memory = {"tier": "long", "kind": "note", "start": now, "end": now, "app": app, "context": context,
                  "title": note[:160] or context, "text": body or context,
                  "entities": entities.extract(f"{note}\n{context}\n{body}", self.known()),
                  "sources": [{"kind": "note", "app": app, "title": context, "at": now}],
                  "importance": 1.0, "refs": 2, "created_at": now}
        with self._lock:
            self.store.add(memory, self.embedder.encode([f"{note}\n{context}\n{body[:2000]}"])[0])
        self._publish()
        return random.choice(["Got it, I'll remember that.", "Kept.", "Noted, I'll hold on to it."]) + \
            (f" It's filed under {context[:60]}." if context and not note else "")

    # -- recalling --------------------------------------------------------------------
    def recall(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        """You asked: a lower bar than an unprompted suggestion, same ranking."""
        query = (query or "").strip()
        if not query:
            return []
        with self._lock:
            vector = self.embedder.encode([query])[0]
            ids, matrix = self.store.matrix()
            pairs = rank.search(vector, ids, matrix, top=30)
            candidates = [(m, s) for i, s in pairs if (m := self.store.get(i)) and not m.get("merged_into")]
            ranked = rank.rank(candidates, entities.extract(query, self.known()),
                               float(getattr(self.settings, "recall_threshold", 0.35)), self.clock())[:limit]
            for hit in ranked:
                self._referenced(hit["memory"]["id"], "refs")
                hit["focus"] = self._focus(hit["memory"], vector, 180)
        return ranked

    def spoken_recall(self, query: str) -> str:
        hits = self.recall(query, 2)
        if not hits:
            return f"I don't have anything about {query}."
        now = self.clock()
        parts = [f"{_when(h['memory']['start'], now)}, {_source_name(h['memory'])}: {h['focus']}"
                 for h in hits]
        return "Here's what I have. " + " Also, ".join(parts) + "."

    def _focus(self, memory: dict[str, Any], query: np.ndarray, limit: int = 150) -> str:
        """The sentence of a memory that speaks to what you're doing now, not just its last line."""
        sentences = _sentences(memory)
        if len(sentences) < 2:
            return _snippet(memory, limit)
        sims = self.embedder.encode(sentences) @ np.asarray(query, dtype=np.float32)
        best = sentences[int(np.argmax(sims))]
        return best[:limit].rstrip(" ,.;") + ("…" if len(best) > limit else "")

    def _referenced(self, memory_id: int, column: str) -> None:
        self.store.bump(memory_id, column)
        memory = self.store.get(memory_id)
        if memory and memory["tier"] == "short" and memory["refs"] >= int(getattr(self.settings, "promote_refs", 2)):
            consolidate.promote(self.store, memory_id)

    # -- speaking up ------------------------------------------------------------------
    def paused(self) -> bool:
        return float(self.store.meta("paused_until", 0) or 0) > self.clock()

    def pause(self, seconds: float) -> None:
        self.store.set_meta("paused_until", self.clock() + seconds)
        self._publish()

    def resume(self) -> None:
        self.store.set_meta("paused_until", 0)
        self._publish()

    def _trigger(self, chunk: dict[str, Any] | None) -> dict[str, Any] | None:
        """You settled into a new context: is there a memory worth surfacing?"""
        if chunk is None or self.paused() or not getattr(self.settings, "suggest", True):
            return None
        now = self.clock()
        if now - self.store.last_suggestion_at() < float(getattr(self.settings, "cooldown_seconds", 180)):
            return None
        query = "\n".join([chunk["context"], *chunk.get("clips", [])[-2:], (chunk.get("text") or "")[:1000]])
        vector = self.embedder.encode([query])[0]
        ids, matrix = self.store.matrix()
        recent = now - 30 * 60
        repeat = now - float(getattr(self.settings, "same_memory_hours", 6)) * 3600
        candidates = []
        for memory_id, similarity in rank.search(vector, ids, matrix, top=40):
            memory = self.store.get(memory_id)
            if (memory is None or memory.get("merged_into")
                    or (memory["app"], memory["context"]) == (chunk["app"], chunk["context"])  # what you're looking at
                    or (memory["kind"] != "summary" and memory["end"] >= recent)               # what you just did
                    or float(memory.get("last_shown") or 0) >= repeat):
                continue
            candidates.append((memory, similarity))
        ranked = rank.rank(candidates, entities.extract(query, self.known()),
                           float(getattr(self.settings, "threshold", 0.55)), now)
        if not ranked:
            return None   # silence is the default
        best = ranked[0]
        best["focus"] = self._focus(best["memory"], vector)
        return self._suggest(best, chunk["context"])

    def _suggest(self, best: dict[str, Any], context: str) -> dict[str, Any]:
        memory, now = best["memory"], self.clock()
        speak = bool(getattr(self.settings, "voice", True) and self.offer is not None
                     and best["similarity"] >= float(getattr(self.settings, "voice_threshold", 0.65))
                     and self.can_speak())
        suggestion_id = self.store.log_suggestion(memory["id"], best["score"], best["similarity"], context, speak)
        self.store.bump(memory["id"], "shown")
        self._pending = {"id": suggestion_id, "memory_id": memory["id"], "at": now}
        focus = best.get("focus") or _snippet(memory)
        line = f"{_source_name(memory)} ({_when(memory['start'], now)}): {focus}"
        self.bus.publish("afterthought_suggestion", id=suggestion_id, memory=memory["id"], text=line,
                         similarity=round(best["similarity"], 3), score=round(best["score"], 3), spoken=speak)
        self.bus.log(f"AfterThought: {line} (similarity {best['similarity']:.2f})")
        if self.notify is not None:
            self.notify(line)
        if speak:
            ask = random.choice(_ASKS).format(when=_when(memory["start"], now), source=_source_name(memory),
                                              snippet=focus[:120])
            self.offer(ask, lambda yes, sid=suggestion_id: self.answer(yes, sid))
        self._publish()
        return {"id": suggestion_id, "memory": memory, "line": line, "spoken": speak, **best}

    def answer(self, useful: bool, suggestion_id: int | None = None) -> str:
        """Accept or dismiss a suggestion (the last one if no id). This is how it learns."""
        pending = self._pending
        if suggestion_id is None:
            if pending is None:
                return "There's no suggestion to answer."
            suggestion_id = pending["id"]
        row = next((s for s in self.store.suggestions(50) if s["id"] == suggestion_id), None)
        if row is None:
            return "I can't find that suggestion."
        self.store.resolve_suggestion(suggestion_id, "accepted" if useful else "dismissed")
        if useful:
            self._referenced(row["memory_id"], "accepted")
        else:
            self.store.bump(row["memory_id"], "dismissed")
        if pending and pending["id"] == suggestion_id:
            self._pending = None
        self._publish()
        return random.choice(["Good, I'll bring that kind of thing up again.", "Noted.", "Glad it helped."]) if useful \
            else random.choice(["Okay, I'll rank that lower.", "Got it, less of that.", "Noted, I'll hold back on that one."])

    def forget(self, memory_id: int | None = None) -> str:
        if memory_id is None:
            if self._pending is None:
                return "I haven't brought anything up to forget."
            memory_id = self._pending["memory_id"]
            self._pending = None
        gone = self.store.delete(memory_id)
        self._publish()
        return "Forgotten." if gone else "That memory is already gone."

    # -- consolidation ----------------------------------------------------------------
    def _maybe_consolidate(self, now: float) -> None:
        last = float(self.store.meta("consolidated_at", 0) or 0)
        hour = int(getattr(self.settings, "consolidate_hour", 3))
        due_today = datetime.fromtimestamp(now).replace(hour=hour, minute=0, second=0).timestamp()
        if due_today > now:
            due_today -= 86400
        if last < due_today:
            self.consolidate()

    def consolidate(self) -> dict[str, int]:
        with self._lock:
            report = consolidate.run(self.store, self.clock(), float(getattr(self.settings, "short_term_days", 7)),
                                     int(getattr(self.settings, "promote_refs", 2)))
            self.store.set_meta("consolidated_at", self.clock())
        self.bus.log(f"AfterThought consolidated: {report['add']} new, {report['update']} updated, "
                     f"{report['noop']} let go, {report['expired']} expired.")
        self._publish()
        return report

    # -- reporting --------------------------------------------------------------------
    def spoken_status(self) -> str:
        apps = self.consent()
        counts = self.store.counts()
        watching = ", ".join(apps) if apps else "nothing yet; say remember what I do in, and an app"
        pause = " Suggestions are paused." if self.paused() else ""
        return (f"I'm watching {watching}. I hold {counts['short']} recent memories and {counts['long']} "
                f"long-term ones.{pause}")

    def state(self) -> dict[str, Any]:
        counts = self.store.counts()
        return {"revision": self._revision, "enabled": bool(getattr(self.settings, "enabled", True)),
                "ready": self._ready, "watching": len(self.consent()), "short": counts["short"],
                "long": counts["long"], "paused": self.paused()}

    def document(self) -> dict[str, Any]:
        now = self.clock()
        suggestions = []
        for s in self.store.suggestions(15):
            memory = self.store.get(s["memory_id"])
            if memory:
                suggestions.append({**s, "title": memory["title"], "snippet": _snippet(memory, 220),
                                    "source": _source_name(memory), "when": _when(memory["start"], now)})
        seen = sorted(self._seen_apps, key=lambda a: -self._seen_apps[a])
        consent = self.consent()
        return {
            **self.state(),
            "model": getattr(self.embedder, "name", ""),
            "short_count": self.store.counts()["short"],
            "threshold": float(getattr(self.settings, "threshold", 0.55)),
            "paused_until": float(self.store.meta("paused_until", 0) or 0),
            "consolidated_at": float(self.store.meta("consolidated_at", 0) or 0),
            "apps": [{"name": a, "watched": a.lower() in consent} for a in
                     list(dict.fromkeys(seen + [c for c in consent if c not in {s.lower() for s in seen}]))][:30],
            "suggestions": suggestions,
            "long": [self._shown(m) for m in self.store.listing(tier="long", limit=200)],
            "short": [self._shown(m) for m in self.store.listing(tier="short", limit=40, unmerged=True)],
        }

    @staticmethod
    def _shown(memory: dict[str, Any]) -> dict[str, Any]:
        keep = ("id", "tier", "kind", "start", "end", "app", "context", "title", "text", "entities", "sources",
                "importance", "refs", "accepted", "dismissed", "shown")
        out = {k: memory.get(k) for k in keep}
        out["text"] = (out["text"] or "")[:600]
        return out

    def _publish(self) -> None:
        self._revision += 1
        self.bus.publish("afterthought", **self.state())
