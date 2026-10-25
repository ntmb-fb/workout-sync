"""SQLite store: a key/value table plus one row per Strava activity with a
status per target (garmin, polar)."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

TARGETS = ("garmin", "polar")

# Per-target status values
PENDING, DONE, DUPLICATE, FAILED, SKIPPED = "pending", "done", "duplicate", "failed", "skipped"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activities (
    strava_id INTEGER PRIMARY KEY,
    name TEXT, sport_type TEXT, start_date TEXT, device TEXT,
    skipped_reason TEXT,
    garmin_status TEXT, garmin_detail TEXT, garmin_attempts INTEGER NOT NULL DEFAULT 0,
    polar_status TEXT, polar_detail TEXT, polar_attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: Path, secret_key: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
        key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
        self._fernet = Fernet(key)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock, self._conn:
            return self._conn.execute(sql, args).fetchall()

    # ---- key/value

    def get(self, key: str, default: Any = None) -> Any:
        rows = self._exec("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key=?", (key,))

    def set_secret(self, key: str, value: Any) -> None:
        self.set(key, self._fernet.encrypt(json.dumps(value).encode()).decode())

    def get_secret(self, key: str, default: Any = None) -> Any:
        token = self.get(key)
        if token is None:
            return default
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken:  # SECRET_KEY changed
            return default

    # ---- activities

    def has_activity(self, strava_id: int) -> bool:
        return bool(self._exec("SELECT 1 FROM activities WHERE strava_id=?", (strava_id,)))

    def add_activity(self, strava_id: int, name: str, sport_type: str, start_date: str, device: str,
                     targets: list[str], skipped_reason: str | None = None) -> None:
        now = time.time()
        status = {t: (SKIPPED if skipped_reason or t not in targets else PENDING) for t in TARGETS}
        self._exec(
            "INSERT OR IGNORE INTO activities(strava_id,name,sport_type,start_date,device,skipped_reason,"
            "garmin_status,polar_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (strava_id, name, sport_type, start_date, device, skipped_reason,
             status["garmin"], status["polar"], now, now))

    def set_status(self, strava_id: int, target: str, status: str, detail: str = "",
                   count_attempt: bool = False) -> None:
        assert target in TARGETS
        inc = 1 if count_attempt else 0
        self._exec(f"UPDATE activities SET {target}_status=?, {target}_detail=?, "
                   f"{target}_attempts={target}_attempts+?, updated_at=? WHERE strava_id=?",
                   (status, detail, inc, time.time(), strava_id))

    def reset(self, strava_id: int, target: str) -> None:
        """Queue a target again (manual resend); also clears an activity-level skip."""
        assert target in TARGETS
        self._exec(f"UPDATE activities SET {target}_status=?, {target}_detail='', {target}_attempts=0, "
                   "skipped_reason=NULL, updated_at=? WHERE strava_id=?", (PENDING, time.time(), strava_id))

    def work_queue(self, targets: list[str], max_attempts: int) -> list[dict]:
        """Activities with at least one enabled target still to upload."""
        conds = [f"({t}_status='{PENDING}' OR ({t}_status='{FAILED}' AND {t}_attempts<{int(max_attempts)}))"
                 for t in targets if t in TARGETS]
        if not conds:
            return []
        rows = self._exec(f"SELECT * FROM activities WHERE {' OR '.join(conds)} ORDER BY start_date")
        return [dict(r) for r in rows]

    def needs(self, row: dict, target: str, max_attempts: int) -> bool:
        s = row[f"{target}_status"]
        return s == PENDING or (s == FAILED and row[f"{target}_attempts"] < max_attempts)

    def recent(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM activities ORDER BY start_date DESC LIMIT ?", (limit,))]

    def activity(self, strava_id: int) -> dict | None:
        rows = self._exec("SELECT * FROM activities WHERE strava_id=?", (strava_id,))
        return dict(rows[0]) if rows else None
