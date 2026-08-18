from __future__ import annotations

import unittest

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.domain import (
    ActorResponse,
    ChatMessage,
    ConversationContext,
    NormalizedMessage,
    ReplyReason,
    ReviewVerdict,
)


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeRepository:
    async def recent_messages(self, *, chat_id, limit):
        return (ChatMessage("user", "Alice", "Предыдущее сообщение"),)


def make_message(**overrides) -> NormalizedMessage:
    values = {
        "chat_id": 1,
        "message_id": 2,
        "user_id": 3,
        "user_name": "Bob",
        "text": "Что думаешь?",
        "chat_type": "group",
        "bot_name": "Ch00chKA",
        "bot_username": "ch00chka_bot",
        "bot_aliases": ("чучка", "choochka"),
    }
    values.update(overrides)
    return NormalizedMessage(**values)


class ParticipationAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_mention_does_not_call_llm(self):
        gateway = FakeGateway("unused")
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(make_message(mentions_bot=True))

        self.assertTrue(result.should_reply)
        self.assertEqual(result.reason, ReplyReason.MENTIONED)
        self.assertEqual(gateway.calls, [])

    async def test_ambiguous_message_uses_structured_llm_decision(self):
        gateway = FakeGateway(
            '{"should_reply": true, "reason": "continuation", "confidence": 0.8}'
        )
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(make_message())

        self.assertTrue(result.should_reply)
        self.assertEqual(result.reason, ReplyReason.CONTINUATION)
        self.assertEqual(result.confidence, 0.8)
        self.assertIn("Предыдущее сообщение", gateway.calls[0]["messages"][1]["content"])
        self.assertIn("чучка", gateway.calls[0]["messages"][1]["content"])
        self.assertIn("Ch00chKA", gateway.calls[0]["messages"][1]["content"])

    async def test_string_false_is_not_treated_as_true(self):
        gateway = FakeGateway(
            '{"should_reply": "false", "reason": "no_value", "confidence": 1}'
        )
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(make_message())

        self.assertFalse(result.should_reply)


class ActorAndObserverTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self):
        return ConversationContext(
            personality="Говори коротко",
            summary="Обсуждали тесты",
            recent_messages=(ChatMessage("user", "Alice", "Старое сообщение"),),
            current_message=make_message(),
            prompt_version=PROMPT_VERSION,
        )

    async def test_actor_revision_contains_previous_draft(self):
        gateway = FakeGateway("Исправленный ответ")
        actor = LLMAgentActor(gateway=gateway, model="fake")

        response = await actor.respond(
            self.make_context(),
            revision_instruction="Сократи",
            previous_response="Слишком длинный черновик",
        )

        self.assertEqual(response.text, "Исправленный ответ")
        system_prompt = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("Слишком длинный черновик", system_prompt)
        self.assertIn("Сократи", system_prompt)
        self.assertIn("Не называй себя ботом", system_prompt)
        self.assertEqual(gateway.calls[0]["max_tokens"], 180)

    async def test_observer_returns_structured_review(self):
        gateway = FakeGateway(
            '{"verdict":"revise","violations":["too_long"],'
            '"revision_instruction":"Сократи"}'
        )
        observer = LLMAgentObserver(gateway=gateway, model="fake")

        result = await observer.review(
            self.make_context(),
            ActorResponse("Черновик", "fake", PROMPT_VERSION),
        )

        self.assertEqual(result.verdict, ReviewVerdict.REVISE)
        self.assertEqual(result.violations, ("too_long",))


if __name__ == "__main__":
    unittest.main()
