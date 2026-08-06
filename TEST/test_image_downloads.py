"""Regression tests for protected chapter-image downloads."""

import tempfile
import unittest
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.novel import download_image, get_cover_image_element, get_image_urls


class TestChapterImages(unittest.TestCase):
    def test_skips_data_uri_placeholder_and_resolves_relative_url(self):
        image_urls = get_image_urls(
            '<img src="data:image/gif;base64,placeholder">'
            '<img src="/wp-content/uploads/chapter.jpg">',
            "https://www.foxaholic.com",
            name="img",
            other_attr="src",
        )

        self.assertEqual(
            image_urls,
            ["https://www.foxaholic.com/wp-content/uploads/chapter.jpg"],
        )

    @patch("utils.novel.requests.get")
    def test_uses_page_referer_and_browser_cookies_for_same_host(
        self, mock_get
    ):
        response = MagicMock(status_code=200, content=b"image-bytes")
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://www.foxaholic.com/wp-content/uploads/chapter.jpg",
                output_dir=output_dir,
                name="chapter11_img1",
                img_referrer=True,
                referer_url="https://www.foxaholic.com/novel/example/chapter-11/",
                cookies={"cf_clearance": "test-cookie"},
                update_log=MagicMock(),
            )

            self.assertEqual(Path(saved_path).read_bytes(), b"image-bytes")

        _, request_kwargs = mock_get.call_args
        self.assertEqual(
            request_kwargs["headers"]["Referer"],
            "https://www.foxaholic.com/novel/example/chapter-11/",
        )
        self.assertEqual(request_kwargs["cookies"], {"cf_clearance": "test-cookie"})
        self.assertEqual(request_kwargs["timeout"], 20)

    @patch("utils.novel.requests.get")
    def test_does_not_forward_browser_cookies_to_third_party_host(self, mock_get):
        response = MagicMock(status_code=200, content=b"image-bytes")
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            download_image(
                "https://images.example.net/chapter.jpg",
                output_dir=output_dir,
                name="chapter11_img1",
                img_referrer=True,
                referer_url="https://www.foxaholic.com/novel/example/chapter-11/",
                cookies={"cf_clearance": "test-cookie"},
                update_log=MagicMock(),
            )

        _, request_kwargs = mock_get.call_args
        self.assertIsNone(request_kwargs["cookies"])

    @patch("utils.novel.requests.get")
    def test_uses_verified_browser_when_same_host_request_is_forbidden(
        self, mock_get
    ):
        response = MagicMock(status_code=403, content=b"")
        mock_get.return_value.__enter__.return_value = response
        browser = MagicMock()
        browser.execute_async_script.return_value = {
            "data": base64.b64encode(b"browser-image").decode()
        }

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://www.foxaholic.com/wp-content/uploads/chapter.jpg",
                output_dir=output_dir,
                name="chapter11_img1",
                img_referrer=True,
                referer_url="https://www.foxaholic.com/novel/example/chapter-11/",
                browser_driver=browser,
                update_log=MagicMock(),
            )
            self.assertEqual(Path(saved_path).read_bytes(), b"browser-image")

        browser.execute_async_script.assert_called_once()
        self.assertEqual(mock_get.call_count, 1)

    @patch("utils.novel.download_image")
    def test_cover_download_receives_page_context(self, mock_download):
        mock_download.return_value = "/tmp/cover.jpg"

        cover_path = get_cover_image_element(
            '<img itemprop="image" src="https://static.example.com/cover.jpg">',
            update_log=MagicMock(),
            output_dir="/tmp",
            img_name="cover",
            img_referrer="https://example.com/",
            referer_url="https://example.com/my-novel/",
            cookies={"clearance": "cookie"},
            browser_driver=MagicMock(),
            cover_args={"name": "img", "itemprop": "image", "other_attr": "src"},
        )

        self.assertEqual(cover_path, "/tmp/cover.jpg")
        _, kwargs = mock_download.call_args
        self.assertEqual(kwargs["img_referrer"], "https://example.com/")
        self.assertEqual(kwargs["referer_url"], "https://example.com/my-novel/")
        self.assertEqual(kwargs["cookies"], {"clearance": "cookie"})
        self.assertIsNotNone(kwargs["browser_driver"])

    @patch("utils.novel.requests.get")
    def test_uses_configured_site_referrer_for_static_cover_host(self, mock_get):
        response = MagicMock(status_code=200, content=b"image-bytes")
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            download_image(
                "https://static.truyenfull.live/cover/example.jpg",
                output_dir=output_dir,
                name="cover",
                img_referrer="https://truyenfull.live/",
                update_log=MagicMock(),
            )

        _, request_kwargs = mock_get.call_args
        self.assertEqual(
            request_kwargs["headers"]["Referer"], "https://truyenfull.live/"
        )
