from __future__ import annotations

import asyncio
import re
import time

from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.ports import LLMGateway, ResearchBackend
from ch00chka.ai.prompts import RESEARCH_DECISION_SYSTEM_PROMPT
from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import (
    NormalizedMessage,
    ResearchDecision,
    ResearchResult,
    ResearchSource,
)


_ALLOWED_LANGUAGES = {"ru", "en", "ja"}
_ALLOWED_REASONS = {
    "explicit_request",
    "fresh_information",
    "unknown_entity",
    "fact_check",
    "not_needed",
}


def _clean_query(value: object) -> str | None:
    if not isinstance(value, str) or "://" in value:
        return None
    query = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    query = re.sub(r"\s+", " ", query).strip()
    return query if 2 <= len(query) <= 160 else None


class LLMAgentResearcher:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        model: str,
        repository: ConversationRepository,
        backend: ResearchBackend,
        cache_ttl_seconds: int,
        recent_messages_limit: int = 6,
        max_queries: int = 2,
        max_facts: int = 6,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._repository = repository
        self._backend = backend
        self._cache_ttl_seconds = cache_ttl_seconds
        self._recent_messages_limit = recent_messages_limit
        self._max_queries = max_queries
        self._max_facts = max_facts
        self._cache: dict[tuple, tuple[float, tuple]] = {}
        self._cache_lock = asyncio.Lock()

    async def research(self, message: NormalizedMessage) -> ResearchResult:
        decision = await self._decide(message)
        if not decision.needs_research:
            return ResearchResult(decision=decision)

        cache_key = (
            decision.queries,
            decision.source_types,
            decision.language,
        )
        now = time.monotonic()
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return ResearchResult(
                    decision=decision,
                    facts=cached[1],
                    from_cache=True,
                )

        batches = await asyncio.gather(
            *(
                self._backend.search(
                    query=query,
                    source_types=decision.source_types,
                    language=decision.language,
                    limit=3,
                )
                for query in decision.queries
            ),
            return_exceptions=True,
        )
        facts = []
        seen_urls: set[str] = set()
        for batch in batches:
            if isinstance(batch, BaseException):
                continue
            for fact in batch:
                if fact.url not in seen_urls:
                    seen_urls.add(fact.url)
                    facts.append(fact)
                if len(facts) >= self._max_facts:
                    break

        result_facts = tuple(facts[: self._max_facts])
        async with self._cache_lock:
            self._cache[cache_key] = (
                now + self._cache_ttl_seconds,
                result_facts,
            )
        return ResearchResult(decision=decision, facts=result_facts)

    async def _decide(self, message: NormalizedMessage) -> ResearchDecision:
        recent = list(
            await self._repository.recent_messages(
                chat_id=message.chat_id,
                limit=self._recent_messages_limit + 1,
            )
        )
        if (
            recent
            and recent[-1].role == "user"
            and recent[-1].user_name == message.user_name
            and recent[-1].text == message.text
        ):
            recent.pop()
        transcript = "\n".join(
            f"{item.user_name}: {item.text}" for item in recent[-self._recent_messages_limit :]
        )

        completion = await self._gateway.complete(
            model=self._model,
            temperature=0.0,
            max_tokens=260,
            messages=[
                {"role": "system", "content": RESEARCH_DECISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Недавний разговор:\n{transcript or 'Пусто'}\n\n"
                        f"Текущее сообщение от {message.user_name}: {message.text}"
                    ),
                },
            ],
        )
        if completion.incomplete:
            raise RuntimeError(
                f"Research decision was incomplete: {completion.finish_reason}"
            )
        data = parse_json_object(completion.text)

        queries = tuple(
            query
            for query in (_clean_query(item) for item in data.get("queries", []))
            if query
        )[: self._max_queries]
        source_types = []
        for value in data.get("source_types", []):
            try:
                source = ResearchSource(str(value))
            except ValueError:
                continue
            if source not in source_types:
                source_types.append(source)
        language = str(data.get("language", "ru"))
        if language not in _ALLOWED_LANGUAGES:
            language = "ru"
        reason = str(data.get("reason", "not_needed"))
        if reason not in _ALLOWED_REASONS:
            reason = "not_needed"
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        needs_research = bool(
            data.get("needs_research") is True and queries and source_types
        )
        return ResearchDecision(
            needs_research=needs_research,
            reason=reason,
            queries=queries if needs_research else (),
            source_types=tuple(source_types) if needs_research else (),
            language=language,
            confidence=confidence,
        )
