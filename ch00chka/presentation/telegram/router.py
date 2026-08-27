from __future__ import annotations

import logging
import re
import unicodedata
from typing import Protocol

from aiogram import Bot, Router, types
from aiogram.enums import ChatMemberStatus, MessageEntityType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command

from ch00chka.ai.aliases import BotAliasRegistry, LLMAliasGenerator, sanitize_aliases
from ch00chka.application import MessageProcessor
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ActionType, NormalizedMessage, ReferencedMessage
from ch00chka.presentation.telegram.access import AdminStartGate
from ch00chka.presentation.telegram.formatting import markdown_to_telegram_html

logger = logging.getLogger(__name__)


class MediaAdapter(Protocol):
    async def handle(self, message: types.Message, urls: tuple[str, ...]) -> None: ...


class NoopMediaAdapter:
    async def handle(self, message: types.Message, urls: tuple[str, ...]) -> None:
        return None


def _extract_urls(message: types.Message) -> tuple[str, ...]:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or ()
    urls: list[str] = []
    for entity in entities:
        if entity.type not in (MessageEntityType.URL, MessageEntityType.TEXT_LINK):
            continue
        label = entity.extract_from(text)
        if (
            entity.type == MessageEntityType.TEXT_LINK
            and _is_invisible_text(label)
        ):
            logger.info(
                "Ignoring a link attached only to invisible Telegram text"
            )
            continue
        url = entity.url or entity.extract_from(text)
        if url:
            urls.append(url)
    return tuple(urls)


def _is_invisible_text(value: str) -> bool:
    """Treat Unicode formatting anchors such as U+200B as invisible."""
    return not any(unicodedata.category(char)[0] in "LNPS" for char in value)


async def _is_chat_admin(message: types.Message, bot: Bot) -> bool:
    if not message.from_user:
        return False
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


def _parse_alias_seeds(text: str) -> tuple[str, ...]:
    payload = text.partition(" ")[2].strip()
    if not payload:
        return ()
    return sanitize_aliases(re.split(r"[,;\n]+", payload))


def _message_author(message: types.Message) -> str:
    if message.from_user:
        return message.from_user.full_name
    if message.sender_chat:
        return message.sender_chat.title
    return "Неизвестный участник"


def _reference_text(message: types.Message, *, limit: int = 2_000) -> str:
    text = (message.text or message.caption or "").strip()
    if not text:
        text = "[сообщение без текста]"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _reply_actor_text(message: types.Message, text: str) -> None:
    rendered = markdown_to_telegram_html(text)
    try:
        await message.reply(rendered, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        logger.warning(
            "Telegram rejected formatted actor response for message %s; using plain text",
            message.message_id,
        )
        await message.reply(text)


def create_router(
    *,
    processor: MessageProcessor,
    repository: ConversationRepository,
    alias_registry: BotAliasRegistry,
    alias_generator: LLMAliasGenerator,
    media: MediaAdapter | None = None,
    admin_id: int | None = None,
) -> Router:
    router = Router(name="group_messages")
    router.message.outer_middleware(AdminStartGate(admin_id))
    media_adapter = media or NoopMediaAdapter()
    bot_identity: types.User | None = None

    @router.message(Command("help"))
    async def help_command(message: types.Message) -> None:
        await message.reply(
            "Привет! Я помощник этого чата.\n"
            "/set_personality [текст] — настроить личность (только администратор).\n"
            "/get_personality — показать текущую личность.\n"
            "/generate_aliases чучка, чуч — задать обращения и создать производные.\n"
            "/get_aliases — показать обращения этого чата."
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

    @router.message(Command("generate_aliases"))
    async def generate_aliases_command(message: types.Message, bot: Bot) -> None:
        if message.chat.type not in ("group", "supergroup"):
            return
        if not await _is_chat_admin(message, bot):
            await message.reply("Менять обращения могут только администраторы чата.")
            return

        seeds = _parse_alias_seeds(message.text or "")
        if not seeds:
            await message.reply(
                "Укажи исходные обращения через запятую, например: "
                "/generate_aliases чучка, чуч, choochka"
            )
            return

        bot_user = await bot.get_me()
        generated = seeds
        generation_failed = False
        try:
            generated = await alias_generator.generate(
                bot_name=bot_user.first_name,
                bot_username=bot_user.username,
                seed_aliases=seeds,
            )
        except Exception:
            generation_failed = True
            logger.exception("Could not generate aliases for chat %s", message.chat.id)

        await repository.set_aliases(message.chat.id, generated)
        suffix = (
            "\nПроизводные создать не удалось, поэтому сохранил исходные варианты."
            if generation_failed
            else ""
        )
        await message.reply("Обращения сохранены: " + ", ".join(generated) + suffix)

    @router.message(Command("get_aliases"))
    async def get_aliases_command(message: types.Message) -> None:
        aliases = await repository.get_aliases(message.chat.id)
        await message.reply(
            "Обращения этого чата: " + (", ".join(aliases) if aliases else "не заданы")
        )

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
        replied_message = message.reply_to_message
        bot_username = bot_user.username or ""
        chat_aliases = await repository.get_aliases(message.chat.id)
        message_aliases = BotAliasRegistry((*alias_registry.aliases, *chat_aliases))
        known_aliases = message_aliases.aliases
        normalized = NormalizedMessage(
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_id=message.from_user.id,
            user_name=message.from_user.full_name,
            text=text,
            chat_type=message.chat.type,
            bot_name=bot_user.first_name,
            bot_username=bot_user.username,
            bot_aliases=known_aliases,
            is_reply_to_bot=bool(reply_from and reply_from.id == bot_user.id),
            mentions_bot=bool(
                (bot_username and f"@{bot_username.lower()}" in text.lower())
                or message_aliases.matches(text)
            ),
            urls=_extract_urls(message),
            reply_to_message=(
                ReferencedMessage(
                    message_id=replied_message.message_id,
                    user_name=_message_author(replied_message),
                    text=_reference_text(replied_message),
                    is_bot=bool(reply_from and reply_from.is_bot),
                )
                if replied_message
                else None
            ),
            quoted_text=(
                message.quote.text[:1_000]
                if message.quote and message.quote.text
                else None
            ),
        )

        result = None
        try:
            result = await processor.process(normalized)
            if result.reply_text:
                await _reply_actor_text(message, result.reply_text)
        except Exception:
            logger.exception("Failed to process message %s", message.message_id)
            if normalized.is_reply_to_bot or normalized.mentions_bot:
                await message.reply("Не получилось сформировать ответ. Попробуй ещё раз чуть позже.")

        media_urls = tuple(
            str(action.payload["url"])
            for action in (result.plan.actions if result else ())
            if action.type is ActionType.MEDIA_DOWNLOAD and action.payload.get("url")
        )
        if media_urls:
            try:
                await media_adapter.handle(message, media_urls)
            except Exception:
                logger.exception("Media adapter failed for message %s", message.message_id)
                await message.reply("Не получилось обработать ссылку на медиа.")

    return router
