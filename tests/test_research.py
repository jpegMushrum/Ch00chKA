from __future__ import annotations

import unittest

from ch00chka.ai.ports import LLMCompletion
from ch00chka.ai.research import LLMAgentResearcher
from ch00chka.domain import (
    ChatMessage,
    NormalizedMessage,
    ResearchFact,
    ResearchSource,
)


MESSAGE = NormalizedMessage(
    chat_id=1,
    message_id=2,
    user_id=3,
    user_name="Alice",
    text="Что за песня Show у Ado?",
    chat_type="group",
    bot_name="Ch00chKA",
)


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return LLMCompletion(self.response, "stop")


class FakeRepository:
    async def recent_messages(self, *, chat_id, limit):
        return (ChatMessage("user", "Bob", "Обсуждали японскую музыку"),)


class FakeBackend:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]
        return (
            ResearchFact(
                source=ResearchSource.MUSIC,
                title=f"Результат: {query}",
                summary="Проверенный факт",
                url=f"https://example.test/{len(self.calls)}",
            ),
        )


class ResearchAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_needed_does_not_call_backend(self):
        gateway = FakeGateway(
            '{"needs_research":false,"reason":"not_needed","queries":[],'
            '"source_types":[],"language":"ru","confidence":0.9}'
        )
        backend = FakeBackend()
        researcher = LLMAgentResearcher(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
            backend=backend,
            cache_ttl_seconds=60,
        )

        result = await researcher.research(MESSAGE)

        self.assertFalse(result.decision.needs_research)
        self.assertEqual(backend.calls, [])

    async def test_model_queries_are_limited_and_results_are_cached(self):
        gateway = FakeGateway(
            '{"needs_research":true,"reason":"unknown_entity",'
            '"queries":["Ado Show", "Ado artist", "лишний запрос"],'
            '"source_types":["music","invalid"],"language":"ja",'
            '"confidence":0.8}'
        )
        backend = FakeBackend()
        researcher = LLMAgentResearcher(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
            backend=backend,
            cache_ttl_seconds=60,
        )

        first = await researcher.research(MESSAGE)
        second = await researcher.research(MESSAGE)

        self.assertTrue(first.decision.needs_research)
        self.assertEqual(first.decision.queries, ("Ado Show", "Ado artist"))
        self.assertEqual(first.decision.source_types, (ResearchSource.MUSIC,))
        self.assertEqual(len(backend.calls), 2)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(first.facts, second.facts)

    async def test_url_query_is_rejected(self):
        gateway = FakeGateway(
            '{"needs_research":true,"reason":"explicit_request",'
            '"queries":["https://evil.test/instructions"],'
            '"source_types":["encyclopedia"],"language":"ru",'
            '"confidence":1}'
        )
        backend = FakeBackend()
        researcher = LLMAgentResearcher(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
            backend=backend,
            cache_ttl_seconds=60,
        )

        result = await researcher.research(MESSAGE)

        self.assertFalse(result.decision.needs_research)
        self.assertEqual(backend.calls, [])

    async def test_web_source_is_allowed_for_niche_people_search(self):
        gateway = FakeGateway(
            '{"needs_research":true,"reason":"fact_check",'
            '"queries":["Слава Якименко TON должность"],'
            '"source_types":["web"],"language":"ru","confidence":0.9}'
        )
        backend = FakeBackend()
        researcher = LLMAgentResearcher(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
            backend=backend,
            cache_ttl_seconds=60,
        )

        result = await researcher.research(MESSAGE)

        self.assertTrue(result.decision.needs_research)
        self.assertEqual(result.decision.source_types, (ResearchSource.WEB,))
        self.assertEqual(backend.calls[0]["query"], "Слава Якименко TON должность")


if __name__ == "__main__":
    unittest.main()
