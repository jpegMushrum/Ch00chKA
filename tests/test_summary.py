from __future__ import annotations

import unittest

from ch00chka.ai.ports import LLMCompletion
from ch00chka.ai.summary import LLMAgentConversationMemory
from ch00chka.domain import SummarizableMessage, SummaryBatch


class FakeGateway:
    def __init__(self, text: str, finish_reason: str | None = "stop") -> None:
        self.text = text
        self.finish_reason = finish_reason
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMCompletion(self.text, self.finish_reason)


class FakeRepository:
    def __init__(self, batch: SummaryBatch | None) -> None:
        self.batch = batch
        self.saved = []

    async def get_summary_batch(self, **kwargs):
        return self.batch

    async def save_summary(self, **kwargs):
        self.saved.append(kwargs)


def make_memory(
    gateway,
    repository,
    *,
    max_chars=2000,
    retry_cooldown_seconds=900,
    clock=lambda: 100.0,
):
    return LLMAgentConversationMemory(
        gateway=gateway,
        model="fake-summary",
        repository=repository,
        keep_recent=30,
        min_batch_size=8,
        max_batch_size=60,
        max_chars=max_chars,
        retry_cooldown_seconds=retry_cooldown_seconds,
        clock=clock,
    )


class SummaryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_existing_summary_with_named_messages(self):
        repository = FakeRepository(
            SummaryBatch(
                current_summary="Alice любит японскую музыку.",
                messages=(
                    SummarizableMessage(10, "user", "Bob", "Я работаю дизайнером"),
                    SummarizableMessage(11, "assistant", "Ch00chKA", "Запомнила"),
                ),
            )
        )
        gateway = FakeGateway("Alice любит японскую музыку. Bob работает дизайнером.")

        updated = await make_memory(gateway, repository).refresh(42)

        self.assertTrue(updated)
        self.assertEqual(repository.saved[0]["through_message_id"], 11)
        self.assertIn("Alice любит", gateway.calls[0]["messages"][1]["content"])
        self.assertIn('"author": "Bob"', gateway.calls[0]["messages"][1]["content"])
        self.assertIn(
            '"max_summary_characters": 2000',
            gateway.calls[0]["messages"][1]["content"],
        )
        self.assertEqual(gateway.calls[0]["temperature"], 0.2)
        self.assertEqual(gateway.calls[0]["max_tokens"], 2000)

    async def test_does_not_call_model_without_eligible_batch(self):
        repository = FakeRepository(None)
        gateway = FakeGateway("unused")

        updated = await make_memory(gateway, repository).refresh(42)

        self.assertFalse(updated)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(repository.saved, [])

    async def test_incomplete_summary_does_not_advance_cursor(self):
        repository = FakeRepository(
            SummaryBatch(
                current_summary="",
                messages=(SummarizableMessage(5, "user", "Bob", "Факт"),),
            )
        )
        gateway = FakeGateway("Оборвано", "length")

        updated = await make_memory(gateway, repository).refresh(42)

        self.assertFalse(updated)
        self.assertEqual(repository.saved, [])

    async def test_incomplete_summary_is_not_retried_during_cooldown(self):
        repository = FakeRepository(
            SummaryBatch(
                current_summary="",
                messages=(SummarizableMessage(5, "user", "Bob", "Факт"),),
            )
        )
        gateway = FakeGateway("Оборвано", "length")
        memory = make_memory(gateway, repository)

        first = await memory.refresh(42)
        second = await memory.refresh(42)

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(repository.saved, [])

    async def test_retry_is_allowed_after_cooldown(self):
        now = [100.0]
        repository = FakeRepository(
            SummaryBatch(
                current_summary="",
                messages=(SummarizableMessage(5, "user", "Bob", "Факт"),),
            )
        )
        gateway = FakeGateway("Оборвано", "length")
        memory = make_memory(gateway, repository, clock=lambda: now[0])

        await memory.refresh(42)
        now[0] += 901
        await memory.refresh(42)

        self.assertEqual(len(gateway.calls), 2)
