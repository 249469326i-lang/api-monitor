import os
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 独立数据目录，避免污染真实配置
_MODULE_TMP = tempfile.mkdtemp(prefix="api_monitor_tokens_")
os.environ["API_MONITOR_DATA_DIR"] = _MODULE_TMP

from core import db, testing

db.init_db()


def _success_body():
    """同时覆盖 openai_chat / openai_responses / gemini 三种解析路径的回复体。"""
    return json.dumps(
        {
            "choices": [{"message": {"content": "Hello"}}],
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}],
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
        }
    )


class DeepSeekEndpointTest(unittest.TestCase):
    """统一 DeepSeek 端点判断（db.py，测试引擎与入库归一共用）。"""

    def test_is_deepseek_endpoint(self):
        self.assertTrue(db.is_deepseek_endpoint("https://api.deepseek.com"))
        self.assertTrue(db.is_deepseek_endpoint("https://api.deepseek.com/v1"))
        self.assertTrue(db.is_deepseek_endpoint("https://relay.deepseek.com/v1"))
        self.assertTrue(db.is_deepseek_endpoint("https://api.deepseek.com:443/v1"))
        self.assertFalse(db.is_deepseek_endpoint("https://api.openai.com/v1"))
        self.assertFalse(db.is_deepseek_endpoint("https://deepseek.example.com/v1"))
        self.assertFalse(db.is_deepseek_endpoint(""))
        self.assertFalse(db.is_deepseek_endpoint(None))


class NormalizeContextLengthTest(unittest.TestCase):
    """入库时 context_length 越界归一（core/db.py）。"""

    def test_deepseek_out_of_range_capped(self):
        self.assertEqual(db._normalize_context_length(1000000, "https://api.deepseek.com"), 393216)

    def test_deepseek_in_range_preserved(self):
        self.assertEqual(db._normalize_context_length(200000, "https://api.deepseek.com"), 200000)

    def test_deepseek_any_subdomain_capped_same(self):
        # 保存端与发送端判断必须一致：任何 *.deepseek.com 都按 393216 钳制，
        # 防止「保存时 393216、发请求时又被压到 128000」的错位。
        self.assertEqual(db._normalize_context_length(393216, "https://relay.deepseek.com/v1"), 393216)
        self.assertEqual(db._normalize_context_length(1000000, "https://deepseek.com/v1"), 393216)

    def test_non_deepseek_capped_to_generic(self):
        self.assertEqual(db._normalize_context_length(1000000, "https://api.openai.com/v1"), 128000)
        self.assertEqual(db._normalize_context_length(65536, "https://api.openai.com/v1"), 65536)

    def test_invalid_values_return_zero(self):
        for bad in (0, -5, None, "abc", "", [], {}):
            self.assertEqual(db._normalize_context_length(bad, "https://api.deepseek.com"), 0)


class TestChatEndpointClampTest(unittest.TestCase):
    """定向测试请求发出的 max_tokens/max_output_tokens 必须被钳制（core/testing.py）。"""

    def _run(self, endpoint, api_format, context_length=1000000):
        captured = {}

        def fake_send(url, headers, body):
            captured["body"] = body
            return {"success": True, "body": _success_body()}

        with patch("core.testing._send_post_request", side_effect=fake_send):
            result = testing._test_chat_endpoint(
                endpoint,
                "sk-test",
                "deepseek-v4-flash" if "deepseek.com" in endpoint else "gpt-4o-mini",
                "codex",
                api_format,
                context_length=context_length,
            )
        return result, captured

    def test_openai_chat_deepseek_clamped(self):
        result, captured = self._run("https://api.deepseek.com", "openai_chat")
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["max_tokens"], 393216)

    def test_openai_chat_non_deepseek_clamped(self):
        result, captured = self._run("https://proxy.example/v1", "openai_chat")
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["max_tokens"], 128000)

    def test_openai_responses_deepseek_clamped(self):
        result, captured = self._run("https://api.deepseek.com", "openai_responses")
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["max_output_tokens"], 393216)

    def test_openai_responses_non_deepseek_clamped(self):
        result, captured = self._run("https://proxy.example/v1", "openai_responses")
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["max_output_tokens"], 128000)

    def test_gemini_deepseek_clamped(self):
        result, captured = self._run("https://api.deepseek.com", "gemini_native")
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["generationConfig"]["maxOutputTokens"], 393216)

    def test_in_range_preserved(self):
        result, captured = self._run("https://api.deepseek.com", "openai_chat", context_length=200000)
        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["max_tokens"], 200000)

    def test_zero_or_garbage_uses_default(self):
        for bad in (0, None, "1M", "abc"):
            result, captured = self._run("https://proxy.example/v1", "openai_chat", context_length=bad)
            self.assertTrue(result["success"])
            self.assertEqual(captured["body"]["max_tokens"], 256)


if __name__ == "__main__":
    unittest.main()
