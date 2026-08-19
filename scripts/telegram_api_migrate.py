from __future__ import annotations

import argparse
import asyncio

from aiogram import Bot

from config import load_settings


async def log_out_from_cloud() -> None:
    settings = load_settings()
    async with Bot(token=settings.bot_token.get_secret_value()) as bot:
        await bot.log_out()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log the bot out from Telegram's cloud Bot API before migration.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the cloud logOut operation.",
    )
    args = parser.parse_args()
    if not args.yes:
        parser.error("logOut changes bot routing; repeat with --yes to confirm")
    asyncio.run(log_out_from_cloud())


if __name__ == "__main__":
    main()
