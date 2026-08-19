from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import yt_dlp
from aiogram import types
from aiogram.types import FSInputFile
from yt_dlp.utils import DownloadError

from ch00chka.integrations.media_urls import MediaPlatform, detect_media_platform


logger = logging.getLogger(__name__)


class MediaDownloadError(RuntimeError):
    pass


class MediaTooLargeError(MediaDownloadError):
    pass


class MediaTooLongError(MediaDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    platform: MediaPlatform
    title: str | None = None


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
        proxy_url: str | None = None,
        cookies_file: str | None = None,
    ) -> None:
        self._temp_dir = Path(temp_dir).resolve()
        self._cache_dir = self._temp_dir / "cache"
        self._max_bytes = max_bytes
        self._max_duration_seconds = max_duration_seconds
        self._proxy_url = proxy_url
        self._cookies_file = cookies_file
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
        options = {
            "format": format_selector,
            "format_sort": ["vcodec:h264", "acodec:aac", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "outtmpl": str(work_dir / "media.%(ext)s"),
            "cachedir": str(self._cache_dir),
            "noplaylist": True,
            "playlistend": 1,
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
        if self._cookies_file:
            options["cookiefile"] = self._cookies_file

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as exc:
            if duration_marker in str(exc):
                raise MediaTooLongError(
                    f"Video exceeds {self._max_duration_seconds} seconds"
                ) from exc
            raise MediaDownloadError(str(exc)) from exc

        candidates = [
            path
            for path in work_dir.iterdir()
            if path.is_file()
            and not path.name.endswith((".part", ".ytdl", ".json"))
        ]
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
            raise MediaDownloadError("yt-dlp did not create a media file")

        path = max(candidates, key=lambda item: item.stat().st_size)
        if path.stat().st_size > self._max_bytes:
            raise MediaTooLargeError(f"Media exceeds {self._max_bytes} bytes")

        title = info.get("title") if isinstance(info, dict) else None
        return DownloadedMedia(
            path=path,
            platform=platform,
            title=title if isinstance(title, str) else None,
        )


class YtDlpMediaAdapter:
    def __init__(
        self,
        *,
        downloader: YtDlpMediaDownloader,
        upload_chunk_size: int = 1_048_576,
    ) -> None:
        self._downloader = downloader
        self._upload_chunk_size = upload_chunk_size

    async def handle(self, message: types.Message, urls: tuple[str, ...]) -> None:
        for url in urls:
            platform = detect_media_platform(url)
            if platform is None:
                continue

            try:
                async with self._downloader.download(url) as media:
                    await message.reply_video(
                        video=FSInputFile(
                            media.path,
                            chunk_size=self._upload_chunk_size,
                        )
                    )
            except MediaTooLongError:
                await message.reply("Видео слишком длинное для загрузки.")
            except MediaTooLargeError:
                await message.reply("Видео получилось слишком большим для Telegram.")
            except MediaDownloadError as exc:
                logger.warning("Could not download %s media: %s", platform, exc)
                if platform is MediaPlatform.TIKTOK:
                    await message.reply(
                        "TikTok не отдал видео загрузчику. Оно может быть "
                        "приватным, удалённым или временно защищённым проверкой."
                    )
                else:
                    await message.reply(
                        "Не получилось скачать это видео без авторизации. "
                        "Возможно, оно приватное или платформа ограничила доступ."
                    )
            except Exception:
                logger.exception("Could not send %s media", platform)
                await message.reply("Не получилось обработать или отправить видео.")
