from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models import BotState


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_states (
                    chat_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get(self, chat_id: int) -> BotState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT state_json FROM bot_states WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            return None
        return BotState.model_validate(json.loads(row["state_json"]))

    def set(self, chat_id: int, state: BotState) -> None:
        payload = state.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_states(chat_id, state_json, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, payload),
            )

    def delete(self, chat_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bot_states WHERE chat_id = ?", (chat_id,))

