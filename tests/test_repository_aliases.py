from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import aiosqlite

    from ch00chka.domain import ParticipantMemory
    from ch00chka.infrastructure.sqlite_repository import SQLiteConversationRepository
except ModuleNotFoundError:
    aiosqlite = None
    ParticipantMemory = None
    SQLiteConversationRepository = None


@unittest.skipIf(aiosqlite is None, "aiosqlite is not installed in this test runtime")
class RepositoryAliasTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_toggles_are_persisted_and_isolated_by_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteConversationRepository(
                db_path=str(Path(directory) / "memory.db"),
                default_personality="По умолчанию",
            )
            await repository.initialize()

            enabled = await repository.toggle_feature(
                chat_id=100,
                feature="research",
                default_enabled=True,
            )

            self.assertFalse(enabled)
            self.assertEqual(
                await repository.get_feature_overrides(100),
                {"research": False},
            )
            self.assertEqual(await repository.get_feature_overrides(200), {})

            enabled = await repository.toggle_feature(
                chat_id=100,
                feature="research",
                default_enabled=True,
            )
            self.assertTrue(enabled)

    async def test_participants_are_updated_and_isolated_by_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteConversationRepository(
                db_path=str(Path(directory) / "memory.db"),
                default_personality="По умолчанию",
            )
            await repository.initialize()
            await repository.upsert_participant(
                chat_id=100,
                user_id=1,
                display_name="Alice",
                username="alice",
            )
            await repository.upsert_participant(
                chat_id=200,
                user_id=2,
                display_name="Bob",
                username="bob",
            )
            await repository.upsert_participant(
                chat_id=100,
                user_id=1,
                display_name="Alice Updated",
                username=None,
            )

            chat_100 = await repository.list_participants(100)
            chat_200 = await repository.list_participants(200)

            self.assertEqual(len(chat_100), 1)
            self.assertEqual(chat_100[0].user_id, 1)
            self.assertEqual(chat_100[0].display_name, "Alice Updated")
            self.assertIsNone(chat_100[0].username)
            self.assertEqual(len(chat_200), 1)
            self.assertEqual(chat_200[0].user_id, 2)

            aliases = await repository.list_participant_aliases(100)
            self.assertEqual(
                {(alias.user_id, alias.alias) for alias in aliases},
                {(1, "Alice"), (1, "Alice Updated")},
            )

    async def test_participant_aliases_are_scoped_and_can_be_added_from_chat_context(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteConversationRepository(
                db_path=str(Path(directory) / "memory.db"),
                default_personality="По умолчанию",
            )
            await repository.initialize()
            await repository.upsert_participant(
                chat_id=100,
                user_id=1,
                display_name="vassago",
                username="vassago",
            )
            await repository.save_participant_aliases(
                chat_id=100,
                user_id=1,
                aliases=("Рома",),
                source="chat_relation",
                confidence=75,
            )
            await repository.save_participant_aliases(
                chat_id=200,
                user_id=1,
                aliases=("Другой Рома",),
                source="self_intro",
                confidence=95,
            )

            aliases = await repository.list_participant_aliases(100)
            other_aliases = await repository.list_participant_aliases(200)

            self.assertIn(("Рома", "chat_relation", 75), {
                (alias.alias, alias.source, alias.confidence) for alias in aliases
            })
            self.assertNotIn("Другой Рома", {alias.alias for alias in aliases})
            self.assertIn("Другой Рома", {alias.alias for alias in other_aliases})

    async def test_participant_memories_are_separate_and_scoped_to_the_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteConversationRepository(
                db_path=str(Path(directory) / "memory.db"),
                default_personality="По умолчанию",
            )
            await repository.initialize()
            await repository.upsert_participant(
                chat_id=100,
                user_id=1,
                display_name="Alice",
                username="alice",
            )
            await repository.upsert_participant(
                chat_id=100,
                user_id=2,
                display_name="Bob",
                username="bob",
            )
            await repository.upsert_participant(
                chat_id=200,
                user_id=1,
                display_name="Alice elsewhere",
                username="alice",
            )
            await repository.save_participant_memories(
                chat_id=100,
                memories=(
                    ParticipantMemory(1, "Alice", "alice", ("Любит моды.",)),
                    ParticipantMemory(2, "Bob", "bob", ("Работает дизайнером.",)),
                ),
            )

            memories = await repository.get_participant_memories(
                chat_id=100,
                user_ids=(2, 1),
            )
            other_chat = await repository.get_participant_memories(
                chat_id=200,
                user_ids=(1,),
            )

            self.assertEqual([memory.user_id for memory in memories], [2, 1])
            self.assertEqual(memories[0].facts, ("Работает дизайнером.",))
            self.assertEqual([memory.user_id for memory in other_chat], [1])
            self.assertEqual(other_chat[0].facts, ())
            self.assertIn("Alice elsewhere", other_chat[0].aliases)

    async def test_existing_chat_meta_table_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "memory.db")
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE chat_meta (
                        chat_id INTEGER PRIMARY KEY,
                        summary TEXT DEFAULT '',
                        personality TEXT DEFAULT '',
                        last_message_time REAL
                    )
                    """
                )
                await db.execute(
                    "INSERT INTO chat_meta (chat_id, personality) VALUES (1, 'Резкая')"
                )
                await db.commit()

            repository = SQLiteConversationRepository(
                db_path=db_path,
                default_personality="По умолчанию",
            )
            await repository.initialize()
            await repository.set_aliases(1, ("чучка", "чуч"))

            self.assertEqual(await repository.get_aliases(1), ("чучка", "чуч"))
            self.assertEqual(await repository.get_personality(1), "Резкая")

            async with aiosqlite.connect(db_path) as db:
                async with db.execute("PRAGMA table_info(chat_meta)") as cursor:
                    columns = {str(row[1]) for row in await cursor.fetchall()}
            self.assertIn("summary_message_id", columns)

    async def test_summary_batch_keeps_recent_messages_and_advances_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteConversationRepository(
                db_path=str(Path(directory) / "memory.db"),
                default_personality="По умолчанию",
            )
            await repository.initialize()
            for number in range(1, 13):
                await repository.add_message(
                    chat_id=7,
                    role="user",
                    user_name="Alice",
                    text=f"Сообщение {number}",
                )

            batch = await repository.get_summary_batch(
                chat_id=7,
                keep_recent=5,
                min_batch_size=3,
                max_batch_size=4,
            )

            self.assertIsNotNone(batch)
            self.assertEqual(
                [message.text for message in batch.messages],
                [f"Сообщение {number}" for number in range(1, 5)],
            )
            await repository.save_summary(
                chat_id=7,
                summary="Alice написала первые четыре сообщения.",
                through_message_id=batch.through_message_id,
            )

            next_batch = await repository.get_summary_batch(
                chat_id=7,
                keep_recent=5,
                min_batch_size=3,
                max_batch_size=4,
            )
            self.assertIsNotNone(next_batch)
            self.assertEqual(next_batch.current_summary, "Alice написала первые четыре сообщения.")
            self.assertEqual(next_batch.messages[0].text, "Сообщение 5")


if __name__ == "__main__":
    unittest.main()
