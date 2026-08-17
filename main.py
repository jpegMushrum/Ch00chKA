import asyncio
import logging
import shutil
from aiogram import Bot, Dispatcher
from config import config
from handlers import some_logic
from AI_module import database, core


def validate_runtime_dependencies():
    required_binaries = ("ffmpeg", "ffprobe", "node")
    missing = [name for name in required_binaries if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "Не найдены системные зависимости: "
            f"{', '.join(missing)}. Используйте Docker-образ или установите их в PATH."
        )

async def main():
    # Включаем логирование в консоль
    logging.basicConfig(level=logging.INFO)

    validate_runtime_dependencies()
    await database.init_talker_db()
    
    # Инициализируем бота, забирая токен в скрытом виде
    bot = Bot(token=config.bot_token.get_secret_value())
    dp = Dispatcher()

    # Подключаем групповой роутер к главному диспетчеру
    dp.include_router(some_logic.router)

    # Запуск Long Polling опроса. Бот начинает слушать сервера Telegram.
    print("Бот успешно запущен через Long Polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
