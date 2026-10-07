"""Task 1 — pipeline contract tests.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_contracts.py -v
    # or
    python -m unittest TEST/test_contracts.py -v
"""

import unittest
from unittest.mock import patch

from api import contracts as c
from api.contracts import (
    AIConfigError,
    AIValidationError,
    ArtifactResult,
    BATCH_VERSION,
    BatchRequest,
    Chapter,
    ContentType,
    CrawlContext,
    CrawlRequest,
    CrawlResult,
    DEFAULT_HEADLESS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SLEEP_MS,
    ExporterError,
    FailureRecord,
    FetchFailureError,
    IMAGE_MANIFEST_SCHEMA_VERSION,
    ImageManifestEntry,
    ImageStatus,
    IncompleteCrawlError,
    IntermediatePaths,
    InvalidFlowError,
    InvalidRequestError,
    Metadata,
    MissingFlowError,
    OutputFormat,
    PackagingMode,
    PipelineError,
    SelectionMode,
    Volume,
    default_packaging,
    is_supported_combination,
    packaging_modes_for,
)

VALID_URL = "https://example.com/novel/one"
VALID_CHAPTER_URL = "https://example.com/chapter/12"


def _valid_novel(**overrides):
    base = {
        "url": VALID_URL,
        "content_type": ContentType.NOVEL,
        "output_format": OutputFormat.EPUB,
    }
    base.update(overrides)
    return CrawlRequest(**base)


class TestValidComplexions(unittest.TestCase):
    """Every valid content/output/packaging combination normalizes."""

    def test_all_valid_combinations_construct(self):
        matrix = {
            ContentType.NOVEL: {
                OutputFormat.EPUB: (PackagingMode.COMBINED, PackagingMode.PER_VOLUME),
                OutputFormat.PDF: (PackagingMode.COMBINED, PackagingMode.PER_VOLUME),
            },
            ContentType.COMIC: {
                OutputFormat.CBZ: (PackagingMode.PER_CHAPTER,),
                OutputFormat.PDF: (PackagingMode.PER_CHAPTER,),
                OutputFormat.FOLDER: (PackagingMode.PER_CHAPTER,),
            },
            ContentType.GALLERY: {
                OutputFormat.CBZ: (PackagingMode.PER_GALLERY,),
                OutputFormat.PDF: (PackagingMode.PER_GALLERY,),
                OutputFormat.FOLDER: (PackagingMode.PER_GALLERY,),
            },
        }
        built = 0
        for content_type, formats in matrix.items():
            for output_format, packagings in formats.items():
                for packaging in packagings:
                    request = CrawlRequest(
                        url=VALID_URL,
                        content_type=content_type,
                        output_format=output_format,
                        packaging=packaging,
                    )
                    self.assertEqual(request.content_type, content_type)
                    self.assertEqual(request.output_format, output_format)
                    self.assertEqual(request.packaging, packaging)
                    built += 1
        self.assertEqual(built, 10)

    def test_default_packaging_follows_content_type(self):
        self.assertEqual(default_packaging(ContentType.NOVEL), PackagingMode.COMBINED)
        self.assertEqual(default_packaging(ContentType.COMIC), PackagingMode.PER_CHAPTER)
        self.assertEqual(default_packaging(ContentType.GALLERY), PackagingMode.PER_GALLERY)

    def test_packaging_none_uses_content_default(self):
        for content_type in ContentType:
            request = CrawlRequest(
                url=VALID_URL,
                content_type=content_type,
                output_format=next(iter(SUPPORTED[content_type])),
                packaging=None,
            )
            self.assertEqual(request.packaging, default_packaging(content_type))

    def test_optional_fields_get_documented_defaults(self):
        request = _valid_novel()
        self.assertIs(request.selection, SelectionMode.FULL)
        self.assertIs(request.packaging, PackagingMode.COMBINED)
        self.assertIsNone(request.start_index)
        self.assertIsNone(request.end_index)
        self.assertIsNone(request.chapter_url)
        self.assertIsNone(request.output_dir)
        self.assertIsNone(request.fetch_mode)
        self.assertIs(request.headless, DEFAULT_HEADLESS)
        self.assertEqual(request.sleep_ms, DEFAULT_SLEEP_MS)
        self.assertIsNone(request.max_retries)
        self.assertEqual(request.max_workers, DEFAULT_MAX_WORKERS)
        self.assertFalse(request.keep_logged_in)

    def test_deterministic_normalization_and_equality(self):
        a = CrawlRequest.from_dict(
            {"url": "example.com/novel/one", "content_type": "novel", "output_format": "epub"}
        )
        b = _valid_novel()
        self.assertEqual(a, b)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_string_enums_are_coerced(self):
        request = CrawlRequest.from_dict(
            {
                "url": "example.com/novel/one",
                "content_type": "NOVEL",
                "output_format": "epub",
                "selection": "full",
                "packaging": "combined",
            }
        )
        self.assertIs(request.content_type, ContentType.NOVEL)
        self.assertIs(request.output_format, OutputFormat.EPUB)
        self.assertIs(request.selection, SelectionMode.FULL)
        self.assertIs(request.packaging, PackagingMode.COMBINED)

    def test_is_supported_combination_helper(self):
        self.assertTrue(
            is_supported_combination(ContentType.NOVEL, OutputFormat.EPUB, PackagingMode.COMBINED)
        )
        self.assertFalse(
            is_supported_combination(ContentType.NOVEL, OutputFormat.CBZ, PackagingMode.COMBINED)
        )
        self.assertEqual(
            packaging_modes_for(ContentType.GALLERY, OutputFormat.FOLDER),
            (PackagingMode.PER_GALLERY,),
        )
        self.assertEqual(packaging_modes_for(ContentType.NOVEL, OutputFormat.CBZ), ())


SUPPORTED = c.SUPPORTED_OUTPUT_COMBINATIONS


class TestUnsupportedCombinations(unittest.TestCase):
    """Invalid content/output/packaging pairs fail before any side effect."""

    def _expect_invalid(self, content_type, output_format, packaging=None):
        with self.assertRaises(InvalidRequestError):
            CrawlRequest(
                url=VALID_URL,
                content_type=content_type,
                output_format=output_format,
                packaging=packaging,
            )

    def test_novel_cannot_use_cbz_or_folder(self):
        self._expect_invalid(ContentType.NOVEL, OutputFormat.CBZ)
        self._expect_invalid(ContentType.NOVEL, OutputFormat.FOLDER)

    def test_comic_cannot_use_epub(self):
        self._expect_invalid(ContentType.COMIC, OutputFormat.EPUB)

    def test_gallery_cannot_use_epub(self):
        self._expect_invalid(ContentType.GALLERY, OutputFormat.EPUB)

    def test_comic_gallery_reject_non_per_volume_packaging(self):
        self._expect_invalid(ContentType.COMIC, OutputFormat.CBZ, PackagingMode.COMBINED)
        self._expect_invalid(ContentType.GALLERY, OutputFormat.FOLDER, PackagingMode.COMBINED)

    def test_error_message_lists_supported_options(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            CrawlRequest(
                url=VALID_URL,
                content_type=ContentType.NOVEL,
                output_format=OutputFormat.CBZ,
            )
        message = str(ctx.exception)
        self.assertIn("cbz", message)
        self.assertIn("novel", message)


class TestUrlValidation(unittest.TestCase):
    """URL normalization and rejection of malformed URLs."""

    def test_scheme_less_url_gets_https(self):
        request = _valid_novel(url="example.com/novel/one")
        self.assertEqual(request.url, "https://example.com/novel/one")

    def test_canonicalization_lowers_host_and_drops_fragment(self):
        request = _valid_novel(url="HTTP://EXAMPLE.com:8080/n?x=1#frag")
        self.assertEqual(request.url, "http://example.com:8080/n?x=1")

    def test_localhost_is_valid(self):
        request = _valid_novel(url="localhost:8080/story")
        self.assertEqual(request.url, "https://localhost:8080/story")

    def test_blank_url_rejected(self):
        for value in ("", "   "):
            with self.assertRaises(InvalidRequestError):
                _valid_novel(url=value)

    def test_non_http_scheme_rejected(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(url="ftp://example.com/novel")

    def test_hostless_url_rejected(self):
        for value in ("http://", "https://", "https:///path"):
            with self.assertRaises(InvalidRequestError):
                _valid_novel(url=value)

    def test_whitespace_host_rejected(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(url="not a url")

    def test_dotless_host_rejected(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(url="notaurl")

    def test_non_string_url_rejected(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(url=12345)

    def test_malformed_port_ipv6_and_whitespace_rejected_deterministically(self):
        for value in (
            "https://example.com:bad/path",
            "https://example.com:",
            "https://example.com:0/path",
            "https://[oops/path",
            "https://example.com/a b",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(url=value)

    def test_url_credentials_are_rejected_without_echoing_them(self):
        secret = "TOPSECRET"
        with self.assertRaises(InvalidRequestError) as ctx:
            _valid_novel(url=f"https://alice:{secret}@example.com/story")
        self.assertNotIn(secret, str(ctx.exception))


class TestSelectionValidation(unittest.TestCase):
    """Full/range/single selection coherence is enforced."""

    def test_default_full(self):
        request = _valid_novel()
        self.assertIs(request.selection, SelectionMode.FULL)

    def test_full_rejects_range_indexes(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.FULL, start_index=1)
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.FULL, end_index=5)

    def test_full_rejects_chapter_url(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.FULL, chapter_url=VALID_CHAPTER_URL)

    def test_valid_range_accepted(self):
        request = _valid_novel(
            selection=SelectionMode.RANGE, start_index=2, end_index=7, output_format=OutputFormat.PDF
        )
        self.assertEqual((request.start_index, request.end_index), (2, 7))

    def test_range_requires_both_indexes(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            _valid_novel(selection=SelectionMode.RANGE, start_index=1)
        self.assertIn("start_index and end_index", str(ctx.exception))
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, end_index=5)

    def test_range_index_must_be_positive(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, start_index=0, end_index=5)
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, start_index=-1, end_index=5)

    def test_range_end_must_not_precede_start(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, start_index=7, end_index=2)

    def test_range_indexes_must_be_integers(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, start_index="1", end_index=5)
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.RANGE, start_index=True, end_index=5)

    def test_range_rejects_chapter_url(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(
                selection=SelectionMode.RANGE,
                start_index=1,
                end_index=3,
                chapter_url=VALID_CHAPTER_URL,
            )

    def test_single_requires_chapter_url(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            _valid_novel(selection=SelectionMode.SINGLE)
        self.assertIn("chapter_url", str(ctx.exception))

    def test_single_normalizes_chapter_url(self):
        request = _valid_novel(
            selection=SelectionMode.SINGLE,
            chapter_url="example.com/chapter/12",
        )
        self.assertEqual(request.chapter_url, "https://example.com/chapter/12")

    def test_single_rejects_indexes(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.SINGLE, chapter_url=VALID_CHAPTER_URL, start_index=1)
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.SINGLE, chapter_url=VALID_CHAPTER_URL, end_index=3)

    def test_single_rejects_invalid_chapter_url(self):
        with self.assertRaises(InvalidRequestError):
            _valid_novel(selection=SelectionMode.SINGLE, chapter_url="ftp://example.com/x")


class TestWorkerAndDelayValidation(unittest.TestCase):
    """max_workers and sleep_ms bounds are enforced."""

    def test_max_workers_bounds(self):
        self.assertEqual(_valid_novel(max_workers=1).max_workers, 1)
        self.assertEqual(_valid_novel(max_workers=DEFAULT_MAX_WORKERS).max_workers, 4)
        for bad in (0, 6, -1, 100):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(max_workers=bad)

    def test_max_workers_rejects_non_ints(self):
        for bad in (True, "4", 4.5, None):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(max_workers=bad)

    def test_sleep_ms_non_negative(self):
        self.assertEqual(_valid_novel(sleep_ms=0).sleep_ms, 0)
        with self.assertRaises(InvalidRequestError):
            _valid_novel(sleep_ms=-1)

    def test_sleep_ms_must_be_int(self):
        for bad in (True, "1000", 1000.5, None):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(sleep_ms=bad)

    def test_max_retries_is_optional_non_negative_integer(self):
        self.assertEqual(_valid_novel(max_retries=0).max_retries, 0)
        self.assertEqual(_valid_novel(max_retries=3).max_retries, 3)
        for bad in (True, -1, "3", 3.5):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(max_retries=bad)


class TestFetchAndBooleanValidation(unittest.TestCase):
    """fetch_mode, headless, keep_logged_in, output_dir validation."""

    def test_fetch_mode_none_and_valid_values(self):
        self.assertIsNone(_valid_novel().fetch_mode)
        for mode in ("requests", "browser", "auto"):
            self.assertEqual(_valid_novel(fetch_mode=mode).fetch_mode, mode)

    def test_fetch_mode_rejects_unknown(self):
        for bad in ("phantom", "selenium", 5):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidRequestError):
                    _valid_novel(fetch_mode=bad)

    def test_headless_must_be_bool(self):
        self.assertTrue(_valid_novel(headless=True).headless)
        self.assertFalse(_valid_novel(headless=False).headless)
        for bad in ("yes", 1, None):
            with self.assertRaises(InvalidRequestError):
                _valid_novel(headless=bad)

    def test_keep_logged_in_must_be_bool(self):
        self.assertTrue(_valid_novel(keep_logged_in=True).keep_logged_in)
        for bad in ("yes", 1, None):
            with self.assertRaises(InvalidRequestError):
                _valid_novel(keep_logged_in=bad)

    def test_output_dir_accepts_str_and_path(self):
        request = _valid_novel(output_dir="outputs/Novel/fancy")
        self.assertEqual(request.output_dir, "outputs/Novel/fancy")
        from pathlib import Path

        request = _valid_novel(output_dir=Path("outputs/Comic/x"))
        self.assertEqual(request.output_dir, "outputs/Comic/x")

    def test_output_dir_rejects_blank(self):
        for bad in ("", "   "):
            with self.assertRaises(InvalidRequestError):
                _valid_novel(output_dir=bad)


class TestSerialization(unittest.TestCase):
    """Request dict import/export used by batch v2."""

    def test_to_dict_from_dict_round_trip(self):
        request = _valid_novel(
            selection=SelectionMode.RANGE,
            start_index=2,
            end_index=5,
            output_format=OutputFormat.PDF,
            packaging=PackagingMode.PER_VOLUME,
            output_dir="outputs/Novel/one",
            fetch_mode="auto",
            headless=False,
            sleep_ms=500,
            max_retries=2,
            max_workers=3,
            keep_logged_in=True,
        )
        restored = CrawlRequest.from_dict(request.to_dict())
        self.assertEqual(restored, request)
        data = request.to_dict()
        self.assertEqual(data["content_type"], "novel")
        self.assertEqual(data["selection"], "range")
        self.assertEqual(data["start_index"], 2)
        self.assertEqual(data["headless"], False)

    def test_to_dict_is_json_safe(self):
        import json

        data = _valid_novel().to_dict()
        json.dumps(data)  # must not raise

    def test_unknown_field_rejected(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            CrawlRequest.from_dict(
                {
                    "url": VALID_URL,
                    "content_type": "novel",
                    "output_format": "epub",
                    "definitely_typo": True,
                }
            )
        self.assertIn("definitely_typo", str(ctx.exception))

    def test_missing_required_fields_rejected(self):
        base = {"url": VALID_URL, "content_type": "novel", "output_format": "epub"}
        for key in ("url", "content_type", "output_format"):
            minus = dict(base)
            del minus[key]
            with self.subTest(missing=key):
                with self.assertRaises(InvalidRequestError):
                    CrawlRequest.from_dict(minus)

    def test_non_dict_payload_rejected(self):
        with self.assertRaises(InvalidRequestError):
            CrawlRequest.from_dict([VALID_URL])

    def test_invalid_enum_string_rejected(self):
        with self.assertRaises(InvalidRequestError):
            CrawlRequest.from_dict(
                {"url": VALID_URL, "content_type": "video", "output_format": "epub"}
            )
        with self.assertRaises(InvalidRequestError):
            CrawlRequest.from_dict(
                {"url": VALID_URL, "content_type": "novel", "output_format": "web"}
            )


class TestResultReportingSerialization(unittest.TestCase):
    """CrawlResult to_dict/from_dict round trip."""

    def _sample_result(self):
        metadata = Metadata(title="Alpha", author="Beta", genres=("a", "b"))
        chapter = Chapter(
            ordinal=1,
            volume_index=1,
            position_in_volume=1,
            identifier="12.5",
            title="Ch 12.5",
            url=VALID_CHAPTER_URL,
        )
        volume = Volume(index=1, title="Vol 1", chapters=(chapter,))
        request = _valid_novel()
        return CrawlResult(
            request=request,
            success=True,
            artifacts=(
                ArtifactResult(
                    output_format=OutputFormat.EPUB,
                    packaging=PackagingMode.COMBINED,
                    path="outputs/Novel/one/Alpha.epub",
                ),
            ),
            failures=(),
            metadata=metadata,
            volumes=(volume,),
            chapter_count=1,
            image_count=2,
            intermediate=IntermediatePaths(
                output_dir="outputs/Novel/one",
                metadata_path="outputs/Novel/one/novel_info.json",
                img_info_path="outputs/Novel/one/img/img_info.json",
            ),
        )

    def test_round_trip(self):
        result = self._sample_result()
        restored = CrawlResult.from_dict(result.to_dict())
        self.assertEqual(restored, result)

    def test_failure_record_serialization(self):
        result = CrawlResult(
            request=_valid_novel(),
            success=False,
            failures=(
                FailureRecord(
                    stage="crawl_chapter",
                    code="incomplete",
                    message="chapter 3 permanently failed",
                    chapter_ordinal=3,
                    url=VALID_CHAPTER_URL,
                ),
            ),
        )
        data = result.to_dict()
        self.assertFalse(data["success"])
        self.assertEqual(data["failures"][0]["code"], "incomplete")
        restored = CrawlResult.from_dict(data)
        self.assertEqual(restored.failures[0].chapter_ordinal, 3)

    def test_failure_report_is_secret_safe_by_shape(self):
        result = CrawlResult(request=_valid_novel(), success=False)
        data = result.to_dict()
        self.assertIn("failures", data)
        self.assertIn("intermediate", data)


class TestBatchRequest(unittest.TestCase):
    """Batch v2 container validation."""

    def test_parse_valid_v2_batch(self):
        payload = {
            "version": 2,
            "jobs": [
                {"url": VALID_URL, "content_type": "novel", "output_format": "epub"},
                {
                    "url": VALID_URL,
                    "content_type": "comic",
                    "output_format": "cbz",
                    "selection": "range",
                    "start_index": 1,
                    "end_index": 3,
                },
            ],
        }
        batch = BatchRequest.from_dict(payload)
        self.assertEqual(batch.version, BATCH_VERSION)
        self.assertEqual(len(batch.jobs), 2)
        self.assertIs(batch.jobs[0].content_type, ContentType.NOVEL)
        self.assertIs(batch.jobs[1].selection, SelectionMode.RANGE)
        self.assertIs(batch.jobs[1].packaging, PackagingMode.PER_CHAPTER)
        self.assertEqual(BatchRequest.from_dict(batch.to_dict()), batch)

    def test_non_v2_version_rejected_with_guidance(self):
        for version in (1, 3, "2"):
            with self.subTest(version=version):
                with self.assertRaises(InvalidRequestError) as ctx:
                    BatchRequest.from_dict({"version": version, "jobs": []})
                self.assertIn("Unsupported batch version", str(ctx.exception))
                self.assertIn("migrate", str(ctx.exception))

    def test_missing_jobs_rejected(self):
        with self.assertRaises(InvalidRequestError):
            BatchRequest.from_dict({"version": 2})

    def test_jobs_must_be_list(self):
        with self.assertRaises(InvalidRequestError):
            BatchRequest.from_dict({"version": 2, "jobs": {}})

    def test_invalid_job_rejected(self):
        with self.assertRaises(InvalidRequestError):
            BatchRequest.from_dict(
                {
                    "version": 2,
                    "jobs": [
                        {"url": VALID_URL, "content_type": "novel", "output_format": "cbz"}
                    ],
                }
            )

    def test_non_dict_payload_rejected(self):
        with self.assertRaises(InvalidRequestError):
            BatchRequest.from_dict([1, 2, 3])


class TestValueTypes(unittest.TestCase):
    """Metadata/Volume/Chapter/ImageManifest value types serialize cleanly."""

    def test_chapter_keeps_decimal_identifier_as_string(self):
        chapter = Chapter(ordinal=2, volume_index=1, position_in_volume=2, identifier="12.5")
        self.assertEqual(chapter.identifier, "12.5")
        self.assertEqual(chapter.to_dict()["identifier"], "12.5")

    def test_image_manifest_entry_defaults(self):
        entry = ImageManifestEntry()
        self.assertEqual(entry.schema_version, IMAGE_MANIFEST_SCHEMA_VERSION)
        self.assertIs(entry.status, ImageStatus.DOWNLOADED)
        self.assertEqual(entry.to_dict()["status"], "downloaded")

    def test_image_manifest_entry_round_trip(self):
        entry = ImageManifestEntry(
            chapter_ordinal=1,
            occurrence=0,
            source_url="https://img.example.com/1.jpg",
            raw_tag='<img src="https://img.example.com/1.jpg">',
            filename="vol1_chap1_img1.jpg",
            relative_path="img/vol1_chap1_img1.jpg",
            replacement_tag='<img src="img/vol1_chap1_img1.jpg">',
            status=ImageStatus.FAILED,
        )
        data = entry.to_dict()
        self.assertEqual(data["schema_version"], IMAGE_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["raw_tag"], '<img src="https://img.example.com/1.jpg">')

    def test_metadata_and_volume_serialization(self):
        metadata = Metadata(title="T", author="A", other_info={"k": "v"})
        self.assertEqual(metadata.to_dict()["other_info"], {"k": "v"})
        volume = Volume(index=1, title="V1")
        self.assertEqual(volume.to_dict()["synthetic"], False)

    def test_context_starting_state(self):
        context = CrawlContext(request=_valid_novel())
        self.assertIsNone(context.metadata)
        self.assertEqual(context.volumes, [])
        self.assertEqual(context.image_entries, [])
        self.assertIsNone(context.output_dir)


class TestErrorTaxonomy(unittest.TestCase):
    """All pipeline errors subclass PipelineError and are distinct."""

    def test_error_hierarchy(self):
        errors = (
            InvalidRequestError,
            MissingFlowError,
            InvalidFlowError,
            FetchFailureError,
            IncompleteCrawlError,
            ExporterError,
            AIConfigError,
            AIValidationError,
        )
        for error in errors:
            with self.subTest(error=error):
                self.assertTrue(issubclass(error, PipelineError))
                self.assertTrue(issubclass(error, Exception))

    def test_request_error_is_invalid_request(self):
        with self.assertRaises(InvalidRequestError) as ctx:
            _valid_novel(url="")
        self.assertIsInstance(ctx.exception, PipelineError)

    def test_flow_errors_are_distinct(self):
        self.assertIsNot(MissingFlowError, InvalidFlowError)
        self.assertIsNot(IncompleteCrawlError, ExporterError)
        self.assertIsNot(AIConfigError, AIValidationError)


class TestNoSideEffects(unittest.TestCase):
    """Validation and parsing never touch the network or the filesystem."""

    def test_request_creation_makes_no_network_or_fs_calls(self):
        with patch("requests.get") as fake_get, patch("os.makedirs") as fake_mkdir:
            payload = {
                "version": 2,
                "jobs": [_valid_novel().to_dict()],
            }
            BatchRequest.from_dict(payload)
            fake_get.assert_not_called()
            fake_mkdir.assert_not_called()

    def test_crawl_result_serialization_is_pure(self):
        with patch("os.makedirs") as fake_mkdir:
            result = CrawlResult(request=_valid_novel(), success=True)
            result.to_dict()
            fake_mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
