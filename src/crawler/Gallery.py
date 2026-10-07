"""GalleryCrawler — configurable, JSON-driven gallery picture downloader.

Crawl a gallery page that lists ordered links to picture pages, extract one
image per picture page, and package the images.  Site structure is loaded
from ``data/formats/<alias>.json`` after resolving the site through
``data/aliases.csv``, matching the rest of the project.

This module implements configuration resolution/validation, page parsing,
sequential image downloading, and CBZ packaging.  The constructor performs
no network requests and creates no output directories.

Usage
-----
>>> from crawler.Gallery import run
>>> result = run("https://example.com/gallery/abc", make_cbz=True, keep_images=True)
>>> result["cbz_path"]
'outputs/Gallery/abc/abc.cbz'
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import tqdm

from utils.fetcher import PageFetcher
from utils.novel import download_image

ALIASES_PATH = os.path.join("data", "aliases.csv")
FORMATS_DIR = os.path.join("data", "formats")
DEFAULT_OUTPUT_ROOT = os.path.join("outputs", "Gallery")

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
"""Filesystem-invalid characters removed from gallery titles."""


class IncompleteGalleryError(RuntimeError):
    """A resumable crawl could not download its next picture page."""


def _normalize_url(url: str) -> str:
    """Return *url* with ``https://`` added when a scheme is missing."""
    value = str(url or "").strip()
    if value and not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _normalize_host(host: str) -> str:
    """Lowercase *host* and strip a leading ``www.`` and trailing slashes."""
    return str(host or "").strip().lower().removeprefix("www.").rstrip("/")


def _usable_selector(selector):
    """Return selector args for finding elements, or ``None``.

    ``other_attr`` names a data attribute read from a matched element rather
    than a way to find one, so it never makes a selector usable by itself.
    """
    if not isinstance(selector, dict):
        return None
    cleaned = {
        key: value
        for key, value in selector.items()
        if key != "other_attr" and value != ""
    }
    return cleaned if cleaned else None


def _required_attr(selector, label: str, format_path: str) -> str:
    """Return a non-blank ``other_attr`` value or raise ``ValueError``."""
    if not isinstance(selector, dict):
        raise ValueError(
            f"Gallery format is missing '{label}' object: {format_path}"
        )
    attr = str(selector.get("other_attr") or "").strip()
    if not attr:
        raise ValueError(
            f"Gallery format '{label}.other_attr' must be present: {format_path}"
        )
    return attr


def _is_web_url(url: str) -> bool:
    """Return whether *url* is an absolute HTTP(S) URL."""
    parsed = urlsplit(url)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _positive_int(value, default: int, label: str, format_path: str) -> int:
    """Return a positive integer format option, or a clear config error."""
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer: {format_path}") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be a positive integer: {format_path}")
    return parsed


def _positive_float(value, default: float, label: str, format_path: str) -> float:
    """Return a positive numeric format option, or a clear config error."""
    try:
        parsed = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive number: {format_path}") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive number: {format_path}")
    return parsed


class GalleryCrawler:
    """Resolve and validate a gallery site's configuration.

    Parameters
    ----------
    gallery_url : str
        URL of the gallery page.  A missing scheme is assumed to be
        ``https://``.
    output_dir : str or None
        Root output directory; the gallery subfolder is derived later.
    fetch_mode : str or None
        ``"requests"``, ``"browser"``, or ``"auto"``.  Overrides the site
        format's ``fetch.mode`` when given.
    headless : bool or None
        Browser headless flag.  Overrides the site format's
        ``fetch.headless`` when given.
    """

    def __init__(
        self,
        gallery_url: str,
        output_dir: str | None = None,
        fetch_mode: str | None = None,
        headless: bool | None = None,
    ):
        self.gallery_url = _normalize_url(gallery_url)
        if not self.gallery_url:
            raise ValueError("A gallery URL is required.")

        self.output_dir = output_dir
        self.explicit_fetch_mode = fetch_mode
        self.explicit_headless = headless

        host = _normalize_host(urlsplit(self.gallery_url).netloc)
        if not host:
            raise ValueError(
                f"Could not determine a host from gallery URL: {self.gallery_url!r}"
            )

        self.site_name = self._resolve_site_name(host)
        self.format_path = os.path.join(FORMATS_DIR, f"{self.site_name}.json")
        self.format_data = self._load_format(self.format_path)
        self._validate_format()

        fetch_config = self.format_data.get("fetch")
        if not isinstance(fetch_config, dict):
            fetch_config = {}

        self.fetch_mode = self.explicit_fetch_mode or str(
            fetch_config.get("mode") or "requests"
        )
        if self.explicit_headless is not None:
            self.headless = bool(self.explicit_headless)
        else:
            self.headless = bool(fetch_config.get("headless", True))

        self.img_referrer = self.format_data.get("img_referrer", False)
        self.fetcher = PageFetcher(
            fetch_mode=self.fetch_mode,
            cloudflare=bool(fetch_config.get("cloudflare", True)),
            challenge_timeout=int(fetch_config.get("challenge_timeout_seconds", 180)),
            profile_name=(fetch_config.get("profile_name") or None),
            headless=self.headless,
        )

    def _resolve_site_name(self, host: str) -> str:
        """Return the alias format name for *host*, or raise ``ValueError``."""
        if not os.path.isfile(ALIASES_PATH):
            raise ValueError(f"Aliases file not found: {ALIASES_PATH}")
        with open(ALIASES_PATH, "r", encoding="utf-8") as aliases_file:
            for row in csv.DictReader(aliases_file):
                site = _normalize_host(str(row.get("site") or ""))
                if site == host:
                    name = str(row.get("name") or "").strip()
                    if not name:
                        raise ValueError(
                            f"Configured alias for '{host}' has no format name."
                        )
                    return name
        raise ValueError(f"Site '{host}' was not found in {ALIASES_PATH}.")

    def _load_format(self, format_path: str) -> dict:
        """Load and validate the JSON structure of the site format file."""
        if not os.path.isfile(format_path):
            raise ValueError(f"Format file not found: {format_path}")
        try:
            with open(format_path, "r", encoding="utf-8") as format_file:
                data = json.load(format_file)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Format file is not valid JSON: {format_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"Format file must contain a JSON object: {format_path}"
            )
        return data

    def _validate_format(self) -> None:
        """Validate gallery selectors and store the cleaned variants."""
        gallery_links = self.format_data.get("gallery_links")
        if not isinstance(gallery_links, dict):
            raise ValueError(
                "Gallery format is missing 'gallery_links' object: "
                f"{self.format_path}"
            )

        container = _usable_selector(gallery_links.get("container"))
        if container is None:
            raise ValueError(
                "Gallery format 'gallery_links.container' must contain a "
                f"usable selector: {self.format_path}"
            )
        self._container_selector = container

        link = gallery_links.get("link")
        self._link_attr = _required_attr(link, "gallery_links.link", self.format_path)
        self._link_selector = _usable_selector(link) or {"name": "a"}

        pagination = gallery_links.get("pagination")
        pagination_container = _usable_selector(
            pagination.get("container") if isinstance(pagination, dict) else None
        )
        self._pagination_container_selector = pagination_container
        if pagination_container is None:
            self._pagination_link_selector = {"name": "a"}
            self._pagination_link_attr = "href"
            self._pagination_page_param = "p"
        else:
            pagination_link = (
                pagination.get("link") if isinstance(pagination, dict) else None
            )
            self._pagination_link_attr = str(
                (pagination_link or {}).get("other_attr") or ""
            ).strip() or "href"
            self._pagination_link_selector = _usable_selector(pagination_link) or {
                "name": "a"
            }
            self._pagination_page_param = str(
                pagination.get("page_param") or ""
            ).strip() or "p"

        picture = self.format_data.get("picture")
        if not isinstance(picture, dict):
            raise ValueError(
                "Gallery format is missing 'picture' object: "
                f"{self.format_path}"
            )

        image = picture.get("image")
        image_selector = _usable_selector(image)
        if image_selector is None:
            raise ValueError(
                "Gallery format 'picture.image' must contain a usable "
                f"selector: {self.format_path}"
            )
        self._image_selector = image_selector
        self._image_attr = _required_attr(image, "picture.image", self.format_path)

        delivery = self.format_data.get("image_delivery")
        if not isinstance(delivery, dict):
            delivery = {}
        self._refresh_picture_page_on_failure = bool(
            delivery.get("refresh_picture_page_on_failure", False)
        )
        self._image_refresh_attempts = max(
            0, int(delivery.get("max_refreshes", 0) or 0)
        )
        self._image_request_attempts = _positive_int(
            delivery.get("request_attempts"),
            default=5,
            label="image_delivery.request_attempts",
            format_path=self.format_path,
        )
        self._image_request_timeout = _positive_float(
            delivery.get("request_timeout_seconds"),
            default=20,
            label="image_delivery.request_timeout_seconds",
            format_path=self.format_path,
        )
        self._image_retry_delay = _positive_float(
            delivery.get("retry_delay_seconds"),
            default=2,
            label="image_delivery.retry_delay_seconds",
            format_path=self.format_path,
        )
        self._stop_on_image_failure = bool(
            delivery.get("stop_on_image_failure", False)
        )
        self._resume_partial_downloads = bool(
            delivery.get("resume_partial_downloads", False)
        )
        try:
            self._incomplete_session_retries = max(
                0, int(delivery.get("incomplete_session_retries", 5) or 0)
            )
            self._incomplete_retry_delay = max(
                0.0, float(delivery.get("incomplete_retry_delay_seconds", 0) or 0)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "image_delivery incomplete-session retry settings must be numeric: "
                f"{self.format_path}"
            ) from exc
        self._image_failure_hint = str(delivery.get("failure_hint") or "").strip()
        fallback = delivery.get("failure_page_fallback")
        if not isinstance(fallback, dict):
            fallback = {}
        self._failure_fallback_selector = _usable_selector(
            fallback.get("selector")
        )
        self._failure_fallback_url_attr = str(
            fallback.get("url_attr") or "href"
        ).strip()
        self._failure_fallback_onclick_pattern = str(
            fallback.get("onclick_pattern") or ""
        ).strip()
        self._failure_fallback_query_param = str(
            fallback.get("query_param") or "nl"
        ).strip()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def extract_title(self, gallery_html: str) -> str:
        """Return a sanitized gallery title from the page or URL slug.

        The configured ``title`` selector is used when present.  Without a
        usable selector or a match, the decoded final gallery URL path
        segment is used, then ``"gallery"``.
        """
        title_selector = _usable_selector(self.format_data.get("title", {}))
        if title_selector:
            soup = BeautifulSoup(gallery_html, "html.parser")
            element = soup.find(**title_selector)
            if element is not None:
                text = element.get_text(" ", strip=True)
                if text:
                    return self._sanitize_title(text)
        return self._title_from_url()

    def _title_from_url(self) -> str:
        path = urlsplit(self.gallery_url).path.rstrip("/")
        segment = unquote(path.rsplit("/", 1)[-1]) if path else ""
        return self._sanitize_title(segment)

    def _sanitize_title(self, text: str) -> str:
        """Return a filesystem-safe title, falling back to ``"gallery"``."""
        cleaned = INVALID_FILENAME_CHARS.sub("", str(text or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "gallery"

    def parse_gallery(self, gallery_html: str, base_url: str | None = None) -> list[str]:
        """Return ordered, deduplicated picture-page URLs from gallery HTML.

        Links are read from the configured container using the configured
        link selector and attribute.  URL fragments are stripped and
        duplicates removed while preserving first-seen DOM order.  Relative
        links are resolved against *base_url* (or the gallery URL when
        omitted).
        """
        base_url = base_url or self.gallery_url
        soup = BeautifulSoup(gallery_html, "html.parser")
        container = soup.find(**self._container_selector)
        if container is None:
            raise ValueError(
                f"Gallery container not found on {base_url} "
                f"using selector {self._container_selector!r}."
            )

        picture_urls = []
        seen = set()
        for anchor in container.find_all(**self._link_selector):
            raw = str(anchor.get(self._link_attr) or "").strip()
            if not raw or raw.startswith("#"):
                continue
            resolved = self._strip_fragment(urljoin(base_url, raw))
            if not _is_web_url(resolved):
                continue
            if resolved == self._strip_fragment(self.gallery_url):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            picture_urls.append(resolved)

        if not picture_urls:
            raise ValueError(
                f"No usable picture links found on {base_url}."
            )
        return picture_urls

    def _pagination_page_urls(self, gallery_html: str) -> list[str]:
        """Return ordered, deduplicated gallery page URLs from the navigation.

        Uses the optional ``gallery_links.pagination`` selectors.  The
        current gallery URL is never included.  Returns ``[]`` when
        pagination is not configured or the navigation bar is absent.
        """
        if self._pagination_container_selector is None:
            return []
        soup = BeautifulSoup(gallery_html, "html.parser")
        container = soup.find(**self._pagination_container_selector)
        if container is None:
            return []
        current = self._strip_fragment(self.gallery_url)
        page_urls = []
        seen = set()
        for anchor in container.find_all(**self._pagination_link_selector):
            raw = str(anchor.get(self._pagination_link_attr) or "").strip()
            if not raw or raw.startswith("#"):
                continue
            resolved = self._strip_fragment(urljoin(self.gallery_url, raw))
            if not _is_web_url(resolved) or resolved == current:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            page_urls.append(resolved)
        return page_urls

    def _page_number(self, url: str) -> int:
        """Return the gallery page number of *url*, or 0 when absent."""
        query = urlsplit(url).query
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == self._pagination_page_param:
                try:
                    return int(value)
                except ValueError:
                    break
        return 0

    def parse_picture(self, picture_html: str, picture_url: str) -> str:
        """Return the single configured image URL from a picture page."""
        soup = BeautifulSoup(picture_html, "html.parser")
        image = soup.find(**self._image_selector)
        if image is None:
            raise ValueError(
                f"No configured image found on picture page: {picture_url}"
            )
        raw = str(image.get(self._image_attr) or "").strip()
        if not raw:
            raise ValueError(
                f"Configured image attribute '{self._image_attr}' is missing "
                f"on picture page: {picture_url}"
            )
        image_url = self._strip_fragment(urljoin(picture_url, raw))
        if not _is_web_url(image_url):
            raise ValueError(
                f"Configured image URL is not HTTP(S) on picture page: {picture_url}"
            )
        return image_url

    def picture_image_urls(self, picture_urls: list[str]) -> list[str]:
        """Fetch every picture page and return its image URL in order.

        Repeated image URLs are preserved because each picture page
        represents an ordered gallery position.  Shared fetcher errors
        propagate to the caller.
        """
        image_urls = []
        for picture_url in picture_urls:
            picture_html = self.fetcher.fetch(picture_url)
            image_urls.append(self.parse_picture(picture_html, picture_url))
        return image_urls

    def _download_picture_image(
        self,
        picture_url: str,
        output_dir: str,
        page_name: str,
        img_ext: str,
        preserve_ext: bool,
    ) -> tuple[str, str]:
        """Download one picture, refreshing its page after a failed image URL.

        Some gallery CDNs issue short-lived image dispatch URLs.  A refresh
        re-fetches the picture page and extracts a new URL; it does not retry
        gallery-list pages or introduce parallel image downloads.
        """
        picture_html = self.fetcher.fetch(picture_url)
        image_page_url = picture_url
        image_url = self.parse_picture(picture_html, image_page_url)
        refreshes_remaining = (
            self._image_refresh_attempts
            if self._refresh_picture_page_on_failure
            else 0
        )
        fallback_attempted = False

        while True:
            local_path = download_image(
                image_url,
                output_dir=output_dir,
                ext=img_ext,
                name=page_name,
                img_referrer=self.img_referrer,
                referer_url=image_page_url,
                cookies=getattr(self.fetcher, "cookies", None),
                browser_driver=getattr(self.fetcher, "browser_driver", None),
                preserve_ext=preserve_ext,
                require_valid_image=True,
                # Gallery downloads deliberately stay on the HTTP/guest
                # recovery path until ChromeDriver compatibility is reliable.
                selenium_fallback=False,
                max_retries=self._image_request_attempts,
                request_timeout_seconds=self._image_request_timeout,
                retry_delay_seconds=self._image_retry_delay,
            )
            if local_path:
                return image_url, local_path

            fallback_url = self._failure_page_url(picture_html, image_page_url)
            if (
                not fallback_attempted
                and fallback_url
                and fallback_url != image_page_url
            ):
                fallback_attempted = True
                picture_html = self.fetcher.fetch(fallback_url)
                image_page_url = fallback_url
                image_url = self.parse_picture(picture_html, image_page_url)
                continue

            if refreshes_remaining <= 0:
                return image_url, local_path

            refreshes_remaining -= 1
            picture_html = self.fetcher.fetch(picture_url)
            image_page_url = picture_url
            image_url = self.parse_picture(picture_html, image_page_url)

    def _failure_page_url(self, picture_html: str, picture_url: str) -> str:
        """Return the site-provided image-failure fallback URL, if any."""
        if self._failure_fallback_selector is None:
            return ""
        soup = BeautifulSoup(picture_html, "html.parser")
        link = soup.find(**self._failure_fallback_selector)
        if link is None:
            return ""

        raw_url = str(link.get(self._failure_fallback_url_attr) or "").strip()
        if raw_url and not raw_url.startswith("#"):
            resolved = self._strip_fragment(urljoin(picture_url, raw_url))
            if _is_web_url(resolved):
                return resolved

        if not self._failure_fallback_onclick_pattern:
            return ""
        onclick = str(link.get("onclick") or "")
        try:
            match = re.search(self._failure_fallback_onclick_pattern, onclick)
        except re.error:
            return ""
        if not match:
            return ""
        token = match.group(1)
        parsed = urlsplit(picture_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query[self._failure_fallback_query_param] = token
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                "",
            )
        )

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    def crawl(
        self,
        make_cbz: bool = True,
        keep_images: bool = True,
        img_ext: str = "jpg",
        preserve_ext: bool = True,
    ) -> dict:
        """Download every gallery image in order into the output folder.

        When the site format defines ``gallery_links.pagination`` and the
        gallery page shows a navigation bar, the linked gallery pages are
        crawled in page-number order and their picture links are combined,
        so a gallery split across several pages downloads as one set.

        Parameters
        ----------
        make_cbz : bool
            Create ``<title>.cbz`` in the gallery folder after every image
            downloads successfully.
        keep_images : bool
            Retain the source image files after a successful archive is
            created.  ``make_cbz=False`` with ``keep_images=False`` would
            retain no output and is rejected.
        img_ext : str
            Fallback file extension used when the downloaded image format
            cannot be detected from its bytes.
        preserve_ext : bool
            Keep the downloaded image's original format (for example saving
            a WebP source as ``.webp``) instead of re-encoding everything
            to JPEG.

        Returns
        -------
        dict
            Keys: ``title``, ``gallery_url``, ``output_dir``,
            ``picture_urls``, ``image_urls``, ``downloaded_files``, and
            ``cbz_path`` (``None`` when ``make_cbz`` is False).

        Raises
        ------
        RuntimeError
            When at least one picture page fails to download or the archive
            cannot be created. Existing completed output is preserved,
            incomplete staged output is discarded by default. Formats that
            enable ``resume_partial_downloads`` retain a checkpointed partial
            directory instead, and the error lists the failed page.
        """
        if not make_cbz and not keep_images:
            raise ValueError(
                "make_cbz and keep_images cannot both be False because "
                "that would retain no output."
            )

        gallery_html = self.fetcher.fetch(self.gallery_url)
        self.title = self.extract_title(gallery_html)

        picture_urls: list[str] = []
        visited_pages = set()
        page_queue = [self._strip_fragment(self.gallery_url)]
        page_html_by_url = {self._strip_fragment(self.gallery_url): gallery_html}
        while page_queue:
            page_url = page_queue.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)
            page_html = page_html_by_url.pop(page_url, None)
            if page_html is None:
                page_html = self.fetcher.fetch(page_url)
            picture_urls.extend(self.parse_gallery(page_html, base_url=page_url))
            for next_url in sorted(
                self._pagination_page_urls(page_html),
                key=self._page_number,
            ):
                if next_url not in visited_pages and next_url not in page_queue:
                    page_queue.append(next_url)
        output_dir = self._resolve_output_dir()
        staging_dir = self._create_staging_dir(output_dir)
        try:
            width = self._page_width(len(picture_urls))
            resume_state = self._load_partial_state(staging_dir, picture_urls)
            image_urls: list[str] = []
            downloaded_files: list[str] = []
            failures: list[dict] = []

            progress = tqdm.tqdm(
                total=len(picture_urls),
                desc="Downloading gallery images",
                unit="image",
            )
            try:
                for index, picture_url in enumerate(picture_urls, start=1):
                    page_name = f"{index:0{width}d}"
                    resumed = self._resumed_page(resume_state, staging_dir, page_name)
                    if resumed:
                        image_url, local_path = resumed
                        image_urls.append(image_url)
                        downloaded_files.append(local_path)
                        progress.update(1)
                        continue

                    failed_this_page = False
                    try:
                        image_url, local_path = self._download_picture_image(
                            picture_url,
                            output_dir=staging_dir,
                            page_name=page_name,
                            img_ext=img_ext,
                            preserve_ext=preserve_ext,
                        )
                    except Exception as exc:
                        failures.append(
                            {
                                "index": index,
                                "url": picture_url,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        failed_this_page = True
                    else:
                        if not local_path:
                            failures.append(
                                {
                                    "index": index,
                                    "url": picture_url,
                                    "error": "image download returned no valid image file",
                                }
                            )
                            failed_this_page = True
                        else:
                            image_urls.append(image_url)
                            downloaded_files.append(local_path)
                            self._record_resumed_page(
                                resume_state, staging_dir, page_name, image_url
                            )
                    finally:
                        progress.update(1)

                    if failed_this_page and self._stop_on_image_failure:
                        break
            finally:
                progress.close()

            if failures:
                summary = "\n".join(
                    f"  page {failure['index']} ({failure['url']}): {failure['error']}"
                    for failure in failures
                )
                hint = f"\nHint: {self._image_failure_hint}" if self._image_failure_hint else ""
                partial_hint = ""
                if self._resume_partial_downloads:
                    partial_hint = (
                        f"\nPartial downloads retained at {staging_dir}. "
                        "Rerun the same gallery later to resume from the "
                        "first missing image."
                    )
                    staging_dir = None
                error_type = (
                    IncompleteGalleryError
                    if self._resume_partial_downloads
                    else RuntimeError
                )
                raise error_type(
                    f"Failed to download {len(failures)} of {len(picture_urls)} "
                    f"picture pages for {self.title}:\n{summary}{hint}{partial_hint}"
                )

            self._remove_file(self._partial_state_path(staging_dir))
            cbz_path = None
            if make_cbz:
                cbz_path = self._package_cbz(
                    staging_dir, downloaded_files, keep_images=keep_images
                )

            final_downloaded_files = [
                os.path.join(output_dir, os.path.basename(path))
                for path in downloaded_files
            ]
            final_cbz_path = (
                os.path.join(output_dir, os.path.basename(cbz_path))
                if cbz_path
                else None
            )
            self._publish_staging_dir(staging_dir, output_dir)
            staging_dir = None

            return {
                "title": self.title,
                "gallery_url": self.gallery_url,
                "output_dir": output_dir,
                "picture_urls": picture_urls,
                "image_urls": image_urls,
                "downloaded_files": final_downloaded_files if keep_images else [],
                "cbz_path": final_cbz_path,
            }
        finally:
            if staging_dir:
                self._remove_tree(staging_dir)

    def run(
        self,
        make_cbz: bool = True,
        keep_images: bool = True,
        img_ext: str = "jpg",
        preserve_ext: bool = True,
    ) -> dict:
        """Run :meth:`crawl`, resuming incomplete downloads in this session.

        Resumable formats get one initial crawl plus up to
        ``incomplete_session_retries`` attempts using their checkpointed
        ``.incomplete`` directory. Other errors, including archive and output
        publication errors, are not retried.
        """
        resume_attempt = 0
        try:
            while True:
                try:
                    return self.crawl(
                        make_cbz=make_cbz,
                        keep_images=keep_images,
                        img_ext=img_ext,
                        preserve_ext=preserve_ext,
                    )
                except IncompleteGalleryError as exc:
                    if resume_attempt >= self._incomplete_session_retries:
                        message = (
                            "[Gallery] Download remains incomplete after "
                            f"{self._incomplete_session_retries} resume attempts "
                            "in this session. The retained .incomplete directory "
                            "can be resumed in a later run."
                        )
                        print(message)
                        raise IncompleteGalleryError(
                            f"{message}\nLast failure: {exc}"
                        ) from exc

                    resume_attempt += 1
                    message = (
                        "[Gallery] Download incomplete; resuming retained images "
                        f"(retry {resume_attempt}/{self._incomplete_session_retries})."
                    )
                    print(message)
                    if self._incomplete_retry_delay:
                        time.sleep(self._incomplete_retry_delay)
        finally:
            self.close()

    def _resolve_output_dir(self) -> str:
        root = self.output_dir or DEFAULT_OUTPUT_ROOT
        return os.path.join(root, self.title)

    def _create_staging_dir(self, output_dir: str) -> str:
        """Create a sibling staging folder without touching existing output.

        Resumable formats use a deterministic ``.incomplete`` sibling so a
        later run can retain the completed pages. Other formats retain the
        original private temporary directory behaviour.
        """
        root = os.path.dirname(output_dir)
        try:
            os.makedirs(root, exist_ok=True)
            if self._resume_partial_downloads:
                partial_dir = f"{output_dir}.incomplete"
                os.makedirs(partial_dir, exist_ok=True)
                return partial_dir
            return tempfile.mkdtemp(prefix=f".{self.title}.staging-", dir=root)
        except OSError as exc:
            raise RuntimeError(
                f"Could not create output directory: {output_dir}: {exc}"
            ) from exc

    @staticmethod
    def _partial_state_path(partial_dir: str) -> str:
        return os.path.join(partial_dir, ".gallery-crawl-state.json")

    def _load_partial_state(self, partial_dir: str, picture_urls: list[str]) -> dict | None:
        """Load or create the checkpoint used by resumable gallery formats."""
        if not self._resume_partial_downloads:
            return None

        state_path = self._partial_state_path(partial_dir)
        expected = {
            "gallery_url": self.gallery_url,
            "picture_urls": picture_urls,
        }
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as state_file:
                    state = json.load(state_file)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Could not read partial gallery state: {state_path}: {exc}"
                ) from exc
            if not isinstance(state, dict) or any(
                state.get(key) != value for key, value in expected.items()
            ):
                raise RuntimeError(
                    "The existing partial download belongs to a different gallery. "
                    f"Move or remove {partial_dir} before starting this crawl."
                )
            if not isinstance(state.get("image_urls"), dict):
                raise RuntimeError(f"Partial gallery state is invalid: {state_path}")
            return state

        state = {**expected, "image_urls": {}}
        self._save_partial_state(partial_dir, state)
        return state

    def _save_partial_state(self, partial_dir: str, state: dict) -> None:
        """Atomically persist a resumable gallery checkpoint."""
        state_path = self._partial_state_path(partial_dir)
        temporary_path = f"{state_path}.tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file, ensure_ascii=False, indent=2)
            os.replace(temporary_path, state_path)
        except OSError as exc:
            self._remove_file(temporary_path)
            raise RuntimeError(
                f"Could not save partial gallery state: {state_path}: {exc}"
            ) from exc

    def _resumed_page(
        self, state: dict | None, partial_dir: str, page_name: str
    ) -> tuple[str, str] | None:
        """Return a checkpointed page only when its image file still exists."""
        if state is None:
            return None
        image_url = state["image_urls"].get(page_name)
        if not image_url:
            return None
        prefix = f"{page_name}."
        try:
            filenames = os.listdir(partial_dir)
        except OSError:
            return None
        for filename in filenames:
            if filename.startswith(prefix):
                local_path = os.path.join(partial_dir, filename)
                if os.path.isfile(local_path):
                    return str(image_url), local_path
        return None

    def _record_resumed_page(
        self, state: dict | None, partial_dir: str, page_name: str, image_url: str
    ) -> None:
        """Record a completed page immediately so an interrupted run resumes."""
        if state is None:
            return
        state["image_urls"][page_name] = image_url
        self._save_partial_state(partial_dir, state)

    def _publish_staging_dir(self, staging_dir: str, output_dir: str) -> None:
        """Replace prior output only after a complete staged crawl succeeds."""
        backup_dir = None
        try:
            if os.path.lexists(output_dir):
                backup_dir = f"{output_dir}.previous-{uuid.uuid4().hex}"
                os.replace(output_dir, backup_dir)
            os.replace(staging_dir, output_dir)
        except OSError as exc:
            if backup_dir and os.path.lexists(backup_dir) and not os.path.lexists(output_dir):
                try:
                    os.replace(backup_dir, output_dir)
                except OSError:
                    pass
            raise RuntimeError(
                f"Could not publish completed gallery output: {output_dir}: {exc}"
            ) from exc

        if backup_dir:
            self._remove_tree(backup_dir)

    @staticmethod
    def _remove_tree(path: str) -> None:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass

    def _package_cbz(
        self,
        output_dir: str,
        downloaded_files: list[str],
        keep_images: bool = True,
    ) -> str:
        """Create ``<title>.cbz`` in *output_dir* and return its path.

        Images are added in *downloaded_files* order using only their
        basenames.  The archive is written to a temporary path and renamed
        into place only after every member has been written and the archive
        closed, so no partial CBZ is ever exposed.  Source images are
        removed only when ``keep_images`` is False and only after the final
        archive exists.
        """
        cbz_path = os.path.join(output_dir, f"{self.title}.cbz")
        temp_path = f"{cbz_path}.tmp"

        try:
            with zipfile.ZipFile(temp_path, "w") as archive:
                for image_path in downloaded_files:
                    archive.write(image_path, os.path.basename(image_path))
        except Exception as exc:
            self._remove_file(temp_path)
            raise RuntimeError(
                f"Failed to package CBZ for {self.title}: {exc}"
            ) from exc

        try:
            os.replace(temp_path, cbz_path)
        except OSError as exc:
            self._remove_file(temp_path)
            raise RuntimeError(
                f"Failed to finalize CBZ for {self.title}: {exc}"
            ) from exc

        if not keep_images:
            for image_path in downloaded_files:
                try:
                    os.remove(image_path)
                except OSError as exc:
                    raise RuntimeError(
                        "Failed to remove source image after CBZ creation: "
                        f"{image_path}: {exc}"
                    ) from exc

        return cbz_path

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _page_width(count: int) -> int:
        """Return the number of digits used to pad page numbers.

        Always at least four digits, expanding for galleries with more
        than 9,999 pages.
        """
        return max(4, len(str(int(count))))

    @staticmethod
    def _strip_fragment(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    def close(self) -> None:
        """Release the shared page fetcher (idempotent)."""
        if getattr(self, "fetcher", None) is not None:
            self.fetcher.close()


def run(
    gallery_url: str,
    output_dir: str | None = None,
    make_cbz: bool = True,
    keep_images: bool = True,
    fetch_mode: str | None = None,
    headless: bool | None = None,
    preserve_ext: bool = True,
) -> dict:
    """Crawl a gallery with a fresh crawler and always release it.

    Returns the documented result dictionary; the crawler's fetcher is
    closed on success and on every failure path.
    """
    from api.runner import reject_legacy_crawler

    reject_legacy_crawler()
    crawler = GalleryCrawler(
        gallery_url,
        output_dir=output_dir,
        fetch_mode=fetch_mode,
        headless=headless,
    )
    return crawler.run(
        make_cbz=make_cbz, keep_images=keep_images, preserve_ext=preserve_ext
    )
