from __future__ import annotations

from typing import Protocol, Sequence

from ch00chka.domain import (
    ActionPlan,
    ActorResponse,
    ChatMessage,
    ConversationContext,
    NormalizedMessage,
    ParticipationDecision,
    ReviewResult,
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

    async def get_personality(self, chat_id: int) -> str: ...

    async def set_personality(self, chat_id: int, personality: str) -> None: ...


class ParticipationDecider(Protocol):
    async def decide(self, message: NormalizedMessage) -> ParticipationDecision: ...


class ContextBuilder(Protocol):
    async def build(self, message: NormalizedMessage) -> ConversationContext: ...


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
