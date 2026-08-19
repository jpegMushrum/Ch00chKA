from __future__ import annotations

import asyncio
import json
import logging

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
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._repository = repository
        self._keep_recent = keep_recent
        self._min_batch_size = min_batch_size
        self._max_batch_size = max_batch_size
        self._max_chars = max_chars
        self._locks: dict[int, asyncio.Lock] = {}

    async def refresh(self, chat_id: int) -> bool:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            batch = await self._repository.get_summary_batch(
                chat_id=chat_id,
                keep_recent=self._keep_recent,
                min_batch_size=self._min_batch_size,
                max_batch_size=self._max_batch_size,
            )
            if batch is None:
                return False

            payload = {
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
                max_tokens=1200,
            )
            if completion.incomplete:
                logger.warning(
                    "Summary update was incomplete for chat %s; cursor retained",
                    chat_id,
                )
                return False

            summary = completion.text.strip()[: self._max_chars]
            await self._repository.save_summary(
                chat_id=chat_id,
                summary=summary,
                through_message_id=batch.through_message_id,
            )
            logger.info(
                "Conversation summary updated for chat %s through message %s",
                chat_id,
                batch.through_message_id,
            )
            return True
