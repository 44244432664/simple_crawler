"""Unified execution API, batch v2 runner, and breaking cutover.

Task 6 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

Every supported crawl — TUI, batch, and programmatic — funnels through one
public interface built on the validated :class:`CrawlRequest` and the flow
engine:

* ``run_crawl(request) -> CrawlResult`` — one crawl end to end.
* ``run_batch(payload) -> list[CrawlResult]`` — versioned ``{"version": 2, "jobs": [...]}``.
* ``resolve_format(request) -> dict`` — alias resolution to a format JSON.

Design rules
------------
* **Alias resolution before any crawl work.**  The request host is resolved to
  a site alias and a format JSON before the flow is built or any network
  request happens.
* **Flow-less formats break the cutover.**  A known site whose format has no
  ``flow`` key raises :class:`MissingFlowError`; an unknown host raises
  :class:`UnknownSiteError`.  Neither ever falls back to a legacy crawler.
* **Legacy batch rejected.**  Non-v2 batch payloads fail with concise migration
  guidance (via :class:`BatchRequest`).
* **No supported path bypasses flow validation.**  ``run_crawl`` always builds
  and validates the flow through :func:`api.flow.build_flow` before executing,
  and returns a structured :class:`CrawlResult` (success flag, artifacts, and
  structured failures) rather than silently producing partial output.
* **Intermediate data preserved.**  On success or failure the result includes
  the output directory and the image-manifest path for troubleshooting.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from api.contracts import (
    BATCH_VERSION,
    ContentType,
    CrawlContext,
    CrawlRequest,
    CrawlResult,
    ExporterError,
    FailureRecord,
    IntermediatePaths,
    InvalidFlowError,
    InvalidRequestError,
    MissingFlowError,
    OutputFormat,
    PipelineError,
    UnknownSiteError,
    default_output_root,
)
from api.flow import build_flow, execute_flow

# ---------------------------------------------------------------------------
# Format / alias resolution
# ---------------------------------------------------------------------------


def load_aliases(aliases_path: str | os.PathLike) -> dict[str, dict[str, str]]:
    """Return ``{host: {"name": ..., "crawler_class": ...}}`` from a CSV.

    The CSV has a ``site`` column holding each host; a trailing ``/`` or
    whitespace on the site is ignored.
    """
    result: dict[str, dict[str, str]] = {}
    path = Path(aliases_path)
    if not path.exists():
        return result
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return result
            for row in reader:
                site = _normalize_alias_host(row.get("site") or "")
                name = (row.get("name") or "").strip()
                if not site or not name:
                    continue
                result[site] = {
                    "name": name,
                    "crawler_class": (row.get("crawler_class") or "").strip(),
                }
    except (OSError, UnicodeError, csv.Error) as exc:
        raise InvalidFlowError(f"Could not read aliases file {path}: {exc}") from exc
    return result


def _normalize_alias_host(value: str) -> str:
    """Normalize an alias site value and a request host identically."""
    candidate = str(value or "").strip().rstrip("/")
    if "://" in candidate:
        candidate = urlsplit(candidate).hostname or ""
    elif candidate.count(":") > 1 and not candidate.startswith("["):
        # Already-extracted IPv6 hostname (as returned by urlsplit.hostname).
        pass
    else:
        candidate = urlsplit(f"//{candidate}").hostname or ""
    return candidate.lower().rstrip(".").removeprefix("www.")


def _request_host(request: CrawlRequest) -> str:
    try:
        return _normalize_alias_host(urlsplit(request.url).hostname or "")
    except Exception:  # pragma: no cover
        return ""


class FormatResolver:
    """Resolve a request to its site format JSON against a formats tree.

    Parameters
    ----------
    formats_dir : str or Path or None
        Directory containing ``<name>.json`` format files (defaults to
        ``data/formats``).
    aliases_path : str or Path or None
        Path to the aliases CSV; ``None`` loads ``data/aliases.csv`` but does
        not fail if it is missing (an unknown-host error is produced instead).
    """

    def __init__(
        self,
        formats_dir: str | os.PathLike | None = None,
        aliases_path: str | os.PathLike | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        self.formats_dir = Path(formats_dir) if formats_dir else (root / "data" / "formats")
        self.aliases_path = (
            Path(aliases_path) if aliases_path is not None else (root / "data" / "aliases.csv")
        )
        self._aliases: dict[str, dict[str, str]] | None = None
        self._aliases_signature: tuple[int, int] | None = None

    @property
    def aliases(self) -> dict[str, dict[str, str]]:
        try:
            stat = self.aliases_path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            signature = None
        except OSError as exc:
            raise InvalidFlowError(
                f"Could not inspect aliases file {self.aliases_path}: {exc}"
            ) from exc
        if self._aliases is None or signature != self._aliases_signature:
            self._aliases = load_aliases(self.aliases_path)
            self._aliases_signature = signature
        return self._aliases

    def alias_for(self, request: CrawlRequest) -> dict[str, str]:
        host = _request_host(request)
        alias = self.aliases.get(host)
        if alias is None:
            raise UnknownSiteError(
                f"No site format is configured for host {host!r}. "
                "Register the site (add an alias + a flow-enabled format) before crawling."
            )
        return alias

    def format_for(self, request: CrawlRequest) -> dict:
        """Return the resolved format JSON for *request*.

        Raises
        ------
        UnknownSiteError
            No alias/host is registered.
        MissingFlowError
            The site's format has no ``flow`` key (flow-less legacy format) or
            its format file is missing.
        InvalidFlowError
            The format JSON is malformed (not a JSON object / unreadable).
        """
        alias = self.alias_for(request)
        name = alias["name"]
        path = self.formats_dir / f"{name}.json"
        if not path.exists():
            raise MissingFlowError(
                f"Site format file not found for alias {name!r} at {path}. "
                "A flow-enabled format is required for the breaking cutover."
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise InvalidFlowError(f"Site format {path!s} is not valid JSON: {exc}") from exc
        except (OSError, UnicodeError) as exc:
            raise InvalidFlowError(f"Could not read site format {path!s}: {exc}") from exc
        if not isinstance(data, dict):
            raise InvalidFlowError(f"Site format {path!s} must be a JSON object.")
        if data.get("flow") is None:
            raise MissingFlowError(
                f"Site format for {name!r} has no 'flow' key (legacy format). "
                "A flow is required for the breaking cutover; update the format JSON "
                "to add a 'flow' array before crawling."
            )
        return data


_default_resolver: FormatResolver | None = None


def get_default_resolver() -> FormatResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = FormatResolver()
    return _default_resolver


def resolve_format(
    request: object,
    *,
    formats_dir: str | os.PathLike | None = None,
    aliases_path: str | os.PathLike | None = None,
) -> dict:
    """Resolve *request* (a :class:`CrawlRequest` or JSON dict) to a format."""
    if not isinstance(request, CrawlRequest):
        request = CrawlRequest.from_dict(request)
    if formats_dir is not None or aliases_path is not None:
        return FormatResolver(formats_dir, aliases_path).format_for(request)
    return get_default_resolver().format_for(request)


# ---------------------------------------------------------------------------
# run_crawl
# ---------------------------------------------------------------------------


def _coerce_request(request: object) -> CrawlRequest:
    if isinstance(request, CrawlRequest):
        return request
    return CrawlRequest.from_dict(request)


def _placeholder_request(request: object = None) -> CrawlRequest:
    url = "https://example.com/"
    if isinstance(request, dict) and isinstance(request.get("url"), str):
        url = request["url"]
    try:
        return CrawlRequest(
            url=url,
            content_type=ContentType.NOVEL,
            output_format=OutputFormat.EPUB,
        )
    except InvalidRequestError:
        pass
    return CrawlRequest(
        url="https://example.com/",
        content_type=ContentType.NOVEL,
        output_format=OutputFormat.EPUB,
    )


def _failure(
    stage: str,
    exc: BaseException,
    *,
    chapter_ordinal: int | None = None,
    url: str | None = None,
) -> FailureRecord:
    return FailureRecord(
        stage=stage,
        code=type(exc).__name__,
        message=str(exc) or type(exc).__name__,
        chapter_ordinal=chapter_ordinal,
        url=url,
    )


def run_crawl(
    request: object,
    *,
    formats_dir: str | os.PathLike | None = None,
    aliases_path: str | os.PathLike | None = None,
    interactive: bool = False,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str, int, int], None] | None = None,
    _resolver: FormatResolver | None = None,
) -> CrawlResult:
    """Run one crawl request end to end and return a structured result.

    Resolves the alias and validates the flow **before any crawl work**, so a
    flow-less legacy format or an unknown host fails with the structured
    breaking-cutover error and never touches the network.

    The returned :class:`CrawlResult` always carries ``success``, any produced
    artifacts, structured failures, and intermediate paths for troubleshooting.
    """
    # 1. Validate the request (no network, no output).
    try:
        req = _coerce_request(request)
    except PipelineError as exc:
        placeholder = _placeholder_request(request)
        return CrawlResult(request=placeholder, success=False, failures=(_failure("request", exc),))

    log_events: list[str] = []

    def write_log(message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        log_events.append(f"{timestamp} {message}")
        if log_fn is not None:
            log_fn(message)

    context = CrawlContext(request=req, format_definition={}, log=write_log, progress=progress_fn)
    write_log(f"Crawl started: {_safe_url_for_log(req.url)}")

    try:
        resolver = _resolver or (
            FormatResolver(formats_dir, aliases_path)
            if (formats_dir is not None or aliases_path is not None)
            else get_default_resolver()
        )
        format_definition = resolver.format_for(req)
        write_log("Site format resolved.")
    except PipelineError as exc:
        return _result_from_context(
            req, context, success=False, failure=_failure("resolve", exc), log_events=log_events
        )
    except Exception as exc:  # defensive: public API always returns a structured result
        return _result_from_context(
            req, context, success=False, failure=_failure("resolve", exc), log_events=log_events
        )

    # 2. Build + validate the flow (still no network), then execute.
    try:
        flow = build_flow(req, format_definition, interactive=interactive)
        write_log("Crawl flow validated.")
    except PipelineError as exc:
        return _result_from_context(
            req, context, success=False, failure=_failure("flow", exc), log_events=log_events
        )
    except Exception as exc:  # defensive
        return _result_from_context(
            req, context, success=False, failure=_failure("flow", exc), log_events=log_events
        )

    try:
        execute_flow(flow, context)
    except KeyboardInterrupt:  # pragma: no cover
        try:
            _persist_novel_info(
                context,
                success=False,
            )
            _persist_crawl_log(
                context,
                log_events,
                success=False,
                failures=(_failure(context.current_stage or "crawl", KeyboardInterrupt()),),
            )
        except Exception:
            pass
        raise
    except PipelineError as exc:
        return _result_from_context(
            req,
            context,
            success=False,
            failure=_failure(
                context.current_stage or "crawl",
                exc,
                chapter_ordinal=getattr(exc, "chapter_ordinal", None),
                url=getattr(exc, "url", None),
            ),
            log_events=log_events,
        )
    except Exception as exc:  # defensive
        return _result_from_context(
            req,
            context,
            success=False,
            failure=_failure(context.current_stage or "crawl", exc),
            log_events=log_events,
        )

    try:
        _validate_completed_context(context)
    except PipelineError as exc:
        return _result_from_context(
            req, context, success=False, failure=_failure("export", exc), log_events=log_events
        )

    return _result_from_context(req, context, success=True, failure=None, log_events=log_events)


def _validate_completed_context(context: CrawlContext) -> None:
    """Refuse to publish a successful result without real completed artifacts."""
    if not context.artifacts:
        raise ExporterError("create_ebook completed without producing any artifacts.")
    for artifact in context.artifacts:
        if artifact.status != "created":
            raise ExporterError(
                f"create_ebook returned artifact status {artifact.status!r}, expected 'created'."
            )
        if not artifact.path or not os.path.exists(artifact.path):
            raise ExporterError(
                f"create_ebook reported an artifact that does not exist: {artifact.path!r}."
            )


def _result_from_context(
    request: CrawlRequest,
    context: CrawlContext,
    *,
    success: bool,
    failure: FailureRecord | None,
    log_events: list[str],
) -> CrawlResult:
    if not context.output_dir:
        context.output_dir = request.output_dir or _fallback_output_dir(request)
    failures = [failure] if failure else []
    metadata_path = None
    if request.content_type is ContentType.NOVEL and context.output_dir:
        try:
            metadata_path = _persist_novel_info(
                context,
                success=success,
            )
        except Exception as exc:
            failures.append(_failure("persist", exc))
            success = False
    log_path = None
    try:
        log_path = _persist_crawl_log(
            context,
            log_events,
            success=success,
            failures=tuple(failures),
        )
    except Exception as exc:
        failures.append(_failure("log", exc))
        success = False
    all_failures = tuple(failures)
    intermediate = None
    if context.output_dir:
        img_info_path = os.path.join(context.output_dir, "img", "img_info.json")
        intermediate = IntermediatePaths(
            output_dir=context.output_dir,
            img_info_path=img_info_path if os.path.isfile(img_info_path) else None,
            metadata_path=metadata_path,
            log_path=log_path,
        )
    return CrawlResult(
        request=request,
        success=success,
        artifacts=tuple(context.artifacts),
        failures=all_failures,
        metadata=context.metadata,
        volumes=tuple(context.volumes),
        chapter_count=len(context.chapter_contents),
        image_count=len(context.image_entries),
        intermediate=intermediate,
    )


def _safe_url_for_log(url: str) -> str:
    """Keep query and credential values out of a persistent crawl log."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _fallback_output_dir(request: CrawlRequest) -> str:
    """Return a stable log location when a crawl fails before title extraction."""
    host = (urlsplit(request.url).hostname or "unknown").lower()
    safe_host = re.sub(r"[^a-z0-9._-]+", "_", host).strip("._") or "unknown"
    return os.path.join(default_output_root(request.content_type), f"unknown_{safe_host}")


def _persist_crawl_log(
    context: CrawlContext,
    events: list[str],
    *,
    success: bool,
    failures: tuple[FailureRecord, ...],
) -> str | None:
    """Append one timestamped crawl record and its terminal state to disk."""
    if not context.output_dir:
        return None
    logs_dir = os.path.join(context.output_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    path = os.path.join(logs_dir, "crawl_log.txt")
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"\n{timestamp} --- crawl run ---\n")
        for event in events:
            handle.write(f"{event}\n")
        state = "completed" if success else "failed"
        handle.write(f"{timestamp} Crawl state: {state}\n")
        for failure in failures:
            detail = f"{failure.stage} {failure.code}: {failure.message}"
            if failure.chapter_ordinal is not None:
                detail += f" (chapter {failure.chapter_ordinal})"
            if failure.url:
                detail += f" [{_safe_url_for_log(failure.url)}]"
            handle.write(f"{timestamp} Failure: {detail}\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _persist_novel_info(
    context: CrawlContext,
    *,
    success: bool,
) -> str | None:
    """Atomically persist legacy-compatible novel metadata and partial crawl data."""
    if context.request.content_type is not ContentType.NOVEL or not context.output_dir:
        return None

    metadata = context.metadata
    info = metadata.to_dict() if metadata is not None else {}
    info["cover_image"] = info.get("cover_url") or ""
    info["novel_url"] = (
        metadata.source_url if metadata is not None and metadata.source_url else context.request.url
    )
    info["chapter_links"] = [
        chapter.url
        for volume in context.volumes
        for chapter in volume.chapters
        if chapter.url
    ]

    contents = {
        int(content.get("ordinal", 0)): content
        for content in context.chapter_contents
        if int(content.get("ordinal", 0)) > 0
    }
    volumes = []
    for volume in context.volumes:
        chapter_contents = []
        for chapter in volume.chapters:
            content = contents.get(chapter.ordinal)
            if content is None:
                continue
            has_images = any(
                entry.chapter_ordinal == chapter.ordinal
                for entry in context.image_entries
            )
            chapter_contents.append(
                {
                    "chapter_title": str(content.get("title") or chapter.title),
                    "chapter_content": str(content.get("html") or ""),
                    "chapter_img_folder": (
                        os.path.join(context.output_dir, "img") if has_images else None
                    ),
                }
            )
        volumes.append(
            {
                "title": volume.title,
                "cover_image": volume.cover_url or None,
                "chapter_links": [chapter.url for chapter in volume.chapters if chapter.url],
                "chapter_contents": chapter_contents,
            }
        )

    payload = {
        "info": info,
        "volumes": volumes,
        "status": "completed" if success else "failed",
    }
    os.makedirs(context.output_dir, exist_ok=True)
    path = os.path.join(context.output_dir, "novel_info.json")
    fd, temporary = tempfile.mkstemp(
        prefix=".novel_info.", suffix=".tmp", dir=context.output_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Batch v2 + legacy rejection
# ---------------------------------------------------------------------------


def run_batch(
    payload: object,
    *,
    formats_dir: str | os.PathLike | None = None,
    aliases_path: str | os.PathLike | None = None,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str, int, int], None] | None = None,
) -> list[CrawlResult]:
    """Run a versioned batch payload.

    ``payload`` must be ``{"version": 2, "jobs": [...]}``.  Each job goes
    through the same request validator and pipeline as a single crawl; results
    come back **in job order** (a failed job yields a failed result, never an
    exception).

    Legacy (non-v2) payloads raise :class:`InvalidRequestError` with migration
    guidance instead of falling through to the old crawler behavior.
    """
    jobs = _batch_jobs(payload)
    resolver = (
        FormatResolver(formats_dir, aliases_path)
        if (formats_dir is not None or aliases_path is not None)
        else get_default_resolver()
    )
    return [
        run_crawl(
            job,
            formats_dir=formats_dir,
            aliases_path=aliases_path,
            log_fn=log_fn,
            progress_fn=progress_fn,
            _resolver=resolver,
        )
        for job in jobs
    ]


def _batch_jobs(payload: object) -> list[object]:
    """Validate the v2 envelope while leaving each job to ``run_crawl``.

    This preserves one ordered structured result per input job, including jobs
    whose individual CrawlRequest validation fails.
    """
    if not isinstance(payload, dict):
        raise InvalidRequestError(
            'Legacy batch input is not supported; use {"version": 2, "jobs": [...]}.'
        )
    version = payload.get("version")
    if version != BATCH_VERSION:
        raise InvalidRequestError(
            f"Unsupported batch version {version!r}. Expected {BATCH_VERSION}. "
            'Migrate to {"version": 2, "jobs": [...]}.'
        )
    if "jobs" not in payload:
        raise InvalidRequestError("Batch payload is missing 'jobs'.")
    jobs = payload["jobs"]
    if not isinstance(jobs, list):
        raise InvalidRequestError("Batch 'jobs' must be a list of request objects.")
    return jobs


def reject_legacy_batch(payload: object) -> None:
    """Reject any non-v2 batch payload with concise migration guidance."""
    _batch_jobs(payload)


def reject_legacy_crawler() -> None:
    """Breaking-cutover guard for legacy crawler entry points.

    Called by any wrapper that used to dispatch to the old crawler classes so
    that no supported path silently bypasses flow validation.
    """
    raise MissingFlowError(
        "Legacy crawler paths are not supported after the breaking cutover. "
        "Route crawls through run_crawl / run_batch with a flow-enabled format."
    )


__all__ = [
    "FormatResolver",
    "get_default_resolver",
    "load_aliases",
    "reject_legacy_batch",
    "reject_legacy_crawler",
    "resolve_format",
    "run_batch",
    "run_crawl",
]
