from __future__ import annotations

import logging
from dataclasses import replace

from ch00chka.application.ports import (
    ActionPlanner,
    Actor,
    ContextBuilder,
    ConversationRepository,
    Observer,
    ParticipationDecider,
    Researcher,
)
from ch00chka.domain import (
    ActionPlan,
    ActionType,
    NormalizedMessage,
    ParticipationDecision,
    PlannedAction,
    ProcessingResult,
    ReplyReason,
    ReviewResult,
    ReviewVerdict,
    ResearchDecision,
    ResearchResult,
)

logger = logging.getLogger(__name__)


class EmptyActionPlanner:
    async def plan(self, message: NormalizedMessage) -> ActionPlan:
        return ActionPlan()


class NullResearcher:
    async def research(self, message: NormalizedMessage) -> ResearchResult:
        return ResearchResult(
            decision=ResearchDecision(
                needs_research=False,
                reason="disabled",
            )
        )


class MessageProcessor:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        participation: ParticipationDecider,
        context_builder: ContextBuilder,
        actor: Actor,
        observer: Observer,
        researcher: Researcher | None = None,
        action_planner: ActionPlanner | None = None,
        observer_enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._participation = participation
        self._context_builder = context_builder
        self._actor = actor
        self._observer = observer
        self._researcher = researcher or NullResearcher()
        self._action_planner = action_planner or EmptyActionPlanner()
        self._observer_enabled = observer_enabled

    async def process(self, message: NormalizedMessage) -> ProcessingResult:
        plan = await self._action_planner.plan(message)

        if not message.text.strip():
            return ProcessingResult(
                plan=plan,
                participation=ParticipationDecision(
                    should_reply=False,
                    reason=ReplyReason.INVALID_MESSAGE,
                ),
            )

        await self._repository.add_message(
            chat_id=message.chat_id,
            role="user",
            user_name=message.user_name,
            text=message.text,
        )

        try:
            decision = await self._participation.decide(message)
        except Exception:
            logger.exception("Participation agent failed")
            decision = ParticipationDecision(
                should_reply=False,
                reason=ReplyReason.ERROR,
                confidence=0.0,
            )

        if not decision.should_reply:
            return ProcessingResult(plan=plan, participation=decision)

        research: ResearchResult | None = None
        try:
            research = await self._researcher.research(message)
        except Exception:
            logger.exception("Research pipeline failed; continuing without external facts")

        try:
            context = await self._context_builder.build(message)
            if research and research.decision.needs_research:
                context = replace(
                    context,
                    research_facts=research.facts,
                    research_performed=True,
                )
            response = await self._actor.respond(context)
        except Exception:
            logger.exception("Actor pipeline failed")
            return ProcessingResult(
                plan=plan,
                participation=ParticipationDecision(
                    should_reply=False,
                    reason=ReplyReason.ERROR,
                    confidence=0.0,
                ),
                review=ReviewResult(
                    verdict=ReviewVerdict.BLOCK,
                    violations=("actor_pipeline_unavailable",),
                ),
                research=research,
            )
        review: ReviewResult | None = None

        if self._observer_enabled or response.incomplete:
            review = await self._review(context, response)

            if review.verdict is ReviewVerdict.BLOCK:
                return ProcessingResult(
                    plan=plan,
                    participation=decision,
                    review=review,
                    research=research,
                )

            if review.verdict is ReviewVerdict.REVISE:
                revision_instruction = review.revision_instruction or (
                    "Исправь нарушения: " + ", ".join(review.violations)
                )
                response = await self._actor.respond(
                    context,
                    revision_instruction=revision_instruction,
                    previous_response=response.text,
                )
                if self._observer_enabled or response.incomplete:
                    review = await self._review(context, response)
                    if review.verdict is not ReviewVerdict.ACCEPT:
                        return ProcessingResult(
                            plan=plan,
                            participation=decision,
                            review=review,
                            research=research,
                        )
                else:
                    review = ReviewResult(
                        verdict=ReviewVerdict.ACCEPT,
                        violations=("truncation_recovered",),
                    )

        plan = plan.with_action(
            PlannedAction(
                type=ActionType.CHAT_REPLY,
                payload={"text": response.text},
            )
        )
        await self._repository.add_message(
            chat_id=message.chat_id,
            role="assistant",
            user_name=message.bot_name,
            text=response.text,
        )
        return ProcessingResult(
            plan=plan,
            participation=decision,
            reply_text=response.text,
            review=review,
            research=research,
        )

    async def _review(self, context, response) -> ReviewResult:
        try:
            return await self._observer.review(context, response)
        except Exception:
            logger.exception("Observer agent failed; accepting actor response")
            return ReviewResult(
                verdict=ReviewVerdict.ACCEPT,
                violations=("observer_unavailable",),
            )
