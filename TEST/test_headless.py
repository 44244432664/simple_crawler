"""
Unit tests for PageFetcher headless/visible browser mode (Tasks 1 & 2).

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_headless.py -v
    # or
    python -m unittest TEST/test_headless.py -v
"""

import io
import json
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

from utils.fetcher import (
    FetchMode,
    PageFetcher,
    BrowserUnavailableError,
    ChallengeTimeoutError,
    chrome_options,
)


def _mock_driver(page_source="<html><body>OK</body></html>"):
    driver = MagicMock()
    driver.page_source = page_source
    driver.uc_open_with_reconnect = MagicMock()
    driver.wait_for_element = MagicMock()
    driver.quit = MagicMock()
    return driver


class TestPageFetcherHeadlessDefault(unittest.TestCase):
    """headless=True by default."""

    def test_headless_defaults_to_true(self):
        fetcher = PageFetcher()
        self.assertTrue(fetcher.headless)
        fetcher.close()

    def test_headless_explicit_true(self):
        fetcher = PageFetcher(headless=True)
        self.assertTrue(fetcher.headless)
        fetcher.close()

    def test_headless_explicit_false(self):
        fetcher = PageFetcher(headless=False)
        self.assertFalse(fetcher.headless)
        fetcher.close()

    def test_headless_is_stored(self):
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, headless=False)
        self.assertFalse(fetcher.headless)
        fetcher.close()


class TestPageFetcherHeadlessBrowserLaunch(unittest.TestCase):
    """_ensure_browser respects headless setting."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="test_h_profile")
        self.fetcher._log.setLevel(logging.DEBUG)

    def tearDown(self):
        self.fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_true_passes_headless_to_driver(self, mock_driver_cls):
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            self.fetcher.fetch("https://example.com")

        call_kwargs = mock_driver_cls.call_args[1]
        self.assertIn("headless", call_kwargs)
        self.assertTrue(call_kwargs["headless"])
        self.assertNotIn("headed", call_kwargs)

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_false_passes_headed_to_driver(self, mock_driver_cls):
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="test_v_profile", headless=False)
        fetcher._log.setLevel(logging.DEBUG)
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            fetcher.fetch("https://example.com")

        call_kwargs = mock_driver_cls.call_args[1]
        self.assertIn("headed", call_kwargs)
        self.assertTrue(call_kwargs["headed"])
        self.assertNotIn("headless", call_kwargs)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_logs_headless_mode(self, mock_driver_cls):
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        self.fetcher._log.setLevel(logging.INFO)
        self.fetcher._profile_name = "test_log_h"

        with patch("os.makedirs"):
            with self.assertLogs(self.fetcher._log, level="INFO") as log_cm:
                self.fetcher.fetch("https://example.com")

        log_output = " ".join(log_cm.output)
        self.assertIn("headless", log_output)
        self.assertIn("test_log_h", log_output)

    @patch("seleniumbase.Driver", autospec=True)
    def test_logs_visible_mode(self, mock_driver_cls):
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="test_log_v", headless=False)
        fetcher._log.setLevel(logging.INFO)

        with patch("os.makedirs"):
            with self.assertLogs(fetcher._log, level="INFO") as log_cm:
                fetcher.fetch("https://example.com")

        log_output = " ".join(log_cm.output)
        self.assertIn("visible", log_output)
        self.assertIn("test_log_v", log_output)
        fetcher.close()


class TestPageFetcherHeadlessBrowserReuse(unittest.TestCase):
    """Headless setting persists across browser reuse."""

    @patch("seleniumbase.Driver", autospec=True)
    def test_reuses_driver_with_same_headless_setting(self, mock_driver_cls):
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="test_reuse_h", headless=True)

        with patch("os.makedirs"):
            fetcher.fetch("https://example.com/page1")
            fetcher.fetch("https://example.com/page2")

        self.assertEqual(mock_driver_cls.call_count, 1)
        call_kwargs = mock_driver_cls.call_args[1]
        self.assertTrue(call_kwargs.get("headless", False))
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_reuses_driver_with_visible_setting(self, mock_driver_cls):
        """Visible mode (headless=False) also reuses driver across fetches."""
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="test_reuse_v", headless=False)

        with patch("os.makedirs"):
            fetcher.fetch("https://example.com/page1")
            fetcher.fetch("https://example.com/page2")

        self.assertEqual(mock_driver_cls.call_count, 1)
        call_kwargs = mock_driver_cls.call_args[1]
        self.assertTrue(call_kwargs.get("headed", False))
        fetcher.close()


class TestPageFetcherHeadlessRequestsMode(unittest.TestCase):
    """Requests mode ignores headless (no browser started)."""

    def test_requests_mode_ignores_headless(self):
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS, headless=True)
        self.assertTrue(fetcher.headless)
        self.assertIsNone(fetcher._driver)
        fetcher.close()

    def test_requests_mode_headless_false(self):
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS, headless=False)
        self.assertFalse(fetcher.headless)
        self.assertIsNone(fetcher._driver)
        fetcher.close()


class TestPageFetcherHeadlessAutoMode(unittest.TestCase):
    """Auto mode: headless applies after browser fallback."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.AUTO, profile_name="test_auto_h")
        self.fetcher._log.setLevel(logging.DEBUG)

    def tearDown(self):
        self.fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_auto_mode_headless_after_fallback(self, mock_driver_cls):
        mock_instance = _mock_driver()
        mock_driver_cls.return_value = mock_instance

        from utils.fetcher import CloudflareChallengeError, CF_CHALLENGE_HEADER
        import requests

        cf_resp = requests.Response()
        cf_resp.status_code = 200
        cf_resp.encoding = "utf-8"
        cf_resp.raw = io.BytesIO(b"<html>challenge</html>")
        cf_resp.headers.update({CF_CHALLENGE_HEADER: "challenge"})
        cf_resp._content = b"<html>challenge</html>"

        with patch.object(self.fetcher.session, "get", return_value=cf_resp):
            with patch("os.makedirs"):
                html = self.fetcher.fetch("https://example.com")

        call_kwargs = mock_driver_cls.call_args[1]
        self.assertIn("headless", call_kwargs)
        self.assertTrue(call_kwargs["headless"])


# ---------------------------------------------------------------------------
# Tests: Task 2 — Cloudflare terminal messages
# ---------------------------------------------------------------------------


class TestPageFetcherCloudflareMessages(unittest.TestCase):
    """Cloudflare challenge messages differ by headless/visible mode."""

    # -- headless messages ---------------------------------------------------

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_challenge_message(self, mock_driver_cls):
        """Headless mode: print says 'awaiting automatic verification'."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_headless", headless=True
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        # Make _is_challenge_page return True, then break immediately
        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    # ChallengeTimeoutError expected — prevent infinite loop
                    fetcher.challenge_timeout = 0
                    with self.assertRaises(ChallengeTimeoutError):
                        fetcher.fetch("https://example.com")

        output = mock_stdout.getvalue()
        self.assertIn("awaiting automatic verification", output)
        self.assertNotIn("Chrome window", output)
        self.assertNotIn("Complete the verification", output)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_timeout_message(self, mock_driver_cls):
        """Headless timeout recommends rerunning with visible browser."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_timeout_h", headless=True
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("pathlib.Path.write_text"):
                    fetcher.challenge_timeout = 0
                    with self.assertRaises(ChallengeTimeoutError) as cm:
                        fetcher.fetch("https://example.com")

        err_msg = str(cm.exception)
        self.assertIn("automatic verification timed out", err_msg)
        self.assertIn("rerun with a visible browser", err_msg.lower())
        self.assertIn("headless=False", err_msg)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_log_message(self, mock_driver_cls):
        """Headless log says 'awaiting automatic verification'."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_log_h", headless=True
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        fetcher._log.setLevel(logging.INFO)
        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("pathlib.Path.write_text"):
                    fetcher.challenge_timeout = 0
                    with self.assertLogs(fetcher._log, level="INFO") as log_cm:
                        with self.assertRaises(ChallengeTimeoutError):
                            fetcher.fetch("https://example.com")

        log_output = " ".join(log_cm.output)
        self.assertIn("awaiting automatic verification", log_output)
        self.assertNotIn("manual verification", log_output)
        fetcher.close()

    # -- visible messages ----------------------------------------------------

    @patch("seleniumbase.Driver", autospec=True)
    def test_visible_challenge_message(self, mock_driver_cls):
        """Visible mode: print says 'Complete the verification in the Chrome window'."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_visible", headless=False
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    fetcher.challenge_timeout = 0
                    with self.assertRaises(ChallengeTimeoutError):
                        fetcher.fetch("https://example.com")

        output = mock_stdout.getvalue()
        self.assertIn("Complete the verification in the Chrome window", output)
        self.assertNotIn("awaiting automatic verification", output)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_visible_timeout_message(self, mock_driver_cls):
        """Visible timeout retains 'Manual Cloudflare verification' message."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_timeout_v", headless=False
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("pathlib.Path.write_text"):
                    fetcher.challenge_timeout = 0
                    with self.assertRaises(ChallengeTimeoutError) as cm:
                        fetcher.fetch("https://example.com")

        err_msg = str(cm.exception)
        self.assertIn("Manual Cloudflare verification timed out", err_msg)
        self.assertNotIn("automatic verification", err_msg)
        self.assertNotIn("rerun with a visible browser", err_msg.lower())
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_visible_log_message(self, mock_driver_cls):
        """Visible log says 'waiting for manual verification in Chrome'."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_log_v", headless=False
        )
        mock_instance = _mock_driver(page_source="<html>cf-chl-widget</html>")
        mock_driver_cls.return_value = mock_instance

        fetcher._log.setLevel(logging.INFO)
        with patch.object(fetcher, "_is_challenge_page", return_value=True):
            with patch("os.makedirs"):
                with patch("pathlib.Path.write_text"):
                    fetcher.challenge_timeout = 0
                    with self.assertLogs(fetcher._log, level="INFO") as log_cm:
                        with self.assertRaises(ChallengeTimeoutError):
                            fetcher.fetch("https://example.com")

        log_output = " ".join(log_cm.output)
        self.assertIn("manual verification in Chrome", log_output)
        self.assertNotIn("awaiting automatic verification", log_output)
        fetcher.close()

    # -- successful auto-verification (no timeout) ---------------------------

    @patch("seleniumbase.Driver", autospec=True)
    def test_headless_auto_clear_succeeds(self, mock_driver_cls):
        """Headless auto-verification works same as before (only messages differ)."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_clear_h", headless=True
        )
        mock_instance = _mock_driver(page_source="<html>real content</html>")
        mock_driver_cls.return_value = mock_instance

        # Challenge detected once, then cleared
        call_count = 0

        def challenge_side_effect(_sel=None):
            nonlocal call_count
            call_count += 1
            return call_count == 1  # True on first call, False after

        with patch.object(
            fetcher, "_is_challenge_page", side_effect=challenge_side_effect
        ):
            with patch("os.makedirs"):
                html = fetcher.fetch("https://example.com")

        self.assertIn("real content", html)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_visible_auto_clear_succeeds(self, mock_driver_cls):
        """Visible auto-verification also returns page content after challenge clears."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER, profile_name="cf_clear_v", headless=False
        )
        mock_instance = _mock_driver(page_source="<html>real visible content</html>")
        mock_driver_cls.return_value = mock_instance

        call_count = 0

        def challenge_side_effect(_sel=None):
            nonlocal call_count
            call_count += 1
            return call_count == 1

        with patch.object(
            fetcher, "_is_challenge_page", side_effect=challenge_side_effect
        ):
            with patch("os.makedirs"):
                html = fetcher.fetch("https://example.com")

        self.assertIn("real visible content", html)
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: Task 3 — Shared chrome_options helper
# ---------------------------------------------------------------------------


class TestChromeOptionsHelper(unittest.TestCase):
    """chrome_options() builds ChromeOptions correctly."""

    def test_headless_adds_flag(self):
        options = chrome_options(headless=True)
        args = options.arguments
        self.assertIn("--headless=new", args)

    def test_visible_omits_headless_flag(self):
        options = chrome_options(headless=False)
        args = options.arguments
        self.assertNotIn("--headless=new", args)
        self.assertNotIn("--headless", args)

    def test_always_sets_window_size(self):
        options = chrome_options(headless=True)
        args = options.arguments
        self.assertTrue(
            any("--window-size" in arg for arg in args),
            f"Expected --window-size in {args}",
        )

    def test_visible_sets_window_size(self):
        options = chrome_options(headless=False)
        args = options.arguments
        self.assertTrue(
            any("--window-size" in arg for arg in args),
            f"Expected --window-size in {args}",
        )

    def test_window_size_value(self):
        options = chrome_options(headless=True)
        size_args = [a for a in options.arguments if "--window-size" in a]
        self.assertTrue(len(size_args) >= 1)
        self.assertIn("1920,1080", size_args[0])

    def test_default_is_headless(self):
        options = chrome_options()
        self.assertIn("--headless=new", options.arguments)


class TestChromeOptionsIntegrationNovelCrawler(unittest.TestCase):
    """NovelCrawler uses chrome_options when driver=True."""

    @patch("utils.novel.extract_base_url")
    @patch("pandas.read_csv")
    @patch("builtins.open", new_callable=MagicMock)
    @patch("json.load")
    @patch("selenium.webdriver.Chrome")
    @patch("os.makedirs")
    @patch("crawler.Novel.PageFetcher")
    def test_driver_created_with_chrome_options(
        self, mock_fetcher, mock_makedirs, mock_chrome,
        mock_json, mock_open, mock_csv, mock_extract,
    ):
        from crawler.Novel import NovelCrawler

        mock_instance = MagicMock()
        mock_chrome.return_value = mock_instance
        mock_extract.return_value = "https://example.com"
        mock_df = MagicMock()
        mock_df.index.__bool__ = MagicMock(return_value=False)  # site not found → skip
        mock_df.__getitem__.return_value = mock_df
        mock_df.at = MagicMock()
        mock_csv.return_value = mock_df
        mock_json.return_value = {}

        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir="/tmp",
            sleep_time=1000,
            driver=True,
        )

        call_kwargs = mock_chrome.call_args[1]
        self.assertIn("options", call_kwargs)
        options = call_kwargs["options"]
        self.assertIn("--headless=new", options.arguments)
        self.assertTrue(
            any("--window-size" in a for a in options.arguments)
        )
        crawler.close()

    @patch("utils.novel.extract_base_url")
    @patch("pandas.read_csv")
    @patch("builtins.open", new_callable=MagicMock)
    @patch("json.load")
    @patch("os.makedirs")
    @patch("crawler.Novel.PageFetcher")
    def test_no_driver_when_driver_false(
        self, mock_fetcher, mock_makedirs, mock_json,
        mock_open, mock_csv, mock_extract,
    ):
        from crawler.Novel import NovelCrawler

        mock_extract.return_value = "https://example.com"
        mock_df = MagicMock()
        mock_df.index.__bool__ = MagicMock(return_value=False)
        mock_csv.return_value = mock_df
        mock_json.return_value = {}

        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir="/tmp",
            sleep_time=1000,
            driver=False,
        )
        self.assertIsNone(crawler.driver)
        crawler.close()

    @patch("utils.novel.extract_base_url")
    @patch("pandas.read_csv")
    @patch("builtins.open", new_callable=MagicMock)
    @patch("json.load")
    @patch("selenium.webdriver.Chrome")
    @patch("os.makedirs")
    @patch("crawler.Novel.PageFetcher")
    def test_driver_visible_omits_headless_flag(
        self, mock_fetcher, mock_makedirs, mock_chrome,
        mock_json, mock_open, mock_csv, mock_extract,
    ):
        """NovelCrawler with headless=False does NOT pass --headless=new."""
        from crawler.Novel import NovelCrawler

        mock_instance = MagicMock()
        mock_chrome.return_value = mock_instance
        mock_extract.return_value = "https://example.com"
        mock_df = MagicMock()
        mock_df.index.__bool__ = MagicMock(return_value=False)
        mock_df.__getitem__.return_value = mock_df
        mock_df.at = MagicMock()
        mock_csv.return_value = mock_df
        mock_json.return_value = {}

        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir="/tmp",
            sleep_time=1000,
            driver=True,
            headless=False,
        )

        call_kwargs = mock_chrome.call_args[1]
        self.assertIn("options", call_kwargs)
        options = call_kwargs["options"]
        self.assertNotIn("--headless=new", options.arguments)
        self.assertTrue(
            any("--window-size" in a for a in options.arguments)
        )
        crawler.close()


class TestChromeOptionsIntegrationXCrawler(unittest.TestCase):
    """XCrawler uses chrome_options when driver=True."""

    def _make_xcrawler(self, headless_val):
        from crawler.X import XCrawler
        with patch("utils.novel.extract_base_url", return_value="https://ex.com"):
            with patch("crawler.X.csv.DictReader") as mock_reader:
                mock_reader.return_value = [
                    {"site": "ex.com", "name": "ex_site"}
                ]
                with patch("os.path.exists", return_value=True):
                    with patch("builtins.open", new_callable=MagicMock):
                        with patch("json.load", return_value={}):
                            with patch("selenium.webdriver.Chrome") as mock_chrome:
                                mock_chrome.return_value = MagicMock()
                                with patch("os.makedirs"):
                                    with patch("crawler.X.PageFetcher"):
                                        crawler = XCrawler(
                                            url="https://ex.com/novel",
                                            output_dir="/tmp", sleep_time=1000,
                                            driver=True, headless=headless_val,
                                        )
        return crawler, mock_chrome

    def test_xcrawler_driver_headless_has_flag(self):
        """XCrawler with headless=True passes --headless=new to Chrome."""
        crawler, mock_chrome = self._make_xcrawler(True)
        call_kwargs = mock_chrome.call_args[1]
        self.assertIn("options", call_kwargs)
        self.assertIn("--headless=new", call_kwargs["options"].arguments)
        crawler.close()

    def test_xcrawler_driver_visible_omits_flag(self):
        """XCrawler with headless=False does NOT pass --headless=new."""
        crawler, mock_chrome = self._make_xcrawler(False)
        call_kwargs = mock_chrome.call_args[1]
        self.assertIn("options", call_kwargs)
        self.assertNotIn("--headless=new", call_kwargs["options"].arguments)
        crawler.close()


# ---------------------------------------------------------------------------
# Tests: Task 4 — headless propagation through crawler APIs
# ---------------------------------------------------------------------------


class TestNovelCrawlerHeadlessResolution(unittest.TestCase):
    """NovelCrawler resolves headless: explicit > site config > True."""

    def _crawler_with_format(self, format_data=None, headless=None, url="https://example.com/novel"):
        """Create a NovelCrawler with given format data and headless param."""
        from crawler.Novel import NovelCrawler

        format_data = format_data or {}
        with patch("crawler.Novel.extract_base_url", return_value="https://example.com"):
            with patch("pandas.read_csv"):
                with patch("builtins.open", new_callable=MagicMock):
                    with patch("json.load", return_value=format_data):
                        with patch("os.makedirs"):
                            with patch("crawler.Novel.PageFetcher"):
                                kwargs = dict(url=url, output_dir="/tmp", sleep_time=1000, driver=False)
                                if headless is not None:
                                    kwargs["headless"] = headless
                                return NovelCrawler(**kwargs)

    def test_default_resolves_to_true(self):
        crawler = self._crawler_with_format()
        self.assertTrue(crawler.headless)
        crawler.close()

    def test_explicit_false_overrides_default(self):
        crawler = self._crawler_with_format(headless=False)
        self.assertFalse(crawler.headless)
        crawler.close()

    def test_explicit_true_overrides_default(self):
        crawler = self._crawler_with_format(headless=True)
        self.assertTrue(crawler.headless)
        crawler.close()

    def test_site_config_applied_when_no_explicit(self):
        crawler = self._crawler_with_format(format_data={"fetch": {"headless": False}})
        self.assertFalse(crawler.headless)
        crawler.close()

    def test_site_config_true_applied_when_no_explicit(self):
        crawler = self._crawler_with_format(format_data={"fetch": {"headless": True}})
        self.assertTrue(crawler.headless)
        crawler.close()

    def test_explicit_overrides_site_config(self):
        crawler = self._crawler_with_format(
            format_data={"fetch": {"headless": False}}, headless=True
        )
        self.assertTrue(crawler.headless)
        crawler.close()

    def test_explicit_false_overrides_site_config_true(self):
        crawler = self._crawler_with_format(
            format_data={"fetch": {"headless": True}}, headless=False
        )
        self.assertFalse(crawler.headless)
        crawler.close()

    def test_no_url_defaults_to_true(self):
        """When url is None, no format data is loaded; headless defaults to True."""
        from crawler.Novel import NovelCrawler
        with patch("crawler.Novel.extract_base_url"):
            with patch("os.makedirs"):
                with patch("crawler.Novel.PageFetcher"):
                    crawler = NovelCrawler(
                        url=None, output_dir="/tmp", sleep_time=1000, driver=False,
                    )
        self.assertTrue(crawler.headless)
        crawler.close()

    def test_passes_resolved_to_pagefetcher(self):
        """PageFetcher receives the resolved headless value."""
        with patch("crawler.Novel.extract_base_url", return_value="https://example.com"):
            with patch("pandas.read_csv"):
                with patch("builtins.open", new_callable=MagicMock):
                    with patch("json.load", return_value={}):
                        with patch("os.makedirs"):
                            with patch("crawler.Novel.PageFetcher") as mock_pf:
                                from crawler.Novel import NovelCrawler
                                crawler = NovelCrawler(
                                    url="https://example.com/novel",
                                    output_dir="/tmp", sleep_time=1000,
                                    headless=False,
                                )
        call_kwargs = mock_pf.call_args[1]
        self.assertIn("headless", call_kwargs)
        self.assertFalse(call_kwargs["headless"])
        crawler.close()


class TestXCrawlerHeadlessResolution(unittest.TestCase):
    """XCrawler resolves headless: explicit > site config > True."""

    @patch("os.path.exists", return_value=True)
    def test_default_resolves_to_true(self, mock_exists):
        from crawler.X import XCrawler
        with patch("os.makedirs"):
            with patch("crawler.X.PageFetcher"):
                with patch("crawler.X.extract_base_url", return_value=""):
                    crawler = XCrawler(url=None, output_dir="/tmp", sleep_time=1000)
        self.assertTrue(crawler.headless)
        crawler.close()

    @patch("os.path.exists", return_value=True)
    def test_explicit_false(self, mock_exists):
        from crawler.X import XCrawler
        with patch("os.makedirs"):
            with patch("crawler.X.PageFetcher"):
                with patch("crawler.X.extract_base_url", return_value=""):
                    crawler = XCrawler(
                        url=None, output_dir="/tmp", sleep_time=1000,
                        headless=False,
                    )
        self.assertFalse(crawler.headless)
        crawler.close()

    @patch("os.path.exists", return_value=True)
    def test_site_config_applied(self, mock_exists):
        """With url, site config fetch.headless=False is respected."""
        from crawler.X import XCrawler
        with patch("crawler.X.extract_base_url", return_value="https://ex.com"):
            with patch("crawler.X.csv.DictReader") as mock_reader:
                mock_reader.return_value = [
                    {"site": "ex.com", "name": "ex_site"}
                ]
                with patch("builtins.open", new_callable=MagicMock):
                    with patch("json.load", return_value={
                        "fetch": {"headless": False}
                    }):
                        with patch("os.makedirs"):
                            with patch("crawler.X.PageFetcher"):
                                crawler = XCrawler(
                                    url="https://ex.com/novel",
                                    output_dir="/tmp", sleep_time=1000,
                                )
        self.assertFalse(crawler.headless)
        crawler.close()

    @patch("os.path.exists", return_value=True)
    def test_explicit_overrides_site_config(self, mock_exists):
        from crawler.X import XCrawler
        with patch("crawler.X.extract_base_url", return_value="https://ex.com"):
            with patch("crawler.X.csv.DictReader") as mock_reader:
                mock_reader.return_value = [
                    {"site": "ex.com", "name": "ex_site"}
                ]
                with patch("builtins.open", new_callable=MagicMock):
                    with patch("json.load", return_value={
                        "fetch": {"headless": False}
                    }):
                        with patch("os.makedirs"):
                            with patch("crawler.X.PageFetcher"):
                                crawler = XCrawler(
                                    url="https://ex.com/novel",
                                    output_dir="/tmp", sleep_time=1000,
                                    headless=True,
                                )
        self.assertTrue(crawler.headless)
        crawler.close()

    @patch("os.path.exists", return_value=True)
    def test_passes_resolved_to_pagefetcher(self, mock_exists):
        """PageFetcher receives the resolved headless value."""
        from crawler.X import XCrawler
        with patch("crawler.X.extract_base_url", return_value="https://ex.com"):
            with patch("crawler.X.csv.DictReader") as mock_reader:
                mock_reader.return_value = [
                    {"site": "ex.com", "name": "ex_site"}
                ]
                with patch("builtins.open", new_callable=MagicMock):
                    with patch("json.load", return_value={}):
                        with patch("os.makedirs"):
                            with patch("crawler.X.PageFetcher") as mock_pf:
                                crawler = XCrawler(
                                    url="https://ex.com/novel",
                                    output_dir="/tmp", sleep_time=1000,
                                    headless=True,
                                )
        call_kwargs = mock_pf.call_args[1]
        self.assertIn("headless", call_kwargs)
        self.assertTrue(call_kwargs["headless"])
        crawler.close()


# ===================================================================
# Task 5 — run() propagates headless to crawler constructors
# ===================================================================


def _run_aliases_df(class_name="NovelRequest"):
    import pandas as pd
    return pd.DataFrame([
        {"site": "example.com", "name": "testsite", "crawler_class": class_name},
    ])


class TestRunHeadlessNovelCrawler(unittest.TestCase):
    """run() passes headless to NovelCrawler (request and selenium paths)."""

    def _run_and_check(self, class_name, headless_kwarg, expected):
        from crawler.Novel import run
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df(class_name)):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("crawler.Novel.NovelCrawler") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance
                    kwargs = {"novel_url": "https://example.com/novel", "crawl_type": "full"}
                    if headless_kwarg is not None:
                        kwargs["headless"] = headless_kwarg
                    run(**kwargs)
        _, call_kwargs = mock_cls.call_args
        self.assertIn("headless", call_kwargs)
        self.assertEqual(call_kwargs["headless"], expected)

    def test_default_omits_headless(self):
        """run() without headless passes None to NovelCrawler."""
        self._run_and_check("NovelRequest", None, None)

    def test_default_none_headless(self):
        """run() with headless=None passes None to NovelCrawler."""
        self._run_and_check("NovelRequest", None, None)

    def test_explicit_true(self):
        """run(headless=True) passes True to NovelCrawler."""
        self._run_and_check("NovelRequest", True, True)

    def test_explicit_false(self):
        """run(headless=False) passes False to NovelCrawler."""
        self._run_and_check("NovelRequest", False, False)

    def test_selenium_path_passes_headless(self):
        """run() passes headless to NovelCrawler even in selenium path."""
        self._run_and_check("NovelSelenium", True, True)

    def test_selenium_path_false(self):
        """run(headless=False) reaches selenium-path NovelCrawler."""
        self._run_and_check("NovelSelenium", False, False)


class TestRunHeadlessXCrawler(unittest.TestCase):
    """run() passes headless to XCrawler."""

    def _run_xcrawler(self, headless_kwarg=None):
        from crawler.Novel import run
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("XCrawler")):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("crawler.X.XCrawler") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance
                    kwargs = dict(
                        novel_url="https://example.com/novel", crawl_type="range",
                        crawl_type_args={"start_chapter": 1, "end_chapter": 1},
                    )
                    if headless_kwarg is not None:
                        kwargs["headless"] = headless_kwarg
                    run(**kwargs)
        return mock_cls.call_args[1]

    def test_default_passes_none(self):
        kwargs = self._run_xcrawler()
        self.assertIn("headless", kwargs)
        self.assertIsNone(kwargs["headless"])

    def test_explicit_false(self):
        kwargs = self._run_xcrawler(headless_kwarg=False)
        self.assertIn("headless", kwargs)
        self.assertFalse(kwargs["headless"])


class TestRunHeadlessDoclnUnchanged(unittest.TestCase):
    """run() does NOT pass headless to DoclnCrawler."""

    def test_headless_not_passed_to_docln(self):
        from crawler.Novel import run
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("DoclnCrawler")):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("crawler.crawl_docln.DoclnCrawler") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance
                    run(novel_url="https://example.com/novel", crawl_type="full",
                        headless=False)
        _, kwargs = mock_cls.call_args
        self.assertNotIn("headless", kwargs)


# ===================================================================
# Task 6 — novel_crawl() prompts for browser visibility
# ===================================================================


class TestNovelCrawlHeadlessPrompt(unittest.TestCase):
    """novel_crawl() asks "Show browser window?" and passes headless to run()."""

    def _run_novel_crawl(self, inputs, class_name="NovelRequest"):
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df(class_name)):
            with patch("builtins.input", side_effect=inputs):
                with patch("crawler.Novel.run") as mock_run:
                    from crawler.Novel import novel_crawl
                    novel_crawl()
        return mock_run.call_args[1] if mock_run.called else {}

    def test_y_means_visible(self):
        """Input 'y' passes headless=False."""
        kwargs = self._run_novel_crawl([
            "https://example.com/novel", "", "1000",   # url, dir, sleep
            "full",                                      # crawl_type
            "",                                          # custom_volume_list (blank → None)
            "all",                                       # book_type
            "n",                                         # keep_logged_in
            "",                                          # fetch_mode
            "y",                                         # headless: y → False
            "",                                          # max_workers (blank → 4)
        ])
        self.assertIn("headless", kwargs)
        self.assertFalse(kwargs["headless"])

    def test_n_means_hidden(self):
        """Input 'n' passes headless=True."""
        kwargs = self._run_novel_crawl([
            "https://example.com/novel", "", "1000",
            "full", "",
            "all", "n", "",
            "n",
            "",
        ])
        self.assertIn("headless", kwargs)
        self.assertTrue(kwargs["headless"])

    def test_blank_means_hidden(self):
        """Blank input passes headless=True."""
        kwargs = self._run_novel_crawl([
            "https://example.com/novel", "", "1000",
            "full", "",
            "all", "n", "",
            "",
            "",
        ])
        self.assertIn("headless", kwargs)
        self.assertTrue(kwargs["headless"])

    def test_capital_Y_means_visible(self):
        """Input 'Y' (uppercase) also passes headless=False."""
        kwargs = self._run_novel_crawl([
            "https://example.com/novel", "", "1000",
            "full", "",
            "all", "n", "",
            "Y",
            "",
        ])
        self.assertIn("headless", kwargs)
        self.assertFalse(kwargs["headless"])


class TestNovelCrawlHeadlessXCrawler(unittest.TestCase):
    """XCrawler sites also get the headless prompt in novel_crawl()."""

    def _run_novel_crawl_x(self, headless_input):
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("XCrawler")):
            with patch("builtins.input", side_effect=[
                "https://example.com/novel", "", "1000",  # url, dir, sleep
                "1", "1",                                  # start, end chapter
                "all",                                     # book_type
                "n",                                       # keep_logged_in
                "",                                        # fetch_mode
                headless_input,
                "",                                        # max_workers (blank → 4)
            ]):
                with patch("crawler.Novel.run") as mock_run:
                    from crawler.Novel import novel_crawl
                    novel_crawl()
        return mock_run.call_args[1] if mock_run.called else {}

    def test_xcrawler_y_means_visible(self):
        kwargs = self._run_novel_crawl_x("y")
        self.assertFalse(kwargs["headless"])

    def test_xcrawler_n_means_hidden(self):
        kwargs = self._run_novel_crawl_x("n")
        self.assertTrue(kwargs["headless"])


# ===================================================================
# Task 9 — End-to-end: interactive values reach the crawler
# ===================================================================


class TestInteractiveEndToEndNovelCrawler(unittest.TestCase):
    """novel_crawl() interactive input reaches NovelCrawler.__init__()."""

    def _run_and_check(self, headless_input, expected):
        inputs = [
            "https://example.com/novel", "", "1000",   # url, dir, sleep
            "full",                                      # crawl_type
            "",                                          # custom_volume_list
            "all",                                       # book_type
            "n",                                         # keep_logged_in
            "",                                          # fetch_mode
            headless_input,                              # headless prompt
            "",                                          # max_workers (blank → 4)
        ]
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("NovelRequest")):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("builtins.input", side_effect=inputs):
                    with patch("crawler.Novel.NovelCrawler") as mock_cls:
                        mock_instance = MagicMock()
                        mock_cls.return_value = mock_instance
                        with patch("crawler.Novel.PageFetcher"):
                            with patch("os.path.exists", return_value=True):
                                with patch("os.makedirs"):
                                    from crawler.Novel import novel_crawl
                                    novel_crawl()
        self.assertTrue(mock_cls.called)
        _, call_kwargs = mock_cls.call_args
        self.assertIn("headless", call_kwargs)
        self.assertEqual(call_kwargs["headless"], expected)

    def test_y_reaches_novel_crawler_as_false(self):
        self._run_and_check("y", False)

    def test_n_reaches_novel_crawler_as_true(self):
        self._run_and_check("n", True)

    def test_blank_reaches_novel_crawler_as_true(self):
        self._run_and_check("", True)


class TestInteractiveEndToEndXCrawler(unittest.TestCase):
    """novel_crawl() interactive input reaches XCrawler.__init__()."""

    def _run_and_check(self, headless_input, expected):
        inputs = [
            "https://example.com/novel", "", "1000",   # url, dir, sleep
            "1", "1",                                    # start, end chapter
            "all",                                       # book_type
            "n",                                         # keep_logged_in
            "",                                          # fetch_mode
            headless_input,                              # headless prompt
            "",                                          # max_workers (blank → 4)
        ]
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("XCrawler")):
            with patch("crawler.X.csv.DictReader") as mock_reader:
                mock_reader.return_value = [
                    {"site": "example.com", "name": "testsite"}
                ]
                with patch("os.path.exists", return_value=True):
                    with patch("builtins.open", new_callable=MagicMock) as mock_file:
                        mock_file.return_value.read.return_value = "{}"
                        with patch("builtins.input", side_effect=inputs):
                            with patch("crawler.X.XCrawler") as mock_cls:
                                mock_instance = MagicMock()
                                mock_cls.return_value = mock_instance
                                with patch("crawler.X.PageFetcher"):
                                    with patch("os.makedirs"):
                                        from crawler.Novel import novel_crawl
                                        novel_crawl()
        self.assertTrue(mock_cls.called)
        _, call_kwargs = mock_cls.call_args
        self.assertIn("headless", call_kwargs)
        self.assertEqual(call_kwargs["headless"], expected)

    def test_y_reaches_xcrawler_as_false(self):
        self._run_and_check("y", False)

    def test_n_reaches_xcrawler_as_true(self):
        self._run_and_check("n", True)


# ===================================================================
# Task 7 — Configuration templates include headless
# ===================================================================


class TestFormatConfigIncludesHeadless(unittest.TestCase):
    """Site format files with a fetch block now include headless: true."""

    def test_foxaholic_fetch_has_headless(self):
        with open("data/formats/foxaholic.json") as f:
            config = json.load(f)
        fetch = config.get("fetch", {})
        self.assertIn("headless", fetch)
        self.assertFalse(fetch["headless"])

    def test_x_truyenfull_fetch_has_headless(self):
        with open("data/formats/x_truyenfull.json") as f:
            config = json.load(f)
        fetch = config.get("fetch", {})
        self.assertIn("headless", fetch)
        self.assertTrue(fetch["headless"])

    def test_docln_no_fetch_still_defaults_true(self):
        """Files without a fetch block still resolve headless=True via code."""
        from crawler.Novel import NovelCrawler
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("NovelRequest")):
            with patch("builtins.open", new_callable=mock_open) as mock_file:
                mock_file.return_value.read.return_value = json.dumps({})
                with patch("crawler.Novel.PageFetcher") as mock_pf:
                    with patch("os.path.exists", return_value=True):
                        with patch("os.makedirs"):
                            crawler = NovelCrawler(
                                url="https://example.com/novel",
                                output_dir="/tmp", sleep_time=1000,
                            )
        self.assertIsNotNone(crawler.headless)
        self.assertTrue(crawler.headless)
        crawler.close()


class TestBatchCrawlHeadless(unittest.TestCase):
    """to_crawl.json entries with headless pass it through run()."""

    def test_batch_entry_with_headless_true(self):
        """Simulates a batch-crawl dict with headless: true passed to run()."""
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("NovelRequest")):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("crawler.Novel.NovelCrawler") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance
                    from crawler.Novel import run
                    run(novel_url="https://example.com/novel", crawl_type="full",
                        headless=True)
        _, kwargs = mock_cls.call_args
        self.assertEqual(kwargs.get("headless"), True)

    def test_batch_entry_without_headless(self):
        """Batch entry without headless passes None (crawler defaults to True)."""
        with patch("crawler.Novel.pd.read_csv", return_value=_run_aliases_df("NovelRequest")):
            with patch("builtins.open", new_callable=MagicMock) as mock_file:
                mock_file.return_value.read.return_value = "{}"
                with patch("crawler.Novel.NovelCrawler") as mock_cls:
                    mock_instance = MagicMock()
                    mock_cls.return_value = mock_instance
                    from crawler.Novel import run
                    run(novel_url="https://example.com/novel", crawl_type="full")
        _, kwargs = mock_cls.call_args
        self.assertIsNone(kwargs.get("headless"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
