from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from yt_dlp.utils import DownloadError

from ch00chka.integrations.media import (
    DownloadedMedia,
    DownloadedMediaItem,
    MediaKind,
    YtDlpMediaAdapter,
    YtDlpMediaDownloader,
    _safe_gallery_dl_output,
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
    def test_gallery_dl_diagnostics_redact_session_data(self):
        diagnostic = _safe_gallery_dl_output(
            "Cookie: sessionid=top-secret\n"
            "csrftoken=also-secret\n"
            "ERROR: login required"
        )

        self.assertNotIn("top-secret", diagnostic)
        self.assertNotIn("also-secret", diagnostic)
        self.assertIn("ERROR: login required", diagnostic)

    def test_media_candidates_include_gallery_subdirectories_not_temporary_cookies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            media_dir = work_dir / "instagram" / "author"
            media_dir.mkdir(parents=True)
            (media_dir / "media_001.jpg").write_bytes(b"image")
            cookie_dir = work_dir / ".cookies"
            cookie_dir.mkdir()
            (cookie_dir / "cookies.txt").write_text("secret", encoding="utf-8")

            candidates = YtDlpMediaDownloader._media_candidates(work_dir)

        self.assertEqual([path.name for path in candidates], ["media_001.jpg"])

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

    def test_instagram_and_tiktok_use_configured_media_limit(self):
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
        self.assertEqual(FakeYoutubeDL.last_options["playlistend"], 50)

    def test_uses_the_cookie_file_matching_the_media_platform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_cookie = Path(temp_dir) / "youtube-cookies.txt"
            cookie_jar_dir = Path(temp_dir) / "cookie-jars"
            source_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
                cookies_file=str(Path(temp_dir) / "fallback-cookies.txt"),
                cookies_files={
                    MediaPlatform.YOUTUBE: str(source_cookie),
                    MediaPlatform.TIKTOK: str(Path(temp_dir) / "tiktok-cookies.txt"),
                },
                cookie_jar_dir=str(cookie_jar_dir),
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
            cookie_jar = Path(FakeYoutubeDL.last_options["cookiefile"])
            cookie_jar_contents = cookie_jar.read_text(encoding="utf-8")

        self.assertEqual(cookie_jar, cookie_jar_dir / "youtube-cookies.txt")
        self.assertEqual(cookie_jar_contents, "# Netscape HTTP Cookie File\n")

    def test_cookie_jar_is_reseeded_only_when_reset_is_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_cookie = Path(temp_dir) / "tiktok-export.txt"
            cookie_jar_dir = Path(temp_dir) / "cookie-jars"
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()
            source_cookie.write_text("initial", encoding="utf-8")
            common_args = {
                "temp_dir": temp_dir,
                "max_bytes": 48_000_000,
                "max_duration_seconds": 600,
                "max_concurrent_downloads": 1,
                "cookies_files": {MediaPlatform.TIKTOK: str(source_cookie)},
                "cookie_jar_dir": str(cookie_jar_dir),
            }
            downloader = YtDlpMediaDownloader(**common_args)
            first_jar = Path(
                downloader._cookie_file_for_platform(MediaPlatform.TIKTOK, work_dir)
            )
            source_cookie.write_text("replacement", encoding="utf-8")
            unchanged_jar = Path(
                downloader._cookie_file_for_platform(MediaPlatform.TIKTOK, work_dir)
            )
            self.assertEqual(first_jar, unchanged_jar)
            self.assertEqual(unchanged_jar.read_text(encoding="utf-8"), "initial")

            reset_downloader = YtDlpMediaDownloader(
                **common_args,
                reset_cookie_jars=True,
            )
            reset_jar = Path(
                reset_downloader._cookie_file_for_platform(
                    MediaPlatform.TIKTOK,
                    work_dir,
                )
            )

            self.assertEqual(reset_jar.read_text(encoding="utf-8"), "replacement")

    def test_falls_back_to_gallery_dl_for_tiktok_photo_posts(self):
        def gallery_dl_run(command, **kwargs):
            destination = Path(command[command.index("--destination") + 1])
            output_dir = destination / "tiktok" / "user"
            output_dir.mkdir(parents=True)
            (output_dir / "media_001.jpg").write_bytes(b"image")
            return type("Result", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as temp_dir:
            source_cookie = Path(temp_dir) / "tiktok-export.txt"
            cookie_jar_dir = Path(temp_dir) / "cookie-jars"
            source_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            downloader = YtDlpMediaDownloader(
                temp_dir=temp_dir,
                max_bytes=48_000_000,
                max_duration_seconds=600,
                max_concurrent_downloads=1,
                cookies_files={
                    MediaPlatform.TIKTOK: str(source_cookie),
                },
                cookie_jar_dir=str(cookie_jar_dir),
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
        self.assertIn(str(cookie_jar_dir / "tiktok-cookies.txt"), command)
        self.assertIn("1-50", command)
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

    async def answer_photo(self, *, photo):
        self.calls.append(("answer_photo", photo))

    async def answer_video(self, *, video):
        self.calls.append(("answer_video", video))

    async def answer_document(self, *, document):
        self.calls.append(("answer_document", document))

    async def answer_media_group(self, *, media):
        self.calls.append(("answer_album", media))


class _SuccessfulMediaDownloader:
    def __init__(self, media: DownloadedMedia) -> None:
        self._media = media

    @asynccontextmanager
    async def download(self, url: str):
        yield self._media


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

    async def test_sends_without_reply_when_source_will_be_deleted(self):
        adapter = YtDlpMediaAdapter(downloader=object())
        message = _RecordingMessage()
        media = DownloadedMedia(
            items=(DownloadedMediaItem(Path("clip.mp4"), MediaKind.VIDEO),),
            platform=MediaPlatform.TIKTOK,
        )

        await adapter._send_media(message, media, reply_to_source=False)

        self.assertEqual([kind for kind, _ in message.calls], ["answer_video"])

    async def test_splits_large_carousel_into_multiple_albums(self):
        adapter = YtDlpMediaAdapter(downloader=object())
        message = _RecordingMessage()
        media = DownloadedMedia(
            items=tuple(
                DownloadedMediaItem(Path(f"slide-{index}.jpg"), MediaKind.PHOTO)
                for index in range(21)
            ),
            platform=MediaPlatform.INSTAGRAM,
        )

        await adapter._send_media(message, media)

        self.assertEqual(
            [kind for kind, _ in message.calls],
            ["album", "album", "photo"],
        )
        self.assertEqual([len(payload) for _, payload in message.calls[:2]], [10, 10])

    async def test_reports_a_url_as_delivered_only_after_send_succeeds(self):
        media = DownloadedMedia(
            items=(DownloadedMediaItem(Path("clip.mp4"), MediaKind.VIDEO),),
            platform=MediaPlatform.YOUTUBE,
        )
        adapter = YtDlpMediaAdapter(
            downloader=_SuccessfulMediaDownloader(media),
        )
        message = _RecordingMessage()
        url = "https://youtu.be/example"

        result = await adapter.handle(message, (url,))

        self.assertTrue(result.all_delivered)
        self.assertEqual(result.delivered_urls, (url,))


if __name__ == "__main__":
    unittest.main()
