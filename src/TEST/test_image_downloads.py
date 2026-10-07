"""Regression tests for protected chapter-image downloads."""

import tempfile
import unittest
import base64
import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from utils.novel import (
    _detect_image_extension,
    _is_valid_image,
    download_image,
    get_cover_image_element,
    get_image_urls,
)


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def _webp_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="WEBP")
    return buffer.getvalue()


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


class TestPreserveExtension(unittest.TestCase):
    """preserve_ext keeps the downloaded image's original format."""

    def test_detect_extension_from_bytes(self):
        self.assertEqual(_detect_image_extension(_jpeg_bytes()), "jpg")
        self.assertEqual(_detect_image_extension(_png_bytes()), "png")
        self.assertEqual(_detect_image_extension(_webp_bytes()), "webp")
        self.assertEqual(_detect_image_extension(b"not an image"), "jpg")

    def test_detect_extension_uses_default_for_unreadable_bytes(self):
        self.assertEqual(_detect_image_extension(b"garbage", default="bin"), "bin")

    def test_valid_image_check_rejects_non_image_data(self):
        self.assertTrue(_is_valid_image(_jpeg_bytes()))
        self.assertFalse(_is_valid_image(b"not an image"))

    @patch("utils.novel.requests.get")
    def test_require_valid_image_rejects_http_200_non_image_response(self, mock_get):
        response = MagicMock(status_code=200, content=b"<html>blocked</html>")
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.jpg",
                output_dir=output_dir,
                name="page_0001",
                preserve_ext=True,
                require_valid_image=True,
            )

            self.assertEqual(saved_path, "")
            self.assertEqual(os.listdir(output_dir), [])

    @patch("utils.novel.requests.get")
    def test_preserve_ext_saves_raw_bytes_with_detected_extension(self, mock_get):
        webp = _webp_bytes()
        response = MagicMock(status_code=200, content=webp)
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.webp",
                output_dir=output_dir,
                name="page_0001",
                preserve_ext=True,
            )

            self.assertTrue(saved_path.endswith(".webp"))
            self.assertEqual(Path(saved_path).read_bytes(), webp)

    @patch("utils.novel.requests.get")
    def test_default_still_normalizes_to_jpeg(self, mock_get):
        webp = _webp_bytes()
        response = MagicMock(status_code=200, content=webp)
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.webp",
                output_dir=output_dir,
                name="page_0001",
            )

            self.assertTrue(saved_path.endswith(".jpg"))
            with Image.open(saved_path) as image:
                self.assertEqual(image.format, "JPEG")

    @patch("utils.novel.requests.get")
    def test_preserve_ext_jpeg_stays_jpeg(self, mock_get):
        jpeg = _jpeg_bytes()
        response = MagicMock(status_code=200, content=jpeg)
        mock_get.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.jpeg",
                output_dir=output_dir,
                name="page_0001",
                preserve_ext=True,
            )

            self.assertTrue(saved_path.endswith(".jpg"))
            self.assertEqual(Path(saved_path).read_bytes(), jpeg)


class TestSeleniumFallback(unittest.TestCase):
    """Selenium is used to crawl the image after five failed retries."""

    @patch("utils.novel.time.sleep")
    @patch("utils.novel.requests.get")
    def test_uses_provided_driver_after_five_failed_requests(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("connection refused")
        browser = MagicMock()
        update_log = MagicMock()
        browser.execute_async_script.return_value = {
            "data": base64.b64encode(_jpeg_bytes()).decode()
        }

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.jpg",
                output_dir=output_dir,
                name="page_0001",
                browser_driver=browser,
                update_log=update_log,
                selenium_fallback=True,
            )
            self.assertTrue(os.path.isfile(saved_path))
            self.assertEqual(Path(saved_path).read_bytes(), _jpeg_bytes())

        self.assertEqual(mock_get.call_count, 5)
        browser.get.assert_called_once()
        browser.execute_async_script.assert_called_once()
        update_log.assert_any_call(
            "Image download failed after 5 request attempts; "
            "trying Selenium before site fallback: "
            "https://images.example.net/page.jpg"
        )

    @patch("utils.novel.time.sleep")
    @patch("utils.novel.requests.get")
    def test_starts_and_quits_own_driver_when_none_provided(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("connection refused")
        driver = MagicMock()
        driver.execute_async_script.return_value = {
            "data": base64.b64encode(_jpeg_bytes()).decode()
        }

        with patch("utils.fetcher.matching_chrome_service") as mock_service:
            with patch("utils.novel.webdriver") as fake_webdriver:
                fake_webdriver.Chrome.return_value = driver
                with tempfile.TemporaryDirectory() as output_dir:
                    saved_path = download_image(
                        "https://images.example.net/page.jpg",
                        output_dir=output_dir,
                        name="page_0001",
                        update_log=MagicMock(),
                        selenium_fallback=True,
                    )
                    self.assertTrue(os.path.isfile(saved_path))

        fake_webdriver.Chrome.assert_called_once()
        self.assertIs(
            fake_webdriver.Chrome.call_args.kwargs["service"],
            mock_service.return_value,
        )
        driver.get.assert_called_once()
        driver.execute_async_script.assert_called_once()
        driver.quit.assert_called_once()

    @patch("utils.novel.time.sleep")
    @patch("utils.novel.requests.get")
    def test_returns_empty_when_selenium_fallback_has_no_image(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("connection refused")
        browser = MagicMock()
        browser.execute_async_script.return_value = {"error": "fetch blocked"}

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.jpg",
                output_dir=output_dir,
                name="page_0001",
                browser_driver=browser,
                update_log=MagicMock(),
                selenium_fallback=True,
            )

        self.assertEqual(saved_path, "")
        browser.get.assert_called_once()
        browser.execute_async_script.assert_called_once()

    @patch("utils.novel.time.sleep")
    @patch("utils.novel.requests.get")
    def test_deactivated_by_default_does_not_use_selenium(self, mock_get, mock_sleep):
        mock_get.side_effect = Exception("connection refused")
        browser = MagicMock()
        browser.execute_async_script.return_value = {
            "data": base64.b64encode(_jpeg_bytes()).decode()
        }

        with tempfile.TemporaryDirectory() as output_dir:
            saved_path = download_image(
                "https://images.example.net/page.jpg",
                output_dir=output_dir,
                name="page_0001",
                browser_driver=browser,
                update_log=MagicMock(),
            )

        self.assertEqual(saved_path, "")
        self.assertEqual(mock_get.call_count, 5)
        browser.get.assert_not_called()
        browser.execute_async_script.assert_not_called()
