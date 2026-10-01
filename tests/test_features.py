from __future__ import annotations

import unittest

from ch00chka.application.features import ChatFeatureService
from ch00chka.domain import ChatFeature


class FakeFeatureRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[int, str], bool] = {}

    async def get_feature_overrides(self, chat_id: int) -> dict[str, bool]:
        return {
            feature: enabled
            for (stored_chat_id, feature), enabled in self.values.items()
            if stored_chat_id == chat_id
        }

    async def toggle_feature(
        self,
        *,
        chat_id: int,
        feature: str,
        default_enabled: bool,
    ) -> bool:
        key = (chat_id, feature)
        enabled = not self.values.get(key, default_enabled)
        self.values[key] = enabled
        return enabled


class ChatFeatureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = FakeFeatureRepository()
        self.service = ChatFeatureService(
            repository=self.repository,
            defaults={
                feature: feature is not ChatFeature.RESEARCH
                for feature in ChatFeature
            },
        )

    async def test_defaults_and_overrides_are_scoped_to_chat(self):
        first = await self.service.states(100)

        self.assertTrue(first[ChatFeature.AI_RESPONSES])
        self.assertFalse(first[ChatFeature.RESEARCH])

        self.assertFalse(await self.service.toggle(100, ChatFeature.AI_RESPONSES))
        first = await self.service.states(100)
        second = await self.service.states(200)

        self.assertFalse(first[ChatFeature.AI_RESPONSES])
        self.assertTrue(second[ChatFeature.AI_RESPONSES])

    async def test_processing_options_follow_feature_states(self):
        states = await self.service.states(100)

        options = self.service.processing_options(states)

        self.assertTrue(options.responses_enabled)
        self.assertTrue(options.memory_enabled)
        self.assertFalse(options.research_enabled)
        self.assertTrue(options.observer_enabled)


if __name__ == "__main__":
    unittest.main()
