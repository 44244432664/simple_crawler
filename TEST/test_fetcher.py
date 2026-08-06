"""
Unit tests for utils.fetcher.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_fetcher.py -v
    # or
    python -m unittest TEST/test_fetcher.py -v
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

import requests

from utils.fetcher import (
    FetchMode,
    FetchError,
    CloudflareChallengeError,
    SelectorMismatchError,
    BrowserUnavailableError,
    ChallengeTimeoutError,
    PageFetcher,
    PageFetcherError,
    _default_profile_name,
    classify_response,
    is_cloudflare_challenge,
    CF_CHALLENGE_HEADER,
    PROFILES_DIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code=200,
    text="<html><body>OK</body></html>",
    headers=None,
):
    """Build a minimal ``requests.Response``-like object."""
    resp = requests.Response()
    resp.status_code = status_code
    resp.encoding = "utf-8"
    resp.raw = io.BytesIO(text.encode("utf-8"))
    # _content must be set so that resp.text works
    object.__setattr__(resp, "_content", text.encode("utf-8"))
    if headers:
        resp.headers.update(headers)
    return resp


def _mock_session_get(resp):
    """Return a patcher that makes ``session.get`` return *resp*."""
    patcher = patch.object(requests.Session, "get", return_value=resp)
    return patcher


def _mock_driver(page_source="<html><body>Browser OK</body></html>"):
    """Return a mock seleniumbase Driver instance."""
    driver = MagicMock()
    driver.page_source = page_source
    driver.uc_open_with_reconnect = MagicMock()
    driver.wait_for_element = MagicMock()
    driver.quit = MagicMock()
    return driver


# ---------------------------------------------------------------------------
# Tests: is_cloudflare_challenge
# ---------------------------------------------------------------------------


class TestCloudflareDetection(unittest.TestCase):
    """Cloudflare challenge detection – header and HTML markers."""

    def test_cf_header_present(self):
        resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_cf_header_missing_plain_page(self):
        resp = _make_response()
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_html_marker_challenge_platform(self):
        resp = _make_response(text="<html>challenge-platform content</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_html_marker_cf_chl(self):
        resp = _make_response(text="<html>cf-chl-widget</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_html_marker_just_a_moment(self):
        # Weak markers only trigger when status >= 400
        resp = _make_response(status_code=503, text="<html>Just a moment...</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_html_marker_just_a_moment_200_not_flagged(self):
        resp = _make_response(status_code=200, text="<html>Just a moment...</html>")
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_html_marker_attention_required(self):
        # Weak markers only trigger when status >= 400
        resp = _make_response(status_code=403, text="<html>Attention Required! | Cloudflare</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_plain_403_not_misclassified(self):
        """A plain 403 without Cloudflare markers must NOT be detected."""
        resp = _make_response(status_code=403, text="<html>Forbidden</html>")
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_plain_500_not_misclassified(self):
        resp = _make_response(status_code=500, text="<html>Server Error</html>")
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_empty_body_not_misclassified(self):
        resp = _make_response(text="")
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_cf_503_with_weak_marker_is_detected(self):
        resp = _make_response(status_code=503, text="<html>Just a moment...</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_cf_403_with_weak_marker_is_detected(self):
        resp = _make_response(
            status_code=403,
            text="<html><title>Attention Required! | Cloudflare</title></html>",
        )
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_weak_marker_200_not_detected(self):
        """'Just a moment' on a 200 page should NOT be flagged."""
        resp = _make_response(
            status_code=200,
            text="<html>Please wait just a moment while we load your content...</html>",
        )
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_normal_html_not_detected(self):
        resp = _make_response(text="<html><body>Welcome to my website</body></html>")
        self.assertFalse(is_cloudflare_challenge(resp))

    def test_case_insensitive_marker_matching(self):
        resp = _make_response(text="<html>CHALLENGE-PLATFORM</html>")
        self.assertTrue(is_cloudflare_challenge(resp))

    def test_binary_response_not_detected(self):
        resp = _make_response(text="\x00\x01\x02\x03")
        self.assertFalse(is_cloudflare_challenge(resp))


# ---------------------------------------------------------------------------
# Tests: classify_response
# ---------------------------------------------------------------------------


class TestClassifyResponse(unittest.TestCase):
    """Response classification."""

    def test_cf_challenge_returns_cf_challenge(self):
        resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        self.assertEqual(classify_response(resp), "cf_challenge")

    def test_ok_returns_ok(self):
        resp = _make_response()
        self.assertEqual(classify_response(resp), "ok")

    def test_http_error_returns_http_error(self):
        resp = _make_response(status_code=404, text="Not Found")
        self.assertEqual(classify_response(resp), "http_error")

    def test_403_without_cf_markers_is_http_error(self):
        resp = _make_response(status_code=403, text="<html>Forbidden</html>")
        self.assertEqual(classify_response(resp), "http_error")

    def test_403_with_cf_markers_is_cf_challenge(self):
        resp = _make_response(
            status_code=403,
            text="<html>challenge-platform content</html>",
        )
        self.assertEqual(classify_response(resp), "cf_challenge")


# ---------------------------------------------------------------------------
# Tests: Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy(unittest.TestCase):
    """Exception inheritance and specificity."""

    def test_cloudflare_challenge_is_fetch_error(self):
        self.assertTrue(issubclass(CloudflareChallengeError, FetchError))

    def test_cloudflare_challenge_is_page_fetcher_error(self):
        self.assertTrue(issubclass(CloudflareChallengeError, PageFetcherError))

    def test_selector_mismatch_is_page_fetcher_error(self):
        self.assertTrue(issubclass(SelectorMismatchError, PageFetcherError))

    def test_selector_mismatch_not_fetch_error(self):
        self.assertFalse(issubclass(SelectorMismatchError, FetchError))

    def test_fetch_not_cloudflare_error_not_caught_as_cf(self):
        """A plain FetchError (network) should NOT be a CloudflareChallengeError."""
        err = FetchError("connection refused")
        self.assertNotIsInstance(err, CloudflareChallengeError)


# ---------------------------------------------------------------------------
# Tests: PageFetcher — requests mode
# ---------------------------------------------------------------------------


class TestPageFetcherRequestsMode(unittest.TestCase):
    """PageFetcher in ``requests`` mode."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS)

    def tearDown(self):
        self.fetcher.close()

    def test_returns_html(self):
        resp = _make_response()
        with _mock_session_get(resp):
            html = self.fetcher.fetch("https://example.com")
        self.assertEqual(html, resp.text)

    def test_cloudflare_header_raises(self):
        resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        with _mock_session_get(resp):
            with self.assertRaises(CloudflareChallengeError) as cm:
                self.fetcher.fetch("https://example.com")
        self.assertIn("Cloudflare challenge", str(cm.exception))

    def test_cloudflare_html_marker_raises(self):
        resp = _make_response(text="<html>challenge-platform</html>")
        with _mock_session_get(resp):
            with self.assertRaises(CloudflareChallengeError) as cm:
                self.fetcher.fetch("https://example.com")
        self.assertIn("Cloudflare challenge", str(cm.exception))

    def test_cloudflare_off_does_not_detect(self):
        """When cloudflare=False the fetcher must NOT check for markers."""
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS, cloudflare=False)
        resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        with _mock_session_get(resp):
            html = fetcher.fetch("https://example.com")
        self.assertEqual(html, resp.text)
        fetcher.close()

    def test_network_error_propagates(self):
        with patch.object(
            requests.Session, "get", side_effect=requests.ConnectionError("refused")
        ):
            with self.assertRaises(FetchError) as cm:
                self.fetcher.fetch("https://example.com")
        self.assertIn("refused", str(cm.exception))

    def test_403_raises_without_cloudflare_classification(self):
        """Plain 403 without CF markers is a FetchError, not misclassified."""
        resp = _make_response(status_code=403, text="<html>Forbidden</html>")
        fetcher = PageFetcher(
            fetch_mode=FetchMode.REQUESTS,
            client_error_retries=0,
        )
        with _mock_session_get(resp):
            with self.assertRaises(FetchError):
                fetcher.fetch("https://example.com")
        fetcher.close()

    def test_retries_non_cloudflare_4xx_then_returns_success(self):
        """Temporary 4xx responses are retried with a warning."""
        first = _make_response(status_code=429, text="<html>Too Many Requests</html>")
        second = _make_response(text="<html>Recovered</html>")
        fetcher = PageFetcher(
            fetch_mode=FetchMode.REQUESTS,
            client_error_retries=2,
            client_error_retry_delay=0,
        )

        with patch.object(requests.Session, "get", side_effect=[first, second]) as get, \
             self.assertLogs("PageFetcher", level="WARNING") as logs:
            html = fetcher.fetch("https://example.com")

        self.assertEqual(html, second.text)
        self.assertEqual(get.call_count, 2)
        self.assertIn("HTTP 429", logs.output[0])
        self.assertIn("retrying client error", logs.output[0])
        fetcher.close()

    def test_stops_immediately_on_5xx(self):
        """Server errors are reported but are not retried or sent to Chrome."""
        response = _make_response(
            status_code=503,
            text="<html>Unavailable</html>",
            headers={CF_CHALLENGE_HEADER: "challenge"},
        )
        fetcher = PageFetcher(
            fetch_mode=FetchMode.REQUESTS,
            client_error_retries=3,
            client_error_retry_delay=0,
        )

        with _mock_session_get(response) as get, \
             self.assertLogs("PageFetcher", level="WARNING") as logs:
            with self.assertRaises(FetchError) as cm:
                fetcher.fetch("https://example.com")

        self.assertEqual(get.call_count, 1)
        self.assertIn("HTTP 503", str(cm.exception))
        self.assertIn("will not retry", str(cm.exception))
        self.assertNotIsInstance(cm.exception, CloudflareChallengeError)
        self.assertIn("stopping immediately", logs.output[0])
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: PageFetcher — browser mode
# ---------------------------------------------------------------------------


class TestPageFetcherBrowserMode(unittest.TestCase):
    """PageFetcher in ``browser`` mode."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER)

    def tearDown(self):
        self.fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_starts_driver_and_returns_page_source(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.page_source = "<html><body>Browser content</body></html>"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            html = self.fetcher.fetch("https://example.com")

        self.assertIn("Browser content", html)
        mock_instance.uc_open_with_reconnect.assert_called_once_with(
            "https://example.com", reconnect_time=3
        )

    @patch("seleniumbase.Driver", autospec=True)
    def test_reuses_driver_across_fetches(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            self.fetcher.fetch("https://example.com/page1")
            self.fetcher.fetch("https://example.com/page2")

        self.assertEqual(mock_driver_cls.call_count, 1)
        self.assertEqual(mock_instance.uc_open_with_reconnect.call_count, 2)

    @patch("seleniumbase.Driver", autospec=True)
    def test_expected_selector_passed_through(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            self.fetcher.fetch(
                "https://example.com", expected_selector="#chapter-c"
            )

        mock_instance.wait_for_element.assert_called_once_with(
            "#chapter-c", timeout=10
        )

    @patch("seleniumbase.Driver", autospec=True)
    def test_expected_selector_not_found_raises_selector_error(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_instance.wait_for_element.side_effect = Exception("not found")
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            with self.assertRaises(SelectorMismatchError) as cm:
                self.fetcher.fetch(
                    "https://example.com", expected_selector="#missing"
                )
        self.assertIn("#missing", str(cm.exception))

    def test_browser_unavailable_when_seleniumbase_missing(self):
        """Simulate seleniumbase being unavailable by removing it from sys.modules."""
        import sys
        old_modules = {}
        for mod in list(sys.modules.keys()):
            if mod.startswith("seleniumbase"):
                old_modules[mod] = sys.modules.pop(mod)

        with patch("os.makedirs"):
            with self.assertRaises(BrowserUnavailableError):
                self.fetcher.fetch("https://example.com")

        # Restore
        sys.modules.update(old_modules)

    @patch("seleniumbase.Driver", autospec=True)
    def test_navigation_error_raises_fetch_error(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.uc_open_with_reconnect.side_effect = Exception("timeout")
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            with self.assertRaises(FetchError) as cm:
                self.fetcher.fetch("https://example.com")
        self.assertIn("timeout", str(cm.exception))

    @patch("seleniumbase.Driver", autospec=True)
    def test_visible_browser_waits_for_missing_content_selector(self, mock_driver_cls):
        """A headed Cloudflare session waits even when no marker is present."""
        fetcher = PageFetcher(
            fetch_mode=FetchMode.BROWSER,
            cloudflare=True,
            challenge_timeout=0,
            headless=False,
        )
        mock_instance = _mock_driver("<html><body>Access denied</body></html>")
        mock_instance.find_elements.return_value = []
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"), patch("pathlib.Path.write_text"):
            with self.assertRaises(ChallengeTimeoutError) as cm:
                fetcher.fetch(
                    "https://example.com/novel", expected_selector='h1[itemprop="name"]'
                )

        self.assertIn("Manual Cloudflare verification timed out", str(cm.exception))
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: PageFetcher — auto mode
# ---------------------------------------------------------------------------


class TestPageFetcherAutoMode(unittest.TestCase):
    """PageFetcher in ``auto`` mode."""

    def setUp(self):
        self.fetcher = PageFetcher(fetch_mode=FetchMode.AUTO)
        self.fetcher._profile_name = "test_profile"

    def tearDown(self):
        self.fetcher.close()

    def test_uses_requests_when_no_cloudflare(self):
        resp = _make_response()
        with _mock_session_get(resp):
            html = self.fetcher.fetch("https://example.com")
        self.assertEqual(html, resp.text)
        self.assertFalse(self.fetcher._browser_mode_active)

    @patch("seleniumbase.Driver", autospec=True)
    def test_falls_back_to_browser_on_cf_header(self, mock_driver_cls):
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
        self.assertIsNotNone(self.fetcher._driver)

    def test_visible_mode_falls_back_when_expected_content_is_missing(self):
        """A generic HTTP success page opens Chrome for manual recovery."""
        fetcher = PageFetcher(fetch_mode=FetchMode.AUTO, headless=False)
        generic_resp = _make_response(
            text="<html><body><h1>Blocked</h1></body></html>"
        )
        with _mock_session_get(generic_resp), patch.object(
            fetcher, "_browser_fetch", return_value="browser page"
        ) as browser_fetch:
            html = fetcher.fetch(
                "https://example.com/novel", expected_selector='h1[itemprop="name"]'
            )

        self.assertEqual(html, "browser page")
        self.assertTrue(fetcher._browser_mode_active)
        browser_fetch.assert_called_once_with(
            "https://example.com/novel", 'h1[itemprop="name"]'
        )
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_second_fetch_uses_browser_after_fallback(self, mock_driver_cls):
        """After falling back, subsequent requests stay in browser mode."""
        mock_instance = MagicMock()
        mock_instance.page_source = "browser_page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        # First fetch triggers fallback
        cf_resp = _make_response(headers={CF_CHALLENGE_HEADER: "challenge"})
        with _mock_session_get(cf_resp):
            with patch("os.makedirs"):
                self.fetcher.fetch("https://example.com/page1")

        # Second fetch should go straight to browser (no requests attempted)
        mock_instance.uc_open_with_reconnect.reset_mock()
        with patch("os.makedirs"):
            html2 = self.fetcher.fetch("https://example.com/page2")

        self.assertEqual(html2, "browser_page")
        self.assertEqual(mock_instance.uc_open_with_reconnect.call_count, 1)
        # Verify browser was reused (Driver constructor called once total)
        self.assertEqual(mock_driver_cls.call_count, 1)

    def test_skips_cloudflare_detection_when_disabled(self):
        """When cloudflare=False, auto mode returns the page as-is."""
        fetcher = PageFetcher(fetch_mode=FetchMode.AUTO, cloudflare=False)

        cf_resp = _make_response(
            headers={CF_CHALLENGE_HEADER: "challenge"},
            text="<html>challenge page</html>",
        )
        with _mock_session_get(cf_resp):
            html = fetcher.fetch("https://example.com")

        self.assertIn("challenge page", html)
        self.assertFalse(fetcher._browser_mode_active)
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: close()
# ---------------------------------------------------------------------------


class TestPageFetcherClose(unittest.TestCase):
    """Resource cleanup."""

    def test_close_session(self):
        fetcher = PageFetcher()
        with patch.object(fetcher.session, "close") as mock_close:
            fetcher.close()
        mock_close.assert_called_once()

    @patch("seleniumbase.Driver", autospec=True)
    def test_close_quits_driver(self, mock_driver_cls):
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER)
        mock_instance = MagicMock()
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            fetcher.fetch("https://example.com")

        fetcher.close()
        mock_instance.quit.assert_called_once()

    def test_close_safe_when_driver_never_started(self):
        fetcher = PageFetcher(fetch_mode=FetchMode.REQUESTS)
        # Should not raise
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_close_does_not_raise_on_driver_quit_error(self, mock_driver_cls):
        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER)
        mock_instance = MagicMock()
        mock_instance.quit.side_effect = Exception("quit error")
        mock_driver_cls.return_value = mock_instance

        with patch("os.makedirs"):
            fetcher.fetch("https://example.com")

        # Should not raise
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: Profile naming
# ---------------------------------------------------------------------------


class TestProfileNaming(unittest.TestCase):
    """Profile name derivation."""

    def test_default_from_url(self):
        name = _default_profile_name("https://truyenfull.live/novel")
        self.assertEqual(name, "truyenfull_live")

    def test_default_strips_www(self):
        name = _default_profile_name("https://www.example.com/path")
        self.assertEqual(name, "example_com")

    def test_default_handles_port(self):
        name = _default_profile_name("https://example.com:8080/path")
        self.assertEqual(name, "example_com")


# ---------------------------------------------------------------------------
# Tests: Configuration and edge cases
# ---------------------------------------------------------------------------


class TestPageFetcherConfiguration(unittest.TestCase):
    """Initialization and configuration."""

    def test_invalid_fetch_mode_raises(self):
        with self.assertRaises(ValueError):
            PageFetcher(fetch_mode="invalid")

    def test_valid_fetch_modes(self):
        for mode in (FetchMode.REQUESTS, FetchMode.BROWSER, FetchMode.AUTO):
            fetcher = PageFetcher(fetch_mode=mode)
            self.assertEqual(fetcher.fetch_mode, mode)
            fetcher.close()

    def test_session_has_default_headers(self):
        fetcher = PageFetcher()
        ua = fetcher.session.headers.get("User-Agent", "")
        self.assertIn("Chrome/120", ua)
        fetcher.close()

    def test_custom_profile_name(self):
        fetcher = PageFetcher(profile_name="custom_site")
        self.assertEqual(fetcher._profile_name, "custom_site")
        fetcher.close()

    def test_challenge_timeout_default(self):
        fetcher = PageFetcher()
        self.assertEqual(fetcher.challenge_timeout, 180)
        fetcher.close()

    def test_cloudflare_flag_default_true(self):
        fetcher = PageFetcher()
        self.assertTrue(fetcher.cloudflare)
        fetcher.close()

    @patch("seleniumbase.Driver", autospec=True)
    def test_profile_dir_created(self, mock_driver_cls):
        mock_instance = MagicMock()
        mock_instance.page_source = "page"
        mock_instance.uc_open_with_reconnect = MagicMock()
        mock_driver_cls.return_value = mock_instance

        fetcher = PageFetcher(fetch_mode=FetchMode.BROWSER, profile_name="mysite")

        with patch("os.makedirs") as mock_makedirs:
            fetcher.fetch("https://example.com")

        # Should create both .crawler_profiles and .crawler_profiles/mysite
        self.assertGreaterEqual(mock_makedirs.call_count, 1)
        call_dirs = [call[0][0] for call in mock_makedirs.call_args_list]
        any_profile = any("mysite" in d for d in call_dirs)
        self.assertTrue(any_profile, f"Expected mysite in {call_dirs}")
        fetcher.close()


# ---------------------------------------------------------------------------
# Tests: FetchMode validation
# ---------------------------------------------------------------------------


class TestFetchMode(unittest.TestCase):
    def test_is_valid(self):
        self.assertTrue(FetchMode.is_valid("requests"))
        self.assertTrue(FetchMode.is_valid("browser"))
        self.assertTrue(FetchMode.is_valid("auto"))
        self.assertFalse(FetchMode.is_valid(""))
        self.assertFalse(FetchMode.is_valid("selenium"))
        self.assertFalse(FetchMode.is_valid(None))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
