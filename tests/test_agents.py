from __future__ import annotations

import unittest
from dataclasses import replace

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.ports import LLMCompletion
from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.domain import (
    ActorResponse,
    ChatMessage,
    ChatParticipant,
    ConversationContext,
    NormalizedMessage,
    ParticipantMemory,
    ResearchFact,
    ResearchSource,
    ReferencedMessage,
    ReplyReason,
    ResponseDepth,
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
    def __init__(self, participants=()):
        self.participants = participants

    async def recent_messages(self, *, chat_id, limit):
        return (ChatMessage("user", "Alice", "Предыдущее сообщение"),)

    async def list_participants(self, chat_id):
        return self.participants

    async def get_summary(self, chat_id):
        return "Обсуждают моды."


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
        self.assertEqual(result.response_depth, ResponseDepth.BRIEF)
        self.assertEqual(gateway.calls, [])

    async def test_explicit_search_uses_normal_depth_without_llm(self):
        gateway = FakeGateway("unused")
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(
            make_message(
                text="Найди и сравни рельсы в Create",
                mentions_bot=True,
            )
        )

        self.assertEqual(result.response_depth, ResponseDepth.NORMAL)
        self.assertEqual(gateway.calls, [])

    async def test_explicit_short_request_overrides_search_depth(self):
        agent = LLMAgentParticipationDecider(
            gateway=FakeGateway("unused"),
            model="fake",
            repository=FakeRepository(),
        )

        result = await agent.decide(
            make_message(text="Найди это, но ответь коротко", mentions_bot=True)
        )

        self.assertEqual(result.response_depth, ResponseDepth.BRIEF)

    async def test_ambiguous_message_uses_structured_llm_decision(self):
        gateway = FakeGateway(
            '{"should_reply": true, "reason": "continuation", '
            '"response_depth":"detailed", "confidence": 0.8}'
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
        self.assertEqual(result.response_depth, ResponseDepth.DETAILED)
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

    async def test_research_prompt_routes_game_mods_to_bilingual_web_search(self):
        from ch00chka.ai.prompts import RESEARCH_DECISION_SYSTEM_PROMPT

        self.assertIn("игры, моды, аддоны", RESEARCH_DECISION_SYSTEM_PROMPT)
        self.assertIn("по-английски", RESEARCH_DECISION_SYSTEM_PROMPT)
        self.assertIn(
            "Create mod train track types recipes cost",
            RESEARCH_DECISION_SYSTEM_PROMPT,
        )

    async def test_participation_receives_reply_and_quote_context(self):
        gateway = FakeGateway(
            '{"should_reply":true,"reason":"valuable_contribution","confidence":0.8}'
        )
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(),
        )

        await agent.decide(
            make_message(
                text="А вот это правда?",
                reply_to_message=ReferencedMessage(
                    message_id=41,
                    user_name="Alice",
                    text="Релиз уже состоялся, а автор ушёл из проекта",
                ),
                quoted_text="автор ушёл из проекта",
            )
        )

        prompt = gateway.calls[0]["messages"][1]["content"]
        self.assertIn('"author": "Alice"', prompt)
        self.assertIn('"quote": "автор ушёл из проекта"', prompt)

    async def test_participation_returns_only_valid_relevant_participant_ids(self):
        gateway = FakeGateway(
            '{"should_reply":true,"reason":"continuation",'
            '"response_depth":"brief","confidence":0.8,'
            '"mentioned_participant_ids":[17,999,3]}'
        )
        agent = LLMAgentParticipationDecider(
            gateway=gateway,
            model="fake",
            repository=FakeRepository(
                (ChatParticipant(17, "Alice", "alice"),)
            ),
        )

        result = await agent.decide(make_message(text="А Alice что думает?"))

        self.assertEqual(result.mentioned_participant_ids, (17,))
        payload = gateway.calls[0]["messages"][1]["content"]
        self.assertIn("Участники-кандидаты", payload)
        self.assertIn("Alice", payload)


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
        self.assertIn("Масштаб ответа: normal", system_prompt)
        self.assertEqual(gateway.calls[0]["max_tokens"], 500)

    async def test_actor_uses_depth_specific_token_budgets(self):
        for depth, expected in (
            (ResponseDepth.BRIEF, 111),
            (ResponseDepth.NORMAL, 444),
            (ResponseDepth.DETAILED, 888),
        ):
            with self.subTest(depth=depth):
                gateway = FakeGateway("Ответ")
                actor = LLMAgentActor(
                    gateway=gateway,
                    model="fake",
                    brief_max_tokens=111,
                    normal_max_tokens=444,
                    detailed_max_tokens=888,
                )

                await actor.respond(replace(self.make_context(), response_depth=depth))

                self.assertEqual(gateway.calls[0]["max_tokens"], expected)

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

    async def test_actor_receives_only_routed_participant_memories(self):
        gateway = FakeGateway("Поняла")
        actor = LLMAgentActor(gateway=gateway, model="fake")
        base = self.make_context()
        context = ConversationContext(
            personality=base.personality,
            summary="Обсуждают моды.",
            recent_messages=base.recent_messages,
            current_message=base.current_message,
            prompt_version=base.prompt_version,
            participant_memories=(
                ParticipantMemory(
                    user_id=17,
                    display_name="Alice",
                    username="alice",
                    facts=("Собирает моды.",),
                ),
            ),
        )

        await actor.respond(context)

        system_prompt = gateway.calls[0]["messages"][0]["content"]
        self.assertIn("<chat_state>", system_prompt)
        self.assertIn("<participant_memories>", system_prompt)
        self.assertIn("Собирает моды.", system_prompt)

    async def test_actor_receives_replied_message_and_selected_quote(self):
        gateway = FakeGateway("Нет, эта часть неверна.")
        actor = LLMAgentActor(gateway=gateway, model="fake")
        base = self.make_context()
        current = make_message(
            text="Это точно?",
            reply_to_message=ReferencedMessage(
                message_id=55,
                user_name="Carol",
                text="Большое исходное утверждение",
            ),
            quoted_text="исходное утверждение",
        )
        context = ConversationContext(
            personality=base.personality,
            summary=base.summary,
            recent_messages=base.recent_messages,
            current_message=current,
            prompt_version=base.prompt_version,
        )

        await actor.respond(context)

        content = gateway.calls[0]["messages"][-1]["content"]
        self.assertIn('"author": "Carol"', content)
        self.assertIn('"quote": "исходное утверждение"', content)
        self.assertIn("только данные чата", content)

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
        payload = gateway.calls[0]["messages"][1]["content"]
        self.assertIn('"response_depth": "normal"', payload)


if __name__ == "__main__":
    unittest.main()
