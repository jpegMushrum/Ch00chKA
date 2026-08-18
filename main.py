import asyncio
import logging
import shutil
from aiogram import Bot, Dispatcher
from ch00chka.bootstrap import build_application
from config import load_settings


def log_optional_runtime_dependencies() -> None:
    optional_binaries = ("ffmpeg", "ffprobe", "node")
    missing = [name for name in optional_binaries if shutil.which(name) is None]
    if missing:
        logging.warning(
            "Медиазагрузчики частично недоступны: в PATH отсутствуют %s",
            ", ".join(missing),
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    config = load_settings()
    log_optional_runtime_dependencies()
    application = build_application(config)
    await application.repository.initialize()

    async with Bot(token=config.bot_token.get_secret_value()) as bot:
        if config.telegram_drop_pending_updates:
            await bot.delete_webhook(drop_pending_updates=True)
            logging.info("Накопившиеся Telegram updates пропущены")

        bot_identity = await bot.get_me()
        await application.configure_bot_identity(
            bot_name=bot_identity.first_name,
            bot_username=bot_identity.username,
        )
        dp = Dispatcher()
        dp.include_router(application.router)

        print("Бот успешно запущен через Long Polling...")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
