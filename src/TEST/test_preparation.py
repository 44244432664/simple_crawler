"""Tests for api.preparation – Task 3 of ai-integration.

Covers selector utilities, get_metadata handler, and volumes_prepare handler
with HTML fixtures for real volumes, root chapter lists, pagination,
duplicates, relative links, missing covers, ordering, decimal identifiers,
synthetic gallery volumes, and selection across boundaries.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from dataclasses import replace

from api.contracts import (
    Chapter,
    ContentType,
    CrawlContext,
    CrawlRequest,
    IncompleteCrawlError,
    InvalidFlowError,
    Metadata,
    OutputFormat,
    SelectionMode,
    Volume,
)
from api.flow import build_flow, execute_flow, set_module_handler
from api.preparation import (
    canonical_page_url,
    find_all_elements,
    find_element,
    get_metadata_handler,
    is_empty_selector,
    read_attribute,
    register_preparation_handlers,
    resolve_url,
    selector_args,
    selector_to_css,
    volumes_prepare_handler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(
    content_type: str = "novel",
    output_format: str = "epub",
    selection: str = "full",
    url: str = "https://example.com/novel/title",
    start_index: int | None = None,
    end_index: int | None = None,
    chapter_url: str | None = None,
) -> CrawlRequest:
    kwargs: dict = {
        "url": url,
        "content_type": content_type,
        "output_format": output_format,
        "selection": selection,
    }
    if start_index is not None:
        kwargs["start_index"] = start_index
    if end_index is not None:
        kwargs["end_index"] = end_index
    if chapter_url is not None:
        kwargs["chapter_url"] = chapter_url
    return CrawlRequest(**kwargs)


def _ctx(
    request: CrawlRequest | None = None,
    format_definition: dict | None = None,
    metadata: Metadata | None = None,
    main_page_html: str | None = None,
    output_dir: str | None = None,
    raw_page=None,
    log_fn=None,
) -> CrawlContext:
    ctx = CrawlContext(
        request=request or _req(),
        format_definition=format_definition or {},
        metadata=metadata,
        main_page_html=main_page_html,
        output_dir=output_dir,
        raw_page=raw_page,
        log=log_fn,
    )
    return ctx


def _mock_raw_page(html_map: dict[str, str] | str = ""):
    """Return a mock RawPageService whose get_raw_page returns from *html_map*."""
    mock = MagicMock()
    if isinstance(html_map, str):
        mock.get_raw_page = MagicMock(return_value=html_map)
    else:
        mock.get_raw_page = MagicMock(side_effect=lambda url, *a, **kw: html_map.get(url, ""))
    return mock


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------


METADATA_NOVEL_HTML = """
<html><head><title>My Test Novel</title></head>
<body>
<h1>My Test Novel</h1>
<img class="cover" src="https://example.com/cover.jpg" />
<div class="genres">
    <a class="genre-tag">Fantasy</a>
    <a class="genre-tag">Adventure</a>
</div>
<div class="description">An epic fantasy adventure.</div>
<div class="info-label">Author</div>
<div class="info-value">Test Author</div>
<div class="info-label">Status</div>
<div class="info-value">Ongoing</div>
</body></html>
"""


METADATA_COVER_FROM_META_HTML = """
<html><body>
<h1>Novel With Meta Cover</h1>
<meta property="og:image" content="https://example.com/meta-cover.png" />
<div class="info-label">Author</div>
<div class="info-value">Meta Author</div>
</body></html>
"""


METADATA_MISSING_TITLE_HTML = """
<html><body><p>No title element</p></body></html>
"""


VOL_SECTION_HTML = """
<html><body>
<h1>My Novel</h1>
<div class="vol-section">
    <h3 class="vol-title">Volume 1</h3>
    <ul class="chap-list">
        <li><a href="/chapter/1">Ch 1</a></li>
        <li><a href="/chapter/2">Ch 2</a></li>
    </ul>
</div>
<div class="vol-section">
    <h3 class="vol-title">Volume 2</h3>
    <ul class="chap-list">
        <li><a href="/chapter/3">Ch 3</a></li>
        <li><a href="https://example.com/chapter/4">Ch 4</a></li>
    </ul>
</div>
<div class="vol-section disabled">
    <h3 class="vol-title">Volume 3 (disabled)</h3>
    <ul class="chap-list">
        <li><a href="/chapter/5">Ch 5</a></li>
    </ul>
</div>
</body></html>
"""


ROOT_CHAPTER_LIST_HTML_PAGE1 = """
<html><body>
<h1>Novel With Flat Chapters</h1>
<div class="chapter-list">
    <a href="/ch/10">Ch 10</a>
    <a href="/ch/9">Ch 9</a>
    <a href="/ch/8">Ch 8</a>
    <div class="pagination">
        <a href="/chapter-list?page=2">Next</a>
    </div>
</div>
</body></html>
"""


ROOT_CHAPTER_LIST_HTML_PAGE2 = """
<html><body>
<div class="chapter-list">
    <a href="/ch/7">Ch 7</a>
    <a href="/ch/6">Ch 6</a>
    <a href="/ch/5">Ch 5</a>
</div>
</body></html>
"""


ROOT_CHAPTER_LIST_DUPES_HTML = """
<html><body>
<div class="chapter-list">
    <a href="/ch/1">Ch 1</a>
    <a href="/ch/1">Ch 1 dup</a>
    <a href="/ch/2">Ch 2</a>
    <a href="/ch/1">Ch 1 again</a>
</div>
</body></html>
"""


ROOT_CHAPTER_RELATIVE_HTML = """
<html><body>
<div class="chapter-list">
    <a href="chapter/1">Ch 1</a>
    <a href="../chapter/2">Ch 2</a>
    <a href="https://other.com/ch/3">Ch 3 other</a>
</div>
</body></html>
"""


VOL_SECTION_DECIMAL_IDS_HTML = """
<html><body>
<div class="vol-section">
    <h3 class="vol-title">Part 1</h3>
    <ul class="chap-list">
        <li><a href="/ch/1.1">Chapter 1.1</a></li>
        <li><a href="/ch/1.5">Chapter 1.5</a></li>
    </ul>
</div>
<div class="vol-section">
    <h3 class="vol-title">Part 2</h3>
    <ul class="chap-list">
        <li><a href="/ch/2">Chapter 2</a></li>
        <li><a href="/ch/12.5">Chapter 12.5</a></li>
    </ul>
</div>
</body></html>
"""


NEWEST_FIRST_HTML = """
<html><body>
<div class="chapter-list">
    <a href="/ch/100">Ch 100</a>
    <a href="/ch/99">Ch 99</a>
    <a href="/ch/98">Ch 98</a>
</div>
</body></html>
"""


GALLERY_HTML = """
<html><body>
<h1>My Gallery</h1>
<div class="gallery-grid">
    <a href="/pic/001">Pic 1</a>
    <a href="/pic/002">Pic 2</a>
    <a href="/pic/003">Pic 3</a>
</div>
</body></html>
"""


NO_VOL_NO_CHAPTER_LIST_HTML = """
<html><body>
<h1>Empty Novel</h1>
<p>There are no chapters listed.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Selector utility tests
# ---------------------------------------------------------------------------


class TestSelectorArgs(unittest.TestCase):
    """selector_args converts format selector dicts to bs4 find kwargs."""

    def test_strips_non_find_keys(self):
        result = selector_args({
            "name": "a",
            "class_": "chapter",
            "other_attr": "href",
            "container": {"name": "div"},
            "click": {"class_": "btn"},
            "text": True,
            "allowed_hosts": ["example.com"],
            "delete": 2,
        })
        self.assertEqual(result, {"name": "a", "class_": "chapter"})

    def test_expands_attrs_dict(self):
        result = selector_args({
            "name": "meta",
            "attrs": {"property": "og:image"},
            "other_attr": "content",
        })
        self.assertEqual(result, {"name": "meta", "property": "og:image"})

    def test_filters_blank_values(self):
        result = selector_args({"name": "div", "class_": "", "id": ""})
        self.assertEqual(result, {"name": "div"})

    def test_empty_selector(self):
        self.assertEqual(selector_args({}), {})
        self.assertEqual(selector_args(None), {})

    def test_preserves_itemprop(self):
        result = selector_args({"name": "div", "itemprop": "description"})
        self.assertEqual(result, {"name": "div", "itemprop": "description"})

    def test_vol_name_is_stripped(self):
        result = selector_args({"vol_name": "Chapters", "class_": "x"})
        self.assertEqual(result, {"class_": "x"})

    def test_pagination_link_max_pages_stripped(self):
        result = selector_args({
            "name": "div",
            "class_": "list",
            "link": {"name": "a"},
            "pagination": {"name": "div"},
            "max_pages": 250,
        })
        self.assertEqual(result, {"name": "div", "class_": "list"})

    def test_page_param_stripped(self):
        result = selector_args({
            "name": "div",
            "page_param": "p",
        })
        self.assertEqual(result, {"name": "div"})


class TestFindElement(unittest.TestCase):
    """find_element locates a single element or returns None."""

    def test_finds_by_name(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body><p>Hello</p></body>", "html.parser")
        el = find_element(soup, {"name": "p"})
        self.assertIsNotNone(el)
        self.assertEqual(el.text, "Hello")

    def test_returns_none_when_empty(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body></body>", "html.parser")
        self.assertIsNone(find_element(soup, {}))
        self.assertIsNone(find_element(soup, None))

    def test_returns_none_when_no_match(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body><p>Hi</p></body>", "html.parser")
        self.assertIsNone(find_element(soup, {"name": "span"}))


class TestFindAllElements(unittest.TestCase):
    """find_all_elements locates multiple elements."""

    def test_finds_multiple(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body><a>A</a><a>B</a></body>", "html.parser")
        els = find_all_elements(soup, {"name": "a"})
        self.assertEqual(len(els), 2)

    def test_empty_when_no_match(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body></body>", "html.parser")
        self.assertEqual(find_all_elements(soup, {"name": "div"}), [])

    def test_empty_selector(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<body><p>X</p></body>", "html.parser")
        self.assertEqual(find_all_elements(soup, {}), [])


class TestReadAttribute(unittest.TestCase):
    """read_attribute extracts element attributes with JS fallback."""

    def test_reads_normal_attr(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<img src="url.png">', "html.parser")
        el = soup.find("img")
        self.assertEqual(read_attribute(el, "src"), "url.png")

    def test_strips_whitespace(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup('<img src="  url.png  ">', "html.parser")
        el = soup.find("img")
        self.assertEqual(read_attribute(el, "src"), "url.png")

    def test_js_fallback(self):
        from bs4 import BeautifulSoup
        html = '<img onclick="loadImage(\'https://example.com/lazy.jpg\')">'
        soup = BeautifulSoup(html, "html.parser")
        el = soup.find("img")
        result = read_attribute(el, "onclick")
        # The regex should extract the URL
        self.assertIn("example.com", result)

    def test_empty_attr_returns_empty(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<img>", "html.parser")
        el = soup.find("img")
        self.assertEqual(read_attribute(el, "src"), "")
        self.assertEqual(read_attribute(el, ""), "")


class TestResolveUrl(unittest.TestCase):
    """resolve_url resolves relative URLs against a base."""

    def test_absolute_url_unchanged(self):
        self.assertEqual(
            resolve_url("https://base.com", "https://other.com/path"),
            "https://other.com/path",
        )

    def test_relative_resolved(self):
        result = resolve_url("https://example.com/novel/", "/chapter/1")
        self.assertEqual(result, "https://example.com/chapter/1")

    def test_empty_href(self):
        self.assertEqual(resolve_url("https://example.com", ""), "")

    def test_empty_base(self):
        self.assertEqual(resolve_url("", "chapter/1"), "chapter/1")


class TestCanonicalPageUrl(unittest.TestCase):
    """canonical_page_url normalizes URLs for deduplication."""

    def test_strips_fragment(self):
        self.assertEqual(
            canonical_page_url("https://example.com/path#section"),
            "https://example.com/path",
        )

    def test_lowercases_scheme_and_host(self):
        self.assertEqual(
            canonical_page_url("HTTPS://EXAMPLE.COM/Path"),
            "https://example.com/Path",
        )

    def test_preserves_query(self):
        self.assertEqual(
            canonical_page_url("https://example.com/path?p=1&x=2"),
            "https://example.com/path?p=1&x=2",
        )


class TestIsEmptySelector(unittest.TestCase):
    """is_empty_selector detects selectors with no usable find keys."""

    def test_none_is_empty(self):
        self.assertTrue(is_empty_selector(None))

    def test_empty_dict_is_empty(self):
        self.assertTrue(is_empty_selector({}))

    def test_blank_values_is_empty(self):
        self.assertTrue(is_empty_selector({"name": "", "class_": ""}))

    def test_non_blank_name_is_not_empty(self):
        self.assertFalse(is_empty_selector({"name": "div"}))

    def test_non_blank_class_is_not_empty(self):
        self.assertFalse(is_empty_selector({"class_": "chapter"}))

    def test_attrs_dict_with_entries_is_not_empty(self):
        self.assertFalse(is_empty_selector({"attrs": {"property": "og:image"}}))

    def test_only_non_find_keys_is_empty(self):
        self.assertTrue(is_empty_selector({
            "other_attr": "href",
            "container": {"name": "div"},
            "click": {"class_": "btn"},
        }))


class TestSelectorToCss(unittest.TestCase):
    """selector_to_css generates CSS selector strings."""

    def test_basic(self):
        self.assertEqual(
            selector_to_css({"name": "div", "class_": "chapter", "id": "c1"}),
            "div.chapter#c1",
        )

    def test_itemprop(self):
        self.assertEqual(
            selector_to_css({"name": "div", "itemprop": "description"}),
            'div[itemprop="description"]',
        )

    def test_empty_returns_none(self):
        self.assertIsNone(selector_to_css({}))
        self.assertIsNone(selector_to_css(None))

    def test_multiple_classes(self):
        self.assertEqual(
            selector_to_css({"name": "div", "class_": "a b"}),
            "div.a.b",
        )


# ---------------------------------------------------------------------------
# get_metadata tests
# ---------------------------------------------------------------------------


class TestGetMetadata(unittest.TestCase):
    """get_metadata handler extracts work-level metadata from the main page."""

    def test_extracts_all_fields(self):
        request = _req(url="https://example.com/novel/test")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_NOVEL_HTML))
        fmt = {
            "title": {"name": "h1"},
            "cover": {"name": "img", "class_": "cover", "other_attr": "src"},
            "genre": {"name": "a", "class_": "genre-tag"},
            "description": {"name": "div", "class_": "description", "text": True},
            "other_info": {
                "holder": {"name": "div", "class_": "info-label"},
                "value": {"name": "div", "class_": "info-value"},
            },
        }
        ctx.format_definition = fmt
        get_metadata_handler(ctx, {})

        self.assertIsNotNone(ctx.metadata)
        self.assertEqual(ctx.metadata.title, "My Test Novel")
        self.assertEqual(ctx.metadata.cover_url, "https://example.com/cover.jpg")
        self.assertEqual(ctx.metadata.genres, ("Fantasy", "Adventure"))
        self.assertEqual(ctx.metadata.description, "An epic fantasy adventure.")
        self.assertEqual(ctx.metadata.author, "Test Author")
        self.assertEqual(ctx.metadata.other_info, {"Status": "Ongoing"})
        self.assertEqual(ctx.metadata.source_url, "https://example.com/novel/test")
        self.assertIsNotNone(ctx.main_page_html)

    def test_cover_from_meta_tag(self):
        request = _req(url="https://example.com/novel/meta")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_COVER_FROM_META_HTML))
        fmt = {
            "title": {"name": "h1"},
            "cover": {"name": "meta", "attrs": {"property": "og:image"}, "other_attr": "content"},
        }
        ctx.format_definition = fmt
        get_metadata_handler(ctx, {})

        self.assertIsNotNone(ctx.metadata)
        self.assertEqual(ctx.metadata.cover_url, "https://example.com/meta-cover.png")

    def test_relative_cover_is_resolved_against_the_page_url(self):
        html = '<h1>X</h1><img class="cover" src="images/cover.jpg">'
        request = _req(url="https://example.com/books/current/index.html")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(html))
        ctx.format_definition = {
            "title": {"name": "h1"},
            "cover": {"name": "img", "class_": "cover", "other_attr": "src"},
        }
        get_metadata_handler(ctx, {})
        self.assertEqual(
            ctx.metadata.cover_url,
            "https://example.com/books/current/images/cover.jpg",
        )

    def test_request_output_directory_is_preserved(self):
        request = CrawlRequest(
            "https://example.com/novel/test", "novel", "epub", output_dir="chosen-output"
        )
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_NOVEL_HTML))
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {})
        self.assertEqual(ctx.output_dir, "chosen-output")

    def test_missing_title_raises(self):
        request = _req(url="https://example.com/novel/notitle")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_MISSING_TITLE_HTML))
        fmt = {"title": {"name": "h1"}}
        ctx.format_definition = fmt
        with self.assertRaises(IncompleteCrawlError):
            get_metadata_handler(ctx, {})

    def test_empty_title_selector_raises(self):
        request = _req()
        ctx = _ctx(request=request, raw_page=_mock_raw_page(""))
        ctx.format_definition = {"title": {"name": "", "class_": ""}}
        with self.assertRaises(InvalidFlowError):
            get_metadata_handler(ctx, {})

    def test_no_title_selector_raises(self):
        request = _req()
        ctx = _ctx(request=request, raw_page=_mock_raw_page(""))
        ctx.format_definition = {}
        with self.assertRaises(InvalidFlowError):
            get_metadata_handler(ctx, {})

    def test_output_dir_computed_when_empty(self):
        request = _req(url="https://example.com/novel/test")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_NOVEL_HTML))
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {})
        self.assertIsNotNone(ctx.output_dir)
        self.assertEqual(ctx.output_dir, "outputs/Novel/My_Test_Novel")

    def test_default_output_dir_uses_content_type_and_title(self):
        for content_type, output_format in (
            ("novel", "epub"),
            ("comic", "cbz"),
            ("gallery", "cbz"),
        ):
            with self.subTest(content_type=content_type):
                request = _req(content_type=content_type, output_format=output_format)
                ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_NOVEL_HTML))
                ctx.format_definition = {"title": {"name": "h1"}}

                get_metadata_handler(ctx, {})

                expected_title = "My Test Novel" if content_type == "gallery" else "My_Test_Novel"
                self.assertEqual(ctx.output_dir, f"outputs/{content_type.capitalize()}/{expected_title}")

    def test_gallery_title_uses_legacy_sanitization(self):
        request = _req(content_type="gallery", output_format="cbz")
        ctx = _ctx(request=request, raw_page=_mock_raw_page("<h1>A: B / C</h1>"))
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {})
        self.assertEqual(ctx.output_dir, "outputs/Gallery/A B C")

    def test_output_dir_preserved_when_set(self):
        request = _req(url="https://example.com/novel/test")
        ctx = _ctx(request=request, raw_page=_mock_raw_page(METADATA_NOVEL_HTML))
        ctx.output_dir = "custom/output"
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {})
        self.assertEqual(ctx.output_dir, "custom/output")

    def test_no_raw_page_raises(self):
        ctx = _ctx(request=_req(), raw_page=None)
        ctx.format_definition = {"title": {"name": "h1"}}
        with self.assertRaises(InvalidFlowError):
            get_metadata_handler(ctx, {})

    def test_uses_step_url_over_request_url(self):
        html = '<html><body><h1>Step Title</h1></body></html>'
        mock = _mock_raw_page(html)
        request = _req(url="https://example.com/novel/a")
        ctx = _ctx(request=request, raw_page=mock)
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {"url": "https://example.com/novel/b"})
        self.assertEqual(ctx.metadata.source_url, "https://example.com/novel/b")

    def test_genres_with_container(self):
        html = """
        <html><body>
        <h1>X</h1>
        <div class="genre-box">
            <span class="tag">Romance</span>
            <span class="tag">Drama</span>
        </div>
        </body></html>
        """
        mock = _mock_raw_page(html)
        request = _req(url="https://example.com/x")
        ctx = _ctx(request=request, raw_page=mock)
        ctx.format_definition = {
            "title": {"name": "h1"},
            "genre": {
                "name": "span",
                "class_": "tag",
                "container": {"name": "div", "class_": "genre-box"},
            },
        }
        get_metadata_handler(ctx, {})
        self.assertEqual(ctx.metadata.genres, ("Romance", "Drama"))

    def test_description_html_mode(self):
        html = '<html><body><h1>X</h1><div class="desc"><p>Hello</p><p>World</p></div></body></html>'
        mock = _mock_raw_page(html)
        request = _req(url="https://example.com/x")
        ctx = _ctx(request=request, raw_page=mock)
        ctx.format_definition = {
            "title": {"name": "h1"},
            "description": {"name": "div", "class_": "desc", "text": False},
        }
        get_metadata_handler(ctx, {})
        self.assertIn("<p>", ctx.metadata.description)

    def test_missing_optional_fields_graceful(self):
        html = '<html><body><h1>Title Only</h1></body></html>'
        mock = _mock_raw_page(html)
        request = _req(url="https://example.com/x")
        ctx = _ctx(request=request, raw_page=mock)
        ctx.format_definition = {"title": {"name": "h1"}}
        get_metadata_handler(ctx, {})
        self.assertEqual(ctx.metadata.title, "Title Only")
        self.assertEqual(ctx.metadata.author, "")
        self.assertEqual(ctx.metadata.cover_url, "")
        self.assertEqual(ctx.metadata.genres, ())
        self.assertEqual(ctx.metadata.description, "")


# ---------------------------------------------------------------------------
# volumes_prepare tests
# ---------------------------------------------------------------------------


def _make_format(
    vol_section=None,
    chapter_list=None,
    chapter_list_order=None,
    gallery_links=None,
    vol_title=None,
    vol_cover=None,
    vol_chap_list=None,
    no_vol_group=False,
) -> dict:
    """Build a minimal format definition dict for volumes_prepare tests."""
    fmt: dict = {}
    if not no_vol_group:
        fmt["vol_group"] = {
            "vol_section": vol_section if vol_section is not None else {"name": "div", "class_": "vol-section"},
            "vol_title": vol_title if vol_title is not None else {"name": "h3", "class_": "vol-title"},
            "vol_cover": vol_cover if vol_cover is not None else {},
            "chapter_list": vol_chap_list if vol_chap_list is not None else {"name": "ul", "class_": "chap-list"},
        }
    if chapter_list is not None:
        fmt["chapter_list"] = chapter_list
    if chapter_list_order:
        fmt["chapter_list_order"] = chapter_list_order
    if gallery_links is not None:
        fmt["gallery_links"] = gallery_links
    return fmt


class TestVolumesPrepare(unittest.TestCase):
    """volumes_prepare extracts volumes and chapters."""

    def test_real_volumes_from_vol_section(self):
        request = _req(url="https://example.com/novel/test")
        metadata = Metadata(title="Test", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 2)
        # Disabled volume 3 is excluded
        vol0 = ctx.volumes[0]
        vol1 = ctx.volumes[1]
        self.assertEqual(vol0.title, "Volume 1")
        self.assertEqual(vol1.title, "Volume 2")
        self.assertFalse(vol0.synthetic)
        self.assertFalse(vol1.synthetic)
        self.assertEqual(len(vol0.chapters), 2)
        self.assertEqual(len(vol1.chapters), 2)

    def test_relative_links_resolved(self):
        request = _req(url="https://example.com/novel/test")
        metadata = Metadata(title="Test", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        ch1_url = ctx.volumes[0].chapters[0].url
        self.assertTrue(ch1_url.startswith("https://example.com/"))

    def test_disabled_volumes_excluded(self):
        request = _req(url="https://example.com/novel/test")
        metadata = Metadata(title="Test", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        vol_titles = [v.title for v in ctx.volumes]
        self.assertNotIn("Volume 3 (disabled)", vol_titles)

    def test_synthetic_volume_for_root_chapter_list(self):
        request = _req(url="https://example.com/novel/flat")
        metadata = Metadata(title="Flat Novel", source_url="https://example.com/novel/flat")
        # The pagination anchor on page1 points to /chapter-list?page=2
        next_page_url = canonical_page_url("https://example.com/chapter-list?page=2")
        mock = _mock_raw_page({
            next_page_url: ROOT_CHAPTER_LIST_HTML_PAGE2,
        })
        ctx = _ctx(
            request=request,
            main_page_html=ROOT_CHAPTER_LIST_HTML_PAGE1,
            metadata=metadata,
            raw_page=mock,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
                "pagination": {"name": "div", "class_": "pagination"},
                "max_pages": 2,
            },
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 1)
        vol = ctx.volumes[0]
        self.assertTrue(vol.synthetic)
        self.assertEqual(vol.title, "")
        self.assertTrue(vol.cover_url.startswith("generated://default-cover/"))
        # Pagination should discover page 2 as well
        all_urls = [ch.url for ch in vol.chapters]
        self.assertTrue(any("/ch/10" in u for u in all_urls))
        self.assertTrue(any("/ch/6" in u or "/ch/7" in u for u in all_urls))

    def test_root_pagination_obeys_max_pages_and_finds_sibling_navigation(self):
        html = """
        <div class="chapter-list"><a href="/ch/1">One</a></div>
        <nav class="pagination">
          <a href="/list?p=2">Two</a><a href="/list?p=3">Three</a>
        </nav>
        """
        request = _req(url="https://example.com/list")
        raw_page = _mock_raw_page({})
        ctx = _ctx(
            request=request,
            main_page_html=html,
            metadata=Metadata(title="X", source_url=request.url),
            raw_page=raw_page,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
                "pagination": {"name": "nav", "class_": "pagination"},
                "max_pages": 1,
            },
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})
        self.assertEqual(raw_page.get_raw_page.call_count, 0)
        self.assertEqual([c.url for c in ctx.volumes[0].chapters], ["https://example.com/ch/1"])

    def test_deduplication_preserves_first(self):
        request = _req(url="https://example.com/novel/dupe")
        metadata = Metadata(title="Dupe", source_url="https://example.com/novel/dupe")
        ctx = _ctx(
            request=request,
            main_page_html=ROOT_CHAPTER_LIST_DUPES_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
            },
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})

        urls = [ch.url for ch in ctx.volumes[0].chapters]
        # /ch/1 should appear only once
        ch1_count = sum(1 for u in urls if "/ch/1" in u)
        self.assertEqual(ch1_count, 1)
        # 2 unique URLs total (/ch/1, /ch/2)
        self.assertEqual(len(urls), 2)

    def test_relative_and_cross_host_filtered(self):
        request = _req(url="https://example.com/novel/rel")
        metadata = Metadata(title="Rel", source_url="https://example.com/novel/rel")
        ctx = _ctx(
            request=request,
            main_page_html=ROOT_CHAPTER_RELATIVE_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
            },
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})

        urls = [ch.url for ch in ctx.volumes[0].chapters]
        # https://other.com/ch/3 should be excluded (cross-host)
        self.assertFalse(any("other.com" in u for u in urls))
        self.assertEqual(len(urls), 2)

    def test_oldest_first_ordering(self):
        request = _req(url="https://example.com/novel/order")
        metadata = Metadata(title="Order", source_url="https://example.com/novel/order")
        html = """
        <html><body>
        <div class="vol-section">
            <h3 class="vol-title">V1</h3>
            <ul class="chap-list">
                <li><a href="/ch/3">Ch 3</a></li>
                <li><a href="/ch/2">Ch 2</a></li>
                <li><a href="/ch/1">Ch 1</a></li>
            </ul>
        </div>
        </body></html>
        """
        ctx = _ctx(request=request, main_page_html=html, metadata=metadata)
        ctx.format_definition = _make_format(chapter_list_order="oldest_first")
        volumes_prepare_handler(ctx, {})

        urls = [ch.url for ch in ctx.volumes[0].chapters]
        # oldest_first = no reversal, keeps DOM order
        self.assertTrue(urls[0].endswith("/ch/3"))

    def test_newest_first_reverses_order(self):
        request = _req(url="https://example.com/novel/newest")
        metadata = Metadata(title="Newest", source_url="https://example.com/novel/newest")
        ctx = _ctx(
            request=request,
            main_page_html=NEWEST_FIRST_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
            },
            chapter_list_order="newest_first",
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})

        urls = [ch.url for ch in ctx.volumes[0].chapters]
        # newest_first reverses the list → ch/98, ch/99, ch/100
        self.assertTrue(urls[0].endswith("/ch/98"))
        self.assertTrue(urls[-1].endswith("/ch/100"))

    def test_decimal_identifiers_preserved(self):
        request = _req(url="https://example.com/novel/decimal")
        metadata = Metadata(title="Decimal", source_url="https://example.com/novel/decimal")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_DECIMAL_IDS_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        ctx.format_definition["chapter_naming"] = {
            "source": "text",
            "regex": r"(\d+(?:\.\d+)?)",
            "display_template": "{identifier}",
        }
        volumes_prepare_handler(ctx, {})

        all_identifiers = []
        for vol in ctx.volumes:
            for ch in vol.chapters:
                all_identifiers.append(ch.identifier)
        self.assertEqual(all_identifiers, ["1.1", "1.5", "2", "12.5"])

    def test_ordinals_assigned_globally(self):
        request = _req(url="https://example.com/novel/ord")
        metadata = Metadata(title="Ord", source_url="https://example.com/novel/ord")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        ordinals = []
        for vol in ctx.volumes:
            for ch in vol.chapters:
                ordinals.append(ch.ordinal)
        self.assertEqual(ordinals, [1, 2, 3, 4])

    def test_gallery_synthetic_volume(self):
        request = _req(content_type="gallery", output_format="folder", url="https://ehentai.gallery/g/123")
        metadata = Metadata(title="Gallery", source_url="https://ehentai.gallery/g/123")
        mock = MagicMock()
        mock.get_raw_page.return_value = GALLERY_HTML
        ctx = _ctx(
            request=request,
            main_page_html=GALLERY_HTML,
            metadata=metadata,
            raw_page=mock,
        )
        ctx.format_definition = {
            "gallery_links": {
                "container": {"name": "div", "class_": "gallery-grid"},
                "link": {"name": "a"},
            },
        }
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 1)
        vol = ctx.volumes[0]
        self.assertTrue(vol.synthetic)
        self.assertEqual(vol.index, 0)
        self.assertEqual(len(vol.chapters), 3)
        # Each chapter is a picture page URL
        urls = [ch.url for ch in vol.chapters]
        self.assertTrue(any("/pic/001" in u for u in urls))

    def test_gallery_pagination_reuses_cached_page_and_filters_external_links(self):
        page2 = '<div class="gallery-grid"><a href="/pic/2">Two</a></div>'
        request = _req(content_type="gallery", output_format="folder", url="https://example.com/gallery")
        raw_page = _mock_raw_page({"https://example.com/gallery?p=2": page2})
        ctx = _ctx(
            request=request,
            main_page_html="""
              <div class="gallery-grid"><a href="/pic/1">One</a><a href="https://evil.example/p">Bad</a></div>
              <nav class="pages"><a href="?p=2">Two</a></nav>
            """,
            metadata=Metadata(title="Gallery", source_url=request.url),
            raw_page=raw_page,
        )
        ctx.format_definition = {
            "gallery_links": {
                "container": {"name": "div", "class_": "gallery-grid"},
                "link": {"name": "a"},
                "pagination": {
                    "container": {"name": "nav", "class_": "pages"},
                    "max_pages": 2,
                },
            },
        }
        volumes_prepare_handler(ctx, {})
        self.assertEqual(raw_page.get_raw_page.call_count, 1)
        self.assertEqual(
            [c.url for c in ctx.volumes[0].chapters],
            ["https://example.com/pic/1", "https://example.com/pic/2"],
        )

    def test_empty_chapter_list(self):
        request = _req(url="https://example.com/novel/empty")
        metadata = Metadata(title="Empty", source_url="https://example.com/novel/empty")
        ctx = _ctx(
            request=request,
            main_page_html=NO_VOL_NO_CHAPTER_LIST_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format(
            chapter_list={
                "container": {"name": "div", "class_": "chapter-list"},
                "link": {"name": "a"},
            },
            no_vol_group=True,
        )
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 1)
        self.assertEqual(len(ctx.volumes[0].chapters), 0)

    def test_requires_metadata(self):
        ctx = _ctx(main_page_html="<html></html>", metadata=None)
        ctx.format_definition = _make_format(no_vol_group=True)
        with self.assertRaises(InvalidFlowError):
            volumes_prepare_handler(ctx, {})

    def test_requires_main_page_html(self):
        metadata = Metadata(title="X")
        ctx = _ctx(main_page_html=None, metadata=metadata)
        ctx.format_definition = _make_format(no_vol_group=True)
        with self.assertRaises(InvalidFlowError):
            volumes_prepare_handler(ctx, {})

    def test_missing_chapter_strategy_raises(self):
        request = _req(url="https://example.com/x")
        ctx = _ctx(
            request=request,
            main_page_html="<h1>X</h1>",
            metadata=Metadata(title="X", source_url=request.url),
        )
        with self.assertRaises(InvalidFlowError):
            volumes_prepare_handler(ctx, {})

    def test_cover_fallback_to_metadata_cover(self):
        html = """
        <html><body>
        <div class="vol-section">
            <h3 class="vol-title">V1</h3>
            <ul class="chap-list">
                <li><a href="/ch/1">Ch 1</a></li>
            </ul>
        </div>
        </body></html>
        """
        request = _req(url="https://example.com/x")
        metadata = Metadata(
            title="X",
            cover_url="https://example.com/work-cover.jpg",
            source_url="https://example.com/x",
        )
        ctx = _ctx(request=request, main_page_html=html, metadata=metadata)
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        vol = ctx.volumes[0]
        self.assertEqual(vol.cover_url, "https://example.com/work-cover.jpg")

    def test_missing_cover_uses_deterministic_generated_reference(self):
        request = _req(url="https://example.com/x")
        metadata = Metadata(title="X", author="A", source_url=request.url)
        ctx = _ctx(request=request, main_page_html=VOL_SECTION_HTML, metadata=metadata)
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})
        self.assertTrue(ctx.volumes[0].cover_url.startswith("generated://default-cover/"))
        self.assertEqual(ctx.volumes[0].cover_url, ctx.volumes[1].cover_url)

    def test_volume_links_are_document_relative_and_deduplicated_globally(self):
        html = """
        <div class="vol-section"><ul class="chap-list">
          <li><a href="chapter/1">First</a></li>
        </ul></div>
        <div class="vol-section"><ul class="chap-list">
          <li><a href="https://EXAMPLE.com/novel/chapter/1#again">Duplicate</a></li>
        </ul></div>
        """
        request = _req(url="https://example.com/novel/index.html")
        ctx = _ctx(
            request=request,
            main_page_html=html,
            metadata=Metadata(title="X", source_url=request.url),
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})
        links = [chapter.url for volume in ctx.volumes for chapter in volume.chapters]
        self.assertEqual(links, ["https://example.com/novel/chapter/1"])

    def test_log_message_on_success(self):
        logs: list[str] = []
        request = _req(url="https://example.com/x")
        metadata = Metadata(title="X", source_url="https://example.com/x")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
            log_fn=lambda msg: logs.append(msg),
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})
        self.assertTrue(any("Volumes prepared:" in m for m in logs))


class TestSelection(unittest.TestCase):
    """Test selection (full/range/single) applied across volumes."""

    def test_full_keeps_all(self):
        request = _req(selection="full")
        metadata = Metadata(title="T", source_url="https://example.com/t")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        total = sum(len(v.chapters) for v in ctx.volumes)
        self.assertEqual(total, 4)

    def test_range_filters_chapters(self):
        request = _req(selection="range", start_index=2, end_index=3)
        metadata = Metadata(title="T", source_url="https://example.com/t")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        ordinals = [ch.ordinal for vol in ctx.volumes for ch in vol.chapters]
        self.assertEqual(ordinals, [2, 3])

    def test_single_chapter_by_url(self):
        request = _req(
            selection="single",
            chapter_url="https://example.com/chapter/2",
        )
        metadata = Metadata(title="T", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 1)
        self.assertEqual(len(ctx.volumes[0].chapters), 1)
        self.assertIn("/chapter/2", ctx.volumes[0].chapters[0].url)

    def test_range_across_volumes(self):
        request = _req(selection="range", start_index=1, end_index=4)
        metadata = Metadata(title="T", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        # All 4 chapters across 2 volumes
        total = sum(len(v.chapters) for v in ctx.volumes)
        self.assertEqual(total, 4)

    def test_range_partial_volume(self):
        request = _req(selection="range", start_index=3, end_index=3)
        metadata = Metadata(title="T", source_url="https://example.com/novel/test")
        ctx = _ctx(
            request=request,
            main_page_html=VOL_SECTION_HTML,
            metadata=metadata,
        )
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        self.assertEqual(len(ctx.volumes), 1)
        self.assertEqual(ctx.volumes[0].chapters[0].ordinal, 3)


class TestVolumeCoverWithVolCover(unittest.TestCase):
    """Volume-specific cover URLs from vol_group.vol_cover selector."""

    def test_volume_cover_extracted(self):
        html = """
        <html><body>
        <div class="vol-section">
            <h3 class="vol-title">V1</h3>
            <img class="vol-cover" src="https://example.com/vol1.jpg" />
            <ul class="chap-list">
                <li><a href="/ch/1">Ch 1</a></li>
            </ul>
        </div>
        <div class="vol-section">
            <h3 class="vol-title">V2</h3>
            <img class="vol-cover" src="https://example.com/vol2.jpg" />
            <ul class="chap-list">
                <li><a href="/ch/2">Ch 2</a></li>
            </ul>
        </div>
        </body></html>
        """
        request = _req(url="https://example.com/novel/covers")
        metadata = Metadata(title="Covers", source_url="https://example.com/novel/covers")
        ctx = _ctx(request=request, main_page_html=html, metadata=metadata)
        ctx.format_definition = _make_format(
            vol_cover={"name": "img", "class_": "vol-cover", "other_attr": "src"},
        )
        volumes_prepare_handler(ctx, {})

        self.assertEqual(ctx.volumes[0].cover_url, "https://example.com/vol1.jpg")
        self.assertEqual(ctx.volumes[1].cover_url, "https://example.com/vol2.jpg")


class TestRegistration(unittest.TestCase):
    """Handler registration works correctly."""

    def test_register_preparation_installs_real_handlers(self):
        # Register explicitly so this test is order-independent (flow tests
        # reset the registry via _register_defaults during teardown).
        register_preparation_handlers()
        from api import get_module
        meta_spec = get_module("get_metadata")
        vol_spec = get_module("volumes_prepare")
        self.assertEqual(meta_spec.handler.__name__, "get_metadata_handler")
        self.assertEqual(vol_spec.handler.__name__, "volumes_prepare_handler")

    def test_re_registration_idempotent(self):
        register_preparation_handlers()
        from api import get_module
        meta_spec = get_module("get_metadata")
        self.assertEqual(meta_spec.handler.__name__, "get_metadata_handler")


class TestVolumeCoverFromMetaTag(unittest.TestCase):
    """Cover URL extraction when the selector uses attrs dict."""

    def test_cover_from_og_image_meta(self):
        request = _req(url="https://example.com/novel/og")
        mock = _mock_raw_page(METADATA_COVER_FROM_META_HTML)
        ctx = _ctx(request=request, main_page_html=METADATA_COVER_FROM_META_HTML, raw_page=mock)
        ctx.format_definition = {
            "title": {"name": "h1"},
            "cover": {"name": "meta", "attrs": {"property": "og:image"}, "other_attr": "content"},
            "vol_group": {
                "vol_section": {"name": "div", "class_": "vol-section"},
                "vol_title": {"name": "h3"},
                "vol_cover": {},
                "chapter_list": {"name": "ul"},
            },
        }
        get_metadata_handler(ctx, {"url": "https://example.com/novel/og"})
        self.assertEqual(ctx.metadata.cover_url, "https://example.com/meta-cover.png")


class TestPositionInVolume(unittest.TestCase):
    """position_in_volume is 1-based within each volume."""

    def test_positions_restart_per_volume(self):
        request = _req(url="https://example.com/novel/test")
        metadata = Metadata(title="T", source_url="https://example.com/novel/test")
        ctx = _ctx(request=request, main_page_html=VOL_SECTION_HTML, metadata=metadata)
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        vol0_positions = [ch.position_in_volume for ch in ctx.volumes[0].chapters]
        vol1_positions = [ch.position_in_volume for ch in ctx.volumes[1].chapters]
        self.assertEqual(vol0_positions, [1, 2])
        self.assertEqual(vol1_positions, [1, 2])


class TestVolumeIndex(unittest.TestCase):
    """Volume index is 0-based in DOM order."""

    def test_volume_indices(self):
        request = _req(url="https://example.com/novel/test")
        metadata = Metadata(title="T", source_url="https://example.com/novel/test")
        ctx = _ctx(request=request, main_page_html=VOL_SECTION_HTML, metadata=metadata)
        ctx.format_definition = _make_format()
        volumes_prepare_handler(ctx, {})

        indices = [v.index for v in ctx.volumes]
        self.assertEqual(indices, [0, 1])


if __name__ == "__main__":
    unittest.main()
