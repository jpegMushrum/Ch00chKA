from __future__ import annotations

from typing import Sequence

from openai import AsyncOpenAI

from ch00chka.ai.ports import ChatMessagePayload

class DeepSeekGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        thinking_enabled: bool = False,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._thinking_enabled = thinking_enabled

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessagePayload],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={
                "thinking": {
                    "type": "enabled" if self._thinking_enabled else "disabled"
                }
            },
        )
        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("LLM returned an empty response")
        return response.choices[0].message.content.strip()
