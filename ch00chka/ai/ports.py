from __future__ import annotations

from typing import Protocol, Sequence


ChatMessagePayload = dict[str, str]


class LLMGateway(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[ChatMessagePayload],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str: ...
