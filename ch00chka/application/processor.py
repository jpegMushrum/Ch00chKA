from __future__ import annotations

import logging
from dataclasses import replace

from ch00chka.application.ports import (
    ActionPlanner,
    Actor,
    ContextBuilder,
    ConversationMemory,
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
    ProcessingOptions,
    ProcessingResult,
    ReplyReason,
    ReviewResult,
    ReviewVerdict,
    ResearchDecision,
    ResearchResult,
)

logger = logging.getLogger(__name__)
FILTERED_REPLY_TEXT = "Filtered"


def _filtered_reply_text(review: ReviewResult) -> str:
    """Expose a useful reason without leaking reviewer instructions or raw codes."""
    violations = {violation.casefold() for violation in review.violations}
    if "too_long" in violations:
        reason = "ответ получился слишком длинным"
    elif "output_truncated" in violations or any(
        violation.startswith("incomplete_output:") for violation in violations
    ):
        reason = "ответ оборвался до завершения"
    elif "unsafe" in violations:
        reason = "ответ не прошёл проверку безопасности"
    elif any("identity" in violation or "persona" in violation for violation in violations):
        reason = "ответ не соответствует заданному образу"
    elif any("research" in violation or "fact" in violation for violation in violations):
        reason = "в ответе есть непроверенные сведения"
    elif any("repeat" in violation for violation in violations):
        reason = "ответ повторяет уже сказанное"
    else:
        reason = "ответ не прошёл проверку"
    return f"{FILTERED_REPLY_TEXT}: {reason}."


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


class NullConversationMemory:
    async def refresh(self, chat_id: int) -> bool:
        return False


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
        memory: ConversationMemory | None = None,
        action_planner: ActionPlanner | None = None,
        observer_enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._participation = participation
        self._context_builder = context_builder
        self._actor = actor
        self._observer = observer
        self._researcher = researcher or NullResearcher()
        self._memory = memory or NullConversationMemory()
        self._action_planner = action_planner or EmptyActionPlanner()
        self._observer_enabled = observer_enabled

    async def process(
        self,
        message: NormalizedMessage,
        *,
        options: ProcessingOptions | None = None,
    ) -> ProcessingResult:
        options = options or ProcessingOptions()
        plan = await self._action_planner.plan(message)

        if not message.text.strip():
            return ProcessingResult(
                plan=plan,
                participation=ParticipationDecision(
                    should_reply=False,
                    reason=ReplyReason.INVALID_MESSAGE,
                ),
            )

        if options.memory_enabled:
            await self._repository.add_message(
                chat_id=message.chat_id,
                role="user",
                user_name=message.user_name,
                text=message.text,
                user_id=message.user_id,
            )
            try:
                await self._memory.refresh(message.chat_id)
            except Exception:
                logger.exception(
                    "Conversation summary update failed; continuing with existing memory"
                )

        if not options.responses_enabled:
            return ProcessingResult(
                plan=plan,
                participation=ParticipationDecision(
                    should_reply=False,
                    reason=ReplyReason.DISABLED,
                ),
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
        if options.research_enabled:
            try:
                research = await self._researcher.research(message)
            except Exception:
                logger.exception("Research pipeline failed; continuing without external facts")

        try:
            context = await self._context_builder.build(
                message,
                mentioned_participant_ids=decision.mentioned_participant_ids,
            )
            context = replace(context, response_depth=decision.response_depth)
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

        observer_enabled = self._observer_enabled and options.observer_enabled
        if observer_enabled or response.incomplete:
            review = await self._review(context, response)

            if review.verdict is ReviewVerdict.BLOCK:
                self._log_rejection(message, response, review, research, decision)
                return await self._filtered_result(
                    plan=plan,
                    decision=decision,
                    review=review,
                    research=research,
                    message=message,
                    memory_enabled=options.memory_enabled,
                )

            if review.verdict is ReviewVerdict.REVISE:
                if not response.truncated:
                    self._log_rejection(message, response, review, research, decision)
                    return await self._filtered_result(
                        plan=plan,
                        decision=decision,
                        review=review,
                        research=research,
                        message=message,
                        memory_enabled=options.memory_enabled,
                    )
                revision_instruction = review.revision_instruction or (
                    "Исправь нарушения: " + ", ".join(review.violations)
                )
                response = await self._actor.respond(
                    context,
                    revision_instruction=revision_instruction,
                    previous_response=response.text,
                )
                if observer_enabled or response.incomplete:
                    review = await self._review(context, response)
                    if review.verdict is not ReviewVerdict.ACCEPT:
                        self._log_rejection(message, response, review, research, decision)
                        return await self._filtered_result(
                            plan=plan,
                            decision=decision,
                            review=review,
                            research=research,
                            message=message,
                            memory_enabled=options.memory_enabled,
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
        if options.memory_enabled:
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

    @staticmethod
    def _log_rejection(message, response, review, research, decision) -> None:
        logger.warning(
            "Actor response rejected: chat_id=%s message_id=%s verdict=%s "
            "violations=%s finish_reason=%s response_depth=%s "
            "research_performed=%s research_facts=%s",
            message.chat_id,
            message.message_id,
            review.verdict,
            review.violations,
            response.finish_reason,
            decision.response_depth,
            bool(research and research.decision.needs_research),
            len(research.facts) if research else 0,
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

    async def _filtered_result(
        self,
        *,
        plan: ActionPlan,
        decision: ParticipationDecision,
        review: ReviewResult,
        research: ResearchResult | None,
        message: NormalizedMessage,
        memory_enabled: bool,
    ) -> ProcessingResult:
        filtered_text = _filtered_reply_text(review)
        filtered_plan = plan.with_action(
            PlannedAction(
                type=ActionType.CHAT_REPLY,
                payload={"text": filtered_text},
            )
        )
        if memory_enabled:
            await self._repository.add_message(
                chat_id=message.chat_id,
                role="assistant",
                user_name=message.bot_name,
                text=filtered_text,
            )
        return ProcessingResult(
            plan=filtered_plan,
            participation=decision,
            reply_text=filtered_text,
            review=review,
            research=research,
        )
