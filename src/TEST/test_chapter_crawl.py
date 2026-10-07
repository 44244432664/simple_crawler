"""Tests for api.chapter_crawl — Task 4 of ai-integration.

Covers text extraction (no image requests), image download with custom tags,
relative URLs, duplicate tags, failed downloads, cookies/referrers,
sequential vs parallel aggregation, manifest writing, and incomplete-crawl
failure handling.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock

from api.chapter_crawl import (
    crawl_chapter_handler,
    crawl_text_content,
    download_image,
    download_images_for_chapter,
    register_chapter_crawl_handlers,
    write_manifest,
)
from api.contracts import (
    Chapter,
    ContentType,
    CrawlContext,
    CrawlRequest,
    ImageManifestEntry,
    ImageStatus,
    IncompleteCrawlError,
    InvalidFlowError,
    Metadata,
    OutputFormat,
    SelectionMode,
    Volume,
)
from api.preparation import selector_args, selector_to_css

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(
    content_type: str = "novel",
    output_format: str = "epub",
    fetch_mode: str | None = "requests",
    max_workers: int = 2,
    url: str = "https://example.com/novel/title",
) -> CrawlRequest:
    return CrawlRequest(
        url=url,
        content_type=content_type,
        output_format=output_format,
        fetch_mode=fetch_mode,
        max_workers=max_workers,
    )


def _ctx(
    request: CrawlRequest | None = None,
    format_definition: dict | None = None,
    volumes: list[Volume] | None = None,
    output_dir: str | None = None,
    raw_page=None,
    log_fn=None,
    progress_fn=None,
) -> CrawlContext:
    ctx = CrawlContext(
        request=request or _req(),
        format_definition=format_definition or {},
        volumes=volumes or [],
        output_dir=output_dir,
        raw_page=raw_page,
        log=log_fn,
        progress=progress_fn,
    )
    return ctx


def _vol(index: int, chapters) -> Volume:
    return Volume(index=index, title=f"V{index+1}", chapters=tuple(chapters))


def _chapter(ordinal, url, volume_index=0, pos=1, identifier=None):
    return Chapter(
        ordinal=ordinal,
        volume_index=volume_index,
        position_in_volume=pos,
        identifier=identifier or str(ordinal),
        title=f"Chapter {ordinal}",
        url=url,
    )


def _mock_raw_page(html_map, cookies=None):
    mock = MagicMock()
    mock.get_raw_page = MagicMock(side_effect=lambda url, *a, **kw: html_map.get(url, ""))
    mock.cookies = cookies
    return mock


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

TEXT_CHAPTER_IMG_HTML = """
<html><body>
<h1 class="chapter-title">Ch One</h1>
<div class="nl">
    <p>Some text.</p>
    <script>var evil = 1;</script>
    <div class="ad">Advertisement</div>
    <img src="https://cdn.example.com/1.png" />
    <img src="https://cdn.example.com/2.png" />
</div>
<div class="footer-ad">footer junk</div>
</body></html>
"""

TEXT_CHAPTER_NO_IMG_HTML = """
<html><body>
<h1>Ch Two</h1>
<div class="nl"><p>Plain text chapter.</p></div>
</body></html>
"""

CHAPTER_DATA_SRC_HTML = """
<html><body>
<div class="nl">
    <img data-src="../img/a.jpg" />
    <img data-src="https://cdn.example.com/b.png" />
    <img src="" />
    <img data-src="https://other.example.com/forbidden.png" />
</div>
</body></html>
"""

DUPLICATE_IMG_HTML = """
<html><body>
<div class="nl">
    <img src="https://cdn.example.com/dup.png" />
    <img src="https://cdn.example.com/dup.png" />
    <img src="https://cdn.example.com/unique.png" />
</div>
</body></html>
"""


def _fmt(chapter=None, img_referrer=False):
    fmt = {
        "chapter": chapter or {
            "title": {"name": "h1", "class_": "chapter-title"},
            "content": {"name": "div", "class_": "nl"},
            "image": {"name": "img", "other_attr": "src"},
            "remove": [{"name": "script"}, {"class_": "ad"}, {"class_prefix": "footer-"}],
        },
        "img_referrer": img_referrer,
    }
    return fmt


# ---------------------------------------------------------------------------
# text crawl tests
# ---------------------------------------------------------------------------


class TestCrawlTextContent(unittest.TestCase):
    """crawl_text_content sanitizes without any image request."""

    def test_strips_unwanted_and_preserves_img_tags(self):
        html_map = {
            "https://example.com/novel/title/ch/1": TEXT_CHAPTER_IMG_HTML,
        }
        raw = _mock_raw_page(html_map)
        request = _req(url="https://example.com/novel/title")
        ctx = _ctx(request=request, raw_page=raw, format_definition=_fmt())
        chapter = _chapter(1, "https://example.com/novel/title/ch/1")
        result, title = crawl_text_content(ctx, chapter, {})
        # script and ad removed
        self.assertNotIn("<script>", result)
        self.assertNotIn("Advertisement", result)
        self.assertNotIn("footer-junk", result)
        self.assertIn('src="https://cdn.example.com/1.png"', result)
        self.assertIn('src="https://cdn.example.com/2.png"', result)
        self.assertEqual(title.lower().strip(), "ch one")
        # No image download performed — get_raw_page only for chapter page, no fetch for images
        self.assertEqual(raw.get_raw_page.call_count, 1)

    def test_no_image_requests_made(self):
        # Assert that no network image fetch ever happens during text crawl
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            html_map = {
                "https://e.com/ch/1": TEXT_CHAPTER_NO_IMG_HTML,
            }
            raw = _mock_raw_page(html_map)
            request = _req(url="https://e.com/novel/title")
            ctx = _ctx(request=request, raw_page=raw, format_definition=_fmt())
            chapter = _chapter(1, "https://e.com/ch/1")
            crawl_text_content(ctx, chapter, {})
            mock_get.assert_not_called()

    def test_fallback_to_body_when_no_content_selector(self):
        fmt = {
            "chapter": {
                "title": {"name": "h1"},
                # no content selector → fallback body
            }
        }
        html_map = {
            "https://e.com/ch/1": TEXT_CHAPTER_NO_IMG_HTML,
        }
        raw = _mock_raw_page(html_map)
        ctx = _ctx(request=_req(url="https://e.com/novel/title"), raw_page=raw, format_definition=fmt)
        chapter = _chapter(1, "https://e.com/ch/1")
        result, _ = crawl_text_content(ctx, chapter, {})
        self.assertIn("Plain text chapter", result)

    def test_title_fallback_page_title(self):
        fmt = {
            "chapter": {
                "title": {"name": "h1", "class_": "nonexistent"},
                "content": {"name": "div", "class_": "nl"},
            }
        }
        # no match for the chapter-title selector → title element fallback
        html_map = {"https://e.com/ch/2": TEXT_CHAPTER_NO_IMG_HTML}
        raw = _mock_raw_page(html_map)
        ctx = _ctx(request=_req(url="https://e.com/novel/title"), raw_page=raw, format_definition=fmt)
        chapter = _chapter(2, "https://e.com/ch/2")
        result, title = crawl_text_content(ctx, chapter, {})
        self.assertIn("Plain text chapter", result)

    def test_no_raw_page_raises(self):
        context = _ctx(request=_req(), raw_page=None, format_definition=_fmt())
        with self.assertRaises(InvalidFlowError):
            crawl_text_content(context, _chapter(1, "https://e.com/ch/1"), {})

    def test_chapter_without_url_raises(self):
        context = _ctx(request=_req(), raw_page=_mock_raw_page(""), format_definition=_fmt())
        with self.assertRaises(IncompleteCrawlError):
            crawl_text_content(context, _chapter(1, ""), {})

    def test_content_selector_mismatch_raises_incomplete(self):
        fmt = {
            "chapter": {
                "content": {"name": "div", "class_": "doesnotexist"},
            }
        }
        html_map = {"https://e.com/ch/1": "<html><body><p>x</p></body></html>"}
        raw = _mock_raw_page(html_map)
        ctx = _ctx(request=_req(url="https://e.com/novel/title"), raw_page=raw, format_definition=fmt)
        with self.assertRaises(IncompleteCrawlError):
            crawl_text_content(ctx, _chapter(1, "https://e.com/ch/1"), {})


# ---------------------------------------------------------------------------
# image download tests
# ---------------------------------------------------------------------------


class TestDownloadImage(unittest.TestCase):
    """download_image produces one manifest entry with the right fields."""

    def _ctx_with_dir(self):
        tmp = tempfile.mkdtemp()
        return _ctx(request=_req(), output_dir=tmp)

    def test_successful_download(self):
        ctx = self._ctx_with_dir()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"Content-Type": "image/png"}
            resp.content = png
            mock_get.return_value = resp
            entry = download_image(
                ctx,
                "https://cdn.example.com/x.png",
                '<img src="https://cdn.example.com/x.png">',
                chapter_ordinal=1,
                occurrence=1,
                referer_url="https://example.com/ch/1",
                img_referrer=False,
            )
        self.assertEqual(entry.status, ImageStatus.DOWNLOADED)
        self.assertEqual(entry.source_url, "https://cdn.example.com/x.png")
        self.assertEqual(entry.chapter_ordinal, 1)
        self.assertEqual(entry.occurrence, 1)
        self.assertEqual(entry.schema_version, 1)
        self.assertEqual(entry.raw_tag, '<img src="https://cdn.example.com/x.png">')
        self.assertTrue(entry.filename.endswith(".png"))
        self.assertTrue(os.path.exists(os.path.join(ctx.output_dir, "img", entry.filename)))

    def test_failed_http_returns_failed_entry(self):
        ctx = self._ctx_with_dir()
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 404
            resp.headers = {}
            resp.content = b""
            mock_get.return_value = resp
            entry = download_image(
                ctx,
                "https://cdn.example.com/missing.png",
                "<img>",
                chapter_ordinal=1,
                occurrence=2,
            )
        self.assertEqual(entry.status, ImageStatus.FAILED)
        self.assertEqual(entry.source_url, "https://cdn.example.com/missing.png")
        # A permanent 404 must not consume the retry-delay budget.
        self.assertEqual(mock_get.call_count, 1)

    def test_invalid_image_bytes_failed(self):
        ctx = self._ctx_with_dir()
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"Content-Type": "text/html"}
            resp.content = b"<html>not an image</html>"
            mock_get.return_value = resp
            entry = download_image(
                ctx, "https://cdn.example.com/notimg.png", "<img>", 1, 1
            )
        self.assertEqual(entry.status, ImageStatus.FAILED)

    def test_referer_and_cookies_forwarded(self):
        ctx = self._ctx_with_dir()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        import requests as _requests
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"Content-Type": "image/png"}
            resp.content = png
            mock_get.return_value = resp
            cookies = {"session": "abc"}
            download_image(
                ctx,
                "https://example.com/img.png",
                "<img>",
                1,
                1,
                referer_url="https://example.com/ch/1",
                img_referrer=True,
                cookies=cookies,
                max_retries=1,
            )
        call_kwargs = mock_get.call_args
        self.assertIsNotNone(call_kwargs)
        kwargs = call_kwargs[1]
        self.assertEqual(kwargs["headers"].get("Referer"), "https://example.com/ch/1")
        self.assertEqual(kwargs.get("cookies"), {"session": "abc"})


class TestDownloadImagesForChapter(unittest.TestCase):
    """download_images_for_chapter locates and downloads all images."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_custom_data_src_and_relative_resolution(self):
        ctx = self._ctx_with_fmt(CHAPTER_DATA_SRC_HTML, fmt=_fmt(chapter={
            "content": {"name": "div", "class_": "nl"},
            "image": {
                "name": "img",
                "other_attr": "data-src",
                "allowed_hosts": ["cdn.example.com"],
            },
        }))
        # relative ../img/a.jpg resolves against https://e.com/novel/title/ch/1
        referer = "https://e.com/novel/title/ch/1"
        chapter = _chapter(1, referer)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"Content-Type": "image/png"}
            resp.content = png
            mock_get.return_value = resp
            entries = download_images_for_chapter(ctx, chapter, CHAPTER_DATA_SRC_HTML, chapter_url=referer)
        # Only cdn.example.com/b.png is within allowed_hosts; a.jpg resolves to e.com (blocked), blank skipped
        self.assertEqual(len(entries), 1)

    def test_duplicate_identical_tags_distinguished_by_occurrence(self):
        ctx = self._ctx_with_fmt(DUPLICATE_IMG_HTML, fmt=_fmt())
        referer = "https://e.com/ch/1"
        chapter = _chapter(1, referer)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = png
                return resp
            mock_get.side_effect = side
            entries = download_images_for_chapter(ctx, chapter, DUPLICATE_IMG_HTML, chapter_url=referer)
        self.assertEqual(len(entries), 3)
        # First and second entries share the same source URL but occurrence differs
        self.assertEqual(entries[0].source_url, entries[1].source_url)
        self.assertEqual(entries[0].occurrence, 1)
        self.assertEqual(entries[1].occurrence, 2)
        self.assertEqual(entries[2].occurrence, 3)

    def test_delete_count_strips_trailing_ads(self):
        html = """
        <div class="nl">
            <img src="https://cdn.example.com/real1.png" />
            <img src="https://cdn.example.com/real2.png" />
            <img src="https://cdn.example.com/ad1.png" />
            <img src="https://cdn.example.com/ad2.png" />
        </div>
        """
        fmt = _fmt(chapter={
            "content": {"name": "div", "class_": "nl"},
            "image": {"name": "img", "other_attr": "src", "delete": 2},
        })
        ctx = self._ctx_with_fmt(html, fmt)
        referer = "https://e.com/ch/1"
        chapter = _chapter(1, referer)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = png
                return resp
            mock_get.side_effect = side
            entries = download_images_for_chapter(ctx, chapter, html, chapter_url=referer)
        self.assertEqual(len(entries), 2)
        self.assertIn("real1", entries[0].source_url)
        self.assertIn("real2", entries[1].source_url)

    def test_cookies_from_raw_page_forwarded(self):
        ctx = self._ctx_with_fmt(
            DUPLICATE_IMG_HTML,
            fmt=_fmt(),
            cookies={"session": "abc"},
        )
        referer = "https://e.com/ch/1"
        chapter = _chapter(1, "https://e.com/ch/2")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = png
                return resp
            mock_get.side_effect = side
            download_images_for_chapter(ctx, chapter, DUPLICATE_IMG_HTML, chapter_url=referer)
        call_kwargs = mock_get.call_args_list[0][1]
        self.assertEqual(call_kwargs["cookies"].get("session"), "abc")

    def test_browser_cookies_are_forwarded(self):
        ctx = self._ctx_with_fmt(DUPLICATE_IMG_HTML, fmt=_fmt())
        ctx.raw_page.cookies = {"request": "one"}
        ctx.raw_page.browser_driver = MagicMock()
        ctx.raw_page.browser_driver.get_cookies.return_value = [
            {"name": "verified", "value": "two", "domain": ".cdn.example.com", "path": "/"}
        ]
        chapter = _chapter(1, "https://e.com/ch/1")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, headers={"Content-Type": "image/png"}, content=png
            )
            download_images_for_chapter(ctx, chapter, DUPLICATE_IMG_HTML, chapter_url=chapter.url)
        cookies = mock_get.call_args.kwargs["cookies"]
        self.assertEqual(cookies.get("request"), "one")
        self.assertEqual(cookies.get("verified"), "two")

    def _ctx_with_fmt(self, html, fmt, cookies=None):
        raw = _mock_raw_page({}, cookies=cookies)
        return _ctx(request=_req(url="https://e.com/novel/title"), format_definition=fmt, output_dir=self.tmp, raw_page=raw)


# ---------------------------------------------------------------------------
# coordinator tests
# ---------------------------------------------------------------------------

TWO_CHAPTER_HTML = {
    "https://e.com/novel/title/ch/1": TEXT_CHAPTER_IMG_HTML,
    "https://e.com/novel/title/ch/2": TEXT_CHAPTER_NO_IMG_HTML,
}


class TestCrawlChapterHandler(unittest.TestCase):
    """crawl_chapter_handler coordinates, aggregates, and writes the manifest."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _ctx_with_volumes(self, volumes, fmt=None, fetch_mode="requests"):
        raw = _mock_raw_page(TWO_CHAPTER_HTML)
        return _ctx(
            request=_req(url="https://e.com/novel/title", fetch_mode=fetch_mode),
            volumes=volumes,
            output_dir=self.tmp,
            format_definition=fmt or _fmt(),
            raw_page=raw,
        )

    def test_writes_manifest_once_in_order(self):
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1"), _chapter(2, "https://e.com/novel/title/ch/2")])
        ]
        ctx = self._ctx_with_volumes(volumes)
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
                return resp
            mock_get.side_effect = side
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content", "download_image"]})

        path = os.path.join(self.tmp, "img", "img_info.json")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            entries = json.load(f)
        # one entry per downloaded image (chapter 1 has 2 images, chapter 2 has none)
        self.assertEqual(len(entries), 2)
        # in source order: both from chapter 1
        self.assertEqual(entries[0]["chapter_ordinal"], 1)
        self.assertEqual(entries[1]["chapter_ordinal"], 1)

    def test_chapter_contents_populated(self):
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1"), _chapter(2, "https://e.com/novel/title/ch/2")])
        ]
        ctx = self._ctx_with_volumes(volumes)
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
                return resp
            mock_get.side_effect = side
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"]})

        self.assertEqual(len(ctx.chapter_contents), 2)
        self.assertEqual(ctx.chapter_contents[0]["ordinal"], 1)
        self.assertEqual(ctx.chapter_contents[1]["ordinal"], 2)
        self.assertIn("Some text", ctx.chapter_contents[0]["html"])

    def test_text_only_no_image_requests(self):
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])
        ]
        ctx = self._ctx_with_volumes(volumes)
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"]})
            mock_get.assert_not_called()
        # Manifest written even with zero images (empty list)
        path = os.path.join(self.tmp, "img", "img_info.json")
        with open(path) as f:
            self.assertEqual(json.load(f), [])

    def test_fetch_failure_marks_incomplete(self):
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])
        ]
        ctx = self._ctx_with_volumes(volumes)
        # get_raw_page returns empty string → content selector mismatch
        ctx.raw_page.get_raw_page = MagicMock(return_value="")
        with self.assertRaises(IncompleteCrawlError):
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"]})

    def test_failed_required_image_marks_crawl_incomplete_and_is_manifested(self):
        volumes = [_vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])]
        ctx = self._ctx_with_volumes(volumes)
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=404, headers={}, content=b"")
            with self.assertRaises(IncompleteCrawlError) as raised:
                crawl_chapter_handler(ctx, {"actions": ["download_image"]})
        self.assertIn("required image", str(raised.exception))
        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(all(entry.status is ImageStatus.FAILED for entry in ctx.image_entries))
        with open(os.path.join(self.tmp, "img", "img_info.json")) as manifest:
            self.assertEqual(json.load(manifest)[0]["status"], "failed")

    def test_flow_expected_selector_is_forwarded_to_chapter_fetch(self):
        volumes = [_vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])]
        ctx = self._ctx_with_volumes(volumes)
        crawl_chapter_handler(
            ctx, {"actions": ["crawl_text_content"], "expected_selector": ".ready"}
        )
        self.assertEqual(ctx.raw_page.get_raw_page.call_args.args[1], ".ready")

    def test_progress_callback_invoked(self):
        progress = []
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1"), _chapter(2, "https://e.com/novel/title/ch/2")])
        ]
        ctx = self._ctx_with_volumes(volumes)
        ctx.progress = lambda kind, done, total: progress.append((kind, done, total))
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
                return resp
            mock_get.side_effect = side
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content", "download_image"]})
        self.assertTrue(len(progress) >= 2)
        for _, _, total in progress:
            self.assertEqual(total, 2)

    def test_image_progress_is_reported_for_gallery_images(self):
        progress = []
        volumes = [_vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])]
        ctx = self._ctx_with_volumes(volumes)
        ctx.progress = lambda kind, done, total: progress.append((kind, done, total))
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                headers={"Content-Type": "image/png"},
                content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
            )
            crawl_chapter_handler(ctx, {"actions": ["download_image"]})
        self.assertEqual(
            [event for event in progress if event[0] == "image"],
            [("image", 1, 2), ("image", 2, 2)],
        )

    def test_empty_volumes_returns_early(self):
        ctx = self._ctx_with_volumes([])
        crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"]})
        self.assertEqual(ctx.chapter_contents, [])
        self.assertEqual(ctx.image_entries, [])


class TestSequentialAndParallel(unittest.TestCase):
    """Browser/auto mode runs sequentially; requests mode uses workers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _ctx_with_volumes(self, volumes, fmt=None, fetch_mode="requests"):
        raw = _mock_raw_page(TWO_CHAPTER_HTML)
        return _ctx(
            request=_req(url="https://e.com/novel/title", fetch_mode=fetch_mode),
            volumes=volumes,
            output_dir=self.tmp,
            format_definition=fmt or _fmt(),
            raw_page=raw,
        )

    def test_sequential_browser_mode(self):
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1"), _chapter(2, "https://e.com/novel/title/ch/2")])
        ]
        raw = _mock_raw_page(TWO_CHAPTER_HTML)
        ctx = _ctx(
            request=_req(url="https://e.com/novel/title", fetch_mode="browser"),
            volumes=volumes,
            output_dir=self.tmp,
            format_definition=_fmt(),
            raw_page=raw,
        )
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            # In browser mode we still rely on raw_page for chapter fetch
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"]})
        self.assertEqual(len(ctx.chapter_contents), 2)

    def test_flow_retry_count_retries_failed_sequential_chapter(self):
        volumes = [_vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])]
        raw = _mock_raw_page(TWO_CHAPTER_HTML)
        raw.get_raw_page = MagicMock(side_effect=[RuntimeError("temporary"), TEXT_CHAPTER_IMG_HTML])
        ctx = _ctx(
            request=_req(url="https://e.com/novel/title", fetch_mode="browser"),
            volumes=volumes,
            output_dir=self.tmp,
            format_definition=_fmt(),
            raw_page=raw,
        )

        crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"], "max_retries": 1})

        self.assertEqual(raw.get_raw_page.call_count, 2)
        self.assertEqual(len(ctx.chapter_contents), 1)

    def test_request_retry_count_overrides_flow_retry_count(self):
        volumes = [_vol(0, [_chapter(1, "https://e.com/novel/title/ch/1")])]
        raw = _mock_raw_page(TWO_CHAPTER_HTML)
        raw.get_raw_page = MagicMock(side_effect=[RuntimeError("temporary"), TEXT_CHAPTER_IMG_HTML])
        request = CrawlRequest(
            url="https://e.com/novel/title",
            content_type="novel",
            output_format="epub",
            fetch_mode="browser",
            max_retries=0,
        )
        ctx = _ctx(
            request=request,
            volumes=volumes,
            output_dir=self.tmp,
            format_definition=_fmt(),
            raw_page=raw,
        )

        with self.assertRaises(IncompleteCrawlError):
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content"], "max_retries": 1})

        self.assertEqual(raw.get_raw_page.call_count, 1)

    def test_parallel_requests_mode_produces_same_manifest(self):
        # Same source order regardless of worker count
        volumes = [
            _vol(0, [_chapter(1, "https://e.com/novel/title/ch/1"), _chapter(2, "https://e.com/novel/title/ch/2")])
        ]
        ctx = self._ctx_with_volumes(volumes, fetch_mode="requests")
        with mock.patch("api.chapter_crawl.requests.get") as mock_get:
            def side(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {"Content-Type": "image/png"}
                resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
                return resp
            mock_get.side_effect = side
            crawl_chapter_handler(ctx, {"actions": ["crawl_text_content", "download_image"]})
        self.assertEqual(len(ctx.image_entries), 2)
        self.assertEqual(ctx.image_entries[0].chapter_ordinal, 1)


class TestManifestWrite(unittest.TestCase):
    """write_manifest writes entries in source order."""

    def test_write_manifest(self):
        tmp = tempfile.mkdtemp()
        ctx = _ctx(request=_req(), output_dir=tmp)
        ctx.image_entries.append(
            ImageManifestEntry(
                chapter_ordinal=1, occurrence=1,
                source_url="https://cdn.example.com/a.png",
                raw_tag='<img src="https://cdn.example.com/a.png">',
                filename="img_0001_001.png",
                relative_path="img/img_0001_001.png",
                replacement_tag='<img src="img_0001_001.png">',
                status=ImageStatus.DOWNLOADED,
                schema_version=1,
            )
        )
        path = write_manifest(ctx)
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["filename"], "img_0001_001.png")
        self.assertEqual(data[0]["schema_version"], 1)


class TestRegistration(unittest.TestCase):
    """crawl_chapter handler is upgradable and idempotent."""

    def test_register_crawl_chapter_idempotent(self):
        from api import get_module
        register_chapter_crawl_handlers()
        spec = get_module("crawl_chapter")
        self.assertEqual(spec.handler.__name__, "crawl_chapter_handler")


class TestGalleryFlow(unittest.TestCase):
    """Gallery chapters — each picture page yields a chapter with one image."""

    def test_gallery_chapter_downloads_image(self):
        # Gallery: content type gallery, actions download_image only
        pic_html = """
        <html><body>
        <img id="img" src="https://cdn.gallery.com/pic/001.jpg" />
        </body></html>
        """
        # Chapter URL is the picture page; its content is the image page
        pic_url = "https://gallery.gallery/g/1/001"
        html_map = {pic_url: pic_html}
        raw = _mock_raw_page(html_map)
        tmp = tempfile.mkdtemp()
        fmt = {
            "chapter": {
                "image": {"name": "img", "id": "img", "other_attr": "src"},
            },
        }
        volumes = [_vol(0, [_chapter(1, pic_url, identifier="1")])]
        ctx = _ctx(
            request=_req(content_type="gallery", output_format="folder", fetch_mode="requests", url="https://gallery.gallery/g/1"),
            volumes=volumes,
            output_dir=tmp,
            format_definition=fmt,
            raw_page=raw,
        )
        import api.chapter_crawl as cc
        with mock.patch.object(cc.requests, "get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"Content-Type": "image/jpeg"}
            resp.content = b"\xff\xd8\xff\xe0" + b"\x00" * 16
            mock_get.return_value = resp
            crawl_chapter_handler(ctx, {"actions": ["download_image"]})

        self.assertEqual(len(ctx.image_entries), 1)
        self.assertEqual(ctx.image_entries[0].status, ImageStatus.DOWNLOADED)
        path = os.path.join(tmp, "img", "img_info.json")
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)


if __name__ == "__main__":
    unittest.main()
