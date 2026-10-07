"""Task 7 — AI provider + prompt infrastructure tests.

Run with:
    source .Luma/bin/activate
    python -m pytest TEST/test_ai_provider.py -v
    # or
    python -m unittest TEST/test_ai_provider.py -v
"""

from __future__ import annotations

import json
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

import requests

from api.ai_config import (
    AIConfig,
    DEFAULT_BASE_URLS,
    DEFAULT_TIMEOUT,
    PROVIDERS,
    aiconfig_from_dict,
    default_secrets_path,
    load_aiconfig,
    load_secrets,
    load_system_prompt,
    system_prompt_path,
)
from api.ai_provider import generate_json
from api.contracts import AIConfigError, AIValidationError

SECRET = "sk-a-really-secret-key-for-tests-123456"
BASE_AI = {
    "provider": "openai",
    "api_key": SECRET,
    "model": "gpt-4o-mini",
    "base_url": None,
    "timeout": 30,
}
API = {
    "openai": BASE_AI["api_key"],
    "model": BASE_AI["model"],
    "base_url": BASE_AI["base_url"],
    "timeout": BASE_AI["timeout"],
}

SCHEMA = {
    "required": ["title", "flow"],
    "properties": {
        "title": {"type": "string"},
        "flow": {"type": "array"},
    },
}

VALID_JSON = {"title": "Alpha", "flow": []}


def _config(**overrides):
    data = dict(BASE_AI)
    data.update(overrides)
    return aiconfig_from_dict(data)


def _make_response(status=200, envelope=None, text=""):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {}
    if envelope is not None:
        resp.json.return_value = envelope
    return resp


def _mock_session(envelope=None, status=200, text="", exc=None):
    session = mock.MagicMock()
    if exc is not None:
        session.post.side_effect = exc
    else:
        session.post.return_value = _make_response(status=status, envelope=envelope, text=text)
    return session


# ---------------------------------------------------------------------------
# AIConfig loading / validation
# ---------------------------------------------------------------------------


class TestAIConfigLoading(unittest.TestCase):
    def test_defaults_resolved(self):
        cfg = aiconfig_from_dict(
            {"provider": "openai", "api_key": SECRET, "model": "gpt-4o-mini"}
        )
        self.assertEqual(cfg.provider, "openai")
        self.assertEqual(cfg.base_url, DEFAULT_BASE_URLS["openai"])
        self.assertEqual(cfg.timeout, DEFAULT_TIMEOUT)
        self.assertTrue(cfg.has_key)
        self.assertTrue(cfg.needs_key)
        self.assertEqual(
            cfg.to_dict(),
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "base_url": DEFAULT_BASE_URLS["openai"],
                "timeout": DEFAULT_TIMEOUT,
            },
        )

    def test_to_dict_never_includes_key(self):
        cfg = _config()
        dumped = json.dumps(cfg.to_dict())
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn("api_key", cfg.to_dict())

    def test_ollama_may_omit_key(self):
        cfg = aiconfig_from_dict(
            {"provider": "ollama", "model": "llama3", "base_url": None, "timeout": None}
        )
        self.assertEqual(cfg.api_key, "")
        self.assertFalse(cfg.has_key)
        self.assertFalse(cfg.needs_key)
        self.assertEqual(cfg.base_url, DEFAULT_BASE_URLS["ollama"])

    def test_custom_base_url_preserved_and_stripped(self):
        cfg = _config(base_url="https://proxy.example.com/v1/")
        self.assertEqual(cfg.base_url, "https://proxy.example.com/v1")

    def test_provider_required_and_allowlisted(self):
        for bad in (None, "", "unknown", "cohere"):
            with self.subTest(bad=bad):
                with self.assertRaises(AIConfigError) as ctx:
                    _config(provider=bad)
                self.assertIn("provider", str(ctx.exception))

    def test_all_supported_providers(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                required = PROVIDERS[provider]
                cfg = _config(provider=provider)
                self.assertEqual(cfg.provider, provider)
                self.assertEqual(cfg.needs_key, required)

    def test_missing_key_for_keyed_provider(self):
        for provider in ("openai", "groq", "gemini", "anthropic"):
            with self.subTest(provider=provider):
                with self.assertRaises(AIConfigError):
                    _config(provider=provider, api_key=None)

    def test_model_required(self):
        with self.assertRaises(AIConfigError):
            _config(model=None)
        with self.assertRaises(AIConfigError):
            _config(model="   ")

    def test_timeout_validation(self):
        for bad in (0, -1, True, "30", 3.5):
            with self.subTest(bad=bad):
                with self.assertRaises(AIConfigError):
                    _config(timeout=bad)

    def test_base_url_scheme_validated(self):
        for bad in (
            "not-a-url",
            "ftp://x",
            "https://has space",
            "https://",
            "http://remote.example.com",
            "https://user:password@example.com",
            "https://example.com?key=secret",
            "https://example.com/#fragment",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(AIConfigError):
                    _config(base_url=bad)

    def test_loopback_http_base_url_allowed(self):
        for value in ("http://localhost:11434", "http://127.0.0.1:8080", "http://[::1]:9090"):
            with self.subTest(value=value):
                self.assertEqual(_config(base_url=value).base_url, value)

    def test_load_secrets_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            with self.assertRaises(AIConfigError) as ctx:
                load_secrets(missing)
            self.assertIn("not found", str(ctx.exception).lower())

    def test_load_secrets_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(AIConfigError) as ctx:
                load_secrets(path)
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_malformed_secrets_exception_chain_does_not_retain_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(f'{{"api_key":"{SECRET}"', encoding="utf-8")
            with self.assertRaises(AIConfigError) as ctx:
                load_secrets(path)
            rendered = "".join(traceback.format_exception(ctx.exception))
            self.assertNotIn(SECRET, rendered)
            self.assertIsNone(ctx.exception.__cause__)

    def test_load_secrets_not_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "arr.json"
            path.write_text("[1,2]", encoding="utf-8")
            with self.assertRaises(AIConfigError):
                load_secrets(path)

    def test_load_aiconfig_missing_api_block(self):
        with self.assertRaises(AIConfigError) as ctx:
            load_aiconfig(raw={"some": "other"})
        self.assertIn("API", str(ctx.exception))

    def test_load_aiconfig_from_raw(self):
        cfg = load_aiconfig(raw={"API": API})
        self.assertEqual(cfg.provider, "openai")
        self.assertEqual(cfg.api_key, SECRET)

    def test_load_aiconfig_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            path.write_text(json.dumps({"API": API}), encoding="utf-8")
            cfg = load_aiconfig(secrets_path=path)
            self.assertEqual(cfg.provider, "openai")

    def test_default_secrets_path_is_ignored_data_file(self):
        path = default_secrets_path()
        self.assertTrue(str(path).endswith("data/secrets.json"))

    def test_secrets_error_never_echoes_key(self):
        with self.assertRaises(AIConfigError) as ctx:
            load_aiconfig(raw={"API": {"openai": None, "model": "x"}})
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn("api_key=", str(ctx.exception))


# ---------------------------------------------------------------------------
# System prompt file
# ---------------------------------------------------------------------------


class TestSystemPrompt(unittest.TestCase):
    def test_prompt_file_exists_and_tracked_locatable(self):
        path = system_prompt_path()
        self.assertTrue(path.name.startswith("ai_site_generation_v1"))
        self.assertTrue(path.exists())
        content = load_system_prompt()
        self.assertTrue(content.strip())

    def test_prompt_mentions_required_topics(self):
        content = load_system_prompt()
        for topic in (
            "selector",
            "flow",
            "content",
            "untrusted",
            "decimal",
            "vol_group.vol_section",
            "chapter_list.link",
            "gallery_links",
            "attrs`: an object",
        ):
            with self.subTest(topic=topic):
                self.assertIn(topic, content.lower())

    def test_prompt_does_not_describe_attrs_as_string(self):
        content = load_system_prompt().lower()
        self.assertNotIn("`attrs` (a string)", content)

    def test_prompt_load_custom_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(load_system_prompt(path), "hello")

    def test_prompt_missing_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.txt"
            with self.assertRaises(AIConfigError):
                load_system_prompt(missing)


# ---------------------------------------------------------------------------
# Provider normalization — happy paths
# ---------------------------------------------------------------------------


class TestProviderHappyPaths(unittest.TestCase):
    def _assert_same_normalized(self, provider, envelope):
        cfg = _config(provider=provider)
        session = _mock_session(envelope=envelope)
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        return out, session

    def test_openai_style(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]}
        )
        cfg = _config(provider="openai")
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        self.assertTrue("Bearer " in session.post.call_args.kwargs["headers"]["Authorization"])
        response_format = session.post.call_args.kwargs["json"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["schema"], SCHEMA)

    def test_groq_style(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]}
        )
        cfg = _config(provider="groq")
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        self.assertEqual(
            session.post.call_args.args[0], f"{DEFAULT_BASE_URLS['groq']}/chat/completions"
        )
        request_body = session.post.call_args.kwargs["json"]
        self.assertEqual(request_body["response_format"], {"type": "json_object"})
        self.assertIn(json.dumps(SCHEMA, separators=(",", ":")), request_body["messages"][0]["content"])

    def test_gemini_style(self):
        session = _mock_session(
            envelope={"candidates": [{"content": {"parts": [{"text": json.dumps(VALID_JSON)}]}}]}
        )
        cfg = _config(provider="gemini")
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        call = session.post.call_args
        self.assertNotIn(SECRET, call.args[0])
        self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], SECRET)
        self.assertEqual(
            call.kwargs["json"]["generationConfig"]["responseJsonSchema"], SCHEMA
        )
        self.assertEqual(call.kwargs["json"]["systemInstruction"]["parts"][0]["text"].splitlines()[0], "sys")
        self.assertEqual(call.kwargs["json"]["contents"][0]["parts"], [{"text": "payload"}])

    def test_anthropic_style(self):
        session = _mock_session(
            envelope={"content": [{"type": "text", "text": json.dumps(VALID_JSON)}]}
        )
        cfg = _config(provider="anthropic")
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        self.assertEqual(
            session.post.call_args.kwargs["json"]["output_config"],
            {"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    def test_ollama_style(self):
        session = _mock_session(
            envelope={"message": {"content": json.dumps(VALID_JSON)}}
        )
        cfg = _config(provider="ollama", api_key=None)
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        self.assertNotIn("Authorization", session.post.call_args.kwargs["headers"])
        self.assertEqual(session.post.call_args.kwargs["json"]["format"], SCHEMA)

    def test_remote_ollama_uses_configured_key(self):
        session = _mock_session(envelope={"message": {"content": json.dumps(VALID_JSON)}})
        cfg = _config(provider="ollama", base_url="https://ollama.com")
        out = generate_json("sys", "payload", SCHEMA, config=cfg, session=session)
        self.assertEqual(out, VALID_JSON)
        self.assertEqual(
            session.post.call_args.kwargs["headers"]["Authorization"], f"Bearer {SECRET}"
        )
        self.assertEqual(session.post.call_args.kwargs["json"]["format"], "json")
        self.assertIn(
            json.dumps(SCHEMA, separators=(",", ":")),
            session.post.call_args.kwargs["json"]["messages"][0]["content"],
        )

    def test_multipart_text_is_joined_for_gemini_and_anthropic(self):
        fragments = (json.dumps(VALID_JSON)[:10], json.dumps(VALID_JSON)[10:])
        cases = {
            "gemini": {
                "candidates": [{"content": {"parts": [{"text": fragments[0]}, {"text": fragments[1]}]}}]
            },
            "anthropic": {
                "content": [{"type": "text", "text": fragments[0]}, {"type": "text", "text": fragments[1]}]
            },
        }
        for provider, envelope in cases.items():
            with self.subTest(provider=provider):
                session = _mock_session(envelope=envelope)
                self.assertEqual(
                    generate_json("sys", "payload", SCHEMA, config=_config(provider=provider), session=session),
                    VALID_JSON,
                )

    def test_custom_base_url_builds_each_provider_endpoint(self):
        base = "https://proxy.example.test/custom"
        cases = {
            "openai": (
                {"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]},
                f"{base}/chat/completions",
            ),
            "groq": (
                {"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]},
                f"{base}/chat/completions",
            ),
            "gemini": (
                {"candidates": [{"content": {"parts": [{"text": json.dumps(VALID_JSON)}]}}]},
                f"{base}/v1beta/models/gpt-4o-mini:generateContent",
            ),
            "anthropic": (
                {"content": [{"type": "text", "text": json.dumps(VALID_JSON)}]},
                f"{base}/v1/messages",
            ),
            "ollama": (
                {"message": {"content": json.dumps(VALID_JSON)}},
                f"{base}/api/chat",
            ),
        }
        for provider, (envelope, expected_url) in cases.items():
            with self.subTest(provider=provider):
                session = _mock_session(envelope=envelope)
                generate_json(
                    "sys",
                    "payload",
                    SCHEMA,
                    config=_config(provider=provider, base_url=base),
                    session=session,
                )
                self.assertEqual(session.post.call_args.args[0], expected_url)

    def test_equivalent_responses_yield_same_json_across_providers(self):
        expected = {"title": "Same", "flow": [1, 2]}
        results = []
        providers_envelopes = {
            "openai": {"choices": [{"message": {"content": json.dumps(expected)}}]},
            "groq": {"choices": [{"message": {"content": json.dumps(expected)}}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": json.dumps(expected)}]}}]},
            "anthropic": {"content": [{"type": "text", "text": json.dumps(expected)}]},
            "ollama": {"message": {"content": json.dumps(expected)}},
        }
        for provider, envelope in providers_envelopes.items():
            out, _ = self._assert_same_normalized(provider, envelope)
            results.append(out)
        for result in results:
            self.assertEqual(result, expected)

    def test_fenced_json_unwrapped(self):
        fenced = "```json\n" + json.dumps(VALID_JSON) + "\n```"
        session = _mock_session(
            envelope={"choices": [{"message": {"content": fenced}}]}
        )
        out = generate_json("sys", "payload", SCHEMA, config=_config(provider="openai"), session=session)
        self.assertEqual(out, VALID_JSON)

    def test_fenced_json_upper_json_label(self):
        fenced = "```JSON\n" + json.dumps(VALID_JSON) + "\n```"
        session = _mock_session(
            envelope={"choices": [{"message": {"content": fenced}}]}
        )
        out = generate_json("sys", "payload", SCHEMA, config=_config(provider="openai"), session=session)
        self.assertEqual(out, VALID_JSON)

    def test_config_none_loads_from_secrets(self):
        with mock.patch("api.ai_provider.load_aiconfig", return_value=_config()) as mk:
            session = _mock_session(
                envelope={"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]}
            )
            out = generate_json("sys", "payload", SCHEMA, session=session)
            mk.assert_called_once()
            self.assertEqual(out, VALID_JSON)

    def test_own_session_closed_when_none_injected(self):
        fake_session = mock.MagicMock()
        fake_session.post.return_value = _make_response(
            envelope={"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]}
        )
        with mock.patch("api.ai_provider._session", return_value=fake_session):
            with mock.patch("api.ai_provider.load_aiconfig", return_value=_config()):
                out = generate_json("sys", "payload", SCHEMA)
        self.assertEqual(out, VALID_JSON)
        fake_session.post.assert_called_once()
        fake_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# Provider failure normalization
# ---------------------------------------------------------------------------


class TestProviderFailures(unittest.TestCase):
    def test_http_failures_are_normalized_for_every_provider(self):
        for provider in PROVIDERS:
            for status in (401, 429, 500):
                with self.subTest(provider=provider, status=status):
                    session = _mock_session(status=status, text=f"failure {SECRET}")
                    with self.assertRaises(AIConfigError) as ctx:
                        generate_json(
                            "sys", "p", SCHEMA, config=_config(provider=provider), session=session
                        )
                    self.assertNotIn(SECRET, "".join(traceback.format_exception(ctx.exception)))

    def test_auth_401(self):
        for status in (401, 403):
            with self.subTest(status=status):
                session = _mock_session(status=status, text="unauthorized")
                with self.assertRaises(AIConfigError) as ctx:
                    generate_json("sys", "p", SCHEMA, config=_config(), session=session)
                self.assertNotIn(SECRET, str(ctx.exception))

    def test_rate_limit_429(self):
        session = _mock_session(status=429, text="rate limited")
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertIn("429", str(ctx.exception))

    @mock.patch("api.ai_provider.time.sleep")
    def test_rate_limit_retries_once_using_retry_after(self, sleep):
        limited = _make_response(status=429, text="rate limited")
        limited.headers = {"Retry-After": "2"}
        success = _make_response(
            envelope={"choices": [{"message": {"content": json.dumps(VALID_JSON)}}]}
        )
        session = mock.MagicMock()
        session.post.side_effect = [limited, success]

        result = generate_json("sys", "p", SCHEMA, config=_config(), session=session)

        self.assertEqual(result, VALID_JSON)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(session.post.call_count, 2)

    def test_server_error_500(self):
        session = _mock_session(status=500, text="boom")
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertIn("500", str(ctx.exception))

    def test_timeout(self):
        session = _mock_session(exc=requests.Timeout())
        cfg = _config(timeout=3)
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=cfg, session=session)
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_connection_error(self):
        session = _mock_session(exc=requests.ConnectionError("boom: secret"))
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIn("request failed", str(ctx.exception))

    def test_request_exception_traceback_does_not_retain_secret(self):
        session = _mock_session(
            exc=requests.ConnectionError(f"failed https://example.test/?key={SECRET}")
        )
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(provider="gemini"), session=session)
        rendered = "".join(traceback.format_exception(ctx.exception))
        self.assertNotIn(SECRET, rendered)
        self.assertIsNone(ctx.exception.__cause__)

    def test_error_detail_is_redacted_and_bounded(self):
        body = (
            "Authorization: Bearer provider-echo-token\n"
            "x-goog-api-key: another-secret\n"
            f"request failed?key={SECRET}\n"
            + "x" * 12000
        )
        session = _mock_session(status=400, text=body)
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        message = str(ctx.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("provider-echo-token", message)
        self.assertNotIn("another-secret", message)
        self.assertIn("[truncated]", message)
        self.assertLess(len(message), 550)

    def test_invalid_call_inputs_fail_before_network(self):
        bad_calls = (
            ("", "payload", SCHEMA, None),
            ("sys", None, SCHEMA, None),
            ("sys", "payload", [], None),
            ("sys", "payload", {"type": "bogus"}, None),
            ("sys", "payload", SCHEMA, 0),
            ("sys", "payload", SCHEMA, True),
        )
        for system, payload, schema, timeout in bad_calls:
            with self.subTest(system=system, payload=payload, schema=schema, timeout=timeout):
                session = _mock_session(envelope={})
                with self.assertRaises(AIConfigError):
                    generate_json(
                        system, payload, schema, config=_config(), session=session, timeout=timeout
                    )
                session.post.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed / unexpected output
# ---------------------------------------------------------------------------


class TestMalformedOutput(unittest.TestCase):
    def test_non_json_body(self):
        session = _mock_session(status=200, text="not json at all")
        session.post.return_value.json.side_effect = ValueError("bad")
        with self.assertRaises(AIValidationError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertIn("non-JSON", str(ctx.exception))

    def test_content_not_valid_json(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": "this is not json"}}]}
        )
        with self.assertRaises(AIValidationError):
            generate_json("sys", "p", SCHEMA, config=_config(provider="openai"), session=session)

    def test_content_json_is_not_object(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": "[1,2,3]"}}]}
        )
        with self.assertRaises(AIValidationError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(provider="openai"), session=session)
        self.assertIn("not an object", str(ctx.exception))

    def test_missing_required_field(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": '{"title": "x"}'}}]}
        )
        with self.assertRaises(AIValidationError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(provider="openai"), session=session)
        self.assertIn("flow", str(ctx.exception))

    def test_wrong_type_field(self):
        bad_schema = {"required": [], "properties": {"n": {"type": "string"}}}
        session = _mock_session(
            envelope={"choices": [{"message": {"content": '{"n": 42}'}}]}
        )
        with self.assertRaises(AIValidationError):
            generate_json("sys", "p", bad_schema, config=_config(provider="openai"), session=session)

    def test_deep_schema_types_enums_and_additional_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "flow": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "module": {"type": "string", "enum": ["get_metadata"]},
                            "enabled": {"type": "boolean"},
                        },
                        "required": ["module", "enabled"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["flow"],
            "additionalProperties": False,
        }
        invalid_values = (
            {"flow": [{"module": 42, "enabled": True}]},
            {"flow": [{"module": "other", "enabled": True}]},
            {"flow": [{"module": "get_metadata", "enabled": "yes"}]},
            {"flow": [{"module": "get_metadata", "enabled": True, "extra": 1}]},
            {"flow": []},
            {"flow": [{"module": "get_metadata", "enabled": True}], "extra": 1},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                session = _mock_session(
                    envelope={"choices": [{"message": {"content": json.dumps(value)}}]}
                )
                with self.assertRaises(AIValidationError):
                    generate_json("sys", "p", schema, config=_config(), session=session)

    def test_required_nullable_field_accepts_null(self):
        schema = {
            "type": "object",
            "properties": {"author": {"type": ["string", "null"]}},
            "required": ["author"],
        }
        session = _mock_session(
            envelope={"choices": [{"message": {"content": '{"author": null}'}}]}
        )
        self.assertEqual(
            generate_json("sys", "p", schema, config=_config(), session=session),
            {"author": None},
        )

    def test_properties_are_checked_without_required_keyword(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        session = _mock_session(
            envelope={"choices": [{"message": {"content": '{"count": true}'}}]}
        )
        with self.assertRaises(AIValidationError):
            generate_json("sys", "p", schema, config=_config(), session=session)

    def test_unexpected_shape_provider_error(self):
        session = _mock_session(envelope={})
        # empty dict is valid json but lacks choices -> ProviderError -> AIValidationError
        with self.assertRaises(AIValidationError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(provider="openai"), session=session)
        self.assertIn("unexpected shape", str(ctx.exception))

    def test_no_schema_skips_field_validation(self):
        session = _mock_session(
            envelope={"choices": [{"message": {"content": '{"n": 42}'}}]}
        )
        out = generate_json("sys", "p", None, config=_config(provider="openai"), session=session)
        self.assertEqual(out, {"n": 42})


# ---------------------------------------------------------------------------
# Secret safety
# ---------------------------------------------------------------------------


class TestSecretSafety(unittest.TestCase):
    def test_key_never_in_success_errors(self):
        variants = [
            (401, "unauthorized", None),
            (429, "rate limited", None),
            (500, "server boom", None),
            (200, "", {"choices": [{"message": {"content": "garbage"}}]}),
            (200, "bad body", None),
        ]
        for status, text, envelope in variants:
            session = _mock_session(status=status, text=text, envelope=envelope)
            if status == 200 and envelope is None:
                session.post.return_value.json.side_effect = ValueError("bad")
            with self.subTest((status, text)):
                with self.assertRaises((AIConfigError, AIValidationError)) as ctx:
                    generate_json("sys", "p", SCHEMA, config=_config(), session=session)
                self.assertNotIn(SECRET, str(ctx.exception))

    def test_auth_header_contains_key_but_error_does_not(self):
        session = _mock_session(status=401, text="nope")
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertIn(SECRET, session.post.call_args.kwargs["headers"]["Authorization"])
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_connection_error_message_scrubbed(self):
        session = _mock_session(exc=requests.ConnectionError(f"refused {SECRET}"))
        with self.assertRaises(AIConfigError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_non_json_body_error_scrubbed(self):
        body_with_secret = f"html error containing {SECRET}"
        session = _mock_session(status=200, text=body_with_secret)
        session.post.return_value.json.side_effect = ValueError("bad")
        with self.assertRaises(AIValidationError) as ctx:
            generate_json("sys", "p", SCHEMA, config=_config(), session=session)
        self.assertNotIn(SECRET, str(ctx.exception))


# ---------------------------------------------------------------------------
# No SDK / no .env guarantees
# ---------------------------------------------------------------------------


class TestNoSdkNoEnv(unittest.TestCase):
    def test_ai_modules_import_no_sdk(self):
        import api.ai_provider
        import api.ai_config

        for mod in (api.ai_provider, api.ai_config):
            source = mod.__file__ or ""
            with open(source, encoding="utf-8") as f:
                text = f.read()
            for sdk in ("import openai", "import anthropic", "import google.generativeai", "from openai"):
                self.assertNotIn(sdk, text)

    def test_config_module_mentions_no_env_creation(self):
        with open(api_config_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertIn(".env", text)  # documents that we deliberately avoid it
        self.assertNotIn("from dotenv", text)


def api_config_path():
    import api.ai_config as m

    return m.__file__ or ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
