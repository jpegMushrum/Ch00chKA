from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Router

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.aliases import BotAliasRegistry, LLMAliasGenerator
from ch00chka.ai.context import RepositoryContextBuilder
from ch00chka.ai.gateway import DeepSeekGateway
from ch00chka.ai.memory_router import RepositoryMemoryRouter
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.ai.research import LLMAgentResearcher
from ch00chka.ai.summary import LLMAgentConversationMemory
from ch00chka.application import ChatFeatureService, MessageProcessor
from ch00chka.application.planners import UrlActionPlanner
from ch00chka.domain import ChatFeature
from ch00chka.infrastructure import SQLiteConversationRepository
from ch00chka.integrations.media import YtDlpMediaAdapter, YtDlpMediaDownloader
from ch00chka.integrations.research_sources import PublicResearchBackend
from ch00chka.presentation.telegram import create_router
from config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Application:
    router: Router
    repository: SQLiteConversationRepository
    processor: MessageProcessor
    alias_registry: BotAliasRegistry

    async def configure_bot_identity(
        self,
        *,
        bot_name: str,
        bot_username: str | None,
    ) -> None:
        aliases = {bot_name}
        if bot_username:
            aliases.add(bot_username)

        self.alias_registry.replace(aliases)
        logger.info("Bot aliases initialized: %s", ", ".join(self.alias_registry.aliases))


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
    alias_registry = BotAliasRegistry(())
    alias_generator = LLMAliasGenerator(
        gateway=gateway,
        model=settings.ai_participation_model,
    )
    participation = LLMAgentParticipationDecider(
        gateway=gateway,
        model=settings.ai_participation_model,
        repository=repository,
        participant_candidates_limit=settings.ai_memory_participant_candidates_limit,
    )
    context_builder = RepositoryContextBuilder(
        repository=repository,
        recent_messages_limit=settings.ai_recent_messages_limit,
        memory_router=RepositoryMemoryRouter(
            repository=repository,
            max_profiles=settings.ai_memory_max_profiles,
            max_facts_per_profile=settings.ai_memory_max_facts_per_profile,
        ),
    )
    actor = LLMAgentActor(
        gateway=gateway,
        model=settings.ai_model,
        brief_max_tokens=settings.ai_brief_max_tokens,
        normal_max_tokens=settings.ai_normal_max_tokens,
        detailed_max_tokens=settings.ai_detailed_max_tokens,
    )
    observer = LLMAgentObserver(gateway=gateway, model=settings.ai_observer_model)
    memory = None
    if settings.ai_summary_enabled:
        memory = LLMAgentConversationMemory(
            gateway=gateway,
            model=settings.ai_summary_model,
            repository=repository,
            keep_recent=settings.ai_recent_messages_limit,
            min_batch_size=settings.ai_summary_batch_size,
            max_batch_size=settings.ai_summary_max_batch_messages,
            max_chars=settings.ai_summary_max_chars,
            retry_cooldown_seconds=settings.ai_summary_retry_cooldown_seconds,
        )
    researcher = LLMAgentResearcher(
        gateway=gateway,
        model=settings.ai_research_model,
        repository=repository,
        backend=PublicResearchBackend(
            timeout_seconds=settings.research_timeout_seconds,
            web_base_url=settings.research_web_base_url,
        ),
        cache_ttl_seconds=settings.research_cache_ttl_seconds,
        max_facts=settings.research_max_facts,
    )
    processor = MessageProcessor(
        repository=repository,
        participation=participation,
        context_builder=context_builder,
        actor=actor,
        observer=observer,
        researcher=researcher,
        memory=memory,
        action_planner=UrlActionPlanner(),
        observer_enabled=True,
    )
    feature_service = ChatFeatureService(
        repository=repository,
        defaults={
            ChatFeature.AI_RESPONSES: True,
            ChatFeature.MEMORY: True,
            ChatFeature.RESEARCH: settings.ai_research_enabled,
            ChatFeature.OBSERVER: settings.ai_observer_enabled,
            ChatFeature.YOUTUBE: True,
            ChatFeature.TIKTOK: True,
            ChatFeature.INSTAGRAM: True,
            ChatFeature.MENTIONS: True,
        },
    )
    media_downloader = YtDlpMediaDownloader(
        temp_dir=settings.media_temp_dir,
        max_bytes=settings.effective_media_max_bytes,
        max_duration_seconds=settings.effective_media_max_duration_seconds,
        max_concurrent_downloads=settings.media_max_concurrent_downloads,
        proxy_url=(
            settings.media_proxy_url.get_secret_value()
            if settings.media_proxy_url
            else None
        ),
        cookies_file=settings.media_cookies_file or None,
    )
    router = create_router(
        processor=processor,
        repository=repository,
        alias_registry=alias_registry,
        alias_generator=alias_generator,
        feature_service=feature_service,
        media=YtDlpMediaAdapter(
            downloader=media_downloader,
            upload_chunk_size=settings.telegram_upload_chunk_size,
        ),
        admin_id=settings.telegram_admin_id or None,
    )
    return Application(
        router=router,
        repository=repository,
        processor=processor,
        alias_registry=alias_registry,
    )
