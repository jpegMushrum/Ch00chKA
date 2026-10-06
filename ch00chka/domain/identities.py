from __future__ import annotations

import re
from collections.abc import Iterable


_ALIAS_TOKEN = re.compile(r"[\w-]+", re.UNICODE)
_EDGE_PUNCTUATION = " \t\r\n.,:;!?…'\"«»()[]{}"


def clean_participant_alias(value: str) -> str | None:
    """Return a compact human-readable alias, or None for unusable input."""
    alias = " ".join(value.strip().lstrip("@").split()).strip(_EDGE_PUNCTUATION)
    if not alias or len(alias) > 80 or not any(char.isalpha() for char in alias):
        return None
    return alias


def normalize_participant_alias(value: str) -> str | None:
    alias = clean_participant_alias(value)
    return alias.casefold() if alias else None


def participant_identity_aliases(
    display_name: str,
    username: str | None,
) -> tuple[str, ...]:
    """Derive safe aliases from Telegram's current, authoritative identity data."""
    candidates: list[str] = [display_name]
    if username:
        candidates.append(username)
    name_tokens = _ALIAS_TOKEN.findall(display_name)
    if name_tokens:
        candidates.append(name_tokens[0])

    aliases: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        alias = clean_participant_alias(value)
        key = normalize_participant_alias(alias) if alias else None
        if alias and key and key not in seen:
            seen.add(key)
            aliases.append(alias)
    return tuple(aliases)


def unique_aliases(values: Iterable[str]) -> tuple[str, ...]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        alias = clean_participant_alias(value)
        key = normalize_participant_alias(alias) if alias else None
        if alias and key and key not in seen:
            seen.add(key)
            aliases.append(alias)
    return tuple(aliases)
