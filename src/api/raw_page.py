"""Raw-page fetch service over the shared :class:`utils.fetcher.PageFetcher`.

Task 2 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).
This module is the pipeline's single way to fetch raw HTML:

* It wraps the existing requests/browser/auto :class:`PageFetcher` unchanged,
  preserving its session reuse, Cloudflare challenge handling, retries, and
  headless configuration.
* ``selector readiness`` is forwarded as the fetcher's ``expected_selector``.
* One :class:`RawPageService` instance is kept alive for a whole flow run so a
  requests session (and browser driver, when active) is reused across steps.
* :class:`RawPageService.close` is idempotent and is called from the flow
  engine's ``finally`` block, guaranteeing cleanup after success, failure, or
  interruption.

Fetch-mode resolution (first match wins):

1. the ``fetch_mode`` keyword argument (explicit override);
2. ``request.fetch_mode`` when it is not ``None``;
3. ``format_definition["fetch"]["mode"]``;
4. ``"requests"`` (the safe default).

Headless resolution (first match wins):

1. the ``headless`` keyword argument;
2. ``request.headless`` (always an explicit boolean on a validated request).
   The format's ``fetch.headless`` is a TUI hint only and is not consumed by
   the service, because the request always carries an explicit value.

Remaining browser details (``cloudflare``, ``challenge_timeout_seconds``,
``profile_name``) come from ``format_definition["fetch"]``.
"""

from __future__ import annotations

from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from api.contracts import (
    CrawlRequest,
    FetchFailureError,
    InvalidFlowError,
    InvalidRequestError,
    _normalize_url,
)
from utils.fetcher import (
    FetchMode,
    PageFetcher,
    PageFetcherError,
)

DEFAULT_CHALLENGE_TIMEOUT = 180
"""Seconds the browser waits for Cloudflare verification when the format does
not configure ``fetch.challenge_timeout_seconds``."""


def _format_fetch_block(format_definition: object) -> dict:
    if not isinstance(format_definition, dict):
        return {}
    fetch = format_definition.get("fetch", {})
    if fetch is None:
        return {}
    if not isinstance(fetch, dict):
        raise InvalidFlowError(
            "format_definition['fetch'] must be a JSON object, "
            f"got {type(fetch).__name__}."
        )
    return fetch


def _resolve_fetch_configuration(
    request: CrawlRequest,
    format_definition: object,
    *,
    fetch_mode: Optional[str] = None,
    headless: Optional[bool] = None,
) -> tuple[str, bool, bool, int, str | None]:
    """Purely validate and resolve raw-page configuration."""
    fetch = _format_fetch_block(format_definition)

    mode = fetch_mode if fetch_mode is not None else request.fetch_mode
    if mode is None:
        raw_mode = fetch.get("mode")
        mode = str(raw_mode).strip().lower() if raw_mode is not None else FetchMode.REQUESTS
    else:
        mode = str(mode).strip().lower()
    if not FetchMode.is_valid(mode):
        raise InvalidFlowError(
            f"invalid fetch mode {mode!r}; expected 'requests', 'browser', or 'auto'."
        )

    if headless is not None and not isinstance(headless, bool):
        raise InvalidFlowError("headless override must be a boolean.")
    effective_headless = request.headless if headless is None else headless

    raw_cloudflare = fetch.get("cloudflare", True)
    if not isinstance(raw_cloudflare, bool):
        raise InvalidFlowError(
            "format_definition['fetch']['cloudflare'] must be a boolean; "
            f"got {type(raw_cloudflare).__name__}."
        )

    raw_timeout = fetch.get("challenge_timeout_seconds")
    if isinstance(raw_timeout, bool) or (raw_timeout is not None and not isinstance(raw_timeout, int)):
        raise InvalidFlowError(
            "format_definition['fetch']['challenge_timeout_seconds'] must be an integer."
        )
    challenge_timeout = raw_timeout if raw_timeout is not None else DEFAULT_CHALLENGE_TIMEOUT
    if challenge_timeout < 0:
        raise InvalidFlowError("challenge_timeout_seconds must be zero or greater.")

    raw_profile = fetch.get("profile_name")
    if raw_profile is None:
        profile_name = None
    elif isinstance(raw_profile, str) and raw_profile.strip():
        profile_name = raw_profile.strip()
    elif isinstance(raw_profile, str):
        profile_name = None
    else:
        raise InvalidFlowError(
            "format_definition['fetch']['profile_name'] must be a string or null."
        )
    return mode, effective_headless, raw_cloudflare, challenge_timeout, profile_name


def validate_fetch_configuration(request: CrawlRequest, format_definition: object) -> None:
    """Validate fetch settings without creating a session, browser, or directory."""
    _resolve_fetch_configuration(request, format_definition)


def _safe_url_for_error(url: str) -> str:
    """Drop userinfo and query/fragment values before exposing a fetch URL."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    host_part = f"[{host}]" if ":" in host else host
    netloc = f"{host_part}:{parsed.port}" if parsed.port is not None else host_part
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


class RawPageService:
    """Long-lived raw-page fetch service bound to one :class:`CrawlRequest`.

    The service owns exactly one :class:`PageFetcher`, so requests share a
    session and, once active, one browser driver.  Call :meth:`close` when the
    flow finishes (the flow engine does this in a ``finally`` block).

    Parameters
    ----------
    request : CrawlRequest
        Normalized crawl request defining fetch mode/headless defaults.
    format_definition : dict, optional
        Site format; only its ``fetch`` block configures browser details
        (mode/headless/cloudflare/timeout/profile).
    fetch_mode : str, optional
        Explicit override of the resolution order above.
    headless : bool, optional
        Explicit override of the browser window mode.

    Raises
    ------
    InvalidFlowError
        The resolved fetch mode or format fetch configuration is invalid.
    """

    def __init__(
        self,
        request: CrawlRequest,
        format_definition: object = None,
        *,
        fetch_mode: Optional[str] = None,
        headless: Optional[bool] = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(request, CrawlRequest):
            raise InvalidRequestError(
                "RawPageService requires a CrawlRequest; "
                f"got {type(request).__name__}."
            )
        self.request = request
        self.format_definition = format_definition if isinstance(format_definition, dict) else {}

        mode, effective_headless, raw_cloudflare, challenge_timeout, profile_name = _resolve_fetch_configuration(
            request, format_definition, fetch_mode=fetch_mode, headless=headless
        )

        self.fetch_mode = mode
        self.headless = effective_headless
        fetcher_kwargs = {
            "fetch_mode": mode,
            "cloudflare": raw_cloudflare,
            "challenge_timeout": challenge_timeout,
            "profile_name": profile_name,
            "headless": effective_headless,
            "keep_logged_in": request.keep_logged_in,
        }
        if request.max_retries is not None:
            fetcher_kwargs["client_error_retries"] = request.max_retries
        if before_request is not None:
            fetcher_kwargs["before_request"] = before_request
        self._fetcher: Optional[PageFetcher] = PageFetcher(
            **fetcher_kwargs,
        )

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def get_raw_page(
        self,
        url: str,
        expected_selector: Optional[str] = None,
    ) -> str:
        """Fetch *url* and return its raw HTML.

        *expected_selector* is forwarded as a selector-readiness check in
        requests, browser, and auto modes. All fetcher errors are normalized to
        :class:`FetchFailureError`.
        """
        normalized = _normalize_url(url, label="url")
        if self._fetcher is None:
            raise InvalidRequestError("RawPageService is closed; create a new service.")
        try:
            return self._fetcher.fetch(normalized, expected_selector)
        except PageFetcherError as exc:
            raise FetchFailureError(
                f"Could not fetch {_safe_url_for_error(normalized)}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Lifecycle + pass-throughs
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying session and, if started, the browser driver.

        Idempotent: :meth:`utils.fetcher.PageFetcher.close` is safe to call
        more than once, and subsequent calls on this service are no-ops.  The
        service cannot be reused after it is closed.
        """
        fetcher = self._fetcher
        self._fetcher = None
        if fetcher is not None:
            try:
                fetcher.close()
            finally:
                fetcher = None

    @property
    def cookies(self):
        """The requests session cookies (for same-origin asset downloads)."""
        return self._fetcher.cookies if self._fetcher is not None else None

    @property
    def browser_driver(self):
        """The active verified browser driver or ``None`` when unused."""
        return self._fetcher.browser_driver if self._fetcher is not None else None


def get_raw_page(
    request: CrawlRequest,
    url: str,
    expected_selector: Optional[str] = None,
    *,
    fetch_mode: Optional[str] = None,
    headless: Optional[bool] = None,
    format_definition: object = None,
    before_request: Callable[[], None] | None = None,
) -> str:
    """Fetch one page with a fresh, immediately-closed :class:`RawPageService`.

    Convenience wrapper for one-off page retrieval (the flow engine instead
    keeps a service alive for the whole run to reuse its session).  Resources
    are always closed, including on failure.
    """
    service = RawPageService(
        request,
        format_definition,
        fetch_mode=fetch_mode,
        headless=headless,
        before_request=before_request,
    )
    try:
        return service.get_raw_page(url, expected_selector)
    finally:
        service.close()
