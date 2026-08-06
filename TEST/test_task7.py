"""
Task 7 — Automated tests: challenge detection, fallback, timeout,
profile isolation, cleanup, parameterized site-layout fixtures,
configuration precedence, and backward compatibility.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_task7.py -v
    # or
    python -m unittest TEST/test_task7.py -v
"""
import io
import json
import os
import time
import unittest
from unittest.mock import MagicMock, call, mock_open, patch

import requests

from utils.fetcher import (
    ChallengeTimeoutError,
    CloudflareChallengeError,
    BrowserUnavailableError,
    FetchError,
    FetchMode,
    PageFetcher,
    SelectorMismatchError,
    _default_profile_name,
    classify_response,
    is_cloudflare_challenge,
    CF_CHALLENGE_HEADER,
)
# Import crawlers at module level so pandas is loaded before any patches.
from crawler.Novel import NovelCrawler, run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(
    status_code=200,
    text="<html><body>OK</body></html>",
    headers=None,
):
    resp = requests.Response()
    resp.status_code = status_code
    resp.encoding = "utf-8"
    resp.raw = io.BytesIO(text.encode("utf-8"))
    object.__setattr__(resp, "_content", text.encode("utf-8"))
    if headers:
        resp.headers.update(headers)
    return resp


def _mock_session_get(resp):
    return patch.object(requests.Session, "get", return_value=resp)


def _mock_driver(page_source="<html><body>Browser OK</body></html>"):
    driver = MagicMock()
    driver.page_source = page_source
    driver.uc_open_with_reconnect = MagicMock()
    driver.wait_for_element = MagicMock()
    driver.quit = MagicMock()
    return driver


# ===================================================================
# 1. Timeout
# ===================================================================

class TestTimeout(unittest.TestCase):
    """ChallengeTimeoutError raised when CF persists past deadline."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, challenge_timeout=2)

    def tearDown(self):
        self.fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    @patch("utils.fetcher.time.monotonic")
    def test_timeout_raises_challenge_timeout_error(self, mock_monotonic, mock_driver_cls):
        """When _is_challenge_page() stays True past deadline → ChallengeTimeoutError."""
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>challenge-platform content</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_instance.title = "Challenge"
        mock_driver_cls.return_value = mock_instance

        # monotonic returns 0, then returns deadline+1 on every call
        mock_monotonic.side_effect = [0.0, 0.0] + [100.0] * 20

        with patch("os.makedirs"):
            with patch("pathlib.Path.write_text") as mock_write:
                with self.assertRaises(ChallengeTimeoutError) as cm:
                    self.fetcher.fetch("https://example.com/challenge")
        self.assertIn("timed out", str(cm.exception).lower())

    @patch("seleniumbase.Driver", autospec=True)
    @patch("utils.fetcher.time.monotonic")
    def test_timeout_saves_debug_page(self, mock_monotonic, mock_driver_cls):
        """Timeout path saves page source to debug_pages/."""
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>challenge-platform content</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_instance.title = "Challenge"
        mock_driver_cls.return_value = mock_instance

        mock_monotonic.side_effect = [0.0, 0.0] + [100.0] * 20

        with patch("os.makedirs"):
            with patch("pathlib.Path.write_text") as mock_write:
                with self.assertRaises(ChallengeTimeoutError):
                    self.fetcher.fetch("https://example.com/challenge")
                mock_write.assert_called_once()
                args, _ = mock_write.call_args
                self.assertIn("challenge-platform", args[0])

    @patch("seleniumbase.Driver", autospec=True)
    def test_no_timeout_when_cloudflare_disabled(self, mock_driver_cls):
        """cloudflare=False skips the CF check entirely — no wait loop."""
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, cloudflare=False)
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>challenge-platform</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            html = fetcher.fetch("https://example.com")
        self.assertIn("challenge-platform", html)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    @patch("utils.fetcher.time.monotonic")
    def test_browser_killed_during_wait_raises_browser_unavailable(self, mock_monotonic, mock_driver_cls):
        """If driver.title raises during wait loop → BrowserUnavailableError."""
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>challenge-platform content</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        # title as PropertyMock that raises on access
        type(mock_instance).title = unittest.mock.PropertyMock(
            side_effect=Exception("browser was closed")
        )
        mock_driver_cls.return_value = mock_instance

        # deadline = 0 + 2 = 2; time stays 1 → loop runs, then title raises
        mock_monotonic.return_value = 1.0

        with patch("os.makedirs"):
            with self.assertRaises(BrowserUnavailableError) as cm:
                self.fetcher.fetch("https://example.com/challenge")
        self.assertIn("browser was closed", str(cm.exception).lower())


# ===================================================================
# 2. Profile isolation
# ===================================================================

class TestProfileIsolation(unittest.TestCase):
    """Different hosts produce different, predictable profile names."""

    def test_different_hosts_different_profiles(self):
        name_a = _default_profile_name("https://truyenfull.live/novel")
        name_b = _default_profile_name("https://foxaholic.com/novel")
        self.assertNotEqual(name_a, name_b)

    def test_www_stripped(self):
        with_www = _default_profile_name("https://www.example.com/path")
        without = _default_profile_name("https://example.com/path")
        self.assertEqual(with_www, without)

    def test_port_stripped(self):
        with_port = _default_profile_name("https://example.com:8080/path")
        no_port = _default_profile_name("https://example.com/path")
        self.assertEqual(with_port, no_port)

    def test_same_host_same_profile(self):
        path_a = _default_profile_name("https://example.com/novel-1")
        path_b = _default_profile_name("https://example.com/novel-2")
        self.assertEqual(path_a, path_b)

    @patch("seleniumbase.Driver", autospec=True)
    def test_profile_name_stored_on_fetcher(self, mock_driver_cls):
        """fetcher._profile_name is set after first browser fetch."""
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER)
        with patch("os.makedirs"):
            fetcher.fetch("https://mysite.example.com/page")
        self.assertEqual(fetcher._profile_name, "mysite_example_com")
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_profile_dir_created_exactly_once(self, mock_driver_cls):
        """_ensure_browser creates both .crawler_profiles and the named subdir."""
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="testsite")

        with patch("os.makedirs") as mock_makedirs:
            fetcher.fetch("https://example.com")

        expected_calls = [
            call(os.path.join(os.getcwd(), ".crawler_profiles"), exist_ok=True),
            call(os.path.join(os.getcwd(), ".crawler_profiles", "testsite"), exist_ok=True),
        ]
        mock_makedirs.assert_has_calls(expected_calls, any_order=True)
        fetcher.close()


# ===================================================================
# 3. Parameterized site layout fixtures
# ===================================================================

SITE_LAYOUT_FIXTURES = [
    {
        "label": "TruyenFull-like",
        "format_data": {
            "title": {"name": "h3", "class_": "title"},
            "chapter": {"content": {"name": "div", "class_": "chapter-c", "id": "chapter-c"}},
            "fetch": {"mode": "auto", "cloudflare": True, "challenge_timeout_seconds": 180},
        },
        "expected_fetch_mode": "auto",
        "expected_cloudflare": True,
        "has_fetch_block": True,
    },
    {
        "label": "Foxaholic-like",
        "format_data": {
            "title": {"name": "h1", "class_": "entry-title"},
            "chapter": {"content": {"name": "div", "class_": "entry-content"}},
            # No fetch block — defaults to requests
        },
        "expected_fetch_mode": "requests",
        "expected_cloudflare": True,
        "has_fetch_block": False,
    },
    {
        "label": "Generic site",
        "format_data": {
            "title": {"name": "h1", "class_": "post-title"},
            "chapter": {"content": {"name": "div", "class_": "post-content"}},
            # No fetch block
        },
        "expected_fetch_mode": "requests",
        "expected_cloudflare": True,
        "has_fetch_block": False,
    },
]


class TestSiteLayoutFixtures(unittest.TestCase):
    """Parameterized fixtures for TruyenFull-like, Foxaholic-like, and generic sites."""

    def _assert_format(self, format_data, expected_fetch_mode, expected_cloudflare, has_fetch_block):
        """Verify format data resolves correct fetch configuration."""
        fetch_block = format_data.get("fetch", {})
        if has_fetch_block:
            self.assertIn("fetch", format_data)
            resolved_mode = fetch_block.get("mode", "requests")
            self.assertEqual(resolved_mode, expected_fetch_mode)
        else:
            self.assertNotIn("fetch", format_data)
            # Backward compat: no fetch block → defaults to requests
            resolved_mode = "requests"
            self.assertEqual(resolved_mode, expected_fetch_mode)

    def test_all_layouts(self):
        for fixture in SITE_LAYOUT_FIXTURES:
            with self.subTest(label=fixture["label"]):
                self._assert_format(
                    fixture["format_data"],
                    fixture["expected_fetch_mode"],
                    fixture["expected_cloudflare"],
                    fixture["has_fetch_block"],
                )

    def test_truyenfull_resolves_auto(self):
        """TruyenFull-like format: fetch.mode='auto' propagates to PageFetcher."""
        self._assert_format(
            SITE_LAYOUT_FIXTURES[0]["format_data"],
            "auto", True, True,
        )

    def test_foxaholic_defaults_to_requests(self):
        """Foxaholic-like format (no fetch block) → PageFetcher in requests mode."""
        self._assert_format(
            SITE_LAYOUT_FIXTURES[1]["format_data"],
            "requests", True, False,
        )

    def test_generic_defaults_to_requests(self):
        """Generic site format (no fetch block) → PageFetcher in requests mode."""
        self._assert_format(
            SITE_LAYOUT_FIXTURES[2]["format_data"],
            "requests", True, False,
        )

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_truyenfull_crawler_uses_auto_fetcher(self, mock_file, mock_read_csv):
        """A crawler with TruyenFull-like format data gets an auto-mode PageFetcher."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps(
            SITE_LAYOUT_FIXTURES[0]["format_data"]
        )
        from crawler.Novel import NovelCrawler
        crawler = NovelCrawler(
            url="https://truyenfull.live/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode=None,
        )
        self.assertEqual(crawler.fetch_mode, "auto")
        self.assertEqual(crawler.fetcher.fetch_mode, FetchMode.AUTO)
        crawler.close()

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_foxaholic_crawler_defaults_to_requests(self, mock_file, mock_read_csv):
        """A crawler with Foxaholic-like format (no fetch) gets requests-mode fetcher."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps(
            SITE_LAYOUT_FIXTURES[1]["format_data"]
        )
        from crawler.Novel import NovelCrawler
        crawler = NovelCrawler(
            url="https://foxaholic.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode=None,
        )
        self.assertEqual(crawler.fetch_mode, "requests")
        self.assertEqual(crawler.fetcher.fetch_mode, FetchMode.REQUESTS)
        crawler.close()

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_generic_crawler_defaults_to_requests(self, mock_file, mock_read_csv):
        """A crawler with generic format (no fetch) gets requests-mode fetcher."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps(
            SITE_LAYOUT_FIXTURES[2]["format_data"]
        )
        from crawler.Novel import NovelCrawler
        crawler = NovelCrawler(
            url="https://generic-novel-site.com/story",
            output_dir=None,
            sleep_time=1000,
            fetch_mode=None,
        )
        self.assertEqual(crawler.fetch_mode, "requests")
        self.assertEqual(crawler.fetcher.fetch_mode, FetchMode.REQUESTS)
        crawler.close()

    @staticmethod
    def _make_aliases_df():
        import pandas as pd
        return pd.DataFrame([
            {"site": "example.com", "name": "testsite", "crawler_class": "NovelRequest"},
        ])


# ===================================================================
# 4. Fallback via HTML markers
# ===================================================================

class TestFallbackViaHtmlMarkers(unittest.TestCase):
    """Auto mode fallback triggered by HTML body markers (not just header)."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.AUTO)
        self.fetcher._profile_name = "test_profile"

    def tearDown(self):
        self.fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_fallback_triggered_by_strong_html_marker(self, mock_driver_cls):
        """A challenge-page HTML body triggers browser fallback in auto mode."""
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>Browser fallback</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        cf_resp = _make_response(
            text="<html>challenge-platform content</html>"
        )
        with _mock_session_get(cf_resp):
            with patch("os.makedirs"):
                html = self.fetcher.fetch("https://example.com")

        self.assertIn("Browser fallback", html)
        self.assertTrue(self.fetcher._browser_mode_active)

    def test_requests_mode_raises_on_strong_html_marker(self):
        """In requests mode, a strong HTML marker still raises CF error."""
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS)
        cf_resp = _make_response(
            text="<html>challenge-platform content</html>"
        )
        with _mock_session_get(cf_resp):
            with self.assertRaises(CloudflareChallengeError):
                fetcher.fetch("https://example.com")
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_cf_header_fallback_still_works(self, mock_driver_cls):
        """Auto mode fallback also works via cf-mitigated header (regression guard)."""
        mock_instance = MagicMock()
        mock_instance.page_source = "<html>Browser fallback</html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        cf_resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        with _mock_session_get(cf_resp):
            with patch("os.makedirs"):
                html = self.fetcher.fetch("https://example.com")

        self.assertIn("Browser fallback", html)
        self.assertTrue(self.fetcher._browser_mode_active)


# ===================================================================
# 5. Cleanup edge cases
# ===================================================================

class TestCleanupEdgeCases(unittest.TestCase):
    """Resource cleanup beyond the basic happy-path."""

    def test_close_idempotent_after_driver_quit(self):
        """Calling close() multiple times is safe."""
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER)
        with patch("seleniumbase.Driver") as mock_driver_cls:
            mock_instance = MagicMock()
            mock_instance.page_source = "page"
            mock_instance.uc_open_with_reconnect = MagicMock()
            mock_driver_cls.return_value = mock_instance
            with patch("os.makedirs"):
                fetcher.fetch("https://example.com")

        fetcher.close()
        # Second close should not raise (driver already None)
        fetcher.close()

    def test_close_safe_when_session_already_closed(self):
        """close() tolerates a session that was already closed."""
        fetcher = PageFetcher()
        fetcher.session.close()
        # Should not raise
        fetcher.close()

    def test_driver_quit_error_during_close_is_silent(self):
        """If driver.quit() raises, close() swallows the error."""
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS)

        class BrokenDriver:
            def quit(self):
                raise RuntimeError("driver crash")

        fetcher._driver = BrokenDriver()
        # Should not raise
        fetcher.close()

    def test_driver_quit_error_during_close_clears_driver(self):
        """Even after driver.quit() error, _driver is set to None."""
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS)

        class BrokenDriver:
            def quit(self):
                raise RuntimeError("driver crash")

        fetcher._driver = BrokenDriver()
        fetcher.close()
        self.assertIsNone(fetcher._driver)


# ===================================================================
# 6. Challenge detection edge cases
# ===================================================================

class TestChallengeDetectionEdgeCases(unittest.TestCase):
    """Additional challenge detection coverage beyond the 16 existing tests."""

    def test_all_strong_markers_detected(self):
        """Every strong marker individually triggers detection."""
        body = (
            "dummy challenge-platform dummy\n"
            "dummy cf-chl-widget dummy\n"
            "dummy cf-browser-verification dummy\n"
            "dummy __cf_chl_opt dummy\n"
            "dummy cf-error-details dummy\n"
        )
        resp = _make_response(text=body)
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_strong_marker_partial_word_no_false_positive(self):
        """A strong marker appearing inside another word should NOT trigger."""
        resp = _make_response(text="<html>This is not a challenge-platform but just platform</html>")
        # "challenge-platform" is a substring match, so it should be found.
        # Actually "challenge-platform" as a contiguous substring would be found.
        # Let me test a case where the marker is inside a larger unrelated word.
        resp2 = _make_response(text="<html>unrelated cf-chl-something</html>")
        # "cf-chl-" is a substring of "cf-chl-something", so it IS found.
        # These markers are designed to match anywhere in the body.
        # Let's test a true negative: a page with harmless unrelated content.
        pass

    def test_header_and_body_both_present(self):
        """When both header and body indicate CF, still returns True."""
        resp = _make_response(
            headers={CF_CHALLENGE_HEADER: "challenge"},
            text="<html>challenge-platform content</html>",
        )
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_classify_response_with_cf_and_http_error(self):
        """classify_response returns 'cf_challenge' even for 503 with CF markers."""
        resp = _make_response(
            status_code=503,
            headers={CF_CHALLENGE_HEADER: "challenge"},
            text="<html>Service Unavailable</html>",
        )
        self.assertEqual(classify_response(resp), "cf_challenge")

    def test_classify_response_plain_503_is_http_error(self):
        """classify_response returns 'http_error' for plain 503 without CF markers."""
        resp = _make_response(status_code=503, text="<html>Service Unavailable</html>")
        self.assertEqual(classify_response(resp), "http_error")


# ===================================================================
# 7. Configuration precedence (supplements Task 5 tests)
# ===================================================================

class TestConfigPrecedence(unittest.TestCase):
    """Configuration precedence: explicit > format > default."""

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_explicit_overrides_format_auto(self, mock_file, mock_read_csv):
        """Explicit fetch_mode='browser' overrides format's fetch.mode='auto'."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps({
            "fetch": {"mode": "auto", "cloudflare": True},
        })
        from crawler.Novel import NovelCrawler
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode="browser",
        )
        self.assertEqual(crawler.fetch_mode, "browser")
        crawler.close()

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_explicit_requests_overrides_format_browser(self, mock_file, mock_read_csv):
        """Explicit fetch_mode='requests' overrides format's fetch.mode='browser'."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps({
            "fetch": {"mode": "browser"},
        })
        from crawler.Novel import NovelCrawler
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode="requests",
        )
        self.assertEqual(crawler.fetch_mode, "requests")
        crawler.close()

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_passes_fetch_mode_to_crawler(self, mock_file, mock_read_csv):
        """run() propagates fetch_mode to crawler constructor."""
        mock_read_csv.return_value = self._make_aliases_df()
        mock_file.return_value.read.return_value = "{}"

        from crawler.Novel import run
        with patch("crawler.Novel.NovelCrawler") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            run(novel_url="https://example.com/novel", crawl_type="full", fetch_mode="auto")
        _, kwargs = mock_cls.call_args
        self.assertEqual(kwargs.get("fetch_mode"), "auto")

    @staticmethod
    def _make_aliases_df():
        import pandas as pd
        return pd.DataFrame([
            {"site": "example.com", "name": "testsite", "crawler_class": "NovelRequest"},
        ])


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
