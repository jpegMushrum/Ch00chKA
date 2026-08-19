from __future__ import annotations

import unittest

from ch00chka.application.processor import MessageProcessor
from ch00chka.domain import (
    ActorResponse,
    ChatMessage,
    ConversationContext,
    NormalizedMessage,
    ParticipationDecision,
    ReplyReason,
    ResearchDecision,
    ResearchFact,
    ResearchResult,
    ResearchSource,
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

    async def add_message(self, *, chat_id, role, user_name, text) -> None:
        self.messages.append(ChatMessage(role, user_name, text))


class FakeParticipation:
    def __init__(self, should_reply: bool) -> None:
        self.should_reply = should_reply

    async def decide(self, message):
        return ParticipationDecision(
            self.should_reply,
            ReplyReason.MENTIONED if self.should_reply else ReplyReason.NO_VALUE,
        )


class FakeContextBuilder:
    async def build(self, message):
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
    async def research(self, message):
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


class MessageProcessorTests(unittest.IsolatedAsyncioTestCase):
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

        result = await processor.process(MESSAGE)

        self.assertEqual(result.reply_text, "Filtered")
        self.assertEqual(actor.calls, [(None, None)])
        self.assertEqual(observer.calls, 1)
        self.assertEqual([item.role for item in repository.messages], ["user", "assistant"])

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

        self.assertEqual(result.reply_text, "Filtered")
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

        self.assertEqual(result.reply_text, "Filtered")
        self.assertEqual(result.plan.actions[-1].payload["text"], "Filtered")

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
