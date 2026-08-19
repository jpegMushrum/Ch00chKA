from __future__ import annotations

import unittest

from ch00chka.presentation.telegram.formatting import markdown_to_telegram_html


class TelegramFormattingTests(unittest.TestCase):
    def test_renders_bold_heading_and_bullet_list(self):
        source = "**Фишка в том, что:**\n- Поезда собираются из блоков"

        result = markdown_to_telegram_html(source)

        self.assertEqual(
            result,
            "<b>Фишка в том, что:</b>\n• Поезда собираются из блоков",
        )

    def test_escapes_raw_html_and_renders_inline_code(self):
        result = markdown_to_telegram_html("<script> `a < b` & всё")

        self.assertEqual(
            result,
            "&lt;script&gt; <code>a &lt; b</code> &amp; всё",
        )

    def test_renders_fenced_code_block(self):
        result = markdown_to_telegram_html("```python\nprint('<ok>')\n```")

        self.assertEqual(
            result,
            '<pre><code class="language-python">print(\'&lt;ok&gt;\')</code></pre>',
        )

    def test_allows_http_links_but_not_unsafe_schemes(self):
        result = markdown_to_telegram_html(
            "[Wiki](https://example.com/a) [bad](javascript:alert(1))"
        )

        self.assertIn('<a href="https://example.com/a">Wiki</a>', result)
        self.assertIn("[bad](javascript:alert(1))", result)

    def test_unclosed_markers_remain_literal(self):
        self.assertEqual(markdown_to_telegram_html("**не закрыто"), "**не закрыто")


if __name__ == "__main__":
    unittest.main()
