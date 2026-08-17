from __future__ import annotations

import logging
from typing import Protocol

from aiogram import Bot, Router, types
from aiogram.enums import ChatMemberStatus, MessageEntityType
from aiogram.filters import Command

from ch00chka.application import MessageProcessor
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ActionType, NormalizedMessage

logger = logging.getLogger(__name__)


class MediaAdapter(Protocol):
    async def handle(self, message: types.Message) -> None: ...


class NoopMediaAdapter:
    async def handle(self, message: types.Message) -> None:
        return None


def _extract_urls(message: types.Message) -> tuple[str, ...]:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or ()
    urls: list[str] = []
    for entity in entities:
        if entity.type not in (MessageEntityType.URL, MessageEntityType.TEXT_LINK):
            continue
        url = entity.url or text[entity.offset : entity.offset + entity.length]
        if url:
            urls.append(url)
    return tuple(urls)


async def _is_chat_admin(message: types.Message, bot: Bot) -> bool:
    if not message.from_user:
        return False
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


def create_router(
    *,
    processor: MessageProcessor,
    repository: ConversationRepository,
    media: MediaAdapter | None = None,
) -> Router:
    router = Router(name="group_messages")
    media_adapter = media or NoopMediaAdapter()
    bot_identity: types.User | None = None

    @router.message(Command("help"))
    async def help_command(message: types.Message) -> None:
        await message.reply(
            "Привет! Я помощник этого чата.\n"
            "/set_personality [текст] — настроить личность (только администратор).\n"
            "/get_personality — показать текущую личность."
        )

    @router.message(Command("set_personality"))
    async def set_personality_command(message: types.Message, bot: Bot) -> None:
        if message.chat.type not in ("group", "supergroup"):
            return
        if not await _is_chat_admin(message, bot):
            await message.reply("Менять личность бота могут только администраторы чата.")
            return
        text = message.text or ""
        personality = text.partition(" ")[2].strip()
        if not personality:
            await message.reply("После команды нужно указать описание личности.")
            return
        await repository.set_personality(message.chat.id, personality)
        await message.reply("Личность чата обновлена.")

    @router.message(Command("get_personality"))
    async def get_personality_command(message: types.Message) -> None:
        personality = await repository.get_personality(message.chat.id)
        await message.reply(personality)

    @router.message()
    async def process_group_message(message: types.Message, bot: Bot) -> None:
        nonlocal bot_identity
        if message.chat.type not in ("group", "supergroup") or not message.from_user:
            return

        text = message.text or message.caption or ""
        if bot_identity is None:
            bot_identity = await bot.get_me()
        bot_user = bot_identity
        reply_from = message.reply_to_message.from_user if message.reply_to_message else None
        bot_username = bot_user.username or ""
        normalized = NormalizedMessage(
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_id=message.from_user.id,
            user_name=message.from_user.full_name,
            text=text,
            chat_type=message.chat.type,
            bot_name=bot_user.first_name,
            bot_username=bot_user.username,
            is_reply_to_bot=bool(reply_from and reply_from.id == bot_user.id),
            mentions_bot=bool(bot_username and f"@{bot_username.lower()}" in text.lower()),
            urls=_extract_urls(message),
        )

        result = None
        try:
            result = await processor.process(normalized)
            if result.reply_text:
                await message.reply(result.reply_text)
        except Exception:
            logger.exception("Failed to process message %s", message.message_id)
            if normalized.is_reply_to_bot or normalized.mentions_bot:
                await message.reply("Не получилось сформировать ответ. Попробуй ещё раз чуть позже.")

        has_media_action = bool(
            result
            and any(action.type is ActionType.MEDIA_DOWNLOAD for action in result.plan.actions)
        )
        if has_media_action:
            try:
                await media_adapter.handle(message)
            except Exception:
                logger.exception("Media adapter failed for message %s", message.message_id)
                await message.reply("Не получилось обработать ссылку на медиа.")

    return router
