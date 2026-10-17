"""Durable, per-user AMV recognition jobs and approved song identities."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .sqlite_utils import connect_sqlite

ACTIVE = ('queued', 'running')


class RecognitionStore:
    """Keep source metadata separate from an explicitly approved identity."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.lock = threading.Lock()
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS music_recognition (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                source_key TEXT NOT NULL, status TEXT NOT NULL,
                data TEXT NOT NULL, updated REAL NOT NULL
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS recognition_source
                ON music_recognition(user_id, source_key, updated)""")
            # A killed process cannot finish its threads. Keep the results
            # and source so the user can explicitly retry after a restart.
            conn.execute("""UPDATE music_recognition SET status='interrupted'
                WHERE status IN ('queued', 'running')""")

    def connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, row_factory=True)

    @staticmethod
    def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            **json.loads(row['data']),
            'id': row['id'],
            'status': row['status'],
            'updated': row['updated'],
        }

    def get(self, job_id: str, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.decode(
                conn.execute(
                    'SELECT * FROM music_recognition WHERE id=? AND user_id=?',
                    (job_id, user_id),
                ).fetchone()
            )

    def recent(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                'SELECT * FROM music_recognition WHERE user_id=? '
                'ORDER BY updated DESC LIMIT 100',
                (user_id,),
            ).fetchall()
        return [self.decode(row) for row in rows]

    def approved(self, key: str, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.decode(
                conn.execute(
                    'SELECT * FROM music_recognition WHERE source_key=? '
                    "AND user_id=? AND status='accepted' "
                    'ORDER BY updated DESC LIMIT 1',
                    (key, user_id),
                ).fetchone()
            )

    def latest(self, key: str, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.decode(
                conn.execute(
                    'SELECT * FROM music_recognition WHERE source_key=? '
                    'AND user_id=? ORDER BY updated DESC LIMIT 1',
                    (key, user_id),
                ).fetchone()
            )

    def create(
        self, source: dict[str, Any], user_id: int, *, retry: bool = False
    ) -> tuple[dict[str, Any], bool]:
        """Deduplicate work, reuse results and bound concurrent paid work."""
        key = source['key']
        with self.lock, self.connect() as conn:
            row = conn.execute(
                'SELECT * FROM music_recognition WHERE source_key=? '
                'AND user_id=? ORDER BY updated DESC LIMIT 1',
                (key, user_id),
            ).fetchone()
            if row and (row['status'] in ACTIVE or not retry):
                return self.decode(row), False
            count = conn.execute(
                'SELECT COUNT(*) FROM music_recognition '
                "WHERE status IN ('queued','running') AND user_id=?",
                (user_id,),
            ).fetchone()[0]
            total = conn.execute(
                'SELECT COUNT(*) FROM music_recognition '
                "WHERE status IN ('queued','running')",
            ).fetchone()[0]
            if count >= 10 or total >= 50:
                raise ValueError(
                    'Recognition queue is full; wait for a job to finish'
                )
            attempts = conn.execute(
                'SELECT COUNT(*) FROM music_recognition WHERE user_id=? '
                "AND json_extract(data, '$.created')>?",
                (user_id, time.time() - 300),
            ).fetchone()[0]
            if attempts >= 30:
                raise ValueError(
                    'Recognition rate limit reached; retry in five minutes'
                )
            job_id = uuid.uuid4().hex
            data = {
                'source': source,
                'candidates': [],
                'warnings': [],
                'created': time.time(),
            }
            conn.execute(
                'INSERT INTO music_recognition VALUES (?,?,?,?,?,?)',
                (
                    job_id,
                    user_id,
                    key,
                    'queued',
                    json.dumps(data),
                    time.time(),
                ),
            )
            # Keep the cache bounded without deleting approved identities.
            conn.execute(
                'DELETE FROM music_recognition WHERE user_id=? '
                "AND status NOT IN ('queued','running','accepted') "
                'AND id NOT IN (SELECT id FROM music_recognition '
                'WHERE user_id=? ORDER BY updated DESC LIMIT 200)',
                (user_id, user_id),
            )
        return self.get(job_id, user_id), True

    def update(
        self, job_id: str, user_id: int, status: str, **fields: Any
    ) -> dict[str, Any]:
        with self.lock, self.connect() as conn:
            row = conn.execute(
                'SELECT * FROM music_recognition WHERE id=? AND user_id=?',
                (job_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError('Recognition not found')
            data = json.loads(row['data'])
            data.update(fields)
            if status == 'accepted':
                # One approved identity per source and user. Rejecting a
                # later re-analysis must not erase the previous approval.
                conn.execute(
                    "UPDATE music_recognition SET status='superseded' "
                    "WHERE source_key=? AND user_id=? AND status='accepted' "
                    'AND id!=?',
                    (row['source_key'], user_id, job_id),
                )
            conn.execute(
                'UPDATE music_recognition SET status=?,data=?,updated=? '
                'WHERE id=? AND user_id=?',
                (status, json.dumps(data), time.time(), job_id, user_id),
            )
        return self.get(job_id, user_id)
