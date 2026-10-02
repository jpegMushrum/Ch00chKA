from __future__ import annotations

import asyncio
import json
import re

from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.message_format import format_current_message
from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.prompts import PARTICIPATION_SYSTEM_PROMPT
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import (
    ChatParticipant,
    NormalizedMessage,
    ParticipationDecision,
    ReplyReason,
    ResponseDepth,
)


_MODEL_REASONS = {
    "direct_question": ReplyReason.DIRECT_QUESTION,
    "continuation": ReplyReason.CONTINUATION,
    "valuable_contribution": ReplyReason.VALUABLE_CONTRIBUTION,
    "search_request": ReplyReason.SEARCH_REQUEST,
    "no_value": ReplyReason.NO_VALUE,
}

_BRIEF_REQUEST = re.compile(
    r"\b(коротко|кратко|в двух словах|одним словом|без подробностей|briefly)\b",
    re.IGNORECASE,
)
_DETAILED_REQUEST = re.compile(
    r"\b(подробно|детально|разв[её]рнуто|пошагово|полный (?:список|разбор)|"
    r"со всеми подробностями|in detail|step[- ]by[- ]step)\b",
    re.IGNORECASE,
)
_NORMAL_REQUEST = re.compile(
    r"\b(найди|поищи|проверь|сравни|объясни|перечисли|какие бывают|"
    r"что известно|кто такой|что такое|как (?:сделать|работает)|почему|"
    r"find|search|compare|explain|list)\b",
    re.IGNORECASE,
)
_CHAT_STATE_CONTEXT_MAX_CHARS = 800


def _direct_response_depth(text: str) -> ResponseDepth:
    if _BRIEF_REQUEST.search(text):
        return ResponseDepth.BRIEF
    if _DETAILED_REQUEST.search(text):
        return ResponseDepth.DETAILED
    if _NORMAL_REQUEST.search(text):
        return ResponseDepth.NORMAL
    return ResponseDepth.BRIEF


def _participant_index(
    participants: tuple[ChatParticipant, ...],
    *,
    limit: int,
) -> tuple[dict[str, int | str | None], ...]:
    return tuple(
        {
            "user_id": participant.user_id,
            "name": participant.display_name,
            "username": participant.username,
        }
        for participant in participants[:limit]
    )


def _mentioned_participant_ids(
    raw_value: object,
    *,
    allowed_ids: set[int],
    current_user_id: int,
) -> tuple[int, ...]:
    if not isinstance(raw_value, list):
        return ()
    selected: list[int] = []
    for value in raw_value:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            continue
        if (
            user_id in allowed_ids
            and user_id != current_user_id
            and user_id not in selected
        ):
            selected.append(user_id)
        if len(selected) == 2:
            break
    return tuple(selected)


class LLMAgentParticipationDecider:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        model: str,
        repository: ConversationRepository,
        recent_messages_limit: int = 8,
        participant_candidates_limit: int = 40,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._repository = repository
        self._recent_messages_limit = recent_messages_limit
        self._participant_candidates_limit = participant_candidates_limit

    async def decide(self, message: NormalizedMessage) -> ParticipationDecision:
        if message.is_reply_to_bot:
            return ParticipationDecision(
                True,
                ReplyReason.REPLY_TO_BOT,
                response_depth=_direct_response_depth(message.text),
            )
        if message.mentions_bot:
            return ParticipationDecision(
                True,
                ReplyReason.MENTIONED,
                response_depth=_direct_response_depth(message.text),
            )

        recent = list(
            await self._repository.recent_messages(
                chat_id=message.chat_id,
                limit=self._recent_messages_limit + 1,
            )
        )
        if (
            recent
            and recent[-1].role == "user"
            and (recent[-1].user_id is None or recent[-1].user_id == message.user_id)
            and recent[-1].user_name == message.user_name
            and recent[-1].text == message.text
        ):
            recent.pop()
        transcript = "\n".join(
            (
                f"{item.user_name} [user_id={item.user_id}]: {item.text}"
                if item.role == "user"
                else f"{item.user_name}: {item.text}"
            )
            for item in recent[-self._recent_messages_limit :]
        )
        chat_state, participants_result = await asyncio.gather(
            self._repository.get_summary(message.chat_id),
            self._repository.list_participants(message.chat_id),
        )
        participants = tuple(participants_result)
        participant_index = _participant_index(
            participants,
            limit=self._participant_candidates_limit,
        )

        completion = await self._gateway.complete(
            model=self._model,
            temperature=0.0,
            max_tokens=160,
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
                        f"Участники-кандидаты для распознавания упоминаний "
                        f"(только эти user_id можно вернуть): "
                        f"{json.dumps(participant_index, ensure_ascii=False)}\n"
                        f"Краткое состояние чата:\n"
                        f"{chat_state[:_CHAT_STATE_CONTEXT_MAX_CHARS] or 'Пусто'}\n"
                        f"Недавний контекст:\n{transcript or 'Пусто'}\n\n"
                        f"{format_current_message(message)}"
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
        try:
            response_depth = ResponseDepth(str(data.get("response_depth", "")))
        except ValueError:
            response_depth = (
                ResponseDepth.NORMAL
                if reason is ReplyReason.SEARCH_REQUEST
                else ResponseDepth.BRIEF
            )
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(confidence, 1.0))
        return ParticipationDecision(
            should_reply,
            reason,
            confidence,
            response_depth=response_depth,
            mentioned_participant_ids=_mentioned_participant_ids(
                data.get("mentioned_participant_ids"),
                allowed_ids={participant.user_id for participant in participants},
                current_user_id=message.user_id,
            ),
        )
