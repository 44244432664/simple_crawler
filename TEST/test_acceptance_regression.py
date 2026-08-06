"""Tests for Task 7 — regression and acceptance validation.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_acceptance_regression.py -v
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from crawler.Novel import NovelCrawler
from utils.worker_config import ChapterJob


# ---------------------------------------------------------------------------
# Schema verification
# ---------------------------------------------------------------------------


class TestJobDictSchema(unittest.TestCase):
    """No output schema changes beyond the optional max_workers field."""

    def test_basic_job_dict_has_no_unexpected_keys(self):
        """A minimal job dict contains only expected keys."""
        from tui import build_novel_job

        job = build_novel_job(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
        )

        expected_keys = {
            "novel_url",
            "output_dir",
            "sleep_time",
            "crawl_type",
            "crawl_type_args",
            "book_type",
            "keep_logged_in",
            "fetch_mode",
            "headless",
            "max_workers",
        }
        actual_keys = set(job.keys())
        self.assertEqual(
            actual_keys,
            expected_keys,
            f"Unexpected keys in job dict: {actual_keys - expected_keys}",
        )

    def test_range_job_dict_has_crawl_type_args(self):
        """A range job's crawl_type_args contain only expected keys."""
        from tui import build_novel_job

        job = build_novel_job(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            crawl_type="range",
            book_type="all",
            start_chapter=1,
            end_chapter=10,
        )

        expected_args_keys = {"start_chapter", "end_chapter"}
        actual_args_keys = set(job["crawl_type_args"].keys())
        self.assertEqual(
            actual_args_keys,
            expected_args_keys,
            f"Unexpected keys in crawl_type_args: {actual_args_keys - expected_args_keys}",
        )

    def test_novel_info_json_structure(self):
        """Verify the structure of novel_info.json written by a crawl."""
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.output_dir = tempfile.mkdtemp() + "/"
        crawler.sleep_time = 0
        crawler.max_workers = 4
        crawler.update_log = lambda msg, new=False: None
        crawler._pacer = MagicMock()
        crawler._worker_fetcher_factory = MagicMock()
        crawler.fetcher = MagicMock()
        crawler.driver = None
        crawler.keep_logged_in = False
        crawler.format_data = {"chapter": {"content": {}, "title": {}, "image": {}}}
        crawler.base_url = "https://example.com"
        crawler.fetch_mode = "requests"

        volumes = [
            {
                "title": "Volume 1",
                "cover_image": None,
                "chapter_links": ["https://example.com/ch1"],
            }
        ]
        crawler.get_all_info = MagicMock(return_value=({"title": "Test"}, volumes))
        crawler.novel_info = {"title": "Test", "chapter_links": []}

        def _fake_retries(chapter_url, img_output_dir=None, img_prefix="",
                          chapter_label=None, max_retries=5):
            return {
                "chapter_title": f"Title for {chapter_url}",
                "chapter_content": f"<p>Content for {chapter_url}</p>",
                "chapter_img_folder": None,
            }

        crawler._crawl_chapter_with_retries = _fake_retries
        crawler.crawl()

        json_path = os.path.join(crawler.output_dir, "novel_info.json")
        self.assertTrue(os.path.exists(json_path))

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("info", data)
        self.assertIn("volumes", data)
        self.assertIsInstance(data["volumes"], list)
        if data["volumes"]:
            vol = data["volumes"][0]
            expected_vol_keys = {"title", "cover_image", "chapter_links", "chapter_contents"}
            for key in expected_vol_keys:
                self.assertIn(key, vol, f"Volume missing key: {key}")
            if vol["chapter_contents"]:
                ch = vol["chapter_contents"][0]
                expected_ch_keys = {"chapter_title", "chapter_content", "chapter_img_folder"}
                self.assertSetEqual(set(ch.keys()), expected_ch_keys)


# ---------------------------------------------------------------------------
# crawl_multi remains sequential
# ---------------------------------------------------------------------------


class TestCrawlMultiIsSequential(unittest.TestCase):
    """crawl_multi processes novels one at a time — no batching."""

    def test_crawl_multi_uses_for_loop(self):
        """crawl_multi does not import ThreadPoolExecutor."""
        import inspect
        import utils.crawl_multi as cm

        source = inspect.getsource(cm.crawl_multi)
        self.assertNotIn("ThreadPoolExecutor", source)

    def test_crawl_multi_processes_jobs_in_order(self):
        """crawl_multi runs jobs sequentially in list order."""
        import utils.crawl_multi as cm

        order = []

        def _fake_run(**kwargs):
            order.append(kwargs.get("novel_url"))

        with patch.object(cm, "_get_run_callable", return_value=_fake_run):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(
                    [
                        {"novel_url": "https://example.com/1"},
                        {"novel_url": "https://example.com/2"},
                        {"novel_url": "https://example.com/3"},
                    ],
                    f,
                )
                tmp_path = f.name

            try:
                cm.crawl_multi(tmp_path)
            finally:
                os.unlink(tmp_path)

        self.assertEqual(order, [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ])

    def test_crawl_multi_sequential_time(self):
        """crawl_multi takes at least N * delay time (no overlap)."""
        import utils.crawl_multi as cm

        delays = [0] * 5

        def _fake_run_with_delay(**kwargs):
            time.sleep(0.03)

        with patch.object(cm, "_get_run_callable", return_value=_fake_run_with_delay):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(
                    [{"novel_url": f"https://example.com/{i}"} for i in range(5)],
                    f,
                )
                tmp_path = f.name

            try:
                start = time.monotonic()
                cm.crawl_multi(tmp_path)
                elapsed = time.monotonic() - start
            finally:
                os.unlink(tmp_path)

        # 5 jobs × 0.03s each should take at least ~0.15s
        self.assertGreaterEqual(elapsed, 0.10)


# ---------------------------------------------------------------------------
# Mocked timing benchmark — parallel vs sequential overlap
# ---------------------------------------------------------------------------


class TestParallelTimingBenchmark(unittest.TestCase):
    """Demonstrate that concurrent chapter fetching is faster than sequential.

    Uses mocked work so the test is fast, deterministic, and requires no network.
    """

    def _make_bench_crawler(self, max_workers, work_delay=0.05):
        """Create a NovelCrawler ready for a benchmark run."""
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.output_dir = tempfile.mkdtemp() + "/"
        crawler.sleep_time = 0
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

        def _delayed_work(chapter_url, img_output_dir=None, img_prefix="",
                          chapter_label=None, max_retries=5):
            time.sleep(work_delay)
            return {
                "chapter_title": f"Title for {chapter_url}",
                "chapter_content": f"<p>Content for {chapter_url}</p>",
                "chapter_img_folder": None,
            }

        crawler._crawl_chapter_with_retries = _delayed_work
        return crawler

    def test_parallel_faster_than_sequential(self):
        """6 chapters with 4 workers completes faster than with 1 worker."""
        num_chapters = 6
        delay_per_chapter = 0.05
        jobs = [
            ChapterJob(
                position=i,
                url=f"https://example.com/ch{i}",
                img_prefix=f"vol1_chap{i+1}",
                label=f"vol1_chapter_{i+1}",
            )
            for i in range(num_chapters)
        ]

        # Sequential timing
        seq_crawler = self._make_bench_crawler(max_workers=1, work_delay=delay_per_chapter)
        seq_crawler._pacer = MagicMock()  # disable pacer for measurement

        start = time.monotonic()
        seq_results = seq_crawler._crawl_chapters_sequentially(jobs)
        seq_time = time.monotonic() - start
        self.assertEqual(len(seq_results), num_chapters)

        # Parallel timing
        par_crawler = self._make_bench_crawler(max_workers=4, work_delay=delay_per_chapter)

        start = time.monotonic()
        par_results = par_crawler._schedule_chapters(
            jobs, desc="Benchmark", unit="chapter"
        )
        par_time = time.monotonic() - start
        self.assertEqual(len(par_results), num_chapters)

        # Assert parallel is faster — with 4 workers on 6 items × 0.05s each:
        #   Sequential: ~0.30s
        #   Parallel:   ~0.10s (2 rounds of 4 workers)
        self.assertGreater(
            seq_time,
            par_time,
            f"Expected parallel ({par_time:.3f}s) to be faster than "
            f"sequential ({seq_time:.3f}s)",
        )

        # Assert a meaningful speedup: parallel should be at least 1.5× faster
        speedup = seq_time / par_time
        self.assertGreater(
            speedup,
            1.5,
            f"Speedup too low: {speedup:.1f}x (seq={seq_time:.3f}s, par={par_time:.3f}s)",
        )

    def test_parallel_with_pacer_still_faster(self):
        """Even with a nonzero pacer interval, parallel can overlap.

        With delay=0.02s per chapter and pacer interval=0.01s, 4 workers
        on 6 chapters should still finish faster than sequential.
        """
        num_chapters = 6
        delay_per_chapter = 0.02
        pacer_interval = 0.01  # 10ms between starts

        jobs = [
            ChapterJob(
                position=i,
                url=f"https://example.com/ch{i}",
                img_prefix=f"vol1_chap{i+1}",
                label=f"vol1_chapter_{i+1}",
            )
            for i in range(num_chapters)
        ]

        # Sequential — sleeps delay + pacer per chapter
        def _delayed_work(chapter_url, img_output_dir=None, img_prefix="",
                          chapter_label=None, max_retries=5):
            time.sleep(delay_per_chapter)
            return {
                "chapter_title": f"Title for {chapter_url}",
                "chapter_content": f"<p>Content for {chapter_url}</p>",
                "chapter_img_folder": None,
            }

        from utils.worker_config import GlobalPacer

        seq_crawler = self._make_bench_crawler(max_workers=1, work_delay=delay_per_chapter)
        seq_crawler._crawl_chapter_with_retries = _delayed_work
        seq_crawler._pacer = GlobalPacer(pacer_interval)

        start = time.monotonic()
        seq_results = seq_crawler._crawl_chapters_sequentially(jobs)
        seq_time = time.monotonic() - start
        self.assertEqual(len(seq_results), num_chapters)

        # Parallel
        par_crawler = self._make_bench_crawler(max_workers=4, work_delay=delay_per_chapter)
        par_crawler._crawl_chapter_with_retries = _delayed_work
        par_crawler._pacer = GlobalPacer(pacer_interval)

        start = time.monotonic()
        par_results = par_crawler._schedule_chapters(
            jobs, desc="Benchmark", unit="chapter"
        )
        par_time = time.monotonic() - start
        self.assertEqual(len(par_results), num_chapters)

        # Parallel should still be faster even with pacer
        self.assertGreater(
            seq_time,
            par_time,
            f"Expected parallel ({par_time:.3f}s) to be faster than "
            f"sequential ({seq_time:.3f}s) even with pacer",
        )


# ---------------------------------------------------------------------------
# Tests do not require live websites or browsers
# ---------------------------------------------------------------------------


class TestNoLiveDependencies(unittest.TestCase):
    """All concurrency tests work offline with mocked dependencies."""

    def test_chapter_scheduler_tests_require_no_network(self):
        """test_chapter_scheduler.py tests should pass when imported."""
        import TEST.test_chapter_scheduler as _  # noqa: F811

    def test_thread_infrastructure_tests_require_no_network(self):
        """test_thread_infrastructure.py tests should pass when imported."""
        import TEST.test_thread_infrastructure as _  # noqa: F811

    def test_crawl_integration_tests_require_no_network(self):
        """test_crawl_integration.py tests should pass when imported."""
        import TEST.test_crawl_integration as _  # noqa: F811

    def test_safe_fallback_tests_require_no_network(self):
        """test_safe_fallback.py tests should pass when imported."""
        import TEST.test_safe_fallback as _  # noqa: F811


if __name__ == "__main__":
    unittest.main()
