"""Native HTTP adapters for the approved AI providers and ``generate_json``.

Task 7 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

This module returns one structured JSON object from any supported AI provider
through a single :func:`generate_json` interface, using only the stdlib and
``requests`` (no AI SDK dependency).

Supported providers (native HTTP adapters, no SDK):
* ``openai``      -- ``{base}/chat/completions``
* ``groq``        -- ``{base}/chat/completions``
* ``gemini``      -- ``{base}/v1beta/models/{model}:generateContent``
* ``anthropic``   -- ``{base}/v1/messages``
* ``ollama``      -- ``{base}/api/chat`` (may omit the API key)

Design rules
------------
* **Secret-safe.**  The ``api_key`` is used only to build an authorization
  header.  It is never logged, never included in error
  messages, and full provider responses never leak through exceptions.  All
  raised errors are scrubbed with :meth:`AIConfig.redact`.
* **Fenced JSON tolerated.**  A response whose content wraps JSON in a
  ````` ```json ... ``` `````` code fence is unwrapped before parsing.
* **Actionable failures.**  Auth (401/403), rate-limit (429), timeout, and
  server errors raise :class:`AIConfigError` (config/server-level) while
  *successful* calls that yield malformed/un-parseable output raise
  :class:`AIValidationError`.
* **No ``.env`` and no SDK import.**
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from api.ai_config import AIConfig, load_aiconfig
from api.contracts import AIConfigError, AIValidationError

_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*(.*?)\s*```$", re.DOTALL)
_MAX_ERROR_DETAIL = 400
_SECRET_FIELD_RE = re.compile(
    r"(?i)(?P<label>(?:authorization|x-api-key|x-goog-api-key)\s*[:=]\s*)"
    r"(?P<value>(?:(?:Bearer|Basic)\s+)?[^\s,;]+)"
)
_SECRET_QUERY_RE = re.compile(
    r"(?i)(?P<label>[?&](?:key|api_key|access_token)=)(?P<value>[^&\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


class ProviderError(Exception):
    """Internal marker wrapping a provider-level failure (never surfaced).

    The message here may contain provider wording; callers redact it before
    raising the public :class:`AIConfigError`.
    """


# ---------------------------------------------------------------------------
# Request/response normalization per provider
# ---------------------------------------------------------------------------


def _term(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _schema_instruction(system_prompt: str, schema: object) -> str:
    """Ground JSON-only providers with the exact schema in their prompt."""
    if not isinstance(schema, dict) or not schema:
        return system_prompt
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{system_prompt.rstrip()}\n\n"
        "Return exactly one JSON object matching this JSON Schema. Do not add "
        f"markdown or commentary:\n{encoded}"
    )


def _openai_request(
    cfg: AIConfig, system_prompt: str, user_prompt: str, schema: object
) -> tuple[str, dict, dict, str]:
    url = f"{cfg.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    structured = isinstance(schema, dict) and bool(schema)
    prompt = (
        _schema_instruction(system_prompt, schema)
        if cfg.provider == "groq"
        else system_prompt
    )
    body = {
        "model": cfg.model,
        "messages": _term(prompt, user_prompt),
        "temperature": 0,
        # Groq supports JSON-object mode across more models than JSON-schema
        # mode.  The exact schema remains in the system prompt and is always
        # validated locally below.
        "response_format": (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "site_definition",
                    "strict": False,
                    "schema": schema,
                },
            }
            if structured and cfg.provider == "openai"
            else {"type": "json_object"}
        ),
    }
    return url, headers, body, "openai"


def _openai_extract(payload: dict) -> str:
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("OpenAI-style response missing choices[0].message.content") from exc


def _gemini_request(
    cfg: AIConfig, system_prompt: str, user_prompt: str, schema: object
) -> tuple[str, dict, dict, str]:
    model = cfg.model.removeprefix("models/")
    url = f"{cfg.base_url}/v1beta/models/{quote(model, safe='-._~')}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": cfg.api_key}
    body = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    if isinstance(schema, dict) and schema:
        body["generationConfig"]["responseJsonSchema"] = schema
    return url, headers, body, "gemini"


def _gemini_extract(payload: dict) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Gemini response missing candidates[0].content.parts[0].text") from exc
    if not text:
        raise ProviderError("Gemini response contained no text content part")
    return text


def _anthropic_request(
    cfg: AIConfig, system_prompt: str, user_prompt: str, schema: object
) -> tuple[str, dict, dict, str]:
    url = f"{cfg.base_url}/v1/messages"
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if isinstance(schema, dict) and schema:
        body["output_config"] = {
            "format": {"type": "json_schema", "schema": schema}
        }
    return url, headers, body, "anthropic"


def _anthropic_extract(payload: dict) -> str:
    try:
        content = payload["content"]
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    except (KeyError, TypeError) as exc:
        raise ProviderError("Anthropic response missing content[].text") from exc
    if text:
        return text
    raise ProviderError("Anthropic response contained no text content block")


def _ollama_request(
    cfg: AIConfig, system_prompt: str, user_prompt: str, schema: object
) -> tuple[str, dict, dict, str]:
    url = f"{cfg.base_url}/api/chat"
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    host = (urlsplit(cfg.base_url).hostname or "").lower()
    # Ollama Cloud currently accepts JSON mode but not schema-constrained
    # output.  Keep the schema in the system prompt there and validate locally.
    cloud_host = host == "ollama.com" or host.endswith(".ollama.com")
    structured = isinstance(schema, dict) and bool(schema)
    body = {
        "model": cfg.model,
        "messages": _term(_schema_instruction(system_prompt, schema), user_prompt),
        "stream": False,
        "options": {"temperature": 0},
        "format": schema if structured and not cloud_host else "json",
    }
    return url, headers, body, "ollama"


def _ollama_extract(payload: dict) -> str:
    try:
        return payload["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise ProviderError("Ollama response missing message.content") from exc


# Provider -> (request builder, response extractor)
_OPENAI_LIKE = {"openai", "groq"}


def _request_builder(cfg: AIConfig):
    if cfg.provider in _OPENAI_LIKE:
        return _openai_request
    if cfg.provider == "gemini":
        return _gemini_request
    if cfg.provider == "anthropic":
        return _anthropic_request
    if cfg.provider == "ollama":
        return _ollama_request
    raise AIConfigError(f"Unsupported AI provider {cfg.provider!r}.")


def _extractor(cfg: AIConfig):
    if cfg.provider in _OPENAI_LIKE:
        return _openai_extract
    if cfg.provider == "gemini":
        return _gemini_extract
    if cfg.provider == "anthropic":
        return _anthropic_extract
    if cfg.provider == "ollama":
        return _ollama_extract
    raise AIConfigError(f"Unsupported AI provider {cfg.provider!r}.")


def _safe_error_message(config: AIConfig, message: str) -> str:
    """Scrub any accidental credential leak and keep a short actionable text."""
    # Bound work before applying regexes: response bodies can be arbitrarily
    # large, while only the first few hundred characters are user-actionable.
    scan_limit = _MAX_ERROR_DETAIL + max(len(config.api_key), 256) + 100
    message = config.redact(str(message or "")[:scan_limit])
    message = _BEARER_RE.sub("Bearer [REDACTED]", message)
    message = _SECRET_FIELD_RE.sub(r"\g<label>[REDACTED]", message)
    message = _SECRET_QUERY_RE.sub(r"\g<label>[REDACTED]", message)
    message = " ".join(message.split()) or "no detail"
    if len(message) > _MAX_ERROR_DETAIL:
        message = f"{message[:_MAX_ERROR_DETAIL]}… [truncated]"
    return message


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def _session() -> requests.Session:
    return requests.Session()


def _retry_after_seconds(resp: requests.Response, timeout: int) -> float | None:
    """Return a bounded numeric Retry-After delay for one provider retry."""
    headers = getattr(resp, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if delay <= 0 or delay > min(timeout, 60):
        return None
    return delay


def generate_json(
    system_prompt: str,
    payload: str,
    schema: object,
    *,
    config: AIConfig | None = None,
    session: requests.Session | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Ask the active AI provider for one structured JSON object.

    Parameters
    ----------
    system_prompt : str
        The tracked system-prompt text (selector rules, flow schema, content
        requirements, untrusted-HTML handling).
    payload : str
        User content to analyze (e.g. the compacted main-page or chapter HTML).
        Treated as untrusted data; it is placed in the user message verbatim.
    schema : object
        JSON schema describing the expected output shape.  Embedded into the
        request so providers that support structured output honor it; always
        used to validate the returned JSON.
    config : AIConfig, optional
        Active provider configuration.  ``None`` loads it from
        ``data/secrets.json`` via :func:`api.ai_config.load_aiconfig`.
    session : requests.Session, optional
        Reusable session (tests inject a mocked session; otherwise a short-lived
        session is opened and closed per call).
    timeout : int, optional
        Overrides ``config.timeout`` when provided.

    Returns
    -------
    dict
        The parsed JSON object returned by the provider (fenced JSON
        unwrapped), validated against *schema*.

    Raises
    ------
    AIConfigError
        No/unsupported/invalid configuration, or a provider-level failure
        (auth, rate-limit, timeout, server error, network error).  Messages are
        secret-safe and actionable.
    AIValidationError
        The provider returned text that is not valid JSON or does not match the
        schema.
    """
    if config is None:
        config = load_aiconfig()
    if not isinstance(config, AIConfig):
        raise AIConfigError(
            f"config must be an AIConfig; got {type(config).__name__}."
        )

    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise AIConfigError("system_prompt must be a non-empty string.")
    if not isinstance(payload, str):
        raise AIConfigError("payload must be a string.")
    _validate_schema_definition(schema)

    url, headers, body, _ = _request_builder(config)(
        config, system_prompt, payload, schema
    )
    effective_timeout = timeout if timeout is not None else config.timeout
    if isinstance(effective_timeout, bool) or not isinstance(effective_timeout, int) or effective_timeout <= 0:
        raise AIConfigError("timeout must be a positive integer number of seconds.")

    own_session = session is None
    sess = session if own_session is False else _session()
    try:
        try:
            resp = sess.post(
                url, headers=headers, json=body, timeout=effective_timeout
            )
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp, effective_timeout)
                if retry_after is not None:
                    time.sleep(retry_after)
                    resp = sess.post(
                        url, headers=headers, json=body, timeout=effective_timeout
                    )
        except requests.Timeout as exc:
            raise AIConfigError(
                f"AI provider {config.provider!r} timed out after "
                f"{effective_timeout}s (host {_host_for_error(url)})."
            ) from None
        except requests.RequestException as exc:
            raise AIConfigError(
                f"AI provider {config.provider!r} request failed: "
                f"{_safe_error_message(config, str(exc))}"
            ) from None
    finally:
        if own_session:
            sess.close()

    return _handle_response(config, url, resp, schema)


def _host_for_error(url: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or "?"
    return host


def _handle_response(
    config: AIConfig, url: str, resp: requests.Response, schema: object
) -> dict[str, Any]:
    """Dispatch a provider HTTP response and return the validated JSON object."""
    provider = config.provider
    status = resp.status_code

    # Authentication failures
    if status in (401, 403):
        raise AIConfigError(
            f"AI provider {provider!r} rejected the API key (HTTP {status}). "
            "Check API.<provider>.api_key and base_url in data/secrets.json."
        )
    if status == 429:
        raise AIConfigError(
            f"AI provider {provider!r} is rate-limited (HTTP 429). "
            "Wait and retry, or lower request volume."
        )
    if status >= 500:
        raise AIConfigError(
            f"AI provider {provider!r} reported a server error (HTTP {status})."
        )
    if status >= 400:
        raise AIConfigError(
            f"AI provider {provider!r} returned HTTP {status}. "
            f"Detail: {_safe_error_message(config, resp.text)}"
        )
    if status != 200:
        raise AIConfigError(
            f"AI provider {provider!r} returned unexpected HTTP {status}."
        )

    # Parse the (JSON) envelope.
    try:
        envelope = resp.json()
    except (ValueError, requests.exceptions.JSONDecodeError) as exc:
        raise AIValidationError(
            f"AI provider {provider!r} returned a non-JSON response. "
            f"Detail: {_safe_error_message(config, resp.text)}"
        ) from None

    # Extract the content field, normalizing per provider.
    extractor = _extractor(config)
    try:
        content = extractor(envelope)
    except ProviderError as exc:
        raise AIValidationError(
            f"AI provider {provider!r} response had an unexpected shape: "
            f"{_safe_error_message(config, str(exc))}"
        ) from exc

    parsed = _parse_json_content(config, content)
    _validate_schema(parsed, schema, config)
    return parsed


def _parse_json_content(config: AIConfig, content: str) -> dict[str, Any]:
    """Parse provider returned text as JSON, unwrapping a code fence if needed.

    Raises :class:`AIValidationError` when the content is not valid JSON.
    """
    if not isinstance(content, str):
        raise AIValidationError(
            f"AI provider {config.provider!r} returned a non-string content field."
        )
    text = content.strip()
    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIValidationError(
            f"AI provider {config.provider!r} returned malformed JSON: {exc}. "
            "The response could not be interpreted as a JSON object."
        ) from None
    if not isinstance(parsed, dict):
        raise AIValidationError(
            f"AI provider {config.provider!r} returned JSON that is not an "
            f"object (got {type(parsed).__name__})."
        )
    return parsed


_JSON_TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}


def _validate_schema_definition(schema: object, path: str = "schema") -> None:
    """Reject malformed schemas before issuing a billable provider request."""
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise AIConfigError(f"{path} must be a JSON Schema object or None.")
    declared = schema.get("type")
    declared_types = declared if isinstance(declared, list) else [declared]
    if declared is not None and (
        not declared_types
        or any(not isinstance(item, str) or item not in _JSON_TYPES for item in declared_types)
    ):
        raise AIConfigError(f"{path}.type contains an unsupported JSON type.")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise AIConfigError(f"{path}.properties must be an object.")
        for name, rule in properties.items():
            if not isinstance(name, str):
                raise AIConfigError(f"{path}.properties keys must be strings.")
            _validate_schema_definition(rule, f"{path}.properties.{name}")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list) or any(not isinstance(key, str) for key in required)
    ):
        raise AIConfigError(f"{path}.required must be an array of strings.")
    items = schema.get("items")
    if items is not None:
        _validate_schema_definition(items, f"{path}.items")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, dict)):
        raise AIConfigError(f"{path}.additionalProperties must be boolean or a schema.")
    if isinstance(additional, dict):
        _validate_schema_definition(additional, f"{path}.additionalProperties")
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(keyword)
        if branches is None:
            continue
        if not isinstance(branches, list) or not branches:
            raise AIConfigError(f"{path}.{keyword} must be a non-empty schema array.")
        for index, branch in enumerate(branches):
            _validate_schema_definition(branch, f"{path}.{keyword}[{index}]")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise AIConfigError(f"{path}.enum must be a non-empty array.")
    pattern = schema.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise AIConfigError(f"{path}.pattern must be a string.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise AIConfigError(f"{path}.pattern is invalid: {exc}.") from None


def _matches_type(value: object, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }[expected]


def _value_error(value: object, schema: dict, path: str) -> str | None:
    if "const" in schema and value != schema["const"]:
        return f"{path} must equal {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']!r}"

    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        errors = [_value_error(value, branch, path) for branch in branches]
        matches = sum(error is None for error in errors)
        if keyword == "allOf" and matches != len(branches):
            return next(error for error in errors if error is not None)
        if keyword == "anyOf" and matches == 0:
            return f"{path} does not match any allowed schema"
        if keyword == "oneOf" and matches != 1:
            return f"{path} must match exactly one allowed schema"

    declared = schema.get("type")
    expected_types = declared if isinstance(declared, list) else [declared]
    expected_types = [item for item in expected_types if isinstance(item, str)]
    if expected_types and not any(_matches_type(value, item) for item in expected_types):
        return f"{path} must be of type {' or '.join(expected_types)}"

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            return f"{path} is missing required field(s): {', '.join(missing)}"
        properties = schema.get("properties", {})
        for key, rule in properties.items():
            if key in value:
                error = _value_error(value[key], rule, f"{path}.{key}")
                if error:
                    return error
        additional = schema.get("additionalProperties", True)
        extra = [key for key in value if key not in properties]
        if additional is False and extra:
            return f"{path} contains unexpected field(s): {', '.join(extra)}"
        if isinstance(additional, dict):
            for key in extra:
                error = _value_error(value[key], additional, f"{path}.{key}")
                if error:
                    return error

    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            return f"{path} must contain at least {schema['minItems']} item(s)"
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            return f"{path} must contain at most {schema['maxItems']} item(s)"
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                error = _value_error(item, items, f"{path}[{index}]")
                if error:
                    return error

    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            return f"{path} is shorter than minLength {schema['minLength']}"
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            return f"{path} is longer than maxLength {schema['maxLength']}"
        if isinstance(schema.get("pattern"), str) and re.search(schema["pattern"], value) is None:
            return f"{path} does not match the required pattern"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            return f"{path} is below minimum {schema['minimum']}"
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            return f"{path} is above maximum {schema['maximum']}"
    return None


def _validate_schema(parsed: dict[str, Any], schema: object, config: AIConfig) -> None:
    """Validate provider output against the supported JSON Schema subset."""
    if not isinstance(schema, dict) or not schema:
        return
    error = _value_error(parsed, schema, "response")
    if error:
        raise AIValidationError(
            f"AI provider {config.provider!r} response failed schema validation: {error}."
        )


__all__ = ["generate_json"]
