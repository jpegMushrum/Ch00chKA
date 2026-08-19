from __future__ import annotations

import html
import re
from urllib.parse import urlsplit


_FENCE = re.compile(r"^\s*```(?P<language>[A-Za-z0-9_+.-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<content>.+)$")
_BULLET = re.compile(r"^(?P<indent>\s*)[-+*]\s+(?P<content>.*)$")
_QUOTE = re.compile(r"^\s*>\s?(?P<content>.*)$")


def markdown_to_telegram_html(text: str) -> str:
    """Render a conservative Markdown subset supported by Telegram HTML."""
    rendered: list[str] = []
    code_lines: list[str] | None = None
    code_language = ""

    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if code_lines is None:
                code_lines = []
                code_language = fence.group("language")
            else:
                rendered.append(_render_code_block(code_lines, code_language))
                code_lines = None
                code_language = ""
            continue

        if code_lines is not None:
            code_lines.append(line)
            continue

        heading = _HEADING.match(line)
        if heading:
            rendered.append(f"<b>{_render_inline(heading.group('content'))}</b>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            rendered.append(
                f"{bullet.group('indent')}• {_render_inline(bullet.group('content'))}"
            )
            continue

        quote = _QUOTE.match(line)
        if quote:
            rendered.append(
                f"<blockquote>{_render_inline(quote.group('content'))}</blockquote>"
            )
            continue

        rendered.append(_render_inline(line))

    if code_lines is not None:
        rendered.append(_render_code_block(code_lines, code_language))

    return "\n".join(rendered)


def _render_code_block(lines: list[str], language: str) -> str:
    content = html.escape("\n".join(lines), quote=False)
    safe_language = re.sub(r"[^A-Za-z0-9_+-]", "", language)
    if safe_language:
        return f'<pre><code class="language-{safe_language}">{content}</code></pre>'
    return f"<pre>{content}</pre>"


def _render_inline(text: str) -> str:
    output: list[str] = []
    position = 0
    while position < len(text):
        if text.startswith("**", position) or text.startswith("__", position):
            marker = text[position : position + 2]
            end = text.find(marker, position + 2)
            if end >= position + 2:
                output.append(f"<b>{_render_inline(text[position + 2 : end])}</b>")
                position = end + 2
                continue

        if text.startswith("~~", position):
            end = text.find("~~", position + 2)
            if end >= position + 2:
                output.append(f"<s>{_render_inline(text[position + 2 : end])}</s>")
                position = end + 2
                continue

        if text[position] == "`":
            end = text.find("`", position + 1)
            if end > position + 1:
                output.append(
                    f"<code>{html.escape(text[position + 1 : end], quote=False)}</code>"
                )
                position = end + 1
                continue

        if text[position] == "[":
            label_end = text.find("](", position + 1)
            if label_end > position + 1:
                url_end = text.find(")", label_end + 2)
                if url_end > label_end + 2:
                    url = text[label_end + 2 : url_end]
                    if _safe_link(url):
                        label = _render_inline(text[position + 1 : label_end])
                        output.append(
                            f'<a href="{html.escape(url, quote=True)}">{label}</a>'
                        )
                        position = url_end + 1
                        continue

        if text[position] == "*":
            end = text.find("*", position + 1)
            if end > position + 1:
                output.append(f"<i>{_render_inline(text[position + 1 : end])}</i>")
                position = end + 1
                continue

        output.append(html.escape(text[position], quote=False))
        position += 1

    return "".join(output)


def _safe_link(url: str) -> bool:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
