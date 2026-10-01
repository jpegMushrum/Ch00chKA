from __future__ import annotations

from collections.abc import Mapping

from ch00chka.application.ports import ConversationRepository
from ch00chka.domain import ChatFeature, ProcessingOptions


class ChatFeatureService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        defaults: Mapping[ChatFeature, bool],
    ) -> None:
        self._repository = repository
        self._defaults = {
            feature: bool(defaults.get(feature, True)) for feature in ChatFeature
        }

    async def states(self, chat_id: int) -> dict[ChatFeature, bool]:
        overrides = await self._repository.get_feature_overrides(chat_id)
        return {
            feature: overrides.get(feature.value, default)
            for feature, default in self._defaults.items()
        }

    async def toggle(self, chat_id: int, feature: ChatFeature) -> bool:
        return await self._repository.toggle_feature(
            chat_id=chat_id,
            feature=feature.value,
            default_enabled=self._defaults[feature],
        )

    @staticmethod
    def processing_options(
        states: Mapping[ChatFeature, bool],
    ) -> ProcessingOptions:
        return ProcessingOptions(
            responses_enabled=states[ChatFeature.AI_RESPONSES],
            memory_enabled=states[ChatFeature.MEMORY],
            research_enabled=states[ChatFeature.RESEARCH],
            observer_enabled=states[ChatFeature.OBSERVER],
        )
