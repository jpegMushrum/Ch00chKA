from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ReplyReason(StrEnum):
    MENTIONED = "mentioned"
    REPLY_TO_BOT = "reply_to_bot"
    DIRECT_QUESTION = "direct_question"
    CONTINUATION = "continuation"
    VALUABLE_CONTRIBUTION = "valuable_contribution"
    SEARCH_REQUEST = "search_request"
    NO_VALUE = "no_value"
    DISABLED = "disabled"
    INVALID_MESSAGE = "invalid_message"
    ERROR = "error"


class ReviewVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    BLOCK = "block"


class ActionType(StrEnum):
    CHAT_REPLY = "chat_reply"
    MEDIA_DOWNLOAD = "media_download"
    COMMAND = "command"


class ChatFeature(StrEnum):
    AI_RESPONSES = "ai_responses"
    MEMORY = "memory"
    RESEARCH = "research"
    OBSERVER = "observer"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    MENTIONS = "mentions"
    DELETE_SOURCE_LINKS = "delete_source_links"


class ResearchSource(StrEnum):
    MUSIC = "music"
    ENCYCLOPEDIA = "encyclopedia"
    WEB = "web"


class ResponseDepth(StrEnum):
    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"


@dataclass(frozen=True, slots=True)
class ReferencedMessage:
    message_id: int
    user_name: str
    text: str
    is_bot: bool = False
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    chat_id: int
    message_id: int
    user_id: int
    user_name: str
    text: str
    chat_type: str
    bot_name: str
    bot_username: str | None = None
    bot_aliases: tuple[str, ...] = ()
    is_reply_to_bot: bool = False
    mentions_bot: bool = False
    urls: tuple[str, ...] = ()
    reply_to_message: ReferencedMessage | None = None
    quoted_text: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    user_name: str
    text: str
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class ChatParticipant:
    user_id: int
    display_name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class MediaDeliveryResult:
    """Outcome of handling the supported media URLs from one chat message."""

    requested_urls: tuple[str, ...]
    delivered_urls: tuple[str, ...]

    @property
    def all_delivered(self) -> bool:
        return bool(self.requested_urls) and (
            len(self.delivered_urls) == len(self.requested_urls)
        )


@dataclass(frozen=True, slots=True)
class ParticipantMemory:
    """A compact, chat-scoped set of durable facts about one participant."""

    user_id: int
    display_name: str
    username: str | None = None
    facts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummarizableMessage:
    id: int
    role: str
    user_name: str
    text: str
    user_id: int | None = None


@dataclass(frozen=True, slots=True)
class SummaryBatch:
    current_summary: str
    messages: tuple[SummarizableMessage, ...]

    @property
    def through_message_id(self) -> int:
        return self.messages[-1].id


@dataclass(frozen=True, slots=True)
class ParticipationDecision:
    should_reply: bool
    reason: ReplyReason
    confidence: float = 1.0
    response_depth: ResponseDepth = ResponseDepth.BRIEF
    mentioned_participant_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ConversationContext:
    personality: str
    summary: str
    recent_messages: tuple[ChatMessage, ...]
    current_message: NormalizedMessage
    prompt_version: str
    response_depth: ResponseDepth = ResponseDepth.NORMAL
    research_facts: tuple["ResearchFact", ...] = ()
    research_performed: bool = False
    participant_memories: tuple[ParticipantMemory, ...] = ()

    @property
    def chat_state(self) -> str:
        """The durable state of this chat (kept as ``summary`` for DB compatibility)."""
        return self.summary


@dataclass(frozen=True, slots=True)
class ResearchDecision:
    needs_research: bool
    reason: str
    queries: tuple[str, ...] = ()
    source_types: tuple[ResearchSource, ...] = ()
    language: str = "ru"
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class ResearchFact:
    source: ResearchSource
    title: str
    summary: str
    url: str
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    decision: ResearchDecision
    facts: tuple[ResearchFact, ...] = ()
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class ActorResponse:
    text: str
    model: str
    prompt_version: str
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    @property
    def incomplete(self) -> bool:
        return self.finish_reason not in (None, "stop")


@dataclass(frozen=True, slots=True)
class ReviewResult:
    verdict: ReviewVerdict
    violations: tuple[str, ...] = ()
    revision_instruction: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedAction:
    type: ActionType
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionPlan:
    actions: tuple[PlannedAction, ...] = ()

    def with_action(self, action: PlannedAction) -> "ActionPlan":
        return ActionPlan(actions=(*self.actions, action))


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    plan: ActionPlan
    participation: ParticipationDecision
    reply_text: str | None = None
    review: ReviewResult | None = None
    research: ResearchResult | None = None


@dataclass(frozen=True, slots=True)
class ProcessingOptions:
    responses_enabled: bool = True
    memory_enabled: bool = True
    research_enabled: bool = True
    observer_enabled: bool = True
