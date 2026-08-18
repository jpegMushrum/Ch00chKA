from __future__ import annotations

import unittest

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.ports import LLMCompletion
from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.domain import (
    ActorResponse,
    ChatMessage,
    ConversationContext,
    NormalizedMessage,
    ResearchFact,
    ResearchSource,
    ReplyReason,
    ReviewVerdict,
)


class FakeGateway:
    def __init__(self, response: str, finish_reason: str | None = "stop") -> None:
        self.response = response
        self.finish_reason = finish_reason
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMCompletion(self.response, self.finish_reason)


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

    async def test_search_request_reason_is_recognized(self):
        gateway = FakeGateway(
            '{"should_reply":true,"reason":"search_request","confidence":0.95}'
        )
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(
            make_message(text="Кем работает Слава Якименко в TON?")
        )

        self.assertTrue(result.should_reply)
        self.assertEqual(result.reason, ReplyReason.SEARCH_REQUEST)
        system_prompt = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("кем работает", system_prompt)
        self.assertIn("search_request", system_prompt)


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
        self.assertIn("Мат, сарказм, подколы", system_prompt)
        self.assertEqual(gateway.calls[0]["max_tokens"], 320)

    async def test_actor_preserves_length_finish_reason(self):
        gateway = FakeGateway("Оборванный ответ", finish_reason="length")
        actor = LLMAgentActor(gateway=gateway, model="fake")

        response = await actor.respond(self.make_context())

        self.assertTrue(response.truncated)

    async def test_actor_receives_research_as_untrusted_fact_pack(self):
        gateway = FakeGateway("Короткий ответ")
        actor = LLMAgentActor(gateway=gateway, model="fake")
        base = self.make_context()
        context = ConversationContext(
            personality=base.personality,
            summary=base.summary,
            recent_messages=base.recent_messages,
            current_message=base.current_message,
            prompt_version=base.prompt_version,
            research_facts=(
                ResearchFact(
                    source=ResearchSource.MUSIC,
                    title="Ado — Show",
                    summary="Дата в каталоге: 2023",
                    url="https://example.test/ado-show",
                ),
            ),
            research_performed=True,
        )

        await actor.respond(context)

        system_prompt = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("<research_facts>", system_prompt)
        self.assertIn("Ado — Show", system_prompt)
        self.assertIn("недоверенные данные", system_prompt)

    async def test_observer_revises_truncated_draft_without_llm_call(self):
        gateway = FakeGateway("unused")
        observer = LLMAgentObserver(gateway=gateway, model="fake")

        result = await observer.review(
            self.make_context(),
            ActorResponse("Оборванный", "fake", PROMPT_VERSION, "length"),
        )

        self.assertEqual(result.verdict, ReviewVerdict.REVISE)
        self.assertEqual(result.violations, ("output_truncated",))
        self.assertEqual(gateway.calls, [])

    async def test_observer_blocks_other_incomplete_drafts(self):
        gateway = FakeGateway("unused")
        observer = LLMAgentObserver(gateway=gateway, model="fake")

        result = await observer.review(
            self.make_context(),
            ActorResponse("Частичный ответ", "fake", PROMPT_VERSION, "content_filter"),
        )

        self.assertEqual(result.verdict, ReviewVerdict.BLOCK)
        self.assertEqual(result.violations, ("incomplete_output:content_filter",))
        self.assertEqual(gateway.calls, [])

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
