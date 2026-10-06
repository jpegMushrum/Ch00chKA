from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import aiosqlite

from ch00chka.domain import (
    ChatMessage,
    ChatParticipant,
    ParticipantAlias,
    ParticipantMemory,
    SummarizableMessage,
    SummaryBatch,
    normalize_participant_alias,
    participant_identity_aliases,
    unique_aliases,
)


def _decode_facts(raw_facts: object) -> tuple[str, ...]:
    if not raw_facts:
        return ()
    try:
        value = json.loads(str(raw_facts))
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return _normalize_facts(value)


def _normalize_facts(values: Sequence[object]) -> tuple[str, ...]:
    facts: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        fact = " ".join(value.split())[:320]
        key = fact.casefold()
        if fact and key not in seen:
            seen.add(key)
            facts.append(fact)
        if len(facts) == 12:
            break
    return tuple(facts)


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
                    text TEXT NOT NULL,
                    user_id INTEGER
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_participants (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    username TEXT,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS participant_memories (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    facts TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS participant_aliases (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    alias_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, user_id, alias_key)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_features (
                    chat_id INTEGER NOT NULL,
                    feature TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, feature)
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
            async with db.execute("PRAGMA table_info(chat_history)") as cursor:
                history_columns = {str(row[1]) for row in await cursor.fetchall()}
            if "user_id" not in history_columns:
                await db.execute("ALTER TABLE chat_history ADD COLUMN user_id INTEGER")
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id_id
                ON chat_history(chat_id, id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_participants_chat_last_seen
                ON chat_participants(chat_id, last_seen DESC)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_participant_memories_chat_user
                ON participant_memories(chat_id, user_id)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_participant_aliases_chat_key
                ON participant_aliases(chat_id, alias_key, confidence DESC)
                """
            )
            async with db.execute(
                """
                SELECT chat_id, user_id, display_name, username
                FROM chat_participants
                """
            ) as cursor:
                existing_participants = await cursor.fetchall()
            for chat_id, user_id, display_name, username in existing_participants:
                await self._save_participant_aliases(
                    db,
                    chat_id=int(chat_id),
                    user_id=int(user_id),
                    aliases=participant_identity_aliases(
                        str(display_name),
                        str(username) if username else None,
                    ),
                    source="telegram",
                    confidence=100,
                )
            await db.commit()

    async def get_feature_overrides(self, chat_id: int) -> dict[str, bool]:
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT feature, enabled
                FROM chat_features
                WHERE chat_id = ?
                """,
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return {str(feature): bool(enabled) for feature, enabled in rows}

    async def toggle_feature(
        self,
        *,
        chat_id: int,
        feature: str,
        default_enabled: bool,
    ) -> bool:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT enabled
                FROM chat_features
                WHERE chat_id = ? AND feature = ?
                """,
                (chat_id, feature),
            ) as cursor:
                row = await cursor.fetchone()
            current = bool(row[0]) if row else default_enabled
            enabled = not current
            await db.execute(
                """
                INSERT INTO chat_features (chat_id, feature, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, feature) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (chat_id, feature, int(enabled), time.time()),
            )
            await db.commit()
        return enabled

    async def upsert_participant(
        self,
        *,
        chat_id: int,
        user_id: int,
        display_name: str,
        username: str | None,
    ) -> None:
        normalized_name = display_name.strip() or f"User {user_id}"
        normalized_username = username.strip() if username else None
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                INSERT INTO chat_participants (
                    chat_id, user_id, display_name, username, last_seen
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (
                    chat_id,
                    user_id,
                    normalized_name,
                    normalized_username,
                    time.time(),
                ),
            )
            await self._save_participant_aliases(
                db,
                chat_id=chat_id,
                user_id=user_id,
                aliases=participant_identity_aliases(
                    normalized_name,
                    normalized_username,
                ),
                source="telegram",
                confidence=100,
            )
            await db.commit()

    async def list_participants(self, chat_id: int) -> Sequence[ChatParticipant]:
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT user_id, display_name, username
                FROM chat_participants
                WHERE chat_id = ?
                ORDER BY last_seen DESC, user_id ASC
                """,
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(
            ChatParticipant(
                user_id=int(user_id),
                display_name=str(display_name),
                username=str(username) if username else None,
            )
            for user_id, display_name, username in rows
        )

    async def list_participant_aliases(
        self,
        chat_id: int,
    ) -> Sequence[ParticipantAlias]:
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT user_id, alias, source, confidence
                FROM participant_aliases
                WHERE chat_id = ?
                ORDER BY confidence DESC, updated_at DESC, alias_key ASC, user_id ASC
                """,
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(
            ParticipantAlias(
                user_id=int(user_id),
                alias=str(alias),
                source=str(source),
                confidence=int(confidence),
            )
            for user_id, alias, source, confidence in rows
        )

    async def save_participant_aliases(
        self,
        *,
        chat_id: int,
        user_id: int,
        aliases: Sequence[str],
        source: str,
        confidence: int,
    ) -> None:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._save_participant_aliases(
                db,
                chat_id=chat_id,
                user_id=user_id,
                aliases=aliases,
                source=source,
                confidence=confidence,
            )
            await db.commit()

    @staticmethod
    async def _save_participant_aliases(
        db: aiosqlite.Connection,
        *,
        chat_id: int,
        user_id: int,
        aliases: Sequence[str],
        source: str,
        confidence: int,
    ) -> None:
        normalized_source = source.strip()[:32] or "manual"
        normalized_confidence = max(0, min(int(confidence), 100))
        for alias in unique_aliases(aliases):
            alias_key = normalize_participant_alias(alias)
            if not alias_key:
                continue
            await db.execute(
                """
                INSERT INTO participant_aliases (
                    chat_id, user_id, alias, alias_key, source, confidence, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id, alias_key) DO UPDATE SET
                    alias = excluded.alias,
                    source = CASE
                        WHEN excluded.confidence >= participant_aliases.confidence
                        THEN excluded.source ELSE participant_aliases.source
                    END,
                    confidence = MAX(participant_aliases.confidence, excluded.confidence),
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    user_id,
                    alias,
                    alias_key,
                    normalized_source,
                    normalized_confidence,
                    time.time(),
                ),
            )

    async def get_participant_memories(
        self,
        *,
        chat_id: int,
        user_ids: Sequence[int],
    ) -> Sequence[ParticipantMemory]:
        ordered_ids = tuple(dict.fromkeys(int(user_id) for user_id in user_ids))
        if not ordered_ids:
            return ()
        placeholders = ", ".join("?" for _ in ordered_ids)
        async with self._connection() as db:
            async with db.execute(
                f"""
                SELECT p.user_id, p.display_name, p.username, m.facts
                FROM chat_participants AS p
                LEFT JOIN participant_memories AS m
                    ON m.chat_id = p.chat_id AND m.user_id = p.user_id
                WHERE p.chat_id = ? AND p.user_id IN ({placeholders})
                """,
                (chat_id, *ordered_ids),
            ) as cursor:
                rows = await cursor.fetchall()

        aliases = await self.list_participant_aliases(chat_id)
        aliases_by_user: dict[int, list[str]] = {}
        requested_set = set(ordered_ids)
        for alias in aliases:
            if alias.user_id in requested_set:
                aliases_by_user.setdefault(alias.user_id, []).append(alias.alias)

        by_id: dict[int, ParticipantMemory] = {}
        for user_id, display_name, username, raw_facts in rows:
            facts = _decode_facts(raw_facts)
            participant_aliases = tuple(aliases_by_user.get(int(user_id), ()))
            if not facts and not participant_aliases:
                continue
            by_id[int(user_id)] = ParticipantMemory(
                user_id=int(user_id),
                display_name=str(display_name),
                username=str(username) if username else None,
                facts=facts,
                aliases=participant_aliases,
            )
        return tuple(by_id[user_id] for user_id in ordered_ids if user_id in by_id)

    async def save_participant_memories(
        self,
        *,
        chat_id: int,
        memories: Sequence[ParticipantMemory],
    ) -> None:
        if not memories:
            return
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            for memory in memories:
                facts = _normalize_facts(memory.facts)
                if not facts:
                    await db.execute(
                        "DELETE FROM participant_memories WHERE chat_id = ? AND user_id = ?",
                        (chat_id, memory.user_id),
                    )
                    continue
                await db.execute(
                    """
                    INSERT INTO participant_memories (chat_id, user_id, facts, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        facts = excluded.facts,
                        updated_at = excluded.updated_at
                    """,
                    (
                        chat_id,
                        memory.user_id,
                        json.dumps(facts, ensure_ascii=False),
                        time.time(),
                    ),
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
        user_id: int | None = None,
    ) -> None:
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_history (chat_id, role, user_name, text, user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, role, user_name, text, user_id),
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
                SELECT role, user_name, text, user_id
                FROM chat_history
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        return tuple(
            ChatMessage(
                role=str(role),
                user_name=str(user_name),
                text=str(text),
                user_id=int(user_id) if user_id is not None else None,
            )
            for role, user_name, text, user_id in reversed(rows)
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
                SELECT id, role, user_name, text, user_id
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
                user_id=int(user_id) if user_id is not None else None,
            )
            for message_id, role, user_name, text, user_id in rows[:eligible_count]
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
