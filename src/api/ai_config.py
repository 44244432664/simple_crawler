"""AI provider configuration loaded from ``data/secrets.json``.

Task 7 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

The single active AI provider key and model are read from the ``API`` block in
the ignored ``data/secrets.json`` file:

.. code-block:: json

    {
      "API": {
        "openai": "...",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "timeout": 60
      }
    }

Design rules
------------
* **No ``.env``.**  Secrets live only in ``data/secrets.json``; this module does
  not create or load a ``.env`` file.
* **Pure validation.**  Building an :class:`AIConfig` performs no network calls,
  directory creation, or output writes.
* **Secret-safe.**  Errors from this module never echo the ``api_key`` value, and
  the ``to_dict`` view deliberately omits it.
"""

from __future__ import annotations

import json
import ipaddress
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from api.contracts import AIConfigError

# Provider name -> whether an API key is mandatory.  Ollama may omit the key.
PROVIDERS: dict[str, bool] = {
    "openai": True,
    "groq": True,
    "gemini": True,
    "anthropic": True,
    "ollama": False,
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434",
}

DEFAULT_TIMEOUT = 60
"""Default request timeout in seconds when the config omits ``timeout``."""


class AIConfig:
    """Validated active AI provider configuration.

    Attributes mirror the selected ``API.<provider>`` JSON block.  ``base_url`` is resolved to a
    provider default when omitted.  ``api_key`` may be empty for Ollama.
    """

    __slots__ = ("provider", "api_key", "model", "base_url", "timeout")

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        timeout: int,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def needs_key(self) -> bool:
        return PROVIDERS.get(self.provider, True)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def redact(self, value: str) -> str:
        """Replace the configured key with ``[REDACTED]`` (no-op keyless)."""
        if self.api_key:
            return value.replace(self.api_key, "[REDACTED]")
        return value

    def to_dict(self) -> dict[str, str | int]:
        """JSON-safe, key-free view (never includes the credential)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
        }


def _normalize_provider(value: object) -> str:
    normalized = str(value or "").strip().lower() if isinstance(value, str) else str(value or "").strip().lower()
    if normalized not in PROVIDERS:
        options = ", ".join(sorted(PROVIDERS))
        raise AIConfigError(
            f"API provider must be one of: {options}. Got {value!r}."
        )
    return normalized


def _normalize_non_secret_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AIConfigError(f"API provider {label} must be a string. Got {type(value).__name__}.")
    normalized = value.strip()
    if not normalized:
        raise AIConfigError(f"API provider {label} is required.")
    return normalized


def _normalize_key(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AIConfigError("API provider api_key must be a string or be omitted for Ollama.")
    return value.strip()


def _normalize_base_url(value: object, provider: str) -> str:
    if value is None:
        return DEFAULT_BASE_URLS[provider]
    if not isinstance(value, str) or not value.strip():
        raise AIConfigError("API provider base_url must be a non-empty http(s) URL string or be omitted.")
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        # Accessing ``port`` also rejects malformed/non-numeric ports.
        parsed.port
    except ValueError:
        raise AIConfigError("API provider base_url must be a valid absolute http(s) URL.") from None
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise AIConfigError("API provider base_url must be an absolute http(s) URL.")
    if any(char.isspace() for char in candidate):
        raise AIConfigError("API provider base_url must not contain whitespace.")
    if parsed.username is not None or parsed.password is not None:
        raise AIConfigError("API provider base_url must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise AIConfigError("API provider base_url must not contain a query string or fragment.")
    if parsed.scheme.lower() == "http":
        is_loopback = host.lower() == "localhost" or host.lower().endswith(".localhost")
        try:
            is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not is_loopback:
            raise AIConfigError(
                "API provider base_url must use HTTPS unless it targets a loopback host."
            )
    return candidate


def _normalize_timeout(value: object) -> int:
    if value is None:
        return DEFAULT_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, int):
        raise AIConfigError("API provider timeout must be a positive integer (or be omitted).")
    if value <= 0:
        raise AIConfigError("API provider timeout must be a positive integer.")
    return value


def aiconfig_from_dict(data: object) -> AIConfig:
    """Build an :class:`AIConfig` from one ``API.<provider>`` JSON object.

    Raises :class:`AIConfigError` on any missing/malformed/invalid field
    without echoing the credential.
    """
    if not isinstance(data, dict):
        raise AIConfigError("API provider configuration must be a JSON object with provider/api_key/model.")
    provider = _normalize_provider(data.get("provider"))
    api_key = _normalize_key(data.get("api_key"))
    model = _normalize_non_secret_string(data.get("model"), "model")
    base_url = _normalize_base_url(data.get("base_url"), provider)
    timeout = _normalize_timeout(data.get("timeout"))
    if PROVIDERS[provider] and not api_key:
        raise AIConfigError(
            f"API provider api_key is required for provider {provider!r} (Ollama may omit it)."
        )
    return AIConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )


def load_secrets(path: str | os.PathLike) -> dict[str, Any]:
    """Load and parse a secrets JSON file and return its JSON object.

    Raises :class:`AIConfigError` if the file is missing, unreadable, or not a
    top-level JSON object.
    """
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise AIConfigError(f"Secrets file not found at {p}.")
    except json.JSONDecodeError as exc:
        # JSONDecodeError retains the complete source document on ``exc.doc``;
        # chaining it would keep credentials reachable through the public error.
        raise AIConfigError(f"Secrets file {p} is not valid JSON: {exc}") from None
    except (OSError, UnicodeError) as exc:
        raise AIConfigError(f"Could not read secrets file {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise AIConfigError(f"Secrets file {p} must contain a top-level JSON object.")
    return data


def load_aiconfig(
    secrets_path: str | os.PathLike | None = None,
    raw: object | None = None,
) -> AIConfig:
    """Load the active :class:`AIConfig` from ``data/secrets.json``.

    Parameters
    ----------
    secrets_path : str or Path or None
        Path to the secrets file.  ``None`` loads ``data/secrets.json`` (the
        authoritative, ignored file referenced by the plan).
    raw : object, optional
        When provided, used as the already-parsed secrets dict (tests inject
        this so no file is touched).

    Raises
    ------
    AIConfigError
        The file is missing/malformed or the ``API.<provider>``/``API.model``
        fields are missing or invalid.
    """
    if raw is not None:
        secrets = raw if isinstance(raw, dict) else {"API": raw}
    else:
        root = Path(__file__).resolve().parents[1]
        path = Path(secrets_path) if secrets_path else (root / "data" / "secrets.json")
        secrets = load_secrets(path)
    api = secrets.get("API") if isinstance(secrets, dict) else None
    if not isinstance(api, dict):
        raise AIConfigError(
            "API is not configured. Add an 'API' block containing one provider "
            "key (for example, API.openai) and API.model to data/secrets.json."
        )
    providers = [
        name
        for name in PROVIDERS
        if name in api
    ]
    if len(providers) != 1:
        raise AIConfigError(
            "API must contain exactly one configured provider key, such as "
            "API.openai."
        )
    provider = providers[0]
    return aiconfig_from_dict(
        {
            "provider": provider,
            "api_key": api.get(provider),
            "model": api.get("model"),
            "base_url": api.get("base_url"),
            "timeout": api.get("timeout"),
        }
    )


def default_secrets_path() -> Path:
    """Return the canonical ``data/secrets.json`` path."""
    root = Path(__file__).resolve().parents[1]
    return root / "data" / "secrets.json"


def system_prompt_path() -> Path:
    """Return the tracked, versioned system-prompt file path.

    This is the single source of the selector/flow/content/untrusted-HTML rules
    documented by Task 7.  The project intentionally does not use ``.env``.
    """
    root = Path(__file__).resolve().parents[1]
    return root / "data" / "system_prompt" / "ai_site_generation_v1.txt"


def load_system_prompt(path: str | os.PathLike | None = None) -> str:
    """Load the system-prompt text file for AI site-definition generation.

    Parameters
    ----------
    path : str or Path or None
        Overrides the default tracked prompt file when provided.

    Raises
    ------
    AIConfigError
        The prompt file is missing or unreadable.
    """
    p = Path(path) if path else system_prompt_path()
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AIConfigError(f"System prompt file not found at {p}.") from None
    except (OSError, UnicodeError) as exc:
        raise AIConfigError(f"Could not read system prompt file {p}: {exc}") from exc


__all__ = [
    "AIConfig",
    "DEFAULT_BASE_URLS",
    "DEFAULT_TIMEOUT",
    "PROVIDERS",
    "aiconfig_from_dict",
    "default_secrets_path",
    "load_aiconfig",
    "load_secrets",
    "load_system_prompt",
    "system_prompt_path",
]
