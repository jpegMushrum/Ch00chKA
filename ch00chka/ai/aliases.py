from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.prompts import ALIAS_GENERATION_SYSTEM_PROMPT


_GENERIC_ALIASES = {
    "ai",
    "bot",
    "assistant",
    "ии",
    "бот",
    "робот",
    "ассистент",
    "помощник",
}


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    normalized = re.sub(r"\s+", " ", normalized).strip(" @.,!?;:'\"()[]{}")
    return normalized


def sanitize_aliases(values: Iterable[object]) -> tuple[str, ...]:
    aliases: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        alias = normalize_alias(value)
        if (
            2 <= len(alias) <= 32
            and alias not in _GENERIC_ALIASES
            and "\n" not in alias
            and alias.count(" ") <= 2
        ):
            aliases.add(alias)
    return tuple(sorted(aliases))


class BotAliasRegistry:
    def __init__(self, aliases: Iterable[str] = ()) -> None:
        self._aliases = sanitize_aliases(aliases)

    @property
    def aliases(self) -> tuple[str, ...]:
        return self._aliases

    def replace(self, aliases: Iterable[str]) -> None:
        self._aliases = sanitize_aliases(aliases)

    def matches(self, text: str) -> bool:
        normalized_text = normalize_alias(text)
        return any(
            re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized_text)
            for alias in self._aliases
        )


class LLMAliasGenerator:
    def __init__(self, *, gateway: LLMGateway, model: str) -> None:
        self._gateway = gateway
        self._model = model

    async def generate(self, *, bot_name: str, bot_username: str | None) -> tuple[str, ...]:
        raw = await self._gateway.complete(
            model=self._model,
            temperature=0.2,
            max_tokens=160,
            messages=[
                {"role": "system", "content": ALIAS_GENERATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Отображаемое имя: {bot_name}\n"
                        f"Username: @{bot_username or 'не задан'}"
                    ),
                },
            ],
        )
        data = parse_json_object(raw)
        values = data.get("aliases", [])
        if not isinstance(values, list):
            raise ValueError("Alias generator returned a non-list aliases field")
        return sanitize_aliases(values)
