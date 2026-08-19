from __future__ import annotations

import unittest

from config import Settings
from main import create_bot


def make_settings(**overrides) -> Settings:
    values = {
        "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "AI_api_token": "test-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


class BotFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_api_is_default(self):
        bot = create_bot(make_settings())
        try:
            self.assertIn("api.telegram.org", bot.session.api.base)
            self.assertFalse(bot.session.api.is_local)
        finally:
            await bot.session.close()

    async def test_custom_local_api_server_is_configured(self):
        bot = create_bot(
            make_settings(
                telegram_api_base_url="http://telegram-bot-api:8081/",
                telegram_api_mode="local",
                telegram_api_request_timeout_seconds=1800,
            )
        )
        try:
            self.assertEqual(
                bot.session.api.base,
                "http://telegram-bot-api:8081/bot{token}/{method}",
            )
            self.assertTrue(bot.session.api.is_local)
            self.assertEqual(bot.session.timeout, 1800)
        finally:
            await bot.session.close()

    async def test_local_mode_uses_internal_default_and_large_media_limits(self):
        settings = make_settings(telegram_api_mode="local")
        bot = create_bot(settings)
        try:
            self.assertEqual(
                bot.session.api.base,
                "http://telegram-bot-api:8081/bot{token}/{method}",
            )
            self.assertEqual(settings.effective_media_max_bytes, 1_900_000_000)
            self.assertEqual(settings.effective_media_max_duration_seconds, 14_400)
        finally:
            await bot.session.close()

    async def test_cloud_mode_ignores_local_endpoint_and_uses_cloud_limits(self):
        settings = make_settings(
            telegram_api_mode="cloud",
            telegram_api_base_url="http://telegram-bot-api:8081",
        )
        bot = create_bot(settings)
        try:
            self.assertIn("api.telegram.org", bot.session.api.base)
            self.assertEqual(settings.effective_media_max_bytes, 48_000_000)
            self.assertEqual(settings.effective_media_max_duration_seconds, 600)
        finally:
            await bot.session.close()
