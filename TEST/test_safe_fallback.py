"""Tests for Task 5 — safe sequential fallbacks.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_safe_fallback.py -v
"""

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from crawler.Novel import NovelCrawler
from crawler.X import XCrawler
from crawler.crawl_docln import DoclnCrawler
from utils.worker_config import ChapterJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crawler(cls=NovelCrawler, fetch_mode="requests", keep_logged_in=False,
                  driver=None, max_workers=4, supports_parallel=True):
    """Create a minimal crawler instance without hitting the network."""
    crawler = cls.__new__(cls)
    crawler.output_dir = tempfile.mkdtemp() + "/"
    crawler.sleep_time = 0
    crawler.max_workers = max_workers
    crawler.update_log = lambda msg, new=False: None
    crawler._pacer = MagicMock()
    crawler._worker_fetcher_factory = MagicMock()
    crawler.fetcher = MagicMock()
    crawler.driver = driver
    crawler.keep_logged_in = keep_logged_in
    crawler.format_data = {"chapter": {"content": {}, "title": {}, "image": {}}}
    crawler.base_url = "https://example.com"
    crawler.fetch_mode = fetch_mode
    crawler._supports_parallel = supports_parallel
    return crawler


def _fake_work_factory(delay=0.01):
    """Return a mock _crawl_chapter_with_retries that succeeds."""

    def _fake_retries(chapter_url, img_output_dir=None, img_prefix="",
                      chapter_label=None, max_retries=5):
        return {
            "chapter_title": f"Title for {chapter_url}",
            "chapter_content": f"<p>Content for {chapter_url}</p>",
            "chapter_img_folder": None,
        }

    return _fake_retries


def _make_jobs(n):
    return [
        ChapterJob(
            position=i,
            url=f"https://example.com/ch{i}",
            img_prefix=f"vol1_chap{i+1}",
            label=f"vol1_chapter_{i+1}",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _is_parallel_safe
# ---------------------------------------------------------------------------


class TestIsParallelSafe(unittest.TestCase):
    """_is_parallel_safe returns True only when all conditions are met."""

    def test_requests_mode_no_login_no_driver(self):
        crawler = _make_crawler(fetch_mode="requests", keep_logged_in=False, driver=None)
        self.assertTrue(crawler._is_parallel_safe())

    def test_auto_mode_not_safe(self):
        crawler = _make_crawler(fetch_mode="auto")
        self.assertFalse(crawler._is_parallel_safe())

    def test_browser_mode_not_safe(self):
        crawler = _make_crawler(fetch_mode="browser")
        self.assertFalse(crawler._is_parallel_safe())

    def test_keep_logged_in_not_safe(self):
        crawler = _make_crawler(fetch_mode="requests", keep_logged_in=True)
        self.assertFalse(crawler._is_parallel_safe())

    def test_driver_active_not_safe(self):
        crawler = _make_crawler(fetch_mode="requests", driver=MagicMock())
        self.assertFalse(crawler._is_parallel_safe())

    def test_supports_parallel_false_not_safe(self):
        crawler = _make_crawler(fetch_mode="requests", supports_parallel=False)
        self.assertFalse(crawler._is_parallel_safe())

    def test_multiple_reasons_compound(self):
        crawler = _make_crawler(
            fetch_mode="browser", keep_logged_in=True, driver=MagicMock(), supports_parallel=False
        )
        self.assertFalse(crawler._is_parallel_safe())

    def test_requests_only_mode_string(self):
        """Only the exact string 'requests' is accepted."""
        crawler = _make_crawler(fetch_mode="Requests")
        self.assertFalse(crawler._is_parallel_safe())


# ---------------------------------------------------------------------------
# Class-level _supports_parallel
# ---------------------------------------------------------------------------


class TestSupportsParallelFlag(unittest.TestCase):
    """NovelCrawler defaults to True, DoclnCrawler overrides to False."""

    def test_novelcrawler_default_true(self):
        self.assertTrue(NovelCrawler._supports_parallel)

    def test_xcrawler_inherits_true(self):
        self.assertTrue(XCrawler._supports_parallel)

    def test_doclncrawler_false(self):
        self.assertFalse(DoclnCrawler._supports_parallel)


# ---------------------------------------------------------------------------
# _crawl_chapters_sequentially
# ---------------------------------------------------------------------------


class TestCrawlChaptersSequentially(unittest.TestCase):
    """Sequential fallback produces ordered results without creating an executor."""

    def test_returns_ordered_results(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(4)
        results = crawler._crawl_chapters_sequentially(jobs)
        self.assertEqual(len(results), 4)
        for i, r in enumerate(results):
            self.assertIn(f"ch{i}", r["chapter_title"])

    def test_empty_jobs_returns_empty(self):
        crawler = _make_crawler()
        results = crawler._crawl_chapters_sequentially([])
        self.assertEqual(results, [])

    def test_sequential_path_runs_each_job_once(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = MagicMock(side_effect=_fake_work_factory())
        jobs = _make_jobs(2)
        crawler._crawl_chapters_sequentially(jobs)
        self.assertEqual(crawler._crawl_chapter_with_retries.call_count, 2)

    def test_failed_chapters_omitted(self):
        def _failing_retries(chapter_url, img_output_dir=None, img_prefix="",
                             chapter_label=None, max_retries=5):
            if "ch1" in chapter_url:
                return None
            return {"chapter_title": f"Title for {chapter_url}", "chapter_content": "", "chapter_img_folder": None}

        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _failing_retries
        jobs = _make_jobs(3)
        results = crawler._crawl_chapters_sequentially(jobs)
        self.assertEqual(len(results), 2)


# ---------------------------------------------------------------------------
# _schedule_chapters fallback routing
# ---------------------------------------------------------------------------


class TestScheduleChaptersFallback(unittest.TestCase):
    """_schedule_chapters routes to sequential or threaded path as appropriate."""

    def test_max_workers_one_always_sequential(self):
        """max_workers=1 uses sequential path without any fallback message."""
        crawler = _make_crawler(max_workers=1)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(3)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = [{"chapter_title": "T", "chapter_content": "", "chapter_img_folder": None}] * 3
            crawler._schedule_chapters(jobs)
            mock_seq.assert_called_once()

    def test_auto_mode_falls_back_with_message(self):
        """auto mode with max_workers>1 prints a fallback message."""
        crawler = _make_crawler(fetch_mode="auto", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(3)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                # Should have printed a fallback message
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertTrue(any("falling back to sequential" in c.lower() for c in print_calls))

    def test_browser_mode_falls_back(self):
        crawler = _make_crawler(fetch_mode="browser", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(2)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertTrue(any("fetch_mode=" in c for c in print_calls))

    def test_keep_logged_in_falls_back(self):
        crawler = _make_crawler(fetch_mode="requests", keep_logged_in=True, max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(2)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertTrue(any("keep_logged_in=True" in c for c in print_calls))

    def test_driver_active_falls_back(self):
        crawler = _make_crawler(fetch_mode="requests", driver=MagicMock(), max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(2)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertTrue(any("driver=active" in c for c in print_calls))

    def test_supports_parallel_false_falls_back(self):
        crawler = _make_crawler(fetch_mode="requests", supports_parallel=False, max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(2)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertTrue(any("independent sessions" in c for c in print_calls))

    def test_no_fallback_message_when_max_workers_one(self):
        """max_workers=1 should NOT print a fallback message."""
        crawler = _make_crawler(fetch_mode="auto", max_workers=1)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(2)

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = []
            with patch("builtins.print") as mock_print:
                crawler._schedule_chapters(jobs)
                print_calls = [str(c) for c in mock_print.call_args_list]
                self.assertFalse(any("falling back" in c.lower() for c in print_calls))

    def test_safe_mode_uses_chapter_scheduler(self):
        """Requests mode with no login/driver should use the scheduler."""
        crawler = _make_crawler(fetch_mode="requests", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()
        jobs = _make_jobs(3)

        with patch("crawler.Novel.ChapterScheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.run.return_value = []
            MockScheduler.return_value = mock_instance
            crawler._schedule_chapters(jobs)
            MockScheduler.assert_called_once()

    def test_empty_jobs_returns_immediately(self):
        crawler = _make_crawler()
        results = crawler._schedule_chapters([])
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Integration with crawl paths
# ---------------------------------------------------------------------------


class TestCrawlPathFallback(unittest.TestCase):
    """Crawl methods use the fallback when conditions are unsafe."""

    def test_crawl_auto_mode_uses_sequential(self):
        crawler = _make_crawler(cls=NovelCrawler, fetch_mode="auto", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volumes = [{"title": "Vol 1", "chapter_links": ["https://example.com/ch1"]}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = [{"chapter_title": "T", "chapter_content": "", "chapter_img_folder": None}]
            crawler.crawl()
            mock_seq.assert_called_once()

    def test_crawl_requests_mode_uses_scheduler(self):
        crawler = _make_crawler(cls=NovelCrawler, fetch_mode="requests", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volumes = [{"title": "Vol 1", "chapter_links": ["https://example.com/ch1"]}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        with patch.object(crawler, "_schedule_chapters") as mock_sched:
            mock_sched.return_value = [{"chapter_title": "T", "chapter_content": "", "chapter_img_folder": None}]
            crawler.crawl()
            mock_sched.assert_called_once()

    def test_crawl_range_auto_mode_uses_sequential(self):
        crawler = _make_crawler(cls=NovelCrawler, fetch_mode="auto", max_workers=4)
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volumes = [{"title": "Vol 1", "chapter_links": [
            "https://example.com/ch1", "https://example.com/ch2", "https://example.com/ch3"
        ]}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = [{"chapter_title": "T", "chapter_content": "", "chapter_img_folder": None}] * 2
            crawler.crawl_range(1, 2)
            mock_seq.assert_called_once()


# ---------------------------------------------------------------------------
# DoclnCrawler is always sequential
# ---------------------------------------------------------------------------


class TestDoclnAlwaysSequential(unittest.TestCase):
    """DoclnCrawler always uses the sequential fallback regardless of settings."""

    def test_docln_supports_parallel_is_false(self):
        self.assertFalse(DoclnCrawler._supports_parallel)

    def test_docln_is_parallel_safe_is_false(self):
        crawler = DoclnCrawler.__new__(DoclnCrawler)
        crawler.fetch_mode = "requests"
        crawler.keep_logged_in = False
        crawler.driver = None
        crawler._supports_parallel = False
        self.assertFalse(crawler._is_parallel_safe())


# ---------------------------------------------------------------------------
# XCrawler fallback
# ---------------------------------------------------------------------------


class TestXCrawlerFallback(unittest.TestCase):
    """XCrawler inherits the same fallback behaviour."""

    def test_xcrawler_auto_mode_falls_back(self):
        crawler = XCrawler.__new__(XCrawler)
        crawler.output_dir = tempfile.mkdtemp() + "/"
        crawler.sleep_time = 0
        crawler.max_workers = 4
        crawler.update_log = lambda msg, new=False: None
        crawler._pacer = MagicMock()
        crawler._worker_fetcher_factory = MagicMock()
        crawler.fetcher = MagicMock()
        crawler.driver = None
        crawler.keep_logged_in = False
        crawler.fetch_mode = "auto"
        crawler.start_chapter = 1
        crawler.end_chapter = 2
        crawler.site_name = "test"
        crawler.format_data = {}
        crawler.base_url = "https://example.com"
        crawler._supports_parallel = True
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        chapter_links = ["https://example.com/1/", "https://example.com/2/"]
        volumes = [{"title": "vol_0", "chapter_links": chapter_links}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": chapter_links}

        with patch.object(crawler, "_crawl_chapters_sequentially") as mock_seq:
            mock_seq.return_value = [{"chapter_title": "T", "chapter_content": "", "chapter_img_folder": None}] * 2
            crawler.crawl(start_chapter=1, end_chapter=2)
            mock_seq.assert_called_once()


if __name__ == "__main__":
    unittest.main()
