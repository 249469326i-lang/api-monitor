import os
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 模块级隔离:部分测试类(ChatEndpointTest 等)不换 APPDATA,直接读设置表。
# 在干净机器(CI)上没有已初始化的数据库会炸,统一重定向到临时目录并建表。
_MODULE_TMP = tempfile.mkdtemp(prefix="api_monitor_tests_")
os.environ["API_MONITOR_DATA_DIR"] = _MODULE_TMP

from main import API
from core import db, providers, testing
from core.validators import validate_api_format, validate_model

db.init_db()


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
        self._old_data_dir = os.environ.get("API_MONITOR_DATA_DIR")
        os.environ["APPDATA"] = os.path.join(self._tmp, "appdata")
        os.environ["USERPROFILE"] = self._tmp
        os.environ["HOME"] = self._tmp
        # 数据目录走 API_MONITOR_DATA_DIR（core.paths 的隔离入口），
        # 每个测试一个全新目录，保证库是干净的
        os.environ["API_MONITOR_DATA_DIR"] = os.path.join(self._tmp, "data")
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
        if self._old_data_dir is None:
            os.environ.pop("API_MONITOR_DATA_DIR", None)
        else:
            os.environ["API_MONITOR_DATA_DIR"] = self._old_data_dir
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


class CodexConfigTest(unittest.TestCase):
    """Codex 应用支持: 设当前 / 同步 / 按 app_type 独立「当前」标记。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_appdata = os.environ.get("APPDATA")
        self._old_userprofile = os.environ.get("USERPROFILE")
        self._old_home = os.environ.get("HOME")
        self._old_data_dir = os.environ.get("API_MONITOR_DATA_DIR")
        os.environ["APPDATA"] = os.path.join(self._tmp, "appdata")
        os.environ["USERPROFILE"] = self._tmp
        os.environ["HOME"] = self._tmp
        os.environ["API_MONITOR_DATA_DIR"] = os.path.join(self._tmp, "data")
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
        if self._old_data_dir is None:
            os.environ.pop("API_MONITOR_DATA_DIR", None)
        else:
            os.environ["API_MONITOR_DATA_DIR"] = self._old_data_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _codex_config_path(self):
        return os.path.join(self._tmp, ".codex", "config.toml")

    def _seed_codex_config(self, base_url="https://api.picpi.top", model="gpt-5.6-sol"):
        cfg = (
            "# codex config\n"
            'model_provider = "custom"\n'
            f'model = "{model}"\n'
            "\n"
            "[model_providers.custom]\n"
            'name = "旧供应商"\n'
            f'base_url = "{base_url}"\n'
            'wire_api = "responses"\n'
            "requires_openai_auth = true\n"
            "\n"
            "[mcp_servers.foo]\n"
            'command = "echo hi"\n'
            "\n"
            "[desktop]\n"
            'theme = "light"\n'
        )
        path = self._codex_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg)
        return path

    def test_set_codex_current_writes_config_toml(self):
        pid = db.add_provider(
            {
                "name": "Picpi",
                "app_type": "codex",
                "endpoint": "https://api.picpi.top",
                "api_key": "sk-test-123",
                "default_model": "gpt-5.6-sol",
                "api_format": "openai_responses",
            }
        )
        result = providers.set_current_provider(pid)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(self._codex_config_path()))
        with open(self._codex_config_path(), "r", encoding="utf-8") as f:
            parsed = tomllib.loads(f.read())
        self.assertEqual(parsed["model_provider"], "api_monitor")
        self.assertEqual(parsed["model"], "gpt-5.6-sol")
        section = parsed["model_providers"]["api_monitor"]
        self.assertEqual(section["base_url"], "https://api.picpi.top")
        self.assertNotIn("env_key", section)
        self.assertEqual(section["experimental_bearer_token"], "sk-test-123")
        self.assertEqual(section["wire_api"], "responses")
        self.assertIs(section["requires_openai_auth"], False)
        self.assertEqual(section["name"], "Picpi")
        # 供应商被标为当前
        self.assertEqual(db.get_provider_by_id(pid)["role"], "当前")

    def test_set_codex_current_preserves_unrelated_content(self):
        self._seed_codex_config()
        pid = db.add_provider(
            {
                "name": "Picpi",
                "app_type": "codex",
                "endpoint": "https://api.picpi.top",
                "default_model": "gpt-5.6-sol",
            }
        )
        providers.set_current_provider(pid)
        with open(self._codex_config_path(), "r", encoding="utf-8") as f:
            cfg = f.read()
        # 注释、mcp_servers、desktop 与旧 provider 表都保留
        self.assertIn("# codex config", cfg)
        self.assertIn("[mcp_servers.foo]", cfg)
        self.assertIn('command = "echo hi"', cfg)
        self.assertIn('theme = "light"', cfg)
        self.assertIn("model_providers.custom", cfg)
        # 新表已写入
        self.assertIn("model_providers.api_monitor", cfg)

    def test_sync_codex_creates_provider(self):
        self._seed_codex_config(base_url="https://api.codexproxy.example/v1", model="gpt-5")
        result = providers.sync_codex_provider(force=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "created")
        p = db.get_provider_by_id(result["provider_id"])
        self.assertEqual(p["app_type"], "codex")
        self.assertEqual(p["endpoint"], "https://api.codexproxy.example/v1")
        self.assertEqual(p["default_model"], "gpt-5")
        self.assertEqual(p["api_format"], "openai_responses")
        self.assertEqual(p["role"], "当前")

    def test_sync_codex_matches_existing_and_marks_current(self):
        self._seed_codex_config()
        pid = db.add_provider(
            {
                "name": "已有",
                "app_type": "codex",
                "endpoint": "https://api.picpi.top",
                "default_model": "gpt-5.6-sol",
                "role": "备用",
            }
        )
        result = providers.sync_codex_provider(force=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "exists")
        self.assertEqual(result["provider_id"], pid)
        self.assertEqual(db.get_provider_by_id(pid)["role"], "当前")

    def test_sync_codex_missing_config_graceful(self):
        result = providers.sync_codex_provider(force=True)
        self.assertFalse(result["success"])
        self.assertEqual(result["action"], "error")

    def test_claude_and_codex_current_coexist(self):
        claude_id = db.add_provider(
            {
                "name": "Claude A",
                "app_type": "claude",
                "endpoint": "https://claude.example",
                "api_key": "sk-c",
            }
        )
        self.assertTrue(providers.set_current_provider(claude_id)["success"])

        codex_id = db.add_provider(
            {
                "name": "Codex B",
                "app_type": "codex",
                "endpoint": "https://codex.example",
                "api_key": "sk-x",
            }
        )
        self.assertTrue(providers.set_current_provider(codex_id)["success"])

        self.assertEqual(db.get_provider_by_id(claude_id)["role"], "当前")
        self.assertEqual(db.get_provider_by_id(codex_id)["role"], "当前")

        # 同步 Claude settings 不应动 codex 的当前标记
        providers.sync_current_from_settings()
        self.assertEqual(db.get_provider_by_id(codex_id)["role"], "当前")
        self.assertEqual(db.get_provider_by_id(claude_id)["role"], "当前")

    def test_dual_app_provider_independent_config(self):
        """同一供应商绑定 claude+codex，两端独立端点/模型/角色互不干扰。"""
        pid = db.add_provider(
            {
                "name": "DeepSeek",
                "apps": [
                    {"app_type": "claude", "endpoint": "https://api.deepseek.com/anthropic", "default_model": "deepseek-chat", "api_format": "anthropic_messages"},
                    {"app_type": "codex", "endpoint": "https://api.deepseek.com/v1", "default_model": "deepseek-v4-flash", "api_format": "openai_responses"},
                ],
                "api_key": "sk-dual",
            }
        )
        p = db.get_provider_by_id(pid)
        self.assertEqual(p["app_type"], "both")

        # 双端各自设为当前
        self.assertTrue(providers.set_current_provider(pid, "claude")["success"])
        self.assertTrue(providers.set_current_provider(pid, "codex")["success"])

        # settings.json 用 claude 端点
        settings_path = os.path.join(self._tmp, ".claude", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(settings["env"]["ANTHROPIC_MODEL"], "deepseek-chat")

        # config.toml 用 codex 端点
        with open(self._codex_config_path(), "r", encoding="utf-8") as f:
            cfg = tomllib.loads(f.read())
        self.assertEqual(cfg["model_provider"], "api_monitor")
        self.assertEqual(cfg["model"], "deepseek-v4-flash")
        self.assertEqual(cfg["model_providers"]["api_monitor"]["base_url"], "https://api.deepseek.com/v1")

        # 两应用绑定各自 role=当前，互不干扰
        p2 = db.get_provider_by_id(pid)
        claude_b = next(b for b in p2["apps"] if b["app_type"] == "claude")
        codex_b = next(b for b in p2["apps"] if b["app_type"] == "codex")
        self.assertEqual(claude_b["role"], "当前")
        self.assertEqual(codex_b["role"], "当前")

        # 当前模式标记
        modes = providers.get_current_modes()
        self.assertEqual(modes["claude"]["mode"], "provider")
        self.assertEqual(modes["claude"]["provider_name"], "DeepSeek")
        self.assertEqual(modes["codex"]["mode"], "provider")
        self.assertEqual(modes["codex"]["provider_name"], "DeepSeek")

    def test_set_current_official_removes_config(self):
        """官方模式: codex 删顶层 model_provider/model，claude 删 ANTHROPIC_* 覆盖。"""
        pid = db.add_provider(
            {
                "name": "Picpi",
                "apps": [
                    {"app_type": "claude", "endpoint": "https://api.picpi.top/anthropic", "default_model": "deepseek-chat"},
                    {"app_type": "codex", "endpoint": "https://api.picpi.top/v1", "default_model": "deepseek-v4-flash"},
                ],
                "api_key": "sk-test-123",
            }
        )
        self.assertTrue(providers.set_current_provider(pid, "claude")["success"])
        self.assertTrue(providers.set_current_provider(pid, "codex")["success"])

        # codex 切官方: 删顶层 model_provider / model / model_catalog_json，
        # 并删除整个 [model_providers.api_monitor] 第三方表，其它内容保留
        self.assertTrue(providers.set_current_official("codex")["success"])
        with open(self._codex_config_path(), "r", encoding="utf-8") as f:
            cfg_text = f.read()
        parsed = tomllib.loads(cfg_text)
        self.assertNotIn("model_provider", parsed)
        self.assertNotIn("model", parsed)
        self.assertNotIn("model_catalog_json", parsed)
        self.assertNotIn("model_providers.api_monitor", cfg_text)  # 第三方供应商表已删除
        p = db.get_provider_by_id(pid)
        codex_b = next(b for b in p["apps"] if b["app_type"] == "codex")
        self.assertEqual(codex_b["role"], "备用")
        self.assertEqual(providers.get_current_modes()["codex"]["mode"], "official")

        # claude 切官方: settings.json env 删除 ANTHROPIC_*
        self.assertTrue(providers.set_current_official("claude")["success"])
        settings_path = os.path.join(self._tmp, ".claude", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        env = settings.get("env", {})
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_MODEL", env)
        p2 = db.get_provider_by_id(pid)
        claude_b = next(b for b in p2["apps"] if b["app_type"] == "claude")
        self.assertEqual(claude_b["role"], "备用")
        self.assertEqual(providers.get_current_modes()["claude"]["mode"], "official")

    def test_current_modes_derives_from_binding_when_setting_missing(self):
        """模式设置缺失时，get_current_modes 按绑定角色推导。"""
        pid = db.add_provider({"name": "X", "app_type": "codex", "endpoint": "https://x.example/v1", "api_key": "sk-x"})
        self.assertTrue(providers.set_current_provider(pid, "codex")["success"])
        # 人为清掉模式设置 → 从绑定推导为 provider
        db.set_setting("codex_current_mode", "")
        modes = providers.get_current_modes()
        self.assertEqual(modes["codex"]["mode"], "provider")
        self.assertEqual(modes["codex"]["provider_name"], "X")
        # 没有当前绑定 → 推导为 official
        db.set_setting("codex_current_mode", "")
        providers.set_current_official("codex")
        modes = providers.get_current_modes()
        self.assertEqual(modes["codex"]["mode"], "official")
        self.assertIsNone(modes["codex"]["provider_name"])

    def test_sync_settings_unmatched_override_stays_provider(self):
        """settings.json 有第三方覆盖但库里无匹配供应商 → 保持第三方模式而非误判官方。"""
        settings_path = os.path.join(self._tmp, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump({"env": {"ANTHROPIC_BASE_URL": "https://unknown.example"}}, f)
        providers.sync_current_from_settings()
        modes = providers.get_current_modes()
        self.assertEqual(modes["claude"]["mode"], "provider")
        self.assertIsNone(modes["claude"]["provider_name"])

        # 无第三方覆盖 → 官方
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump({"env": {}}, f)
        providers.sync_current_from_settings()
        self.assertEqual(providers.get_current_modes()["claude"]["mode"], "official")


if __name__ == "__main__":
    unittest.main()
