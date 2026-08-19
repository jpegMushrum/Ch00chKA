from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    from aiogram.enums import MessageEntityType

    from ch00chka.presentation.telegram.router import _extract_urls, _parse_alias_seeds
except ModuleNotFoundError:
    MessageEntityType = None
    _extract_urls = None
    _parse_alias_seeds = None


@unittest.skipIf(_parse_alias_seeds is None, "aiogram is not installed in this test runtime")
class RouterHelperTests(unittest.TestCase):
    def test_alias_seeds_are_parsed_from_command(self):
        self.assertEqual(
            _parse_alias_seeds("/generate_aliases Чучка, чуч; choochka"),
            ("choochka", "чуч", "чучка"),
        )

    def test_empty_alias_command_is_rejected(self):
        self.assertEqual(_parse_alias_seeds("/generate_aliases"), ())

    def _message(self, *, label, forwarded=True, video=True):
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
            video=object() if video else None,
            animation=None,
            video_note=None,
            document=None,
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

    def test_hidden_link_without_forwarded_video_is_preserved(self):
        message = self._message(label="\u200b", forwarded=False)

        self.assertEqual(
            _extract_urls(message),
            ("https://www.instagram.com/p/example/",),
        )


if __name__ == "__main__":
    unittest.main()
