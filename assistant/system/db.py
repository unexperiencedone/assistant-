"""SQLite schema and connection setup for the local index.

One `items` row per app, file or folder. An FTS5 trigram index over the names
gives substring search in milliseconds even with hundreds of thousands of rows.
Each thread opens its own connection; WAL mode lets the background indexer
write while voice commands read.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL CHECK (kind IN ('app', 'file', 'folder')),
    name       TEXT    NOT NULL,
    path       TEXT    NOT NULL UNIQUE,   -- filesystem path, or shell:AppsFolder\\<AppID>
    parent     TEXT,
    ext        TEXT,
    size       INTEGER,
    mtime      REAL,
    seen_at    INTEGER NOT NULL,          -- index pass that last saw it (for pruning)
    use_count  INTEGER NOT NULL DEFAULT 0,
    last_used  INTEGER,
    score      REAL    NOT NULL DEFAULT 0, -- frecency, valid as of score_at
    score_at   INTEGER NOT NULL DEFAULT 0,
    rank_key   REAL                        -- log2(score) + score_at / half_life: sorts like the decayed score
);
CREATE INDEX IF NOT EXISTS idx_items_kind   ON items (kind);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items (parent);
CREATE INDEX IF NOT EXISTS idx_items_size   ON items (size) WHERE kind = 'file';

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts
    USING fts5 (name, content = 'items', content_rowid = 'id', tokenize = 'trigram');

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts (rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, name) VALUES ('delete', old.id, old.name);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF name ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, name) VALUES ('delete', old.id, old.name);
    INSERT INTO items_fts (rowid, name) VALUES (new.id, new.name);
END;

-- Directory mtimes let re-index passes skip folders whose entries didn't change.
CREATE TABLE IF NOT EXISTS dirs (
    path       TEXT PRIMARY KEY,
    mtime      REAL NOT NULL,
    scanned_at INTEGER NOT NULL
);

-- Content hashes for duplicate detection, reused while size and mtime match.
CREATE TABLE IF NOT EXISTS hashes (
    path    TEXT PRIMARY KEY,
    size    INTEGER NOT NULL,
    mtime   REAL NOT NULL,
    partial TEXT,
    full    TEXT
);

-- "open spotify" -> the item you actually opened last time: an O(1) fast path.
CREATE TABLE IF NOT EXISTS query_cache (
    query     TEXT PRIMARY KEY,
    item_id   INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    hits      INTEGER NOT NULL DEFAULT 1,
    last_used INTEGER NOT NULL
);

-- Spoken/typed commands, ranked the same way as files, so the canvas can offer
-- the ones this user actually uses instead of a hardcoded list.
CREATE TABLE IF NOT EXISTS commands (
    text      TEXT PRIMARY KEY,
    count     INTEGER NOT NULL DEFAULT 0,
    last_used INTEGER,
    score     REAL    NOT NULL DEFAULT 0,
    score_at  INTEGER NOT NULL DEFAULT 0,
    rank_key  REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            self.migrated_from = int(row["value"]) if row else SCHEMA_VERSION
            _migrate(conn)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))

    def connect(self) -> sqlite3.Connection:
        """Per-thread connection (sqlite3 connections must not cross threads)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA cache_size = -20000")  # ~20 MB page cache
            self._local.conn = conn
        return conn

    def get_meta(self, key: str) -> str | None:
        row = self.connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema. Safe to run every start."""
    for table in ("items", "commands"):
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "rank_key" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN rank_key REAL")
    conn.execute("DROP INDEX IF EXISTS idx_items_used")  # sorted by raw score, which ignores decay
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_rank ON items (kind, rank_key) WHERE use_count > 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commands_rank ON commands (rank_key)")
