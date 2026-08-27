from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.prompts import SUMMARY_SYSTEM_PROMPT
from ch00chka.application.ports import ConversationRepository


logger = logging.getLogger(__name__)


class LLMAgentConversationMemory:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        model: str,
        repository: ConversationRepository,
        keep_recent: int,
        min_batch_size: int,
        max_batch_size: int,
        max_chars: int,
        retry_cooldown_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._repository = repository
        self._keep_recent = keep_recent
        self._min_batch_size = min_batch_size
        self._max_batch_size = max_batch_size
        self._max_chars = max_chars
        self._retry_cooldown_seconds = retry_cooldown_seconds
        self._clock = clock
        self._locks: dict[int, asyncio.Lock] = {}
        self._retry_after: dict[int, float] = {}

    async def refresh(self, chat_id: int) -> bool:
        if self._retry_after.get(chat_id, 0.0) > self._clock():
            return False
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if self._retry_after.get(chat_id, 0.0) > self._clock():
                return False
            batch = await self._repository.get_summary_batch(
                chat_id=chat_id,
                keep_recent=self._keep_recent,
                min_batch_size=self._min_batch_size,
                max_batch_size=self._max_batch_size,
            )
            if batch is None:
                self._retry_after.pop(chat_id, None)
                return False

            payload = {
                "max_summary_characters": self._max_chars,
                "existing_summary": batch.current_summary,
                "new_messages": [
                    {
                        "role": message.role,
                        "author": message.user_name,
                        "text": message.text,
                    }
                    for message in batch.messages
                ],
            }
            try:
                completion = await self._gateway.complete(
                    messages=(
                        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ),
                    model=self._model,
                    temperature=0.2,
                    max_tokens=2000,
                )
            except Exception:
                self._defer_retry(chat_id)
                raise
            if completion.incomplete:
                self._defer_retry(chat_id)
                logger.warning(
                    "Summary update was incomplete for chat %s: finish_reason=%s; "
                    "cursor retained, retry deferred for %.0f seconds",
                    chat_id,
                    completion.finish_reason,
                    self._retry_cooldown_seconds,
                )
                return False

            summary = completion.text.strip()[: self._max_chars]
            await self._repository.save_summary(
                chat_id=chat_id,
                summary=summary,
                through_message_id=batch.through_message_id,
            )
            self._retry_after.pop(chat_id, None)
            logger.info(
                "Conversation summary updated for chat %s through message %s",
                chat_id,
                batch.through_message_id,
            )
            return True

    def _defer_retry(self, chat_id: int) -> None:
        self._retry_after[chat_id] = self._clock() + self._retry_cooldown_seconds
