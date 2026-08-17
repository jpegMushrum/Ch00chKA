from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import aiosqlite

from ch00chka.domain import ChatMessage


class SQLiteConversationRepository:
    def __init__(self, *, db_path: str, default_personality: str) -> None:
        self._db_path = db_path
        self._default_personality = default_personality

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self._db_path)
        try:
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA busy_timeout=5000")
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        db_dir = os.path.dirname(os.path.abspath(self._db_path))
        os.makedirs(db_dir, exist_ok=True)

        async with self._connection() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    text TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_meta (
                    chat_id INTEGER PRIMARY KEY,
                    summary TEXT DEFAULT '',
                    personality TEXT DEFAULT '',
                    last_message_time REAL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id_id
                ON chat_history(chat_id, id)
                """
            )
            await db.commit()

    async def add_message(
        self,
        *,
        chat_id: int,
        role: str,
        user_name: str,
        text: str,
    ) -> None:
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_history (chat_id, role, user_name, text)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, role, user_name, text),
            )
            await db.execute(
                """
                INSERT INTO chat_meta (chat_id, last_message_time)
                VALUES (?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET last_message_time = excluded.last_message_time
                """,
                (chat_id, time.time()),
            )
            await db.commit()

    async def recent_messages(
        self,
        *,
        chat_id: int,
        limit: int,
    ) -> Sequence[ChatMessage]:
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT role, user_name, text
                FROM chat_history
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        return tuple(
            ChatMessage(role=role, user_name=user_name, text=text)
            for role, user_name, text in reversed(rows)
            if text
        )

    async def get_summary(self, chat_id: int) -> str:
        async with self._connection() as db:
            async with db.execute(
                "SELECT summary FROM chat_meta WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return str(row[0]) if row and row[0] else ""

    async def get_personality(self, chat_id: int) -> str:
        async with self._connection() as db:
            async with db.execute(
                "SELECT personality FROM chat_meta WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return str(row[0]) if row and row[0] else self._default_personality

    async def set_personality(self, chat_id: int, personality: str) -> None:
        normalized = personality.strip()
        if not normalized:
            raise ValueError("Personality cannot be empty")

        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_meta (chat_id, personality)
                VALUES (?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET personality = excluded.personality
                """,
                (chat_id, normalized),
            )
            await db.commit()
