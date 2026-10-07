"""Tests for the raw-page service (Task 2): get_raw_page / RawPageService."""

import unittest
from unittest import mock

from api import (
    ContentType,
    CrawlRequest,
    InvalidFlowError,
    InvalidRequestError,
    OutputFormat,
    RawPageService,
    get_raw_page,
)
from api.contracts import FetchFailureError
from utils.fetcher import (
    CloudflareChallengeError,
    FetchError,
    SelectorMismatchError,
)


def _request(**overrides):
    base = dict(
        url="https://example.com/story",
        content_type=ContentType.NOVEL,
        output_format=OutputFormat.EPUB,
    )
    base.update(overrides)
    return CrawlRequest(**base)


class TestRawPageServiceConstruction(unittest.TestCase):
    def test_defaults_to_requests_mode(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), {})
            fake_pf.assert_called_once_with(
                fetch_mode="requests",
                cloudflare=True,
                challenge_timeout=180,
                profile_name=None,
                headless=True,
                keep_logged_in=False,
            )
            service.close()

    def test_format_fetch_mode_used_when_request_is_none(self):
        fmt = {"fetch": {"mode": "auto", "headless": False, "profile_name": "mysite"}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), fmt)
            # Fetch mode resolves from the format; headless stays the
            # request's explicit boolean (True by default on this request).
            fake_pf.assert_called_once_with(
                fetch_mode="auto",
                cloudflare=True,
                challenge_timeout=180,
                profile_name="mysite",
                headless=True,
                keep_logged_in=False,
            )
            service.close()

    def test_request_headless_false_propagates(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(headless=False), {})
            self.assertEqual(fake_pf.call_args.kwargs["headless"], False)
            service.close()

    def test_keep_logged_in_propagates_to_fetcher(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(keep_logged_in=True), {})
            self.assertTrue(fake_pf.call_args.kwargs["keep_logged_in"])
            service.close()

    def test_request_max_retries_propagates_to_fetcher(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(max_retries=2), {})
            self.assertEqual(fake_pf.call_args.kwargs["client_error_retries"], 2)
            service.close()

    def test_explicit_headless_param_beats_request(self):
        fmt = {"fetch": {"mode": "auto"}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(headless=True), fmt, headless=False)
            self.assertEqual(fake_pf.call_args.kwargs["headless"], False)
            service.close()

    def test_request_fetch_mode_beats_format(self):
        fmt = {"fetch": {"mode": "auto"}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(fetch_mode="browser"), fmt)
            fake_pf.assert_called_once_with(
                fetch_mode="browser",
                cloudflare=True,
                challenge_timeout=180,
                profile_name=None,
                headless=True,
                keep_logged_in=False,
            )
            service.close()

    def test_explicit_fetch_mode_beats_request(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(fetch_mode="browser"), {}, fetch_mode="requests")
            fake_pf.assert_called_once_with(
                fetch_mode="requests",
                cloudflare=True,
                challenge_timeout=180,
                profile_name=None,
                headless=True,
                keep_logged_in=False,
            )
            service.close()

    def test_format_timeout_and_cloudflare_forwarded(self):
        fmt = {"fetch": {"mode": "browser", "cloudflare": False, "challenge_timeout_seconds": 45}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), fmt)
            fake_pf.assert_called_once_with(
                fetch_mode="browser",
                cloudflare=False,
                challenge_timeout=45,
                profile_name=None,
                headless=True,
                keep_logged_in=False,
            )
            service.close()

    def test_empty_profile_becomes_none(self):
        fmt = {"fetch": {"mode": "requests", "profile_name": "  "}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), fmt)
            self.assertEqual(fake_pf.call_args.kwargs["profile_name"], None)
            service.close()

    def test_bad_mode_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": {"mode": "socks"}})

    def test_non_dict_fetch_block_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": "requests"})

    def test_non_bool_cloudflare_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": {"cloudflare": "yes"}})

    def test_bad_timeout_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": {"challenge_timeout_seconds": "soon"}})

    def test_negative_timeout_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": {"challenge_timeout_seconds": -1}})

    def test_non_string_profile_rejected(self):
        with self.assertRaises(InvalidFlowError):
            RawPageService(_request(), {"fetch": {"profile_name": 42}})


class TestRawPageFetch(unittest.TestCase):
    def test_forwards_url_and_expected_selector(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.return_value = "<html><h1>hi</h1></html>"
            service = RawPageService(_request(), {})
            html = service.get_raw_page("https://example.com/page", ".content")
            self.assertEqual(html, "<html><h1>hi</h1></html>")
            fake_pf.return_value.fetch.assert_called_once_with(
                "https://example.com/page", ".content"
            )
            service.close()

    def test_rejects_non_http_url(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), {})
            with self.assertRaises(InvalidRequestError):
                service.get_raw_page("ftp://example.com/page")
            fake_pf.return_value.fetch.assert_not_called()
            service.close()

    def test_rejects_relative_url(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), {})
            with self.assertRaises(InvalidRequestError):
                service.get_raw_page("//missing-host/path")
            fake_pf.return_value.fetch.assert_not_called()
            service.close()

    def test_cloudflare_challenge_maps_to_fetch_failure(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.side_effect = CloudflareChallengeError("cf")
            service = RawPageService(_request(), {})
            with self.assertRaises(FetchFailureError):
                service.get_raw_page("https://example.com/page")
            service.close()

    def test_selector_mismatch_maps_to_fetch_failure(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.side_effect = SelectorMismatchError(".nope")
            service = RawPageService(_request(), {})
            with self.assertRaises(FetchFailureError):
                service.get_raw_page("https://example.com/page", ".nope")
            service.close()

    def test_fetch_error_maps_to_fetch_failure(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.side_effect = FetchError("http 500")
            service = RawPageService(_request(), {})
            with self.assertRaises(FetchFailureError):
                service.get_raw_page("https://example.com/page")

    def test_fetch_failure_redacts_query_values(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.side_effect = FetchError("network error")
            service = RawPageService(_request(), {})
            with self.assertRaises(FetchFailureError) as ctx:
                service.get_raw_page("https://example.com/page?token=TOPSECRET")
            self.assertNotIn("TOPSECRET", str(ctx.exception))
            service.close()

    def test_close_is_idempotent(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            service = RawPageService(_request(), {})
            service.close()
            service.close()
            fake_pf.return_value.close.assert_called_once()

    def test_closed_service_cannot_fetch(self):
        with mock.patch("api.raw_page.PageFetcher"):
            service = RawPageService(_request(), {})
            service.close()
            with self.assertRaises(InvalidRequestError):
                service.get_raw_page("https://example.com/page")

    def test_session_reuse_across_calls(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.return_value = "<html>"
            service = RawPageService(_request(), {})
            service.get_raw_page("https://example.com/a")
            service.get_raw_page("https://example.com/b")
            fake_pf.assert_called_once()
            self.assertEqual(fake_pf.return_value.fetch.call_count, 2)
            service.close()


class TestGetRawPageFunction(unittest.TestCase):
    def test_fetches_and_closes(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.return_value = "<html>ok</html>"
            html = get_raw_page(_request(), "example.com/page")
            self.assertEqual(html, "<html>ok</html>")
            fake_pf.return_value.fetch.assert_called_once_with(
                "https://example.com/page", None
            )
            fake_pf.return_value.close.assert_called_once()

    def test_closes_on_error(self):
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.side_effect = FetchError("boom")
            with self.assertRaises(FetchFailureError):
                get_raw_page(_request(), "https://example.com/page")
            fake_pf.return_value.close.assert_called_once()

    def test_passes_format_definition(self):
        fmt = {"fetch": {"mode": "browser"}}
        with mock.patch("api.raw_page.PageFetcher") as fake_pf:
            fake_pf.return_value.fetch.return_value = "<html>"
            get_raw_page(_request(), "https://example.com/page", format_definition=fmt)
            self.assertEqual(fake_pf.call_args.kwargs["fetch_mode"], "browser")


if __name__ == "__main__":
    unittest.main()
