"""Chapter crawling, image downloading, and manifest bookkeeping.

Task 4 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

This module implements the ``crawl_chapter`` flow module handler, which
separates **text extraction** from **image downloading** while preserving
exact replacement information for the exporter (``create_ebook``, Task 5).

Design rules
------------
* ``crawl_text_content`` sanitizes configured unwanted elements and stores
  canonical remote image tags **unchanged**; it performs **zero** image
  downloads.
* ``download_image`` downloads images with referrers, browser cookies,
  retry/validation, and stable project naming, producing one ordered
  ``ImageManifestEntry`` per successful image.
* Duplicate identical image tags are distinguished by their **occurrence**
  index within a chapter.
* Request-only flows reuse the existing safe thread pool (``ChapterScheduler``)
  for parallel chapter crawling.  Browser/``auto`` flows execute chapter
  images strictly **sequentially** through the shared raw-page service,
  because a browser driver cannot be shared safely across threads.
* Workers never write ``img/img_info.json`` directly; the coordinator writes
  it **once**, after aggregation, in source-order.
* Exhausted required image/chapter failures raise ``IncompleteCrawlError``,
  which retains intermediate data/logs and prevents final packaging.
"""

from __future__ import annotations

import copy
import os
import re
import threading
from typing import Optional

import requests
from bs4 import BeautifulSoup

from api.contracts import (
    Chapter,
    CrawlContext,
    FetchFailureError,
    ImageManifestEntry,
    ImageStatus,
    IMAGE_MANIFEST_SCHEMA_VERSION,
    IncompleteCrawlError,
    InvalidFlowError,
    Volume,
)
from api.flow import set_module_handler
from api.preparation import resolve_url, selector_args, selector_to_css

# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _base_output_dir(context: CrawlContext) -> str:
    """Return and ensure the img output directory exists (as a path)."""
    root = context.output_dir
    if not root:
        raise InvalidFlowError("output_dir must be resolved before crawling images.")
    img_dir = os.path.join(root, "img")
    os.makedirs(img_dir, exist_ok=True)
    return img_dir


def _safe_filename(value: str, default: str = "image") -> str:
    """Return a filesystem-safe base filename (no extension)."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(". ")
    return cleaned if cleaned else default


def _manifest_path(context: CrawlContext) -> str:
    """Path to ``img/img_info.json`` (does not create it)."""
    root = context.output_dir
    if not root:
        raise InvalidFlowError("output_dir must be resolved before crawling images.")
    return os.path.join(root, "img", "img_info.json")


def write_manifest(context: CrawlContext) -> str:
    """Write ``img/img_info.json`` once, in source order.

    Returns the manifest path.
    """
    import json

    root = context.output_dir
    if not root:
        raise InvalidFlowError("output_dir must be resolved before crawling images.")
    img_dir = os.path.join(root, "img")
    os.makedirs(img_dir, exist_ok=True)
    entries = [
        entry.to_dict()
        for entry in context.image_entries
    ]
    path = os.path.join(img_dir, "img_info.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# Text crawl
# ---------------------------------------------------------------------------


def _clean_selector_args(selector, excluded=()):
    """Convert a format selector to safe BeautifulSoup search arguments."""
    args = selector_args(selector)
    return {key: value for key, value in args.items() if key not in excluded}


def _should_drop(element, remove_specs: list[dict]) -> bool:
    """Return ``True`` if *element* should be dropped per remove specs."""
    for spec in remove_specs:
        name = spec.get("name")
        class_ = spec.get("class_") or ""
        id_ = spec.get("id") or ""
        class_prefix = spec.get("class_prefix") or ""
        id_prefix = spec.get("id_prefix") or ""
        if name and element.name != name:
            continue
        if class_ and (class_ not in (element.get("class") or [])):
            continue
        if id_ and element.get("id") != id_:
            continue
        if class_prefix:
            classes = element.get("class") or []
            if not any(c.startswith(class_prefix) for c in classes):
                continue
        if id_prefix and not (element.get("id") or "").startswith(id_prefix):
            continue
        return True
    return False


def _extract_chapter_content(soup: BeautifulSoup, fmt: dict) -> BeautifulSoup:
    """Extract and sanitize the chapter content element.

    Returns the (possibly modified) content *element* (a BeautifulSoup Tag).
    """
    chapter = fmt.get("chapter") or {}
    if not isinstance(chapter, dict):
        raise InvalidFlowError("format 'chapter' must be an object.")
    content_selector = chapter.get("content") or {}
    content_args = _clean_selector_args(content_selector)
    if content_args:
        element = soup.find(**content_args)
        if element is None:
            raise IncompleteCrawlError(
                "chapter content element could not be located with the format's "
                "'chapter.content' selector."
            )
    else:
        element = soup.find("body") or soup

    remove_specs = chapter.get("remove") or []
    if not isinstance(remove_specs, list):
        remove_specs = []
    # Always strip scripts/styles/noscripts first
    for node in element.find_all(["script", "style", "noscript"]):
        node.decompose()
    for node in list(element.find_all()):
        if _should_drop(node, remove_specs):
            node.decompose()
    return element


def _extract_chapter_title(chapter_url: str, soup: BeautifulSoup, fmt: dict) -> str:
    """Extract the chapter title or fall back to page title/heading."""
    chapter = fmt.get("chapter") or {}
    if not isinstance(chapter, dict):
        return "Untitled"
    title_selector = chapter.get("title") or {}
    title_args = _clean_selector_args(title_selector)
    title_el = soup.find(**title_args) if title_args else None
    if title_el is not None and title_el.text.strip():
        return title_el.text.strip()
    fallback = soup.find("title") or soup.find(["h1", "h2", "h3"])
    if fallback is not None and fallback.text.strip():
        return fallback.text.strip()
    return "Untitled"


def crawl_text_content(context: CrawlContext, chapter: Chapter, params: dict) -> tuple[str, str]:
    """Fetch *chapter* and return sanitized chapter HTML without downloading images.

    The canonical remote image tags are preserved unchanged.
    """
    raw_page = context.raw_page
    if raw_page is None:
        raise InvalidFlowError("raw_page service is not initialised on the context.")

    fmt = context.format_definition
    chapter_sel = (fmt.get("chapter") or {})
    if not isinstance(chapter_sel, dict):
        raise InvalidFlowError("format 'chapter' must be an object.")
    content_css = selector_to_css(chapter_sel.get("content")) or None

    url = chapter.url or ""
    if not url:
        raise IncompleteCrawlError("chapter has no URL to crawl.")
    expected = params.get("expected_selector") or content_css
    try:
        html = raw_page.get_raw_page(url, expected)
    except FetchFailureError:
        raise
    except Exception as exc:
        raise FetchFailureError(
            f"could not fetch chapter {url}: {exc}"
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    element = _extract_chapter_content(soup, fmt)
    title = _extract_chapter_title(url, soup, fmt)
    return element.prettify(), title


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

_IMAGE_EXT_BY_HEADER = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


class _PermanentImageError(IOError):
    """An image response cannot succeed by retrying the same request."""


def _detect_ext(content_type: str | None, default: str = "jpg") -> str:
    if not content_type:
        return default
    return _IMAGE_EXT_BY_HEADER.get(content_type.split(";")[0].strip().lower(), default)


def _fetch_image_bytes(
    url: str,
    referer_url: str | None,
    img_referrer: bool,
    cookies=None,
    timeout: float = 20,
    max_retries: int = 5,
    retry_delay: float = 2,
    before_request=None,
) -> tuple[bytes, str]:
    """Download image bytes with retries and optional referer.

    Returns ``(bytes, content_type)``.  Raises ``IOError`` on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    host = referer_url or url
    if img_referrer:
        headers["Referer"] = host

    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
        raise ValueError("max_retries must be a positive integer.")
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if before_request is not None:
                before_request()
            resp = requests.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
            )
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type")
                return resp.content, content_type or ""
            # Retrying a missing/forbidden image adds delay without improving
            # completion.  408/429 are explicitly transient client responses.
            if 400 <= resp.status_code < 500 and resp.status_code not in {408, 429}:
                raise _PermanentImageError(f"HTTP {resp.status_code}")
            if resp.status_code != 200:
                raise IOError(f"HTTP {resp.status_code}")
        except _PermanentImageError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_retries - 1:
                import time as _time
                _time.sleep(retry_delay)
    raise IOError(f"failed to download image {url}: {last_exc}")


def _validate_image_bytes(data: bytes) -> bool:
    """Return whether *data* looks like a real image."""
    if len(data) < 4:
        return False
    head = data[:8]
    return (
        head.startswith(b"\xff\xd8\xff")          # JPEG
        or head.startswith(b"\x89PNG")            # PNG
        or head.startswith(b"RIFF")               # WebP/RIFF
        or head.startswith(b"GIF8")               # GIF
        or head.startswith(b"BM")                 # BMP
    )


def download_image(
    context: CrawlContext,
    source_url: str,
    raw_tag: str,
    chapter_ordinal: int,
    occurrence: int,
    *,
    referer_url: str | None = None,
    img_referrer: bool = False,
    cookies=None,
    require_valid_image: bool = True,
    max_retries: int = 5,
    timeout: float = 20,
    retry_delay: float = 2,
) -> ImageManifestEntry:
    """Download one image and return its manifest entry.

    The caller appends the returned entry to ``context.image_entries``.
    On permanent failure an entry with ``ImageStatus.FAILED`` is returned.
    """
    img_dir = _base_output_dir(context)

    try:
        data, content_type = _fetch_image_bytes(
            source_url,
            referer_url,
            img_referrer,
            cookies=cookies,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            before_request=context.pacer,
        )
    except IOError as exc:
        return ImageManifestEntry(
            chapter_ordinal=chapter_ordinal,
            occurrence=occurrence,
            source_url=source_url,
            raw_tag=raw_tag,
            status=ImageStatus.FAILED,
        )

    if require_valid_image and not _validate_image_bytes(data):
        return ImageManifestEntry(
            chapter_ordinal=chapter_ordinal,
            occurrence=occurrence,
            source_url=source_url,
            raw_tag=raw_tag,
            status=ImageStatus.FAILED,
        )

    ext = _detect_ext(content_type)
    base = _safe_filename(f"img_{chapter_ordinal:04d}_{occurrence:03d}")
    filename = f"{base}.{ext}"
    local_path = os.path.join(img_dir, filename)
    with open(local_path, "wb") as f:
        f.write(data)

    replacement_tag = f"<img src=\"{filename}\">"
    relative_path = f"img/{filename}"
    return ImageManifestEntry(
        chapter_ordinal=chapter_ordinal,
        occurrence=occurrence,
        source_url=source_url,
        raw_tag=raw_tag,
        filename=filename,
        relative_path=relative_path,
        replacement_tag=replacement_tag,
        status=ImageStatus.DOWNLOADED,
    )


def _image_cookies(raw_page):
    """Merge request-session and active browser cookies for asset requests."""
    if raw_page is None:
        return None
    cookies = None
    try:
        session_cookies = raw_page.cookies
        if session_cookies is not None:
            cookies = requests.cookies.RequestsCookieJar()
            cookies.update(session_cookies)
    except Exception:
        pass
    try:
        driver = raw_page.browser_driver
        browser_cookies = driver.get_cookies() if driver is not None else []
        if browser_cookies:
            cookies = cookies or requests.cookies.RequestsCookieJar()
            for cookie in browser_cookies:
                name = cookie.get("name")
                if name:
                    cookies.set(
                        name,
                        cookie.get("value", ""),
                        domain=cookie.get("domain") or "",
                        path=cookie.get("path") or "/",
                    )
    except Exception:
        # Keep request-session cookies if the browser vanished after fetching.
        pass
    return cookies


def download_images_for_chapter(
    context: CrawlContext,
    chapter: Chapter,
    chapter_html: str,
    *,
    chapter_url: str,
    max_retries: int = 5,
) -> list[ImageManifestEntry]:
    """Locate and download all images within sanitized *chapter_html*.

    Returns a list of manifest entries (one per image, in occurrence order).
    The caller appends them to ``context.image_entries``.
    """
    fmt = context.format_definition
    chapter_sel = fmt.get("chapter") or {}
    if not isinstance(chapter_sel, dict):
        raise InvalidFlowError("format 'chapter' must be an object.")
    image_sel = chapter_sel.get("image") or {}
    if not isinstance(image_sel, dict):
        raise InvalidFlowError("format 'chapter.image' must be an object.")
    img_args = _clean_selector_args(image_sel, excluded=("other_attr", "allowed_hosts", "delete"))
    other_attr = image_sel.get("other_attr") or "src"
    allowed_hosts = image_sel.get("allowed_hosts") or []
    if isinstance(allowed_hosts, str):
        allowed_hosts = [allowed_hosts]
    if not isinstance(allowed_hosts, (list, tuple, set)) or not all(
        isinstance(host, str) and host.strip() for host in allowed_hosts
    ):
        raise InvalidFlowError("chapter.image.allowed_hosts must be a list of host strings.")
    try:
        delete_count = int(image_sel.get("delete") or 0)
    except (TypeError, ValueError) as exc:
        raise InvalidFlowError("chapter.image.delete must be a non-negative integer.") from exc
    if delete_count < 0:
        raise InvalidFlowError("chapter.image.delete must be a non-negative integer.")

    soup = BeautifulSoup(chapter_html, "html.parser")
    if img_args:
        elements = soup.find_all(**img_args)
    else:
        elements = soup.find_all("img")

    # Drop trailing ad images if delete > 0
    if delete_count and elements:
        elements = elements[:-delete_count]

    img_referrer = bool(fmt.get("img_referrer", False))
    referer_url = chapter_url
    cookies = _image_cookies(context.raw_page)

    entries: list[ImageManifestEntry] = []
    image_progress = context.progress
    image_total = len(elements)
    for occurrence, el in enumerate(elements, 1):
        try:
            raw_tag = str(el)
            source_url = el.get(other_attr) or el.get("src") or ""
            if not source_url:
                continue
            source_url = resolve_url(chapter_url, source_url)
            if source_url.lower().startswith(("data:", "javascript:", "about:blank", "#")):
                continue
            if allowed_hosts:
                from urllib.parse import urlsplit as _split
                host = _split(source_url).netloc.lower().removeprefix("www.")
                allowed = {h.lower().removeprefix("www.") for h in allowed_hosts}
                if allowed and host not in allowed:
                    continue
            entry = download_image(
                context,
                source_url,
                raw_tag,
                chapter.ordinal,
                occurrence,
                referer_url=referer_url,
                img_referrer=img_referrer,
                cookies=cookies,
                max_retries=max_retries,
            )
            entries.append(entry)
        finally:
            if image_progress:
                image_progress("image", occurrence, image_total)

    return entries


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def _flatten_chapters(context: CrawlContext) -> list[Chapter]:
    """Return all chapters in ordinal order across volumes."""
    all_chapters: list[Chapter] = []
    for vol in context.volumes:
        all_chapters.extend(vol.chapters)
    all_chapters.sort(key=lambda c: c.ordinal)
    return all_chapters


def _run_one_chapter(
    context: CrawlContext,
    chapter: Chapter,
    actions: tuple[str, ...],
    params: dict | None = None,
    max_retries: int = 4,
) -> dict[str, object]:
    """Crawl one chapter: text (optionally) + images (optionally).

    Returns a dict ``{"chapter": chapter, "html": str|None, "images": [entries]}``.
    """
    html = None
    image_entries: list[ImageManifestEntry] = []
    title = chapter.title or f"Chapter {chapter.ordinal}"

    do_text = (not actions) or "crawl_text_content" in actions
    do_images = "download_image" in actions

    text_params = params or {}
    if do_text:
        html, title = crawl_text_content(context, chapter, text_params)

    if do_images:
        if html is None:
            # Need content element even for pure-image chapters
            html, title = crawl_text_content(context, chapter, text_params)
        image_entries = download_images_for_chapter(
            context, chapter, html, chapter_url=chapter.url, max_retries=max_retries + 1
        )

    return {
        "chapter": chapter,
        "title": title,
        "html": html,
        "images": image_entries,
    }


def _crawl_sequential(context, chapters, actions, progress_fn, params, max_retries):
    """Crawl chapters sequentially through the shared raw-page service."""
    results: list[dict[str, object]] = []
    failures: list[Chapter] = []
    for i, chapter in enumerate(chapters, 1):
        try:
            for attempt in range(max_retries + 1):
                try:
                    result = _run_one_chapter(context, chapter, actions, params, max_retries)
                    break
                except KeyboardInterrupt:
                    raise
                except Exception:
                    if attempt >= max_retries:
                        raise
            results.append(result)
            if progress_fn:
                progress_fn(f"chapter", i, len(chapters))
        except Exception:
            failures.append(chapter)
            if progress_fn:
                progress_fn(f"chapter", i, len(chapters))
    return results, failures


def _parallel_worker_contexts(context: CrawlContext):
    """Create one requests-only RawPageService per scheduler thread.

    A PageFetcher owns a mutable requests.Session, so worker threads must not
    share the coordinator's service.  The initial request-session cookies are
    copied into each isolated worker service; browser/login paths never call
    this helper because they run sequentially.
    """
    from api.raw_page import RawPageService

    # Test/extension adapters may provide an in-memory raw-page object rather
    # than the production service.  They own their own concurrency semantics.
    if not isinstance(context.raw_page, RawPageService):
        return (lambda: context), (lambda: None)

    local = threading.local()
    services: list[RawPageService] = []
    services_lock = threading.Lock()
    progress_lock = threading.Lock()
    initial_cookies = None
    try:
        initial_cookies = context.raw_page.cookies if context.raw_page is not None else None
    except Exception:
        pass

    def get_context() -> CrawlContext:
        worker_context = getattr(local, "context", None)
        if worker_context is not None:
            return worker_context
        service = RawPageService(
            context.request,
            context.format_definition,
            fetch_mode="requests",
            before_request=context.pacer,
        )
        if initial_cookies is not None:
            try:
                service.cookies.update(initial_cookies)
            except Exception:
                pass
        worker_context = copy.copy(context)
        worker_context.raw_page = service
        if context.progress is not None:
            worker_context.progress = (
                lambda kind, done, total: _locked_progress(
                    progress_lock, context.progress, kind, done, total
                )
            )
        local.context = worker_context
        with services_lock:
            services.append(service)
        return worker_context

    def close_all() -> None:
        with services_lock:
            active = list(services)
            services.clear()
        for service in active:
            service.close()

    return get_context, close_all


def _locked_progress(lock, progress_fn, kind: str, done: int, total: int) -> None:
    """Serialize callbacks emitted by parallel image workers."""
    with lock:
        progress_fn(kind, done, total)


def crawl_chapter_handler(context: CrawlContext, params: dict) -> None:
    """Run the chapter crawl for every chapter in ``context.volumes``.

    Populates ``context.chapter_contents`` and ``context.image_entries``,
    and writes ``img/img_info.json`` once after aggregation.

    Raises
    ------
    IncompleteCrawlError
        At least one chapter permanently failed (required anyway).
    """
    if not context.volumes:
        if context.log:
            context.log("crawl_chapter: no volumes to crawl (zero chapters).")
        return

    actions = params.get("actions")
    if actions is None:
        actions = ("crawl_text_content",)
    if not isinstance(actions, (list, tuple)) or not actions:
        raise InvalidFlowError("crawl_chapter requires a non-empty 'actions' parameter.")
    actions = tuple(actions)
    configured_retries = context.request.max_retries
    if configured_retries is None:
        configured_retries = params.get("max_retries", 4)
    if isinstance(configured_retries, bool) or not isinstance(configured_retries, int) or configured_retries < 0:
        raise InvalidFlowError("crawl_chapter max_retries must be a non-negative integer.")

    chapters = _flatten_chapters(context)
    total = len(chapters)
    progress_fn = context.progress

    request = context.request
    fetch_mode = request.fetch_mode
    sequential = fetch_mode in ("browser", "auto") or request.keep_logged_in
    if fetch_mode is None:
        fmt_fetch = context.format_definition.get("fetch") or {}
        sequential = str(fmt_fetch.get("mode", "requests")).strip().lower() in (
            "browser",
            "auto",
        ) or request.keep_logged_in
    from api.raw_page import RawPageService
    if isinstance(context.raw_page, RawPageService):
        try:
            sequential = sequential or context.raw_page.browser_driver is not None
        except Exception:
            pass

    results: list[dict[str, object]] = []
    failed: list[Chapter] = []

    if sequential:
        results, failed = _crawl_sequential(
            context, chapters, actions, progress_fn, params, configured_retries
        )
    else:
        from utils.worker_config import ChapterScheduler, ChapterJob

        jobs = [
            ChapterJob(
                position=idx,
                url=ch.url,
                label=f"Chapter {ch.ordinal}",
                max_retries=configured_retries,
            )
            for idx, ch in enumerate(chapters)
        ]

        get_worker_context, close_worker_contexts = _parallel_worker_contexts(context)

        def work(job: ChapterJob) -> dict[str, object] | None:
            chapter = chapters[job.position]
            return _run_one_chapter(
                get_worker_context(), chapter, actions, params, job.max_retries
            )

        scheduler = ChapterScheduler(
            work,
            jobs,
            max_workers=request.max_workers,
            progress_fn=lambda done, n: progress_fn(f"chapter", done, n)
            if progress_fn else None,
        )
        # Each job carries its chapter in order of position.
        try:
            results = scheduler.run()
        finally:
            close_worker_contexts()
        completed_positions = {r["chapter"].ordinal for r in results}
        failed = [ch for ch in chapters if ch.ordinal not in completed_positions]

    # Populate chapter_contents and image_entries in ordinal order
    result_by_ordinal = {r["chapter"].ordinal: r for r in results}
    ordered_results = [result_by_ordinal[ch.ordinal] for ch in chapters if ch.ordinal in result_by_ordinal]

    for result in ordered_results:
        chapter = result["chapter"]
        context.chapter_contents.append({
            "ordinal": chapter.ordinal,
            "title": result.get("title") or chapter.title or f"Chapter {chapter.ordinal}",
            "html": result.get("html") or "",
            "volume_index": chapter.volume_index,
            "position_in_volume": chapter.position_in_volume,
            "identifier": chapter.identifier,
            "url": chapter.url,
        })
        context.image_entries.extend(result["images"])

    write_manifest(context)

    failed_images = [
        entry for entry in context.image_entries if entry.status is ImageStatus.FAILED
    ]
    if failed or failed_images:
        failure_parts: list[str] = []
        if failed:
            failure_parts.append(
                f"{len(failed)} required chapter(s) failed: "
                + ", ".join(f"#{ch.ordinal}" for ch in failed[:5])
                + ("..." if len(failed) > 5 else "")
            )
        if failed_images:
            failure_parts.append(
                f"{len(failed_images)} required image(s) failed: "
                + ", ".join(
                    f"chapter #{entry.chapter_ordinal}, image #{entry.occurrence}"
                    for entry in failed_images[:5]
                )
                + ("..." if len(failed_images) > 5 else "")
            )
        raise IncompleteCrawlError(
            "; ".join(failure_parts)
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_chapter_crawl_handlers() -> None:
    """Upgrade the canonical ``crawl_chapter`` module with the real handler."""
    set_module_handler("crawl_chapter", crawl_chapter_handler)


register_chapter_crawl_handlers()


__all__ = [
    "crawl_chapter_handler",
    "crawl_text_content",
    "download_image",
    "download_images_for_chapter",
    "register_chapter_crawl_handlers",
    "write_manifest",
]
