from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ChatParticipant, NormalizedMessage, ParticipantMemory


@dataclass(frozen=True, slots=True)
class MemoryRoute:
    """The small, ordered set of participant profiles relevant to one reply."""

    participant_ids: tuple[int, ...]
    memories: tuple[ParticipantMemory, ...]


class RepositoryMemoryRouter:
    """Select participant memories without exposing the whole chat directory to the actor."""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        max_profiles: int = 3,
        max_facts_per_profile: int = 4,
        max_profile_chars: int = 600,
    ) -> None:
        self._repository = repository
        self._max_profiles = max_profiles
        self._max_facts_per_profile = max_facts_per_profile
        self._max_profile_chars = max_profile_chars

    async def route(
        self,
        message: NormalizedMessage,
        *,
        mentioned_participant_ids: Sequence[int] = (),
    ) -> MemoryRoute:
        participants = tuple(await self._repository.list_participants(message.chat_id))
        known_ids = {participant.user_id for participant in participants}
        selected: list[int] = []

        def add(user_id: int | None) -> None:
            if (
                user_id is not None
                and user_id in known_ids
                and user_id not in selected
                and len(selected) < self._max_profiles
            ):
                selected.append(user_id)

        # The author and an explicit reply target are always stronger signals than
        # fuzzy name matching or an LLM classification hint.
        add(message.user_id)
        reply = message.reply_to_message
        if reply and not reply.is_bot:
            add(reply.user_id)
        for user_id in mentioned_participant_ids:
            add(user_id)
        for participant in _textually_mentioned_participants(message, participants):
            add(participant.user_id)

        stored_memories = await self._repository.get_participant_memories(
            chat_id=message.chat_id,
            user_ids=selected,
        )
        memories = tuple(
            _compact_memory(
                memory,
                max_facts=self._max_facts_per_profile,
                max_chars=self._max_profile_chars,
            )
            for memory in stored_memories
        )
        return MemoryRoute(participant_ids=tuple(selected), memories=memories)


def _textually_mentioned_participants(
    message: NormalizedMessage,
    participants: Sequence[ChatParticipant],
) -> tuple[ChatParticipant, ...]:
    text = "\n".join(
        part for part in (message.text, message.quoted_text or "") if part
    ).casefold()
    if not text:
        return ()

    first_name_counts: dict[str, int] = {}
    for participant in participants:
        first_name = _first_name(participant.display_name)
        if first_name:
            first_name_counts[first_name] = first_name_counts.get(first_name, 0) + 1

    matched: list[ChatParticipant] = []
    for participant in participants:
        names = [_normalise_name(participant.display_name)]
        first_name = _first_name(participant.display_name)
        if first_name and first_name_counts.get(first_name) == 1:
            names.append(first_name)
        username = (participant.username or "").strip().casefold()
        if username:
            names.append(f"@{username}")
            names.append(username)
        if any(_contains_name(text, name) for name in names if name):
            matched.append(participant)
    return tuple(matched)


def _normalise_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _first_name(value: str) -> str:
    parts = _normalise_name(value).split(" ", maxsplit=1)
    if not parts or not parts[0]:
        return ""
    first = parts[0]
    return first if len(first) >= 3 else ""


def _contains_name(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w@]){re.escape(name)}(?!\w)", text))


def _compact_memory(
    memory: ParticipantMemory,
    *,
    max_facts: int,
    max_chars: int,
) -> ParticipantMemory:
    facts: list[str] = []
    used_chars = 0
    for fact in memory.facts:
        compact_fact = " ".join(fact.split())
        if not compact_fact:
            continue
        remaining = max_chars - used_chars
        if remaining <= 0 or len(facts) == max_facts:
            break
        if len(compact_fact) > remaining:
            compact_fact = compact_fact[: max(0, remaining - 1)].rstrip() + "…"
        facts.append(compact_fact)
        used_chars += len(compact_fact)
    return ParticipantMemory(
        user_id=memory.user_id,
        display_name=memory.display_name,
        username=memory.username,
        facts=tuple(facts),
    )
