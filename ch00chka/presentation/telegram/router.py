from __future__ import annotations

import html
import logging
import re
import unicodedata
from typing import Protocol

from aiogram import Bot, F, Router, types
from aiogram.enums import ChatMemberStatus, MessageEntityType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ch00chka.ai.aliases import BotAliasRegistry, LLMAliasGenerator, sanitize_aliases
from ch00chka.application import ChatFeatureService, MessageProcessor
from ch00chka.application.identities import ParticipantIdentityRecorder
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import (
    ActionType,
    ChatFeature,
    ChatParticipant,
    MediaDeliveryResult,
    NormalizedMessage,
    ReferencedMessage,
)
from ch00chka.integrations.media_urls import MediaPlatform, detect_media_platform
from ch00chka.presentation.telegram.access import (
    AdminStartGate,
    ParticipantTrackingMiddleware,
)
from ch00chka.presentation.telegram.formatting import markdown_to_telegram_html

logger = logging.getLogger(__name__)

_FEATURE_CALLBACK_PREFIX = "feature:"
_FEATURE_LABELS: tuple[tuple[ChatFeature, str], ...] = (
    (ChatFeature.AI_RESPONSES, "Ответы ИИ"),
    (ChatFeature.MEMORY, "Память чата"),
    (ChatFeature.RESEARCH, "Поиск в интернете"),
    (ChatFeature.OBSERVER, "Проверка ответов"),
    (ChatFeature.YOUTUBE, "YouTube"),
    (ChatFeature.TIKTOK, "TikTok"),
    (ChatFeature.INSTAGRAM, "Instagram"),
    (ChatFeature.MENTIONS, "/all"),
    (ChatFeature.DELETE_SOURCE_LINKS, "Удалять ссылки"),
)
_MEDIA_FEATURES = {
    MediaPlatform.YOUTUBE: ChatFeature.YOUTUBE,
    MediaPlatform.TIKTOK: ChatFeature.TIKTOK,
    MediaPlatform.INSTAGRAM: ChatFeature.INSTAGRAM,
}


class MediaAdapter(Protocol):
    async def handle(
        self,
        message: types.Message,
        urls: tuple[str, ...],
        *,
        reply_to_source: bool = True,
    ) -> MediaDeliveryResult: ...


class NoopMediaAdapter:
    async def handle(
        self,
        message: types.Message,
        urls: tuple[str, ...],
        *,
        reply_to_source: bool = True,
    ) -> MediaDeliveryResult:
        return MediaDeliveryResult(requested_urls=urls, delivered_urls=())


def _features_keyboard(states: dict[ChatFeature, bool]):
    builder = InlineKeyboardBuilder()
    for feature, label in _FEATURE_LABELS:
        enabled = states[feature]
        builder.button(
            text=f"{label}: {'ON' if enabled else 'OFF'}",
            callback_data=f"{_FEATURE_CALLBACK_PREFIX}{feature.value}",
        )
    builder.adjust(2)
    return builder.as_markup()


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


def _participant_mention_chunks(
    participants: tuple[ChatParticipant, ...],
    *,
    max_length: int = 3_500,
) -> tuple[str, ...]:
    chunks: list[str] = []
    current = "📣 "
    for participant in participants:
        label = html.escape(participant.display_name[:80], quote=False)
        mention = f'<a href="tg://user?id={participant.user_id}">{label}</a>'
        candidate = current + (" " if current != "📣 " else "") + mention
        if len(candidate) > max_length and current != "📣 ":
            chunks.append(current)
            current = "📣 " + mention
        else:
            current = candidate
    if current != "📣 ":
        chunks.append(current)
    return tuple(chunks)


def _participant_from_add_command(message: types.Message) -> ChatParticipant | None:
    replied_user = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else None
    )
    if replied_user and not replied_user.is_bot:
        return ChatParticipant(
            user_id=replied_user.id,
            display_name=replied_user.full_name,
            username=replied_user.username,
        )

    text = message.text or ""
    for entity in message.entities or ():
        if entity.type != MessageEntityType.TEXT_MENTION:
            continue
        mentioned_user = entity.user
        if mentioned_user and not mentioned_user.is_bot:
            return ChatParticipant(
                user_id=mentioned_user.id,
                display_name=mentioned_user.full_name,
                username=mentioned_user.username,
            )

    payload = text.partition(" ")[2].strip()
    match = re.fullmatch(r"(\d+)(?:\s+(.+))?", payload, re.DOTALL)
    if not match:
        return None
    user_id = int(match.group(1))
    if user_id <= 0:
        return None
    supplied_name = (match.group(2) or "").strip()
    display_name = supplied_name or f"User {user_id}"
    username = (
        display_name[1:]
        if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", display_name)
        else None
    )
    return ChatParticipant(
        user_id=user_id,
        display_name=display_name,
        username=username,
    )


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


async def _reply_actor_text(
    message: types.Message,
    text: str,
    *,
    reply_to_source: bool = True,
) -> None:
    rendered = markdown_to_telegram_html(text)
    send = message.reply if reply_to_source else message.answer
    try:
        await send(rendered, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        logger.warning(
            "Telegram rejected formatted actor response for message %s; using plain text",
            message.message_id,
        )
        await send(text)


async def _delete_source_message(message: types.Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError as exc:
        logger.warning(
            "Could not delete source media message %s in chat %s: %s",
            message.message_id,
            message.chat.id,
            exc,
        )
    except Exception:
        logger.exception(
            "Unexpected error deleting source media message %s in chat %s",
            message.message_id,
            message.chat.id,
        )


def _will_delete_source_message(
    feature_states: dict[ChatFeature, bool],
    *,
    media_urls: tuple[str, ...],
    supported_media_urls: tuple[str, ...],
) -> bool:
    """Decide before sending, because Telegram replies cannot be detached later."""
    return bool(
        media_urls
        and feature_states[ChatFeature.DELETE_SOURCE_LINKS]
        and len(media_urls) == len(supported_media_urls)
    )


def create_router(
    *,
    processor: MessageProcessor,
    repository: ConversationRepository,
    alias_registry: BotAliasRegistry,
    alias_generator: LLMAliasGenerator,
    feature_service: ChatFeatureService,
    media: MediaAdapter | None = None,
    admin_id: int | None = None,
) -> Router:
    router = Router(name="group_messages")
    start_gate = AdminStartGate(admin_id)
    router.message.outer_middleware(start_gate)
    router.message.middleware(
        ParticipantTrackingMiddleware(
            repository,
            identity_recorder=ParticipantIdentityRecorder(repository=repository),
        )
    )
    media_adapter = media or NoopMediaAdapter()
    bot_identity: types.User | None = None

    @router.message(Command("help"))
    async def help_command(message: types.Message) -> None:
        await message.reply(
            "Привет! Я помощник этого чата.\n"
            "/set_personality [текст] — настроить личность (только администратор).\n"
            "/get_personality — показать текущую личность.\n"
            "/generate_aliases чучка, чуч — задать обращения и создать производные.\n"
            "/get_aliases — показать обращения этого чата.\n"
            "/features — включить или выключить функции чата.\n"
            "/add_mention — добавить участника в /all (только администратор).\n"
            "/all — позвать замеченных участников чата (только администратор)."
        )

    @router.message(Command("features"))
    async def features_command(message: types.Message) -> None:
        if message.chat.type not in ("group", "supergroup"):
            return
        states = await feature_service.states(message.chat.id)
        await message.reply(
            "Функции этого чата. Переключать может любой участник:",
            reply_markup=_features_keyboard(states),
        )

    @router.callback_query(F.data.startswith(_FEATURE_CALLBACK_PREFIX))
    async def feature_callback(callback: types.CallbackQuery) -> None:
        message = callback.message
        if not message or message.chat.type not in ("group", "supergroup"):
            await callback.answer("Настройки доступны только в групповом чате.")
            return
        if not start_gate.is_active(message.chat.id):
            await callback.answer(
                "Сначала администратор должен активировать чат через /start.",
                show_alert=True,
            )
            return

        value = (callback.data or "").removeprefix(_FEATURE_CALLBACK_PREFIX)
        try:
            feature = ChatFeature(value)
        except ValueError:
            await callback.answer("Неизвестная функция.", show_alert=True)
            return

        enabled = await feature_service.toggle(message.chat.id, feature)
        states = await feature_service.states(message.chat.id)
        try:
            await message.edit_reply_markup(reply_markup=_features_keyboard(states))
        except TelegramBadRequest:
            logger.debug(
                "Feature panel was already refreshed for chat %s",
                message.chat.id,
            )
        label = dict(_FEATURE_LABELS)[feature]
        await callback.answer(f"{label}: {'ON' if enabled else 'OFF'}")

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

    @router.message(Command("all"))
    async def mention_all_command(message: types.Message, bot: Bot) -> None:
        if message.chat.type not in ("group", "supergroup") or not message.from_user:
            return
        states = await feature_service.states(message.chat.id)
        if not states[ChatFeature.MENTIONS]:
            await message.reply("Функция /all сейчас выключена в /features.")
            return
        if admin_id is not None:
            if message.from_user.id != admin_id:
                await message.reply("Команда /all доступна только администратору бота.")
                return
        elif not await _is_chat_admin(message, bot):
            await message.reply("Команда /all доступна только администраторам чата.")
            return

        participants = tuple(await repository.list_participants(message.chat.id))
        chunks = _participant_mention_chunks(participants)
        if not chunks:
            await message.reply("Пока некого звать: я ещё не видела участников чата.")
            return
        for chunk in chunks:
            await message.reply(chunk, parse_mode=ParseMode.HTML)

    @router.message(Command("add_mention"))
    async def add_mention_command(message: types.Message, bot: Bot) -> None:
        if message.chat.type not in ("group", "supergroup") or not message.from_user:
            return
        if admin_id is not None:
            if message.from_user.id != admin_id:
                await message.reply(
                    "Команда /add_mention доступна только администратору бота."
                )
                return
        elif not await _is_chat_admin(message, bot):
            await message.reply(
                "Команда /add_mention доступна только администраторам чата."
            )
            return

        participant = _participant_from_add_command(message)
        if participant is None:
            await message.reply(
                "Ответь командой /add_mention на сообщение человека, выбери его "
                "кликабельным упоминанием или напиши: /add_mention 123456789 Имя. "
                "Обычного @username недостаточно — Telegram не сообщает по нему ID."
            )
            return

        await repository.upsert_participant(
            chat_id=message.chat.id,
            user_id=participant.user_id,
            display_name=participant.display_name,
            username=participant.username,
        )
        mention = _participant_mention_chunks((participant,))[0].removeprefix("📣 ")
        await message.reply(
            f"Добавила {mention} в /all этого чата.",
            parse_mode=ParseMode.HTML,
        )

    @router.message()
    async def process_group_message(message: types.Message, bot: Bot) -> None:
        nonlocal bot_identity
        if message.chat.type not in ("group", "supergroup") or not message.from_user:
            return

        text = message.text or message.caption or ""
        feature_states = await feature_service.states(message.chat.id)
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
                    user_id=(
                        reply_from.id if reply_from and not reply_from.is_bot else None
                    ),
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
            result = await processor.process(
                normalized,
                options=feature_service.processing_options(feature_states),
            )
        except Exception:
            logger.exception("Failed to process message %s", message.message_id)
            if normalized.is_reply_to_bot or normalized.mentions_bot:
                await message.reply("Не получилось сформировать ответ. Попробуй ещё раз чуть позже.")

        supported_media_urls = tuple(
            str(action.payload["url"])
            for action in (result.plan.actions if result else ())
            if action.type is ActionType.MEDIA_DOWNLOAD
            and action.payload.get("url")
            and (
                (platform := detect_media_platform(str(action.payload["url"])))
                is not None
            )
        )
        media_urls = tuple(
            url
            for url in supported_media_urls
            if (platform := detect_media_platform(url)) is not None
            and feature_states[_MEDIA_FEATURES[platform]]
        )
        delete_source_when_delivered = _will_delete_source_message(
            feature_states,
            media_urls=media_urls,
            supported_media_urls=supported_media_urls,
        )
        if result and result.reply_text:
            await _reply_actor_text(
                message,
                result.reply_text,
                reply_to_source=not delete_source_when_delivered,
            )
        if media_urls:
            try:
                delivery = await media_adapter.handle(
                    message,
                    media_urls,
                    reply_to_source=not delete_source_when_delivered,
                )
                if delete_source_when_delivered and delivery.all_delivered:
                    await _delete_source_message(message)
            except Exception:
                logger.exception("Media adapter failed for message %s", message.message_id)
                await message.reply("Не получилось обработать ссылку на медиа.")

    return router
