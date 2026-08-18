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


if __name__ == "__main__":
    unittest.main()
