"""Metadata extraction and volume/chapter preparation.

Task 3 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

This module implements the ``get_metadata`` and ``volumes_prepare`` flow module
handlers.  Both operate on pre-validated :class:`CrawlContext` objects and
modify them in-place:

* ``get_metadata`` fetches the main page, extracts title, author, cover,
  description, genres, and other-info, and populates ``context.metadata`` and
  ``context.main_page_html``.
* ``volumes_prepare`` consumes the cached main-page HTML (or fetches
  sub-pages for gallery pagination) and populates ``context.volumes`` as a
  list of :class:`Volume` objects each containing ordered :class:`Chapter`
  tuples.

Design rules
------------
* Both handlers are **pure relative to the filesystem**: they never create
  output directories or write files.
* The selector schema (see ``data/template/site.json`` and
  ``data/template/gallery.json``) drives all extraction.
* Duplicate chapter URLs are deduplicated preserving first occurrence.
* Cover URLs are resolved but not downloaded; download is deferred to
  ``create_ebook``.
* The ``click`` selector is ignored in requests mode (no live driver);
  genre/description expansion via click is not supported here.
* ``main_page_html`` is cached on the context to avoid fetching the same
  page twice (get_metadata → volumes_prepare share it).

Module registration
-------------------
Call ``register_preparation_handlers()`` once at import time to upgrade
the canonical ``get_metadata`` / ``volumes_prepare`` entries in the
flow registry with real implementations.  Importing this module is
sufficient.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from api.contracts import (
    CrawlContext,
    Chapter,
    ContentType,
    IncompleteCrawlError,
    InvalidFlowError,
    InvalidRequestError,
    Metadata,
    Volume,
    default_output_root,
)
from api.flow import set_module_handler

# ---------------------------------------------------------------------------
# Selector utilities
# ---------------------------------------------------------------------------

_NON_FIND_KEYS = frozenset({
    "other_attr",
    "container",
    "click",
    "vol_name",
    "text",
    "allowed_hosts",
    "delete",
    "class_prefix",
    "id_prefix",
    "pagination",
    "link",
    "max_pages",
    "page_param",
    "image",
})


def selector_args(selector: dict | None) -> dict:
    """Convert a format selector dict to bs4 ``find``/``find_all`` kwargs.

    Strips non-find keys, expands the ``attrs`` dict into direct kwargs,
    and omits blank values.
    """
    if not isinstance(selector, dict) or not selector:
        return {}
    result: dict[str, object] = {}
    for key, value in selector.items():
        if key in _NON_FIND_KEYS:
            continue
        if value is None or value == "":
            continue
        if key == "attrs":
            if isinstance(value, dict):
                result.update(value)
            continue
        result[key] = value
    return result


def find_element(soup: BeautifulSoup, selector: dict | None):
    """Return the first element matching *selector* or ``None``."""
    args = selector_args(selector)
    if not args:
        return None
    return soup.find(**args)


def find_all_elements(soup: BeautifulSoup, selector: dict | None) -> list:
    """Return all elements matching *selector*."""
    args = selector_args(selector)
    if not args:
        return []
    return soup.find_all(**args)


def read_attribute(element, other_attr: str) -> str:
    """Read *other_attr* from *element*; fall back to a JS-call regex."""
    if not other_attr:
        return ""
    value = element.get(other_attr, "")
    if value:
        return value.strip()
    match = re.search(rf"{re.escape(other_attr)}\(['\"]([^'\"]+)['\"]\)", str(element))
    return match.group(1).strip() if match else ""


def resolve_url(base_url: str, href: str) -> str:
    """Resolve *href* against the actual page URL that contains it."""
    if not href:
        return ""
    href = href.strip()
    return urljoin(base_url, href) if base_url else href


def canonical_page_url(url: str) -> str:
    """Return a safe canonical HTTP(S) URL for deduplication.

    Fragments and credentials never identify a crawled page.  Default ports
    are also removed, so equivalent links deduplicate reliably.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return ""
    scheme = parsed.scheme.lower()
    host = host.lower()
    host_part = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if scheme == "https" else 80):
        host_part = f"{host_part}:{port}"
    return urlunsplit((scheme, host_part, parsed.path or "/", parsed.query, ""))


def _normalized_hosts(base_url: str, configured: object = None) -> set[str]:
    """Return the source host plus explicitly allowlisted chapter hosts."""
    hosts: set[str] = set()
    host = urlsplit(base_url).hostname
    if host:
        hosts.add(host.lower().removeprefix("www."))
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, (list, tuple, set)):
        for item in configured:
            if isinstance(item, str) and item.strip():
                parsed = urlsplit(item if "://" in item else f"https://{item}")
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower().removeprefix("www."))
    return hosts


def _resolved_page_url(
    base_url: str,
    href: str,
    *,
    allowed_hosts: set[str] | None = None,
) -> str:
    """Resolve an extracted page link, accepting only safe allowed HTTP(S) URLs."""
    resolved = canonical_page_url(resolve_url(base_url, href))
    if not resolved:
        return ""
    if allowed_hosts is not None:
        host = urlsplit(resolved).hostname
        if not host or host.lower().removeprefix("www.") not in allowed_hosts:
            return ""
    return resolved


def is_empty_selector(selector: dict | None) -> bool:
    """Return ``True`` if *selector* has no usable (non-blank) find keys."""
    if not isinstance(selector, dict) or not selector:
        return True
    for key, value in selector.items():
        if key in _NON_FIND_KEYS:
            continue
        if key == "attrs":
            if isinstance(value, dict) and value:
                return False
            continue
        if value and str(value).strip():
            return False
    return True


def selector_to_css(selector: dict | None) -> str | None:
    """Minimal CSS string from a selector dict (for fetch readiness)."""
    if not isinstance(selector, dict) or not selector:
        return None
    parts: list[str] = []
    name = selector.get("name") or ""
    class_ = selector.get("class_") or ""
    id_ = selector.get("id") or ""
    itemprop = selector.get("itemprop") or ""
    if name:
        parts.append(name)
    if class_:
        for cls in class_.split():
            parts.append(f".{cls}")
    if id_:
        parts.append(f"#{id_}")
    if itemprop:
        parts.append(f'[itemprop="{itemprop}"]')
    attrs = selector.get("attrs")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            if isinstance(key, str) and isinstance(value, (str, int, float)):
                escaped = str(value).replace('"', '\\"')
                parts.append(f'[{key}="{escaped}"]')
    return "".join(parts) if parts else None


def _text_of(element) -> str:
    """Return ``.text.strip()`` or ``""``."""
    if element is None:
        return ""
    return element.text.strip()


def _link_target(anchor, selector: dict | None = None) -> str:
    """Read a configured link attribute, defaulting to standard ``href``."""
    attribute = selector.get("other_attr") if isinstance(selector, dict) else None
    return read_attribute(anchor, str(attribute or "href"))


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def _extract_title(soup: BeautifulSoup, title_selector: dict, base_url: str) -> str:
    """Extract the work title using the format's ``title`` selector."""
    el = find_element(soup, title_selector)
    if el is None:
        return ""
    # Prefer concatenated direct text children (matches existing get_title logic)
    direct_texts = [t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]
    if direct_texts:
        title = " ".join(direct_texts).replace('\u201c', "'").replace('\u201d', "'")
        if title:
            return title
    return el.text.strip() or ""


def _extract_cover_url(
    soup: BeautifulSoup,
    cover_selector: dict | None,
    base_url: str,
) -> str:
    """Extract the cover image URL using the format's ``cover`` selector."""
    if not cover_selector or is_empty_selector(cover_selector):
        return ""
    other_attr = cover_selector.get("other_attr") or "src"
    el = find_element(soup, cover_selector)
    if el is None:
        return ""
    return _resolved_page_url(base_url, read_attribute(el, other_attr))


def _extract_genres(soup: BeautifulSoup, genre_selector: dict | None) -> tuple[str, ...]:
    """Extract genre labels."""
    if not genre_selector or is_empty_selector(genre_selector):
        return ()
    container_selector = genre_selector.get("container")
    # click is ignored in requests mode (no live driver)
    search_from = find_element(soup, container_selector) if container_selector else soup
    if search_from is None:
        return ()
    # Use the same selector conversion as every other extraction path.  In
    # particular, ``other_attr`` and ``text`` are metadata directives, not
    # BeautifulSoup search arguments.
    genre_find_args = selector_args(genre_selector)
    if not genre_find_args:
        return ()
    elements = search_from.find_all(**genre_find_args)
    return tuple(el.text.strip() for el in elements if el.text.strip())


def _extract_description(soup: BeautifulSoup, desc_selector: dict | None) -> str:
    """Extract description text or HTML based on the ``text`` flag."""
    if not desc_selector or is_empty_selector(desc_selector):
        return ""
    text_flag = desc_selector.get("text", True)
    el = find_element(soup, desc_selector)
    if el is None:
        return ""
    if text_flag:
        return el.text.strip()
    return el.prettify().strip()


def _extract_other_info(
    soup: BeautifulSoup, other_info_selector: dict | None, base_url: str
) -> tuple[str, dict[str, str]]:
    """Extract author and other-info dict from ``holder``/``value`` selectors.

    Returns ``(author, other_info_dict)``.
    """
    if not other_info_selector or not isinstance(other_info_selector, dict):
        return "", {}
    holder_selector = other_info_selector.get("holder")
    value_selector = other_info_selector.get("value")
    if not holder_selector or not value_selector:
        return "", {}
    holders = find_all_elements(soup, holder_selector)
    values = find_all_elements(soup, value_selector)
    if not holders or not values:
        return "", {}
    # Strip common label punctuation: ^ : < | >
    _LABEL_STRIP_RE = re.compile(r'[\^:<|>]+')
    other_info: dict[str, str] = {}
    author = ""
    count = min(len(holders), len(values))
    for i in range(count):
        key = _LABEL_STRIP_RE.sub('', holders[i].text.strip()).strip()
        val = values[i].text.strip()
        if not key:
            continue
        if key.lower() in ("tác giả", "author"):
            author = val
        else:
            other_info[key] = val
    return author, other_info


def get_metadata_handler(context: CrawlContext, params: dict) -> None:
    """Fetch the main page and extract work-level metadata.

    Populates ``context.metadata`` and caches the HTML on
    ``context.main_page_html`` so ``volumes_prepare`` can reuse it.
    """
    fmt = context.format_definition
    request = context.request
    raw_page = context.raw_page
    if raw_page is None:
        raise InvalidFlowError("raw_page service is not initialised on the context.")

    title_selector = fmt.get("title")
    if not title_selector or is_empty_selector(title_selector):
        raise InvalidFlowError("format requires a 'title' selector to extract metadata.")

    expected_css = selector_to_css(title_selector)
    url = params.get("url") or request.url

    html = raw_page.get_raw_page(url, expected_css)
    context.main_page_html = html

    soup = BeautifulSoup(html, "html.parser")
    # ``urljoin`` must use the page URL, not merely its origin: a link such as
    # ``images/cover.jpg`` is relative to the page directory.
    base_url = url

    # -- Title --
    title = _extract_title(soup, title_selector, base_url)
    if not title:
        raise IncompleteCrawlError("could not extract a non-empty title from the main page.")
    if not context.output_dir:
        context.output_dir = request.output_dir or os.path.join(
            default_output_root(request.content_type),
            _safe_gallery_dirname(title) if request.content_type is ContentType.GALLERY else _safe_dirname(title),
        )

    # -- Cover --
    cover_url = _extract_cover_url(soup, fmt.get("cover"), base_url)

    # -- Genres --
    genres = _extract_genres(soup, fmt.get("genre"))

    # -- Description --
    description = _extract_description(soup, fmt.get("description"))

    # -- Author + other_info --
    author, other_info = _extract_other_info(soup, fmt.get("other_info"), base_url)

    context.metadata = Metadata(
        title=title,
        author=author,
        description=description,
        genres=genres,
        other_info=other_info,
        cover_url=cover_url,
        source_url=url,
    )

    if context.log:
        context.log(f"Metadata extracted: title={title!r}, cover_present={bool(cover_url)}")


def _generated_cover_reference(title: str, author: str = "") -> str:
    """Return the stable logical reference for the later generated-cover step.

    Preparation deliberately performs no filesystem writes.  The exporter can
    materialize this reference with the existing deterministic cover generator
    while every normalized volume already has an explicit fallback value.
    """
    digest = hashlib.sha256(f"{title}\0{author}".encode("utf-8")).hexdigest()[:24]
    return f"generated://default-cover/{digest}.png"


def _safe_dirname(title: str) -> str:
    """Replace OS-unsafe characters for a directory name."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', title)
    safe = re.sub(r'\s+', '_', safe)
    return safe.strip('. ') or "untitled"


def _safe_gallery_dirname(title: str) -> str:
    """Use the legacy gallery title sanitization (preserving word spaces)."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
    return re.sub(r'\s+', ' ', safe).strip(' .') or "gallery"


# ---------------------------------------------------------------------------
# Volume and chapter preparation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ChapterCandidate:
    """An extracted page URL plus its source label before ordinal assignment."""

    url: str
    label: str = ""
    anchor_html: str = ""


def _extract_volume_sections(
    soup: BeautifulSoup,
    fmt: dict,
    base_url: str,
) -> list[dict[str, object]]:
    """Extract volumes from ``vol_group.vol_section`` on the main page.

    Returns a list of dicts ``{title, cover_url, chapter_links}`` in DOM order.
    """
    vol_group = fmt.get("vol_group") or {}
    if not isinstance(vol_group, dict):
        return []
    vol_section = vol_group.get("vol_section")
    if not vol_section or is_empty_selector(vol_section):
        return []

    vol_title = vol_group.get("vol_title") if isinstance(vol_group.get("vol_title"), dict) else {}
    vol_cover = vol_group.get("vol_cover") if isinstance(vol_group.get("vol_cover"), dict) else {}
    vol_chap = vol_group.get("chapter_list") if isinstance(vol_group.get("chapter_list"), dict) else {}

    # Find all volume sections, excluding disabled ones
    sections = find_all_elements(soup, vol_section)
    # Build a disabled-variant selector to filter locked/hidden volumes
    disabled_selector = {**selector_args(vol_section), "class_": "disabled"}
    disabled_sections = set(soup.find_all(**disabled_selector)) if disabled_selector else set()
    sections = [s for s in sections if s not in disabled_sections]

    result: list[dict[str, object]] = []
    for section in sections:
        # Title
        if "vol_name" in vol_title:
            v_title = str(vol_title["vol_name"])
        else:
            title_el = find_element(section, vol_title) if vol_title else None
            v_title = _text_of(title_el)

        # Cover
        v_cover = ""
        if vol_cover and not is_empty_selector(vol_cover):
            cover_other_attr = vol_cover.get("other_attr") or "src"
            cover_el = find_element(section, vol_cover)
            if cover_el is not None:
                v_cover = _resolved_page_url(
                    base_url, read_attribute(cover_el, cover_other_attr)
                )

        # Chapter links
        v_chap_args = selector_args(vol_chap)
        chap_links: list[_ChapterCandidate] = []
        allowed_hosts = _normalized_hosts(base_url, vol_chap.get("allowed_hosts"))
        if v_chap_args:
            chap_container = section.find(**v_chap_args)
            if chap_container is not None:
                for anchor in chap_container.find_all("a"):
                    href = _link_target(anchor)
                    chapter_url = _resolved_page_url(
                        base_url, href, allowed_hosts=allowed_hosts
                    )
                    if chapter_url:
                        chap_links.append(
                            _ChapterCandidate(chapter_url, _text_of(anchor), str(anchor))
                        )

        result.append({
            "title": v_title,
            "cover_url": v_cover,
            "chapter_links": chap_links,
        })

    return result


def _discover_root_chapters(
    soup: BeautifulSoup,
    fmt: dict,
    base_url: str,
    raw_page,
    start_url: str,
    log_fn=None,
) -> list[_ChapterCandidate]:
    """Discover chapter links from a root ``chapter_list`` with pagination.

    Returns a deduplicated flat list of chapter URLs in DOM order across
    all discovered pages.
    """
    chapter_list = fmt.get("chapter_list") or {}
    if not chapter_list or not isinstance(chapter_list, dict):
        return []

    container_selector = chapter_list.get("container", chapter_list)
    if not isinstance(container_selector, dict):
        container_selector = chapter_list
    # Strip non-find keys from the container
    container_args = selector_args(container_selector)

    link_selector = chapter_list.get("link") or {"name": "a"}
    link_args = selector_args(link_selector)

    pagination_selector = chapter_list.get("pagination") or {}
    pagination_args = selector_args(pagination_selector) if pagination_selector else {}

    try:
        max_pages = int(chapter_list.get("max_pages", 250) or 250)
    except (TypeError, ValueError) as exc:
        raise InvalidFlowError("chapter_list.max_pages must be a positive integer.") from exc
    if max_pages < 1:
        raise InvalidFlowError("chapter_list.max_pages must be at least 1.")
    expected_css = selector_to_css(container_args) or selector_to_css(container_selector)
    allowed_hosts = _normalized_hosts(base_url, chapter_list.get("allowed_hosts"))

    # The main page has already been fetched by get_metadata.  Queue it as-is;
    # adding a trailing slash changes document-relative link resolution.
    first_page_url = canonical_page_url(start_url)
    page_queue: list[tuple[str, str | None]] = [(first_page_url, str(soup))]
    seen_pages: set[str] = {canonical_page_url(first_page_url)}
    seen_chapters: set[str] = set()
    chapter_links: list[_ChapterCandidate] = []
    processed_pages = 0

    while page_queue and processed_pages < max_pages:
        page_url, page_html = page_queue.pop(0)
        if page_html is None:
            if raw_page is None:
                raise InvalidFlowError("pagination requires an initialised raw_page service.")
            page_html = raw_page.get_raw_page(page_url, expected_css)
        processed_pages += 1

        page_soup = BeautifulSoup(page_html, "html.parser")
        container_el = page_soup.find(**container_args) if container_args else page_soup
        if container_el is None:
            continue

        # Pagination control
        pagination_el = None
        if pagination_args:
            # Pagination is frequently a sibling of the chapter container.
            pagination_el = container_el.find(**pagination_args) or page_soup.find(**pagination_args)
        pagination_anchor_ids: set[int] = set()
        if pagination_el is not None:
            pagination_anchor_ids = {id(a) for a in pagination_el.find_all("a")}

        # Chapter anchors
        for anchor in container_el.find_all(**link_args):
            href = _link_target(anchor, link_selector)
            if not href:
                continue
            if id(anchor) in pagination_anchor_ids:
                continue
            chapter_url = _resolved_page_url(page_url, href, allowed_hosts=allowed_hosts)
            if not chapter_url:
                continue
            if chapter_url in seen_chapters:
                continue
            seen_chapters.add(chapter_url)
            chapter_links.append(_ChapterCandidate(chapter_url, _text_of(anchor), str(anchor)))

        # Discover next pages
        if pagination_el is None:
            continue
        for anchor in pagination_el.find_all("a"):
            next_url = _resolved_page_url(
                page_url, _link_target(anchor), allowed_hosts=allowed_hosts
            )
            if not next_url or next_url in seen_pages:
                continue
            if len(seen_pages) >= max_pages:
                continue
            # Fetch lazily when this page is actually processed.  This keeps
            # max_pages a true request bound instead of fetching an entire
            # paginator before the loop checks its limit.
            seen_pages.add(next_url)
            page_queue.append((next_url, None))

    if log_fn and page_queue:
        log_fn(f"Stopped chapter discovery after {max_pages} pages.")

    return chapter_links


def _extract_gallery_picture_links(
    soup: BeautifulSoup,
    fmt: dict,
    base_url: str,
    raw_page,
    start_url: str,
    log_fn=None,
) -> list[_ChapterCandidate]:
    """Discover gallery picture-page links from ``gallery_links`` selector.

    Returns a deduplicated flat list of picture-page URLs in DOM order.
    """
    gallery_links = fmt.get("gallery_links") or {}
    if not gallery_links or not isinstance(gallery_links, dict):
        return []

    container_selector = gallery_links.get("container")
    link_selector = gallery_links.get("link") or {"name": "a", "other_attr": "href"}
    pagination = gallery_links.get("pagination") or {}
    if not isinstance(pagination, dict):
        raise InvalidFlowError("gallery_links.pagination must be an object when provided.")

    container_args = selector_args(container_selector) if container_selector else {}
    link_args = selector_args(link_selector)
    pagination_args = selector_args(pagination.get("container")) if pagination else {}
    pagination_link_args = selector_args(pagination.get("link") or {"name": "a"})
    try:
        max_pages = int(pagination.get("max_pages", gallery_links.get("max_pages", 250)) or 250)
    except (TypeError, ValueError) as exc:
        raise InvalidFlowError("gallery pagination max_pages must be a positive integer.") from exc
    if max_pages < 1:
        raise InvalidFlowError("gallery pagination max_pages must be at least 1.")
    allowed_hosts = _normalized_hosts(base_url, gallery_links.get("allowed_hosts"))

    # Main-page HTML is cached by get_metadata.  Follow actual pagination
    # links, rather than guessing ?p=1, so pages starting at 0 or using another
    # query shape work without a duplicate main-page request.
    all_links: list[_ChapterCandidate] = []
    seen: set[str] = set()
    first_page_url = canonical_page_url(start_url)
    page_queue: list[tuple[str, str | None]] = [(first_page_url, str(soup))]
    seen_pages = {first_page_url}
    processed_pages = 0
    while page_queue and processed_pages < max_pages:
        page_url, page_html = page_queue.pop(0)
        if page_html is None:
            if raw_page is None:
                raise InvalidFlowError("gallery pagination requires an initialised raw_page service.")
            page_html = raw_page.get_raw_page(page_url)
        processed_pages += 1
        page_soup = BeautifulSoup(page_html, "html.parser")
        container_el = page_soup.find(**container_args) if container_args else page_soup
        if container_el is not None:
            all_links.extend(
                _collect_anchor_links(
                    container_el,
                    link_selector,
                    link_args,
                    page_url,
                    seen,
                    allowed_hosts=allowed_hosts,
                )
            )
        if not pagination_args:
            continue
        pagination_el = page_soup.find(**pagination_args)
        if pagination_el is None:
            continue
        for anchor in pagination_el.find_all(**pagination_link_args):
            next_url = _resolved_page_url(
                page_url,
                _link_target(anchor, pagination.get("link")),
                allowed_hosts=allowed_hosts,
            )
            if not next_url or next_url in seen_pages or len(seen_pages) >= max_pages:
                continue
            seen_pages.add(next_url)
            page_queue.append((next_url, None))

    if log_fn and page_queue:
        log_fn(f"Stopped gallery discovery after {max_pages} pages.")

    return all_links


def _collect_anchor_links(
    container_el,
    link_selector: dict,
    link_args: dict,
    page_url: str,
    seen: set[str],
    *,
    allowed_hosts: set[str] | None,
) -> list[_ChapterCandidate]:
    """Extract and deduplicate anchor hrefs from a container element."""
    links: list[_ChapterCandidate] = []
    for anchor in container_el.find_all(**link_args):
        href = _link_target(anchor, link_selector)
        if not href:
            continue
        resolved = _resolved_page_url(page_url, href, allowed_hosts=allowed_hosts)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        links.append(_ChapterCandidate(resolved, _text_of(anchor), str(anchor)))
    return links


def _build_chapters(
    chapter_links: list[_ChapterCandidate],
    volume_index: int,
    chapter_naming: dict | None,
) -> tuple[Chapter, ...]:
    """Build :class:`Chapter` descriptors from a flat URL list.

    ``ordinal`` is 1-based global (set later by the caller based on
    accumulated offset).  ``position_in_volume`` is 1-based within
    the volume.
    """
    chapters: list[Chapter] = []
    for pos, candidate in enumerate(chapter_links, 1):
        chapters.append(
            Chapter(
                ordinal=0,  # placeholder; set by caller
                volume_index=volume_index,
                position_in_volume=pos,
                identifier=_chapter_identifier(candidate, chapter_naming),
                title=candidate.label,
                url=candidate.url,
            )
        )
    return tuple(chapters)


def _apply_chapter_order(
    links: list[_ChapterCandidate], order: str | None
) -> list[_ChapterCandidate]:
    """Reverse the list if ``order`` is ``"newest_first"``."""
    if order and str(order).strip().lower() == "newest_first":
        return list(reversed(links))
    return links


def _chapter_identifier(candidate: _ChapterCandidate, naming: dict | None) -> str:
    """Extract a display label without ever coercing decimal identifiers."""
    label = candidate.label.strip()
    if not isinstance(naming, dict):
        return label
    source = str(naming.get("source", "text")).strip().lower()
    value = candidate.url if source in {"url", "href"} else label
    selector = naming.get("selector")
    if isinstance(selector, dict) and candidate.anchor_html:
        selected = find_element(BeautifulSoup(candidate.anchor_html, "html.parser"), selector)
        if selected is None:
            value = ""
        elif source in {"url", "href"}:
            value = read_attribute(selected, naming.get("other_attr") or "href")
        elif source not in {"text", "label", "anchor_text"}:
            value = read_attribute(selected, source)
        else:
            value = _text_of(selected)
    pattern = naming.get("regex") or naming.get("pattern")
    if pattern:
        try:
            match = re.search(str(pattern), value)
        except re.error as exc:
            raise InvalidFlowError("chapter_naming.regex is invalid.") from exc
        if not match:
            value = ""
        else:
            capture = naming.get("capture")
            try:
                value = (
                    match.group(1) if capture is None and match.lastindex else
                    match.group(0) if capture is None else match.group(capture)
                )
            except (IndexError, KeyError) as exc:
                raise InvalidFlowError("chapter_naming.capture does not match its regex.") from exc
    template = naming.get("display_template", naming.get("template"))
    if value and template:
        try:
            value = str(template).format(
                identifier=value, value=value, label=label, url=candidate.url
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise InvalidFlowError("chapter_naming.display_template is invalid.") from exc
    return str(value).strip()


def _deduplicate_candidates(
    candidates: list[_ChapterCandidate], seen: set[str],
) -> list[_ChapterCandidate]:
    """Deduplicate canonical chapter URLs globally, preserving source order."""
    unique: list[_ChapterCandidate] = []
    for candidate in candidates:
        canonical = canonical_page_url(candidate.url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        unique.append(_ChapterCandidate(canonical, candidate.label, candidate.anchor_html))
    return unique


def _apply_selection(
    volumes: list[Volume],
    context: CrawlContext,
) -> list[Volume]:
    """Filter chapters by the request's selection mode.

    * ``FULL`` keeps everything.
    * ``RANGE`` keeps chapters whose ``ordinal`` is in
      ``[start_index, end_index]`` (1-based, inclusive).
    * ``SINGLE`` keeps exactly the chapter matching ``chapter_url``.
    """
    request = context.request
    selection = request.selection
    from api.contracts import SelectionMode

    if selection is SelectionMode.FULL:
        return volumes

    if selection is SelectionMode.RANGE:
        start = request.start_index or 1
        end = request.end_index or start
        result: list[Volume] = []
        for vol in volumes:
            filtered = [ch for ch in vol.chapters if start <= ch.ordinal <= end]
            if filtered:
                result.append(
                    Volume(
                        index=vol.index,
                        title=vol.title,
                        cover_url=vol.cover_url,
                        synthetic=vol.synthetic,
                        chapters=tuple(filtered),
                    )
                )
        return result

    if selection is SelectionMode.SINGLE:
        target = (request.chapter_url or "").strip()
        if not target:
            return volumes
        target_canonical = canonical_page_url(target)
        result = []
        for vol in volumes:
            filtered = [ch for ch in vol.chapters if canonical_page_url(ch.url) == target_canonical]
            if filtered:
                result.append(
                    Volume(
                        index=vol.index,
                        title=vol.title,
                        cover_url=vol.cover_url,
                        synthetic=vol.synthetic,
                        chapters=tuple(filtered),
                    )
                )
        return result

    return volumes


def _assign_ordinals(volumes: list[Volume]) -> list[Volume]:
    """Assign monotonically increasing ``ordinal`` (1-based) across volumes.

    Existing display identifiers are preserved; a global ordinal is only the
    fallback when no configured/source label was extracted.
    """
    counter = 1
    updated: list[Volume] = []
    for vol in volumes:
        new_chapters: list[Chapter] = []
        for ch in vol.chapters:
            new_chapters.append(
                Chapter(
                    ordinal=counter,
                    volume_index=ch.volume_index,
                    position_in_volume=ch.position_in_volume,
                    identifier=ch.identifier or str(counter),
                    title=ch.title,
                    url=ch.url,
                )
            )
            counter += 1
        updated.append(
            Volume(
                index=vol.index,
                title=vol.title,
                cover_url=vol.cover_url,
                synthetic=vol.synthetic,
                chapters=tuple(new_chapters),
            )
        )
    return updated


def volumes_prepare_handler(context: CrawlContext, params: dict) -> None:
    """Extract volumes and chapters from the main page (or sub-pages).

    Three strategies (priority order):

    1. ``vol_group.vol_section`` present → extract volumes from main page.
    2. Root ``chapter_list`` present → BFS pagination, one synthetic volume.
    3. ``gallery_links`` present → discover picture-page links, synthetic volume.

    Populates ``context.volumes``.
    """
    if context.metadata is None:
        raise InvalidFlowError("volumes_prepare requires metadata to be extracted first.")
    if context.main_page_html is None:
        raise InvalidFlowError("volumes_prepare requires main_page_html to be cached by get_metadata.")

    fmt = context.format_definition
    request = context.request
    raw_page = context.raw_page
    log_fn = context.log
    base_url = context.metadata.source_url or request.url
    soup = BeautifulSoup(context.main_page_html, "html.parser")

    chapter_order = fmt.get("chapter_list_order")
    content_type = request.content_type

    volumes: list[Volume] = []
    chapter_naming = fmt.get("chapter_naming")
    generated_cover = _generated_cover_reference(
        context.metadata.title, context.metadata.author
    )

    if content_type == ContentType.GALLERY:
        # Gallery path: gallery_links → synthetic volume with picture-page chapters
        gallery_links = fmt.get("gallery_links")
        if not isinstance(gallery_links, dict) or not selector_args(gallery_links.get("link")):
            raise InvalidFlowError(
                "gallery formats require a usable gallery_links.link selector."
            )
        picture_links = _extract_gallery_picture_links(
            soup, fmt, base_url, raw_page, context.metadata.source_url or request.url, log_fn
        )
        chapters = _build_chapters(picture_links, volume_index=0, chapter_naming=chapter_naming)
        volumes.append(
            Volume(
                index=0,
                title=context.metadata.title,
                cover_url=context.metadata.cover_url or generated_cover,
                synthetic=True,
                chapters=chapters,
            )
        )
    else:
        # Novel / Comic paths
        vol_group = fmt.get("vol_group") if isinstance(fmt.get("vol_group"), dict) else {}
        vol_section = vol_group.get("vol_section") if vol_group else None
        has_volume_selector = bool(selector_args(vol_section))
        has_volume_chapter_selector = bool(
            selector_args(vol_group.get("chapter_list")) if vol_group else {}
        )
        root_chapter_list = fmt.get("chapter_list")
        if isinstance(root_chapter_list, dict):
            root_container = root_chapter_list.get("container", root_chapter_list)
            has_root_chapter_selector = bool(selector_args(root_container)) and bool(
                selector_args(root_chapter_list.get("link") or {"name": "a"})
            )
        else:
            has_root_chapter_selector = False

        if has_volume_selector and not has_volume_chapter_selector:
            raise InvalidFlowError(
                "format vol_group requires a usable chapter_list selector."
            )
        if not has_volume_selector and not has_root_chapter_selector:
            raise InvalidFlowError(
                "format requires usable vol_group or chapter_list chapter selectors."
            )
        vol_sections = _extract_volume_sections(soup, fmt, base_url)
        seen_chapters: set[str] = set()

        if vol_sections:
            # Strategy 1: real volumes from vol_group.vol_section
            for idx, vol_data in enumerate(vol_sections):
                links = _deduplicate_candidates(
                    list(vol_data["chapter_links"]),  # type: ignore[arg-type]
                    seen_chapters,
                )
                links = _apply_chapter_order(links, chapter_order)
                chapters = _build_chapters(links, volume_index=idx, chapter_naming=chapter_naming)
                # Cover fallback: volume cover → work cover → deterministic token.
                cover = (
                    vol_data.get("cover_url")
                    or context.metadata.cover_url
                    or generated_cover
                )
                volumes.append(
                    Volume(
                        index=idx,
                        title=str(vol_data.get("title", "")),
                        cover_url=str(cover) if cover else "",
                        synthetic=False,
                        chapters=chapters,
                    )
                )
        elif has_root_chapter_selector:
            # Strategy 2: root chapter_list → synthetic volume
            chapter_links = _discover_root_chapters(
                soup, fmt, base_url, raw_page, context.metadata.source_url or request.url, log_fn
            )
            chapter_links = _deduplicate_candidates(chapter_links, seen_chapters)
            chapter_links = _apply_chapter_order(chapter_links, chapter_order)
            chapters = _build_chapters(
                chapter_links, volume_index=0, chapter_naming=chapter_naming
            )
            volumes.append(
                Volume(
                    index=0,
                    title="",
                    cover_url=context.metadata.cover_url or generated_cover,
                    synthetic=True,
                    chapters=chapters,
                )
            )

    # Assign ordinals
    volumes = _assign_ordinals(volumes)

    # Apply selection
    volumes = _apply_selection(volumes, context)

    context.volumes = volumes

    total_chapters = sum(len(v.chapters) for v in volumes)
    if log_fn:
        log_fn(
            f"Volumes prepared: {len(volumes)} volume(s), "
            f"{total_chapters} chapter(s)."
        )


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def register_preparation_handlers() -> None:
    """Upgrade canonical ``get_metadata`` / ``volumes_prepare`` with real handlers."""
    set_module_handler("get_metadata", get_metadata_handler)
    set_module_handler("volumes_prepare", volumes_prepare_handler)


register_preparation_handlers()


__all__ = [
    "canonical_page_url",
    "find_all_elements",
    "find_element",
    "get_metadata_handler",
    "is_empty_selector",
    "read_attribute",
    "register_preparation_handlers",
    "resolve_url",
    "selector_args",
    "selector_to_css",
    "volumes_prepare_handler",
]
