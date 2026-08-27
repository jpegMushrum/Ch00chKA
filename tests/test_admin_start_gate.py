from __future__ import annotations

import unittest
from types import SimpleNamespace

from ch00chka.presentation.telegram.access import AdminStartGate, is_start_command


class FakeMessage:
    def __init__(self, *, user_id: int, text: str, chat_id: int = 100) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.text = text
        self.caption = None
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


class AdminStartGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.handled: list[FakeMessage] = []

    async def _handler(self, event, data):
        self.handled.append(event)
        return "handled"

    async def test_messages_are_ignored_before_admin_start(self):
        gate = AdminStartGate(admin_id=42)
        message = FakeMessage(user_id=42, text="Обычное сообщение")

        result = await gate(self._handler, message, {})

        self.assertIsNone(result)
        self.assertEqual(self.handled, [])
        self.assertFalse(gate.is_active(100))

    async def test_unauthorized_start_is_silently_ignored(self):
        gate = AdminStartGate(admin_id=42)
        message = FakeMessage(user_id=13, text="/start")

        await gate(self._handler, message, {})

        self.assertFalse(gate.is_active(100))
        self.assertEqual(message.replies, [])
        self.assertEqual(self.handled, [])

    async def test_admin_start_opens_gate_for_following_messages(self):
        gate = AdminStartGate(admin_id=42)
        start = FakeMessage(user_id=42, text="/start@Ch00chkaBot payload")

        await gate(self._handler, start, {})
        ordinary = FakeMessage(user_id=13, text="Привет")
        result = await gate(self._handler, ordinary, {})

        self.assertTrue(gate.is_active(100))
        self.assertEqual(start.replies, ["Чоочка запущена."])
        self.assertEqual(result, "handled")
        self.assertEqual(self.handled, [ordinary])

    async def test_zero_configuration_keeps_previous_behavior(self):
        gate = AdminStartGate(admin_id=None)
        message = FakeMessage(user_id=13, text="Привет")

        result = await gate(self._handler, message, {})

        self.assertTrue(gate.is_active(100))
        self.assertEqual(result, "handled")

    async def test_start_opens_only_the_current_chat(self):
        gate = AdminStartGate(admin_id=42)
        await gate(
            self._handler,
            FakeMessage(user_id=42, text="/start", chat_id=100),
            {},
        )

        allowed = FakeMessage(user_id=13, text="Привет", chat_id=100)
        blocked = FakeMessage(user_id=13, text="Привет", chat_id=200)
        allowed_result = await gate(self._handler, allowed, {})
        blocked_result = await gate(self._handler, blocked, {})

        self.assertEqual(allowed_result, "handled")
        self.assertIsNone(blocked_result)
        self.assertTrue(gate.is_active(100))
        self.assertFalse(gate.is_active(200))
        self.assertEqual(self.handled, [allowed])

    def test_start_command_parser_does_not_match_prefixes(self):
        self.assertTrue(is_start_command(" /start "))
        self.assertTrue(is_start_command("/start@TestBot payload"))
        self.assertFalse(is_start_command("/starter"))
        self.assertFalse(is_start_command("текст /start"))


if __name__ == "__main__":
    unittest.main()
