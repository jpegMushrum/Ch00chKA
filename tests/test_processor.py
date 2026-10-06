from __future__ import annotations

import unittest

from ch00chka.application.processor import MessageProcessor
from ch00chka.domain import (
    ActorResponse,
    ChatMessage,
    ConversationContext,
    NormalizedMessage,
    ParticipationDecision,
    ProcessingOptions,
    ReplyReason,
    ResearchDecision,
    ResearchFact,
    ResearchResult,
    ResearchSource,
    ResponseDepth,
    ReviewResult,
    ReviewVerdict,
)


MESSAGE = NormalizedMessage(
    chat_id=1,
    message_id=10,
    user_id=2,
    user_name="Tester",
    text="Бот, как дела?",
    chat_type="group",
    bot_name="Ch00chKA",
    mentions_bot=True,
)


class FakeRepository:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    async def add_message(self, *, chat_id, role, user_name, text, user_id=None) -> None:
        self.messages.append(ChatMessage(role, user_name, text, user_id))


class FakeParticipation:
    def __init__(
        self,
        should_reply: bool,
        response_depth: ResponseDepth = ResponseDepth.BRIEF,
    ) -> None:
        self.should_reply = should_reply
        self.response_depth = response_depth
        self.calls = 0

    async def decide(self, message):
        self.calls += 1
        return ParticipationDecision(
            self.should_reply,
            ReplyReason.MENTIONED if self.should_reply else ReplyReason.NO_VALUE,
            response_depth=self.response_depth,
        )


class FakeContextBuilder:
    async def build(self, message, *, mentioned_participant_ids=()):
        return ConversationContext(
            personality="Дружелюбный",
            summary="",
            recent_messages=(),
            current_message=message,
            prompt_version="test",
        )


class ScriptedActor:
    def __init__(self, responses: list[str | tuple[str, str | None]]) -> None:
        self.responses = responses
        self.calls: list[str | None] = []
        self.contexts = []

    async def respond(
        self,
        context,
        *,
        revision_instruction=None,
        previous_response=None,
    ):
        self.contexts.append(context)
        self.calls.append((revision_instruction, previous_response))
        scripted = self.responses[len(self.calls) - 1]
        text, finish_reason = scripted if isinstance(scripted, tuple) else (scripted, "stop")
        return ActorResponse(
            text=text,
            model="fake",
            prompt_version="test",
            finish_reason=finish_reason,
        )


class ScriptedObserver:
    def __init__(self, reviews: list[ReviewResult]) -> None:
        self.reviews = reviews
        self.calls = 0

    async def review(self, context, response):
        review = self.reviews[self.calls]
        self.calls += 1
        return review


class FakeResearcher:
    def __init__(self):
        self.calls = 0

    async def research(self, message):
        self.calls += 1
        return ResearchResult(
            decision=ResearchDecision(
                needs_research=True,
                reason="unknown_entity",
                queries=("Ado Show",),
                source_types=(ResearchSource.MUSIC,),
            ),
            facts=(
                ResearchFact(
                    source=ResearchSource.MUSIC,
                    title="Ado — Show",
                    summary="Песня",
                    url="https://example.test/ado-show",
                ),
            ),
        )


class FakeMemory:
    def __init__(self, *, fail: bool = False) -> None:
        self.chat_ids = []
        self.fail = fail

    async def refresh(self, chat_id):
        self.chat_ids.append(chat_id)
        if self.fail:
            raise RuntimeError("summary unavailable")
        return True


class MessageProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_research_does_not_call_researcher(self):
        researcher = FakeResearcher()
        actor = ScriptedActor(["Ответ без поиска"])
        processor = MessageProcessor(
            repository=FakeRepository(),
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=ScriptedObserver([ReviewResult(ReviewVerdict.ACCEPT)]),
            researcher=researcher,
        )

        result = await processor.process(
            MESSAGE,
            options=ProcessingOptions(research_enabled=False),
        )

        self.assertEqual(researcher.calls, 0)
        self.assertIsNone(result.research)
        self.assertFalse(actor.contexts[0].research_performed)

    async def test_disabled_responses_skip_ai_without_storing_message(self):
        repository = FakeRepository()
        participation = FakeParticipation(True)
        processor = MessageProcessor(
            repository=repository,
            participation=participation,
            context_builder=FakeContextBuilder(),
            actor=ScriptedActor(["unused"]),
            observer=ScriptedObserver([]),
        )

        result = await processor.process(
            MESSAGE,
            options=ProcessingOptions(
                responses_enabled=False,
                memory_enabled=False,
            ),
        )

        self.assertEqual(result.participation.reason, ReplyReason.DISABLED)
        self.assertEqual(repository.messages, [])
        self.assertEqual(participation.calls, 0)

    async def test_disabled_memory_does_not_store_messages_or_refresh_summary(self):
        repository = FakeRepository()
        memory = FakeMemory()
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(False),
            context_builder=FakeContextBuilder(),
            actor=ScriptedActor(["unused"]),
            observer=ScriptedObserver([]),
            memory=memory,
        )

        await processor.process(
            MESSAGE,
            options=ProcessingOptions(memory_enabled=False),
        )

        self.assertEqual(repository.messages, [])
        self.assertEqual(memory.chat_ids, [])

    async def test_selected_response_depth_reaches_actor_context(self):
        actor = ScriptedActor(["Подробный ответ"])
        processor = MessageProcessor(
            repository=FakeRepository(),
            participation=FakeParticipation(True, ResponseDepth.DETAILED),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=ScriptedObserver([ReviewResult(ReviewVerdict.ACCEPT)]),
        )

        await processor.process(MESSAGE)

        self.assertEqual(actor.contexts[0].response_depth, ResponseDepth.DETAILED)

    async def test_refreshes_memory_after_storing_user_message(self):
        repository = FakeRepository()
        memory = FakeMemory()
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(False),
            context_builder=FakeContextBuilder(),
            actor=ScriptedActor(["unused"]),
            observer=ScriptedObserver([]),
            memory=memory,
        )

        await processor.process(MESSAGE)

        self.assertEqual(memory.chat_ids, [MESSAGE.chat_id])
        self.assertEqual(repository.messages[0].text, MESSAGE.text)

    async def test_summary_failure_does_not_break_message_processing(self):
        processor = MessageProcessor(
            repository=FakeRepository(),
            participation=FakeParticipation(False),
            context_builder=FakeContextBuilder(),
            actor=ScriptedActor(["unused"]),
            observer=ScriptedObserver([]),
            memory=FakeMemory(fail=True),
        )

        result = await processor.process(MESSAGE)

        self.assertFalse(result.participation.should_reply)

    async def test_no_reply_stores_user_message_without_calling_actor(self):
        repository = FakeRepository()
        actor = ScriptedActor(["unused"])
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(False),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=ScriptedObserver([]),
        )

        result = await processor.process(MESSAGE)

        self.assertIsNone(result.reply_text)
        self.assertEqual(actor.calls, [])
        self.assertEqual([item.role for item in repository.messages], ["user"])

    async def test_accepted_actor_response_is_persisted(self):
        repository = FakeRepository()
        actor = ScriptedActor(["Нормально!"])
        observer = ScriptedObserver([ReviewResult(ReviewVerdict.ACCEPT)])
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Нормально!")
        self.assertEqual([item.role for item in repository.messages], ["user", "assistant"])
        self.assertEqual(result.plan.actions[-1].payload["text"], "Нормально!")

    async def test_first_content_revision_is_reported_as_filtered(self):
        repository = FakeRepository()
        actor = ScriptedActor(["Слишком длинный ответ", "Короткий ответ"])
        observer = ScriptedObserver(
            [
                ReviewResult(
                    ReviewVerdict.REVISE,
                    violations=("too_long",),
                    revision_instruction="Сократи ответ",
                ),
                ReviewResult(ReviewVerdict.ACCEPT),
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        with self.assertLogs("ch00chka.application.processor", level="WARNING") as logs:
            result = await processor.process(MESSAGE)

        self.assertEqual(
            result.reply_text,
            "Filtered: ответ оказался длиннее, чем подходит для этого сообщения.",
        )
        self.assertEqual(actor.calls, [(None, None)])
        self.assertEqual(observer.calls, 1)
        self.assertEqual([item.role for item in repository.messages], ["user", "assistant"])
        self.assertIn("verdict=revise", logs.output[0])
        self.assertIn("violations=('too_long',)", logs.output[0])
        self.assertNotIn(MESSAGE.text, logs.output[0])

    async def test_second_rejection_blocks_response(self):
        repository = FakeRepository()
        actor = ScriptedActor([("draft", "length"), "still bad"])
        observer = ScriptedObserver(
            [
                ReviewResult(ReviewVerdict.REVISE, revision_instruction="fix"),
                ReviewResult(ReviewVerdict.REVISE, revision_instruction="fix again"),
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(
            result.reply_text,
            "Filtered: проверка не смогла подтвердить уместность ответа.",
        )
        self.assertEqual([item.role for item in repository.messages], ["user", "assistant"])
        self.assertEqual(len(actor.calls), 2)

    async def test_first_block_is_reported_as_filtered(self):
        repository = FakeRepository()
        actor = ScriptedActor(["draft"])
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=ScriptedObserver(
                [ReviewResult(ReviewVerdict.BLOCK, violations=("unsafe",))]
            ),
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(
            result.reply_text,
            "Filtered: в ответе есть неуместная грубость, травля или опасный совет.",
        )
        self.assertEqual(
            result.plan.actions[-1].payload["text"],
            "Filtered: в ответе есть неуместная грубость, травля или опасный совет.",
        )

    async def test_uncertain_facts_are_revised_instead_of_immediately_filtered(self):
        repository = FakeRepository()
        actor = ScriptedActor(
            ["Это точно так.", "Это вроде так, но могу ошибаться."]
        )
        observer = ScriptedObserver(
            [
                ReviewResult(
                    ReviewVerdict.REVISE,
                    violations=("add_uncertainty_disclaimer",),
                    revision_instruction=(
                        "Сохрани ответ, но добавь короткую оговорку о неуверенности."
                    ),
                ),
                ReviewResult(ReviewVerdict.ACCEPT),
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Это вроде так, но могу ошибаться.")
        self.assertEqual(len(actor.calls), 2)
        self.assertEqual(observer.calls, 2)

    async def test_unsupported_claim_is_revised_not_filtered(self):
        repository = FakeRepository()
        actor = ScriptedActor(
            ["Это точно так.", "Наверное, так, но могу ошибаться."]
        )
        observer = ScriptedObserver(
            [
                ReviewResult(
                    ReviewVerdict.BLOCK,
                    violations=("unsupported_factual_claim",),
                ),
                ReviewResult(ReviewVerdict.ACCEPT),
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Наверное, так, но могу ошибаться.")
        self.assertEqual(len(actor.calls), 2)
        self.assertEqual(observer.calls, 2)

    async def test_repeated_uncertainty_review_adds_disclaimer_instead_of_filtering(self):
        repository = FakeRepository()
        actor = ScriptedActor(["Это точно так.", "Похоже, это так"])
        observer = ScriptedObserver(
            [
                ReviewResult(
                    ReviewVerdict.REVISE,
                    violations=("add_uncertainty_disclaimer",),
                ),
                ReviewResult(
                    ReviewVerdict.BLOCK,
                    violations=("unsupported_factual_claim",),
                ),
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Похоже, это так. Но я могу ошибаться.")
        self.assertEqual(result.review.verdict, ReviewVerdict.ACCEPT)

    async def test_truncated_response_is_revised_even_when_observer_is_disabled(self):
        repository = FakeRepository()
        actor = ScriptedActor(
            [("Оборванный ответ", "length"), ("Законченный ответ.", "stop")]
        )
        observer = ScriptedObserver(
            [
                ReviewResult(
                    ReviewVerdict.REVISE,
                    violations=("output_truncated",),
                    revision_instruction="Ответь заново короче и закончи мысль",
                )
            ]
        )
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=observer,
            observer_enabled=False,
        )

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Законченный ответ.")
        self.assertEqual(observer.calls, 1)
        self.assertEqual(result.review.violations, ("truncation_recovered",))

    async def test_research_facts_are_added_to_actor_context(self):
        repository = FakeRepository()
        actor = ScriptedActor(["Это песня Ado."])
        processor = MessageProcessor(
            repository=repository,
            participation=FakeParticipation(True),
            context_builder=FakeContextBuilder(),
            actor=actor,
            observer=ScriptedObserver([ReviewResult(ReviewVerdict.ACCEPT)]),
            researcher=FakeResearcher(),
        )

        result = await processor.process(MESSAGE)

        self.assertTrue(actor.contexts[0].research_performed)
        self.assertEqual(actor.contexts[0].research_facts[0].title, "Ado — Show")
        self.assertIsNotNone(result.research)


if __name__ == "__main__":
    unittest.main()
