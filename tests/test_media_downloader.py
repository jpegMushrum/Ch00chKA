from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ch00chka.integrations.media import YtDlpMediaDownloader
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
        self.assertNotIn("impersonate", FakeYoutubeDL.last_options)

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


if __name__ == "__main__":
    unittest.main()
