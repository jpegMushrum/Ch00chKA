from __future__ import annotations

import unittest

from ch00chka.ai.memory_router import RepositoryMemoryRouter
from ch00chka.domain import (
    ChatParticipant,
    NormalizedMessage,
    ParticipantAlias,
    ParticipantMemory,
    ReferencedMessage,
)


class FakeRepository:
    def __init__(self) -> None:
        self.participants = (
            ChatParticipant(1, "Alice Smith", "alice"),
            ChatParticipant(2, "Roman", "roman"),
            ChatParticipant(3, "Alice Jones", "alice_j"),
        )
        self.memories = {
            1: ParticipantMemory(1, "Alice Smith", "alice", ("Любит моды.",)),
            2: ParticipantMemory(2, "Roman", "roman", ("Играет в Create.",)),
            3: ParticipantMemory(3, "Alice Jones", "alice_j", ("Дизайнер.",)),
        }
        self.aliases = (
            ParticipantAlias(1, "Alice Smith", "telegram", 100),
            ParticipantAlias(1, "alice", "telegram", 100),
            ParticipantAlias(2, "Roman", "telegram", 100),
            ParticipantAlias(2, "roman", "telegram", 100),
            ParticipantAlias(2, "vassago", "telegram", 100),
            ParticipantAlias(2, "Рома", "chat_relation", 75),
            ParticipantAlias(3, "Alice Jones", "telegram", 100),
            ParticipantAlias(3, "alice_j", "telegram", 100),
        )
        self.requested_ids = ()

    async def list_participants(self, chat_id):
        return self.participants

    async def get_participant_memories(self, *, chat_id, user_ids):
        self.requested_ids = tuple(user_ids)
        return tuple(self.memories[user_id] for user_id in user_ids if user_id in self.memories)

    async def list_participant_aliases(self, chat_id):
        return self.aliases


def make_message(**overrides) -> NormalizedMessage:
    values = {
        "chat_id": 100,
        "message_id": 20,
        "user_id": 1,
        "user_name": "Alice Smith",
        "text": "Роман, а что думаешь?",
        "chat_type": "group",
        "bot_name": "Ch00chKA",
    }
    values.update(overrides)
    return NormalizedMessage(**values)


class RepositoryMemoryRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_author_reply_target_and_classifier_hint_in_priority_order(self):
        repository = FakeRepository()
        router = RepositoryMemoryRouter(repository=repository, max_profiles=3)
        message = make_message(
            reply_to_message=ReferencedMessage(
                message_id=19,
                user_name="Roman",
                text="Вот мои рельсы",
                user_id=2,
            )
        )

        route = await router.route(message, mentioned_participant_ids=(3, 999))

        self.assertEqual(route.participant_ids, (1, 2, 3))
        self.assertEqual(repository.requested_ids, (1, 2, 3))
        self.assertEqual([memory.user_id for memory in route.memories], [1, 2, 3])

    async def test_routes_unique_textual_mentions_without_loading_all_people(self):
        repository = FakeRepository()
        router = RepositoryMemoryRouter(repository=repository, max_profiles=2)

        route = await router.route(make_message(text="@roman, это твой проект?"))

        self.assertEqual(route.participant_ids, (1, 2))
        self.assertEqual(repository.requested_ids, (1, 2))

    async def test_routes_a_confirmed_historical_alias_to_its_user_id(self):
        repository = FakeRepository()
        router = RepositoryMemoryRouter(repository=repository, max_profiles=2)

        route = await router.route(make_message(text="Кто такой Рома?"))

        self.assertEqual(route.participant_ids, (1, 2))
        self.assertEqual(route.memories[1].display_name, "Roman")

    async def test_does_not_guess_ambiguous_first_name(self):
        repository = FakeRepository()
        router = RepositoryMemoryRouter(repository=repository, max_profiles=3)

        route = await router.route(make_message(text="Alice, ты тут?"))

        self.assertEqual(route.participant_ids, (1,))

    async def test_caps_each_profile_before_the_actor_sees_it(self):
        repository = FakeRepository()
        repository.memories[1] = ParticipantMemory(
            1,
            "Alice Smith",
            "alice",
            ("Первый факт.", "Второй факт.", "Третий факт."),
        )
        router = RepositoryMemoryRouter(
            repository=repository,
            max_profiles=1,
            max_facts_per_profile=2,
            max_profile_chars=100,
        )

        route = await router.route(make_message(text="привет"))

        self.assertEqual(route.memories[0].facts, ("Первый факт.", "Второй факт."))


if __name__ == "__main__":
    unittest.main()
