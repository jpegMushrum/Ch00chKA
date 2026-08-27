from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from aiogram.enums import MessageEntityType

    from ch00chka.domain import ChatParticipant
    from ch00chka.presentation.telegram.router import (
        _extract_urls,
        _parse_alias_seeds,
        _participant_mention_chunks,
    )
except ModuleNotFoundError:
    ChatParticipant = None
    MessageEntityType = None
    _extract_urls = None
    _parse_alias_seeds = None
    _participant_mention_chunks = None


@unittest.skipIf(_parse_alias_seeds is None, "aiogram is not installed in this test runtime")
class RouterHelperTests(unittest.TestCase):
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
