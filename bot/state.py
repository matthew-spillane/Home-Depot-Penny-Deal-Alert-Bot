"""Persistent state for dedup and alert history (SQLite).

Why SQLite: zero infra to start. IMPORTANT for Railway — containers are
ephemeral, so point DB_PATH at a mounted volume (e.g. /data/penny.sqlite3) or
this file (and therefore all dedup state) vanishes on every redeploy. Phase 3
can migrate to Postgres if durability/scale demands it.

All methods are synchronous sqlite calls. They are fast and infrequent (a
handful per poll cycle), so we run them directly; if they ever become hot they
can be wrapped in loop.run_in_executor.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import date


class State:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                reddit_id   TEXT PRIMARY KEY,   -- post or comment fullname/id
                seen_at     INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                sku        TEXT NOT NULL,
                store_id   TEXT NOT NULL,        -- "" for Phase 1 (no store)
                alert_day  TEXT NOT NULL,        -- YYYY-MM-DD, for daily dedupe
                alerted_at INTEGER NOT NULL,
                PRIMARY KEY (sku, store_id, alert_day)
            );

            CREATE TABLE IF NOT EXISTS watched_stores (
                store_id  TEXT PRIMARY KEY,       -- added at runtime via !addstore
                added_at  INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    # ── Reddit post/comment dedup ────────────────────────────────────────────
    def is_seen(self, reddit_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE reddit_id = ?", (reddit_id,)
        )
        return cur.fetchone() is not None

    def mark_seen(self, reddit_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_items (reddit_id, seen_at) VALUES (?, ?)",
            (reddit_id, int(time.time())),
        )
        self._conn.commit()

    # ── Per-(SKU, store) daily alert dedup ───────────────────────────────────
    def already_alerted_today(self, sku: str, store_id: str = "") -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM alert_history WHERE sku = ? AND store_id = ? AND alert_day = ?",
            (sku, store_id, date.today().isoformat()),
        )
        return cur.fetchone() is not None

    def record_alert(self, sku: str, store_id: str = "") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO alert_history (sku, store_id, alert_day, alerted_at) "
            "VALUES (?, ?, ?, ?)",
            (sku, store_id, date.today().isoformat(), int(time.time())),
        )
        self._conn.commit()

    # ── Runtime-added watched stores (Phase 3 multi-store) ───────────────────
    def add_store(self, store_id: str) -> bool:
        """Add a store; returns True if newly added, False if already present."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO watched_stores (store_id, added_at) VALUES (?, ?)",
            (store_id, int(time.time())),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remove_store(self, store_id: str) -> bool:
        """Remove a store; returns True if it was present."""
        cur = self._conn.execute(
            "DELETE FROM watched_stores WHERE store_id = ?", (store_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_stores(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT store_id FROM watched_stores ORDER BY added_at"
        )
        return [row["store_id"] for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
