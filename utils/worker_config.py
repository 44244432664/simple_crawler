"""Shared limits, validation, and thread infrastructure for chapter crawling."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from utils.fetcher import PageFetcher

# ---------------------------------------------------------------------------
# Worker limits
# ---------------------------------------------------------------------------

DEFAULT_MAX_WORKERS = 4
MAX_WORKERS = 5


def validate_max_workers(value: int) -> int:
    """Return a safe worker count or raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"max_workers must be an integer from 1 to {MAX_WORKERS}, got {value!r}")
    if not 1 <= value <= MAX_WORKERS:
        raise ValueError(
            f"max_workers must be between 1 and {MAX_WORKERS}, got {value}"
        )
    return value


# ---------------------------------------------------------------------------
# Thread-safe log lock
# ---------------------------------------------------------------------------

log_lock = threading.Lock()
"""Module-level lock that serialises ``update_log`` writes to the crawl log.

Every ``NovelCrawler`` instance shares this lock so that concurrent worker
threads never produce interleaved log lines.
"""


# ---------------------------------------------------------------------------
# Global request pacer
# ---------------------------------------------------------------------------

class GlobalPacer:
    """Monotonic, lock-protected pacer that spaces chapter fetch starts.

    Each call to :meth:`acquire` blocks until at least *interval* seconds
    have elapsed since the previous acquisition across the entire pool.

    Parameters
    ----------
    interval : float
        Minimum seconds between consecutive chapter fetch starts.
        A value of ``0`` disables pacing entirely (no artificial delay).
    """

    def __init__(self, interval: float) -> None:
        self._interval = max(0.0, float(interval))
        self._lock = threading.Lock()
        self._next_allowed: float = 0.0

    def acquire(self) -> None:
        """Block until the next fetch is allowed by the pacing policy.

        If *interval* is zero this returns immediately.
        """
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self._interval

    def close(self) -> None:
        """Release resources (no-op for the pacer itself)."""
        pass


# ---------------------------------------------------------------------------
# Worker-fetcher factory
# ---------------------------------------------------------------------------

class WorkerFetcherFactory:
    """Create and track per-thread requests-only ``PageFetcher`` instances.

    Each executor thread should call :meth:`get_fetcher` once (or at most
    once per thread-local context).  All created fetchers are tracked and
    can be closed in bulk via :meth:`close_all`.

    Parameters
    ----------
    cloudflare : bool
        Forwarded to every worker ``PageFetcher``.
    challenge_timeout : int
        Forwarded to every worker ``PageFetcher``.
    profile_name : str or None
        Forwarded to every worker ``PageFetcher``.
    """

    def __init__(
        self,
        cloudflare: bool = True,
        challenge_timeout: int = 180,
        profile_name: str | None = None,
    ) -> None:
        self._cloudflare = cloudflare
        self._challenge_timeout = challenge_timeout
        self._profile_name = profile_name
        self._fetchers: list[PageFetcher] = []
        self._lock = threading.Lock()
        self._local = threading.local()

    def get_fetcher(self) -> PageFetcher:
        """Create a fresh requests-only ``PageFetcher`` and track it.

        The fetcher inherits cloudflare/retry configuration from the parent
        but is **always** in ``"requests"`` mode — it will never start a
        browser.
        """
        from utils.fetcher import PageFetcher as _PageFetcher

        fetcher = getattr(self._local, "fetcher", None)
        if fetcher is not None:
            return fetcher

        fetcher = _PageFetcher(
            fetch_mode="requests",
            cloudflare=self._cloudflare,
            challenge_timeout=self._challenge_timeout,
            profile_name=self._profile_name,
            headless=True,
        )
        self._local.fetcher = fetcher
        with self._lock:
            self._fetchers.append(fetcher)
        return fetcher

    def close_all(self) -> None:
        """Close every tracked fetcher exactly once.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        with self._lock:
            fetchers = list(self._fetchers)
            self._fetchers.clear()
            self._local = threading.local()
        for fetcher in fetchers:
            try:
                fetcher.close()
            except Exception:
                pass

    @property
    def count(self) -> int:
        """Number of fetchers currently tracked (not yet closed)."""
        with self._lock:
            return len(self._fetchers)


# ---------------------------------------------------------------------------
# Chapter scheduling
# ---------------------------------------------------------------------------


@dataclass
class ChapterJob:
    """Descriptor for a single chapter to be scheduled by :class:`ChapterScheduler`.

    Attributes
    ----------
    position : int
        Original source-order index (0-based).  The scheduler returns
        results sorted by this value regardless of completion order.
    url : str
        Chapter URL to crawl.
    img_output_dir : str
        Directory where chapter images should be saved.
    img_prefix : str
        Filename prefix for chapter images (e.g. ``"vol1_chap3"``).
    label : str
        Human-readable label used for debug pages and log messages.
    max_retries : int
        Maximum retry attempts passed to the work function.
    """

    position: int
    url: str
    img_output_dir: str = ""
    img_prefix: str = ""
    label: str = ""
    max_retries: int = 5


class ChapterScheduler:
    """Submit chapter jobs concurrently and return results in source order.

    The scheduler is decoupled from any specific crawler.  The caller
    provides a *work_fn* callable that receives a :class:`ChapterJob` and
    returns a chapter-data dict on success or ``None`` on permanent failure.

    Parameters
    ----------
    work_fn : callable
        ``(job: ChapterJob) -> dict | None``.  Raise an exception or
        return ``None`` to mark a chapter as permanently failed.
    chapters : list[ChapterJob]
        Ordered collection of chapter descriptors.
    max_workers : int
        Maximum concurrent threads (clamped to ``len(chapters)``).
    progress_fn : callable or None
        ``(completed: int, total: int) -> None`` invoked after each
        chapter completes (success or failure).
    """

    def __init__(
        self,
        work_fn: Callable[[ChapterJob], Optional[dict]],
        chapters: list[ChapterJob],
        max_workers: int,
        progress_fn: Optional[Callable[[int, int], None]] = None,
        error_fn: Optional[Callable[[ChapterJob, Exception], None]] = None,
    ) -> None:
        self._work_fn = work_fn
        self._chapters = list(chapters)
        self._max_workers = max_workers
        self._progress_fn = progress_fn
        self._error_fn = error_fn

    def run(self) -> list[dict]:
        """Execute all chapter jobs and return successful results in source order.

        Returns
        -------
        list[dict]
            Chapter payloads in the same order as the input *chapters*.
            Permanently failed chapters are omitted.

        Behaviour on interruption
        -------------------------
        A ``KeyboardInterrupt`` cancels pending futures, waits for active
        workers to finish, and is re-raised to the caller.  This lets the
        caller avoid presenting partial results as a completed crawl.
        """
        if not self._chapters:
            return []

        n = len(self._chapters)
        effective_workers = min(self._max_workers, n)
        results: dict[int, dict] = {}
        completed = 0

        def _invoke(job: ChapterJob) -> Optional[dict]:
            return self._work_fn(job)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers)
        future_to_job = {}
        try:
            future_to_job = {
                executor.submit(_invoke, job): job
                for job in self._chapters
            }

            for future in concurrent.futures.as_completed(future_to_job):
                job = future_to_job[future]
                try:
                    result = future.result()
                    if result is not None:
                        results[job.position] = result
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    if self._error_fn is not None:
                        self._error_fn(job, exc)
                completed += 1
                if self._progress_fn is not None:
                    self._progress_fn(completed, n)
        except KeyboardInterrupt:
            # Cancel work that has not started, wait for active HTTP requests
            # to finish, and propagate the interruption to the crawl caller.
            for future in future_to_job:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        except Exception:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

        return [results[i] for i in range(n) if i in results]
