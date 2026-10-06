from __future__ import annotations

import unittest

from ch00chka.application.identities import ParticipantIdentityRecorder
from ch00chka.domain import ParticipantAlias


class FakeRepository:
    def __init__(self, aliases=()):
        self.aliases = aliases
        self.saved = []

    async def list_participant_aliases(self, chat_id):
        return self.aliases

    async def save_participant_aliases(self, **kwargs):
        self.saved.append(kwargs)


class ParticipantIdentityRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_a_self_introduction_for_the_author(self):
        repository = FakeRepository()
        recorder = ParticipantIdentityRecorder(repository=repository)

        await recorder.observe(
            chat_id=100,
            author_user_id=10,
            text="Меня зовут Рома",
        )

        self.assertEqual(
            repository.saved,
            [{
                "chat_id": 100,
                "user_id": 10,
                "aliases": ("Рома",),
                "source": "self_intro",
                "confidence": 95,
            }],
        )

    async def test_records_a_name_for_the_replied_to_participant(self):
        repository = FakeRepository()
        recorder = ParticipantIdentityRecorder(repository=repository)

        await recorder.observe(
            chat_id=100,
            author_user_id=10,
            reply_target_user_id=20,
            text="Это Рома",
        )

        self.assertEqual(repository.saved[0]["user_id"], 20)
        self.assertEqual(repository.saved[0]["aliases"], ("Рома",))
        self.assertEqual(repository.saved[0]["source"], "reply_relation")

    async def test_resolves_a_known_alias_in_a_textual_relation(self):
        repository = FakeRepository(
            (ParticipantAlias(20, "vassago", "telegram", 100),)
        )
        recorder = ParticipantIdentityRecorder(repository=repository)

        await recorder.observe(
            chat_id=100,
            author_user_id=10,
            text="vassago — это Рома",
        )

        self.assertEqual(repository.saved[0]["user_id"], 20)
        self.assertEqual(repository.saved[0]["aliases"], ("Рома",))
        self.assertEqual(repository.saved[0]["source"], "chat_relation")

    async def test_does_not_guess_an_ambiguous_subject(self):
        repository = FakeRepository(
            (
                ParticipantAlias(20, "vassago", "telegram", 100),
                ParticipantAlias(30, "vassago", "chat_relation", 75),
            )
        )
        recorder = ParticipantIdentityRecorder(repository=repository)

        await recorder.observe(
            chat_id=100,
            author_user_id=10,
            text="vassago — это Рома",
        )

        self.assertEqual(repository.saved, [])
