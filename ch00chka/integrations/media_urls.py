from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse


class MediaPlatform(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


_PLATFORM_DOMAINS: dict[MediaPlatform, tuple[str, ...]] = {
    MediaPlatform.YOUTUBE: ("youtube.com", "youtu.be"),
    MediaPlatform.TIKTOK: ("tiktok.com",),
    MediaPlatform.INSTAGRAM: ("instagram.com",),
}


def _is_domain(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def detect_media_platform(url: str) -> MediaPlatform | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    hostname = parsed.hostname.casefold().rstrip(".")
    for platform, domains in _PLATFORM_DOMAINS.items():
        if any(_is_domain(hostname, domain) for domain in domains):
            return platform
    return None
