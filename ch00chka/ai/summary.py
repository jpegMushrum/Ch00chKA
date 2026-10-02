from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.prompts import SUMMARY_SYSTEM_PROMPT
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ParticipantMemory


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

            participant_names = {
                message.user_id: message.user_name
                for message in batch.messages
                if message.role == "user" and message.user_id is not None
            }
            existing_memories = await self._repository.get_participant_memories(
                chat_id=chat_id,
                user_ids=tuple(participant_names),
            )
            existing_facts = {
                memory.user_id: list(memory.facts) for memory in existing_memories
            }
            payload = {
                "max_summary_characters": self._max_chars,
                "existing_chat_state": batch.current_summary,
                "participant_profiles": [
                    {
                        "user_id": user_id,
                        "name": name,
                        "existing_facts": existing_facts.get(user_id, []),
                    }
                    for user_id, name in participant_names.items()
                ],
                "new_messages": [
                    {
                        "role": message.role,
                        "author_user_id": message.user_id,
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
                    max_tokens=1200,
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

            try:
                update = _parse_memory_update(
                    completion.text,
                    participant_names=participant_names,
                )
            except ValueError:
                self._defer_retry(chat_id)
                logger.warning(
                    "Memory update was invalid for chat %s; cursor retained", chat_id
                )
                return False
            summary, memories = update
            summary = summary[: self._max_chars]
            await self._repository.save_participant_memories(
                chat_id=chat_id,
                memories=memories,
            )
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


def _parse_memory_update(
    text: str,
    *,
    participant_names: dict[int, str],
) -> tuple[str, tuple[ParticipantMemory, ...]]:
    try:
        payload = parse_json_object(text)
    except (TypeError, ValueError) as error:
        raise ValueError("memory update is not JSON") from error

    chat_state = payload.get("chat_state")
    if not isinstance(chat_state, str):
        raise ValueError("memory update is missing chat_state")

    raw_memories = payload.get("participant_memories", [])
    if not isinstance(raw_memories, list):
        raise ValueError("participant_memories is not a list")

    memories: list[ParticipantMemory] = []
    seen_user_ids: set[int] = set()
    for item in raw_memories:
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item.get("user_id"))
        except (TypeError, ValueError):
            continue
        if user_id not in participant_names or user_id in seen_user_ids:
            continue
        raw_facts = item.get("facts")
        if not isinstance(raw_facts, list):
            continue
        facts = tuple(
            fact.strip()[:320]
            for fact in raw_facts
            if isinstance(fact, str) and fact.strip()
        )[:6]
        memories.append(
            ParticipantMemory(
                user_id=user_id,
                display_name=participant_names[user_id],
                facts=facts,
            )
        )
        seen_user_ids.add(user_id)
    return chat_state.strip(), tuple(memories)
