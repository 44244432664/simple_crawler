"""Tests for Task 1 — worker configuration propagation.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_worker_config.py -v
"""

import unittest
from unittest.mock import MagicMock, patch

from crawler.Novel import (
    DEFAULT_MAX_WORKERS,
    NovelCrawler,
    parse_custom_volume_list,
    validate_max_workers,
)
from crawler.X import XCrawler
from tui import build_novel_job


class TestValidateMaxWorkers(unittest.TestCase):
    """validate_max_workers rejects invalid inputs and passes valid ones."""

    def test_positive_integers_accepted(self):
        for value in (1, 2, 4, 5):
            self.assertEqual(validate_max_workers(value), value)

    def test_zero_raises(self):
        with self.assertRaises(ValueError):
            validate_max_workers(0)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            validate_max_workers(-1)

    def test_values_above_limit_raise(self):
        with self.assertRaises(ValueError):
            validate_max_workers(6)

    def test_bool_raises(self):
        with self.assertRaises(ValueError):
            validate_max_workers(True)
        with self.assertRaises(ValueError):
            validate_max_workers(False)

    def test_string_raises(self):
        with self.assertRaises(ValueError):
            validate_max_workers("4")

    def test_float_raises(self):
        with self.assertRaises(ValueError):
            validate_max_workers(3.5)

    def test_none_is_not_accepted_by_validator(self):
        with self.assertRaises((ValueError, TypeError)):
            validate_max_workers(None)


class TestNovelCrawlerMaxWorkers(unittest.TestCase):
    """NovelCrawler stores a validated max_workers."""

    def test_default_is_four(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)
        self.assertEqual(crawler.max_workers, 4)
        crawler.close()

    def test_explicit_value(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000, max_workers=5)
        self.assertEqual(crawler.max_workers, 5)
        crawler.close()

    def test_value_of_one(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000, max_workers=1)
        self.assertEqual(crawler.max_workers, 1)
        crawler.close()

    def test_none_defaults_to_four(self):
        crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000, max_workers=None)
        self.assertEqual(crawler.max_workers, DEFAULT_MAX_WORKERS)
        crawler.close()

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            NovelCrawler(url="", output_dir=None, sleep_time=1000, max_workers=0)


class TestXCrawlerMaxWorkers(unittest.TestCase):
    """XCrawler stores a validated max_workers."""

    def test_default_is_four(self):
        crawler = XCrawler(url="", output_dir=None, sleep_time=1000)
        self.assertEqual(crawler.max_workers, 4)
        crawler.close()

    def test_explicit_value(self):
        crawler = XCrawler(url="", output_dir=None, sleep_time=1000, max_workers=2)
        self.assertEqual(crawler.max_workers, 2)
        crawler.close()

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            XCrawler(url="", output_dir=None, sleep_time=1000, max_workers=-3)


class TestRunForwardsMaxWorkers(unittest.TestCase):
    """run() extracts max_workers from kwargs and passes it to the crawler."""

    def test_forwards_to_novel_crawler(self):
        import pandas as pd_real

        alias_df = pd_real.DataFrame([
            {"site": "example.com", "name": "testsite", "crawler_class": "NovelRequest"},
        ])

        with patch("crawler.Novel.pd.read_csv", return_value=alias_df), \
             patch("crawler.Novel.NovelCrawler") as MockCrawler, \
             patch("builtins.print"):
            mock_instance = MagicMock()
            mock_instance.output_dir = "/tmp/test"
            MockCrawler.return_value = mock_instance

            from crawler.Novel import run
            run(
                novel_url="https://example.com/novel/test",
                sleep_time=1000,
                max_workers=5,
            )

            MockCrawler.assert_called_once()
            _, kwargs = MockCrawler.call_args
            self.assertEqual(kwargs["max_workers"], 5)

    @patch("crawler.Novel.pd")
    def test_forwards_to_xcrawler(self, mock_pd):
        import pandas as pd_real

        alias_df = pd_real.DataFrame([
            {"site": "xsite.com", "name": "xtestsite", "crawler_class": "XCrawler"},
        ])

        with patch("crawler.Novel.pd.read_csv", return_value=alias_df), \
             patch("crawler.X.XCrawler") as MockXC, \
             patch("builtins.print"):
            mock_instance = MagicMock()
            mock_instance.output_dir = "/tmp/test"
            MockXC.return_value = mock_instance

            from crawler.Novel import run
            run(
                novel_url="https://xsite.com/novel/test",
                sleep_time=1000,
                crawl_type_args={"start_chapter": 1, "end_chapter": 5},
                max_workers=3,
            )

            MockXC.assert_called_once()
            _, kwargs = MockXC.call_args
            self.assertEqual(kwargs["max_workers"], 3)

    @patch("crawler.Novel.pd")
    def test_missing_max_workers_defaults_to_four(self, mock_pd):
        import pandas as pd_real

        alias_df = pd_real.DataFrame([
            {"site": "example.com", "name": "testsite", "crawler_class": "NovelRequest"},
        ])

        with patch("crawler.Novel.pd.read_csv", return_value=alias_df), \
             patch("crawler.Novel.NovelCrawler") as MockCrawler, \
             patch("builtins.print"):
            mock_instance = MagicMock()
            mock_instance.output_dir = "/tmp/test"
            MockCrawler.return_value = mock_instance

            from crawler.Novel import run
            run(
                novel_url="https://example.com/novel/test",
                sleep_time=1000,
                # max_workers intentionally omitted
            )

            _, kwargs = MockCrawler.call_args
            self.assertEqual(kwargs["max_workers"], DEFAULT_MAX_WORKERS)

    def test_invalid_worker_count_is_rejected_before_loading_sources(self):
        with patch("crawler.Novel.pd.read_csv") as mock_read_csv:
            from crawler.Novel import run
            with self.assertRaises(ValueError):
                run(novel_url="https://example.com/novel/test", max_workers=6)
        mock_read_csv.assert_not_called()


class TestBuildNovelJob(unittest.TestCase):
    """build_novel_job includes max_workers in the output dict."""

    def test_includes_max_workers_when_provided(self):
        job = build_novel_job(
            url="https://example.com/story",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
            max_workers=5,
        )
        self.assertEqual(job["max_workers"], 5)

    def test_default_when_omitted(self):
        job = build_novel_job(
            url="https://example.com/story",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
        )
        self.assertEqual(job["max_workers"], DEFAULT_MAX_WORKERS)

    def test_one_for_sequential(self):
        job = build_novel_job(
            url="https://example.com/story",
            output_dir=None,
            sleep_time=1000,
            crawl_type="full",
            book_type="all",
            max_workers=1,
        )
        self.assertEqual(job["max_workers"], 1)

    def test_rejects_invalid_worker_count(self):
        with self.assertRaises(ValueError):
            build_novel_job(
                url="https://example.com/story",
                output_dir=None,
                sleep_time=1000,
                crawl_type="full",
                book_type="all",
                max_workers=6,
            )


class TestCustomVolumeListParsing(unittest.TestCase):
    def test_accepts_list_and_legacy_list_literal(self):
        expected = ["https://example.com/one", "https://example.com/two"]
        self.assertEqual(parse_custom_volume_list(expected), expected)
        self.assertEqual(parse_custom_volume_list(str(expected)), expected)

    def test_rejects_executable_or_invalid_values(self):
        for value in (
            "__import__('os').system('echo unsafe')",
            "not-a-list",
            {"https://example.com/one"},
            [""],
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_custom_volume_list(value)


if __name__ == "__main__":
    unittest.main()
