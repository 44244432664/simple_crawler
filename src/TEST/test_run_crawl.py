"""Tests for api.runner — Task 6 of ai-integration.

Covers the unified execution API: alias -> format resolution, breaking cutover
(unknown site and flow-less legacy formats fail before any crawl work), public
``run_crawl(CrawlRequest) -> CrawlResult``, batch v2 stable-ordering with mixed
success/failure, legacy batch/crawler rejection, and that no path bypasses flow
validation (a resolved flow is always built before execution).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from api import get_module, set_module_handler
from api.contracts import (
    ArtifactResult,
    ContentType,
    CrawlRequest,
    ExporterError,
    IncompleteCrawlError,
    InvalidRequestError,
    Metadata,
    MissingFlowError,
    OutputFormat,
    PackagingMode,
    UnknownSiteError,
    Volume,
)
from api.runner import (
    FormatResolver,
    reject_legacy_batch,
    reject_legacy_crawler,
    resolve_format,
    run_batch,
    run_crawl,
)

FLOW_MODULES = ("get_metadata", "volumes_prepare", "crawl_chapter", "create_ebook")

NOVEL_FLOW = [
    {"module": "get_metadata", "params": {"expected_selector": "h1.entry-title"}},
    {"module": "volumes_prepare", "params": {"expected_selector": "ul.chapters"}},
    {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
    {"module": "create_ebook"},
]


class RunnerHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._formats = os.path.join(self._tmp, "formats")
        self._aliases = os.path.join(self._tmp, "aliases.csv")
        os.makedirs(self._formats, exist_ok=True)
        self._save_handlers()
        self._register_fake_handlers(success=True)

    def tearDown(self):
        self._restore_handlers()

    # -- handler preservation -------------------------------------------------
    def _save_handlers(self):
        self._orig = {name: get_module(name).handler for name in FLOW_MODULES}

    def _restore_handlers(self):
        for name, handler in self._orig.items():
            set_module_handler(name, handler)

    def _register_fake_handlers(self, success=True):
        def _meta(ctx, params):
            ctx.output_dir = os.path.join(self._tmp, "out")
            ctx.metadata = Metadata(title="Test Title", source_url=ctx.request.url)
            ctx.main_page_html = "<html><body></body></html>"

        def _volumes(ctx, params):
            ctx.volumes = [Volume(index=0, title="Volume 1", chapters=())]

        def _chapters(ctx, params):
            ctx.chapter_contents = [
                {
                    "ordinal": 1,
                    "title": "Ch One",
                    "html": "<p>One</p>",
                    "volume_index": 0,
                    "position_in_volume": 1,
                    "identifier": "1",
                }
            ]

        def _export(ctx, params):
            if not success:
                raise ExporterError("export failed")
            artifact_path = os.path.join(ctx.output_dir or "", "out.epub")
            os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
            with open(artifact_path, "wb") as artifact_file:
                artifact_file.write(b"fixture artifact")
            ctx.artifacts.append(
                ArtifactResult(
                    output_format=OutputFormat.EPUB,
                    packaging=PackagingMode.COMBINED,
                    path=artifact_path,
                )
            )

        self._fakes = {
            "get_metadata": _meta,
            "volumes_prepare": _volumes,
            "crawl_chapter": _chapters,
            "create_ebook": _export,
        }
        for name, handler in self._fakes.items():
            set_module_handler(name, handler)

    # -- fixture writing -------------------------------------------------------
    def _write_aliases(self, rows):
        with open(self._aliases, "w", encoding="utf-8") as f:
            f.write("site,name,crawler_class\n")
            for site, name in rows:
                f.write(f"{site},{name},X\n")

    def _write_format(self, name, payload):
        with open(os.path.join(self._formats, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _good_site(self):
        self._write_aliases([("good.example", "good")])
        self._write_format("good", {"content_type": "novel", "flow": NOVEL_FLOW})
        return CrawlRequest(
            url="https://good.example/series/title",
            content_type="novel",
            output_format="epub",
            fetch_mode="requests",
        )

    def _resolver(self):
        return FormatResolver(self._formats, self._aliases)


class ResolutionTests(RunnerHarness):
    def test_resolve_aliases_to_format(self):
        req = self._good_site()
        fmt = resolve_format(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertEqual(fmt.get("content_type"), "novel")
        self.assertIsInstance(fmt.get("flow"), list)

    def test_unknown_site_raises(self):
        req = self._good_site()
        req = CrawlRequest(
            url="https://nope.example/x", content_type="novel", output_format="epub"
        )
        with self.assertRaises(UnknownSiteError):
            self._resolver().format_for(req)

    def test_flowless_format_raises_missing_flow(self):
        self._write_aliases([("bad.example", "bad")])
        self._write_format("bad", {"content_type": "novel"})
        req = CrawlRequest(
            url="https://bad.example/series/title",
            content_type="novel",
            output_format="epub",
        )
        with self.assertRaises(MissingFlowError):
            self._resolver().format_for(req)

    def test_missing_format_file_reports_missing_flow(self):
        self._write_aliases([("ghost.example", "ghost")])
        req = CrawlRequest(
            url="https://ghost.example/series/title",
            content_type="novel",
            output_format="epub",
        )
        with self.assertRaises(MissingFlowError):
            self._resolver().format_for(req)

    def test_trailing_slash_and_case_normalized(self):
        self._write_aliases([("MiXeD.Example/", "good")])
        self._write_format("good", {"content_type": "novel", "flow": NOVEL_FLOW})
        req = CrawlRequest(
            url="https://mixed.example/series/title",
            content_type="novel",
            output_format="epub",
        )
        self.assertEqual(resolve_format(req, formats_dir=self._formats, aliases_path=self._aliases)["content_type"], "novel")

    def test_www_and_scheme_aliases_are_normalized(self):
        self._write_aliases([("https://example.com/", "good")])
        self._write_format("good", {"content_type": "novel", "flow": NOVEL_FLOW})
        req = CrawlRequest(url="https://www.example.com/series/title", content_type="novel", output_format="epub")
        self.assertEqual(self._resolver().format_for(req)["content_type"], "novel")

    def test_resolver_reloads_aliases_after_registration(self):
        resolver = self._resolver()
        req = CrawlRequest(url="https://new.example/book", content_type="novel", output_format="epub")
        with self.assertRaises(UnknownSiteError):
            resolver.format_for(req)
        self._write_aliases([("new.example", "new")])
        self._write_format("new", {"content_type": "novel", "flow": NOVEL_FLOW})
        self.assertEqual(resolver.format_for(req)["content_type"], "novel")


class RunCrawlTests(RunnerHarness):
    def test_run_crawl_success(self):
        req = self._good_site()
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertTrue(result.success)
        self.assertEqual(result.request, req)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.failures, ())
        self.assertEqual(result.intermediate.output_dir, os.path.join(self._tmp, "out"))
        self.assertEqual(result.metadata.title, "Test Title")
        self.assertEqual(result.chapter_count, 1)
        self.assertTrue(os.path.isfile(result.intermediate.metadata_path))
        self.assertTrue(os.path.isfile(result.intermediate.log_path))
        with open(result.intermediate.log_path, encoding="utf-8") as handle:
            log_text = handle.read()
        self.assertIn("Stage completed: create_ebook", log_text)
        self.assertIn("Crawl state: completed", log_text)
        with open(result.intermediate.metadata_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["info"]["title"], "Test Title")
        self.assertIn("volumes", saved)

    def test_run_crawl_accepts_dict_request(self):
        self._good_site()
        result = run_crawl(
            {"url": "https://good.example/series/title", "content_type": "novel", "output_format": "epub", "fetch_mode": "requests"},
            formats_dir=self._formats,
            aliases_path=self._aliases,
        )
        self.assertTrue(result.success)

    def test_run_crawl_invalid_request_returns_failed_result(self):
        result = run_crawl("not-a-request", formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].stage, "request")
        self.assertEqual(result.failures[0].code, "InvalidRequestError")

    def test_run_crawl_unknown_site_returns_failed_result(self):
        req = self._good_site()
        req = CrawlRequest(url="https://nope.example/x", content_type="novel", output_format="epub")
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].code, "UnknownSiteError")
        self.assertEqual(result.failures[0].stage, "resolve")

    def test_unexpected_resolver_io_error_is_structured(self):
        req = self._good_site()
        with patch.object(FormatResolver, "format_for", side_effect=PermissionError("denied")):
            result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].stage, "resolve")
        self.assertEqual(result.failures[0].code, "PermissionError")

    def test_run_crawl_flowless_returned_before_flow_build(self):
        self._write_aliases([("bad.example", "bad")])
        self._write_format("bad", {"content_type": "novel"})
        req = CrawlRequest(url="https://bad.example/x", content_type="novel", output_format="epub")
        with patch("api.runner.execute_flow") as exec_mock, patch("api.runner.build_flow") as build_mock:
            result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].code, "MissingFlowError")
        exec_mock.assert_not_called()
        build_mock.assert_not_called()

    def test_run_crawl_exporter_failure(self):
        req = self._good_site()
        self._register_fake_handlers(success=False)
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].code, "ExporterError")
        self.assertEqual(result.failures[0].stage, "export")
        self.assertTrue(os.path.isfile(result.intermediate.metadata_path))
        with open(result.intermediate.metadata_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("failures", saved)
        self.assertTrue(os.path.isfile(result.intermediate.log_path))
        with open(result.intermediate.log_path, encoding="utf-8") as handle:
            log_text = handle.read()
        self.assertIn("Crawl state: failed", log_text)
        self.assertIn("Failure: export ExporterError: export failed", log_text)

    def test_non_novel_crawl_does_not_write_novel_info(self):
        self._write_aliases([("comic.example", "comic")])
        comic_flow = [dict(step) for step in NOVEL_FLOW]
        comic_flow[2] = {
            "module": "crawl_chapter",
            "params": {"actions": ["download_image"]},
        }
        self._write_format("comic", {"content_type": "comic", "flow": comic_flow})
        request = CrawlRequest(
            url="https://comic.example/work",
            content_type="comic",
            output_format="cbz",
        )
        result = run_crawl(request, formats_dir=self._formats, aliases_path=self._aliases)

        self.assertFalse(os.path.exists(os.path.join(self._tmp, "out", "novel_info.json")))
        self.assertIsNone(result.intermediate.metadata_path)

    def test_failure_reports_the_actual_flow_stage(self):
        req = self._good_site()

        def _prepare_failure(ctx, params):
            raise RuntimeError("prepare failed")

        set_module_handler("volumes_prepare", _prepare_failure)
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].stage, "volume_preparation")

    def test_metadata_failure_writes_novel_info_to_custom_output(self):
        original = self._good_site()
        request = CrawlRequest(
            url=original.url,
            content_type="novel",
            output_format="epub",
            output_dir=os.path.join(self._tmp, "failed-metadata"),
        )

        def _metadata_failure(ctx, params):
            raise RuntimeError("metadata failed")

        set_module_handler("get_metadata", _metadata_failure)
        result = run_crawl(request, formats_dir=self._formats, aliases_path=self._aliases)

        self.assertFalse(result.success)
        self.assertTrue(os.path.isfile(result.intermediate.metadata_path))
        with open(result.intermediate.metadata_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["info"]["novel_url"], request.url)

    def test_resolution_failure_writes_a_fallback_log(self):
        request = CrawlRequest(
            url="https://unknown.example/work",
            content_type="novel",
            output_format="epub",
        )
        result = run_crawl(request, formats_dir=self._formats, aliases_path=self._aliases)

        self.assertFalse(result.success)
        self.assertTrue(os.path.isfile(result.intermediate.log_path))
        self.assertEqual(
            result.intermediate.output_dir,
            "outputs/Novel/unknown_unknown.example",
        )
        with open(result.intermediate.log_path, encoding="utf-8") as handle:
            self.assertIn("Failure: resolve UnknownSiteError", handle.read())

    def test_run_crawl_rejects_false_success_without_artifact(self):
        req = self._good_site()
        set_module_handler("create_ebook", lambda ctx, params: None)
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].stage, "export")
        self.assertIn("without producing any artifacts", result.failures[0].message)

    def test_run_crawl_rejects_nonexistent_artifact(self):
        req = self._good_site()

        def _missing(ctx, params):
            ctx.artifacts.append(ArtifactResult(
                output_format=OutputFormat.EPUB,
                packaging=PackagingMode.COMBINED,
                path=os.path.join(self._tmp, "missing.epub"),
            ))

        set_module_handler("create_ebook", _missing)
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertFalse(result.success)
        self.assertIn("does not exist", result.failures[0].message)

    def test_nonexistent_manifest_is_not_reported_as_intermediate(self):
        req = self._good_site()
        result = run_crawl(req, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertTrue(result.success)
        self.assertIsNone(result.intermediate.img_info_path)


class BatchTests(RunnerHarness):
    def _batch_payload(self):
        return {
            "version": 2,
            "jobs": [
                {"url": "https://good.example/series/one", "content_type": "novel", "output_format": "epub", "fetch_mode": "requests"},
                {"url": "https://good.example/series/two", "content_type": "novel", "output_format": "epub", "fetch_mode": "requests"},
            ],
        }

    def test_run_batch_stable_ordering(self):
        self._good_site()
        results = run_batch(self._batch_payload(), formats_dir=self._formats, aliases_path=self._aliases)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))
        urls = [r.request.url for r in results]
        self.assertEqual(urls, ["https://good.example/series/one", "https://good.example/series/two"])

    def test_run_batch_mixed_success(self):
        self._good_site()
        payload = self._batch_payload()
        payload["jobs"].append(
            {"url": "https://nope.example/x", "content_type": "novel", "output_format": "epub"}
        )
        results = run_batch(payload, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertTrue(results[1].success)
        self.assertFalse(results[2].success)
        self.assertEqual(results[2].failures[0].code, "UnknownSiteError")

    def test_invalid_job_returns_ordered_failed_result(self):
        self._good_site()
        payload = self._batch_payload()
        payload["jobs"].insert(1, {"url": "https://good.example/bad"})
        results = run_batch(payload, formats_dir=self._formats, aliases_path=self._aliases)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertEqual(results[1].failures[0].stage, "request")
        self.assertTrue(results[2].success)

    def test_run_batch_rejects_legacy(self):
        with self.assertRaises(InvalidRequestError):
            run_batch({"version": 1, "jobs": []}, formats_dir=self._formats, aliases_path=self._aliases)

    def test_reject_legacy_batch(self):
        with self.assertRaises(InvalidRequestError):
            reject_legacy_batch({"sites": ["docln.sbs"], "urls": ["x"]})

    def test_reject_legacy_crawler(self):
        with self.assertRaises(MissingFlowError):
            reject_legacy_crawler()

    def test_legacy_novel_wrapper_terminates_before_crawl(self):
        from crawler.Novel import run

        with self.assertRaisesRegex(MissingFlowError, "breaking cutover"):
            run(novel_url="https://good.example/book")

    def test_other_legacy_crawler_wrappers_terminate(self):
        from crawler.Comic import run as comic_run
        from crawler.Gallery import run as gallery_run
        from crawler.crawl_qq import control_QQcrawler

        with self.assertRaises(MissingFlowError):
            comic_run("https://good.example/comic", "/tmp/unused")
        with self.assertRaises(MissingFlowError):
            gallery_run("https://good.example/gallery")
        with self.assertRaises(MissingFlowError):
            control_QQcrawler(object(), "get_all")

    def test_legacy_batch_wrapper_returns_migration_error(self):
        from utils.crawl_multi import crawl_multi

        with self.assertRaisesRegex(InvalidRequestError, "version"):
            crawl_multi("legacy.json")


class LegacyFormatRejection(RunnerHarness):
    def test_all_current_formats_are_flowless(self):
        from api.runner import load_aliases
        from api.contracts import MissingFlowError as MFE

        resolver = FormatResolver()  # real data dir
        self.assertTrue(len(resolver.aliases) > 0)
        for site, alias in resolver.aliases.items():
            req = CrawlRequest(
                url=f"https://{site}/{alias['name']}",
                content_type="novel",
                output_format="epub",
            )
            with self.assertRaises(MFE):
                resolver.format_for(req)


if __name__ == "__main__":
    unittest.main()
