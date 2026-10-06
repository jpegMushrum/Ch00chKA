from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from aiogram.enums import MessageEntityType

    from ch00chka.domain import ChatFeature, ChatParticipant
    from ch00chka.presentation.telegram.router import (
        _extract_urls,
        _features_keyboard,
        _media_urls_for_features,
        _parse_alias_seeds,
        _participant_from_add_command,
        _participant_mention_chunks,
        _will_delete_source_message,
    )
except ModuleNotFoundError:
    ChatFeature = None
    ChatParticipant = None
    MessageEntityType = None
    _extract_urls = None
    _features_keyboard = None
    _media_urls_for_features = None
    _parse_alias_seeds = None
    _participant_from_add_command = None
    _participant_mention_chunks = None
    _will_delete_source_message = None


@unittest.skipIf(_parse_alias_seeds is None, "aiogram is not installed in this test runtime")
class RouterHelperTests(unittest.TestCase):
    def test_features_keyboard_reflects_current_states(self):
        states = {feature: True for feature in ChatFeature}
        states[ChatFeature.RESEARCH] = False

        markup = _features_keyboard(states)
        buttons = [button for row in markup.inline_keyboard for button in row]
        by_callback = {button.callback_data: button.text for button in buttons}

        self.assertEqual(len(buttons), len(ChatFeature))
        self.assertEqual(by_callback["feature:ai_responses"], "Ответы ИИ: ON")
        self.assertEqual(by_callback["feature:research"], "Поиск в интернете: OFF")
        self.assertEqual(by_callback["feature:delete_source_links"], "Удалять ссылки: ON")

    def test_delete_source_is_planned_only_when_all_links_are_enabled(self):
        states = {feature: True for feature in ChatFeature}
        urls = ("https://youtu.be/example",)

        self.assertTrue(
            _will_delete_source_message(
                states,
                media_urls=urls,
                supported_media_urls=urls,
            )
        )
        self.assertFalse(
            _will_delete_source_message(
                states,
                media_urls=(),
                supported_media_urls=urls,
            )
        )

    def test_supported_media_links_are_separated_before_ai_processing(self):
        states = {feature: True for feature in ChatFeature}
        urls = (
            "https://youtu.be/example",
            "https://example.com/ordinary-link",
            "https://www.tiktok.com/@creator/video/123",
        )

        supported, enabled = _media_urls_for_features(urls, states)

        self.assertEqual(
            supported,
            ("https://youtu.be/example", "https://www.tiktok.com/@creator/video/123"),
        )
        self.assertEqual(enabled, supported)

        states[ChatFeature.TIKTOK] = False
        supported, enabled = _media_urls_for_features(urls, states)

        self.assertEqual(enabled, ("https://youtu.be/example",))
        self.assertEqual(len(supported), 2)

    def test_alias_seeds_are_parsed_from_command(self):
        self.assertEqual(
            _parse_alias_seeds("/generate_aliases Чучка, чуч; choochka"),
            ("choochka", "чуч", "чучка"),
        )

    def test_empty_alias_command_is_rejected(self):
        self.assertEqual(_parse_alias_seeds("/generate_aliases"), ())

    def test_participant_mentions_are_html_safe(self):
        chunks = _participant_mention_chunks(
            (
                ChatParticipant(user_id=10, display_name="<Alice>"),
                ChatParticipant(user_id=20, display_name="Bob & Carol"),
            )
        )

        self.assertEqual(len(chunks), 1)
        self.assertIn('href="tg://user?id=10"', chunks[0])
        self.assertIn("&lt;Alice&gt;", chunks[0])
        self.assertIn("Bob &amp; Carol", chunks[0])

    def test_participant_mentions_are_split_into_safe_chunks(self):
        participants = tuple(
            ChatParticipant(user_id=user_id, display_name=f"User {user_id}")
            for user_id in range(1, 5)
        )

        chunks = _participant_mention_chunks(participants, max_length=65)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 65 for chunk in chunks))
        self.assertEqual(sum(chunk.count("tg://user?id=") for chunk in chunks), 4)

    def test_add_participant_can_use_reply(self):
        target = SimpleNamespace(
            id=77,
            full_name="Reply User",
            username="reply_user",
            is_bot=False,
        )
        message = SimpleNamespace(
            reply_to_message=SimpleNamespace(from_user=target),
            text="/add_mention",
            entities=(),
        )

        participant = _participant_from_add_command(message)

        self.assertEqual(
            participant,
            ChatParticipant(77, "Reply User", "reply_user"),
        )

    def test_add_participant_can_use_explicit_id_and_name(self):
        message = SimpleNamespace(
            reply_to_message=None,
            text="/add_mention 123456789 Alice Example",
            entities=(),
        )

        participant = _participant_from_add_command(message)

        self.assertEqual(
            participant,
            ChatParticipant(123456789, "Alice Example", None),
        )

    def test_add_participant_can_use_telegram_text_mention(self):
        target = SimpleNamespace(
            id=88,
            full_name="Mentioned User",
            username=None,
            is_bot=False,
        )
        entity = SimpleNamespace(
            type=MessageEntityType.TEXT_MENTION,
            user=target,
        )
        message = SimpleNamespace(
            reply_to_message=None,
            text="/add_mention Mentioned User",
            entities=(entity,),
        )

        participant = _participant_from_add_command(message)

        self.assertEqual(
            participant,
            ChatParticipant(88, "Mentioned User", None),
        )

    def test_plain_username_is_not_accepted_without_user_id(self):
        message = SimpleNamespace(
            reply_to_message=None,
            text="/add_mention @alice",
            entities=(),
        )

        self.assertIsNone(_participant_from_add_command(message))

    def _message(self, *, label, forwarded=True, video=True, photo=False):
        entity = SimpleNamespace(
            type=MessageEntityType.TEXT_LINK,
            url="https://www.instagram.com/p/example/",
            extract_from=lambda text: label,
        )
        return SimpleNamespace(
            text=None,
            caption=label + "Спасибо, что пользуетесь — @SaveAsBot'ом",
            entities=None,
            caption_entities=(entity,),
            forward_origin=object() if forwarded else None,
            forward_date=None,
            photo=(object(),) if photo else None,
            video=object() if video else None,
            animation=None,
            video_note=None,
            document=None,
            audio=None,
            voice=None,
            sticker=None,
        )

    def test_hidden_link_on_forwarded_video_is_ignored(self):
        message = self._message(label="\u200b")

        self.assertEqual(_extract_urls(message), ())

    def test_visible_link_on_forwarded_video_is_preserved(self):
        message = self._message(label="Instagram")

        self.assertEqual(
            _extract_urls(message),
            ("https://www.instagram.com/p/example/",),
        )

    def test_hidden_link_on_forwarded_photo_is_ignored(self):
        message = self._message(label="\u200b\u200b", video=False, photo=True)

        self.assertEqual(_extract_urls(message), ())

    def test_hidden_link_is_ignored_even_without_forward_metadata(self):
        message = self._message(label="\u200b", forwarded=False)

        self.assertEqual(_extract_urls(message), ())


if __name__ == "__main__":
    unittest.main()
