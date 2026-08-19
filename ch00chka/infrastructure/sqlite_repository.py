from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import aiosqlite

from ch00chka.domain import ChatMessage, SummarizableMessage, SummaryBatch


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
                    summary_message_id INTEGER NOT NULL DEFAULT 0,
                    personality TEXT DEFAULT '',
                    aliases TEXT DEFAULT '[]',
                    last_message_time REAL
                )
                """
            )
            async with db.execute("PRAGMA table_info(chat_meta)") as cursor:
                columns = {str(row[1]) for row in await cursor.fetchall()}
            if "aliases" not in columns:
                await db.execute(
                    "ALTER TABLE chat_meta ADD COLUMN aliases TEXT DEFAULT '[]'"
                )
            if "summary_message_id" not in columns:
                await db.execute(
                    "ALTER TABLE chat_meta ADD COLUMN "
                    "summary_message_id INTEGER NOT NULL DEFAULT 0"
                )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id_id
                ON chat_history(chat_id, id)
                """
            )
            await db.commit()

    async def get_aliases(self, chat_id: int) -> tuple[str, ...]:
        async with self._connection() as db:
            async with db.execute(
                "SELECT aliases FROM chat_meta WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row or not row[0]:
            return ()
        try:
            values = json.loads(str(row[0]))
        except (TypeError, ValueError):
            return ()
        if not isinstance(values, list):
            return ()
        return tuple(value for value in values if isinstance(value, str))

    async def set_aliases(self, chat_id: int, aliases: Sequence[str]) -> None:
        values = tuple(alias.strip() for alias in aliases if alias.strip())
        if not values:
            raise ValueError("Aliases cannot be empty")

        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_meta (chat_id, aliases)
                VALUES (?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET aliases = excluded.aliases
                """,
                (chat_id, json.dumps(values, ensure_ascii=False)),
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

    async def get_summary_batch(
        self,
        *,
        chat_id: int,
        keep_recent: int,
        min_batch_size: int,
        max_batch_size: int,
    ) -> SummaryBatch | None:
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT summary, summary_message_id
                FROM chat_meta
                WHERE chat_id = ?
                """,
                (chat_id,),
            ) as cursor:
                meta = await cursor.fetchone()

            current_summary = str(meta[0]) if meta and meta[0] else ""
            summary_message_id = int(meta[1]) if meta and meta[1] else 0
            async with db.execute(
                """
                SELECT id, role, user_name, text
                FROM chat_history
                WHERE chat_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (chat_id, summary_message_id, max_batch_size + keep_recent),
            ) as cursor:
                rows = await cursor.fetchall()

        eligible_count = min(max_batch_size, max(0, len(rows) - keep_recent))
        if eligible_count < min_batch_size:
            return None
        messages = tuple(
            SummarizableMessage(
                id=int(message_id),
                role=str(role),
                user_name=str(user_name),
                text=str(text),
            )
            for message_id, role, user_name, text in rows[:eligible_count]
            if text
        )
        if not messages:
            return None
        return SummaryBatch(current_summary=current_summary, messages=messages)

    async def save_summary(
        self,
        *,
        chat_id: int,
        summary: str,
        through_message_id: int,
    ) -> None:
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_meta (chat_id, summary, summary_message_id)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    summary = excluded.summary,
                    summary_message_id = excluded.summary_message_id
                WHERE chat_meta.summary_message_id < excluded.summary_message_id
                """,
                (chat_id, summary, through_message_id),
            )
            await db.commit()

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
