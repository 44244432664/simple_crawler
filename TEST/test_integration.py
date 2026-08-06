"""
Tests for Task 4 — crawler integration with PageFetcher.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_integration.py -v
"""

import io
import json
import os
import unittest
from unittest.mock import MagicMock, mock_open, patch, PropertyMock

import pandas as pd

from utils.fetcher import PageFetcher, FetchError
from crawler.Novel import NovelCrawler, run


def _mock_page_fetcher():
    """Return a mock PageFetcher instance."""
    fetcher = MagicMock(spec=PageFetcher)
    fetcher.fetch.return_value = "<html><body>Mocked PageFetcher content</body></html>"
    return fetcher


def _make_aliases_df():
    """Return a real DataFrame matching data/aliases.csv schema."""
    return pd.DataFrame([
        {"site": "example.com", "name": "testsite", "crawler_class": "NovelRequest"},
    ])


# ---------------------------------------------------------------------------
# Tests: NovelCrawler creates and uses PageFetcher
# ---------------------------------------------------------------------------


class TestNovelCrawlerPageFetcherCreation(unittest.TestCase):
    """NovelCrawler.__init__ creates a PageFetcher."""

    def test_creates_fetcher_with_default_mode(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.assertIsInstance(crawler.fetcher, PageFetcher)
        self.assertEqual(crawler.fetcher.fetch_mode, "requests")
        crawler.close()

    def test_creates_fetcher_with_browser_mode(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000, fetch_mode="browser")
        self.assertIsInstance(crawler.fetcher, PageFetcher)
        self.assertEqual(crawler.fetcher.fetch_mode, "browser")
        crawler.close()

    def test_creates_fetcher_with_auto_mode(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000, fetch_mode="auto")
        self.assertEqual(crawler.fetcher.fetch_mode, "auto")
        crawler.close()

    def test_fetcher_stored_on_instance(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.assertTrue(hasattr(crawler, "fetcher"))
        crawler.close()


# ---------------------------------------------------------------------------
# Tests: _get_page_content routing
# ---------------------------------------------------------------------------


class TestNovelCrawlerGetPageContent(unittest.TestCase):
    """_get_page_content routes through PageFetcher when driver is None."""

    def setUp(self):
        self.crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.crawler.fetcher = _mock_page_fetcher()

    def tearDown(self):
        self.crawler.close()

    def test_uses_fetcher_when_no_driver(self):
        self.assertIsNone(self.crawler.driver)
        html = self.crawler._get_page_content("https://example.com")
        self.crawler.fetcher.fetch.assert_called_once_with("https://example.com", expected_selector=None)
        self.assertIn("Mocked PageFetcher", html)

    def test_passes_url_to_fetcher(self):
        self.crawler._get_page_content("https://test.com/page")
        self.crawler.fetcher.fetch.assert_called_once_with("https://test.com/page", expected_selector=None)

    def test_uses_old_path_when_driver_set(self):
        mock_driver = MagicMock()
        mock_driver.page_source = "<html><body>Driver content</body></html>"
        self.crawler.driver = mock_driver

        with patch("crawler.Novel.get_page_content", return_value="<html><body>Old path</body></html>") as mock_old:
            html = self.crawler._get_page_content("https://example.com")
        mock_old.assert_called_once_with(mock_driver, "https://example.com")
        self.assertIn("Old path", html)
        # fetcher should NOT have been called
        self.crawler.fetcher.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: close()
# ---------------------------------------------------------------------------


class TestNovelCrawlerClose(unittest.TestCase):
    """close() cleans up fetcher and driver."""

    def test_close_calls_fetcher_close(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        crawler.fetcher = _mock_page_fetcher()
        crawler.close()
        crawler.fetcher.close.assert_called_once()

    def test_close_quits_driver_when_present(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        crawler.fetcher = _mock_page_fetcher()
        mock_driver = MagicMock()
        crawler.driver = mock_driver
        crawler.close()
        mock_driver.quit.assert_called_once()
        self.assertIsNone(crawler.driver)

    def test_close_tolerates_driver_quit_error(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        crawler.fetcher = _mock_page_fetcher()
        mock_driver = MagicMock()
        mock_driver.quit.side_effect = Exception("driver error")
        crawler.driver = mock_driver
        # Should not raise
        crawler.close()

    def test_close_safe_when_no_driver(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        crawler.fetcher = _mock_page_fetcher()
        self.assertIsNone(crawler.driver)
        # Should not raise
        crawler.close()

    def test_close_idempotent(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        crawler.fetcher = _mock_page_fetcher()
        crawler.close()
        crawler.close()  # second call should not raise


# ---------------------------------------------------------------------------
# Tests: run() cleanup
# ---------------------------------------------------------------------------


class TestRunCleanup(unittest.TestCase):
    """run() calls crawler.close() in finally."""

    @patch("crawler.Novel.NovelCrawler")
    def test_close_called_on_success(self, mock_crawler_cls):
        mock_crawler = MagicMock()
        mock_crawler_cls.return_value = mock_crawler
        expected_df = _make_aliases_df()

        with patch("crawler.Novel.pd.read_csv", return_value=expected_df) as mock_read_csv:
            run(novel_url="https://example.com/novel", crawl_type="full")

        mock_crawler.close.assert_called_once()

    @patch("crawler.Novel.NovelCrawler")
    def test_close_called_on_crawl_error(self, mock_crawler_cls):
        mock_crawler = MagicMock()
        mock_crawler_cls.return_value = mock_crawler
        mock_crawler.crawl.side_effect = Exception("crawl failed")
        expected_df = _make_aliases_df()

        with patch("crawler.Novel.pd.read_csv", return_value=expected_df):
            with self.assertRaises(Exception):
                run(novel_url="https://example.com/novel", crawl_type="full")

        mock_crawler.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: XCrawler inherits fetcher
# ---------------------------------------------------------------------------


class TestXCrawlerPageFetcher(unittest.TestCase):
    """XCrawler also creates a PageFetcher."""

    def setUp(self):
        self.aliases_patch = patch("crawler.X.csv.DictReader")
        self.mock_csv_reader = self.aliases_patch.start()
        self.mock_csv_reader.return_value = [{"site": "example.com", "name": "testsite"}]

        self.exists_patch = patch("crawler.X.os.path.exists")
        self.mock_exists = self.exists_patch.start()
        self.mock_exists.return_value = True

        self.makedirs_patch = patch("crawler.X.os.makedirs")
        self.mock_makedirs = self.makedirs_patch.start()

    def tearDown(self):
        self.aliases_patch.stop()
        self.exists_patch.stop()
        self.makedirs_patch.stop()

    @patch("crawler.X.json.load", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    def test_xcrawler_creates_fetcher(self, mock_open_file, mock_json_load):
        from crawler.X import XCrawler

        crawler = XCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            start_chapter=1,
            end_chapter=10,
            fetch_mode="auto",
        )
        self.assertIsInstance(crawler.fetcher, PageFetcher)
        self.assertEqual(crawler.fetcher.fetch_mode, "auto")
        crawler.close()

    @patch("crawler.X.json.load", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    def test_xcrawler_get_page_content_uses_fetcher(self, mock_open_file, mock_json_load):
        from crawler.X import XCrawler

        crawler = XCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            start_chapter=1,
            end_chapter=10,
            fetch_mode="requests",
        )
        crawler.fetcher = _mock_page_fetcher()

        html = crawler._get_page_content("https://example.com/chapter/1")
        crawler.fetcher.fetch.assert_called_once_with("https://example.com/chapter/1", expected_selector=None)
        self.assertIn("Mocked PageFetcher", html)
        crawler.close()


# ---------------------------------------------------------------------------
# Tests: Task 5 — Configuration and UX
# ---------------------------------------------------------------------------


class TestFormatFetchModeDefault(unittest.TestCase):
    """Site format fetch.mode is applied when fetch_mode is None."""

    def setUp(self):
        self.crawler_patcher = patch("crawler.Novel.pd.read_csv")
        self.mock_read_csv = self.crawler_patcher.start()
        mock_df = _make_aliases_df()
        self.mock_read_csv.return_value = mock_df
        self.format_patcher = patch("builtins.open", new_callable=mock_open, read_data="{}")
        self.mock_file = self.format_patcher.start()

    def tearDown(self):
        self.crawler_patcher.stop()
        self.format_patcher.stop()

    def test_format_fetch_mode_applied_when_none(self):
        """Format fetch.mode='browser' is used when fetch_mode=None."""
        self.mock_file.return_value.read.return_value = json.dumps({
            "fetch": {"mode": "browser", "cloudflare": True, "challenge_timeout_seconds": 180}
        })
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode=None,
        )
        self.assertEqual(crawler.fetch_mode, "browser")
        self.assertEqual(crawler.fetcher.fetch_mode, "browser")
        crawler.close()

    def test_explicit_fetch_mode_overrides_format(self):
        """Explicit fetch_mode='requests' overrides format's fetch.mode='auto'."""
        self.mock_file.return_value.read.return_value = json.dumps({
            "fetch": {"mode": "auto", "cloudflare": True}
        })
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode="requests",
        )
        self.assertEqual(crawler.fetch_mode, "requests")
        crawler.close()

    def test_no_fetch_block_defaults_to_requests(self):
        """Format without fetch block falls back to 'requests'."""
        self.mock_file.return_value.read.return_value = json.dumps({
            "title": {"name": "h1", "class_": "title"}
        })
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            fetch_mode=None,
        )
        self.assertEqual(crawler.fetch_mode, "requests")
        crawler.close()

    def test_no_url_defaults_to_requests(self):
        """When url is empty (dummy crawler), no format loaded — defaults to requests."""
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.assertEqual(crawler.fetch_mode, "requests")
        crawler.close()


class TestTruyenFullFormatFetchBlock(unittest.TestCase):
    """The TruyenFull format file has a fetch block with mode='auto'."""

    def test_truyenfull_has_fetch_auto(self):
        import json
        with open("data/formats/x_truyenfull.json", "r", encoding="utf-8") as f:
            fmt = json.load(f)
        fetch = fmt.get("fetch", {})
        self.assertEqual(fetch.get("mode"), "auto")
        self.assertTrue(fetch.get("cloudflare", False))
        self.assertEqual(fetch.get("challenge_timeout_seconds", 0), 180)


class TestRunFetchModeMessage(unittest.TestCase):
    """run() prints fetch mode related messages."""

    @patch("crawler.Novel.NovelCrawler")
    def test_run_prints_fetch_mode_info(self, mock_crawler_cls):
        mock_crawler = MagicMock()
        mock_crawler_cls.return_value = mock_crawler
        expected_df = _make_aliases_df()

        with patch("crawler.Novel.pd.read_csv", return_value=expected_df):
            with patch("builtins.print") as mock_print:
                run(novel_url="https://example.com/novel", crawl_type="full", fetch_mode="auto")

        # Check that fetch_mode was passed through
        _, kwargs = mock_crawler_cls.call_args
        self.assertEqual(kwargs.get("fetch_mode"), "auto")


# ---------------------------------------------------------------------------
# Tests: Task 6 — Expected selector forwarding
# ---------------------------------------------------------------------------


class TestSelectorToCss(unittest.TestCase):
    """selector_to_css converts format selector dicts to CSS strings."""

    def test_name_and_class(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "div", "class_": "content"})
        self.assertEqual(result, "div.content")

    def test_name_class_and_id(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "div", "class_": "chapter-c", "id": "chapter-c"})
        self.assertEqual(result, "div.chapter-c#chapter-c")

    def test_name_only(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "h1"})
        self.assertEqual(result, "h1")

    def test_id_only(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"id": "main-content"})
        self.assertEqual(result, "#main-content")

    def test_class_only(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"class_": "title"})
        self.assertEqual(result, ".title")

    def test_empty_dict_returns_none(self):
        from utils.novel import selector_to_css
        self.assertIsNone(selector_to_css({}))

    def test_all_empty_strings_returns_none(self):
        from utils.novel import selector_to_css
        self.assertIsNone(selector_to_css({"name": "", "class_": "", "id": ""}))

    def test_none_returns_none(self):
        from utils.novel import selector_to_css
        self.assertIsNone(selector_to_css(None))

    def test_multiple_classes(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "div", "class_": "foo bar baz"})
        self.assertEqual(result, "div.foo.bar.baz")

    def test_itemprop(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "h1", "itemprop": "name"})
        self.assertEqual(result, 'h1[itemprop="name"]')

    def test_unknown_keys_ignored(self):
        from utils.novel import selector_to_css
        result = selector_to_css({"name": "span", "other_attr": "src"})
        self.assertEqual(result, "span")


class TestExpectedSelectorForwarding(unittest.TestCase):
    """_get_page_content forwards expected_selector to fetcher."""

    def setUp(self):
        self.crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.crawler.fetcher = _mock_page_fetcher()

    def tearDown(self):
        self.crawler.close()

    def test_expected_selector_forwarded(self):
        self.crawler._get_page_content("https://example.com", expected_selector="div.content")
        self.crawler.fetcher.fetch.assert_called_once_with(
            "https://example.com", expected_selector="div.content"
        )

    def test_none_selector_forwarded_as_none(self):
        self.crawler._get_page_content("https://example.com", expected_selector=None)
        self.crawler.fetcher.fetch.assert_called_once_with(
            "https://example.com", expected_selector=None
        )

    def test_selector_not_forwarded_when_driver_set(self):
        mock_driver = MagicMock()
        self.crawler.driver = mock_driver
        with patch("crawler.Novel.get_page_content", return_value="<html/>"):
            self.crawler._get_page_content("https://example.com", expected_selector="div.x")
        self.crawler.fetcher.fetch.assert_not_called()


class TestGetAllInfoExpectedSelector(unittest.TestCase):
    """get_all_info passes its page-ready selector when fetching the info page."""

    @patch("crawler.Novel.pd.read_csv")
    @patch("builtins.open", new_callable=mock_open)
    def test_info_page_prefers_specific_ready_selector(self, mock_file, mock_read_csv):
        mock_read_csv.return_value = _make_aliases_df()
        mock_file.return_value.read.return_value = json.dumps({
            "title": {"name": "h1", "class_": "entry-title"},
            "info_page_ready": {"name": "div", "class_": "novel-summary"},
            "cover": {"name": "img", "class_": "cover"},
            "genre": {"name": "a", "class_": "genre"},
            "description": {"name": "div", "class_": "desc"},
            "other_info": {"holder": {}, "value": {}},
            "vol_group": {
                "vol_section": {"name": "div", "class_": "volume"},
                "vol_title": {"name": "h2", "class_": "vol-title"},
                "vol_cover": {},
                "chapter_list": {"name": "ul", "class_": "chapters"},
            },
            "vol_page": {
                "vol_title": {},
                "chapter_list": {},
            },
            "chapter": {
                "title": {"name": "h1", "class_": "chapter-title"},
                "content": {"name": "div", "class_": "chapter-c"},
            },
            "img_referrer": False,
        })
        crawler = NovelCrawler(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
        )
        crawler.fetcher = _mock_page_fetcher()

        # Patch get_title to avoid actual parsing
        with patch("crawler.Novel.get_title", return_value="Test Title"):
            with patch("crawler.Novel.get_genres", return_value=[]):
                with patch("crawler.Novel.get_description", return_value=""):
                    with patch("crawler.Novel.get_all_volume", return_value=[]):
                        with patch("crawler.Novel.os.makedirs"):
                            try:
                                crawler.get_all_info()
                            except Exception:
                                pass  # may raise due to incomplete mocks

        # Verify the info page fetch used the stronger readiness selector.
        crawler.fetcher.fetch.assert_any_call(
            "https://example.com/novel", expected_selector="div.novel-summary"
        )
        crawler.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
