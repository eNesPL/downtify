"""AI Music Guard policy, assessments, evidence, overrides and scan jobs."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .sqlite_utils import connect_sqlite

DEFAULT_POLICY = {
    'enabled': True,
    'mode': 'balanced',
    'hide_confirmed': True,
    'hide_suspected': False,
    'check_downloads': True,
    'block_ai_covers': True,
    'block_assisted': False,
}


class AIGuardStore:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ai_assessments (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL,
                    checked REAL NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_seen (
                    user_id INTEGER, assessment_id TEXT,
                    PRIMARY KEY(user_id, assessment_id));
                CREATE TABLE IF NOT EXISTS ai_overrides (
                    user_id INTEGER, scope TEXT, target TEXT,
                    decision TEXT, note TEXT, created REAL,
                    PRIMARY KEY(user_id, scope, target));
                CREATE TABLE IF NOT EXISTS ai_policy (
                    id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_grants (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER,
                    request_key TEXT, target_key TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS ai_resolved (
                    request_key TEXT PRIMARY KEY, version_key TEXT);
                CREATE TABLE IF NOT EXISTS ai_scan_jobs (
                    id TEXT PRIMARY KEY, user_id INTEGER, data TEXT);
            """)
            conn.execute(
                'DELETE FROM ai_grants WHERE expires<?', (time.time(),)
            )
            rows = conn.execute('SELECT id,data FROM ai_scan_jobs').fetchall()
            for row in rows:
                data = json.loads(row['data'])
                if data['status'] == 'running':
                    data['status'] = 'interrupted'
                    conn.execute(
                        'UPDATE ai_scan_jobs SET data=? WHERE id=?',
                        (json.dumps(data), row['id']),
                    )

    def connect(self) -> Any:
        return connect_sqlite(self.path, row_factory=True)

    def policy(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                'SELECT data FROM ai_policy WHERE id=1'
            ).fetchone()
        return {**DEFAULT_POLICY, **(json.loads(row['data']) if row else {})}

    def set_policy(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO ai_policy VALUES (1,?)',
                (json.dumps(data),),
            )
        return self.policy()

    def assessment(
        self, key: str, user_id: int, *, fresh: bool = True
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                'SELECT * FROM ai_assessments WHERE id=?', (key,)
            ).fetchone()
            if row is None or (fresh and row['expires'] <= time.time()):
                return None
            conn.execute(
                'INSERT OR IGNORE INTO ai_seen VALUES (?,?)', (user_id, key)
            )
        return json.loads(row['data'])

    def visible(self, key: str, user_id: int) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    'SELECT 1 FROM ai_seen WHERE user_id=? AND assessment_id=?',
                    (user_id, key),
                ).fetchone()
                is not None
            )

    def save(self, data: dict[str, Any], user_id: int) -> dict[str, Any]:
        with self.lock, self.connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO ai_assessments VALUES (?,?,?,?)',
                (
                    data['id'],
                    json.dumps(data),
                    data['checked_at'],
                    data['expires_at'],
                ),
            )
            conn.execute(
                'INSERT OR IGNORE INTO ai_seen VALUES (?,?)',
                (user_id, data['id']),
            )
        return data

    def override(
        self, user_id: int, scope: str, target: str, decision: str, note: str
    ) -> None:
        with self.lock, self.connect() as conn:
            if decision == 'remove':
                conn.execute(
                    'DELETE FROM ai_overrides WHERE user_id=? AND scope=? AND target=?',
                    (user_id, scope, target),
                )
            else:
                conn.execute(
                    'INSERT OR REPLACE INTO ai_overrides VALUES (?,?,?,?,?,?)',
                    (
                        user_id,
                        scope,
                        target,
                        decision,
                        note,
                        time.time(),
                    ),
                )

    def overrides(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    'SELECT * FROM ai_overrides WHERE user_id IN (0,?) ORDER BY user_id DESC,created DESC',
                    (user_id,),
                )
            ]

    def grant(self, user_id: int, request_key: str, target_key: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock, self.connect() as conn:
            conn.execute(
                'DELETE FROM ai_grants WHERE expires<?', (time.time(),)
            )
            conn.execute(
                'INSERT INTO ai_grants VALUES (?,?,?,?,?)',
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    request_key,
                    target_key,
                    time.time() + 900,
                ),
            )
        return token

    def check_grant(
        self,
        token: str,
        user_id: int,
        request_key: str,
        target_key: str = '',
        *,
        consume: bool = False,
    ) -> bool:
        if not token:
            return False
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.lock, self.connect() as conn:
            row = conn.execute(
                'SELECT * FROM ai_grants WHERE token_hash=? AND user_id=? AND request_key=? AND expires>?',
                (
                    token_hash,
                    user_id,
                    request_key,
                    time.time(),
                ),
            ).fetchone()
            if row is None or (target_key and row['target_key'] != target_key):
                return False
            if consume:
                conn.execute(
                    'DELETE FROM ai_grants WHERE token_hash=?', (token_hash,)
                )
        return True

    def scan(self, job_id: str, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                'SELECT data FROM ai_scan_jobs WHERE id=? AND user_id=?',
                (job_id, user_id),
            ).fetchone()
        return json.loads(row['data']) if row else None

    def resolved(self, key: str) -> str:
        with self.connect() as conn:
            row = conn.execute(
                'SELECT version_key FROM ai_resolved WHERE request_key=?',
                (key,),
            ).fetchone()
        return row['version_key'] if row else ''

    def record_resolved(self, key: str, version: str) -> None:
        with self.lock, self.connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO ai_resolved VALUES (?,?)',
                (key, version),
            )

    def save_scan(self, data: dict[str, Any], user_id: int) -> None:
        with self.lock, self.connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO ai_scan_jobs VALUES (?,?,?)',
                (data['id'], user_id, json.dumps(data)),
            )
