"""
PageFetcher — shared fetch API with Cloudflare-aware browser fallback.

Three fetch modes
-----------------
``"requests"``
    Pure ``requests.Session`` — no browser started.
``"browser"``
    Headed SeleniumBase UC Chrome for every request.
``"auto"``
    ``requests`` first; on a Cloudflare challenge the fetcher
    automatically switches to a persistent browser for the rest
    of the crawl.

Usage
-----
>>> from utils.fetcher import PageFetcher
>>> fetcher = PageFetcher(fetch_mode="requests")
>>> html = fetcher.fetch("https://example.com")
>>> fetcher.close()
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CF_CHALLENGE_HEADER = "cf-mitigated"
"""Response header that Cloudflare sets on challenge pages.

The value is ``"challenge"`` when a challenge is active.  The header
may be absent on non‑challenge responses.
"""

# Strong HTML‑body markers — these almost always indicate a Cloudflare
# challenge or error page.  All checks are case‑insensitive.
CF_STRONG_MARKERS = (
    "challenge-platform",
    "cf-chl-",
    "cf-browser-verification",
    "__cf_chl_opt",
    "cf-error-details",
)

# Title‑or‑heading markers — these short phrases appear in the page
# title or an ``<h1>`` / ``<h2>`` of a Cloudflare challenge/error.
# They are conservative: never match on status‑code alone.
CF_WEAK_MARKERS = (
    "just a moment",
    "attention required",
)

PROFILES_DIR = ".crawler_profiles"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PageFetcherError(Exception):
    """Base exception for all PageFetcher errors."""


class BrowserUnavailableError(PageFetcherError):
    """Raised when the browser cannot be started or is not available."""


class ChallengeTimeoutError(PageFetcherError):
    """Raised when manual Cloudflare verification exceeds the timeout."""


class FetchError(PageFetcherError):
    """Raised when a page cannot be fetched (network error, timeout, HTTP error)."""


class CloudflareChallengeError(FetchError):
    """Raised when the fetched page is a Cloudflare challenge page.

    The caller can catch this subclass to trigger browser fallback
    while letting ordinary ``FetchError`` (network issues, timeouts)
    propagate unchanged.
    """


class SelectorMismatchError(PageFetcherError):
    """Raised when the browser‑loaded page does not contain the
    expected content selector.

    This signals that the page loaded (Cloudflare clearance succeeded)
    but the DOM structure is different from what the parser expects.
    """


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def is_cloudflare_challenge(response: requests.Response) -> bool:
    """Return ``True`` when *response* is a Cloudflare challenge page.

    Detection uses the ``cf-mitigated`` response header as the primary
    signal and narrowly scoped HTML‑body markers as a fallback.

    **Important:** An ordinary HTTP error (e.g. a plain 403) without
    any Cloudflare marker is **not** misclassified.
    """
    # 1. Header signal (primary)
    if CF_CHALLENGE_HEADER in response.headers:
        return True

    body = (response.text or "").lower()

    # 2. Strong HTML markers — sufficient on their own
    for marker in CF_STRONG_MARKERS:
        if marker in body:
            return True

    # 3. Weak markers — only match when the status code is also
    #    a client/server error (> 400).  This avoids flagging
    #    normal pages that happen to contain the phrase
    #    "just a moment" in user content.
    status = response.status_code
    if status >= 400:
        for marker in CF_WEAK_MARKERS:
            if marker in body:
                return True

    return False


def classify_response(response: requests.Response) -> str:
    """Classify a ``requests.Response`` into one of three categories.

    Returns
    -------
    str
        ``"cf_challenge"``
            Cloudflare challenge / error page.
        ``"http_error"``
            HTTP error (4xx / 5xx) without any Cloudflare marker.
        ``"ok"``
            Successful response.
    """
    if is_cloudflare_challenge(response):
        return "cf_challenge"
    try:
        response.raise_for_status()
        return "ok"
    except requests.RequestException:
        return "http_error"


def _default_profile_name(url: str) -> str:
    """Derive a filesystem‑safe profile directory name from *url*."""
    host = urlparse(url).netloc.lower().replace("www.", "").split(":")[0]
    return host.replace(".", "_")


# ---------------------------------------------------------------------------
# FetchMode constants
# ---------------------------------------------------------------------------


class FetchMode:
    REQUESTS = "requests"
    BROWSER = "browser"
    AUTO = "auto"

    _ALL = {REQUESTS, BROWSER, AUTO}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


# ---------------------------------------------------------------------------
# Shared Chrome options helper
# ---------------------------------------------------------------------------


def chrome_options(headless: bool = True) -> "ChromeOptions":
    """Build ChromeOptions with ``--headless=new`` and stable window size.

    Parameters
    ----------
    headless : bool, optional
        When ``True`` (default) the browser starts in headless mode.
        When ``False`` the browser window is visible.

    Returns
    -------
    selenium.webdriver.chrome.options.Options
    """
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    return options


# ---------------------------------------------------------------------------
# PageFetcher
# ---------------------------------------------------------------------------


class PageFetcher:
    """Shared page fetcher with Cloudflare‑aware fallback.

    Parameters
    ----------
    fetch_mode : str, optional
        ``"requests"``, ``"browser"``, or ``"auto"``.
    cloudflare : bool, optional
        Enable Cloudflare challenge detection (auto mode only).
    challenge_timeout : int, optional
        Seconds to wait for manual Cloudflare verification (auto mode only).
    profile_name : str, optional
        Browser profile directory name.  When ``None`` it is auto‑derived
        from the first URL passed to :meth:`fetch`.
    headless : bool, optional
        Run the browser in headless mode (no visible window).
        ``True`` by default.  Set to ``False`` for a visible browser
        (needed for interactive login or manual Cloudflare verification).
    """

    def __init__(
        self,
        fetch_mode: str = FetchMode.REQUESTS,
        cloudflare: bool = True,
        challenge_timeout: int = 180,
        profile_name: Optional[str] = None,
        headless: bool = True,
    ):
        if not FetchMode.is_valid(fetch_mode):
            raise ValueError(
                f"Invalid fetch_mode {fetch_mode!r}. "
                f"Choose from {sorted(FetchMode._ALL)}"
            )

        self.fetch_mode = fetch_mode
        self.cloudflare = cloudflare
        self.challenge_timeout = challenge_timeout
        self._profile_name = profile_name
        self.headless = headless

        # Shared requests session
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

        self._driver = None
        self._browser_mode_active = False
        self._log = logging.getLogger(self.__class__.__name__)
        self._log.debug("PageFetcher initialized (mode=%s)", self.fetch_mode)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        expected_selector: Optional[str] = None,
    ) -> str:
        """Fetch *url* and return its HTML source.

        Parameters
        ----------
        url : str
            The URL to fetch.
        expected_selector : str, optional
            CSS / BeautifulSoup selector expected to be present after
            navigation.  Only meaningful in ``browser`` mode or when
            the browser is active in ``auto`` mode.

        Returns
        -------
        str
            HTML source of the page.

        Raises
        ------
        CloudflareChallengeError
            In ``requests`` mode when a challenge is detected.
        FetchError
            Network / HTTP failure.
        SelectorMismatchError
            In ``browser`` or ``auto`` mode when *expected_selector*
            is not found after page load.
        """
        if self.fetch_mode == FetchMode.REQUESTS:
            return self._requests_fetch(url)

        if self.fetch_mode == FetchMode.BROWSER:
            return self._browser_fetch(url, expected_selector)

        # auto mode — once in browser, stay in browser
        if self._browser_mode_active:
            return self._browser_fetch(url, expected_selector)

        try:
            return self._requests_fetch(url)
        except CloudflareChallengeError:
            if self.cloudflare:
                self._log.info(
                    "Cloudflare challenge detected at %s — switching to browser",
                    url,
                )
                self._browser_mode_active = True
                return self._browser_fetch(url, expected_selector)
            raise

    def close(self) -> None:
        """Release all resources (browser, session).

        Safe to call even when the browser was never started.
        """
        if self._driver is not None:
            try:
                self._driver.quit()
                self._log.debug("Browser driver quit")
            except Exception:
                pass
            self._driver = None
        self.session.close()
        self._log.debug("PageFetcher closed")

    # ------------------------------------------------------------------
    # Internal: requests
    # ------------------------------------------------------------------

    def _requests_fetch(self, url: str) -> str:
        self._log.debug("Requests fetch: %s", url)
        try:
            resp = self.session.get(url, timeout=DEFAULT_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise FetchError(
                f"Request failed for {url}: {exc}"
            ) from exc

        if self.cloudflare and is_cloudflare_challenge(resp):
            raise CloudflareChallengeError(f"Cloudflare challenge detected at {url}")

        try:
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FetchError(
                f"Request failed for {url}: {exc}"
            ) from exc

        return resp.text

    # ------------------------------------------------------------------
    # Internal: browser (SeleniumBase UC)
    # ------------------------------------------------------------------

    def _browser_fetch(
        self,
        url: str,
        expected_selector: Optional[str] = None,
    ) -> str:
        self._ensure_browser(url)
        self._log.debug("Browser fetch: %s", url)
        try:
            self._driver.uc_open_with_reconnect(url, reconnect_time=3)
        except Exception as exc:
            raise FetchError(
                f"Browser navigation failed for {url}: {exc}"
            ) from exc

        if self.cloudflare and self._is_challenge_page(expected_selector):
            if self.headless:
                self._log.info(
                    "Cloudflare challenge detected — "
                    "awaiting automatic verification"
                )
                print(
                    "\n[Cloudflare] Cloudflare challenge detected — "
                    "awaiting automatic verification...\n"
                    f"[Cloudflare] Waiting up to {self.challenge_timeout}s …"
                )
            else:
                self._log.info(
                    "Cloudflare challenge detected after browser navigation — "
                    "waiting for manual verification in Chrome"
                )
                print(
                    "\n[Cloudflare] Complete the verification in the Chrome window.\n"
                    f"[Cloudflare] Waiting up to {self.challenge_timeout}s …"
                )
            deadline = time.monotonic() + self.challenge_timeout
            while time.monotonic() < deadline:
                try:
                    _ = self._driver.title
                except Exception:
                    raise BrowserUnavailableError(
                        "Browser was closed during Cloudflare verification wait"
                    )
                if not self._is_challenge_page(expected_selector):
                    if expected_selector is None:
                        break
                    try:
                        self._driver.wait_for_element(
                            expected_selector, timeout=1
                        )
                        break
                    except Exception:
                        pass
                time.sleep(1)
            else:
                os.makedirs("debug_pages", exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                import pathlib
                safe_host = urlparse(url).netloc.replace(".", "_")
                debug_path = f"debug_pages/cf_timeout_{safe_host}_{ts}.html"
                pathlib.Path(debug_path).write_text(
                    self._driver.page_source, encoding="utf-8"
                )
                if self.headless:
                    raise ChallengeTimeoutError(
                        f"Cloudflare automatic verification timed out after "
                        f"{self.challenge_timeout}s for {url}. "
                        f"Rerun with a visible browser by passing headless=False. "
                        f"Debug page saved to {debug_path}"
                    )
                raise ChallengeTimeoutError(
                    f"Manual Cloudflare verification timed out after "
                    f"{self.challenge_timeout}s for {url}. "
                    f"Debug page saved to {debug_path}"
                )

        if expected_selector:
            try:
                self._driver.wait_for_element(
                    expected_selector, timeout=10
                )
            except Exception:
                raise SelectorMismatchError(
                    f"Expected selector {expected_selector!r} not found on {url}"
                )

        return self._driver.page_source

    def _ensure_browser(self, url: str) -> None:
        if self._driver is not None:
            return

        if self._profile_name is None:
            self._profile_name = _default_profile_name(url)

        profiles_dir = os.path.join(os.getcwd(), PROFILES_DIR)
        os.makedirs(profiles_dir, exist_ok=True)
        profile_dir = os.path.join(profiles_dir, self._profile_name)
        os.makedirs(profile_dir, exist_ok=True)

        try:
            from seleniumbase import Driver
        except ImportError:
            raise BrowserUnavailableError(
                "seleniumbase is required for browser mode. "
                "Install it with: pip install seleniumbase"
            )

        try:
            driver_kwargs = {"uc": True, "user_data_dir": profile_dir}
            if self.headless:
                driver_kwargs["headless"] = True
            else:
                driver_kwargs["headed"] = True
            self._driver = Driver(**driver_kwargs)
        except Exception as exc:
            raise BrowserUnavailableError(
                f"Could not start UC Chrome: {exc}"
            ) from exc

        mode_str = "headless" if self.headless else "visible"
        self._log.info("Started %s UC Chrome (profile: %s)", mode_str, self._profile_name)

    def _is_challenge_page(self, expected_selector: Optional[str] = None) -> bool:
        """Return ``True`` if the current browser page looks like a
        Cloudflare challenge (based on strong HTML markers).

        A site may legitimately include a Cloudflare-related string in a
        third-party script.  A matching, site-specific expected selector is
        therefore decisive evidence that the requested content has loaded.
        """
        if expected_selector:
            try:
                if self._driver.find_elements("css selector", expected_selector):
                    return False
            except Exception:
                # Keep marker detection as the fallback for invalid selectors
                # or transient pages while navigation is still in progress.
                pass
        try:
            source = self._driver.page_source.lower()
        except Exception:
            return False
        return any(marker in source for marker in CF_STRONG_MARKERS)
