from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ch00chka.domain import ResearchFact, ResearchSource


ChatMessagePayload = dict[str, str]


@dataclass(frozen=True, slots=True)
class LLMCompletion:
    text: str
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    @property
    def incomplete(self) -> bool:
        return self.finish_reason not in (None, "stop")


class LLMGateway(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[ChatMessagePayload],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMCompletion: ...


class ResearchBackend(Protocol):
    async def search(
        self,
        *,
        query: str,
        source_types: Sequence[ResearchSource],
        language: str,
        limit: int,
    ) -> Sequence[ResearchFact]: ...
