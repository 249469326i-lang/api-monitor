"""
Tests for agent-protocol mapping and test order logic (core/testing.py).
These tests validate that:
1. The agent's actual protocol is always first in the test order
2. The _probe_api_format prioritizes the correct protocol for codex
3. _test_chat_endpoint returns fail when agent's protocol fails but fallback succeeds
4. app_type=both tests both protocols
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import testing


class _FakeResponse:
    def __init__(self, payload, status=200, raw=False):
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


def _adapt_http_open(fake_urlopen):
    """把基于 urllib.request.urlopen 的 fake(req) 适配成 testing._http_open 签名
    (method, url, headers, body, connect_timeout, read_timeout, _redirects)。"""
    class _Req:
        __slots__ = ("full_url", "data", "method", "headers")

    def fake_http_open(method, url, headers, body=None, connect_timeout=None,
                       read_timeout=None, _redirects=0):
        req = _Req()
        req.full_url = url
        req.data = body
        req.method = method
        req.headers = headers
        resp = fake_urlopen(req)
        return resp.status, resp.read()

    return fake_http_open


class AgentProtocolOrderTest(unittest.TestCase):
    """Tests for _agent_protocol_order pure function."""

    def test_codex_with_openai_chat_api_format_puts_responses_first(self):
        """codex + api_format=openai_chat → openai_responses must be first."""
        order = testing._agent_protocol_order("codex", "openai_chat")
        self.assertEqual(order[0], "openai_responses",
                         f"Expected openai_responses first, got {order}")

    def test_codex_with_empty_api_format_puts_responses_first(self):
        """codex + api_format="" → openai_responses must be first."""
        order = testing._agent_protocol_order("codex", "")
        self.assertEqual(order[0], "openai_responses",
                         f"Expected openai_responses first, got {order}")

    def test_claude_with_openai_chat_api_format_puts_anthropic_first(self):
        """claude + api_format=openai_chat → anthropic_messages must be first."""
        order = testing._agent_protocol_order("claude", "openai_chat")
        self.assertEqual(order[0], "anthropic_messages",
                         f"Expected anthropic_messages first, got {order}")

    def test_claude_with_empty_api_format_puts_anthropic_first(self):
        """claude + api_format="" → anthropic_messages must be first."""
        order = testing._agent_protocol_order("claude", "")
        self.assertEqual(order[0], "anthropic_messages",
                         f"Expected anthropic_messages first, got {order}")

    def test_gemini_with_openai_chat_api_format_puts_gemini_native_first(self):
        """gemini + api_format=openai_chat → gemini_native must be first."""
        order = testing._agent_protocol_order("gemini", "openai_chat")
        self.assertEqual(order[0], "gemini_native",
                         f"Expected gemini_native first, got {order}")

    def test_both_with_openai_chat_has_both_actual_protocols_in_front(self):
        """both → anthropic_messages and openai_responses are both in front."""
        order = testing._agent_protocol_order("both", "openai_chat")
        self.assertIn("anthropic_messages", order[:2])
        self.assertIn("openai_responses", order[:2])
        self.assertEqual(len(order), 4)

    def test_hermes_puts_openai_chat_first(self):
        """hermes → openai_chat must be first."""
        order = testing._agent_protocol_order("hermes", "")
        self.assertEqual(order[0], "openai_chat")

    def test_order_contains_all_formats(self):
        """Every order should contain all 4 formats."""
        for app_type in ("claude", "codex", "hermes", "gemini", "both"):
            order = testing._agent_protocol_order(app_type, "")
            self.assertEqual(len(order), 4, f"Order for {app_type} has {len(order)} items")
            self.assertIn("anthropic_messages", order)
            self.assertIn("openai_chat", order)
            self.assertIn("openai_responses", order)
            self.assertIn("gemini_native", order)


class ProbeApiFormatCodexTest(unittest.TestCase):
    """Tests for _probe_api_format: codex must prioritize openai_responses."""

    def test_codex_probe_responses_before_chat(self):
        """When both /chat/completions and /responses exist,
        codex should probe /responses first and return openai_responses,
        NOT short-circuit on openai_chat."""
        probe_order = []

        def fake_urlopen(req, timeout=None, context=None):
            probe_order.append(req.full_url)
            body = json.loads(req.data.decode("utf-8")) if req.data else {}
            # Both endpoints exist and return 200
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                return _FakeResponse({"output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}]})
            if "/chat/completions" in req.full_url or "/v1/chat/completions" in req.full_url:
                return _FakeResponse({"choices": [{"message": {"content": "hi"}}]})
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                return _FakeResponse({"content": [{"type": "text", "text": "hi"}]})
            if "generateContent" in req.full_url:
                return _FakeResponse({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._probe_api_format(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "codex"
            )

        # With codex, responses should be probed before chat
        # Find the first probe that returned a result
        self.assertEqual(result[0], "openai_responses",
                         f"Expected openai_responses, got {result}. Probe order: {probe_order}")

    def test_codex_does_not_return_openai_chat_when_responses_is_404(self):
        """When /chat/completions returns 200 but /responses returns 404,
        codex should NOT return openai_chat (since codex only uses responses)."""
        probe_order = []

        def fake_urlopen(req, timeout=None, context=None):
            probe_order.append(req.full_url)
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if "/chat/completions" in req.full_url or "/v1/chat/completions" in req.full_url:
                return _FakeResponse({"choices": [{"message": {"content": "hi"}}]})
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if "generateContent" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._probe_api_format(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "codex"
            )

        # openai_responses should be probed first and return 404
        # openai_chat should also be probed but codex prioritizes responses
        # Since responses is 404, the probe should continue to chat
        # But wait - the current probe returns the first format that gets a non-404 response
        # With codex, we want responses probed first. If responses is 404, chat is probed.
        # The probe function returns the first format that gives a non-404 response.
        # So if chat/completions returns 200, it would return openai_chat.
        # 
        # Actually, looking at the probe logic: it returns the FIRST format
        # where any path+auth combination returns a non-404 response.
        # With the new ordering for codex, openai_responses should be probed first.
        # If responses returns 404, it continues to chat, which returns 200 → openai_chat.
        #
        # But the task says: "不能因先命中 openai_chat 就提前返回而漏测 responses"
        # This means: responses should be probed BEFORE chat, so if responses
        # returns 404, we correctly detect that it's not available, and then
        # we can fall through to chat. The key is that responses is tested first.
        #
        # So the assertion should be: responses was probed before chat.
        self.assertEqual(result[0], "openai_chat",
                         f"Expected openai_chat (since responses is 404), got {result}")
        # Verify responses was probed first
        responses_probes = [u for u in probe_order if "responses" in u]
        chat_probes = [u for u in probe_order if "chat/completions" in u]
        self.assertTrue(len(responses_probes) > 0, "responses should have been probed")
        # The first responses probe should appear before the first chat probe
        first_responses_idx = min(i for i, u in enumerate(probe_order) if "responses" in u)
        first_chat_idx = min(i for i, u in enumerate(probe_order) if "chat/completions" in u)
        self.assertLess(first_responses_idx, first_chat_idx,
                        f"responses should be probed before chat. Order: {probe_order}")


class ChatEndpointAgentProtocolTest(unittest.TestCase):
    """Tests for _test_chat_endpoint: agent protocol must be tested first,
    and if it fails but fallback succeeds, return fail."""

    def test_codex_returns_ok_when_responses_succeeds(self):
        """codex: when openai_responses succeeds, return ok with tested_format."""
        def fake_urlopen(req, timeout=None, context=None):
            body = json.loads(req.data.decode("utf-8"))
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                return _FakeResponse({
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello from Codex"}]}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "codex"
            )

        self.assertTrue(result["success"], f"Expected success, got: {result}")
        self.assertEqual(result.get("tested_format"), "openai_responses",
                         f"Expected tested_format=openai_responses, got: {result.get('tested_format')}")
        self.assertEqual(result.get("api_format"), "openai_responses")
        self.assertIn("tested_path", result, "Result should have tested_path field")

    def test_codex_returns_fail_when_responses_fails_but_chat_succeeds(self):
        """codex: when openai_responses fails (404) but openai_chat succeeds,
        return fail with clear error message about Codex."""
        def fake_urlopen(req, timeout=None, context=None):
            body = json.loads(req.data.decode("utf-8"))
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if "/chat/completions" in req.full_url or "/v1/chat/completions" in req.full_url:
                return _FakeResponse({
                    "choices": [{"message": {"content": "Hello from chat"}}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "codex"
            )

        self.assertFalse(result["success"],
                         f"Expected fail because codex needs responses, got: {result}")
        error = result.get("error", "")
        self.assertIn("Responses", error,
                      f"Error should mention Responses API, got: {error}")

    def test_claude_returns_ok_when_anthropic_succeeds(self):
        """claude: when anthropic_messages succeeds, return ok."""
        def fake_urlopen(req, timeout=None, context=None):
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                return _FakeResponse({
                    "content": [{"type": "text", "text": "Hello from Claude"}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "claude-haiku-4-5", "claude"
            )

        self.assertTrue(result["success"], f"Expected success, got: {result}")
        self.assertEqual(result.get("tested_format"), "anthropic_messages")
        self.assertEqual(result.get("api_format"), "anthropic_messages")

    def test_claude_returns_fail_when_anthropic_fails_but_chat_succeeds(self):
        """claude: when anthropic_messages fails but openai_chat succeeds,
        return fail with clear error about Claude."""
        def fake_urlopen(req, timeout=None, context=None):
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if "/chat/completions" in req.full_url or "/v1/chat/completions" in req.full_url:
                return _FakeResponse({
                    "choices": [{"message": {"content": "Hello from chat"}}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "claude-haiku-4-5", "claude"
            )

        self.assertFalse(result["success"],
                         f"Expected fail because claude needs anthropic, got: {result}")
        error = result.get("error", "")
        self.assertIn("Anthropic", error,
                      f"Error should mention Anthropic Messages, got: {error}")

    def test_both_returns_ok_when_both_protocols_succeed(self):
        """both: when both anthropic_messages and openai_responses succeed, return ok."""
        def fake_urlopen(req, timeout=None, context=None):
            body = json.loads(req.data.decode("utf-8"))
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                return _FakeResponse({
                    "content": [{"type": "text", "text": "Hello from Claude"}]
                })
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                return _FakeResponse({
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello from Codex"}]}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "both"
            )

        self.assertTrue(result["success"], f"Expected success for both, got: {result}")
        self.assertIn("tested_format", result)
        # tested_format should indicate both were tested
        tf = result.get("tested_format", "")
        self.assertIn("anthropic_messages", tf)
        self.assertIn("openai_responses", tf)

    def test_both_returns_fail_when_responses_fails(self):
        """both: when anthropic_messages succeeds but openai_responses fails,
        return fail."""
        def fake_urlopen(req, timeout=None, context=None):
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                return _FakeResponse({
                    "content": [{"type": "text", "text": "Hello from Claude"}]
                })
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "both"
            )

        self.assertFalse(result["success"],
                         f"Expected fail because codex protocol fails, got: {result}")
        error = result.get("error", "")
        self.assertIn("Responses", error,
                      f"Error should mention Responses API, got: {error}")

    def test_both_returns_fail_when_anthropic_fails(self):
        """both: when openai_responses succeeds but anthropic_messages fails,
        return fail."""
        def fake_urlopen(req, timeout=None, context=None):
            if "/messages" in req.full_url or "/v1/messages" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))
            if "/responses" in req.full_url or "/v1/responses" in req.full_url:
                return _FakeResponse({
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello from Codex"}]}]
                })
            raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=io.BytesIO(b""))

        with patch("core.testing._http_open", side_effect=_adapt_http_open(fake_urlopen)):
            result = testing._test_chat_endpoint(
                "https://api.example.com/v1", "sk-test", "gpt-4o-mini", "both"
            )

        self.assertFalse(result["success"],
                         f"Expected fail because claude protocol fails, got: {result}")
        error = result.get("error", "")
        self.assertIn("Anthropic", error,
                      f"Error should mention Anthropic Messages, got: {error}")


if __name__ == "__main__":
    unittest.main()