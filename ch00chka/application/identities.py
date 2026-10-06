from __future__ import annotations

import re
from collections.abc import Sequence

from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ParticipantAlias, normalize_participant_alias, unique_aliases


_IDENTIFIER = r"(?P<alias>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_-]{1,31})"
_SELF_PATTERNS = (
    re.compile(
        rf"(?:^|[.!?]\s+)(?:меня\s+зовут|зовите\s+меня|можно\s+звать)\s+{_IDENTIFIER}",
        re.IGNORECASE,
    ),
    re.compile(rf"^\s*я\s*[—-]\s*{_IDENTIFIER}", re.IGNORECASE),
    re.compile(rf"^\s*я\s+(?P<alias>[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_-]{{1,31}})(?=$|[,.!])"),
)
_REPLY_NAME_PATTERNS = (
    re.compile(rf"(?:^|[.!?]\s+)(?:его|е[ёе])\s+зовут\s+{_IDENTIFIER}", re.IGNORECASE),
    re.compile(rf"^\s*[Ээ]то\s+(?P<alias>[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_-]{{1,31}})(?=$|[,.!])"),
)
_RELATION_PATTERN = re.compile(
    r"(?<![\w@])(?P<subject>@?[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_-]{1,31})"
    r"\s*(?:(?:[—–-]\s*)?(?:это(?:\s+же)?|зовут)\s+|[—–-]\s+)"
    + _IDENTIFIER,
    re.IGNORECASE,
)


class ParticipantIdentityRecorder:
    """Record only simple, attributable aliases without making an LLM call."""

    def __init__(self, *, repository: ConversationRepository) -> None:
        self._repository = repository

    async def observe(
        self,
        *,
        chat_id: int,
        author_user_id: int,
        text: str,
        reply_target_user_id: int | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            return

        own_aliases = _aliases_from_patterns(text, _SELF_PATTERNS)
        if own_aliases:
            await self._save(
                chat_id=chat_id,
                user_id=author_user_id,
                aliases=own_aliases,
                source="self_intro",
                confidence=95,
            )

        if reply_target_user_id is not None:
            reply_aliases = _aliases_from_patterns(text, _REPLY_NAME_PATTERNS)
            if reply_aliases:
                await self._save(
                    chat_id=chat_id,
                    user_id=reply_target_user_id,
                    aliases=reply_aliases,
                    source="reply_relation",
                    confidence=85,
                )

        relation_aliases = await self._aliases_from_textual_relations(chat_id, text)
        for user_id, aliases in relation_aliases.items():
            await self._save(
                chat_id=chat_id,
                user_id=user_id,
                aliases=aliases,
                source="chat_relation",
                confidence=75,
            )

    async def _aliases_from_textual_relations(
        self,
        chat_id: int,
        text: str,
    ) -> dict[int, tuple[str, ...]]:
        matches = tuple(_RELATION_PATTERN.finditer(text))
        if not matches:
            return {}
        known_aliases = await self._repository.list_participant_aliases(chat_id)
        owners = _unique_alias_owners(known_aliases)
        collected: dict[int, list[str]] = {}
        for match in matches:
            subject = normalize_participant_alias(match.group("subject"))
            alias = match.group("alias")
            user_id = owners.get(subject) if subject else None
            if user_id is not None:
                collected.setdefault(user_id, []).append(alias)
        return {
            user_id: unique_aliases(aliases)
            for user_id, aliases in collected.items()
        }

    async def _save(
        self,
        *,
        chat_id: int,
        user_id: int,
        aliases: Sequence[str],
        source: str,
        confidence: int,
    ) -> None:
        values = unique_aliases(aliases)
        if values:
            await self._repository.save_participant_aliases(
                chat_id=chat_id,
                user_id=user_id,
                aliases=values,
                source=source,
                confidence=confidence,
            )


def _aliases_from_patterns(text: str, patterns: Sequence[re.Pattern[str]]) -> tuple[str, ...]:
    return unique_aliases(
        match.group("alias")
        for pattern in patterns
        for match in pattern.finditer(text)
    )


def _unique_alias_owners(aliases: Sequence[ParticipantAlias]) -> dict[str, int]:
    owners: dict[str, set[int]] = {}
    for alias in aliases:
        if alias.confidence < 70:
            continue
        key = normalize_participant_alias(alias.alias)
        if key:
            owners.setdefault(key, set()).add(alias.user_id)
    return {
        alias: next(iter(user_ids))
        for alias, user_ids in owners.items()
        if len(user_ids) == 1
    }
