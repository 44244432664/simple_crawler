"""Shared, decision-complete pipeline contracts.

Task 1 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).
This module defines the enums, the validated :class:`CrawlRequest`, the value
types produced and consumed by the flow ("metadata -> volumes -> chapters"),
result/error reporting types, and the batch v2 container.

Design rules
------------
* Every type is a dict/JSON-serializable, immutable value unless it is a
  runtime mutable context (:class:`CrawlContext`).
* Validation is **pure**: it performs no network access and never creates,
  writes, or inspects output directories.
* Valid requests normalize deterministically: the same JSON object always
  produces the same fully-normalized :class:`CrawlRequest`.
* Invalid requests raise :class:`InvalidRequestError` before any crawl work,
  filesystem side effect, or network call happens.

Combination matrix
------------------
=================  ==============================  =========================
content_type       output_format                   packaging
=================  ==============================  =========================
novel              epub, pdf                       combined, per_volume
comic              cbz, pdf, folder                per_chapter
gallery            cbz, pdf, folder                per_gallery
=================  ==============================  =========================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import ipaddress
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from utils.fetcher import FetchMode
from utils.worker_config import DEFAULT_MAX_WORKERS, validate_max_workers

# ---------------------------------------------------------------------------
# Batch payload + manifest constants
# ---------------------------------------------------------------------------

BATCH_VERSION = 2
"""Version of the shared batch JSON payload ``{"version": 2, "jobs": [...]}``."""

IMAGE_MANIFEST_SCHEMA_VERSION = 1
"""Version of each entry stored in ``<output>/<name>/img/img_info.json``."""

DEFAULT_SLEEP_MS = 1000
"""Default inter-request delay in milliseconds."""

DEFAULT_HEADLESS = True
"""Default browser window mode when no format or explicit value is given."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContentType(str, Enum):
    """Family of content a crawl can produce."""

    NOVEL = "novel"
    COMIC = "comic"
    GALLERY = "gallery"


_OUTPUT_TYPE_DIRS = {
    ContentType.NOVEL: "Novel",
    ContentType.COMIC: "Comic",
    ContentType.GALLERY: "Gallery",
}


def default_output_root(content_type: ContentType) -> str:
    """Return the legacy output root for a content type."""
    return str(Path("outputs") / _OUTPUT_TYPE_DIRS[content_type])


class OutputFormat(str, Enum):
    """Final artifact kinds supported by the exporters."""

    EPUB = "epub"
    PDF = "pdf"
    CBZ = "cbz"
    FOLDER = "folder"


class SelectionMode(str, Enum):
    """Which subset of chapters a request targets."""

    FULL = "full"
    RANGE = "range"
    SINGLE = "single"


class PackagingMode(str, Enum):
    """How artifacts are split when exported.

    Novels are either one combined book or one artifact per volume.  Comics
    and galleries have distinct, fixed splitting semantics so callers never
    need to infer whether ``per_volume`` means a chapter or a gallery.
    """

    COMBINED = "combined"
    PER_VOLUME = "per_volume"
    PER_CHAPTER = "per_chapter"
    PER_GALLERY = "per_gallery"


class ImageStatus(str, Enum):
    """Lifecycle of one image download recorded in the image manifest."""

    DOWNLOADED = "downloaded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Supported output combinations
# ---------------------------------------------------------------------------

SUPPORTED_OUTPUT_COMBINATIONS: dict[ContentType, dict[OutputFormat, tuple[PackagingMode, ...]]] = {
    ContentType.NOVEL: {
        OutputFormat.EPUB: (PackagingMode.COMBINED, PackagingMode.PER_VOLUME),
        OutputFormat.PDF: (PackagingMode.COMBINED, PackagingMode.PER_VOLUME),
    },
    ContentType.COMIC: {
        OutputFormat.CBZ: (PackagingMode.PER_CHAPTER,),
        OutputFormat.PDF: (PackagingMode.PER_CHAPTER,),
        OutputFormat.FOLDER: (PackagingMode.PER_CHAPTER,),
    },
    ContentType.GALLERY: {
        OutputFormat.CBZ: (PackagingMode.PER_GALLERY,),
        OutputFormat.PDF: (PackagingMode.PER_GALLERY,),
        OutputFormat.FOLDER: (PackagingMode.PER_GALLERY,),
    },
}

_DEFAULT_PACKAGING: dict[ContentType, PackagingMode] = {
    ContentType.NOVEL: PackagingMode.COMBINED,
    ContentType.COMIC: PackagingMode.PER_CHAPTER,
    ContentType.GALLERY: PackagingMode.PER_GALLERY,
}


def packaging_modes_for(content_type: ContentType, output_format: OutputFormat) -> tuple[PackagingMode, ...]:
    """Return the packaging modes valid for a content/output pair (may be empty)."""
    formats = SUPPORTED_OUTPUT_COMBINATIONS.get(content_type, {})
    return formats.get(output_format, ())


def default_packaging(content_type: ContentType) -> PackagingMode:
    """Return the packaging used when the caller does not choose one."""
    return _DEFAULT_PACKAGING[content_type]


def is_supported_combination(
    content_type: ContentType,
    output_format: OutputFormat,
    packaging: PackagingMode,
) -> bool:
    """Return whether *packaging* is allowed for the content/output pair."""
    return packaging in packaging_modes_for(content_type, output_format)


def _combination_error(content_type: ContentType, output_format: OutputFormat, packaging: PackagingMode) -> str:
    allowed_formats = list(SUPPORTED_OUTPUT_COMBINATIONS.get(content_type, {}).keys())
    allowed_packaging = packaging_modes_for(content_type, output_format)
    return (
        f"Unsupported output for {content_type.value}: "
        f"output_format={output_format.value!r}, packaging={packaging.value!r}. "
        f"Supported formats for {content_type.value}: "
        f"{', '.join(f.value for f in allowed_formats) or 'none'}. "
        f"Supported packaging for this format: "
        f"{', '.join(p.value for p in allowed_packaging) or 'none'}."
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Base class for every pipeline error."""


class InvalidRequestError(PipelineError):
    """A request or batch payload is invalid before any work starts."""


class MissingFlowError(PipelineError):
    """A site format has no ``flow`` key.

    This is the breaking-cutover guard: flow-less legacy formats are rejected
    instead of silently falling back to the old crawler behavior.
    """


class UnknownSiteError(PipelineError):
    """No site format/alias is registered for the request host."""


class InvalidFlowError(PipelineError):
    """A flow definition fails schema, ordering, or allowlist validation."""


class FetchFailureError(PipelineError):
    """A raw page could not be fetched (network, HTTP, Cloudflare, or timeout)."""


class IncompleteCrawlError(PipelineError):
    """Required chapters/images failed; the crawl is incomplete.

    Intermediate data and logs are kept, but no final artifact may be reported
    as successful.
    """


class ExporterError(PipelineError):
    """An artifact could not be produced (EPUB/PDF/CBZ/folder export failure)."""


class AIConfigError(PipelineError):
    """AI provider configuration is missing or invalid."""


class AIValidationError(PipelineError):
    """AI-generated site definitions failed deterministic validation."""


# ---------------------------------------------------------------------------
# Normalization helpers (pure; no I/O)
# ---------------------------------------------------------------------------


def _coerce_enum(value: object, enum_cls: type[Enum], label: str) -> Enum:
    """Coerce a string to *enum_cls* or raise :class:`InvalidRequestError`."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value.strip().lower())
        except ValueError:
            options = ", ".join(member.value for member in enum_cls)
            raise InvalidRequestError(
                f"{label} must be one of: {options}. Got {value!r}."
            ) from None
    options = ", ".join(member.value for member in enum_cls)
    raise InvalidRequestError(
        f"{label} must be a string or {enum_cls.__name__}. Got {type(value).__name__}."
    )


def _normalize_url(value: object, label: str = "url") -> str:
    """Canonicalize an http(s) URL or raise :class:`InvalidRequestError`.

    A URL without a scheme is treated as ``https://`` (the TUI already accepts
    scheme-less input).  The result is canonicalized: lowercase scheme/host,
    port preserved, fragment dropped.  Validation never touches the network.
    """
    if not isinstance(value, str):
        raise InvalidRequestError(f"{label} must be a string.")
    trimmed = value.strip()
    if not trimmed:
        raise InvalidRequestError(f"{label} is required.")
    if "://" in trimmed:
        scheme = trimmed.split("://", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise InvalidRequestError(f"{label} must be an absolute http(s) URL.")
        candidate = trimmed
    else:
        candidate = f"https://{trimmed}"

    if any(char.isspace() or ord(char) < 32 for char in candidate):
        raise InvalidRequestError(f"{label} must not contain whitespace or control characters.")
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError as exc:
        raise InvalidRequestError(f"{label} is malformed.") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise InvalidRequestError(f"{label} must be an absolute http(s) URL with a host.")
    if parsed.netloc.endswith(":") or port == 0:
        raise InvalidRequestError(f"{label} has an invalid port.")
    if username is not None or password is not None:
        raise InvalidRequestError(f"{label} must not contain credentials.")
    try:
        ipaddress.ip_address(host)
        host_is_ip = True
    except ValueError:
        host_is_ip = False
    if "." not in host and host != "localhost" and not host_is_ip:
        raise InvalidRequestError(f"{label} does not look like a real host.")

    normalized_host = host.lower()
    host_part = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    netloc = f"{host_part}:{port}" if port is not None else host_part
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, parsed.query, ""))


def _coerce_index(value: object, label: str) -> int | None:
    """Require *value* to be ``None`` or a non-bool integer."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequestError(f"{label} must be an integer or None. Got {type(value).__name__}.")
    return value


def _coerce_bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    raise InvalidRequestError(f"{label} must be a boolean. Got {type(value).__name__}.")


def _normalize_fetch_mode(value: object) -> str | None:
    """Allow ``None`` (resolve from the site format) or a valid fetch mode."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRequestError(
            "fetch_mode must be None, 'requests', 'browser', or 'auto'. "
            f"Got {value!r}."
        )
    normalized = value.strip().lower()
    if not FetchMode.is_valid(normalized):
        raise InvalidRequestError(
            "fetch_mode must be None, 'requests', 'browser', or 'auto'. "
            f"Got {value!r}."
        )
    return normalized


def _normalize_output_dir(value: object) -> str | None:
    """Accept a path string or ``Path``; stay a pure value (no checks/mkdir)."""
    if value is None:
        return None
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError("output_dir must be a non-empty path or None.")
    return value.strip()


# ---------------------------------------------------------------------------
# CrawlRequest
# ---------------------------------------------------------------------------

_ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "url",
        "content_type",
        "output_format",
        "selection",
        "packaging",
        "start_index",
        "end_index",
        "chapter_url",
        "output_dir",
        "fetch_mode",
        "headless",
        "sleep_ms",
        "max_retries",
        "max_workers",
        "keep_logged_in",
    }
)

_REQUIRED_REQUEST_FIELDS = ("url", "content_type", "output_format")


@dataclass(frozen=True)
class CrawlRequest:
    """A fully validated, normalized crawl job shared by every caller.

    Replaces the crawler-specific argument dictionaries: the TUI, batch jobs,
    and direct callers all describe one crawl with this single object.
    """

    url: str
    content_type: ContentType
    output_format: OutputFormat
    selection: SelectionMode = SelectionMode.FULL
    packaging: PackagingMode | None = None
    start_index: int | None = None
    end_index: int | None = None
    chapter_url: str | None = None
    output_dir: str | None = None
    fetch_mode: str | None = None
    headless: bool = DEFAULT_HEADLESS
    sleep_ms: int = DEFAULT_SLEEP_MS
    max_retries: int | None = None
    max_workers: int = DEFAULT_MAX_WORKERS
    keep_logged_in: bool = False

    def __post_init__(self) -> None:
        url = _normalize_url(self.url)
        content_type = _coerce_enum(self.content_type, ContentType, "content_type")  # type: ignore[assignment]
        output_format = _coerce_enum(self.output_format, OutputFormat, "output_format")  # type: ignore[assignment]
        selection = _coerce_enum(self.selection, SelectionMode, "selection")  # type: ignore[assignment]
        packaging = (
            _coerce_enum(self.packaging, PackagingMode, "packaging")  # type: ignore[assignment]
            if self.packaging is not None
            else default_packaging(content_type)  # type: ignore[arg-type]
        )
        start_index = _coerce_index(self.start_index, "start_index")
        end_index = _coerce_index(self.end_index, "end_index")
        chapter_url = _normalize_url(self.chapter_url, label="chapter_url") if self.chapter_url is not None else None
        output_dir = _normalize_output_dir(self.output_dir)
        fetch_mode = _normalize_fetch_mode(self.fetch_mode)
        headless = _coerce_bool(self.headless, "headless")
        keep_logged_in = _coerce_bool(self.keep_logged_in, "keep_logged_in")

        if isinstance(self.sleep_ms, bool) or not isinstance(self.sleep_ms, int) or self.sleep_ms < 0:
            raise InvalidRequestError(
                f"sleep_ms must be a non-negative integer. Got {self.sleep_ms!r}."
            )
        if (
            self.max_retries is not None
            and (isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or self.max_retries < 0)
        ):
            raise InvalidRequestError(
                f"max_retries must be a non-negative integer or None. Got {self.max_retries!r}."
            )
        try:
            max_workers = validate_max_workers(self.max_workers)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

        # Selection coherence
        if selection is SelectionMode.RANGE:
            if start_index is None or end_index is None:
                raise InvalidRequestError(
                    "RANGE selection requires start_index and end_index."
                )
            if start_index < 1:
                raise InvalidRequestError("start_index must be at least 1.")
            if end_index < start_index:
                raise InvalidRequestError(
                    f"end_index must be >= start_index (got end={end_index}, start={start_index})."
                )
            if chapter_url is not None:
                raise InvalidRequestError("chapter_url is only valid for SINGLE selection.")
        elif selection is SelectionMode.SINGLE:
            if chapter_url is None:
                raise InvalidRequestError("SINGLE selection requires a chapter_url.")
            if start_index is not None or end_index is not None:
                raise InvalidRequestError(
                    "start_index/end_index are not valid for SINGLE selection."
                )
        else:  # FULL
            if start_index is not None or end_index is not None or chapter_url is not None:
                raise InvalidRequestError(
                    "FULL selection must not define start_index, end_index, or chapter_url."
                )

        # Output combination
        if not is_supported_combination(content_type, output_format, packaging):
            raise InvalidRequestError(
                _combination_error(content_type, output_format, packaging)
            )

        object.__setattr__(self, "url", url)
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "output_format", output_format)
        object.__setattr__(self, "selection", selection)
        object.__setattr__(self, "packaging", packaging)
        object.__setattr__(self, "start_index", start_index)
        object.__setattr__(self, "end_index", end_index)
        object.__setattr__(self, "chapter_url", chapter_url)
        object.__setattr__(self, "output_dir", output_dir)
        object.__setattr__(self, "fetch_mode", fetch_mode)
        object.__setattr__(self, "headless", headless)
        object.__setattr__(self, "sleep_ms", self.sleep_ms)
        object.__setattr__(self, "max_retries", self.max_retries)
        object.__setattr__(self, "max_workers", max_workers)
        object.__setattr__(self, "keep_logged_in", keep_logged_in)

    # ------------------------------------------------------------------
    # Serialization (batch input)
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: object) -> "CrawlRequest":
        """Build and validate a request from a JSON object.

        Unknown keys are rejected so typos fail deterministically.  Required
        fields are ``url``, ``content_type``, and ``output_format``.
        """
        if not isinstance(data, dict):
            raise InvalidRequestError("CrawlRequest payload must be a JSON object.")
        unknown = set(data) - _ALLOWED_REQUEST_FIELDS
        if unknown:
            raise InvalidRequestError(
                f"Unknown CrawlRequest field(s): {', '.join(sorted(unknown))}."
            )
        missing = [key for key in _REQUIRED_REQUEST_FIELDS if key not in data]
        if missing:
            raise InvalidRequestError(
                f"Missing required CrawlRequest field(s): {', '.join(missing)}."
            )
        return cls(**data)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-safe dict form of the request."""
        return {
            "url": self.url,
            "content_type": self.content_type.value,
            "output_format": self.output_format.value,
            "selection": self.selection.value,
            "packaging": self.packaging.value,
            "start_index": self.start_index,
            "end_index": self.end_index,
            "chapter_url": self.chapter_url,
            "output_dir": self.output_dir,
            "fetch_mode": self.fetch_mode,
            "headless": self.headless,
            "sleep_ms": self.sleep_ms,
            "max_retries": self.max_retries,
            "max_workers": self.max_workers,
            "keep_logged_in": self.keep_logged_in,
        }


# ---------------------------------------------------------------------------
# Value types produced/consumed by the flow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metadata:
    """Extracted work-level metadata (title, author, cover, description...).

    Missing optional fields default to empty values; only the title is
    considered required for a successful ``get_metadata`` step.
    """

    title: str = ""
    author: str = ""
    description: str = ""
    genres: tuple[str, ...] = ()
    other_info: dict[str, str] = field(default_factory=dict)
    cover_url: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "genres": list(self.genres),
            "other_info": dict(self.other_info),
            "cover_url": self.cover_url,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class Chapter:
    """One normalized chapter descriptor.

    ``identifier`` is the display/sort label and is kept as a string so decimal
    chapter names such as ``"12.5"`` never lose precision.  ``ordinal`` is the
    1-based global ordering position used for selection and sorting.
    """

    ordinal: int
    volume_index: int
    position_in_volume: int
    identifier: str
    title: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "volume_index": self.volume_index,
            "position_in_volume": self.position_in_volume,
            "identifier": self.identifier,
            "title": self.title,
            "url": self.url,
        }


@dataclass(frozen=True)
class Volume:
    """A real or synthetic volume containing ordered chapters."""

    index: int
    title: str = ""
    cover_url: str = ""
    synthetic: bool = False
    chapters: tuple[Chapter, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "title": self.title,
            "cover_url": self.cover_url,
            "synthetic": self.synthetic,
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }


@dataclass(frozen=True)
class ImageManifestEntry:
    """One row of ``img/img_info.json``.

    ``raw_tag`` is the exact canonical remote tag as it appeared in the
    sanitized chapter HTML; exporters replace tags by exact raw-tag plus
    occurrence matching, never by substring URL replacement.
    """

    chapter_ordinal: int = 0
    occurrence: int = 0
    source_url: str = ""
    raw_tag: str = ""
    filename: str = ""
    relative_path: str = ""
    replacement_tag: str = ""
    status: ImageStatus = ImageStatus.DOWNLOADED
    schema_version: int = IMAGE_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "chapter_ordinal": self.chapter_ordinal,
            "occurrence": self.occurrence,
            "source_url": self.source_url,
            "raw_tag": self.raw_tag,
            "filename": self.filename,
            "relative_path": self.relative_path,
            "replacement_tag": self.replacement_tag,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class IntermediatePaths:
    """Paths to intermediate crawl data kept for troubleshooting."""

    output_dir: str = ""
    metadata_path: str | None = None
    img_info_path: str | None = None
    log_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "output_dir": self.output_dir,
            "metadata_path": self.metadata_path,
            "img_info_path": self.img_info_path,
            "log_path": self.log_path,
        }


@dataclass(frozen=True)
class ArtifactResult:
    """One produced artifact (or a failed attempt at one)."""

    output_format: OutputFormat
    packaging: PackagingMode
    path: str = ""
    volume_index: int | None = None
    status: str = "created"

    def to_dict(self) -> dict[str, object]:
        return {
            "output_format": self.output_format.value,
            "packaging": self.packaging.value,
            "path": self.path,
            "volume_index": self.volume_index,
            "status": self.status,
        }


@dataclass(frozen=True)
class FailureRecord:
    """A structured, secret-safe failure for result reporting."""

    stage: str
    code: str
    message: str
    chapter_ordinal: int | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "chapter_ordinal": self.chapter_ordinal,
            "url": self.url,
        }


@dataclass(frozen=True)
class CrawlResult:
    """The outcome of running one :class:`CrawlRequest`.

    ``success`` is False when the crawl is incomplete, an exporter failed, or
    a required chapter/image permanently failed.  Intermediate paths and
    structured failures are always preserved for troubleshooting.
    """

    request: CrawlRequest
    success: bool
    artifacts: tuple[ArtifactResult, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    metadata: Metadata | None = None
    volumes: tuple[Volume, ...] = ()
    chapter_count: int = 0
    image_count: int = 0
    intermediate: IntermediatePaths | None = None

    @classmethod
    def from_request(cls, request: CrawlRequest) -> "CrawlResult":
        """Start an empty result for a request (failure until proven success)."""
        return cls(request=request, success=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "request": self.request.to_dict(),
            "success": self.success,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "failures": [failure.to_dict() for failure in self.failures],
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "volumes": [volume.to_dict() for volume in self.volumes],
            "chapter_count": self.chapter_count,
            "image_count": self.image_count,
            "intermediate": self.intermediate.to_dict() if self.intermediate else None,
        }

    @classmethod
    def from_dict(cls, data: object) -> "CrawlResult":
        """Rebuild a result from its canonical JSON dict form."""
        if not isinstance(data, dict):
            raise InvalidRequestError("CrawlResult payload must be a JSON object.")
        request = CrawlRequest.from_dict(data.get("request"))
        artifacts = tuple(
            ArtifactResult(
                output_format=_coerce_enum(entry.get("output_format"), OutputFormat, "output_format"),  # type: ignore[arg-type]
                packaging=_coerce_enum(entry.get("packaging"), PackagingMode, "packaging"),  # type: ignore[arg-type]
                path=entry.get("path", ""),
                volume_index=entry.get("volume_index"),
                status=entry.get("status", "created"),
            )
            for entry in data.get("artifacts", ())
        )
        failures = tuple(
            FailureRecord(
                stage=entry.get("stage", ""),
                code=entry.get("code", ""),
                message=entry.get("message", ""),
                chapter_ordinal=entry.get("chapter_ordinal"),
                url=entry.get("url"),
            )
            for entry in data.get("failures", ())
        )
        metadata = data.get("metadata")
        metadata_obj = None
        if isinstance(metadata, dict):
            metadata_obj = Metadata(
                title=metadata.get("title", ""),
                author=metadata.get("author", ""),
                description=metadata.get("description", ""),
                genres=tuple(metadata.get("genres", ())),
                other_info=dict(metadata.get("other_info", {})),
                cover_url=metadata.get("cover_url", ""),
                source_url=metadata.get("source_url", ""),
            )
        volumes = tuple(_volume_from_dict(entry) for entry in data.get("volumes", ()))
        intermediate = data.get("intermediate")
        intermediate_obj = (
            IntermediatePaths(
                output_dir=intermediate.get("output_dir", ""),
                metadata_path=intermediate.get("metadata_path"),
                img_info_path=intermediate.get("img_info_path"),
                log_path=intermediate.get("log_path"),
            )
            if isinstance(intermediate, dict)
            else None
        )
        return cls(
            request=request,
            success=bool(data.get("success", False)),
            artifacts=artifacts,
            failures=failures,
            metadata=metadata_obj,
            volumes=volumes,
            chapter_count=int(data.get("chapter_count", 0)),
            image_count=int(data.get("image_count", 0)),
            intermediate=intermediate_obj,
        )


def _volume_from_dict(data: object) -> Volume:
    if not isinstance(data, dict):
        raise InvalidRequestError("Volume payload must be a JSON object.")
    chapters = tuple(
        Chapter(
            ordinal=chapter.get("ordinal", 0),
            volume_index=chapter.get("volume_index", 0),
            position_in_volume=chapter.get("position_in_volume", 0),
            identifier=chapter.get("identifier", ""),
            title=chapter.get("title", ""),
            url=chapter.get("url", ""),
        )
        for chapter in data.get("chapters", ())
    )
    return Volume(
        index=data.get("index", 0),
        title=data.get("title", ""),
        cover_url=data.get("cover_url", ""),
        synthetic=bool(data.get("synthetic", False)),
        chapters=chapters,
    )


# ---------------------------------------------------------------------------
# Batch v2
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchRequest:
    """Versioned batch payload: ``{"version": 2, "jobs": [CrawlRequest, ...]}``."""

    version: int
    jobs: tuple[CrawlRequest, ...]

    @classmethod
    def from_dict(cls, data: object) -> "BatchRequest":
        if not isinstance(data, dict):
            raise InvalidRequestError("Batch payload must be a JSON object.")
        version = data.get("version")
        if version != BATCH_VERSION:
            raise InvalidRequestError(
                f"Unsupported batch version {version!r}. Expected {BATCH_VERSION}. "
                "Legacy single-job JSON is not supported; migrate to a "
                "{\"version\": 2, \"jobs\": [...]} payload. See the ai-integration "
                "migration notes."
            )
        jobs = data.get("jobs")
        if jobs is None:
            raise InvalidRequestError("Batch payload is missing 'jobs'.")
        if not isinstance(jobs, list):
            raise InvalidRequestError("Batch 'jobs' must be a list of request objects.")
        return cls(version=version, jobs=tuple(CrawlRequest.from_dict(job) for job in jobs))

    def to_dict(self) -> dict[str, object]:
        return {"version": self.version, "jobs": [job.to_dict() for job in self.jobs]}


# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------


@dataclass
class CrawlContext:
    """Mutable runtime state shared by every flow step during one crawl.

    Built by the flow engine (later tasks) for each :class:`CrawlRequest`;
    not meant to be constructed by ordinary callers.

    ``raw_page`` is the long-lived :class:`api.raw_page.RawPageService` owned
    by the engine for the duration of a run (session/browser reuse).  The flow
    engine provisions it before the first step and closes it in a ``finally``
    after success, failure, or interruption.  It is ``None`` before a run and
    after a run ends.

    ``main_page_html`` caches the main page HTML fetched by ``get_metadata``
    so ``volumes_prepare`` can reuse it without a second network call.
    Both fields are ``None`` before a run.
    """

    request: CrawlRequest
    format_definition: dict[str, object] = field(default_factory=dict)
    metadata: Metadata | None = None
    volumes: list[Volume] = field(default_factory=list)
    output_dir: str | None = None
    image_entries: list[ImageManifestEntry] = field(default_factory=list)
    artifacts: list["ArtifactResult"] = field(default_factory=list)
    """Final artifacts produced by the ``create_ebook`` export step."""
    progress: Callable[[str, int, int], None] | None = None
    log: Callable[[str], None] | None = None
    current_stage: str | None = None
    """Flow stage currently executing, retained on failure for result reporting."""
    raw_page: "RawPageService | None" = None
    pacer: Callable[[], None] | None = None
    main_page_html: str | None = None
    """Fetched main-page HTML, cached by ``get_metadata`` for reuse in
    ``volumes_prepare`` so the same page is not fetched twice."""
    chapter_contents: list[dict[str, object]] = field(default_factory=list)
    """Sanitized chapter HTML produced by ``crawl_chapter``, one entry per
    chapter in ordinal order, with keys ``ordinal``, ``title``, ``html``,
    ``volume_index``, ``position_in_volume``, and ``identifier``.
    Consumed by ``create_ebook`` for replacement and export."""
