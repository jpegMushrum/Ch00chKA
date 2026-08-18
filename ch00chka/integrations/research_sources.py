from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

import aiohttp

from ch00chka.domain import ResearchFact, ResearchSource


_WIKIPEDIA_LANGUAGES = {"ru", "en", "ja"}
logger = logging.getLogger(__name__)


def _clean_text(value: object, *, limit: int = 700) -> str:
    text = html.unescape(re.sub(r"\s+", " ", str(value or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _public_http_url(value: object) -> str | None:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return None
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return None
    except ValueError:
        pass
    return url


class PublicResearchBackend:
    """Fixed-endpoint research sources that require no user account or API key."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        web_base_url: str | None = None,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._web_base_url = web_base_url.rstrip("/") if web_base_url else None

    async def search(
        self,
        *,
        query: str,
        source_types: Sequence[ResearchSource],
        language: str,
        limit: int,
    ) -> Sequence[ResearchFact]:
        tasks = []
        if ResearchSource.ENCYCLOPEDIA in source_types:
            tasks.append(self._search_wikipedia(query, language, limit))
        if ResearchSource.MUSIC in source_types:
            tasks.append(self._search_music(query, language, limit))
        if ResearchSource.WEB in source_types and self._web_base_url:
            tasks.append(self._search_web(query, language, limit))
        if not tasks:
            return ()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        facts: list[ResearchFact] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Research source failed: %s", result)
                continue
            facts.extend(result)
        return tuple(facts[:limit])

    async def _search_wikipedia(
        self,
        query: str,
        language: str,
        limit: int,
    ) -> tuple[ResearchFact, ...]:
        wiki_language = language if language in _WIKIPEDIA_LANGUAGES else "ru"
        endpoint = f"https://{wiki_language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": str(limit),
            "prop": "extracts|info",
            "exintro": "1",
            "explaintext": "1",
            "inprop": "url",
            "format": "json",
            "formatversion": "2",
        }
        payload = await self._get_json(endpoint, params=params)
        pages = payload.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            return ()

        facts = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            title = _clean_text(page.get("title"), limit=160)
            summary = _clean_text(page.get("extract"))
            url = str(page.get("fullurl") or "")
            if title and summary and url.startswith("https://"):
                facts.append(
                    ResearchFact(
                        source=ResearchSource.ENCYCLOPEDIA,
                        title=title,
                        summary=summary,
                        url=url,
                    )
                )
        return tuple(facts)

    async def _search_web(
        self,
        query: str,
        language: str,
        limit: int,
    ) -> tuple[ResearchFact, ...]:
        params = {
            "q": query,
            "format": "json",
            "language": {"ru": "ru-RU", "en": "en-US", "ja": "ja-JP"}.get(
                language,
                "all",
            ),
            "safesearch": "1",
        }
        payload = await self._get_json(
            f"{self._web_base_url}/search",
            params=params,
        )
        results = payload.get("results", [])
        if not isinstance(results, list):
            return ()

        facts = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"), limit=200)
            url = _public_http_url(item.get("url"))
            summary = _clean_text(item.get("content"))
            published_at = _clean_text(item.get("publishedDate"), limit=80) or None
            if not title or not url:
                continue
            if not summary:
                summary = f"Результат открытого веб-поиска: {title}"
            facts.append(
                ResearchFact(
                    source=ResearchSource.WEB,
                    title=title,
                    summary=summary,
                    url=url,
                    published_at=published_at,
                )
            )
            if len(facts) >= limit:
                break
        return tuple(facts)

    async def _search_music(
        self,
        query: str,
        language: str,
        limit: int,
    ) -> tuple[ResearchFact, ...]:
        params = {
            "term": query,
            "media": "music",
            "entity": "song",
            "limit": str(limit),
            "country": "US",
            "lang": "ja_jp" if language == "ja" else "en_us",
            "explicit": "Yes",
        }
        payload = await self._get_json("https://itunes.apple.com/search", params=params)
        results = payload.get("results", [])
        if not isinstance(results, list):
            return ()

        facts = []
        for item in results:
            if not isinstance(item, dict):
                continue
            track = _clean_text(item.get("trackName"), limit=160)
            artist = _clean_text(item.get("artistName"), limit=160)
            album = _clean_text(item.get("collectionName"), limit=160)
            genre = _clean_text(item.get("primaryGenreName"), limit=80)
            release_date = _clean_text(item.get("releaseDate"), limit=40) or None
            url = str(item.get("trackViewUrl") or "")
            if url.startswith("http://"):
                url = "https://" + url.removeprefix("http://")
            if not track or not artist or not url.startswith("https://"):
                continue
            details = [f"Исполнитель: {artist}"]
            if album:
                details.append(f"релиз: {album}")
            if genre:
                details.append(f"жанр: {genre}")
            if release_date:
                details.append(f"дата в каталоге: {release_date}")
            facts.append(
                ResearchFact(
                    source=ResearchSource.MUSIC,
                    title=f"{artist} — {track}",
                    summary="; ".join(details),
                    url=url,
                    published_at=release_date,
                )
            )
        return tuple(facts)

    async def _get_json(self, url: str, *, params: dict[str, str]) -> dict:
        headers = {
            "User-Agent": (
                "Ch00chKA/0.1 "
                "(https://github.com/jpegMushrum/Ch00chKA; Telegram bot)"
            )
        }
        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise ValueError("Research source returned a non-object response")
        return payload
