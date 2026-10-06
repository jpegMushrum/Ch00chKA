from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types

from ch00chka.application.identities import ParticipantIdentityRecorder
from ch00chka.application.ports import ConversationRepository


logger = logging.getLogger(__name__)
_START_COMMAND = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s|$)", re.IGNORECASE)


def is_start_command(text: str) -> bool:
    return bool(_START_COMMAND.match(text.strip()))


class AdminStartGate(BaseMiddleware):
    """Keep each chat dormant until the configured admin starts it."""

    def __init__(self, admin_id: int | None) -> None:
        self._admin_id = admin_id
        self._active_chats: set[int] = set()
        if admin_id is not None:
            logger.info("Per-chat admin start gate enabled; waiting for /start")

    def is_active(self, chat_id: int) -> bool:
        return self._admin_id is None or chat_id in self._active_chats

    async def __call__(
        self,
        handler: Callable[[types.Message, dict[str, Any]], Awaitable[Any]],
        event: types.Message,
        data: dict[str, Any],
    ) -> Any:
        if self._admin_id is None:
            return await handler(event, data)

        text = event.text or event.caption or ""
        sender_id = event.from_user.id if event.from_user else None
        chat_id = event.chat.id
        if is_start_command(text):
            if sender_id != self._admin_id:
                logger.warning("Ignoring /start from unauthorized Telegram user")
                return None

            was_active = chat_id in self._active_chats
            self._active_chats.add(chat_id)
            logger.info("Admin start gate opened for chat %s", chat_id)
            await event.reply(
                "Чоочка уже запущена." if was_active else "Чоочка запущена."
            )
            return None

        if chat_id not in self._active_chats:
            return None
        return await handler(event, data)


class ParticipantTrackingMiddleware(BaseMiddleware):
    def __init__(
        self,
        repository: ConversationRepository,
        *,
        identity_recorder: ParticipantIdentityRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._identity_recorder = identity_recorder

    async def __call__(
        self,
        handler: Callable[[types.Message, dict[str, Any]], Awaitable[Any]],
        event: types.Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if event.chat.type in ("group", "supergroup") and user and not user.is_bot:
            try:
                await self._repository.upsert_participant(
                    chat_id=event.chat.id,
                    user_id=user.id,
                    display_name=user.full_name,
                    username=user.username,
                )
                reply_user = (
                    getattr(getattr(event, "reply_to_message", None), "from_user", None)
                )
                if reply_user and not reply_user.is_bot:
                    await self._repository.upsert_participant(
                        chat_id=event.chat.id,
                        user_id=reply_user.id,
                        display_name=reply_user.full_name,
                        username=reply_user.username,
                    )
                if self._identity_recorder:
                    await self._identity_recorder.observe(
                        chat_id=event.chat.id,
                        author_user_id=user.id,
                        text=event.text or event.caption or "",
                        reply_target_user_id=(
                            reply_user.id if reply_user and not reply_user.is_bot else None
                        ),
                    )
            except Exception:
                logger.exception(
                    "Could not update participant registry for chat %s",
                    event.chat.id,
                )
        return await handler(event, data)
