"""Fast, offline checks for the interactive terminal UI's job mapping."""

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from api import ContentType, CrawlRequest, FailureRecord, OutputFormat, PackagingMode, CrawlResult
from tui import MENU, SimpleCrawlerTUI, build_novel_job, parse_volume_urls, source_for_url
from utils.worker_config import DEFAULT_MAX_WORKERS


class TestTUIHelpers(unittest.TestCase):
    def test_gallery_is_a_menu_command(self):
        gallery = next(item for item in MENU if item.action == "gallery")
        self.assertEqual(gallery.key, "5")

    def test_gallery_request_uses_fixed_gallery_packaging(self):
        answers = iter(["example.com/gallery/a", "pdf", "single", "chapter.example/chapter", "25", "3", "y", "browser", "y", "2", "gallery-out"])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))

        request = ui._collect_crawl_request(ContentType.GALLERY)

        self.assertEqual(request.url, "https://example.com/gallery/a")
        self.assertEqual(request.output_format, OutputFormat.PDF)
        self.assertEqual(request.packaging, PackagingMode.PER_GALLERY)
        self.assertEqual(request.selection.value, "single")
        self.assertEqual(request.chapter_url, "https://chapter.example/chapter")
        self.assertEqual(request.sleep_ms, 25)
        self.assertEqual(request.max_retries, 3)
        self.assertTrue(request.keep_logged_in)
        self.assertFalse(request.headless)
        self.assertEqual(request.max_workers, 2)
        self.assertEqual(request.output_dir, "gallery-out")

    def test_comic_request_uses_fixed_chapter_packaging(self):
        answers = iter(["comic.example/story", "folder", "range", "2", "8", "", "", "", "", "", "", ""])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))

        request = ui._collect_crawl_request(ContentType.COMIC)

        self.assertEqual(request.output_format, OutputFormat.FOLDER)
        self.assertEqual(request.packaging, PackagingMode.PER_CHAPTER)
        self.assertEqual(request.start_index, 2)
        self.assertEqual(request.end_index, 8)

    def test_novel_request_asks_for_novel_output_options(self):
        answers = iter(["novel.example/story", "pdf", "per_volume", "full", "", "", "", "", "", "", ""])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))

        request = ui._collect_crawl_request(ContentType.NOVEL)

        self.assertEqual(request.output_format, OutputFormat.PDF)
        self.assertEqual(request.packaging, PackagingMode.PER_VOLUME)
        self.assertEqual(request.selection.value, "full")

    def test_every_supported_output_combination_maps_to_a_valid_request(self):
        combinations = (
            (ContentType.NOVEL, "epub", PackagingMode.COMBINED),
            (ContentType.NOVEL, "pdf", PackagingMode.COMBINED),
            (ContentType.COMIC, "cbz", PackagingMode.PER_CHAPTER),
            (ContentType.COMIC, "pdf", PackagingMode.PER_CHAPTER),
            (ContentType.COMIC, "folder", PackagingMode.PER_CHAPTER),
            (ContentType.GALLERY, "cbz", PackagingMode.PER_GALLERY),
            (ContentType.GALLERY, "pdf", PackagingMode.PER_GALLERY),
            (ContentType.GALLERY, "folder", PackagingMode.PER_GALLERY),
        )
        for content_type, output, packaging in combinations:
            with self.subTest(content_type=content_type, output=output):
                if content_type is ContentType.NOVEL:
                    answers = iter(["example.com/work", output, packaging.value, "full", "", "", "", "", "", "", ""])
                else:
                    answers = iter(["example.com/work", output, "full", "", "", "", "", "", "", ""])
                request = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))._collect_crawl_request(content_type)
                self.assertEqual(request.output_format, OutputFormat(output))
                self.assertEqual(request.packaging, packaging)

    def test_unknown_host_offers_ai_and_reuses_request(self):
        request = CrawlRequest(
            url="unknown.example/story",
            content_type=ContentType.NOVEL,
            output_format=OutputFormat.EPUB,
        )
        failed = CrawlResult(
            request=request,
            success=False,
            failures=(FailureRecord(stage="resolve", code="UnknownSiteError", message="unknown"),),
        )
        succeeded = CrawlResult(request=request, success=True)
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: "y")
        ai_result = SimpleNamespace(success=True, registered=True)

        with patch("tui.run_crawl", side_effect=[failed, succeeded]) as run, patch.object(
            ui, "_run_ai_update", return_value=ai_result
        ) as ai:
            ui._run_crawl_with_ai_fallback(request)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], request)
        self.assertEqual(run.call_args_list[1].args[0], request)
        self.assertTrue(run.call_args_list[0].kwargs["interactive"])
        self.assertTrue(run.call_args_list[1].kwargs["interactive"])
        ai.assert_called_once_with(request.url, request.content_type)

    def test_unknown_host_decline_does_not_start_ai_or_retry(self):
        request = CrawlRequest(
            url="unknown.example/story",
            content_type=ContentType.NOVEL,
            output_format=OutputFormat.EPUB,
        )
        failed = CrawlResult(
            request=request,
            success=False,
            failures=(FailureRecord(stage="resolve", code="UnknownSiteError", message="unknown"),),
        )
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: "n")
        with patch("tui.run_crawl", return_value=failed) as run, patch.object(ui, "_run_ai_update") as ai:
            ui._run_crawl_with_ai_fallback(request)
        run.assert_called_once_with(request, interactive=True)
        ai.assert_not_called()

    def test_ai_command_uses_content_type_and_callbacks(self):
        answers = iter(["new.example", "gallery", "", ""])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        result = SimpleNamespace(success=False, registered=False, errors=("cancelled",), draft_path=None, report_path=None)

        with patch.object(ui, "_run_ai_update", return_value=result) as update:
            ui.show_ai()

        update.assert_called_once_with("new.example", ContentType.GALLERY)

    def test_ai_callbacks_prompt_for_repair_registration_and_draft_overwrite(self):
        answers = iter(["y", "y", "y"])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        with patch("tui.AI_update", return_value=SimpleNamespace()) as update:
            ui._run_ai_update("new.example", ContentType.NOVEL)
        callbacks = update.call_args.kwargs
        self.assertTrue(callbacks["confirm_repair"](("bad selector",), 1))
        self.assertTrue(callbacks["confirm_registration"]({"content_type": "novel"}))
        self.assertTrue(callbacks["confirm_draft_overwrite"]("new.example"))

    def test_crawl_confirmation_cancels_without_running(self):
        answers = iter([
            "example.com/work", "epub", "combined", "full", "", "", "", "", "", "", "", "n"
        ])
        ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
        with patch.object(ui, "_run_crawl_with_ai_fallback") as run:
            ui.show_novel()
        run.assert_not_called()

    def test_batch_v2_command_uses_run_batch_without_interactive_flow(self):
        request = CrawlRequest(
            url="example.com/work", content_type="novel", output_format="epub"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            payload = {
                "version": 2,
                "jobs": [{"url": request.url, "content_type": "novel", "output_format": "epub"}],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            answers = iter([str(path), "y", ""])
            ui = SimpleCrawlerTUI(input_fn=lambda _prompt: next(answers))
            with patch("tui.run_batch", return_value=[CrawlResult(request=request, success=True)]) as run:
                ui.show_multi()
        run.assert_called_once_with(payload)

    def test_structured_incomplete_and_export_errors_render_without_traceback(self):
        request = CrawlRequest(
            url="example.com/work", content_type="novel", output_format="epub"
        )
        for code in ("IncompleteCrawlError", "ExporterError"):
            with self.subTest(code=code):
                stream = io.StringIO()
                ui = SimpleCrawlerTUI(output=Console(file=stream, force_terminal=False))
                ui._render_crawl_result(
                    CrawlResult(
                        request=request,
                        success=False,
                        failures=(FailureRecord(stage="export", code=code, message="structured failure"),),
                    )
                )
                rendered = stream.getvalue()
                self.assertIn(code, rendered)
                self.assertNotIn("Traceback", rendered)

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
