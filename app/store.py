"""SQLite storage for metric history and the event log."""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("store")


class Store:
    def __init__(self, path: str, retention_days: int):
        self.retention = retention_days * 86400
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._init()
            self.path = path
        except (OSError, sqlite3.Error) as e:
            log.warning("cannot use %s (%s); history will be kept in memory only", path, e)
            self._db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self._init()
            self.path = ":memory:"

    def _init(self) -> None:
        db = self._db
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS samples_idx ON samples (metric, ts)")
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL)")

    def add_samples(self, ts: float, values: Dict[str, Optional[float]]) -> None:
        rows = [(int(ts), k, float(v)) for k, v in values.items()
                if v is not None and not math.isnan(v) and not math.isinf(v)]
        if rows:
            with self._lock:
                self._db.executemany("INSERT INTO samples VALUES (?,?,?)", rows)

    def history(self, metrics: List[str], since: float, until: float,
                max_points: int = 480) -> Dict[str, list]:
        bucket = max(1, math.ceil((until - since) / max_points))
        out = {}
        with self._lock:
            for m in metrics:
                cur = self._db.execute(
                    "SELECT (ts/?)*?+?/2, AVG(value), MIN(value), MAX(value) FROM samples "
                    "WHERE metric=? AND ts>=? GROUP BY ts/? ORDER BY 1",
                    (bucket, bucket, bucket, m, int(since), bucket))
                out[m] = [list(r) for r in cur.fetchall()]
        return out

    def add_event(self, level: str, kind: str, message: str, ts: Optional[float] = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO events (ts, level, kind, message) VALUES (?,?,?,?)",
                             (int(ts or time.time()), level, kind, message))

    def events(self, limit: int = 40) -> List[dict]:
        with self._lock:
            cur = self._db.execute("SELECT ts, level, kind, message FROM events ORDER BY id DESC LIMIT ?", (limit,))
            return [{"ts": r[0], "level": r[1], "kind": r[2], "message": r[3]} for r in cur.fetchall()]

    def prune(self) -> None:
        cutoff = int(time.time() - self.retention)
        with self._lock:
            self._db.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            self._db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
