from __future__ import annotations

import unittest

from ch00chka.ai.aliases import BotAliasRegistry, LLMAliasGenerator
from ch00chka.ai.ports import LLMCompletion


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMCompletion(self.response, "stop")


class AliasRegistryTests(unittest.TestCase):
    def test_matches_generated_name_as_a_whole_word(self):
        registry = BotAliasRegistry(("Чучка", "Ch00chKA"))

        self.assertTrue(registry.matches("Чучка, ты тут?"))
        self.assertTrue(registry.matches("эй, @ch00chka!"))
        self.assertFalse(registry.matches("Это не-чучкаобразное слово"))

    def test_rejects_generic_aliases(self):
        registry = BotAliasRegistry(("бот", "ИИ", "Чуч"))

        self.assertEqual(registry.aliases, ("чуч",))

    def test_matches_cjk_alias_without_spaces(self):
        registry = BotAliasRegistry(("チュチカ",))

        self.assertTrue(registry.matches("チュチカ元気？"))


class AliasGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_and_sanitizes_model_aliases(self):
        gateway = FakeGateway(
            '{"aliases":["Чучка"," choochka ","бот",42,"Чучка"]}'
        )
        generator = LLMAliasGenerator(gateway=gateway, model="fake")

        aliases = await generator.generate(
            bot_name="Ch00chKA",
            bot_username="ch00chka_bot",
            seed_aliases=("чуч",),
        )

        self.assertEqual(aliases, ("чуч", "choochka", "чучка"))
        self.assertEqual(gateway.calls[0]["temperature"], 0.2)
        self.assertIn("чуч", gateway.calls[0]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
