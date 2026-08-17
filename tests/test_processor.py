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
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[str | None] = []

    async def respond(
        self,
        context,
        *,
        revision_instruction=None,
        previous_response=None,
    ):
        self.calls.append((revision_instruction, previous_response))
        return ActorResponse(
            text=self.responses[len(self.calls) - 1],
            model="fake",
            prompt_version="test",
        )


class ScriptedObserver:
    def __init__(self, reviews: list[ReviewResult]) -> None:
        self.reviews = reviews
        self.calls = 0

    async def review(self, context, response):
        review = self.reviews[self.calls]
        self.calls += 1
        return review


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

    async def test_observer_can_request_only_one_revision(self):
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

        self.assertEqual(result.reply_text, "Короткий ответ")
        self.assertEqual(
            actor.calls,
            [(None, None), ("Сократи ответ", "Слишком длинный ответ")],
        )
        self.assertEqual(observer.calls, 2)

    async def test_second_rejection_blocks_response(self):
        repository = FakeRepository()
        actor = ScriptedActor(["draft", "still bad"])
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

        self.assertIsNone(result.reply_text)
        self.assertEqual([item.role for item in repository.messages], ["user"])
        self.assertEqual(len(actor.calls), 2)


if __name__ == "__main__":
    unittest.main()
