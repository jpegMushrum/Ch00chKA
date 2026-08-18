from __future__ import annotations

import unittest

from ch00chka.integrations.media_urls import MediaPlatform, detect_media_platform


class MediaUrlTests(unittest.TestCase):
    def test_detects_supported_platform_urls(self):
        cases = {
            "https://youtu.be/abc": MediaPlatform.YOUTUBE,
            "https://www.youtube.com/shorts/abc": MediaPlatform.YOUTUBE,
            "https://vm.tiktok.com/abc": MediaPlatform.TIKTOK,
            "https://www.tiktok.com/@author/video/123": MediaPlatform.TIKTOK,
            "https://instagram.com/reel/abc/": MediaPlatform.INSTAGRAM,
            "https://www.instagram.com/p/abc/": MediaPlatform.INSTAGRAM,
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_media_platform(url), expected)

    def test_rejects_lookalike_and_non_http_urls(self):
        urls = (
            "https://youtube.com.evil.test/watch?v=abc",
            "https://notinstagram.com/reel/abc",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "not a url",
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(detect_media_platform(url))


if __name__ == "__main__":
    unittest.main()
