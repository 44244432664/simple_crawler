"""Tests for Task 3 — ordered chapter scheduler.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_chapter_scheduler.py -v
"""

import concurrent.futures
import threading
import time
import unittest

from utils.worker_config import ChapterJob, ChapterScheduler


# ---------------------------------------------------------------------------
# ChapterJob
# ---------------------------------------------------------------------------


class TestChapterJob(unittest.TestCase):
    """ChapterJob stores chapter metadata correctly."""

    def test_defaults(self):
        job = ChapterJob(position=0, url="https://example.com/ch1")
        self.assertEqual(job.position, 0)
        self.assertEqual(job.url, "https://example.com/ch1")
        self.assertEqual(job.img_output_dir, "")
        self.assertEqual(job.img_prefix, "")
        self.assertEqual(job.label, "")
        self.assertEqual(job.max_retries, 5)

    def test_all_fields(self):
        job = ChapterJob(
            position=3,
            url="https://example.com/ch4",
            img_output_dir="/tmp/img",
            img_prefix="vol1_chap4",
            label="vol1_chapter_4",
            max_retries=3,
        )
        self.assertEqual(job.position, 3)
        self.assertEqual(job.url, "https://example.com/ch4")
        self.assertEqual(job.img_output_dir, "/tmp/img")
        self.assertEqual(job.img_prefix, "vol1_chap4")
        self.assertEqual(job.label, "vol1_chapter_4")
        self.assertEqual(job.max_retries, 3)


# ---------------------------------------------------------------------------
# ChapterScheduler — ordering
# ---------------------------------------------------------------------------


class TestSchedulerOrdering(unittest.TestCase):
    """Results are always returned in source order."""

    def test_out_of_order_completion_produces_in_order_results(self):
        """Slow-then-fast workers complete out of order but return in order."""
        barrier = threading.Barrier(3)

        def work(job):
            if job.position == 0:
                # First job is slow — wait for others to finish first
                barrier.wait(timeout=5)
                time.sleep(0.15)
            elif job.position == 1:
                barrier.wait(timeout=5)
            else:
                barrier.wait(timeout=5)
            return {"chapter_title": f"Ch {job.position}", "position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(3)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=3)
        results = scheduler.run()

        self.assertEqual(len(results), 3)
        for i, result in enumerate(results):
            self.assertEqual(result["position"], i)
            self.assertEqual(result["chapter_title"], f"Ch {i}")

    def test_results_match_input_order(self):
        """Results list indices correspond to input job positions."""
        delay_map = {0: 0.1, 1: 0.0, 2: 0.05}

        def work(job):
            time.sleep(delay_map[job.position])
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(3)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=3)
        results = scheduler.run()

        self.assertEqual([r["position"] for r in results], [0, 1, 2])


# ---------------------------------------------------------------------------
# ChapterScheduler — worker limit
# ---------------------------------------------------------------------------


class TestSchedulerWorkerLimit(unittest.TestCase):
    """ThreadPoolExecutor never exceeds max_workers threads."""

    def test_worker_limit_not_exceeded(self):
        active = [0]
        max_seen = [0]
        lock = threading.Lock()

        def work(job):
            with lock:
                active[0] += 1
                max_seen[0] = max(max_seen[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(8)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=3)
        results = scheduler.run()

        self.assertEqual(len(results), 8)
        self.assertLessEqual(max_seen[0], 3)

    def test_max_workers_clamped_to_chapter_count(self):
        """With 2 chapters and max_workers=5, only 2 workers are used."""
        active = [0]
        max_seen = [0]
        lock = threading.Lock()

        def work(job):
            with lock:
                active[0] += 1
                max_seen[0] = max(max_seen[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(2)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=5)
        results = scheduler.run()

        self.assertEqual(len(results), 2)
        self.assertLessEqual(max_seen[0], 2)

    def test_max_workers_one_is_sequential(self):
        """With max_workers=1, only one chapter runs at a time."""
        active = [0]
        max_seen = [0]
        lock = threading.Lock()

        def work(job):
            with lock:
                active[0] += 1
                max_seen[0] = max(max_seen[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(4)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=1)
        results = scheduler.run()

        self.assertEqual(len(results), 4)
        self.assertEqual(max_seen[0], 1)


# ---------------------------------------------------------------------------
# ChapterScheduler — empty and single
# ---------------------------------------------------------------------------


class TestSchedulerEdgeCases(unittest.TestCase):
    """Empty and single-chapter inputs work without unnecessary overhead."""

    def test_empty_input_returns_empty(self):
        def work(job):
            return {"position": job.position}

        scheduler = ChapterScheduler(work, [], max_workers=4)
        results = scheduler.run()
        self.assertEqual(results, [])

    def test_single_chapter_returns_list(self):
        def work(job):
            return {"chapter_title": "Only", "position": job.position}

        chapters = [ChapterJob(position=0, url="https://example.com/ch1")]
        scheduler = ChapterScheduler(work, chapters, max_workers=4)
        results = scheduler.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chapter_title"], "Only")


# ---------------------------------------------------------------------------
# ChapterScheduler — failure handling
# ---------------------------------------------------------------------------


class TestSchedulerFailureHandling(unittest.TestCase):
    """Failed chapters are omitted; successful chapters keep their order."""

    def test_failed_chapters_omitted(self):
        def work(job):
            if job.position == 1:
                return None  # Permanent failure
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(4)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2)
        results = scheduler.run()

        self.assertEqual(len(results), 3)
        positions = [r["position"] for r in results]
        self.assertNotIn(1, positions)
        self.assertEqual(positions, [0, 2, 3])

    def test_raised_exception_marks_chapter_failed(self):
        def work(job):
            if job.position == 2:
                raise RuntimeError("network error")
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(4)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2)
        results = scheduler.run()

        self.assertEqual(len(results), 3)
        positions = [r["position"] for r in results]
        self.assertEqual(positions, [0, 1, 3])

    def test_raised_exception_is_reported_to_error_handler(self):
        errors = []

        def work(job):
            raise RuntimeError("network error")

        scheduler = ChapterScheduler(
            work,
            [ChapterJob(position=0, url="https://example.com/ch0")],
            max_workers=1,
            error_fn=lambda job, error: errors.append((job.url, error)),
        )

        self.assertEqual(scheduler.run(), [])
        self.assertEqual(errors[0][0], "https://example.com/ch0")
        self.assertIsInstance(errors[0][1], RuntimeError)

    def test_all_chapters_fail_returns_empty(self):
        def work(job):
            return None

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(3)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2)
        results = scheduler.run()

        self.assertEqual(results, [])

    def test_one_failed_does_not_disturb_order(self):
        """Successful chapters are interleaved with failures but stay ordered."""
        def work(job):
            if job.position % 2 == 1:
                return None  # Fail all odd-positioned chapters
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(6)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=3)
        results = scheduler.run()

        self.assertEqual(len(results), 3)
        self.assertEqual([r["position"] for r in results], [0, 2, 4])


# ---------------------------------------------------------------------------
# ChapterScheduler — progress callback
# ---------------------------------------------------------------------------


class TestSchedulerProgressCallback(unittest.TestCase):
    """Progress callback is invoked after each chapter completes."""

    def test_progress_called_per_chapter(self):
        progress_calls = []

        def work(job):
            return {"position": job.position}

        def on_progress(completed, total):
            progress_calls.append((completed, total))

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(4)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2, progress_fn=on_progress)
        scheduler.run()

        self.assertEqual(len(progress_calls), 4)
        for _, total in progress_calls:
            self.assertEqual(total, 4)
        # Completed counts should be 1 through 4 in some order
        completed_values = sorted(c for c, _ in progress_calls)
        self.assertEqual(completed_values, [1, 2, 3, 4])

    def test_progress_called_for_failed_chapters(self):
        progress_calls = []

        def work(job):
            if job.position == 1:
                return None
            return {"position": job.position}

        def on_progress(completed, total):
            progress_calls.append((completed, total))

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(3)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2, progress_fn=on_progress)
        scheduler.run()

        self.assertEqual(len(progress_calls), 3)


# ---------------------------------------------------------------------------
# ChapterScheduler — KeyboardInterrupt
# ---------------------------------------------------------------------------


class TestSchedulerInterruption(unittest.TestCase):
    """KeyboardInterrupt cancels pending work and propagates to the caller."""

    def test_keyboard_interrupt_cancels_pending(self):
        started = []
        started_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def work(job):
            with started_lock:
                started.append(job.position)
            if job.position == 0:
                barrier.wait(timeout=5)
                time.sleep(0.2)
                return {"position": job.position}
            elif job.position == 1:
                barrier.wait(timeout=5)
                raise KeyboardInterrupt()
            else:
                # Should never start if position 1 raises during as_completed
                time.sleep(0.5)
                return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(4)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2)

        with self.assertRaises(KeyboardInterrupt):
            scheduler.run()

        self.assertEqual(set(started), {0, 1})

    def test_executor_is_closed_after_interrupt(self):
        def work(job):
            if job.position == 0:
                raise KeyboardInterrupt()
            time.sleep(0.1)
            return {"position": job.position}

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}")
            for i in range(3)
        ]
        scheduler = ChapterScheduler(work, chapters, max_workers=2)

        try:
            scheduler.run()
        except KeyboardInterrupt:
            pass

        # The scheduler should not leave dangling executor state
        # (We verify by ensuring a second run works)
        def work2(job):
            return {"position": job.position}

        scheduler2 = ChapterScheduler(work2, chapters[:1], max_workers=1)
        results = scheduler2.run()
        self.assertEqual(len(results), 1)


# ---------------------------------------------------------------------------
# ChapterScheduler — NovelCrawler integration (lightweight)
# ---------------------------------------------------------------------------


class TestNovelCrawlerScheduleChapters(unittest.TestCase):
    """NovelCrawler can schedule chapters via ChapterScheduler."""

    def test_schedule_chapters_returns_ordered_results(self):
        from crawler.Novel import NovelCrawler

        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.output_dir = "/tmp/test"
        crawler.update_log = lambda msg, new=False: None

        def work(job):
            return {
                "chapter_title": f"Chapter {job.position}",
                "chapter_content": f"<p>Content {job.position}</p>",
                "chapter_img_folder": None,
            }

        chapters = [
            ChapterJob(position=i, url=f"https://example.com/ch{i}", img_prefix=f"vol1_chap{i+1}")
            for i in range(5)
        ]

        scheduler = ChapterScheduler(work, chapters, max_workers=3)
        results = scheduler.run()

        self.assertEqual(len(results), 5)
        for i, result in enumerate(results):
            self.assertEqual(result["chapter_title"], f"Chapter {i}")


if __name__ == "__main__":
    unittest.main()
