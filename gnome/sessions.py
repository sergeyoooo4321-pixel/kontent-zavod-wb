"""Sessions: SQLite per chat_id, восстанавливаются при рестарте сервиса."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    chat_id INTEGER PRIMARY KEY,
    messages_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


@dataclass
class Session:
    chat_id: int
    messages: list[dict] = field(default_factory=list)
    updated_at: int = 0


class SessionStore:
    def __init__(self, db_path: Path):
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._locks: dict[int, asyncio.Lock] = {}

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self._path), isolation_level=None, timeout=10.0)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def load(self, chat_id: int) -> Session:
        with self._conn() as c:
            row = c.execute(
                "SELECT messages_json, updated_at FROM sessions WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        if not row:
            return Session(chat_id=chat_id, messages=[], updated_at=int(time.time()))
        try:
            msgs = json.loads(row[0])
        except Exception:
            msgs = []
        return Session(chat_id=chat_id, messages=msgs, updated_at=int(row[1]))

    def save(self, sess: Session):
        sess.updated_at = int(time.time())
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(chat_id, messages_json, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET "
                "  messages_json=excluded.messages_json, "
                "  updated_at=excluded.updated_at",
                (sess.chat_id, json.dumps(sess.messages, ensure_ascii=False), sess.updated_at),
            )

    def reset(self, chat_id: int):
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE chat_id=?", (chat_id,))

    def list_chats(self) -> list[int]:
        with self._conn() as c:
            rows = c.execute("SELECT chat_id FROM sessions ORDER BY updated_at DESC").fetchall()
        return [r[0] for r in rows]
