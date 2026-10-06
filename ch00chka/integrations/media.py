from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import AsyncIterator, Mapping

import yt_dlp
from aiogram import types
from aiogram.types import FSInputFile
from yt_dlp.utils import DownloadError

from ch00chka.domain import MediaDeliveryResult
from ch00chka.integrations.media_urls import MediaPlatform, detect_media_platform


logger = logging.getLogger(__name__)


class MediaDownloadError(RuntimeError):
    pass


class MediaTooLargeError(MediaDownloadError):
    pass


class MediaTooLongError(MediaDownloadError):
    pass


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class DownloadedMediaItem:
    path: Path
    kind: MediaKind


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    items: tuple[DownloadedMediaItem, ...]
    platform: MediaPlatform
    title: str | None = None


_PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".webm"})
_MAX_MEDIA_GROUP_ITEMS = 10
_DEFAULT_MAX_MEDIA_ITEMS = 50
_GALLERY_DL_LOG_LIMIT = 4_000
_SENSITIVE_HEADER = re.compile(
    r"(?im)^(?:cookie|set-cookie|authorization|proxy-authorization)\s*:\s*.*$"
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(sessionid|csrftoken|ds_user_id|ig_did|ttwid|msToken|token|"
    r"password|authorization)\b(\s*[:=]\s*)([^\s,;]+)"
)


def _media_kind(path: Path) -> MediaKind:
    suffix = path.suffix.casefold()
    if suffix in _PHOTO_SUFFIXES:
        return MediaKind.PHOTO
    if suffix in _VIDEO_SUFFIXES:
        return MediaKind.VIDEO
    return MediaKind.DOCUMENT


def _safe_gallery_dl_output(output: str | bytes | None) -> str:
    """Keep useful diagnostics without ever putting session values in logs."""
    if output is None:
        return "<empty>"
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    output = _SENSITIVE_HEADER.sub("<redacted sensitive header>", output)
    output = _SENSITIVE_VALUE.sub(r"\1\2<redacted>", output)
    output = output.strip()
    if not output:
        return "<empty>"
    if len(output) > _GALLERY_DL_LOG_LIMIT:
        return f"…{output[-_GALLERY_DL_LOG_LIMIT:]}"
    return output


class _YtDlpLogger:
    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logger.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logger.error("yt-dlp: %s", message)


class YtDlpMediaDownloader:
    def __init__(
        self,
        *,
        temp_dir: str,
        max_bytes: int,
        max_duration_seconds: int,
        max_concurrent_downloads: int,
        max_items: int = _DEFAULT_MAX_MEDIA_ITEMS,
        proxy_url: str | None = None,
        cookies_file: str | None = None,
        cookies_files: Mapping[MediaPlatform, str] | None = None,
        cookie_jar_dir: str | None = None,
        reset_cookie_jars: bool = False,
    ) -> None:
        self._temp_dir = Path(temp_dir).resolve()
        self._cache_dir = self._temp_dir / "cache"
        self._max_bytes = max_bytes
        self._max_duration_seconds = max_duration_seconds
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self._max_items = max_items
        self._proxy_url = proxy_url
        self._cookies_file = cookies_file
        self._cookies_files = {
            platform: path.strip()
            for platform, path in (cookies_files or {}).items()
            if path and path.strip()
        }
        self._cookie_jar_dir = (
            Path(cookie_jar_dir).resolve() if cookie_jar_dir and cookie_jar_dir.strip() else None
        )
        self._cookie_locks = {
            platform: threading.Lock() for platform in MediaPlatform
        }
        # A reset is consumed after its first successful seed in this process;
        # concurrent downloads must never replace the same platform jar together.
        self._cookie_jars_to_reset = (
            set(MediaPlatform) if reset_cookie_jars else set()
        )
        self._semaphore = asyncio.Semaphore(max_concurrent_downloads)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def download(self, url: str) -> AsyncIterator[DownloadedMedia]:
        platform = detect_media_platform(url)
        if platform is None:
            raise MediaDownloadError("Unsupported media URL")

        work_dir = Path(tempfile.mkdtemp(prefix=f"{platform}-", dir=self._temp_dir))
        try:
            async with self._semaphore:
                media = await asyncio.to_thread(
                    self._download_sync,
                    url,
                    platform,
                    work_dir,
                )
            yield media
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _download_sync(
        self,
        url: str,
        platform: MediaPlatform,
        work_dir: Path,
    ) -> DownloadedMedia:
        # yt-dlp saves cookies as it closes. Serializing one platform prevents
        # two downloads from corrupting its persistent Netscape cookie jar.
        with self._cookie_locks[platform]:
            return self._download_sync_locked(url, platform, work_dir)

    def _download_sync_locked(
        self,
        url: str,
        platform: MediaPlatform,
        work_dir: Path,
    ) -> DownloadedMedia:
        duration_marker = "MEDIA_DURATION_LIMIT"

        def match_filter(info: dict, *, incomplete: bool) -> str | None:
            duration = info.get("duration")
            if duration and duration > self._max_duration_seconds:
                return f"{duration_marker}:{duration}"
            return None

        audio_budget = min(384_000_000, self._max_bytes // 4)
        video_budget = self._max_bytes - audio_budget
        format_selector = (
            f"bestvideo[height<=720][filesize_approx<={video_budget}]"
            f"+bestaudio[filesize_approx<={audio_budget}]/"
            f"best[height<=720][filesize_approx<={self._max_bytes}]/"
            "best[height<=480]/best"
        )
        supports_gallery = platform in {
            MediaPlatform.TIKTOK,
            MediaPlatform.INSTAGRAM,
        }
        options = {
            "format": format_selector,
            "format_sort": ["vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            # A carousel creates several yt-dlp entries. autonumber keeps their
            # source order and, unlike a fixed filename, avoids overwriting them.
            "outtmpl": str(work_dir / "media_%(autonumber)03d.%(ext)s"),
            "cachedir": str(self._cache_dir),
            # Instagram posts and TikTok photo mode are exposed as playlists by
            # their extractors. A YouTube playlist should still mean one video.
            "noplaylist": not supports_gallery,
            "playlistend": self._max_items if supports_gallery else 1,
            "max_filesize": self._max_bytes,
            "match_filter": match_filter,
            "socket_timeout": 30,
            "retries": 3,
            "extractor_retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 2,
            "js_runtimes": {"node": {}},
            "remote_components": ["ejs:github"],
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            ],
            "logger": _YtDlpLogger(),
            "quiet": True,
            "no_warnings": False,
        }
        if self._proxy_url:
            options["proxy"] = self._proxy_url
        cookies_file = self._cookie_file_for_platform(platform, work_dir)
        if cookies_file:
            options["cookiefile"] = cookies_file

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as exc:
            if duration_marker in str(exc):
                raise MediaTooLongError(
                    f"Video exceeds {self._max_duration_seconds} seconds"
                ) from exc
            if supports_gallery:
                return self._download_images_with_gallery_dl(
                    url=url,
                    platform=platform,
                    work_dir=work_dir,
                    original_error=exc,
                    cookies_file=cookies_file,
                )
            raise MediaDownloadError(str(exc)) from exc

        candidates = self._media_candidates(work_dir)
        if not candidates:
            if isinstance(info, dict):
                reported_total = info.get("filesize") or info.get("filesize_approx")
                requested = info.get("requested_downloads")
                if (
                    not isinstance(reported_total, (int, float))
                    and isinstance(requested, list)
                ):
                    reported_total = sum(
                        value
                        for item in requested
                        if isinstance(item, dict)
                        for value in (item.get("filesize") or item.get("filesize_approx"),)
                        if isinstance(value, (int, float))
                    )
                if isinstance(reported_total, (int, float)) and reported_total > self._max_bytes:
                    raise MediaTooLargeError(f"Media exceeds {self._max_bytes} bytes")
            if supports_gallery:
                return self._download_images_with_gallery_dl(
                    url=url,
                    platform=platform,
                    work_dir=work_dir,
                    cookies_file=cookies_file,
                )
            raise MediaDownloadError("yt-dlp did not create a media file")

        total_size = sum(path.stat().st_size for path in candidates)
        if total_size > self._max_bytes:
            raise MediaTooLargeError(f"Media exceeds {self._max_bytes} bytes")

        title = info.get("title") if isinstance(info, dict) else None
        return DownloadedMedia(
            items=tuple(
                DownloadedMediaItem(path=path, kind=_media_kind(path))
                for path in candidates
            ),
            platform=platform,
            title=title if isinstance(title, str) else None,
        )

    def _download_images_with_gallery_dl(
        self,
        *,
        url: str,
        platform: MediaPlatform,
        work_dir: Path,
        original_error: Exception | None = None,
        cookies_file: str | None = None,
    ) -> DownloadedMedia:
        """Download image posts when yt-dlp cannot expose their image URLs."""
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--destination",
            str(work_dir),
            "--filename",
            "media_{num:>03}.{extension}",
            "--range",
            f"1-{self._max_items}",
            "--filesize-max",
            str(self._max_bytes),
            "-o",
            "cache.file=:memory:",
            "-o",
            "extractor.cookies-update=false",
            "-o",
            "output.progress=false",
            "-o",
            "extractor.instagram.videos=false",
            "-o",
            "extractor.instagram.audio=false",
            "-o",
            "extractor.tiktok.videos=false",
            "-o",
            "extractor.tiktok.audio=false",
        ]
        if self._proxy_url:
            command.extend(("-o", f"extractor.proxy={self._proxy_url}"))
        if cookies_file:
            command.extend(("--cookies", cookies_file))
        command.append(url)

        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "gallery-dl timed out after %s seconds for %s; stdout=%s; stderr=%s",
                exc.timeout,
                platform,
                _safe_gallery_dl_output(exc.stdout),
                _safe_gallery_dl_output(exc.stderr),
            )
            raise MediaDownloadError("gallery-dl could not download image media") from exc
        except OSError as exc:
            logger.warning("gallery-dl could not start for %s: %s", platform, exc)
            raise MediaDownloadError("gallery-dl could not download image media") from exc

        candidates = self._media_candidates(work_dir)
        if result.returncode or not candidates:
            logger.warning(
                "gallery-dl result for %s: exit_code=%s, files=%s; stdout=%s; stderr=%s",
                platform,
                result.returncode,
                len(candidates),
                _safe_gallery_dl_output(getattr(result, "stdout", None)),
                _safe_gallery_dl_output(getattr(result, "stderr", None)),
            )
        if not candidates:
            message = "gallery-dl did not create an image file"
            if original_error is not None:
                message = f"{original_error}; {message}"
            raise MediaDownloadError(message)

        total_size = sum(path.stat().st_size for path in candidates)
        if total_size > self._max_bytes:
            raise MediaTooLargeError(f"Media exceeds {self._max_bytes} bytes")

        return DownloadedMedia(
            items=tuple(
                DownloadedMediaItem(path=path, kind=_media_kind(path))
                for path in candidates
            ),
            platform=platform,
        )

    @staticmethod
    def _media_candidates(work_dir: Path) -> list[Path]:
        return sorted(
            (
                path
                for path in work_dir.rglob("*")
                if path.is_file()
                and ".cookies" not in path.relative_to(work_dir).parts
                and not path.name.endswith((".part", ".ytdl", ".json"))
            ),
            key=lambda item: str(item.relative_to(work_dir)),
        )

    def _cookie_file_for_platform(
        self,
        platform: MediaPlatform,
        work_dir: Path,
    ) -> str | None:
        source = self._cookies_files.get(platform) or self._cookies_file
        if self._cookie_jar_dir is None:
            return self._temporary_cookie_file(source, work_dir) if source else None

        destination = self._cookie_jar_dir / f"{platform.value}-cookies.txt"
        needs_reset = platform in self._cookie_jars_to_reset
        if destination.is_file() and not needs_reset:
            return str(destination)
        if not source:
            if destination.is_file():
                logger.warning(
                    "Cookie jar reset skipped for %s: no source export is configured",
                    platform,
                )
                return str(destination)
            return None

        self._seed_cookie_jar(source, destination)
        self._cookie_jars_to_reset.discard(platform)
        logger.info("Cookie jar initialized for %s", platform)
        return str(destination)

    @staticmethod
    def _seed_cookie_jar(source: str, destination: Path) -> None:
        """Atomically seed a writable persistent jar from a read-only export."""
        staging = destination.with_suffix(f"{destination.suffix}.new")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, staging)
            staging.chmod(0o600)
            staging.replace(destination)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise MediaDownloadError("Could not prepare media cookies") from exc

    @staticmethod
    def _temporary_cookie_file(source: str, work_dir: Path) -> str:
        """Compatibility fallback when persistent cookie jars are disabled."""
        destination = work_dir / ".cookies" / "cookies.txt"
        try:
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, destination)
        except OSError as exc:
            raise MediaDownloadError("Could not prepare media cookies") from exc
        return str(destination)


class YtDlpMediaAdapter:
    def __init__(
        self,
        *,
        downloader: YtDlpMediaDownloader,
        upload_chunk_size: int = 1_048_576,
    ) -> None:
        self._downloader = downloader
        self._upload_chunk_size = upload_chunk_size

    async def handle(
        self,
        message: types.Message,
        urls: tuple[str, ...],
        *,
        reply_to_source: bool = True,
    ) -> MediaDeliveryResult:
        delivered_urls: list[str] = []
        for url in urls:
            platform = detect_media_platform(url)
            if platform is None:
                continue

            try:
                async with self._downloader.download(url) as media:
                    await self._send_media(
                        message,
                        media,
                        reply_to_source=reply_to_source,
                    )
                delivered_urls.append(url)
            except MediaTooLongError:
                await self._send_status(
                    message,
                    "Видео слишком длинное для загрузки.",
                    reply_to_source=reply_to_source,
                )
            except MediaTooLargeError:
                await self._send_status(
                    message,
                    "Контент получился слишком большим для Telegram.",
                    reply_to_source=reply_to_source,
                )
            except MediaDownloadError as exc:
                logger.warning("Could not download %s media: %s", platform, exc)
                if platform is MediaPlatform.TIKTOK:
                    await self._send_status(
                        message,
                        "TikTok не отдал медиа загрузчику. Оно может быть "
                        "приватным, удалённым или временно защищённым проверкой.",
                        reply_to_source=reply_to_source,
                    )
                else:
                    await self._send_status(
                        message,
                        "Не получилось скачать этот контент без авторизации. "
                        "Возможно, оно приватное или платформа ограничила доступ.",
                        reply_to_source=reply_to_source,
                    )
            except Exception:
                logger.exception("Could not send %s media", platform)
                await self._send_status(
                    message,
                    "Не получилось обработать или отправить медиа.",
                    reply_to_source=reply_to_source,
                )
        return MediaDeliveryResult(
            requested_urls=urls,
            delivered_urls=tuple(delivered_urls),
        )

    async def _send_media(
        self,
        message: types.Message,
        media: DownloadedMedia,
        *,
        reply_to_source: bool = True,
    ) -> None:
        """Send one file or albums of up to Telegram's ten-item limit."""
        pending_album: list[DownloadedMediaItem] = []
        for item in media.items:
            if item.kind is MediaKind.DOCUMENT:
                await self._send_album_or_item(
                    message,
                    pending_album,
                    reply_to_source=reply_to_source,
                )
                pending_album.clear()
                await self._send_item(
                    message,
                    item,
                    reply_to_source=reply_to_source,
                )
                continue

            pending_album.append(item)
            if len(pending_album) == _MAX_MEDIA_GROUP_ITEMS:
                await self._send_album_or_item(
                    message,
                    pending_album,
                    reply_to_source=reply_to_source,
                )
                pending_album.clear()

        await self._send_album_or_item(
            message,
            pending_album,
            reply_to_source=reply_to_source,
        )

    async def _send_album_or_item(
        self,
        message: types.Message,
        items: list[DownloadedMediaItem],
        *,
        reply_to_source: bool,
    ) -> None:
        if not items:
            return
        if len(items) == 1:
            await self._send_item(
                message,
                items[0],
                reply_to_source=reply_to_source,
            )
            return

        send = message.reply_media_group if reply_to_source else message.answer_media_group
        await send(media=[self._to_input_media(item) for item in items])

    async def _send_item(
        self,
        message: types.Message,
        item: DownloadedMediaItem,
        *,
        reply_to_source: bool,
    ) -> None:
        source = FSInputFile(item.path, chunk_size=self._upload_chunk_size)
        if item.kind is MediaKind.PHOTO:
            send = message.reply_photo if reply_to_source else message.answer_photo
            await send(photo=source)
        elif item.kind is MediaKind.VIDEO:
            send = message.reply_video if reply_to_source else message.answer_video
            await send(video=source)
        else:
            send = message.reply_document if reply_to_source else message.answer_document
            await send(document=source)

    @staticmethod
    async def _send_status(
        message: types.Message,
        text: str,
        *,
        reply_to_source: bool,
    ) -> None:
        send = message.reply if reply_to_source else message.answer
        await send(text)

    def _to_input_media(
        self,
        item: DownloadedMediaItem,
    ) -> types.InputMediaPhoto | types.InputMediaVideo:
        source = FSInputFile(item.path, chunk_size=self._upload_chunk_size)
        if item.kind is MediaKind.PHOTO:
            return types.InputMediaPhoto(media=source)
        return types.InputMediaVideo(media=source)
