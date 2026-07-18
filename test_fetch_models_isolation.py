import os
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import API
from core import db, providers, testing
from core.validators import validate_api_format, validate_model


class _FakeResponse:
    def __init__(self, payload, status=200, raw=False):
        self.status = 200
        self.status = status
        self._payload = payload
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if self._raw:
            return str(self._payload).encode("utf-8")
        return json.dumps(self._payload).encode("utf-8")


class FetchModelsIsolationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_appdata = os.environ.get("APPDATA")
        self._old_userprofile = os.environ.get("USERPROFILE")
        self._old_home = os.environ.get("HOME")
        os.environ["APPDATA"] = os.path.join(self._tmp, "appdata")
        os.environ["USERPROFILE"] = self._tmp
        os.environ["HOME"] = self._tmp
        db.init_db()

    def tearDown(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata
        if self._old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self._old_userprofile
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _create_ccswitch_db(self):
        cc_dir = os.path.join(self._tmp, ".cc-switch")
        os.makedirs(cc_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(cc_dir, "cc-switch.db"))
        conn.execute("CREATE TABLE providers (settings_config TEXT)")
        conn.execute(
            "INSERT INTO providers (settings_config) VALUES (?)",
            (
                '{"env":{"ANTHROPIC_BASE_URL":"https://same.example/v1",'
                '"ANTHROPIC_MODEL":"model-from-ccswitch",'
                '"ANTHROPIC_DEFAULT_SONNET_MODEL":"alias-from-ccswitch",'
                '"ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME":"name-alias-from-ccswitch"}}',
            ),
        )
        conn.commit()
        conn.close()

    def test_fetch_models_uses_only_monitor_owned_model_hints(self):
        db.add_provider(
            {
                "name": "Own Provider",
                "endpoint": "https://same.example/v1",
                "api_key": "sk-own",
                "default_model": "model-from-monitor",
                "api_format": "anthropic_messages",
            }
        )
        self._create_ccswitch_db()

        seen = {}

        def fake_fetch_models(endpoint, api_key, api_format="", default_model=""):
            seen["api_key"] = api_key
            return {
                "success": True,
                "models": [m.strip() for m in default_model.split(",") if m.strip()] or _get_known_models_by_format(api_format),
            }

        api = object.__new__(API)
        with patch.object(testing, "fetch_models", side_effect=fake_fetch_models):
            result = api.fetch_models("https://same.example/v1", "")

        self.assertTrue(result["success"])
        # main.py 已改为只传本应用 DB 的模型提示和 API Key，不再拼合同域名 cc-switch 模型
        self.assertEqual(seen["api_key"], "sk-own")
        self.assertEqual(
            result["models"],
            [
                "model-from-monitor",
            ],
        )

    def test_fetch_models_prefers_manually_selected_api_format(self):
        db.add_provider(
            {
                "name": "Own Provider",
                "endpoint": "https://same.example/v1",
                "api_key": "sk-own",
                "default_model": "",
                "api_format": "anthropic_messages",
            }
        )
        seen = {}

        def fake_fetch_models(endpoint, api_key, api_format="", default_model=""):
            seen["api_format"] = api_format
            return {"success": True, "models": ["ok"]}

        api = object.__new__(API)
        with patch.object(testing, "fetch_models", side_effect=fake_fetch_models):
            result = api.fetch_models("https://same.example/v1", "", "", "openai_chat")

        self.assertTrue(result["success"])
        self.assertEqual(seen["api_format"], "openai_chat")

    def test_chat_does_not_fall_back_to_ccswitch_anthropic_aliases(self):
        self._create_ccswitch_db()
        seen_models = []

        def fake_urlopen(req, timeout=None, context=None):
            body = json.loads(req.data.decode("utf-8"))
            model = body.get("model")
            if model:
                seen_models.append(model)
            if model == "model-from-monitor":
                raise urllib.error.HTTPError(
                    req.full_url,
                    500,
                    "server error",
                    hdrs=None,
                    fp=io.BytesIO(b""),
                )
            if model == "model-from-ccswitch":
                return _FakeResponse(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "Hello from cc-switch model",
                            }
                        ]
                    }
                )
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://same.example/v1",
                "sk-own",
                "model-from-monitor",
                "claude",
            )

        self.assertFalse(result["success"])
        self.assertIn("model-from-monitor", seen_models)
        self.assertNotIn("model-from-ccswitch", seen_models)

    def test_set_current_provider_writes_claude_base_url_without_trailing_v1(self):
        provider_id = db.add_provider(
            {
                "name": "Zening",
                "endpoint": "http://proxy.example/v1",
                "api_key": "sk-zening",
                "default_model": "gpt-5.5",
                "api_format": "anthropic_messages",
            }
        )

        result = providers.set_current_provider(provider_id)

        self.assertTrue(result["success"])
        settings_path = os.path.join(self._tmp, ".claude", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "http://proxy.example")
        self.assertEqual(settings["env"]["ANTHROPIC_MODEL"], "gpt-5.5")


class FetchModelsCompletenessTest(unittest.TestCase):
    def test_fetch_models_strips_anthropic_suffix_before_falling_back_to_default_model(self):
        def fake_urlopen(req, timeout=None, context=None):
            if req.full_url == "https://proxy.example/v1/models":
                return _FakeResponse({"data": [{"id": "api-a"}, {"id": "api-b"}]})
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://proxy.example/anthropic",
                "",
                default_model="local-only",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["models"], ["api-a", "api-b"])

    def test_fetch_models_returns_remote_models_without_default_model_pollution(self):
        responses = {
            "https://proxy.example/v1/models": {"data": [{"id": "api-a"}]},
            "https://proxy.example/models": {"models": ["api-b"]},
            "https://proxy.example/api/models": {"data": []},
            "https://proxy.example/openai/v1/models": {
                "data": [{"id": "api-b"}, {"id": "api-c"}]
            },
        }

        def fake_urlopen(req, timeout=None, context=None):
            return _FakeResponse(responses[req.full_url])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://proxy.example",
                "",
                default_model="local-extra,api-a",
            )

        self.assertTrue(result["success"])
        self.assertEqual(
            result["models"],
            ["api-a", "api-b", "api-c"],
        )

    def test_fetch_models_uses_default_model_only_when_no_models_endpoint_exists(self):
        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://proxy.example",
                "",
                default_model="local-a, local-b",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["models"], ["local-a", "local-b"])

    def test_fetch_models_does_not_show_claude_fallback_for_third_party_endpoint(self):
        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://longcat.chat/api/v1",
                "sk-longcat",
                api_format="anthropic_messages",
            )

        self.assertFalse(result["success"])
        self.assertTrue(result["no_models_endpoint"])

    def test_fetch_models_uses_known_fallback_only_for_first_party_anthropic(self):
        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://api.anthropic.com/v1",
                "sk-ant",
                api_format="anthropic_messages",
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["from_fallback"])
        self.assertIn("claude-opus-4-8", result["models"])

    def test_fetch_models_sends_anthropic_models_headers(self):
        seen = []

        def fake_urlopen(req, timeout=None, context=None):
            seen.append(dict(req.header_items()))
            headers = {k.lower(): v for k, v in req.header_items()}
            if headers.get("x-api-key") == "sk-test" and headers.get("anthropic-version") == "2023-06-01":
                return _FakeResponse({"data": [{"id": "claude-opus-4-8"}]})
            raise urllib.error.HTTPError(
                req.full_url,
                403,
                "forbidden",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://api.anthropic.com/v1",
                "sk-test",
                api_format="anthropic_messages",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["models"], ["claude-opus-4-8"])
        self.assertTrue(
            any(
                {k.lower(): v for k, v in headers.items()}.get("anthropic-version") == "2023-06-01"
                for headers in seen
            )
        )

    def test_fetch_models_normalizes_gemini_native_models(self):
        def fake_urlopen(req, timeout=None, context=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            self.assertEqual(headers.get("x-goog-api-key"), "gemini-key")
            self.assertIn("key=gemini-key", req.full_url)
            return _FakeResponse(
                {
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/text-embedding-004",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                }
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models(
                "https://generativelanguage.googleapis.com/v1beta",
                "gemini-key",
                api_format="gemini_native",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["models"], ["gemini-2.5-pro"])

    def test_anthropic_fallback_models_are_current(self):
        models = testing._get_known_models_by_format("anthropic_messages")
        self.assertIn("claude-opus-4-8", models)
        self.assertIn("claude-sonnet-4-6", models)
        self.assertIn("claude-haiku-4-5", models)
        self.assertNotIn("claude-3-7-sonnet-20250219", models)
        self.assertNotIn("claude-3-opus-20240229", models)


class ValidatorTest(unittest.TestCase):
    def test_validate_model_accepts_comma_separated_candidates(self):
        ok, err = validate_model("claude-opus-4-8, claude-sonnet-4-6, gemini-2.5-pro")
        self.assertTrue(ok, err)

    def test_validate_model_accepts_vertex_style_snapshot(self):
        ok, err = validate_model("claude-haiku-4-5@20251001")
        self.assertTrue(ok, err)

    def test_validate_model_rejects_dangerous_characters(self):
        ok, _ = validate_model("model; rm -rf")
        self.assertFalse(ok)

    def test_validate_model_rejects_overly_long_lists(self):
        ok, _ = validate_model("m" * 501)
        self.assertFalse(ok)

    def test_validate_api_format_accepts_known_values_and_auto(self):
        for value in ("", "anthropic_messages", "openai_chat", "openai_responses", "gemini_native"):
            ok, err = validate_api_format(value)
            self.assertTrue(ok, err)

    def test_validate_api_format_rejects_unknown_value(self):
        ok, _ = validate_api_format("openai")
        self.assertFalse(ok)

    def test_fetch_models_continues_after_html_models_response(self):
        responses = {
            "https://html.example/models": _FakeResponse("<!doctype html><html></html>", raw=True),
            "https://html.example/v1/models": _FakeResponse({"data": [{"id": "real-model"}]}),
        }

        def fake_urlopen(req, timeout=None, context=None):
            if req.full_url in responses:
                return responses[req.full_url]
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing.fetch_models("https://html.example", "")

        self.assertTrue(result["success"])
        self.assertEqual(result["models"], ["real-model"])


class ChatEndpointTest(unittest.TestCase):
    def test_anthropic_chat_prefers_v1_messages_over_html_root_messages(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["max_tokens"], 32)
            if req.full_url == "https://proxy.example/messages":
                return _FakeResponse("<!doctype html><html></html>", raw=True)
            if req.full_url == "https://proxy.example/v1/messages":
                return _FakeResponse(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": "Hello from the model",
                            }
                        ]
                    }
                )
            self.fail(f"unexpected URL {req.full_url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://proxy.example",
                "sk-test",
                "model-a",
                "claude",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["response_snippet"], "Hello from the model")
        self.assertEqual(
            calls,
            [
                "https://proxy.example/v1/messages",
            ],
        )

    def test_deepseek_chat_uses_deepseek_default_model_when_empty(self):
        seen_models = []

        def fake_urlopen(req, timeout=None, context=None):
            body = json.loads(req.data.decode("utf-8"))
            seen_models.append(body.get("model"))
            if req.full_url == "https://api.deepseek.com/v1/chat/completions" and body.get("model") == "deepseek-chat":
                return _FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Hello from DeepSeek",
                                }
                            }
                        ]
                    }
                )
            raise urllib.error.HTTPError(
                req.full_url,
                400,
                "bad model",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://api.deepseek.com/v1",
                "sk-test",
                "",
                "claude",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["api_format"], "openai_chat")
        self.assertIn("deepseek-chat", seen_models)

    def test_deepseek_chat_strips_anthropic_suffix_before_openai_chat(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            body = json.loads(req.data.decode("utf-8"))
            if req.full_url == "https://api.deepseek.com/v1/chat/completions" and body.get("model") == "deepseek-chat":
                return _FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Hello from DeepSeek",
                                }
                            }
                        ]
                    }
                )
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://api.deepseek.com/anthropic",
                "sk-test",
                "",
                "claude",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["api_format"], "openai_chat")
        self.assertIn("https://api.deepseek.com/v1/chat/completions", calls)

    def test_deepseek_anthropic_thinking_content_counts_as_reply(self):
        def fake_urlopen(req, timeout=None, context=None):
            return _FakeResponse(
                {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "We",
                            "signature": "sig",
                        }
                    ]
                }
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://api.deepseek.com/anthropic",
                "sk-test",
                "deepseek-v4-pro",
                "claude",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["api_format"], "anthropic_messages")
        self.assertEqual(result["response_snippet"], "We")

    def test_deepseek_openai_reasoning_content_counts_as_reply(self):
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "好的",
                    }
                }
            ]
        }

        self.assertEqual(testing._extract_response_snippet(json.dumps(body), "auto"), "好的")

    def test_manual_openai_chat_format_is_tried_before_anthropic(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            if req.full_url == "https://proxy.example/v1/chat/completions":
                return _FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Hello from OpenAI-compatible API",
                                }
                            }
                        ]
                    }
                )
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "not found",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = testing._test_chat_endpoint(
                "https://proxy.example/v1",
                "sk-test",
                "gpt-4o-mini",
                "claude",
                "openai_chat",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["api_format"], "openai_chat")
        self.assertEqual(calls[0], "https://proxy.example/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
