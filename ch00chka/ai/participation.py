from __future__ import annotations

from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.prompts import PARTICIPATION_SYSTEM_PROMPT
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import NormalizedMessage, ParticipationDecision, ReplyReason


_MODEL_REASONS = {
    "direct_question": ReplyReason.DIRECT_QUESTION,
    "continuation": ReplyReason.CONTINUATION,
    "valuable_contribution": ReplyReason.VALUABLE_CONTRIBUTION,
    "search_request": ReplyReason.SEARCH_REQUEST,
    "no_value": ReplyReason.NO_VALUE,
}


class LLMAgentParticipationDecider:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        model: str,
        repository: ConversationRepository,
        recent_messages_limit: int = 8,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._repository = repository
        self._recent_messages_limit = recent_messages_limit

    async def decide(self, message: NormalizedMessage) -> ParticipationDecision:
        if message.is_reply_to_bot:
            return ParticipationDecision(True, ReplyReason.REPLY_TO_BOT)
        if message.mentions_bot:
            return ParticipationDecision(True, ReplyReason.MENTIONED)

        recent = list(
            await self._repository.recent_messages(
                chat_id=message.chat_id,
                limit=self._recent_messages_limit + 1,
            )
        )
        if (
            recent
            and recent[-1].role == "user"
            and recent[-1].user_name == message.user_name
            and recent[-1].text == message.text
        ):
            recent.pop()
        transcript = "\n".join(
            f"{item.user_name}: {item.text}" for item in recent[-self._recent_messages_limit :]
        )

        completion = await self._gateway.complete(
            model=self._model,
            temperature=0.0,
            max_tokens=120,
            messages=[
                {"role": "system", "content": PARTICIPATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Чат: {message.chat_type}\n"
                        f"Имя бота: {message.bot_name}\n"
                        f"Username бота: @{message.bot_username or 'не задан'}\n"
                        f"Варианты обращения к боту: "
                        f"{', '.join(message.bot_aliases) or 'не заданы'}\n"
                        f"Недавний контекст:\n{transcript or 'Пусто'}\n\n"
                        f"Текущее сообщение от {message.user_name}: {message.text}"
                    ),
                },
            ],
        )
        if completion.incomplete:
            raise RuntimeError(
                f"Participation response was incomplete: {completion.finish_reason}"
            )
        data = parse_json_object(completion.text)
        should_reply = data.get("should_reply") is True
        reason = _MODEL_REASONS.get(str(data.get("reason", "no_value")), ReplyReason.NO_VALUE)
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(confidence, 1.0))
        return ParticipationDecision(should_reply, reason, confidence)
