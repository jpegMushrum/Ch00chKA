from __future__ import annotations

from typing import Protocol, Sequence

from ch00chka.domain import (
    ActionPlan,
    ActorResponse,
    ChatMessage,
    ChatParticipant,
    ConversationContext,
    NormalizedMessage,
    ParticipationDecision,
    ReviewResult,
    ResearchResult,
    SummaryBatch,
)


class ConversationRepository(Protocol):
    async def initialize(self) -> None: ...

    async def add_message(
        self,
        *,
        chat_id: int,
        role: str,
        user_name: str,
        text: str,
    ) -> None: ...

    async def recent_messages(
        self,
        *,
        chat_id: int,
        limit: int,
    ) -> Sequence[ChatMessage]: ...

    async def get_summary(self, chat_id: int) -> str: ...

    async def get_summary_batch(
        self,
        *,
        chat_id: int,
        keep_recent: int,
        min_batch_size: int,
        max_batch_size: int,
    ) -> SummaryBatch | None: ...

    async def save_summary(
        self,
        *,
        chat_id: int,
        summary: str,
        through_message_id: int,
    ) -> None: ...

    async def get_personality(self, chat_id: int) -> str: ...

    async def set_personality(self, chat_id: int, personality: str) -> None: ...

    async def get_aliases(self, chat_id: int) -> tuple[str, ...]: ...

    async def set_aliases(self, chat_id: int, aliases: Sequence[str]) -> None: ...

    async def upsert_participant(
        self,
        *,
        chat_id: int,
        user_id: int,
        display_name: str,
        username: str | None,
    ) -> None: ...

    async def list_participants(self, chat_id: int) -> Sequence[ChatParticipant]: ...

    async def get_feature_overrides(self, chat_id: int) -> dict[str, bool]: ...

    async def toggle_feature(
        self,
        *,
        chat_id: int,
        feature: str,
        default_enabled: bool,
    ) -> bool: ...


class ParticipationDecider(Protocol):
    async def decide(self, message: NormalizedMessage) -> ParticipationDecision: ...


class Researcher(Protocol):
    async def research(self, message: NormalizedMessage) -> ResearchResult: ...


class ContextBuilder(Protocol):
    async def build(self, message: NormalizedMessage) -> ConversationContext: ...


class ConversationMemory(Protocol):
    async def refresh(self, chat_id: int) -> bool: ...


class Actor(Protocol):
    async def respond(
        self,
        context: ConversationContext,
        *,
        revision_instruction: str | None = None,
        previous_response: str | None = None,
    ) -> ActorResponse: ...


class Observer(Protocol):
    async def review(
        self,
        context: ConversationContext,
        response: ActorResponse,
    ) -> ReviewResult: ...


class ActionPlanner(Protocol):
    async def plan(self, message: NormalizedMessage) -> ActionPlan: ...
