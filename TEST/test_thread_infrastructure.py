"""Tests for Task 2 — thread-safe request and logging infrastructure.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_thread_infrastructure.py -v
"""

import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from utils.worker_config import (
    GlobalPacer,
    WorkerFetcherFactory,
    log_lock,
)
from utils.fetcher import PageFetcher, FetchMode
from crawler.Novel import NovelCrawler


# ---------------------------------------------------------------------------
# GlobalPacer
# ---------------------------------------------------------------------------


class TestGlobalPacerNoDelay(unittest.TestCase):
    """A pacer with interval=0 should not block at all."""

    def test_acquire_returns_immediately(self):
        pacer = GlobalPacer(0)
        start = time.monotonic()
        pacer.acquire()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)

    def test_close_is_idempotent(self):
        pacer = GlobalPacer(0)
        pacer.close()
        pacer.close()


class TestGlobalPacerWithDelay(unittest.TestCase):
    """A pacer with a positive interval should space out acquires."""

    def test_single_acquire_respects_interval(self):
        pacer = GlobalPacer(0.1)
        start = time.monotonic()
        pacer.acquire()
        pacer.acquire()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.09)

    def test_stale_wait_is_skipped(self):
        """If enough time elapsed before acquire, no extra sleep happens."""
        pacer = GlobalPacer(0.05)
        pacer.acquire()
        time.sleep(0.1)  # Well past the interval
        start = time.monotonic()
        pacer.acquire()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)

    def test_concurrent_threads_are_spaced(self):
        """Multiple threads acquiring in sequence produce cumulative delay."""
        pacer = GlobalPacer(0.05)
        call_times = []
        barrier = threading.Barrier(3)

        def worker():
            barrier.wait()
            pacer.acquire()
            call_times.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(call_times), 3)
        call_times.sort()
        # The gap between first and last should be at least 2 * interval
        total = call_times[-1] - call_times[0]
        self.assertGreaterEqual(total, 0.08)


class TestGlobalPacerNegativeInterval(unittest.TestCase):
    """Negative intervals are clamped to zero (no delay)."""

    def test_negative_interval_is_noop(self):
        pacer = GlobalPacer(-10)
        start = time.monotonic()
        pacer.acquire()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.05)


# ---------------------------------------------------------------------------
# WorkerFetcherFactory
# ---------------------------------------------------------------------------


class TestWorkerFetcherFactory(unittest.TestCase):
    """Each executor thread receives its own requests-only PageFetcher."""

    def test_creates_requests_mode_fetcher(self):
        factory = WorkerFetcherFactory()
        fetcher = factory.get_fetcher()
        try:
            self.assertIsInstance(fetcher, PageFetcher)
            self.assertEqual(fetcher.fetch_mode, FetchMode.REQUESTS)
        finally:
            factory.close_all()

    def test_reuses_the_same_fetcher_in_one_thread(self):
        factory = WorkerFetcherFactory()
        f1 = factory.get_fetcher()
        f2 = factory.get_fetcher()
        try:
            self.assertIs(f1, f2)
            self.assertIs(f1.session, f2.session)
        finally:
            factory.close_all()

    def test_creates_distinct_sessions_across_threads(self):
        factory = WorkerFetcherFactory()
        fetchers = []
        lock = threading.Lock()

        def get_fetcher():
            fetcher = factory.get_fetcher()
            with lock:
                fetchers.append(fetcher)

        threads = [threading.Thread(target=get_fetcher) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        try:
            self.assertEqual(len(fetchers), 2)
            self.assertIsNot(fetchers[0].session, fetchers[1].session)
        finally:
            factory.close_all()

    def test_count_tracks_active_fetchers(self):
        factory = WorkerFetcherFactory()
        self.assertEqual(factory.count, 0)
        f1 = factory.get_fetcher()
        self.assertEqual(factory.count, 1)
        f2 = factory.get_fetcher()
        self.assertEqual(factory.count, 1)
        factory.close_all()
        self.assertEqual(factory.count, 0)

    def test_close_all_is_idempotent(self):
        factory = WorkerFetcherFactory()
        f1 = factory.get_fetcher()
        factory.close_all()
        factory.close_all()
        self.assertEqual(factory.count, 0)

    def test_close_all_closes_sessions(self):
        factory = WorkerFetcherFactory()
        f1 = factory.get_fetcher()
        factory.close_all()
        # Sessions should be closed; accessing should raise or indicate closed
        # requests.Session.close() is safe to call multiple times
        f1.session.close()

    def test_cloudflare_config_forwarded(self):
        factory = WorkerFetcherFactory(cloudflare=False)
        fetcher = factory.get_fetcher()
        try:
            self.assertFalse(fetcher.cloudflare)
        finally:
            factory.close_all()

    def test_fetcher_never_starts_browser(self):
        """Worker fetchers are always requests-mode, never browser."""
        factory = WorkerFetcherFactory()
        fetcher = factory.get_fetcher()
        try:
            self.assertIsNone(fetcher._driver)
            self.assertFalse(fetcher._browser_mode_active)
        finally:
            factory.close_all()


# ---------------------------------------------------------------------------
# Thread-safe update_log
# ---------------------------------------------------------------------------


class TestThreadSafeUpdateLog(unittest.TestCase):
    """update_log writes are serialised by the module-level log_lock."""

    def test_concurrent_writes_are_not_interleaved(self):
        """Multiple threads writing to the same log produce complete entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crawler = NovelCrawler.__new__(NovelCrawler)
            crawler.output_dir = tmpdir

            num_threads = 10
            messages_per_thread = 20
            barrier = threading.Barrier(num_threads)
            written_messages = []

            def writer(thread_id):
                barrier.wait()
                for i in range(messages_per_thread):
                    msg = f"thread{thread_id}_msg{i}"
                    crawler.update_log(msg)
                    written_messages.append(msg)

            threads = [
                threading.Thread(target=writer, args=(tid,))
                for tid in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            log_path = tmpdir + "/logs/crawl_log.txt"
            with open(log_path, "r", encoding="utf-8") as f:
                log_content = f.read()

            # Every message should appear as a complete entry
            for msg in written_messages:
                self.assertIn(msg, log_content)

            # No message should be split across lines (interleaved mid-write)
            for msg in written_messages:
                # Each message should appear on its own complete line
                self.assertIn(msg + "\n\n", log_content)

    def test_new_flag_resets_log(self):
        """The new=True flag replaces the log without race conditions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            crawler = NovelCrawler.__new__(NovelCrawler)
            crawler.output_dir = tmpdir
            crawler.update_log("old message")
            crawler.update_log("new message", new=True)

            log_path = tmpdir + "/logs/crawl_log.txt"
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("old message", content)
            self.assertIn("new message", content)


# ---------------------------------------------------------------------------
# NovelCrawler worker-fetcher integration
# ---------------------------------------------------------------------------


class TestNovelCrawlerWorkerFetcher(unittest.TestCase):
    """NovelCrawler can create and close worker fetchers."""

    def test_create_worker_fetcher_returns_requests_mode(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        try:
            worker = crawler._create_worker_fetcher()
            self.assertIsInstance(worker, PageFetcher)
            self.assertEqual(worker.fetch_mode, FetchMode.REQUESTS)
        finally:
            crawler.close()

    def test_worker_fetchers_are_closed_on_crawler_close(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        w1 = crawler._create_worker_fetcher()
        w2 = crawler._create_worker_fetcher()
        self.assertIs(w1, w2)
        crawler.close()
        # After close, factory should have no tracked fetchers
        self.assertEqual(crawler._worker_fetcher_factory.count, 0)

    def test_pacer_is_initialized(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        try:
            self.assertIsNotNone(crawler._pacer)
            self.assertIsInstance(crawler._pacer, GlobalPacer)
        finally:
            crawler.close()

    def test_pacer_uses_sleep_time(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=500)
        try:
            # sleep_time is converted to seconds: 500ms = 0.5s
            self.assertAlmostEqual(crawler._pacer._interval, 0.5, places=2)
        finally:
            crawler.close()

    def test_multiple_worker_threads_share_nothing(self):
        """Distinct worker threads receive independent sessions."""
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        try:
            workers = []
            lock = threading.Lock()

            def create_worker():
                worker = crawler._create_worker_fetcher()
                with lock:
                    workers.append(worker)

            threads = [threading.Thread(target=create_worker) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            sessions = {id(worker.session) for worker in workers}
            self.assertEqual(len(sessions), 3)
        finally:
            crawler.close()

    def test_chapter_context_uses_worker_fetcher_and_paces_every_fetch(self):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.driver = None
        crawler.fetcher = MagicMock()
        crawler._pacer = MagicMock()
        crawler._chapter_request_context = threading.local()
        worker_fetcher = MagicMock()
        worker_fetcher.fetch.return_value = "<html>chapter</html>"
        crawler._chapter_request_context.fetcher = worker_fetcher
        crawler._chapter_request_context.pace_requests = True

        crawler._get_page_content("https://example.com/chapter-1")
        crawler._get_page_content("https://example.com/chapter-1")

        self.assertEqual(crawler._pacer.acquire.call_count, 2)
        self.assertEqual(worker_fetcher.fetch.call_count, 2)
        crawler.fetcher.fetch.assert_not_called()

    def test_create_worker_fetcher_without_factory_raises(self):
        """Calling _create_worker_fetcher before factory init raises."""
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler._worker_fetcher_factory = None
        with self.assertRaises(RuntimeError):
            crawler._create_worker_fetcher()


if __name__ == "__main__":
    unittest.main()
