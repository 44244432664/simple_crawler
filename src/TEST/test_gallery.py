"""Tests for the gallery crawler — configuration loading and page parsing.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_gallery.py -v
"""

import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from unittest.mock import MagicMock, call, patch

from crawler.Gallery import (
    GalleryCrawler,
    IncompleteGalleryError,
    _normalize_host,
    _normalize_url,
    run as run_gallery,
)
from utils.fetcher import FetchError

VALID_FORMAT = {
    "title": {},
    "gallery_links": {
        "container": {"name": "div", "id": "img-pic"},
        "link": {"name": "a", "other_attr": "href"},
    },
    "picture": {
        "image": {"name": "img", "other_attr": "src"},
    },
    "fetch": {
        "mode": "requests",
        "cloudflare": True,
        "challenge_timeout_seconds": 180,
        "profile_name": "",
        "headless": True,
    },
    "img_referrer": False,
}

PAGINATED_FORMAT = {
    "title": {},
    "gallery_links": {
        "container": {"name": "div", "id": "img-pic"},
        "link": {"name": "a", "other_attr": "href"},
        "pagination": {
            "container": {"name": "table", "class_": "ptt"},
            "link": {"name": "a", "other_attr": "href"},
            "page_param": "p",
        },
    },
    "picture": {
        "image": {"name": "img", "other_attr": "src"},
    },
    "fetch": {
        "mode": "requests",
        "cloudflare": True,
        "challenge_timeout_seconds": 180,
        "profile_name": "",
        "headless": True,
    },
    "img_referrer": False,
}


class FakeFetcher:
    """Captures constructor kwargs on the class and records close() calls."""

    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = 0
        FakeFetcher.calls.append(kwargs)

    def close(self):
        self.closed += 1


def _write_alias(root, rows):
    path = os.path.join(root, "aliases.csv")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("site,name,crawler_class\n")
        for site, name, crawler_class in rows:
            handle.write(f"{site},{name},{crawler_class}\n")
    return path


def _write_format(formats_dir, name, data):
    os.makedirs(formats_dir, exist_ok=True)
    path = os.path.join(formats_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


class TestUrlNormalization(unittest.TestCase):
    def test_scheme_added_when_missing(self):
        self.assertEqual(
            _normalize_url("example.com/gallery/abc"),
            "https://example.com/gallery/abc",
        )

    def test_existing_scheme_unchanged(self):
        self.assertEqual(
            _normalize_url("http://example.com/gallery"),
            "http://example.com/gallery",
        )
        self.assertEqual(
            _normalize_url("https://example.com/gallery"),
            "https://example.com/gallery",
        )

    def test_blank_becomes_empty(self):
        self.assertEqual(_normalize_url("  "), "")


class TestHostNormalization(unittest.TestCase):
    def test_www_and_trailing_slash_stripped(self):
        self.assertEqual(_normalize_host("WWW.Example.com/"), "example.com")

    def test_lowercased(self):
        self.assertEqual(_normalize_host("EXAMPLE.com"), "example.com")


class TestValidConfiguration(unittest.TestCase):
    def setUp(self):
        FakeFetcher.calls.clear()

    def test_valid_mocked_alias_and_format_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("www.mygallery.com/", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")
            _write_format(formats_dir, "mygallery", VALID_FORMAT)

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
                crawler = GalleryCrawler("mygallery.com/gallery/abc")

                self.assertEqual(crawler.gallery_url, "https://mygallery.com/gallery/abc")
                self.assertEqual(crawler.site_name, "mygallery")
                self.assertEqual(crawler.format_data, VALID_FORMAT)
                self.assertIsInstance(crawler.fetcher, FakeFetcher)

                fetcher_kwargs = FakeFetcher.calls[-1]
                self.assertEqual(fetcher_kwargs["fetch_mode"], "requests")
                self.assertTrue(fetcher_kwargs["headless"])
                self.assertEqual(fetcher_kwargs["cloudflare"], True)
                self.assertEqual(fetcher_kwargs["challenge_timeout"], 180)

    def test_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("mygallery.com", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")
            _write_format(formats_dir, "mygallery", VALID_FORMAT)

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
                crawler = GalleryCrawler("https://mygallery.com/gallery/abc")
                crawler.close()
                crawler.close()
                self.assertEqual(crawler.fetcher.closed, 2)


class TestEmptyUrl(unittest.TestCase):
    def test_empty_url_rejected_before_configuration_lookup(self):
        with patch("crawler.Gallery.ALIASES_PATH", os.path.join("does", "not", "exist")):
            with self.assertRaises(ValueError) as ctx:
                GalleryCrawler("   ")
        self.assertIn("URL", str(ctx.exception))


class TestAliasResolution(unittest.TestCase):
    def test_unknown_host_rejected_with_hostname(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("mygallery.com", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")
            _write_format(formats_dir, "mygallery", VALID_FORMAT)

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
                with self.assertRaises(ValueError) as ctx:
                    GalleryCrawler("https://othersite.com/gallery/abc")
            self.assertIn("othersite.com", str(ctx.exception))


class TestFormatLoading(unittest.TestCase):
    def test_missing_format_file_identifies_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("mygallery.com", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
                with self.assertRaises(ValueError) as ctx:
                    GalleryCrawler("https://mygallery.com/gallery/abc")
            self.assertIn(
                os.path.join(formats_dir, "mygallery.json"), str(ctx.exception)
            )

    def test_malformed_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("mygallery.com", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")
            os.makedirs(formats_dir, exist_ok=True)
            with open(os.path.join(formats_dir, "mygallery.json"), "w") as handle:
                handle.write("{not valid json")

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
                with self.assertRaises(ValueError) as ctx:
                    GalleryCrawler("https://mygallery.com/gallery/abc")
            self.assertIn("not valid JSON", str(ctx.exception))


class TestFormatValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.aliases_path = _write_alias(
            self.tmp.name, [("mygallery.com", "mygallery", "GalleryRequests")]
        )
        self.formats_dir = os.path.join(self.tmp.name, "formats")

    def _make_crawler(self, format_data):
        _write_format(self.formats_dir, "mygallery", format_data)
        with patch("crawler.Gallery.ALIASES_PATH", self.aliases_path), patch(
            "crawler.Gallery.FORMATS_DIR", self.formats_dir
        ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
            return GalleryCrawler("https://mygallery.com/gallery/abc")

    def _assert_rejected(self, format_data, fragment):
        with self.assertRaises(ValueError) as ctx:
            self._make_crawler(format_data)
        self.assertIn(fragment, str(ctx.exception))

    def test_missing_gallery_links_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        del format_data["gallery_links"]
        self._assert_rejected(format_data, "gallery_links")

    def test_missing_container_selector_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["gallery_links"]["container"] = {"name": "", "id": ""}
        self._assert_rejected(format_data, "gallery_links.container")

    def test_missing_link_attribute_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["gallery_links"]["link"] = {"name": "a", "other_attr": ""}
        self._assert_rejected(format_data, "gallery_links.link.other_attr")

    def test_missing_picture_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        del format_data["picture"]
        self._assert_rejected(format_data, "picture")

    def test_missing_image_selector_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["picture"]["image"] = {"name": "", "other_attr": "src"}
        self._assert_rejected(format_data, "picture.image")

    def test_missing_image_attribute_rejected(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["picture"]["image"] = {"name": "img", "other_attr": ""}
        self._assert_rejected(format_data, "picture.image.other_attr")

    def test_invalid_config_fails_before_output_directory_creation(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["gallery_links"]["container"] = {}
        _write_format(self.formats_dir, "mygallery", format_data)
        output_dir = os.path.join(self.tmp.name, "should-not-exist")

        with patch("crawler.Gallery.ALIASES_PATH", self.aliases_path), patch(
            "crawler.Gallery.FORMATS_DIR", self.formats_dir
        ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
            with self.assertRaises(ValueError):
                GalleryCrawler(
                    "https://mygallery.com/gallery/abc", output_dir=output_dir
                )
        self.assertFalse(os.path.exists(output_dir))


class TestFetchResolution(unittest.TestCase):
    def setUp(self):
        FakeFetcher.calls.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.aliases_path = _write_alias(
            self.tmp.name, [("mygallery.com", "mygallery", "GalleryRequests")]
        )
        self.formats_dir = os.path.join(self.tmp.name, "formats")
        _write_format(self.formats_dir, "mygallery", VALID_FORMAT)

    def _make_crawler(self, **kwargs):
        with patch("crawler.Gallery.ALIASES_PATH", self.aliases_path), patch(
            "crawler.Gallery.FORMATS_DIR", self.formats_dir
        ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
            crawler = GalleryCrawler(
                "https://mygallery.com/gallery/abc", **kwargs
            )
        return crawler

    def test_explicit_fetch_mode_precedes_json(self):
        crawler = self._make_crawler(fetch_mode="browser")
        self.assertEqual(crawler.fetch_mode, "browser")
        self.assertEqual(FakeFetcher.calls[-1]["fetch_mode"], "browser")

    def test_json_fetch_mode_used_when_explicit_absent(self):
        crawler = self._make_crawler()
        self.assertEqual(crawler.fetch_mode, "requests")
        self.assertEqual(FakeFetcher.calls[-1]["fetch_mode"], "requests")

    def test_explicit_headless_precedes_json(self):
        crawler = self._make_crawler(headless=False)
        self.assertFalse(crawler.headless)
        self.assertFalse(FakeFetcher.calls[-1]["headless"])

    def test_json_fetch_settings_used_when_explicit_absent(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["fetch"]["mode"] = "auto"
        format_data["fetch"]["headless"] = False
        _write_format(self.formats_dir, "mygallery", format_data)

        crawler = self._make_crawler()
        self.assertEqual(crawler.fetch_mode, "auto")
        self.assertFalse(crawler.headless)
        self.assertEqual(FakeFetcher.calls[-1]["fetch_mode"], "auto")
        self.assertFalse(FakeFetcher.calls[-1]["headless"])

    def test_defaults_to_requests_and_headless_when_fetch_absent(self):
        format_data = deepcopy(VALID_FORMAT)
        del format_data["fetch"]
        _write_format(self.formats_dir, "mygallery", format_data)

        crawler = self._make_crawler()
        self.assertEqual(crawler.fetch_mode, "requests")
        self.assertTrue(crawler.headless)
        self.assertTrue(FakeFetcher.calls[-1]["cloudflare"])


class TestUnsupportedFetchMode(unittest.TestCase):
    def test_rejected_by_shared_fetcher(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["fetch"]["mode"] = "invalid"

        with tempfile.TemporaryDirectory() as tmp:
            aliases_path = _write_alias(
                tmp, [("mygallery.com", "mygallery", "GalleryRequests")]
            )
            formats_dir = os.path.join(tmp, "formats")
            _write_format(formats_dir, "mygallery", format_data)

            with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
                "crawler.Gallery.FORMATS_DIR", formats_dir
            ):
                with self.assertRaises(ValueError):
                    GalleryCrawler("https://mygallery.com/gallery/abc")


GALLERY_URL = "https://example.com/gallery/index"

GALLERY_HTML = """
<html><body>
  <nav><a href="https://example.com/home">Home</a></nav>
  <div id="img-pic">
    <a href="page1.html"><img src="thumb1.jpg"></a>
    <a href="page2.html#frag"><img src="thumb2.jpg"></a>
    <a href="/gallery/page3"><img src="thumb3.jpg"></a>
    <a href="//cdn.example.net/page4.html"><img src="thumb4.jpg"></a>
    <a href="https://absolute.example.net/page5.html"><img src="thumb5.jpg"></a>
    <a href="page2.html"><img src="thumb6.jpg"></a>
    <a href="/gallery/page1.html"><img src="thumb7.jpg"></a>
    <a href=""><img src="thumb8.jpg"></a>
  </div>
</body></html>
"""

PICTURE_HTML = """
<html><body>
  <nav><img src="logo.png"></nav>
  <div class="content"><img src="full.jpg"></div>
</body></html>
"""

EXPECTED_PICTURE_URLS = [
    "https://example.com/gallery/page1.html",
    "https://example.com/gallery/page2.html",
    "https://example.com/gallery/page3",
    "https://cdn.example.net/page4.html",
    "https://absolute.example.net/page5.html",
]

THREE_PAGE_HTML = """
<div id="img-pic">
  <a href="p1.html">one</a>
  <a href="p2.html">two</a>
  <a href="p3.html">three</a>
</div>
"""

TWO_PAGE_HTML = """
<div id="img-pic">
  <a href="p1.html">one</a>
  <a href="p2.html">two</a>
</div>
"""

SINGLE_PAGE_HTML = """
<div id="img-pic">
  <a href="p1.html">only</a>
</div>
"""

PICTURE_WITH = '<div class="content"><img src="{src}"></div>'

PAGE1_HTML = """
<div id="img-pic">
  <a href="p1.html">one</a>
  <a href="p2.html">two</a>
</div>
<table class="ptt"><tr>
  <td class="ptdd">&lt;</td>
  <td class="ptds"><a href="https://example.com/gallery/index">1</a></td>
  <td><a href="https://example.com/gallery/index?p=1">2</a></td>
  <td><a href="https://example.com/gallery/index?p=2">3</a></td>
  <td><a href="https://example.com/gallery/index?p=1">&gt;</a></td>
</tr></table>
"""

PAGE2_HTML = """
<div id="img-pic">
  <a href="p3.html">three</a>
  <a href="p4.html">four</a>
</div>
<table class="ptt"><tr>
  <td><a href="https://example.com/gallery/index">&lt;</a></td>
  <td><a href="https://example.com/gallery/index">1</a></td>
  <td><a href="https://example.com/gallery/index?p=1">2</a></td>
  <td><a href="https://example.com/gallery/index?p=2">3</a></td>
  <td><a href="https://example.com/gallery/index?p=2">&gt;</a></td>
</tr></table>
"""

PAGE3_HTML = """
<div id="img-pic">
  <a href="p5.html">five</a>
  <a href="p6.html">six</a>
</div>
<table class="ptt"><tr>
  <td><a href="https://example.com/gallery/index?p=1">&lt;</a></td>
  <td><a href="https://example.com/gallery/index">1</a></td>
  <td><a href="https://example.com/gallery/index?p=1">2</a></td>
  <td><a href="https://example.com/gallery/index?p=2">3</a></td>
  <td class="ptdd">&gt;</td>
</tr></table>
"""


def _write_download(
    img_url,
    output_dir,
    ext="jpg",
    name=None,
    img_referrer=False,
    update_log=None,
    referer_url=None,
    cookies=None,
    browser_driver=None,
    preserve_ext=False,
    require_valid_image=False,
    selenium_fallback=False,
    max_retries=5,
    request_timeout_seconds=20,
    retry_delay_seconds=2,
):
    """Stand-in for ``download_image`` that really writes a file.

    Mirrors the real helper: with ``preserve_ext`` the extension is taken
    from the image URL instead of the fixed *ext*.
    """
    if preserve_ext:
        url_ext = os.path.splitext(img_url.split("?")[0])[1].lstrip(".").lower()
        if url_ext in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
            ext = "jpg" if url_ext == "jpeg" else url_ext
    path = os.path.join(output_dir, f"{name}.{ext}")
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(img_url.encode("utf-8"))
    return path


def _make_parsing_crawler(format_data=None, gallery_url=GALLERY_URL):
    """Build a GalleryCrawler with patched config paths and fetcher."""
    with tempfile.TemporaryDirectory() as tmp:
        aliases_path = _write_alias(
            tmp, [("example.com", "examplegallery", "GalleryRequests")]
        )
        formats_dir = os.path.join(tmp, "formats")
        _write_format(formats_dir, "examplegallery", format_data or VALID_FORMAT)
        with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
            "crawler.Gallery.FORMATS_DIR", formats_dir
        ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
            return GalleryCrawler(gallery_url)


class TestTitleExtraction(unittest.TestCase):
    def test_extracts_sanitized_title_from_selector(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["title"] = {"name": "h1", "class_": "gallery-title"}
        crawler = _make_parsing_crawler(format_data)

        title = crawler.extract_title(
            '<h1 class="gallery-title">  My  Awesome   Gallery </h1>'
        )
        self.assertEqual(title, "My Awesome Gallery")

    def test_unsafe_title_characters_are_removed(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["title"] = {"name": "h1"}
        crawler = _make_parsing_crawler(format_data)

        title = crawler.extract_title('<h1>a/b\\c:d*e?f"g<h>i|j</h1>')
        self.assertNotIn("/", title)
        self.assertNotIn("\\", title)
        self.assertNotIn(":", title)
        self.assertNotIn("*", title)
        self.assertNotIn("?", title)
        self.assertNotIn('"', title)
        self.assertNotIn("<", title)
        self.assertNotIn(">", title)
        self.assertNotIn("|", title)

    def test_leading_and_trailing_dots_trimmed(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["title"] = {"name": "h1"}
        crawler = _make_parsing_crawler(format_data)

        title = crawler.extract_title("<h1>...Hello...</h1>")
        self.assertEqual(title, "Hello")

    def test_missing_title_uses_url_slug(self):
        crawler = _make_parsing_crawler(
            gallery_url="https://example.com/gallery/My%20Gallery"
        )
        self.assertEqual(crawler.extract_title(GALLERY_HTML), "My Gallery")

    def test_missing_title_and_empty_slug_falls_back_to_gallery(self):
        crawler = _make_parsing_crawler(gallery_url="https://example.com/")
        self.assertEqual(crawler.extract_title(GALLERY_HTML), "gallery")

    def test_selector_present_but_unmatched_falls_back_to_slug(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["title"] = {"name": "h2"}
        crawler = _make_parsing_crawler(
            format_data, gallery_url="https://example.com/gallery/abc"
        )
        self.assertEqual(crawler.extract_title(GALLERY_HTML), "abc")


class TestParseGallery(unittest.TestCase):
    def setUp(self):
        self.crawler = _make_parsing_crawler()

    def test_dom_order_and_deduplication(self):
        self.assertEqual(self.crawler.parse_gallery(GALLERY_HTML), EXPECTED_PICTURE_URLS)

    def test_unrelated_links_outside_container_excluded(self):
        picture_urls = self.crawler.parse_gallery(GALLERY_HTML)
        self.assertNotIn("https://example.com/home", picture_urls)

    def test_query_parameters_preserved_and_fragments_removed(self):
        html = (
            '<div id="img-pic">'
            '<a href="page1.html?view=full">one</a>'
            '<a href="page1.html#top">one again</a>'
            "</div>"
        )
        picture_urls = self.crawler.parse_gallery(html)
        self.assertEqual(
            picture_urls,
            [
                "https://example.com/gallery/page1.html?view=full",
                "https://example.com/gallery/page1.html",
            ],
        )

    def test_placeholder_and_non_http_links_are_ignored(self):
        html = (
            '<div id="img-pic">'
            '<a href="#">placeholder</a>'
            '<a href="javascript:void(0)">script</a>'
            '<a href="mailto:test@example.com">mail</a>'
            '<a href="/gallery/page1.html">picture</a>'
            "</div>"
        )
        self.assertEqual(
            self.crawler.parse_gallery(html),
            ["https://example.com/gallery/page1.html"],
        )

    def test_container_missing_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.crawler.parse_gallery("<html><body><p>none</p></body></html>")
        self.assertIn("container", str(ctx.exception))

    def test_container_empty_raises(self):
        with self.assertRaises(ValueError):
            self.crawler.parse_gallery('<div id="img-pic"></div>')

    def test_links_without_usable_href_raise(self):
        with self.assertRaises(ValueError):
            self.crawler.parse_gallery(
                '<div id="img-pic"><a>no href</a><a href=""></a></div>'
            )


class TestPagination(unittest.TestCase):
    def setUp(self):
        self.crawler = _make_parsing_crawler(format_data=PAGINATED_FORMAT)

    def test_no_pagination_config_returns_empty(self):
        crawler = _make_parsing_crawler()
        self.assertEqual(crawler._pagination_page_urls(PAGE1_HTML), [])

    def test_missing_nav_container_returns_empty(self):
        self.assertEqual(
            self.crawler._pagination_page_urls("<div id='img-pic'></div>"), []
        )

    def test_returns_ordered_deduplicated_page_urls(self):
        self.assertEqual(
            self.crawler._pagination_page_urls(PAGE1_HTML),
            [
                "https://example.com/gallery/index?p=1",
                "https://example.com/gallery/index?p=2",
            ],
        )

    def test_current_gallery_url_excluded(self):
        page_urls = self.crawler._pagination_page_urls(PAGE1_HTML)
        self.assertNotIn("https://example.com/gallery/index", page_urls)

    def test_page_number(self):
        self.assertEqual(
            self.crawler._page_number("https://example.com/gallery/index?p=2"), 2
        )
        self.assertEqual(
            self.crawler._page_number("https://example.com/gallery/index"), 0
        )
        self.assertEqual(
            self.crawler._page_number("https://example.com/gallery/index?page=1"), 0
        )

    def test_parse_gallery_resolves_relative_links_against_base(self):
        picture_urls = self.crawler.parse_gallery(
            PAGE2_HTML, base_url="https://example.com/gallery/index?p=1"
        )
        self.assertEqual(
            picture_urls,
            [
                "https://example.com/gallery/p3.html",
                "https://example.com/gallery/p4.html",
            ],
        )


class TestParsePicture(unittest.TestCase):
    def setUp(self):
        self.crawler = _make_parsing_crawler()

    def test_selects_first_configured_image(self):
        html = (
            '<div class="content"><img src="full.jpg"></div>'
            '<img src="trailer.jpg">'
        )
        image_url = self.crawler.parse_picture(
            html, "https://example.com/gallery/pages/page1.html"
        )
        self.assertEqual(
            image_url, "https://example.com/gallery/pages/full.jpg"
        )

    def test_resolves_root_relative_image_url(self):
        image_url = self.crawler.parse_picture(
            '<div class="content"><img src="/full.jpg"></div>',
            "https://example.com/gallery/pages/page1.html",
        )
        self.assertEqual(image_url, "https://example.com/full.jpg")

    def test_resolves_protocol_relative_image_url(self):
        image_url = self.crawler.parse_picture(
            '<div class="content"><img src="//cdn.example.net/full.jpg"></div>',
            "https://example.com/gallery/pages/page1.html",
        )
        self.assertEqual(image_url, "https://cdn.example.net/full.jpg")

    def test_keeps_absolute_image_url(self):
        image_url = self.crawler.parse_picture(
            '<div class="content"><img src="https://abs.example.net/full.jpg"></div>',
            "https://example.com/gallery/pages/page1.html",
        )
        self.assertEqual(image_url, "https://abs.example.net/full.jpg")

    def test_picture_without_image_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.crawler.parse_picture(
                "<html><body><p>broken</p></body></html>",
                "https://example.com/gallery/pages/page1.html",
            )
        self.assertIn("https://example.com/gallery/pages/page1.html", str(ctx.exception))

    def test_image_without_src_attribute_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.crawler.parse_picture(
                '<div class="content"><img></div>',
                "https://example.com/gallery/pages/page1.html",
            )
        self.assertIn("src", str(ctx.exception))

    def test_non_http_image_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.crawler.parse_picture(
                '<img src="data:image/png;base64,AAAA">',
                "https://example.com/gallery/pages/page1.html",
            )
        self.assertIn("not HTTP(S)", str(ctx.exception))


class TestPictureImageUrls(unittest.TestCase):
    def test_returns_ordered_image_urls(self):
        crawler = _make_parsing_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                '<div class="content"><img src="a.jpg"></div>',
                '<div class="content"><img src="b.jpg"></div>',
            ]
        )

        image_urls = crawler.picture_image_urls(
            ["https://example.com/gallery/a.html", "https://example.com/gallery/b.html"]
        )
        self.assertEqual(
            image_urls,
            [
                "https://example.com/gallery/a.jpg",
                "https://example.com/gallery/b.jpg",
            ],
        )
        crawler.fetcher.fetch.assert_has_calls(
            [
                call("https://example.com/gallery/a.html"),
                call("https://example.com/gallery/b.html"),
            ]
        )

    def test_preserves_duplicate_image_urls_across_pages(self):
        crawler = _make_parsing_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                '<div class="content"><img src="same.jpg"></div>',
                '<div class="content"><img src="/gallery/same.jpg"></div>',
            ]
        )
        image_urls = crawler.picture_image_urls(
            ["https://example.com/gallery/a.html", "https://example.com/gallery/b.html"]
        )
        self.assertEqual(image_urls, ["https://example.com/gallery/same.jpg"] * 2)

    def test_shared_fetcher_error_propagates(self):
        crawler = _make_parsing_crawler()
        crawler.fetcher.fetch = MagicMock(side_effect=FetchError("boom"))

        with self.assertRaises(FetchError):
            crawler.picture_image_urls(["https://example.com/gallery/a.html"])

    def test_picture_parse_error_raises(self):
        crawler = _make_parsing_crawler()
        crawler.fetcher.fetch = MagicMock(return_value="<html><body>no image</body></html>")

        with self.assertRaises(ValueError):
            crawler.picture_image_urls(["https://example.com/gallery/a.html"])


class TestCrawl(unittest.TestCase):
    _UNSET = object()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = os.path.join(self.tmp.name, "out")
        self.downloader = patch(
            "crawler.Gallery.download_image", side_effect=_write_download
        )
        self.downloader.start()
        self.addCleanup(self.downloader.stop)

    def _make_crawler(self, output_dir=_UNSET, format_data=None, gallery_url=GALLERY_URL):
        if output_dir is self._UNSET:
            output_dir = self.output_dir
        aliases_path = _write_alias(
            self.tmp.name, [("example.com", "examplegallery", "GalleryRequests")]
        )
        formats_dir = os.path.join(self.tmp.name, "formats")
        _write_format(formats_dir, "examplegallery", format_data or VALID_FORMAT)
        with patch("crawler.Gallery.ALIASES_PATH", aliases_path), patch(
            "crawler.Gallery.FORMATS_DIR", formats_dir
        ), patch("crawler.Gallery.PageFetcher", FakeFetcher):
            return GalleryCrawler(gallery_url, output_dir=output_dir)

    @staticmethod
    def _picture_htmls(*sources):
        return [PICTURE_WITH.format(src=src) for src in sources]

    @staticmethod
    def _three_page_fetch(*sources):
        return MagicMock(
            side_effect=[THREE_PAGE_HTML] + TestCrawl._picture_htmls(*sources)
        )

    def test_downloads_all_pages_in_order(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl()

        expected_dir = os.path.join(self.output_dir, "index")
        self.assertEqual(result["output_dir"], expected_dir)
        self.assertEqual(
            result["downloaded_files"],
            [
                os.path.join(expected_dir, "0001.jpg"),
                os.path.join(expected_dir, "0002.jpg"),
                os.path.join(expected_dir, "0003.jpg"),
            ],
        )
        self.assertEqual(
            result["image_urls"],
            [
                "https://example.com/gallery/i1.jpg",
                "https://example.com/gallery/i2.jpg",
                "https://example.com/gallery/i3.jpg",
            ],
        )
        for path in result["downloaded_files"]:
            self.assertTrue(os.path.isfile(path))

    @patch("crawler.Gallery.tqdm.tqdm")
    def test_download_progress_tracks_each_picture_page(self, progress_factory):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")
        progress = progress_factory.return_value

        crawler.crawl(make_cbz=False)

        progress_factory.assert_called_once_with(
            total=3,
            desc="Downloading gallery images",
            unit="image",
        )
        self.assertEqual(progress.update.call_count, 3)
        progress.close.assert_called_once()

    def test_result_contract_keys(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl()

        self.assertEqual(
            set(result),
            {
                "title",
                "gallery_url",
                "output_dir",
                "picture_urls",
                "image_urls",
                "downloaded_files",
                "cbz_path",
            },
        )
        self.assertEqual(result["gallery_url"], GALLERY_URL)
        self.assertEqual(result["title"], "index")
        self.assertEqual(
            result["cbz_path"], os.path.join(self.output_dir, "index", "index.cbz")
        )
        self.assertEqual(
            result["picture_urls"],
            [
                "https://example.com/gallery/p1.html",
                "https://example.com/gallery/p2.html",
                "https://example.com/gallery/p3.html",
            ],
        )

    def test_default_output_root_used_when_output_dir_omitted(self):
        default_root = os.path.join(self.tmp.name, "outputs", "Gallery")
        crawler = self._make_crawler(output_dir=None)
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        with patch("crawler.Gallery.DEFAULT_OUTPUT_ROOT", default_root):
            result = crawler.crawl()

        expected_dir = os.path.join(default_root, "index")
        self.assertEqual(result["output_dir"], expected_dir)
        self.assertTrue(os.path.isfile(os.path.join(expected_dir, "0001.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(expected_dir, "0003.jpg")))

    def test_single_image_gallery(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                SINGLE_PAGE_HTML,
                PICTURE_WITH.format(src="only.jpg"),
            ]
        )

        result = crawler.crawl()

        self.assertEqual(
            result["downloaded_files"],
            [os.path.join(self.output_dir, "index", "0001.jpg")],
        )
        self.assertTrue(os.path.isfile(result["downloaded_files"][0]))

    def test_pagination_crawls_all_pages_in_order(self):
        crawler = self._make_crawler(format_data=PAGINATED_FORMAT)
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                PAGE1_HTML,
                PAGE2_HTML,
                PAGE3_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                PICTURE_WITH.format(src="i2.jpg"),
                PICTURE_WITH.format(src="i3.jpg"),
                PICTURE_WITH.format(src="i4.jpg"),
                PICTURE_WITH.format(src="i5.jpg"),
                PICTURE_WITH.format(src="i6.jpg"),
            ]
        )

        result = crawler.crawl()

        expected_dir = os.path.join(self.output_dir, "index")
        self.assertEqual(
            result["picture_urls"],
            [
                "https://example.com/gallery/p1.html",
                "https://example.com/gallery/p2.html",
                "https://example.com/gallery/p3.html",
                "https://example.com/gallery/p4.html",
                "https://example.com/gallery/p5.html",
                "https://example.com/gallery/p6.html",
            ],
        )
        self.assertEqual(
            result["image_urls"],
            [
                "https://example.com/gallery/i1.jpg",
                "https://example.com/gallery/i2.jpg",
                "https://example.com/gallery/i3.jpg",
                "https://example.com/gallery/i4.jpg",
                "https://example.com/gallery/i5.jpg",
                "https://example.com/gallery/i6.jpg",
            ],
        )
        self.assertEqual(
            result["downloaded_files"],
            [
                os.path.join(expected_dir, "0001.jpg"),
                os.path.join(expected_dir, "0002.jpg"),
                os.path.join(expected_dir, "0003.jpg"),
                os.path.join(expected_dir, "0004.jpg"),
                os.path.join(expected_dir, "0005.jpg"),
                os.path.join(expected_dir, "0006.jpg"),
            ],
        )
        crawler.fetcher.fetch.assert_has_calls(
            [
                call("https://example.com/gallery/index"),
                call("https://example.com/gallery/index?p=1"),
                call("https://example.com/gallery/index?p=2"),
            ]
        )

    def test_download_call_arguments(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        with patch("crawler.Gallery.download_image") as fake:
            fake.return_value = "/tmp/fake.jpg"
            crawler.crawl(make_cbz=False)

        first_args, first_kwargs = fake.call_args_list[0]
        self.assertEqual(first_args[0], "https://example.com/gallery/i1.jpg")
        self.assertTrue(first_kwargs["output_dir"].startswith(self.output_dir))
        self.assertIn(".index.staging-", first_kwargs["output_dir"])
        self.assertEqual(first_kwargs["ext"], "jpg")
        self.assertEqual(first_kwargs["name"], "0001")
        self.assertTrue(first_kwargs["require_valid_image"])
        self.assertFalse(first_kwargs["selenium_fallback"])
        self.assertEqual(first_kwargs["max_retries"], 5)
        self.assertEqual(first_kwargs["request_timeout_seconds"], 20)

    def test_download_receives_fetcher_session_context(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")
        crawler.fetcher.cookies = {"session": "token"}
        crawler.fetcher.browser_driver = object()

        with patch("crawler.Gallery.download_image") as fake:
            fake.return_value = "/tmp/fake.jpg"
            crawler.crawl(make_cbz=False)

        first_kwargs = fake.call_args_list[0].kwargs
        self.assertEqual(first_kwargs["cookies"], {"session": "token"})
        self.assertIs(first_kwargs["browser_driver"], crawler.fetcher.browser_driver)

    def test_failed_image_refreshes_picture_page_for_a_new_dispatch_url(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "refresh_picture_page_on_failure": True,
            "max_refreshes": 1,
            "failure_hint": "refresh your image delivery settings",
        }
        crawler = self._make_crawler(format_data=format_data)
        picture_url = "https://example.com/gallery/p1.html"
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                SINGLE_PAGE_HTML,
                PICTURE_WITH.format(src="expired.webp"),
                PICTURE_WITH.format(src="fresh.webp"),
            ]
        )

        calls = []

        def fail_then_download(image_url, **kwargs):
            calls.append(image_url)
            if len(calls) == 1:
                return ""
            return _write_download(image_url, **kwargs)

        with patch("crawler.Gallery.download_image", side_effect=fail_then_download):
            result = crawler.crawl(make_cbz=False)

        self.assertEqual(
            calls,
            [
                "https://example.com/gallery/expired.webp",
                "https://example.com/gallery/fresh.webp",
            ],
        )
        self.assertEqual(
            crawler.fetcher.fetch.call_args_list,
            [call(GALLERY_URL), call(picture_url), call(picture_url)],
        )
        self.assertEqual(result["image_urls"], [calls[-1]])

    def test_failed_image_uses_site_provided_guest_fallback_link(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "refresh_picture_page_on_failure": False,
            "max_refreshes": 0,
            "failure_hint": "guest fallback failed",
            "failure_page_fallback": {
                "selector": {"name": "a", "id": "loadfail"},
                "url_attr": "href",
                "onclick_pattern": r"nl\(([^)]+)\)",
                "query_param": "nl",
            },
        }
        crawler = self._make_crawler(format_data=format_data)
        picture_url = "https://example.com/gallery/p1.html"
        primary_page = (
            '<img src="https://hath.example/primary.webp">'
            '<a id="loadfail" href="#" onclick="return nl(guest-token)">'
            "Click here if the image fails loading</a>"
        )
        fallback_page = '<img src="https://fallback.example/fresh.webp">'
        crawler.fetcher.fetch = MagicMock(
            side_effect=[SINGLE_PAGE_HTML, primary_page, fallback_page]
        )

        calls = []

        def fail_then_download(image_url, **kwargs):
            calls.append(image_url)
            if len(calls) == 1:
                return ""
            return _write_download(image_url, **kwargs)

        with patch("crawler.Gallery.download_image", side_effect=fail_then_download):
            result = crawler.crawl(make_cbz=False)

        fallback_url = f"{picture_url}?nl=guest-token"
        self.assertEqual(
            crawler.fetcher.fetch.call_args_list,
            [call(GALLERY_URL), call(picture_url), call(fallback_url)],
        )
        self.assertEqual(
            calls,
            ["https://hath.example/primary.webp", "https://fallback.example/fresh.webp"],
        )
        self.assertEqual(result["image_urls"], ["https://fallback.example/fresh.webp"])

    def test_image_delivery_failure_hint_is_reported(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "refresh_picture_page_on_failure": False,
            "max_refreshes": 0,
            "failure_hint": "Set Image Load Settings to No.",
        }
        crawler = self._make_crawler(format_data=format_data)
        crawler.fetcher.fetch = MagicMock(
            side_effect=[SINGLE_PAGE_HTML, PICTURE_WITH.format(src="i1.jpg")]
        )

        with patch("crawler.Gallery.download_image", return_value=""):
            with self.assertRaises(RuntimeError) as ctx:
                crawler.crawl()

        self.assertIn("Set Image Load Settings to No.", str(ctx.exception))

    def test_referer_passed_when_enabled(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["img_referrer"] = True
        crawler = self._make_crawler(format_data=format_data)
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        with patch("crawler.Gallery.download_image") as fake:
            fake.return_value = "/tmp/fake.jpg"
            crawler.crawl(make_cbz=False)

        picture_urls = [
            "https://example.com/gallery/p1.html",
            "https://example.com/gallery/p2.html",
            "https://example.com/gallery/p3.html",
        ]
        for call_, picture_url in zip(fake.call_args_list, picture_urls):
            self.assertIs(call_.kwargs["img_referrer"], True)
            self.assertEqual(call_.kwargs["referer_url"], picture_url)

    def test_no_referer_when_disabled(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        with patch("crawler.Gallery.download_image") as fake:
            fake.return_value = "/tmp/fake.jpg"
            crawler.crawl(make_cbz=False)

        for call_ in fake.call_args_list:
            self.assertIs(call_.kwargs["img_referrer"], False)

    def test_failed_picture_does_not_publish_partial_output(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                FetchError("network down"),
                PICTURE_WITH.format(src="i3.jpg"),
            ]
        )

        with self.assertRaises(RuntimeError) as ctx:
            crawler.crawl()

        message = str(ctx.exception)
        self.assertIn("https://example.com/gallery/p2.html", message)
        self.assertIn("1 of 3", message)

        expected_dir = os.path.join(self.output_dir, "index")
        self.assertFalse(os.path.exists(expected_dir))
        self.assertFalse(
            any(".staging-" in name for name in os.listdir(self.output_dir))
        )

    def test_guest_failure_stops_and_resumes_from_retained_partial_images(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "request_attempts": 2,
            "request_timeout_seconds": 10,
            "retry_delay_seconds": 1,
            "stop_on_image_failure": True,
            "resume_partial_downloads": True,
        }
        crawler = self._make_crawler(format_data=format_data)
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                PICTURE_WITH.format(src="i2.jpg"),
            ]
        )

        def fail_second_page(_image_url, **kwargs):
            if kwargs["name"] == "0002":
                return ""
            return _write_download(_image_url, **kwargs)

        with patch("crawler.Gallery.download_image", side_effect=fail_second_page):
            with self.assertRaises(RuntimeError) as ctx:
                crawler.crawl(make_cbz=False)

        partial_dir = os.path.join(self.output_dir, "index.incomplete")
        self.assertIn("Partial downloads retained", str(ctx.exception))
        self.assertTrue(os.path.isfile(os.path.join(partial_dir, "0001.jpg")))
        self.assertFalse(os.path.exists(os.path.join(partial_dir, "0002.jpg")))
        self.assertEqual(
            crawler.fetcher.fetch.call_args_list,
            [
                call(GALLERY_URL),
                call("https://example.com/gallery/p1.html"),
                call("https://example.com/gallery/p2.html"),
            ],
        )

        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="i2.jpg"),
                PICTURE_WITH.format(src="i3.jpg"),
            ]
        )
        result = crawler.crawl(make_cbz=False)

        final_dir = os.path.join(self.output_dir, "index")
        self.assertFalse(os.path.exists(partial_dir))
        self.assertEqual(
            result["downloaded_files"],
            [
                os.path.join(final_dir, "0001.jpg"),
                os.path.join(final_dir, "0002.jpg"),
                os.path.join(final_dir, "0003.jpg"),
            ],
        )
        self.assertEqual(
            crawler.fetcher.fetch.call_args_list,
            [
                call(GALLERY_URL),
                call("https://example.com/gallery/p2.html"),
                call("https://example.com/gallery/p3.html"),
            ],
        )

    def test_run_retries_an_incomplete_gallery_within_the_current_session(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "resume_partial_downloads": True,
            "incomplete_session_retries": 5,
            "incomplete_retry_delay_seconds": 0,
        }
        crawler = self._make_crawler(format_data=format_data)
        completed = {"output_dir": self.output_dir}

        with patch.object(
            crawler,
            "crawl",
            side_effect=[
                IncompleteGalleryError("first image delivery failure"),
                IncompleteGalleryError("second image delivery failure"),
                completed,
            ],
        ) as crawl, patch("crawler.Gallery.print") as notify:
            result = crawler.run(make_cbz=False)

        self.assertIs(result, completed)
        self.assertEqual(crawl.call_count, 3)
        self.assertIn("retry 1/5", notify.call_args_list[0].args[0])
        self.assertIn("retry 2/5", notify.call_args_list[1].args[0])

    def test_run_notifies_after_five_unsuccessful_incomplete_retries(self):
        format_data = deepcopy(VALID_FORMAT)
        format_data["image_delivery"] = {
            "resume_partial_downloads": True,
            "incomplete_session_retries": 5,
            "incomplete_retry_delay_seconds": 0,
        }
        crawler = self._make_crawler(format_data=format_data)

        with patch.object(
            crawler,
            "crawl",
            side_effect=IncompleteGalleryError("image delivery unavailable"),
        ) as crawl, patch("crawler.Gallery.print") as notify:
            with self.assertRaises(IncompleteGalleryError) as ctx:
                crawler.run(make_cbz=False)

        self.assertEqual(crawl.call_count, 6)  # Initial attempt + five resumes.
        self.assertIn("after 5 resume attempts", str(ctx.exception))
        self.assertIn("after 5 resume attempts", notify.call_args_list[-1].args[0])

    def test_empty_download_path_raises(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        with patch("crawler.Gallery.download_image", return_value=""):
            with self.assertRaises(RuntimeError) as ctx:
                crawler.crawl()

        self.assertIn("returned no valid image file", str(ctx.exception))

    def test_uncreatable_output_directory_raises(self):
        blocker = os.path.join(self.tmp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        crawler = self._make_crawler(output_dir=blocker)
        crawler.fetcher.fetch = MagicMock(return_value=THREE_PAGE_HTML)

        with self.assertRaises(RuntimeError) as ctx:
            crawler.crawl()

        self.assertIn("Could not create output directory", str(ctx.exception))

    def test_recrawl_replaces_existing_files(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")
        crawler.crawl()

        expected_dir = os.path.join(self.output_dir, "index")
        stale = os.path.join(expected_dir, "0009.jpg")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("old")

        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                TWO_PAGE_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                PICTURE_WITH.format(src="i2.jpg"),
            ]
        )
        crawler.crawl()

        self.assertTrue(os.path.isfile(os.path.join(expected_dir, "0001.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(expected_dir, "0002.jpg")))
        self.assertFalse(os.path.exists(stale))

    def test_failed_recrawl_preserves_previous_complete_gallery(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")
        first = crawler.crawl()
        with open(first["cbz_path"], "rb") as handle:
            first_archive = handle.read()
        with open(first["downloaded_files"][0], "rb") as handle:
            first_page = handle.read()

        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                FetchError("network down"),
                FetchError("network down"),
                FetchError("network down"),
            ]
        )
        with self.assertRaises(RuntimeError):
            crawler.crawl()

        with open(first["cbz_path"], "rb") as handle:
            self.assertEqual(handle.read(), first_archive)
        with open(first["downloaded_files"][0], "rb") as handle:
            self.assertEqual(handle.read(), first_page)
        self.assertFalse(
            any(".staging-" in name for name in os.listdir(self.output_dir))
        )

    def test_rejects_no_output_configuration(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(return_value=THREE_PAGE_HTML)

        with self.assertRaises(ValueError) as ctx:
            crawler.crawl(make_cbz=False, keep_images=False)

        self.assertIn("no output", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.output_dir, "index")))

    def test_page_padding_expands_above_9999(self):
        self.assertEqual(GalleryCrawler._page_width(3), 4)
        self.assertEqual(GalleryCrawler._page_width(9999), 4)
        self.assertEqual(GalleryCrawler._page_width(10000), 5)
        self.assertEqual(GalleryCrawler._page_width(12000), 5)
        self.assertEqual(GalleryCrawler._page_width(100000), 6)

    def test_run_closes_fetcher(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.run()

        self.assertEqual(len(result["downloaded_files"]), 3)
        self.assertEqual(crawler.fetcher.closed, 1)

    def test_run_closes_fetcher_when_crawl_raises(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(side_effect=FetchError("boom"))

        with self.assertRaises(FetchError):
            crawler.run()

        self.assertEqual(crawler.fetcher.closed, 1)


class TestExtPreservation(TestCrawl):
    """Preserving the downloaded image's original format (preserve_ext)."""

    def test_preserves_source_extension_by_default(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="a.webp"),
                PICTURE_WITH.format(src="b.png"),
                PICTURE_WITH.format(src="c.jpg"),
            ]
        )

        result = crawler.crawl(make_cbz=False)

        self.assertEqual(
            [os.path.basename(path) for path in result["downloaded_files"]],
            ["0001.webp", "0002.png", "0003.jpg"],
        )
        expected_dir = os.path.join(self.output_dir, "index")
        for name in ("0001.webp", "0002.png", "0003.jpg"):
            self.assertTrue(os.path.isfile(os.path.join(expected_dir, name)))

    def test_preserve_ext_false_forces_jpeg(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = MagicMock(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="a.webp"),
                PICTURE_WITH.format(src="b.webp"),
                PICTURE_WITH.format(src="c.webp"),
            ]
        )

        result = crawler.crawl(make_cbz=False, preserve_ext=False)

        self.assertEqual(
            [os.path.basename(path) for path in result["downloaded_files"]],
            ["0001.jpg", "0002.jpg", "0003.jpg"],
        )


@unittest.skip("legacy Gallery.run was removed by the Task 6 breaking cutover")
class TestTopLevelRun(unittest.TestCase):
    """Module-level ``run()`` convenience wrapper (Task 6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = os.path.join(self.tmp.name, "out")
        self.downloader = patch(
            "crawler.Gallery.download_image", side_effect=_write_download
        )
        self.downloader.start()
        self.addCleanup(self.downloader.stop)

        self.aliases_path = _write_alias(
            self.tmp.name, [("example.com", "examplegallery", "GalleryRequests")]
        )
        self.formats_dir = os.path.join(self.tmp.name, "formats")
        _write_format(self.formats_dir, "examplegallery", VALID_FORMAT)
        for target, value in (
            ("crawler.Gallery.ALIASES_PATH", self.aliases_path),
            ("crawler.Gallery.FORMATS_DIR", self.formats_dir),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _start_fetcher(self, side_effect=None):
        self.fetcher_patch = patch("crawler.Gallery.PageFetcher")
        fetcher = self.fetcher_patch.start()
        self.addCleanup(self.fetcher_patch.stop)
        if side_effect is not None:
            fetcher.return_value.fetch.side_effect = side_effect
        return fetcher

    def test_run_returns_documented_result_and_closes_fetcher(self):
        fetcher = self._start_fetcher(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                PICTURE_WITH.format(src="i2.jpg"),
                PICTURE_WITH.format(src="i3.jpg"),
            ]
        )

        result = run_gallery(
            "https://example.com/gallery/index", output_dir=self.output_dir
        )

        self.assertEqual(
            set(result),
            {
                "title",
                "gallery_url",
                "output_dir",
                "picture_urls",
                "image_urls",
                "downloaded_files",
                "cbz_path",
            },
        )
        self.assertEqual(result["title"], "index")
        self.assertEqual(len(result["downloaded_files"]), 3)
        self.assertTrue(os.path.isfile(result["cbz_path"]))
        fetcher.return_value.close.assert_called_once()

    def test_run_closes_fetcher_when_crawl_raises(self):
        fetcher = self._start_fetcher(side_effect=FetchError("boom"))

        with self.assertRaises(FetchError):
            run_gallery(
                "https://example.com/gallery/index", output_dir=self.output_dir
            )

        fetcher.return_value.close.assert_called_once()

    def test_run_forwards_constructor_options(self):
        fetcher = self._start_fetcher(
            side_effect=[
                THREE_PAGE_HTML,
                PICTURE_WITH.format(src="i1.jpg"),
                PICTURE_WITH.format(src="i2.jpg"),
                PICTURE_WITH.format(src="i3.jpg"),
            ]
        )

        run_gallery(
            "https://example.com/gallery/index",
            output_dir=self.output_dir,
            fetch_mode="browser",
            headless=False,
            make_cbz=False,
        )

        _, fetcher_kwargs = fetcher.call_args
        self.assertEqual(fetcher_kwargs["fetch_mode"], "browser")
        self.assertFalse(fetcher_kwargs["headless"])
        fetcher.return_value.close.assert_called_once()


class TestCbzPackaging(TestCrawl):
    """CBZ archive creation and cleanup semantics (Task 5)."""

    def _write_pages(self, output_dir, count=3):
        os.makedirs(output_dir, exist_ok=True)
        files = []
        for index in range(1, count + 1):
            path = os.path.join(output_dir, f"{index:04d}.jpg")
            with open(path, "wb") as handle:
                handle.write(b"fake-jpeg")
            files.append(path)
        return files

    def test_members_match_downloaded_files_in_order(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl()

        cbz_path = result["cbz_path"]
        self.assertEqual(
            cbz_path, os.path.join(self.output_dir, "index", "index.cbz")
        )
        self.assertTrue(os.path.isfile(cbz_path))
        with zipfile.ZipFile(cbz_path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["0001.jpg", "0002.jpg", "0003.jpg"],
            )

    def test_default_retains_cbz_and_jpegs(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl()

        expected_dir = os.path.join(self.output_dir, "index")
        self.assertTrue(os.path.isfile(os.path.join(expected_dir, "index.cbz")))
        for page in ("0001.jpg", "0002.jpg", "0003.jpg"):
            self.assertTrue(os.path.isfile(os.path.join(expected_dir, page)))
        self.assertIsNotNone(result["cbz_path"])

    def test_keep_images_false_leaves_cbz_and_removes_images(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl(make_cbz=True, keep_images=False)

        cbz_path = result["cbz_path"]
        self.assertTrue(os.path.isfile(cbz_path))
        with zipfile.ZipFile(cbz_path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["0001.jpg", "0002.jpg", "0003.jpg"],
            )
        self.assertEqual(set(os.listdir(os.path.dirname(cbz_path))), {"index.cbz"})
        self.assertEqual(result["downloaded_files"], [])

    def test_make_cbz_false_returns_none_and_retains_images(self):
        crawler = self._make_crawler()
        crawler.fetcher.fetch = self._three_page_fetch("i1.jpg", "i2.jpg", "i3.jpg")

        result = crawler.crawl(make_cbz=False, keep_images=True)

        self.assertIsNone(result["cbz_path"])
        self.assertEqual(
            set(os.listdir(os.path.join(self.output_dir, "index"))),
            {"0001.jpg", "0002.jpg", "0003.jpg"},
        )

    def test_archive_creation_fails_partway(self):
        crawler = self._make_crawler()
        crawler.title = "index"
        output_dir = os.path.join(self.output_dir, "index")
        files = self._write_pages(output_dir)

        real_write = zipfile.ZipFile.write
        state = {"count": 0}

        def flaky_write(archive, filename, arcname=None):
            state["count"] += 1
            if state["count"] == 2:
                raise RuntimeError("disk full")
            return real_write(archive, filename, arcname)

        with patch("crawler.Gallery.zipfile.ZipFile.write", flaky_write):
            with self.assertRaises(RuntimeError) as ctx:
                crawler._package_cbz(output_dir, files)

        self.assertIn("disk full", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz")))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz.tmp")))
        for path in files:
            self.assertTrue(os.path.isfile(path))

    def test_final_rename_fails(self):
        crawler = self._make_crawler()
        crawler.title = "index"
        output_dir = os.path.join(self.output_dir, "index")
        files = self._write_pages(output_dir)

        with patch(
            "crawler.Gallery.os.replace", side_effect=OSError("rename denied")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                crawler._package_cbz(output_dir, files)

        self.assertIn("finalize", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz")))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz.tmp")))
        for path in files:
            self.assertTrue(os.path.isfile(path))

    def test_image_disappears_before_packaging(self):
        crawler = self._make_crawler()
        crawler.title = "index"
        output_dir = os.path.join(self.output_dir, "index")
        files = self._write_pages(output_dir)
        os.remove(files[1])

        with self.assertRaises(RuntimeError) as ctx:
            crawler._package_cbz(output_dir, files)

        self.assertIn("Failed to package CBZ", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz")))
        self.assertFalse(os.path.exists(os.path.join(output_dir, "index.cbz.tmp")))
        self.assertTrue(os.path.isfile(files[0]))
        self.assertTrue(os.path.isfile(files[2]))

    def test_cleanup_fails_after_valid_archive(self):
        crawler = self._make_crawler()
        crawler.title = "index"
        output_dir = os.path.join(self.output_dir, "index")
        files = self._write_pages(output_dir)

        with patch(
            "crawler.Gallery.os.remove", side_effect=OSError("remove denied")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                crawler._package_cbz(output_dir, files, keep_images=False)

        self.assertIn("Failed to remove source image", str(ctx.exception))
        cbz_path = os.path.join(output_dir, "index.cbz")
        self.assertTrue(os.path.isfile(cbz_path))
        with zipfile.ZipFile(cbz_path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["0001.jpg", "0002.jpg", "0003.jpg"],
            )
        for path in files:
            self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
