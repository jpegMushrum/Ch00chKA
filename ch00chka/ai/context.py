from __future__ import annotations

from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ConversationContext, NormalizedMessage


class RepositoryContextBuilder:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        recent_messages_limit: int,
    ) -> None:
        self._repository = repository
        self._recent_messages_limit = recent_messages_limit

    async def build(self, message: NormalizedMessage) -> ConversationContext:
        messages = list(
            await self._repository.recent_messages(
                chat_id=message.chat_id,
                limit=self._recent_messages_limit + 1,
            )
        )
        if (
            messages
            and messages[-1].role == "user"
            and messages[-1].user_name == message.user_name
            and messages[-1].text == message.text
        ):
            messages.pop()

        return ConversationContext(
            personality=await self._repository.get_personality(message.chat_id),
            summary=await self._repository.get_summary(message.chat_id),
            recent_messages=tuple(messages[-self._recent_messages_limit :]),
            current_message=message,
            prompt_version=PROMPT_VERSION,
        )
