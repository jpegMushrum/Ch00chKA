from __future__ import annotations

import unittest

try:
    from ch00chka.presentation.telegram.router import _parse_alias_seeds
except ModuleNotFoundError:
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


if __name__ == "__main__":
    unittest.main()
