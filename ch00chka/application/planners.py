from __future__ import annotations

from ch00chka.domain import ActionPlan, ActionType, NormalizedMessage, PlannedAction


class UrlActionPlanner:
    """Plans media work without knowing anything about concrete providers."""

    async def plan(self, message: NormalizedMessage) -> ActionPlan:
        return ActionPlan(
            actions=tuple(
                PlannedAction(type=ActionType.MEDIA_DOWNLOAD, payload={"url": url})
                for url in message.urls
            )
        )
