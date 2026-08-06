"""Fast, offline checks for the interactive terminal UI's job mapping."""

import json
import os
import unittest

from tui import SimpleCrawlerTUI, build_novel_job, parse_volume_urls, source_for_url
from utils.worker_config import DEFAULT_MAX_WORKERS


class TestTUIHelpers(unittest.TestCase):
    def test_builds_a_range_job_for_existing_crawler_api(self):
        job = build_novel_job(
            url="truyenfull.live/a-story",
            output_dir="",
            sleep_time=500,
            crawl_type="range",
            book_type="all",
            start_chapter=3,
            end_chapter=8,
            fetch_mode="auto",
        )

        self.assertEqual(job["novel_url"], "https://truyenfull.live/a-story")
        self.assertEqual(job["crawl_type_args"], {"start_chapter": 3, "end_chapter": 8})
        self.assertEqual(job["fetch_mode"], "auto")
        self.assertEqual(job["max_workers"], DEFAULT_MAX_WORKERS)

    def test_build_novel_job_default_max_workers(self):
        """max_workers defaults to 4 when omitted."""
        job = build_novel_job(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
        )
        self.assertEqual(job["max_workers"], DEFAULT_MAX_WORKERS)

    def test_build_novel_job_accepts_explicit_max_workers(self):
        """Explicit max_workers value appears in the job dict."""
        job = build_novel_job(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
            max_workers=2,
        )
        self.assertEqual(job["max_workers"], 2)

    def test_build_novel_job_none_max_workers_defaults(self):
        """None max_workers produces the default."""
        job = build_novel_job(
            url="https://example.com/novel",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
            max_workers=None,
        )
        self.assertEqual(job["max_workers"], DEFAULT_MAX_WORKERS)

    def test_volume_input_is_not_evaluated_as_python(self):
        self.assertEqual(
            parse_volume_urls("https://one.example, https://two.example"),
            ["https://one.example", "https://two.example"],
        )
        self.assertIsNone(parse_volume_urls("  ,  "))

    def test_source_lookup_normalizes_scheme_and_www(self):
        sources = [{"site": "truyenfull.live", "crawler_class": "XCrawler"}]
        self.assertEqual(
            source_for_url("www.truyenfull.live/story", sources),
            sources[0],
        )

    def test_worker_prompt_enforces_upper_limit(self):
        """Entering a value above MAX_WORKERS is re-prompted."""
        answers = iter(["6", "5"])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        self.assertEqual(
            ui.ask_int("Chapter workers", default=None, minimum=1, maximum=5),
            5,
        )

    def test_worker_prompt_rejects_below_minimum(self):
        """Entering a value below 1 is re-prompted."""
        answers = iter(["0", "-1", "1"])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        self.assertEqual(
            ui.ask_int("Chapter workers", default=None, minimum=1, maximum=5),
            1,
        )

    def test_worker_prompt_accepts_default(self):
        """An empty input (default) produces the default value."""
        answers = iter([""])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        self.assertEqual(
            ui.ask_int("Chapter workers", default=DEFAULT_MAX_WORKERS, minimum=1, maximum=5),
            DEFAULT_MAX_WORKERS,
        )


class TestTemplateJSON(unittest.TestCase):
    """The example to_crawl.json template is valid and includes max_workers."""

    def setUp(self):
        template_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "template", "to_crawl.json"
        )
        with open(template_path, "r", encoding="utf-8") as f:
            self.jobs = json.load(f)

    def test_template_is_valid_json(self):
        self.assertIsInstance(self.jobs, list)
        self.assertGreater(len(self.jobs), 0)

    def test_every_job_has_max_workers(self):
        for i, job in enumerate(self.jobs):
            with self.subTest(job=i):
                self.assertIn("max_workers", job)
                self.assertIsInstance(job["max_workers"], int)
                self.assertGreaterEqual(job["max_workers"], 1)

    def test_range_job_has_max_workers(self):
        range_jobs = [j for j in self.jobs if j.get("crawl_type") == "range"]
        if range_jobs:
            self.assertIn("max_workers", range_jobs[0])

    def test_single_job_has_max_workers(self):
        single_jobs = [j for j in self.jobs if j.get("crawl_type") == "single"]
        if single_jobs:
            self.assertIn("max_workers", single_jobs[0])


if __name__ == "__main__":
    unittest.main()
