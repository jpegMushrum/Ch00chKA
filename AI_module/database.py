import os
import aiosqlite

AI_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.getenv(
    "DB_PATH",
    os.path.join(AI_MODULE_DIR, "bot_talker_memory.db"),
)

async def init_talker_db():
    db_dir = os.path.dirname(os.path.abspath(DB_NAME))
    os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB_NAME) as db:
        #Окно
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                user_name TEXT,
                text TEXT
            )
        ''')
        # Таблица для саммари и таймеров активности
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_meta (
                chat_id INTEGER PRIMARY KEY,
                summary TEXT DEFAULT '',
                personality TEXT DEFAULT 'Отвечай коротко, непринужденно, используй разговорный русский язык, шути, если уместно. Не будь душным роботом.',
                last_message_time REAL
            )
        ''')
        await db.commit()
