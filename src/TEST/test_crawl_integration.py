"""Tests for Task 4 — crawl-path scheduling integration.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_crawl_integration.py -v
"""

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from utils.worker_config import ChapterJob, ChapterScheduler
from crawler.Novel import NovelCrawler
from crawler.X import XCrawler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crawler(cls=NovelCrawler, max_workers=4, sleep_time=0):
    """Create a minimal crawler instance without hitting the network."""
    crawler = cls.__new__(cls)
    crawler.output_dir = tempfile.mkdtemp() + "/"
    crawler.sleep_time = sleep_time
    crawler.max_workers = max_workers
    crawler.update_log = lambda msg, new=False: None
    crawler._pacer = MagicMock()
    crawler._worker_fetcher_factory = MagicMock()
    crawler.fetcher = MagicMock()
    crawler.driver = None
    crawler.keep_logged_in = False
    crawler.format_data = {"chapter": {"content": {}, "title": {}, "image": {}}}
    crawler.base_url = "https://example.com"
    crawler.fetch_mode = "requests"
    return crawler


def _fake_work_factory(delay=0.01):
    """Return a mock _crawl_chapter_with_retries that succeeds instantly."""

    def _fake_retries(chapter_url, img_output_dir=None, img_prefix="",
                      chapter_label=None, max_retries=5):
        time.sleep(delay)
        return {
            "chapter_title": f"Title for {chapter_url}",
            "chapter_content": f"<p>Content for {chapter_url}</p>",
            "chapter_img_folder": None,
        }

    return _fake_retries


def _failing_work_factory(fail_positions=None):
    """Return a mock _crawl_chapter_with_retries that fails for some positions."""

    def _failing_retries(chapter_url, img_output_dir=None, img_prefix="",
                         chapter_label=None, max_retries=5):
        # Extract position from img_prefix like "vol1_chap3" -> 3
        if fail_positions and chapter_label:
            for pos in fail_positions:
                if f"chapter_{pos}" in chapter_label or f"chap{pos}" in chapter_label:
                    return None
        return {
            "chapter_title": f"Title for {chapter_url}",
            "chapter_content": f"<p>Content for {chapter_url}</p>",
            "chapter_img_folder": None,
        }

    return _failing_retries


# ---------------------------------------------------------------------------
# _schedule_chapters helper
# ---------------------------------------------------------------------------


class TestScheduleChaptersHelper(unittest.TestCase):
    """NovelCrawler._schedule_chapters delegates to ChapterScheduler."""

    def test_returns_ordered_results(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        jobs = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}",
                       img_prefix=f"vol1_chap{i+1}", label=f"vol1_chapter_{i+1}")
            for i in range(4)
        ]
        results = crawler._schedule_chapters(jobs)

        self.assertEqual(len(results), 4)
        for i, r in enumerate(results):
            self.assertIn(f"ch{i}", r["chapter_title"])

    def test_empty_jobs_returns_empty(self):
        crawler = _make_crawler()
        results = crawler._schedule_chapters([])
        self.assertEqual(results, [])

    def test_worker_factory_is_used(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        jobs = [
            ChapterJob(position=0, url="https://example.com/ch0",
                       img_prefix="vol1_chap1", label="vol1_chapter_1"),
        ]
        crawler._schedule_chapters(jobs)
        crawler._worker_fetcher_factory.get_fetcher.assert_called()

    def test_respects_max_workers(self):
        crawler = _make_crawler(max_workers=2)
        active = [0]
        max_seen = [0]
        lock = threading.Lock()

        def _tracking_retries(chapter_url, img_output_dir=None, img_prefix="",
                              chapter_label=None, max_retries=5):
            with lock:
                active[0] += 1
                max_seen[0] = max(max_seen[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"chapter_title": f"Ch {chapter_url}", "chapter_content": "", "chapter_img_folder": None}

        crawler._crawl_chapter_with_retries = _tracking_retries

        jobs = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}",
                       img_prefix=f"vol1_chap{i+1}", label=f"vol1_chapter_{i+1}")
            for i in range(6)
        ]
        results = crawler._schedule_chapters(jobs)

        self.assertEqual(len(results), 6)
        self.assertLessEqual(max_seen[0], 2)


# ---------------------------------------------------------------------------
# NovelCrawler.crawl() integration
# ---------------------------------------------------------------------------


class TestNovelCrawlerCrawl(unittest.TestCase):
    """crawl() processes volumes sequentially, chapters concurrently within each."""

    def test_crawl_populates_chapter_contents_in_order(self):
        """chapter_contents matches chapter_links order."""
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        # Stub get_all_info to return controlled data
        volumes = [
            {
                "title": "Volume 1",
                "chapter_links": [
                    "https://example.com/ch1",
                    "https://example.com/ch2",
                    "https://example.com/ch3",
                ],
            }
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": volumes[0]["chapter_links"]}

        crawler.crawl()

        vol_contents = volumes[0]["chapter_contents"]
        self.assertEqual(len(vol_contents), 3)
        for i, chapter in enumerate(vol_contents):
            self.assertIn(f"ch{i+1}", chapter["chapter_title"])

    def test_crawl_preserves_volume_order(self):
        """Volumes are processed one at a time; all chapters within are concurrent."""
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volume_order = []
        lock = threading.Lock()

        def _tracking_retries(chapter_url, img_output_dir=None, img_prefix="",
                              chapter_label=None, max_retries=5):
            time.sleep(0.01)
            with lock:
                volume_order.append(chapter_url)
            return {
                "chapter_title": f"Title for {chapter_url}",
                "chapter_content": f"<p>Content</p>",
                "chapter_img_folder": None,
            }

        crawler._crawl_chapter_with_retries = _tracking_retries

        volumes = [
            {"title": "Vol 1", "chapter_links": ["https://example.com/v1/ch1", "https://example.com/v1/ch2"]},
            {"title": "Vol 2", "chapter_links": ["https://example.com/v2/ch1"]},
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl()

        # Vol 1 chapters should all appear before Vol 2
        v1_end = volume_order.index("https://example.com/v1/ch2")
        v2_start = volume_order.index("https://example.com/v2/ch1")
        self.assertLess(v1_end, v2_start)

    def test_crawl_empty_volume(self):
        """An empty volume produces an empty chapter_contents list."""
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volumes = [{"title": "Empty Vol", "chapter_links": []}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl()

        self.assertEqual(volumes[0]["chapter_contents"], [])

    def test_crawl_image_prefixes_match_original(self):
        """Image prefixes use vol{idx+1}_chap{jdx+1} format."""
        captured_prefixes = []

        def _capturing_retries(chapter_url, img_output_dir=None, img_prefix="",
                               chapter_label=None, max_retries=5):
            captured_prefixes.append(img_prefix)
            return {"chapter_title": "T", "chapter_content": "<p>C</p>", "chapter_img_folder": None}

        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _capturing_retries

        volumes = [
            {"title": "Vol 1", "chapter_links": ["https://example.com/ch1", "https://example.com/ch2"]},
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl()

        self.assertIn("vol1_chap1", captured_prefixes)
        self.assertIn("vol1_chap2", captured_prefixes)

    def test_crawl_writes_novel_info_json(self):
        """crawling writes the final JSON output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crawler = _make_crawler()
            crawler.output_dir = tmpdir + "/"
            crawler._crawl_chapter_with_retries = _fake_work_factory()

            volumes = [
                {"title": "Vol 1", "chapter_links": ["https://example.com/ch1"]},
            ]
            crawler.get_all_info = MagicMock(return_value=({"title": "TestNovel"}, volumes))
            crawler.novel_info = {"title": "TestNovel", "chapter_links": []}

            crawler.crawl()

            json_path = os.path.join(tmpdir, "novel_info.json")
            self.assertTrue(os.path.exists(json_path))

    def test_interruption_does_not_write_final_crawl_output(self):
        """An interrupted scheduler must not produce a misleading final JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crawler = _make_crawler()
            crawler.output_dir = tmpdir + "/"
            crawler.get_all_info = MagicMock(return_value=(
                {"title": "TestNovel"},
                [{"title": "Vol 1", "chapter_links": ["https://example.com/ch1"]}],
            ))
            crawler._schedule_chapters = MagicMock(side_effect=KeyboardInterrupt())

            with self.assertRaises(KeyboardInterrupt):
                crawler.crawl()

            self.assertFalse(os.path.exists(os.path.join(tmpdir, "novel_info.json")))


# ---------------------------------------------------------------------------
# NovelCrawler.crawl_range() integration
# ---------------------------------------------------------------------------


class TestNovelCrawlerCrawlRange(unittest.TestCase):
    """crawl_range() schedules selected chapters concurrently."""

    def test_crawl_range_returns_ordered_results(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _fake_work_factory()

        volumes = [
            {
                "title": "Vol 1",
                "chapter_links": [
                    "https://example.com/ch1",
                    "https://example.com/ch2",
                    "https://example.com/ch3",
                    "https://example.com/ch4",
                ],
            }
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl_range(2, 3)

        # Should produce a single volume with 2 chapters in order
        json_path = os.path.join(crawler.output_dir, "novel_info.json")
        import json
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        vol = data["volumes"][0]
        self.assertEqual(len(vol["chapter_contents"]), 2)
        # Chapters should be in source order (ch2, ch3)
        self.assertIn("ch2", vol["chapter_contents"][0]["chapter_title"])
        self.assertIn("ch3", vol["chapter_contents"][1]["chapter_title"])

    def test_crawl_range_image_prefixes(self):
        """Image prefixes use the absolute chapter numbering."""
        captured_prefixes = []

        def _capturing_retries(chapter_url, img_output_dir=None, img_prefix="",
                               chapter_label=None, max_retries=5):
            captured_prefixes.append(img_prefix)
            return {"chapter_title": "T", "chapter_content": "<p>C</p>", "chapter_img_folder": None}

        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _capturing_retries

        volumes = [
            {
                "title": "Vol 1",
                "chapter_links": [
                    "https://example.com/ch1",
                    "https://example.com/ch2",
                    "https://example.com/ch3",
                ],
            }
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl_range(1, 3)

        self.assertIn("vol1_chap1", captured_prefixes)
        self.assertIn("vol1_chap2", captured_prefixes)
        self.assertIn("vol1_chap3", captured_prefixes)


# ---------------------------------------------------------------------------
# XCrawler.crawl() integration
# ---------------------------------------------------------------------------


class TestXCrawlerCrawl(unittest.TestCase):
    """XCrawler.crawl() schedules chapters concurrently via the shared scheduler."""

    def test_xcrawler_crawl_returns_ordered_results(self):
        """XCrawler produces chapter_contents in source order."""
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
        crawler.format_data = {}
        crawler.base_url = "https://example.com"
        crawler.fetch_mode = "requests"
        crawler.start_chapter = 1
        crawler.end_chapter = 4
        crawler.site_name = "test"

        crawler._crawl_chapter_with_retries = _fake_work_factory()

        chapter_links = [
            "https://example.com/1/",
            "https://example.com/2/",
            "https://example.com/3/",
            "https://example.com/4/",
        ]
        volumes = [{"title": "vol_0", "chapter_links": chapter_links}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": chapter_links}

        crawler.crawl(start_chapter=1, end_chapter=4)

        vol = volumes[0]
        self.assertEqual(len(vol["chapter_contents"]), 4)
        for i, chapter in enumerate(vol["chapter_contents"]):
            expected_url = f"https://example.com/{i+1}/"
            self.assertIn(expected_url, chapter["chapter_title"])

    def test_xcrawler_crawl_image_prefixes(self):
        """XCrawler uses chap{idx} prefix starting from start_chapter."""
        captured_prefixes = []

        def _capturing_retries(chapter_url, img_output_dir=None, img_prefix="",
                               chapter_label=None, max_retries=5):
            captured_prefixes.append(img_prefix)
            return {"chapter_title": "T", "chapter_content": "<p>C</p>", "chapter_img_folder": None}

        crawler = XCrawler.__new__(XCrawler)
        crawler.output_dir = tempfile.mkdtemp() + "/"
        crawler.sleep_time = 0
        crawler.max_workers = 2
        crawler.update_log = lambda msg, new=False: None
        crawler._pacer = MagicMock()
        crawler._worker_fetcher_factory = MagicMock()
        crawler.fetcher = MagicMock()
        crawler.driver = None
        crawler.keep_logged_in = False
        crawler.format_data = {}
        crawler.base_url = "https://example.com"
        crawler.fetch_mode = "requests"
        crawler.start_chapter = 5
        crawler.end_chapter = 7
        crawler.site_name = "test"

        crawler._crawl_chapter_with_retries = _capturing_retries

        chapter_links = [
            "https://example.com/5/",
            "https://example.com/6/",
            "https://example.com/7/",
        ]
        volumes = [{"title": "vol_0", "chapter_links": chapter_links}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": chapter_links}

        crawler.crawl(start_chapter=5, end_chapter=7)

        self.assertIn("chap5", captured_prefixes)
        self.assertIn("chap6", captured_prefixes)
        self.assertIn("chap7", captured_prefixes)

    def test_xcrawler_crawl_empty(self):
        """XCrawler with no chapter links produces empty chapter_contents."""
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
        crawler.format_data = {}
        crawler.base_url = "https://example.com"
        crawler.fetch_mode = "requests"
        crawler.start_chapter = 1
        crawler.end_chapter = 0
        crawler.site_name = "test"

        volumes = [{"title": "vol_0", "chapter_links": []}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl(start_chapter=1, end_chapter=0)

        self.assertEqual(volumes[0]["chapter_contents"], [])


# ---------------------------------------------------------------------------
# Failure handling during crawl
# ---------------------------------------------------------------------------


class TestCrawlFailureHandling(unittest.TestCase):
    """Failed chapters are omitted; successful chapters keep their order."""

    def test_crawl_omits_failed_chapters(self):
        crawler = _make_crawler()
        crawler._crawl_chapter_with_retries = _failing_work_factory(fail_positions=[2])

        volumes = [
            {
                "title": "Vol 1",
                "chapter_links": [
                    "https://example.com/ch1",
                    "https://example.com/ch2",
                    "https://example.com/ch3",
                ],
            }
        ]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        crawler.crawl()

        vol_contents = volumes[0]["chapter_contents"]
        # Only ch1 and ch3 should succeed; ch2 is omitted
        self.assertEqual(len(vol_contents), 2)
        self.assertIn("ch1", vol_contents[0]["chapter_title"])
        self.assertIn("ch3", vol_contents[1]["chapter_title"])


# ---------------------------------------------------------------------------
# Single-chapter crawl remains sequential
# ---------------------------------------------------------------------------


class TestSingleChapterCrawl(unittest.TestCase):
    """crawl_chapter() does not use the scheduler."""

    def test_crawl_chapter_calls_directly(self):
        crawler = _make_crawler()
        call_log = []

        def _tracking_crawl(chapter_url, img_output_dir=None, img_prefix=""):
            call_log.append(chapter_url)
            return {"chapter_title": "Single", "chapter_content": "<p>Text</p>", "chapter_img_folder": None}

        # Stub get_all_info
        volumes = [{"title": "Single", "chapter_links": ["https://example.com/ch1"]}]
        crawler.get_all_info = MagicMock(return_value=({}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        with patch.object(crawler, 'crawl_chapter_', _tracking_crawl):
            crawler.crawl_chapter("https://example.com/ch1")

        self.assertEqual(len(call_log), 1)
        self.assertEqual(call_log[0], "https://example.com/ch1")


if __name__ == "__main__":
    unittest.main()
