from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ch00chka.ai.memory_router import RepositoryMemoryRouter
from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ConversationContext, NormalizedMessage


class RepositoryContextBuilder:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        recent_messages_limit: int,
        memory_router: RepositoryMemoryRouter | None = None,
    ) -> None:
        self._repository = repository
        self._recent_messages_limit = recent_messages_limit
        self._memory_router = memory_router or RepositoryMemoryRouter(
            repository=repository
        )

    async def build(
        self,
        message: NormalizedMessage,
        *,
        mentioned_participant_ids: Sequence[int] = (),
    ) -> ConversationContext:
        messages_result, personality, chat_state, route = await asyncio.gather(
            self._repository.recent_messages(
                chat_id=message.chat_id,
                limit=self._recent_messages_limit + 1,
            ),
            self._repository.get_personality(message.chat_id),
            self._repository.get_summary(message.chat_id),
            self._memory_router.route(
                message,
                mentioned_participant_ids=mentioned_participant_ids,
            ),
        )
        messages = list(messages_result)
        if (
            messages
            and messages[-1].role == "user"
            and messages[-1].user_id == message.user_id
            and messages[-1].user_name == message.user_name
            and messages[-1].text == message.text
        ):
            messages.pop()

        return ConversationContext(
            personality=personality,
            summary=chat_state,
            recent_messages=tuple(messages[-self._recent_messages_limit :]),
            current_message=message,
            prompt_version=PROMPT_VERSION,
            participant_memories=route.memories,
        )
