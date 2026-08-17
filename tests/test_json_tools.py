import unittest

from ch00chka.ai.json_tools import parse_json_object


class ParseJsonObjectTests(unittest.TestCase):
    def test_parses_fenced_json(self):
        self.assertEqual(
            parse_json_object('```json\n{"answer": true}\n```'),
            {"answer": True},
        )

    def test_rejects_missing_object(self):
        with self.assertRaises(ValueError):
            parse_json_object("not json")


if __name__ == "__main__":
    unittest.main()
