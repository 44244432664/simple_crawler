"""AI-assisted site analysis, validation, drafts, and registration.

Task 8 of ``TASK/ai-integration_TASK.md``.  The provider and page-fetching
layers remain deliberately separate from this module: ``generate_json`` is the
only AI entry point and ``RawPageService`` is the only production page service.
Generated definitions are treated as untrusted until the local validator has
checked selectors, URLs, and the executable flow.
"""

from __future__ import annotations

import csv
import copy
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

from api.ai_config import load_system_prompt
from api.ai_provider import generate_json
from api.contracts import (
    AIValidationError,
    ContentType,
    CrawlRequest,
    InvalidFlowError,
    OutputFormat,
    _normalize_url,
)
from api.flow import build_flow
from api.preparation import canonical_page_url, is_empty_selector, selector_args
from api.raw_page import RawPageService


MAX_COMPACT_HTML = 60_000
MAX_DRAFT_TEXT = 2_000
REPAIR_LIMIT = 2
_CONTROL_FIELDS = frozenset(
    {
        "first_chapter_url",
        "chapter_url",
        "format",
        "partial_format",
        "format_definition",
        "definition",
        "errors",
        "report",
    }
)
_SELECTOR_KEYS = frozenset(
    {
        "name",
        "id",
        "class_",
        "attrs",
        "other_attr",
        "container",
        "link",
        "pagination",
        "max_pages",
        "allowed_hosts",
        "delete",
        "remove",
        "text",
        "vol_name",
        "image",
        "selector",
        "url_attr",
    }
)
_SENSITIVE_URL_QUERY = re.compile(
    r"(?i)^(?:key|api[_-]?key|token|access[_-]?token|password|passwd|secret|auth)$"
)
_DECIMAL_RE = re.compile(r"\b\d+\.\d+\b")
_DEFINITION_KEYS = frozenset(
    {
        "content_type",
        "cover",
        "title",
        "author",
        "genre",
        "description",
        "other_info",
        "vol_group",
        "chapter_list",
        "chapter_list_order",
        "chapter_naming",
        "vol_page",
        "chapter",
        "fetch",
        "img_referrer",
        "login",
        "gallery_links",
        "picture",
        "image_delivery",
        "flow",
        *_CONTROL_FIELDS,
    }
)
_SENSITIVE_FIELD_NAMES = frozenset(
    {"api_key", "authorization", "cookie", "password", "passwd", "secret", "token", "access_token"}
)
_RAW_HTML_FIELD_NAMES = frozenset(
    {"html", "raw_html", "page_html", "main_html", "chapter_html", "source_html", "content_html"}
)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")

_RESPONSE_SELECTOR_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "id": {"type": "string"},
        "class_": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}, "minItems": 1},
            ]
        },
        "attrs": {"type": "object"},
        "other_attr": {"type": "string"},
    },
    "anyOf": [
        {"required": ["name"]},
        {"required": ["id"]},
        {"required": ["class_"]},
    ],
    "additionalProperties": True,
}


FIRST_CALL_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [
        "content_type",
        "first_chapter_url",
        "title",
        "chapter_list_order",
        "chapter_naming",
    ],
    "properties": {
        "content_type": {"type": "string", "enum": ["novel", "comic", "gallery"]},
        "first_chapter_url": {"type": "string"},
        "title": _RESPONSE_SELECTOR_SCHEMA,
        "chapter_list_order": {
            "type": "string",
            "enum": ["oldest_first", "newest_first"],
        },
        "chapter_naming": {
            "type": "object",
            "required": ["source"],
            "properties": {
                "source": {"type": "string", "enum": ["text", "url"]},
            },
            "additionalProperties": True,
        },
        "chapter_list": {
            "type": "object",
            "required": ["container", "link"],
            "properties": {
                "container": _RESPONSE_SELECTOR_SCHEMA,
                "link": _RESPONSE_SELECTOR_SCHEMA,
            },
            "additionalProperties": True,
        },
        "vol_group": {
            "type": "object",
            "required": ["vol_section", "chapter_list"],
            "properties": {
                "vol_section": _RESPONSE_SELECTOR_SCHEMA,
                "chapter_list": _RESPONSE_SELECTOR_SCHEMA,
            },
            "additionalProperties": True,
        },
        "gallery_links": {
            "type": "object",
            "required": ["container", "link"],
            "properties": {
                "container": _RESPONSE_SELECTOR_SCHEMA,
                "link": _RESPONSE_SELECTOR_SCHEMA,
            },
            "additionalProperties": True,
        },
    },
    "anyOf": [
        {"required": ["chapter_list"]},
        {"required": ["vol_group"]},
        {"required": ["gallery_links"]},
    ],
    "additionalProperties": True,
}

_FLOW_STEP_SCHEMA: dict[str, object] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["module"],
            "properties": {"module": {"const": "get_metadata"}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["module"],
            "properties": {"module": {"const": "volumes_prepare"}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["module", "params"],
            "properties": {
                "module": {"const": "crawl_chapter"},
                "params": {
                    "type": "object",
                    "required": ["actions"],
                    "properties": {
                        "actions": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "string",
                                "enum": ["crawl_text_content", "download_image"],
                            },
                        },
                        "max_retries": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["module"],
            "properties": {"module": {"const": "create_ebook"}},
            "additionalProperties": False,
        },
    ]
}

SECOND_CALL_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["content_type", "chapter", "flow"],
    "properties": {
        "content_type": {"type": "string", "enum": ["novel", "comic", "gallery"]},
        "chapter": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": _RESPONSE_SELECTOR_SCHEMA,
                "content": _RESPONSE_SELECTOR_SCHEMA,
                "image": _RESPONSE_SELECTOR_SCHEMA,
                "remove": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "picture": {
            "type": "object",
            "required": ["image"],
            "properties": {"image": _RESPONSE_SELECTOR_SCHEMA},
            "additionalProperties": False,
        },
        "flow": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": _FLOW_STEP_SCHEMA,
        },
    },
    "anyOf": [
        {
            "properties": {
                "chapter": {"type": "object", "required": ["content"]},
            }
        },
        {
            "properties": {
                "chapter": {"type": "object", "required": ["image"]},
            }
        },
        {"required": ["picture"]},
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RegistrationResult:
    """Paths published by one successful format/alias registration."""

    format_path: str
    aliases_path: str
    host: str
    format_name: str


@dataclass(frozen=True)
class AIUpdateResult:
    """Secret-safe outcome of :func:`AI_update`."""

    success: bool
    validated: bool = False
    registered: bool = False
    definition: dict[str, object] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    draft_path: str | None = None
    report_path: str | None = None
    format_path: str | None = None
    aliases_path: str | None = None
    summary: dict[str, object] = field(default_factory=dict)


_VALIDATED_DEFINITION_TOKEN = object()


class _ValidatedSiteDefinition(dict[str, object]):
    """A definition produced by :func:`validate_site_definition`.

    This private capability type keeps the publication helper from accepting
    arbitrary AI/provider data.  It remains a normal ``dict`` to preserve the
    public return contract of ``validate_site_definition``.
    """

    def __init__(self, payload: Mapping[str, object], *, token: object) -> None:
        if token is not _VALIDATED_DEFINITION_TOKEN:
            raise TypeError("site definitions must be created by validate_site_definition")
        super().__init__(payload)


def _safe_host(url: str) -> str:
    """Return a traversal-safe normalized host for drafts and aliases."""
    host = (urlsplit(url).hostname or "unknown-host").lower().rstrip(".")
    host = host.removeprefix("www.")
    safe = re.sub(r"[^a-z0-9._-]+", "_", host)
    return safe.strip("._") or "unknown-host"


def _redact_url(url: str) -> str:
    """Remove credentials and sensitive query values before persistence."""
    try:
        parsed = urlsplit(url)
        pairs = []
        for item in parsed.query.split("&") if parsed.query else ():
            if "=" in item:
                key, value = item.split("=", 1)
                pairs.append(f"{key}=[REDACTED]" if _SENSITIVE_URL_QUERY.match(key) else f"{key}={value}")
            elif item:
                pairs.append("[REDACTED]" if _SENSITIVE_URL_QUERY.match(item) else item)
        host = parsed.hostname or ""
        netloc = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "&".join(pairs), ""))
    except (TypeError, ValueError):
        return "[REDACTED_URL]"


def _safe_value(value: object, *, key: str = "") -> object:
    """Recursively make candidate data safe for drafts and reports."""
    normalized_key = key.lower().replace("-", "_")
    if _SENSITIVE_URL_QUERY.match(key) or normalized_key in _SENSITIVE_FIELD_NAMES:
        return "[REDACTED]"
    if normalized_key in _RAW_HTML_FIELD_NAMES:
        return "[HTML_REMOVED]"
    if isinstance(value, Mapping):
        return {str(k): _safe_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://")):
        return _redact_url(value)
    # A failed provider response can include the fetched document under an
    # invented key.  Drafts are definitions/reports, never source archives.
    if isinstance(value, str) and _HTML_TAG_RE.search(value):
        return "[HTML_REMOVED]"
    if isinstance(value, str) and len(value) > MAX_DRAFT_TEXT:
        return value[:MAX_DRAFT_TEXT] + "...[truncated]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)[:MAX_DRAFT_TEXT]


def _safe_error(value: object) -> str:
    """Bound error text and remove HTML/credential-shaped content."""
    text = str(value or "")
    text = re.sub(r"(?is)<(script|style).*?</\1>", "[HTML_REMOVED]", text)
    text = re.sub(r"(?s)<[^>]{1,500}>", "[HTML_REMOVED]", text)
    text = re.sub(
        r"(?i)(authorization|api[_-]?key|token|password)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = " ".join(text.split())
    return text[:MAX_DRAFT_TEXT] + ("...[truncated]" if len(text) > MAX_DRAFT_TEXT else "")


def _definition_safety_errors(value: object, path: str = "definition") -> list[str]:
    """Reject fields/URLs that could persist credentials or arbitrary data."""
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if path == "definition" and key_text not in _DEFINITION_KEYS:
                errors.append(f"definition contains unsupported field {key_text!r}.")
            if key_text.lower() in _SENSITIVE_FIELD_NAMES:
                errors.append(f"{path}.{key_text} is not allowed in a site definition.")
            errors.extend(_definition_safety_errors(item, f"{path}.{key_text}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_definition_safety_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str) and "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None:
                errors.append(f"{path} must not contain URL credentials.")
            if any(_SENSITIVE_URL_QUERY.match(pair.split("=", 1)[0]) for pair in parsed.query.split("&") if pair):
                errors.append(f"{path} must not contain sensitive URL query values.")
        except ValueError:
            errors.append(f"{path} contains a malformed URL.")
    return errors


def compact_html(raw_html: str, *, max_chars: int = MAX_COMPACT_HTML) -> str:
    """Compact untrusted HTML without retaining executable or comment content."""
    if not isinstance(raw_html, str):
        raise AIValidationError("main page HTML must be a string.")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise AIValidationError("max_chars must be a positive integer.")
    soup = BeautifulSoup(raw_html, "html.parser")
    for node in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        node.decompose()
    for node in soup.find_all(string=lambda value: isinstance(value, Comment)):
        node.extract()
    compact = re.sub(r"\s+", " ", str(soup)).strip()
    if len(compact) <= max_chars:
        return compact
    return _compact_html_evidence(soup, max_chars)


def _sample_evenly(items: list, maximum: int) -> list:
    """Keep evidence from the whole document instead of only its prefix."""
    if len(items) <= maximum:
        return items
    if maximum == 1:
        return [items[0]]
    return [items[round(index * (len(items) - 1) / (maximum - 1))] for index in range(maximum)]


def _evidence_fragment(element: object) -> str:
    """Return one small, selector-preserving fragment for a useful element."""
    if not hasattr(element, "name"):
        return ""
    fragment = str(element)
    parent = getattr(element, "parent", None)
    # Container class/id selectors are vital for chapter lists.  Preserve the
    # nearest meaningful parent without carrying its potentially huge subtree.
    while parent is not None and getattr(parent, "name", None) not in {None, "body", "html", "[document]"}:
        attrs = getattr(parent, "attrs", {}) or {}
        if attrs:
            attr_text = " ".join(
                f'{name}="{html.escape(" ".join(value) if isinstance(value, list) else str(value), quote=True)}"'
                for name, value in attrs.items()
                if str(value).strip()
            )
            opening = f"<{parent.name}{(' ' + attr_text) if attr_text else ''}>"
            return f"{opening}{fragment}</{parent.name}>"
        parent = getattr(parent, "parent", None)
    return fragment


def _compact_html_evidence(soup: BeautifulSoup, max_chars: int) -> str:
    """Build a bounded selector-evidence view from the entire page.

    Long pages often put chapter links after large descriptions or comments.
    Sampling headings, links, and images across the document is much more
    useful for site-definition generation than truncating at an arbitrary
    prefix while still enforcing the prompt-size limit.
    """
    headings = list(soup.find_all(["title", "h1", "h2", "h3", "meta"]))
    anchors = list(soup.find_all("a", href=True))
    images = list(soup.find_all("img"))
    elements = headings + _sample_evenly(anchors, 240) + _sample_evenly(images, 80)
    fragments: list[str] = []
    seen: set[str] = set()
    used = len("<html><body></body></html>")
    for element in elements:
        fragment = _evidence_fragment(element)
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        if used + len(fragment) > max_chars:
            continue
        fragments.append(fragment)
        used += len(fragment)
    if not fragments:
        # This is only reachable for pathological individual tags larger than
        # the limit.  Do not persist or send their full source.
        return "<html><body>[truncated page with no compactable evidence]</body></html>"
    return "<html><body>" + "".join(fragments) + "</body></html>"


def _canonical_http_url(base_url: str, value: str) -> str:
    resolved = urljoin(base_url, value or "")
    try:
        parsed = urlsplit(resolved)
        parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return canonical_page_url(resolved)


def extracted_http_anchors(html: str, page_url: str) -> tuple[str, ...]:
    """Return unique canonical HTTP(S) anchor targets in source order."""
    soup = BeautifulSoup(html, "html.parser")
    result: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        target = _canonical_http_url(page_url, str(anchor.get("href") or ""))
        if target and target not in seen:
            seen.add(target)
            result.append(target)
    return tuple(result)


def _merge_dicts(left: Mapping[str, object], right: Mapping[str, object]) -> dict[str, object]:
    merged = copy.deepcopy(dict(left))
    for key, value in right.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_dicts(merged[key], value)  # type: ignore[arg-type]
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _candidate_format(candidate: object) -> dict[str, object]:
    if not isinstance(candidate, Mapping):
        return {}
    for key in ("format", "partial_format", "format_definition", "definition"):
        value = candidate.get(key)
        if isinstance(value, Mapping):
            base = dict(value)
            # A second-call response often puts ``flow`` beside ``format``.
            for field_name, field_value in candidate.items():
                if field_name not in _CONTROL_FIELDS and field_name not in base:
                    base[field_name] = field_value
            return base
    return {key: value for key, value in candidate.items() if key not in _CONTROL_FIELDS}


def _first_chapter_candidate(candidate: object) -> str:
    if not isinstance(candidate, Mapping):
        return ""
    for key in ("first_chapter_url", "chapter_url"):
        if isinstance(candidate.get(key), str):
            return candidate[key]
    nested = candidate.get("first_chapter")
    if isinstance(nested, Mapping) and isinstance(nested.get("url"), str):
        return nested["url"]
    return ""


def _find(selector: object, soup: BeautifulSoup) -> list:
    if not isinstance(selector, Mapping):
        return []
    try:
        return soup.find_all(**selector_args(dict(selector)))
    except (KeyError, TypeError, ValueError):
        return []


def _selector_errors(value: object, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return [f"{path} must be an object."]
    for key, item in value.items():
        if key not in _SELECTOR_KEYS:
            errors.append(f"{path} contains unsupported key {key!r}.")
        if key == "attrs" and item is not None and not isinstance(item, Mapping):
            errors.append(f"{path}.attrs must be an object, not a string.")
        if key in {"container", "link", "pagination", "image", "selector"} and isinstance(item, Mapping):
            errors.extend(_selector_errors(item, f"{path}.{key}"))
    return errors


def _selector_match_errors(
    selector: object,
    soup: BeautifulSoup,
    path: str,
    *,
    require_text: bool = False,
    require_attribute: bool = False,
) -> list[str]:
    errors = _selector_errors(selector, path)
    if errors or is_empty_selector(dict(selector) if isinstance(selector, Mapping) else None):
        return errors + ([f"{path} is empty."] if not errors else [])
    matches = _find(selector, soup)
    if not matches:
        return [f"{path} did not match the fetched page."]
    if require_text and not any(element.get_text(" ", strip=True) for element in matches):
        errors.append(f"{path} matched but produced no text.")
    if require_attribute:
        attr = str(selector.get("other_attr") or "src") if isinstance(selector, Mapping) else "src"
        if not any(str(element.get(attr) or "").strip() for element in matches):
            errors.append(f"{path} matched but has no usable {attr!r} value.")
    return errors


def _nested_selector_match_errors(
    parent_matches: list, selector: object, path: str, *, require_attribute: bool = False
) -> list[str]:
    if not isinstance(selector, Mapping) or is_empty_selector(dict(selector)):
        return [f"{path} is empty."]
    if not parent_matches:
        return [f"{path} has no parent matches."]
    if not any(_find(selector, parent) for parent in parent_matches):
        return [f"{path} did not match inside its configured container."]
    if require_attribute:
        attr = str(selector.get("other_attr") or "href")
        if not any(
            str(element.get(attr) or "").strip()
            for parent in parent_matches
            for element in _find(selector, parent)
        ):
            return [f"{path} matched but has no usable {attr!r} value."]
    return []


def _volume_chapter_link_errors(parent_matches: list, selector: object, path: str) -> list[str]:
    """Verify a volume chapter-list container yields at least one real link."""
    errors = _nested_selector_match_errors(parent_matches, selector, path)
    if errors:
        return errors
    containers = [
        container
        for parent in parent_matches
        for container in _find(selector, parent)
    ]
    if not any(str(anchor.get("href") or "").strip() for container in containers for anchor in container.find_all("a")):
        return [f"{path} matched but contains no usable chapter anchor href."]
    return []


def _validate_naming(definition: Mapping[str, object], soup: BeautifulSoup) -> list[str]:
    naming = definition.get("chapter_naming")
    if not isinstance(naming, Mapping):
        return ["chapter_naming is required."]
    source = str(naming.get("source", "text")).lower()
    if source not in {"text", "label", "anchor_text", "url", "href"}:
        return ["chapter_naming.source must be text or url."]
    pattern = naming.get("regex", naming.get("pattern"))
    if pattern is not None:
        if not isinstance(pattern, str):
            return ["chapter_naming.regex must be a string."]
        try:
            compiled = re.compile(pattern)
        except re.error:
            return ["chapter_naming.regex is invalid."]
        for anchor in soup.find_all("a"):
            text = anchor.get_text(" ", strip=True)
            value = str(anchor.get("href") or "") if source in {"url", "href"} else text
            decimal = _DECIMAL_RE.search(value)
            if not decimal:
                continue
            match = compiled.search(value)
            if not match:
                return ["chapter_naming.regex does not match a decimal chapter label."]
            capture = naming.get("capture")
            try:
                captured = match.group(0) if capture is None else match.group(capture)
            except (IndexError, KeyError, TypeError):
                return ["chapter_naming.capture does not match its regex."]
            if not _DECIMAL_RE.search(str(captured)):
                return ["chapter_naming must preserve decimal identifiers as strings."]
            break
    return []


def _validate_first_stage(
    partial: Mapping[str, object], content_type: ContentType, main_soup: BeautifulSoup
) -> list[str]:
    """Check the first call delivered the selectors needed for chapter fetch."""
    errors = _definition_safety_errors(partial)
    if partial.get("content_type") != content_type.value:
        errors.append("first-stage content_type does not match the selected content type.")
    errors.extend(_selector_match_errors(partial.get("title"), main_soup, "title", require_text=True))
    errors.extend(_validate_naming(partial, main_soup))
    order = partial.get("chapter_list_order")
    if order not in {"oldest_first", "newest_first"}:
        errors.append("chapter_list_order must be oldest_first or newest_first.")

    if content_type is ContentType.GALLERY:
        gallery_links = partial.get("gallery_links")
        if not isinstance(gallery_links, Mapping):
            errors.append("gallery_links is required in the first-stage definition.")
        else:
            errors.extend(_selector_match_errors(gallery_links.get("container"), main_soup, "gallery_links.container"))
            parents = _find(gallery_links.get("container"), main_soup)
            errors.extend(_nested_selector_match_errors(parents, gallery_links.get("link"), "gallery_links.link", require_attribute=True))
    else:
        vol_group = partial.get("vol_group")
        root_list = partial.get("chapter_list")
        if isinstance(vol_group, Mapping) and isinstance(vol_group.get("vol_section"), Mapping):
            errors.extend(_selector_match_errors(vol_group.get("vol_section"), main_soup, "vol_group.vol_section"))
            parents = _find(vol_group.get("vol_section"), main_soup)
            errors.extend(_volume_chapter_link_errors(parents, vol_group.get("chapter_list"), "vol_group.chapter_list"))
        elif isinstance(root_list, Mapping):
            container = root_list.get("container", root_list)
            errors.extend(_selector_match_errors(container, main_soup, "chapter_list.container"))
            parents = _find(container, main_soup)
            errors.extend(_nested_selector_match_errors(parents, root_list.get("link"), "chapter_list.link", require_attribute=True))
        else:
            errors.append("a usable vol_group or chapter_list is required in the first-stage definition.")
    return list(dict.fromkeys(errors))


def _validate_content_selectors(
    definition: Mapping[str, object],
    content_type: ContentType,
    main_soup: BeautifulSoup,
    chapter_soup: BeautifulSoup,
) -> list[str]:
    errors: list[str] = []
    order = definition.get("chapter_list_order")
    if order not in {"oldest_first", "newest_first"}:
        errors.append("chapter_list_order must be oldest_first or newest_first.")
    errors.extend(_selector_match_errors(definition.get("title"), main_soup, "title", require_text=True))
    for field_name, require_text, require_attribute in (
        ("author", True, False),
        ("cover", False, True),
        ("genre", True, False),
        ("description", True, False),
    ):
        if definition.get(field_name) not in (None, {}):
            errors.extend(
                _selector_match_errors(
                    definition.get(field_name),
                    main_soup,
                    field_name,
                    require_text=require_text,
                    require_attribute=require_attribute,
                )
            )
    other_info = definition.get("other_info")
    if isinstance(other_info, Mapping):
        errors.extend(_selector_match_errors(other_info.get("holder"), main_soup, "other_info.holder"))
        errors.extend(_selector_match_errors(other_info.get("value"), main_soup, "other_info.value"))

    vol_group = definition.get("vol_group")
    root_list = definition.get("chapter_list")
    chapter_parents: list = []
    if isinstance(vol_group, Mapping) and not is_empty_selector(
        dict(vol_group.get("vol_section")) if isinstance(vol_group.get("vol_section"), Mapping) else None
    ):
        section = vol_group.get("vol_section")
        errors.extend(_selector_match_errors(section, main_soup, "vol_group.vol_section"))
        chapter_parents = _find(section, main_soup)
        errors.extend(_volume_chapter_link_errors(chapter_parents, vol_group.get("chapter_list"), "vol_group.chapter_list"))
    elif isinstance(root_list, Mapping):
        container = root_list.get("container", root_list)
        errors.extend(_selector_match_errors(container, main_soup, "chapter_list.container"))
        chapter_parents = _find(container, main_soup)
        errors.extend(_nested_selector_match_errors(chapter_parents, root_list.get("link"), "chapter_list.link", require_attribute=True))
    else:
        errors.append("a usable vol_group or chapter_list is required.")
    errors.extend(_validate_naming(definition, main_soup))

    chapter = definition.get("chapter")
    if not isinstance(chapter, Mapping):
        chapter = {}
        errors.append("chapter is required.")
    errors.extend(_selector_match_errors(chapter.get("title"), chapter_soup, "chapter.title", require_text=True))
    if content_type is ContentType.NOVEL:
        errors.extend(_selector_match_errors(chapter.get("content"), chapter_soup, "chapter.content", require_text=True))
        actions_required = "crawl_text_content"
    else:
        image_selector = chapter.get("image")
        if content_type is ContentType.GALLERY:
            gallery_links = definition.get("gallery_links")
            if not isinstance(gallery_links, Mapping):
                errors.append("gallery_links is required for gallery definitions.")
            else:
                errors.extend(_selector_match_errors(gallery_links.get("container"), main_soup, "gallery_links.container"))
                gallery_parents = _find(gallery_links.get("container"), main_soup)
                errors.extend(_nested_selector_match_errors(gallery_parents, gallery_links.get("link"), "gallery_links.link", require_attribute=True))
            picture = definition.get("picture")
            if isinstance(picture, Mapping) and isinstance(picture.get("image"), Mapping):
                image_selector = picture.get("image")
                errors.extend(
                    _selector_match_errors(
                        image_selector,
                        chapter_soup,
                        "picture.image",
                        require_attribute=True,
                    )
                )
            elif not image_selector:
                errors.append("picture.image is required for gallery definitions.")
        errors.extend(_selector_match_errors(image_selector, chapter_soup, "chapter.image", require_attribute=True))
        actions_required = "download_image"
    flow = definition.get("flow")
    if not isinstance(flow, list):
        errors.append("flow must be an ordered list.")
    else:
        crawl_steps = [step for step in flow if isinstance(step, Mapping) and step.get("module") == "crawl_chapter"]
        if not crawl_steps:
            errors.append("flow must contain crawl_chapter.")
        else:
            actions = crawl_steps[0].get("params", {}).get("actions", [])
            if actions_required not in actions:
                errors.append(f"flow crawl_chapter must include {actions_required}.")
    return errors


def _normalize_gallery_definition(definition: dict[str, object]) -> dict[str, object]:
    if definition.get("content_type") == ContentType.GALLERY.value:
        picture = definition.get("picture")
        chapter = definition.get("chapter")
        if isinstance(picture, Mapping) and isinstance(picture.get("image"), Mapping):
            chapter = dict(chapter) if isinstance(chapter, Mapping) else {}
            chapter["image"] = copy.deepcopy(picture["image"])
            definition["chapter"] = chapter
    return definition


def validate_site_definition(
    definition: object,
    *,
    content_type: str | ContentType,
    main_html: str,
    chapter_html: str,
    main_url: str,
    chapter_url: str,
) -> dict[str, object]:
    """Validate and normalize one generated definition without network/I/O."""
    try:
        selected = ContentType(content_type.value if isinstance(content_type, ContentType) else str(content_type).strip().lower())
    except ValueError:
        raise AIValidationError("content_type must be novel, comic, or gallery.") from None
    if not isinstance(definition, Mapping):
        raise AIValidationError("generated site definition must be a JSON object.")
    normalized = _normalize_gallery_definition(copy.deepcopy(dict(definition)))
    errors: list[str] = _definition_safety_errors(normalized)
    if normalized.get("content_type") != selected.value:
        errors.append("generated content_type does not match the user-selected content type.")
    main_anchors = extracted_http_anchors(main_html, main_url)
    canonical_chapter = _canonical_http_url(main_url, chapter_url)
    if not canonical_chapter or canonical_chapter not in main_anchors:
        errors.append("first chapter URL is not an HTTP(S) anchor extracted from the main page.")
    if canonical_chapter and urlsplit(canonical_chapter).hostname != urlsplit(main_url).hostname:
        errors.append("first chapter URL must stay on the fetched site's host.")
    errors.extend(
        _validate_content_selectors(
            normalized,
            selected,
            BeautifulSoup(main_html, "html.parser"),
            BeautifulSoup(chapter_html, "html.parser"),
        )
    )
    if errors:
        unique = tuple(dict.fromkeys(errors))
        raise AIValidationError("AI site definition validation failed: " + " ".join(unique))
    normalized.pop("first_chapter_url", None)
    normalized["content_type"] = selected.value
    normalized["_first_chapter_url"] = canonical_chapter
    try:
        request = CrawlRequest(
            url=main_url,
            content_type=selected,
            output_format=OutputFormat.EPUB if selected is ContentType.NOVEL else OutputFormat.CBZ,
            fetch_mode="requests",
        )
        build_flow(request, normalized)
    except (InvalidFlowError, ValueError) as exc:
        raise AIValidationError(f"generated flow is not executable: {exc}") from None
    normalized.pop("_first_chapter_url", None)
    return _ValidatedSiteDefinition(normalized, token=_VALIDATED_DEFINITION_TOKEN)


def _atomic_json(path: Path, payload: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temporary, path)
            os.remove(temporary)
        else:
            os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def save_draft(
    host: str,
    candidate: object,
    errors: list[str] | tuple[str, ...],
    *,
    drafts_dir: str | os.PathLike | None = None,
    overwrite: bool | None = True,
    confirm_overwrite: Callable[..., bool] | None = None,
) -> tuple[str, str]:
    """Atomically save a candidate and report without HTML or credentials.

    ``overwrite=None`` preserves an existing host draft unless the supplied
    callback explicitly approves its replacement.  The default keeps this
    low-level helper backward-compatible for callers that deliberately manage
    draft versions themselves.
    """
    root = Path(drafts_dir) if drafts_dir is not None else Path(__file__).resolve().parents[1] / "data" / "drafts"
    safe_host = _safe_host(f"https://{host}")
    directory = root / safe_host
    safe_candidate = _safe_value(candidate)
    safe_errors = [_safe_error(error) for error in errors]
    candidate_path = directory / "candidate.json"
    report_path = directory / "report.json"
    if candidate_path.exists() and overwrite is None:
        overwrite = _approve_draft_overwrite(confirm_overwrite, safe_host)
    if candidate_path.exists() and not overwrite:
        revision = 1
        while (directory / f"candidate-{revision}.json").exists() or (directory / f"report-{revision}.json").exists():
            revision += 1
        candidate_path = directory / f"candidate-{revision}.json"
        report_path = directory / f"report-{revision}.json"
    _atomic_json(candidate_path, safe_candidate)
    _atomic_json(report_path, {"host": safe_host, "errors": safe_errors, "candidate": str(candidate_path.name)})
    return str(candidate_path), str(report_path)


def _normalize_alias_host(value: str) -> str:
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")


def register_site_definition(
    definition: Mapping[str, object],
    *,
    host: str,
    formats_dir: str | os.PathLike | None = None,
    aliases_path: str | os.PathLike | None = None,
    format_name: str | None = None,
    confirmed: bool = False,
) -> RegistrationResult:
    """Atomically publish a new format and ``FlowCrawler`` alias row."""
    if not isinstance(definition, _ValidatedSiteDefinition):
        raise AIValidationError(
            "registration requires a definition returned by validate_site_definition."
        )
    if confirmed is not True:
        raise AIValidationError("registration requires explicit confirmation.")
    root = Path(__file__).resolve().parents[1]
    formats = Path(formats_dir) if formats_dir is not None else root / "data" / "formats"
    aliases = Path(aliases_path) if aliases_path is not None else root / "data" / "aliases.csv"
    normalized_host = _normalize_alias_host(host)
    if not normalized_host:
        raise AIValidationError("cannot register a definition without a valid host.")
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", format_name or normalized_host).strip("._")
    if not safe_name:
        raise AIValidationError("format name is empty after safe normalization.")
    format_path = formats / f"{safe_name}.json"
    if format_path.exists():
        raise AIValidationError(f"format collision: {format_path.name} already exists.")

    rows: list[dict[str, str]] = []
    if aliases.exists():
        try:
            with aliases.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = [dict(row) for row in reader]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise AIValidationError(f"could not read aliases for registration: {exc}") from None
    if any(_normalize_alias_host(row.get("site", "")) == normalized_host for row in rows):
        raise AIValidationError(f"alias collision: {normalized_host} is already registered.")
    if any((row.get("name") or "").strip() == safe_name for row in rows):
        raise AIValidationError(f"alias format-name collision: {safe_name} is already registered.")

    new_row = {"site": normalized_host, "name": safe_name, "crawler_class": "FlowCrawler"}
    format_created = False
    try:
        _atomic_json(format_path, dict(definition), exclusive=True)
        format_created = True
        aliases.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{aliases.name}.", suffix=".tmp", dir=aliases.parent)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["site", "name", "crawler_class"])
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
                writer.writerow(new_row)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, aliases)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
    except Exception as exc:
        if format_created:
            try:
                format_path.unlink()
            except OSError:
                pass
        if isinstance(exc, AIValidationError):
            raise
        raise AIValidationError(f"atomic site registration failed: {exc}") from None
    return RegistrationResult(str(format_path), str(aliases), normalized_host, safe_name)


class FlowCrawler:
    """Small compatibility wrapper named by newly registered alias rows."""

    def __init__(self, request: object, **options: object) -> None:
        self.request = request
        self.options = options

    def run(self):
        from api.runner import run_crawl

        return run_crawl(self.request, **self.options)


def _approve(callback: Callable[..., bool] | None, errors: tuple[str, ...], attempt: int) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback(errors, attempt))
    except TypeError:
        try:
            return bool(callback(errors))
        except TypeError:
            return bool(callback())


def _approve_registration(
    callback: Callable[..., bool] | None, summary: dict[str, object]
) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback(summary))
    except TypeError:
        return bool(callback())


def _approve_draft_overwrite(callback: Callable[..., bool] | None, host: str) -> bool:
    """Ask only when a failed retry would replace an existing host draft."""
    if callback is None:
        return False
    try:
        return bool(callback(host))
    except TypeError:
        try:
            return bool(callback())
        except TypeError:
            return False


def _result_with_draft(
    *,
    host: str,
    candidate: object,
    errors: list[str],
    drafts_dir: str | os.PathLike | None,
    definition: dict[str, object] | None = None,
    summary: dict[str, object] | None = None,
    confirm_draft_overwrite: Callable[..., bool] | None = None,
) -> AIUpdateResult:
    draft_path, report_path = save_draft(
        host,
        candidate,
        errors,
        drafts_dir=drafts_dir,
        overwrite=None,
        confirm_overwrite=confirm_draft_overwrite,
    )
    return AIUpdateResult(
        success=False,
        validated=False,
        definition=definition or {},
        errors=tuple(errors),
        draft_path=draft_path,
        report_path=report_path,
        summary=summary or {},
    )


def AI_update(
    url: str,
    content_type: str | ContentType,
    *,
    confirm_repair: Callable[..., bool] | None = None,
    confirm_registration: Callable[..., bool] | None = None,
    confirm_draft_overwrite: Callable[..., bool] | None = None,
    drafts_dir: str | os.PathLike | None = None,
    formats_dir: str | os.PathLike | None = None,
    aliases_path: str | os.PathLike | None = None,
    format_name: str | None = None,
    generate: Callable[..., dict[str, Any]] | None = None,
    raw_page: Any = None,
    system_prompt: str | None = None,
) -> AIUpdateResult:
    """Analyze, validate, optionally repair, and optionally register a site.

    ``confirm_repair`` and ``confirm_registration`` are intentionally callbacks
    so the TUI can ask the user while API callers remain non-interactive.
    Returning a failed result with a draft is preferred to raising for expected
    AI/user decisions; configuration/programming errors still become a draft
    with an actionable report.
    """
    try:
        normalized_url = _normalize_url(url)
        selected = ContentType(content_type.value if isinstance(content_type, ContentType) else str(content_type).strip().lower())
    except (ValueError, TypeError) as exc:
        return _result_with_draft(
            host="unknown-host",
            candidate={},
            errors=[_safe_error(exc)],
            drafts_dir=drafts_dir,
            confirm_draft_overwrite=confirm_draft_overwrite,
        )
    host = _safe_host(normalized_url)
    generator = generate or generate_json
    prompt = system_prompt if system_prompt is not None else load_system_prompt()
    request = CrawlRequest(
        url=normalized_url,
        content_type=selected,
        output_format=OutputFormat.EPUB if selected is ContentType.NOVEL else OutputFormat.CBZ,
        fetch_mode="auto",
    )
    service = raw_page or RawPageService(request, {"fetch": {"mode": "auto"}})
    candidate: object = {}
    final_definition: dict[str, object] = {}
    try:
        main_html = service.get_raw_page(normalized_url)
        compact_main = compact_html(main_html)

        first_prompt = (
            f"content_type={selected.value}\nAnalyze this compacted main page and return the first-stage site definition.\n"
            f"{compact_main}"
        )
        first_errors: list[str] = []
        for repair_attempt in range(REPAIR_LIMIT + 1):
            try:
                candidate = generator(prompt, first_prompt, FIRST_CALL_SCHEMA)
                partial = _candidate_format(candidate)
                chapter_candidate = _first_chapter_candidate(candidate)
                anchors = extracted_http_anchors(main_html, normalized_url)
                candidate_url = _canonical_http_url(normalized_url, chapter_candidate)
                candidate_host = urlsplit(candidate_url).hostname if candidate_url else None
                if not chapter_candidate or candidate_url not in anchors:
                    first_errors = ["first chapter URL is not an HTTP(S) anchor extracted from the main page."]
                elif candidate_host != urlsplit(normalized_url).hostname:
                    first_errors = ["first chapter URL must stay on the fetched site's host."]
                elif not partial:
                    first_errors = ["first AI response did not contain a partial format definition."]
                else:
                    first_errors = _validate_first_stage(
                        partial,
                        selected,
                        BeautifulSoup(main_html, "html.parser"),
                    )
            except Exception as exc:
                first_errors = [_safe_error(exc)]
            if not first_errors:
                break
            if repair_attempt >= REPAIR_LIMIT or not _approve(confirm_repair, tuple(first_errors), repair_attempt + 1):
                return _result_with_draft(
                    host=host,
                    candidate=candidate,
                    errors=first_errors,
                    drafts_dir=drafts_dir,
                    confirm_draft_overwrite=confirm_draft_overwrite,
                )
            first_prompt = (
                f"Repair attempt {repair_attempt + 1}: fix these validation errors: {first_errors}.\n"
                f"Return the same first-stage JSON using only this page evidence:\n{compact_main}"
            )

        chapter_url = _canonical_http_url(normalized_url, _first_chapter_candidate(candidate))
        if not chapter_url:
            return _result_with_draft(host=host, candidate=candidate, errors=["first chapter URL is unsafe or missing."], drafts_dir=drafts_dir)
        chapter_html = service.get_raw_page(chapter_url)
        compact_chapter = compact_html(chapter_html)
        second_prompt = (
            f"content_type={selected.value}\nReturn only chapter-page selectors and the executable flow. "
            "Do not repeat or replace first-stage fields.\n"
            f"partial={json.dumps(_safe_value(_candidate_format(candidate)), ensure_ascii=False)}\n"
            f"chapter_url={_redact_url(chapter_url)}\nchapter_page={compact_chapter}"
        )
        second_candidate: object = {}
        final_errors: list[str] = []
        for repair_attempt in range(REPAIR_LIMIT + 1):
            try:
                second_candidate = generator(prompt, second_prompt, SECOND_CALL_SCHEMA)
                final_definition = _normalize_gallery_definition(
                    _merge_dicts(_candidate_format(candidate), _candidate_format(second_candidate))
                )
                final_definition["content_type"] = selected.value
                validate_site_definition(
                    final_definition,
                    content_type=selected,
                    main_html=main_html,
                    chapter_html=chapter_html,
                    main_url=normalized_url,
                    chapter_url=chapter_url,
                )
                final_errors = []
            except Exception as exc:
                final_errors = [_safe_error(exc)]
            if not final_errors:
                break
            if repair_attempt >= REPAIR_LIMIT or not _approve(confirm_repair, tuple(final_errors), repair_attempt + 1):
                return _result_with_draft(
                    host=host,
                    candidate=_merge_dicts(_candidate_format(candidate), _candidate_format(second_candidate)),
                    errors=final_errors,
                    drafts_dir=drafts_dir,
                    definition=final_definition,
                    confirm_draft_overwrite=confirm_draft_overwrite,
                )

            second_prompt = (
                f"Repair attempt {repair_attempt + 1}: fix these errors exactly: {final_errors}.\n"
                f"Return only chapter-page selectors and flow. Preserve content_type={selected.value}. "
                "Do not repeat first-stage fields.\n"
                f"current={json.dumps(_safe_value(final_definition), ensure_ascii=False)}\n"
                f"main_page={compact_main}\nchapter_page={compact_chapter}"
            )

        validated_definition = validate_site_definition(
            final_definition,
            content_type=selected,
            main_html=main_html,
            chapter_html=chapter_html,
            main_url=normalized_url,
            chapter_url=chapter_url,
        )
        summary = {
            "content_type": selected.value,
            "title_selector": validated_definition.get("title"),
            "first_chapter_url": _redact_url(chapter_url),
            "flow_modules": [step.get("module") for step in validated_definition.get("flow", []) if isinstance(step, Mapping)],
        }
        if not _approve_registration(confirm_registration, summary):
            draft_path, report_path = save_draft(
                host,
                validated_definition,
                ["registration was not confirmed"],
                drafts_dir=drafts_dir,
                overwrite=None,
                confirm_overwrite=confirm_draft_overwrite,
            )
            return AIUpdateResult(
                success=False,
                validated=True,
                definition=validated_definition,
                errors=("registration was not confirmed",),
                draft_path=draft_path,
                report_path=report_path,
                summary=summary,
            )
        try:
            registration = register_site_definition(
                validated_definition,
                host=host,
                formats_dir=formats_dir,
                aliases_path=aliases_path,
                format_name=format_name,
                confirmed=True,
            )
        except Exception as exc:
            draft_path, report_path = save_draft(
                host,
                validated_definition,
                [_safe_error(exc)],
                drafts_dir=drafts_dir,
                overwrite=None,
                confirm_overwrite=confirm_draft_overwrite,
            )
            return AIUpdateResult(
                success=False,
                validated=True,
                definition=validated_definition,
                errors=(_safe_error(exc),),
                draft_path=draft_path,
                report_path=report_path,
                summary=summary,
            )
        return AIUpdateResult(
            success=True,
            validated=True,
            registered=True,
            definition=validated_definition,
            format_path=registration.format_path,
            aliases_path=registration.aliases_path,
            summary=summary,
        )
    except Exception as exc:
        return _result_with_draft(
            host=host,
            candidate=candidate,
            errors=[_safe_error(exc)],
            drafts_dir=drafts_dir,
            confirm_draft_overwrite=confirm_draft_overwrite,
        )
    finally:
        if raw_page is None:
            service.close()


analyze_site = AI_update
ai_update = AI_update
validate_definition = validate_site_definition
write_draft = save_draft
register_definition = register_site_definition


__all__ = [
    "AIUpdateResult",
    "FIRST_CALL_SCHEMA",
    "FlowCrawler",
    "REPAIR_LIMIT",
    "RegistrationResult",
    "SECOND_CALL_SCHEMA",
    "AI_update",
    "ai_update",
    "analyze_site",
    "compact_html",
    "extracted_http_anchors",
    "register_definition",
    "register_site_definition",
    "save_draft",
    "validate_definition",
    "validate_site_definition",
    "write_draft",
]
