from __future__ import annotations

from dataclasses import dataclass

from aiogram import Router

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.context import RepositoryContextBuilder
from ch00chka.ai.gateway import DeepSeekGateway
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.application import MessageProcessor
from ch00chka.application.planners import UrlActionPlanner
from ch00chka.infrastructure import SQLiteConversationRepository
from ch00chka.integrations.legacy_media import LegacyMediaAdapter
from ch00chka.presentation.telegram import create_router
from config import Settings


@dataclass(frozen=True, slots=True)
class Application:
    router: Router
    repository: SQLiteConversationRepository
    processor: MessageProcessor


def build_application(settings: Settings) -> Application:
    repository = SQLiteConversationRepository(
        db_path=settings.db_path,
        default_personality=settings.default_personality,
    )
    gateway = DeepSeekGateway(
        api_key=settings.AI_api_token.get_secret_value(),
        base_url=settings.ai_base_url,
        thinking_enabled=settings.ai_thinking_enabled,
    )
    participation = LLMAgentParticipationDecider(
        gateway=gateway,
        model=settings.ai_participation_model,
        repository=repository,
    )
    context_builder = RepositoryContextBuilder(
        repository=repository,
        recent_messages_limit=settings.ai_recent_messages_limit,
    )
    actor = LLMAgentActor(gateway=gateway, model=settings.ai_model)
    observer = LLMAgentObserver(gateway=gateway, model=settings.ai_observer_model)
    processor = MessageProcessor(
        repository=repository,
        participation=participation,
        context_builder=context_builder,
        actor=actor,
        observer=observer,
        action_planner=UrlActionPlanner(),
        observer_enabled=settings.ai_observer_enabled,
    )
    router = create_router(
        processor=processor,
        repository=repository,
        media=LegacyMediaAdapter(),
    )
    return Application(router=router, repository=repository, processor=processor)
