from __future__ import annotations

import unittest

from ch00chka.domain import ResearchSource
from ch00chka.integrations.research_sources import PublicResearchBackend


class StubWebBackend(PublicResearchBackend):
    async def _get_json(self, url, *, params):
        self.request = (url, params)
        return {
            "results": [
                {
                    "title": "Слава Якименко — профиль",
                    "content": "Работает в экосистеме TON.",
                    "url": "https://example.org/profile",
                    "publishedDate": "2026-01-01",
                },
                {
                    "title": "Локальная ссылка",
                    "content": "Не должна попасть в результат.",
                    "url": "http://127.0.0.1/private",
                },
            ]
        }


class PublicResearchBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_results_are_normalized_and_local_urls_are_rejected(self):
        backend = StubWebBackend(web_base_url="http://searxng:8080/")

        facts = await backend.search(
            query="Слава Якименко TON",
            source_types=(ResearchSource.WEB,),
            language="ru",
            limit=3,
        )

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].source, ResearchSource.WEB)
        self.assertEqual(facts[0].title, "Слава Якименко — профиль")
        self.assertEqual(backend.request[0], "http://searxng:8080/search")
        self.assertEqual(backend.request[1]["language"], "ru-RU")


if __name__ == "__main__":
    unittest.main()
