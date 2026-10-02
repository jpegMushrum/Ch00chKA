from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_dlp.utils import DownloadError

from ch00chka.integrations.media import (
    DownloadedMedia,
    DownloadedMediaItem,
    MediaKind,
    YtDlpMediaAdapter,
    YtDlpMediaDownloader,
)
from ch00chka.integrations.media_urls import MediaPlatform


class FakeYoutubeDL:
    last_options = None

    def __init__(self, options):
        type(self).last_options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, *, download):
        output = Path(self.last_options["outtmpl"].replace("%(ext)s", "mp4"))
        output.write_bytes(b"video")
        return {"title": "Test TikTok"}


class FailingYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, *, download):
        raise DownloadError("No video formats found")


class MediaDownloaderTests(unittest.TestCase):
    def test_tiktok_relies_on_extractors_automatic_impersonation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
            )
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            with patch(
                "ch00chka.integrations.media.yt_dlp.YoutubeDL",
                FakeYoutubeDL,
            ):
                media = downloader._download_sync(
                    "https://www.tiktok.com/@user/video/123",
                    MediaPlatform.TIKTOK,
                    work_dir,
                )

        self.assertEqual(media.title, "Test TikTok")
        self.assertEqual(len(media.items), 1)
        self.assertEqual(media.items[0].kind, MediaKind.VIDEO)
        self.assertNotIn("impersonate", FakeYoutubeDL.last_options)

    def test_instagram_and_tiktok_allow_up_to_one_media_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
            )
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            with patch(
                "ch00chka.integrations.media.yt_dlp.YoutubeDL",
                FakeYoutubeDL,
            ):
                downloader._download_sync(
                    "https://www.instagram.com/p/example/",
                    MediaPlatform.INSTAGRAM,
                    work_dir,
                )

        self.assertFalse(FakeYoutubeDL.last_options["noplaylist"])
        self.assertEqual(FakeYoutubeDL.last_options["playlistend"], 10)

    def test_uses_the_cookie_file_matching_the_media_platform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
                cookies_file="/run/secrets/ch00chka/fallback-cookies.txt",
                cookies_files={
                    MediaPlatform.YOUTUBE: "/run/secrets/ch00chka/youtube-cookies.txt",
                    MediaPlatform.TIKTOK: "/run/secrets/ch00chka/tiktok-cookies.txt",
                },
            )
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            with patch(
                "ch00chka.integrations.media.yt_dlp.YoutubeDL",
                FakeYoutubeDL,
            ):
                downloader._download_sync(
                    "https://www.youtube.com/watch?v=example",
                    MediaPlatform.YOUTUBE,
                    work_dir,
                )

        self.assertEqual(
            FakeYoutubeDL.last_options["cookiefile"],
            "/run/secrets/ch00chka/youtube-cookies.txt",
        )

    def test_falls_back_to_gallery_dl_for_tiktok_photo_posts(self):
        def gallery_dl_run(command, **kwargs):
            destination = Path(command[command.index("--destination") + 1])
            (destination / "media_001.jpg").write_bytes(b"image")
            return type("Result", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
                cookies_files={
                    MediaPlatform.TIKTOK: "/run/secrets/ch00chka/tiktok-cookies.txt",
                },
            )
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            with (
                patch(
                    "ch00chka.integrations.media.yt_dlp.YoutubeDL",
                    FailingYoutubeDL,
                ),
                patch(
                    "ch00chka.integrations.media.subprocess.run",
                    side_effect=gallery_dl_run,
                ) as run,
            ):
                media = downloader._download_sync(
                    "https://www.tiktok.com/@user/photo/123",
                    MediaPlatform.TIKTOK,
                    work_dir,
                )

        self.assertEqual(media.items[0].kind, MediaKind.PHOTO)
        command = run.call_args.args[0]
        self.assertIn("--cookies", command)
        self.assertIn("/run/secrets/ch00chka/tiktok-cookies.txt", command)
        self.assertIn("1-10", command)
        self.assertIn("extractor.cookies-update=false", command)


class _RecordingMessage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def reply_photo(self, *, photo):
        self.calls.append(("photo", photo))

    async def reply_video(self, *, video):
        self.calls.append(("video", video))

    async def reply_document(self, *, document):
        self.calls.append(("document", document))

    async def reply_media_group(self, *, media):
        self.calls.append(("album", media))


class MediaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_one_image_as_a_photo(self):
        adapter = YtDlpMediaAdapter(downloader=object())
        message = _RecordingMessage()
        media = DownloadedMedia(
            items=(
                DownloadedMediaItem(
                    path=Path("slide.jpg"),
                    kind=MediaKind.PHOTO,
                ),
            ),
            platform=MediaPlatform.TIKTOK,
        )

        await adapter._send_media(message, media)

        self.assertEqual([kind for kind, _ in message.calls], ["photo"])

    async def test_sends_photo_and_video_carousel_as_a_telegram_album(self):
        adapter = YtDlpMediaAdapter(downloader=object())
        message = _RecordingMessage()
        media = DownloadedMedia(
            items=(
                DownloadedMediaItem(Path("one.jpg"), MediaKind.PHOTO),
                DownloadedMediaItem(Path("two.mp4"), MediaKind.VIDEO),
                DownloadedMediaItem(Path("three.webp"), MediaKind.PHOTO),
            ),
            platform=MediaPlatform.INSTAGRAM,
        )

        await adapter._send_media(message, media)

        self.assertEqual([kind for kind, _ in message.calls], ["album"])
        self.assertEqual(len(message.calls[0][1]), 3)


if __name__ == "__main__":
    unittest.main()
