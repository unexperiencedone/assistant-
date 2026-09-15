"""Query the index and rank results by match quality and frecency."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from .db import Database
from .ranking import decayed, match_score, normalize, rank

FTS_CANDIDATES = 400


@dataclass
class Hit:
    id: int
    kind: str
    name: str
    path: str
    size: int | None
    frecency: float
    score: float


class Searcher:
    def __init__(self, db: Database, half_life: float) -> None:
        self.db = db
        self.half_life = half_life

    def search(
        self,
        query: str,
        kind: str | None = None,
        ext: str | None = None,
        under: str | None = None,
        limit: int = 10,
        prefer_kind: str | None = None,
    ) -> list[Hit]:
        q = normalize(query)
        if not q:
            return []
        conn = self.db.connect()
        rows = self._candidates(conn, query, q, kind, ext, under)
        now = time.time()
        hits = []
        for r in rows:
            m = match_score(q, r["name"])
            if m <= 0:
                continue
            frec = decayed(r["score"], r["score_at"], now, self.half_life)
            hits.append(Hit(r["id"], r["kind"], r["name"], r["path"], r["size"], frec,
                            rank(m, frec, r["kind"], r["path"], prefer_kind)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    @staticmethod
    def _filters(kind: str | None, ext: str | None, under: str | None) -> tuple[str, list]:
        filters, params = [], []
        if kind:
            filters.append("i.kind = ?")
            params.append(kind)
        if ext:
            filters.append("i.ext = ?")
            params.append((ext if ext.startswith(".") else "." + ext).lower())
        if under:
            sep = "\\" if "\\" in under else "/"
            base = under.rstrip("\\/")
            filters.append("(i.path >= ? AND i.path < ?)")  # prefix range scan on the path index
            params += [base + sep, base + chr(ord(sep) + 1)]
        return (" AND " + " AND ".join(filters)) if filters else "", params

    def _candidates(self, conn: sqlite3.Connection, raw: str, q: str, kind, ext, under) -> list[sqlite3.Row]:
        where, params = self._filters(kind, ext, under)
        cols = "i.id, i.kind, i.name, i.path, i.size, i.score, i.score_at"

        rows: dict[int, sqlite3.Row] = {}
        # Apps are few: score them all so initials like "vsc" still match.
        if kind in (None, "app") and not ext and not under:
            for r in conn.execute(f"SELECT {cols} FROM items i WHERE i.kind = 'app'"):
                rows[r["id"]] = r

        # Frequently used things are always considered, whatever their name length.
        for r in conn.execute(f"SELECT {cols} FROM items i WHERE i.use_count > 0{where} ORDER BY i.score DESC LIMIT 300", params):
            rows[r["id"]] = r

        longest = max(q.split(), key=len)
        if len(longest) >= 3:
            # Trigram FTS on the most selective word; quotes make it a literal substring.
            fts_query = '"' + longest.replace('"', '""') + '"'
            sql = (f"SELECT {cols} FROM items_fts f JOIN items i ON i.id = f.rowid "
                   f"WHERE items_fts MATCH ?{where} LIMIT {FTS_CANDIDATES}")
            for r in conn.execute(sql, [fts_query, *params]):
                rows[r["id"]] = r
        else:
            sql = f"SELECT {cols} FROM items i WHERE i.name LIKE ? ESCAPE '\\'{where} LIMIT {FTS_CANDIDATES}"
            like = raw.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for r in conn.execute(sql, [like, *params]):
                rows[r["id"]] = r
        return list(rows.values())

    def top(self, kind: str | None = None, limit: int = 10) -> list[Hit]:
        conn = self.db.connect()
        sql = "SELECT id, kind, name, path, size, score, score_at FROM items WHERE use_count > 0"
        params: list = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        now = time.time()
        hits = [Hit(r["id"], r["kind"], r["name"], r["path"], r["size"],
                    decayed(r["score"], r["score_at"], now, self.half_life), 0.0)
                for r in conn.execute(sql + " ORDER BY score DESC LIMIT 500", params)]
        for h in hits:
            h.score = h.frecency
        hits.sort(key=lambda h: h.frecency, reverse=True)
        return hits[:limit]
