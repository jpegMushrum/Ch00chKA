from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import aiosqlite

    from ch00chka.infrastructure.sqlite_repository import SQLiteConversationRepository
except ModuleNotFoundError:
    aiosqlite = None
    SQLiteConversationRepository = None


@unittest.skipIf(aiosqlite is None, "aiosqlite is not installed in this test runtime")
class RepositoryAliasTests(unittest.IsolatedAsyncioTestCase):
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
