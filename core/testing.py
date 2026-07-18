"""
测试模块 - 供应商连接测试
支持并发测试、可配置 SSL 验证、重试机制和优雅中断
"""

import urllib.request
import urllib.error
import urllib.parse
import ssl
import json
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Tuple
from . import db


def _create_ssl_context() -> ssl.SSLContext:
    """创建 SSL 上下文，根据 ssl_verify 设置决定是否验证证书"""
    verify = db.get_setting("ssl_verify")
    if verify == "1":
        return ssl.create_default_context()
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _get_timeout() -> int:
    """从设置中获取超时时间（秒）"""
    try:
        return int(db.get_setting("test_timeout") or "30")
    except (ValueError, TypeError):
        return 30


def _get_retries() -> int:
    """从设置中获取重试次数"""
    try:
        return int(db.get_setting("test_retries") or "2")
    except (ValueError, TypeError):
        return 2


def _classify_error(error_str: str) -> str:
    """将错误信息分类为具体错误类型"""
    if not error_str:
        return "unknown"
    e = error_str.lower()
    if "timed out" in e or "timeout" in e:
        return "timeout"
    if "name or service not known" in e or "getaddrinfo" in e or "nodename nor servname" in e:
        return "dns_failure"
    if "ssl" in e or "certificate" in e or "tls" in e:
        return "ssl_error"
    if "connection refused" in e or "actively refused" in e:
        return "connection_refused"
    if "connection reset" in e or "broken pipe" in e:
        return "connection_reset"
    if "no route to host" in e or "network is unreachable" in e:
        return "network_unreachable"
    if "http 401" in e or "http 403" in e:
        return "auth_error"
    if "http 429" in e:
        return "rate_limited"
    if "http 5" in e:
        return "server_error"
    return "unknown"


_ERROR_HINTS = {
    "timeout": "连接超时，可能是网络慢或服务器负载高",
    "dns_failure": "域名无法解析，请检查端点 URL 是否正确",
    "ssl_error": "SSL/TLS 证书错误，可能需要在设置中调整 SSL 验证",
    "connection_refused": "连接被拒绝，服务器可能未运行",
    "connection_reset": "连接被重置，可能是防火墙或代理拦截",
    "network_unreachable": "网络不可达，请检查网络连接",
    "auth_error": "API Key 无效或已过期",
    "rate_limited": "请求频率过高，已被限流",
    "server_error": "服务器内部错误，请稍后重试",
    "unknown": "未知错误",
}


# API 格式标识 → 显示名称映射
API_FORMAT_LABELS = {
    "anthropic_messages": "Anthropic Messages",
    "openai_chat": "OpenAI Chat Completions",
    "openai_responses": "OpenAI Responses API",
    "gemini_native": "Gemini generateContent",
}


def test_provider(provider_id: int, mode: str = "fast", log_callback=None) -> Dict[str, any]:
    """
    测试单个供应商

    Args:
        provider_id: 供应商 ID
        mode: 测试模式 - fast(快速) / full(完整)
        log_callback: CLI 日志回调 log_callback(level, text)

    Returns:
        测试结果字典
    """
    provider = db.get_provider_by_id(provider_id)
    if not provider:
        return {"success": False, "error": "供应商不存在"}

    endpoint = provider.get("endpoint", "")
    api_key = provider.get("api_key", "")
    name = provider.get("name", "")
    default_model = (provider.get("default_model") or "").strip()
    app_type = (provider.get("app_type") or "claude").strip()
    api_format = (provider.get("api_format") or "").strip()

    if not endpoint:
        return {"success": False, "error": "未配置端点"}

    # 确保端点不以 / 结尾
    endpoint = endpoint.rstrip("/")

    start_time = time.time()

    def log(level, text):
        if log_callback:
            try:
                log_callback(level, text, name)
            except Exception:
                pass

    # 重试逻辑：指数退避
    max_retries = _get_retries()
    last_result = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = 1.0 * (2 ** (attempt - 1))  # 1s, 2s, 4s...
            log("info", f"↻ 第 {attempt + 1} 次尝试 (等待 {wait}s)")
            time.sleep(wait)

        try:
            if mode == "fast":
                # 快速测试：获取模型列表
                log("info", f"→ GET {endpoint}/models")
                result = _test_models_endpoint(endpoint, api_key)
            else:
                # 完整测试：发送测试消息并验证 AI 回复
                log("info", f"→ POST {endpoint}  发送 \"你是谁呀，小朋友\" 验证 AI 回复")
                result = _test_chat_endpoint(endpoint, api_key, default_model, app_type, api_format)

            last_result = result

            # 成功则不再重试
            if result["success"]:
                break
            # auth_error 和 rate_limited 不重试（重试也没用）
            error_type = _classify_error(result.get("error", ""))
            if error_type in ("auth_error", "rate_limited"):
                break

        except Exception as e:
            last_result = {"success": False, "error": str(e)}
            error_type = _classify_error(str(e))
            if error_type in ("auth_error", "rate_limited", "dns_failure"):
                break

    latency = int((time.time() - start_time) * 1000)

    if last_result["success"]:
        detail = last_result.get("detail", "正常")
        snippet = last_result.get("response_snippet", "")
        api_format = last_result.get("api_format", "")
        if mode == "full" and snippet:
            detail = f"AI 正常回复：{snippet}"

        # 快速测试成功后，如果 api_format 未知，做轻量探测
        if mode == "fast" and not api_format:
            probed = _probe_api_format(endpoint, api_key, default_model, app_type)
            if probed:
                api_format = probed

        # full 模式: 必须有实际 AI 回复内容才算正常
        if mode == "full" and not snippet:
            error_msg = "API 可达但无 AI 回复内容"
            log("err", f"✗ {latency}ms · {error_msg}")
            db.update_provider_status(
                provider_id,
                status="fail",
                latency=latency,
                detail=error_msg,
            )
            db.add_test_history(provider_id, "fail", latency, "no_content", error_msg, mode)
            return {
                "success": False,
                "status": "fail",
                "detail": error_msg,
            }

        # 保存检测到的 API 格式
        if api_format:
            db.update_api_format(provider_id, api_format)

        # CLI 日志: 显示 AI 回复片段
        if snippet:
            fmt_label = API_FORMAT_LABELS.get(api_format, api_format)
            log("ok", f"✓ {latency}ms · [{fmt_label}] AI 回复: {snippet}")
        else:
            log("ok", f"✓ {latency}ms · {detail}")

        db.update_provider_status(
            provider_id,
            status="ok",
            latency=latency,
            detail=detail,
        )
        db.add_test_history(provider_id, "ok", latency, "", detail, mode)
        return {
            "success": True,
            "status": "ok",
            "latency": latency,
            "detail": detail,
            "api_format": api_format,
        }
    else:
        error = last_result.get("error", "连接失败")
        error_type = _classify_error(error)
        hint = _ERROR_HINTS.get(error_type, "")
        detail_msg = f"{error}（{hint}）" if hint else error
        log("err", f"✗ {latency}ms · {detail_msg}")
        db.update_provider_status(
            provider_id,
            status="fail",
            latency=None,
            detail=detail_msg,
        )
        db.add_test_history(provider_id, "fail", None, error_type, error, mode)
        return {
            "success": False,
            "status": "fail",
            "detail": detail_msg,
            "error_type": error_type,
        }


def _probe_api_format(endpoint: str, api_key: str, model: str = "", app_type: str = "claude") -> str:
    """轻量级 API 格式探测（用于快速测试后）
    对每种格式的端点发送极短超时的 POST，只要有响应（含错误码）即认为该格式存在。
    返回格式标识字符串或空字符串。
    """
    # 5 秒超时，避免探测拖慢快速测试
    probe_timeout = 5

    # 探测请求体（极小，不会触发真正的 AI 推理）
    probes = []

    # Anthropic Messages
    claude_model = model if model else "claude-haiku-4-5"
    claude_paths = [f"{endpoint}/messages"]
    if not endpoint.endswith("/v1"):
        claude_paths.append(f"{endpoint}/v1/messages")
    probes.append(("anthropic_messages", claude_paths,
                   {"model": claude_model, "max_tokens": 1, "messages": [{"role": "user", "content": "."}]},
                   {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}))

    # OpenAI Chat Completions
    oai_model = model if model else "gpt-3.5-turbo"
    oai_paths = [f"{endpoint}/chat/completions"]
    if not endpoint.endswith("/v1"):
        oai_paths.append(f"{endpoint}/v1/chat/completions")
    probes.append(("openai_chat", oai_paths,
                   {"model": oai_model, "max_tokens": 1, "messages": [{"role": "user", "content": "."}]},
                   {"Content-Type": "application/json"}))

    # OpenAI Responses API
    resp_paths = [f"{endpoint}/responses"]
    if not endpoint.endswith("/v1"):
        resp_paths.append(f"{endpoint}/v1/responses")
    probes.append(("openai_responses", resp_paths,
                   {"model": model if model else "gpt-4o-mini", "input": "."},
                   {"Content-Type": "application/json"}))

    # Gemini generateContent
    gemini_model = model if model else "gemini-2.0-flash"
    gemini_paths = [f"{endpoint}/models/{gemini_model}:generateContent"]
    probes.append(("gemini_native", gemini_paths,
                   {"contents": [{"parts": [{"text": "."}]}]},
                   {"Content-Type": "application/json"}))

    # 按 app_type 调整探测顺序
    if app_type == "gemini":
        probes.sort(key=lambda x: 0 if x[0] == "gemini_native" else 1)
    elif app_type in ("hermes", "codex"):
        probes.sort(key=lambda x: 0 if x[0] in ("openai_chat", "openai_responses") else 1)

    ctx = _create_ssl_context()
    base_header_variants = [{"User-Agent": _USER_AGENT}]
    if api_key:
        base_header_variants = [
            {"User-Agent": _USER_AGENT, "Authorization": f"Bearer {api_key}"},
            {"User-Agent": _USER_AGENT, "x-api-key": api_key},
        ]

    for fmt_key, paths, body, extra_headers in probes:
        for headers_base in base_header_variants:
            headers = dict(headers_base)
            headers.update(extra_headers)
            # Gemini 需要 key 参数
            if fmt_key == "gemini_native" and api_key:
                headers["x-goog-api-key"] = api_key

            for url in paths:
                if fmt_key == "gemini_native" and api_key:
                    url = f"{url}?key={api_key}"
                try:
                    data = json.dumps(body).encode("utf-8")
                    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=probe_timeout, context=ctx) as resp:
                        if resp.status in (200, 201):
                            return fmt_key
                except urllib.error.HTTPError as e:
                    # 400/401/403/422/429 都说明端点存在且接受该格式
                    if e.code in (400, 401, 403, 404, 405, 422, 429):
                        # 404 说明路径不存在，跳过
                        if e.code == 404:
                            continue
                        return fmt_key
                except (urllib.error.URLError, Exception):
                    # DNS 失败/超时 → 该端点不可达，跳过
                    continue

    return ""


def _test_models_endpoint(endpoint: str, api_key: str) -> Dict[str, any]:
    """测试 /models 端点（快速测试）
    尝试多种路径和认证方式，兼容不同提供商的路由格式。"""
    base = endpoint.rstrip("/")

    # 构建路径变体
    paths = []
    if base.endswith("/v1"):
        paths.append(f"{base}/models")
    else:
        paths.append(f"{base}/v1/models")
        paths.append(f"{base}/models")
        paths.append(f"{base}/api/models")

    # 构建认证方式
    auth_list = []
    if api_key:
        auth_list.append({"Authorization": f"Bearer {api_key}"})
        auth_list.append({"x-api-key": api_key})
    else:
        auth_list.append({})

    ctx = _create_ssl_context()
    timeout = _get_timeout()
    all_404 = True
    last_error = ""

    for auth_h in auth_list:
        headers = {"User-Agent": _USER_AGENT, "anthropic-version": "2023-06-01"}
        headers.update(auth_h)

        for url in paths:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                    if response.status == 200:
                        return {"success": True, "detail": "正常"}
                    last_error = f"HTTP {response.status}"
                    all_404 = False
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    all_404 = False
                last_error = f"HTTP {e.code}"
            except urllib.error.URLError as e:
                return {"success": False, "error": str(e.reason)}
            except Exception as e:
                return {"success": False, "error": str(e)}

    # 所有路径都 404 → 该提供商不支持 /models，用 HEAD 请求做纯连通性检测
    if all_404:
        return _test_connectivity_fallback(base, api_key)

    return {"success": False, "error": last_error or "连接失败"}


def _test_connectivity_fallback(endpoint: str, api_key: str) -> Dict[str, any]:
    """当 /models 全部 404 时，对端点根路径做 HEAD 请求检测连通性"""
    ctx = _create_ssl_context()
    timeout = _get_timeout()

    headers = {"User-Agent": _USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(endpoint, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            return {"success": True, "detail": "端点可达(无 /models)"}
    except urllib.error.HTTPError as e:
        # 4xx/5xx 都说明服务器可达，只是不接受 HEAD
        if 400 <= e.code < 600:
            return {"success": True, "detail": "端点可达(无 /models)"}
        return {"success": False, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": str(e.reason)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _test_chat_endpoint(endpoint: str, api_key: str, model: str = "", app_type: str = "claude", api_format: str = "") -> Dict[str, any]:
    """测试聊天端点（完整测试）
    手动指定 api_format 时优先尝试该格式；否则按 app_type 优先级依次尝试:
      - claude: Anthropic Messages → OpenAI Chat Completions → OpenAI Responses → Gemini
      - hermes/codex: OpenAI Chat Completions → OpenAI Responses → Anthropic Messages → Gemini
      - gemini: Gemini → OpenAI Chat Completions → OpenAI Responses → Anthropic Messages
    返回结果含 api_format 字段标识命中的格式。
    """

    auth_methods = []
    if api_key:
        auth_methods.append(("Bearer", {"Authorization": f"Bearer {api_key}"}))
        auth_methods.append(("x-api-key", {"x-api-key": api_key}))

    def _model_candidates(default_model: str) -> list:
        candidates = []
        seen = set()

        for item in (default_model or "").split(","):
            item = item.strip()
            if item and item not in seen:
                seen.add(item)
                candidates.append(item)
        return candidates

    def _try_anthropic_messages_legacy():
        claude_model = model if model else "claude-haiku-4-5"
        body = {"model": claude_model, "max_tokens": 32, "messages": [{"role": "user", "content": "你是谁呀，小朋友"}]}
        paths = ["messages"] if endpoint.endswith("/v1") else ["v1/messages", "messages"]
        for path in paths:
            for auth_name, auth_h in auth_methods:
                url = f"{endpoint}/{path}"
                headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
                headers.update(auth_h)
                try:
                    result = _send_post_request(url, headers, body)
                    if result["success"]:
                        snippet = _extract_response_snippet(result.get("body"), "auto")
                        if snippet:
                            return {"success": True, "detail": f"AI 正常回复 ({auth_name})", "response_snippet": snippet, "api_format": "anthropic_messages"}
                except Exception:
                    pass
        return None

    def _try_anthropic_messages():
        model_names = _model_candidates(model) or ["claude-haiku-4-5"]
        paths = ["messages"] if endpoint.endswith("/v1") else ["v1/messages", "messages"]
        for claude_model in model_names:
            body = {"model": claude_model, "max_tokens": 32, "messages": [{"role": "user", "content": "你是谁呀，小朋友"}]}
            for path in paths:
                for auth_name, auth_h in auth_methods:
                    url = f"{endpoint}/{path}"
                    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
                    headers.update(auth_h)
                    try:
                        result = _send_post_request(url, headers, body)
                        if result["success"]:
                            snippet = _extract_response_snippet(result.get("body"), "auto")
                            if snippet:
                                return {
                                    "success": True,
                                    "detail": f"AI 正常回复 ({auth_name})",
                                    "response_snippet": snippet,
                                    "api_format": "anthropic_messages",
                                }
                    except Exception:
                        pass
        return None

    def _try_openai_chat():
        if model:
            oai_model = model
        elif "api.deepseek.com" in endpoint.lower():
            oai_model = "deepseek-chat"
        else:
            oai_model = "gpt-3.5-turbo"
        body = {"model": oai_model, "max_tokens": 32, "messages": [{"role": "user", "content": "你是谁呀，小朋友"}]}
        urls = [f"{endpoint}/chat/completions"]
        if not endpoint.endswith("/v1"):
            urls.append(f"{endpoint}/v1/chat/completions")
            parsed = urllib.parse.urlparse(endpoint)
            path_parts = [p for p in parsed.path.split("/") if p]
            if path_parts:
                stripped_path = "/" + "/".join(path_parts[:-1])
                stripped_base = f"{parsed.scheme}://{parsed.netloc}{stripped_path}".rstrip("/")
                urls.append(f"{stripped_base}/v1/chat/completions")
        for url in urls:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            try:
                result = _send_post_request(url, headers, body)
                if result["success"]:
                    snippet = _extract_response_snippet(result.get("body"), "auto")
                    if snippet:
                        return {"success": True, "detail": "AI 正常回复 (OpenAI)", "response_snippet": snippet, "api_format": "openai_chat"}
            except Exception:
                pass
        return None

    def _try_openai_responses():
        oai_model = model if model else "gpt-4o-mini"
        body = {"model": oai_model, "input": "你是谁呀，小朋友"}
        paths = ["responses"]
        if not endpoint.endswith("/v1"):
            paths.append("v1/responses")
        for path in paths:
            url = f"{endpoint}/{path}"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            try:
                result = _send_post_request(url, headers, body)
                if result["success"]:
                    snippet = _extract_response_snippet(result.get("body"), "auto")
                    if snippet:
                        return {"success": True, "detail": "AI 正常回复 (Responses)", "response_snippet": snippet, "api_format": "openai_responses"}
            except Exception:
                pass
        return None

    def _try_gemini():
        gemini_model = model if model else "gemini-2.0-flash"
        body = {"contents": [{"parts": [{"text": "你是谁呀，小朋友"}]}]}
        model_names = [gemini_model] if model else ["gemini-2.0-flash", "gemini-1.5-flash"]
        for m in model_names:
            url = f"{endpoint}/models/{m}:generateContent"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["x-goog-api-key"] = api_key
                url = f"{url}?key={api_key}"
            try:
                result = _send_post_request(url, headers, body)
                if result["success"]:
                    snippet = _extract_response_snippet(result.get("body"), "auto")
                    if snippet:
                        return {"success": True, "detail": "AI 正常回复 (Gemini)", "response_snippet": snippet, "api_format": "gemini_native"}
            except Exception:
                pass
        return None

    format_funcs = {
        "anthropic_messages": _try_anthropic_messages,
        "openai_chat": _try_openai_chat,
        "openai_responses": _try_openai_responses,
        "gemini_native": _try_gemini,
    }

    if app_type == "gemini":
        order = ["gemini_native", "openai_chat", "openai_responses", "anthropic_messages"]
    elif app_type in ("hermes", "codex"):
        order = ["openai_chat", "openai_responses", "anthropic_messages", "gemini_native"]
    else:
        order = ["anthropic_messages", "openai_chat", "openai_responses", "gemini_native"]

    if api_format in format_funcs:
        order = [api_format] + [fmt for fmt in order if fmt != api_format]

    for fmt_key in order:
        result = format_funcs[fmt_key]()
        if result:
            return result

    return {"success": False, "error": "所有 API 格式均无法匹配"}


def _extract_response_snippet(body_str: str, style: str) -> str:
    """从 API 响应体中提取 AI 回复文本片段
    style: 'claude' / 'openai' / 'gemini' / 'responses' / 'auto' (自动检测所有格式)"""
    if not body_str:
        return ""
    try:
        data = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        return ""

    def _try_claude(d):
        content = d.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = (block.get("text") or block.get("thinking") or "").strip()
                    if text:
                        return text[:80] + ("..." if len(text) > 80 else "")
        return ""

    def _try_openai(d):
        choices = d.get("choices", [])
        if choices:
            msg = choices[0].get("message", {}) or choices[0].get("delta", {})
            text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            if text:
                return text[:80] + ("..." if len(text) > 80 else "")
        return ""

    def _try_openai_responses(d):
        """OpenAI Responses API: {"output": [{"type": "message", "content": [{"type": "output_text", "text": "..."}]}]}"""
        output = d.get("output", [])
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict) and item.get("type") == "message":
                    content = item.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                text = (block.get("text") or "").strip()
                                if text:
                                    return text[:80] + ("..." if len(text) > 80 else "")
        return ""

    def _try_gemini(d):
        """Gemini: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}"""
        candidates = d.get("candidates", [])
        if candidates:
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if isinstance(part, dict):
                    text = (part.get("text") or "").strip()
                    if text:
                        return text[:80] + ("..." if len(text) > 80 else "")
        return ""

    def _try_generic(d):
        """通用回退: 在顶层字段中寻找 text/content/response 文本"""
        if not isinstance(d, dict):
            return ""
        for key in ("text", "content", "response", "output", "result"):
            val = d.get(key)
            if isinstance(val, str) and val.strip():
                t = val.strip()
                return t[:80] + ("..." if len(t) > 80 else "")
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for tk in ("text", "content", "value"):
                            tv = item.get(tk, "")
                            if isinstance(tv, str) and tv.strip():
                                tv = tv.strip()
                                return tv[:80] + ("..." if len(tv) > 80 else "")
        return ""

    try:
        if style == "claude":
            return _try_claude(data)
        elif style == "openai":
            return _try_openai(data)
        elif style == "gemini":
            return _try_gemini(data)
        elif style == "responses":
            return _try_openai_responses(data)
        else:  # auto: try all formats
            return (_try_claude(data) or _try_openai(data)
                    or _try_openai_responses(data) or _try_gemini(data)
                    or _try_generic(data))
    except (KeyError, IndexError, TypeError):
        return ""


# 伪装为正常 HTTP 客户端，避免 Cloudflare 1010 拦截 Python-urllib 默认 UA
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def fetch_models(endpoint: str, api_key: str, api_format: str = "", default_model: str = "") -> Dict[str, any]:
    """从提供商的 /models 端点获取可用模型列表
    尝试多种路径和认证方式,兼容 OpenAI / Anthropic / 各种中继代理"""

    if not endpoint:
        return {"success": False, "error": "未提供端点 URL"}

    ctx = _create_ssl_context()
    timeout = _get_timeout()

    # 构建路径变体（覆盖更多中继代理的路由格式）
    base = endpoint.rstrip("/")
    paths = []
    if base.endswith("/v1"):
        paths.append(f"{base}/models")
    else:
        paths.append(f"{base}/v1/models")
        paths.append(f"{base}/models")
        paths.append(f"{base}/api/models")
        # 部分中继在 base/v1 下挂 OpenAI 兼容路由
        paths.append(f"{base}/openai/v1/models")
        # Gemini 原生模型列表常见于 /models，认证使用 x-goog-api-key 或 ?key=...
        if api_format == "gemini_native":
            if not base.endswith("/v1") and not base.endswith("/v1beta"):
                paths.append(f"{base}/v1beta/models")
                paths.append(f"{base}/v1/models")
        # 对类似 https://proxy.example/anthropic 的路径，尝试去掉最后一级
        # 这样 https://proxy.example/anthropic/v1/models 不行时还能试 https://proxy.example/v1/models
        from urllib.parse import urlparse
        parsed = urlparse(base)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts:
            stripped_path = "/" + "/".join(path_parts[:-1])
            stripped_base = f"{parsed.scheme}://{parsed.netloc}{stripped_path}".rstrip("/")
            paths.append(f"{stripped_base}/v1/models")
            paths.append(f"{stripped_base}/models")

    # 构建多种认证头（某些中继对双 header 请求会报错）
    auth_headers_list = []
    if api_key:
        if api_format == "gemini_native":
            auth_headers_list.append({"x-goog-api-key": api_key})
        auth_headers_list.append({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        auth_headers_list.append({"Authorization": f"Bearer {api_key}"})
        auth_headers_list.append({"api-key": api_key})
        # 部分中继需要同时携带两种 header
        auth_headers_list.append({"Authorization": f"Bearer {api_key}", "x-api-key": api_key})
    else:
        auth_headers_list.append({})

    all_404 = True
    last_error = ""
    auth_errors = []  # 收集认证错误以便诊断

    collected_models = []
    seen_models = set()

    def add_models(items):
        for model in items:
            if not isinstance(model, str):
                continue
            model = model.strip()
            if model and model not in seen_models:
                seen_models.add(model)
                collected_models.append(model)

    def _fetch_single_page(url: str, headers: dict):
        """请求单页模型列表，支持 OpenAI 分页"""
        try:
            request_url = url
            if api_format == "gemini_native" and api_key and "x-goog-api-key" in headers:
                parsed_url = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed_url.query)
                if "key" not in qs:
                    qs["key"] = [api_key]
                    request_url = urllib.parse.urlunparse((
                        parsed_url.scheme, parsed_url.netloc, parsed_url.path,
                        parsed_url.params, urllib.parse.urlencode(qs, doseq=True), parsed_url.fragment
                    ))
            req = urllib.request.Request(request_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status}"
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    return None, f"响应非 JSON（HTTP 200，可能是 HTML 页面）"
                models = _extract_model_ids(data)
                # 检查分页 (OpenAI 格式: data => has_more + first_id/last_id ; after cursor 传参翻页)
                next_url = None
                if isinstance(data, dict):
                    # OpenAI /v1/models 标准分页: data + has_more + first_id + last_id + object == "list"
                    if data.get("has_more") is True and data.get("object") == "list" and data.get("first_id") and data.get("last_id"):
                        next_cursor = data.get("last_id")
                        if next_cursor:
                            # 构造翻页 URL（保留查询参数）
                            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
                            parsed = urlparse(url)
                            qs = parse_qs(parsed.query)
                            qs["after"] = [str(next_cursor)]
                            new_qs = urlencode(qs, doseq=True)
                            next_url = urlunparse((
                                parsed.scheme, parsed.netloc, parsed.path,
                                parsed.params, new_qs, parsed.fragment
                            ))
                return {"models": models, "next_url": next_url}, None
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return None, str(e.reason)
        except Exception as e:
            return None, str(e)

    for auth_h in auth_headers_list:
        headers = {"User-Agent": _USER_AGENT}
        headers.update(auth_h)
        auth_failed = False

        for url in paths:
            next_url = url
            page_count = 0
            while next_url and page_count < 10:  # 最多翻 10 页
                result, err = _fetch_single_page(next_url, headers)
                if result is None:
                    # 认证失败 → 尝试下一个 auth
                    if err and (err.startswith("HTTP 401") or err.startswith("HTTP 403")):
                        auth_failed = True
                        auth_errors.append(f"{err} ({list(auth_h.keys())[0] if auth_h else '无认证'})")
                        break
                    if err and "404" not in err:
                        all_404 = False
                    if err and "响应非 JSON" in err:
                        # 某些供应商根路径会返回 HTML，但更靠后的 /v1/models 或去除路径后的 models 可能可用
                        if not last_error:
                            last_error = err
                        break
                    if not last_error:
                        last_error = err
                    break
                all_404 = False
                if result["models"]:
                    add_models(result["models"])
                next_url = result.get("next_url")
                page_count += 1

    if collected_models:
        return {"success": True, "models": collected_models}

    # 全部 404 → 只返回供应商自己配置的模型，不混入第三方端点可能并不支持的通用列表
    if all_404:
        # 1. 供应商有 default_model → 只返回它（代理/中继用自定义模型名）
        if default_model:
            models = [m.strip() for m in default_model.split(",") if m.strip()]
            if models:
                return {"success": True, "models": models}

        # 2. 只有一方官方端点才使用内置候选，避免 Longcat/OpenRouter 等中继误显示 Claude/OpenAI 模型
        if api_format and _is_first_party_model_endpoint(base, api_format):
            known = _get_known_models_by_format(api_format)
            if known:
                return {"success": True, "models": known, "from_fallback": True}

        # 3. 没有任何信息 → 提示手动填写
        return {
            "success": False,
            "error": "该端点不支持模型列表接口，请手动填写模型名称",
            "no_models_endpoint": True,
        }

    # 全部认证方式都失败 — 给出详细诊断
    if auth_errors and not last_error:
        return {
            "success": False,
            "error": f"认证失败: {', '.join(auth_errors)}。请检查 API Key 是否正确",
        }

    return {"success": False, "error": last_error or "获取失败"}


def _extract_model_ids(data) -> list:
    """从 API 响应中提取模型 ID 列表,兼容多种格式"""
    models = []

    # OpenAI 格式: {"data": [{"id": "gpt-4"}, ...]}
    if isinstance(data, dict) and "data" in data:
        items = data["data"]
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    mid = item.get("id") or item.get("name") or item.get("model")
                    if mid and isinstance(mid, str):
                        models.append(_normalize_model_id(mid))
                elif isinstance(item, str):
                    models.append(_normalize_model_id(item))
            if models:
                return models

    # Anthropic / Gemini 格式: {"models": [{"id": "claude-opus-4-8"}, {"name": "models/gemini-2.5-pro"}, ...]}
    if isinstance(data, dict) and "models" in data:
        items = data["models"]
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    methods = item.get("supportedGenerationMethods") or item.get("supported_generation_methods")
                    if isinstance(methods, list) and not any(m in methods for m in ("generateContent", "streamGenerateContent")):
                        continue
                    mid = item.get("id") or item.get("name") or item.get("model")
                    if mid and isinstance(mid, str):
                        models.append(_normalize_model_id(mid))
                elif isinstance(item, str):
                    models.append(_normalize_model_id(item))
            if models:
                return models

    # 直接列表: ["model-a", "model-b"]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
                if mid and isinstance(mid, str):
                    models.append(_normalize_model_id(mid))
        if models:
            return models

    return models


def _normalize_model_id(model_id: str) -> str:
    """归一化模型 ID；Gemini 列表会返回 models/gemini-xxx。"""
    model_id = (model_id or "").strip()
    if model_id.startswith("models/"):
        return model_id.split("/", 1)[1]
    return model_id


def _is_first_party_model_endpoint(base: str, api_format: str) -> bool:
    """仅官方一方端点可使用内置 fallback 模型，避免中继/第三方供应商显示错误品牌模型。"""
    try:
        host = urllib.parse.urlparse(base).netloc.lower()
    except Exception:
        return False

    if api_format == "anthropic_messages":
        return host == "api.anthropic.com"
    if api_format in ("openai_chat", "openai_responses"):
        return host == "api.openai.com"
    if api_format == "gemini_native":
        return host in ("generativelanguage.googleapis.com", "aiplatform.googleapis.com")
    return False


# 已知模型列表：按 api_format 分类，用于不支持 /models 端点的一方官方端点
_KNOWN_MODELS_BY_FORMAT = {
    "anthropic_messages": [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "openai_chat": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "o3-mini",
        "o1",
    ],
    "openai_responses": [
        "gpt-4o",
        "gpt-4o-mini",
        "o3-mini",
        "o1",
    ],
    "gemini_native": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}


def _get_known_models_by_format(api_format: str) -> list:
    """根据 api_format 返回已知模型列表"""
    if not api_format:
        return []
    return _KNOWN_MODELS_BY_FORMAT.get(api_format, [])


def _send_post_request(url: str, headers: Dict[str, str], body: Dict) -> Dict[str, any]:
    """发送 POST 请求,返回包含响应体"""
    data = json.dumps(body).encode("utf-8")
    headers.setdefault("User-Agent", _USER_AGENT)

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )

    ctx = _create_ssl_context()
    timeout = _get_timeout()

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            body_text = response.read().decode("utf-8", errors="replace")
            if response.status in (200, 201):
                return {"success": True, "body": body_text}
            else:
                return {"success": False, "error": f"HTTP {response.status}"}
    except urllib.error.HTTPError as e:
        # 401/403 说明 API 可达，只是 key 有问题，也算连接成功
        if e.code in (401, 403):
            return {"success": True, "detail": "API 可达(key 无效)", "body": ""}
        return {"success": False, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": str(e.reason)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_all_providers(mode: str = "fast", callback=None, log_callback=None,
                       stop_event: threading.Event = None) -> Dict[str, any]:
    """
    测试所有供应商（支持并发）

    Args:
        mode: 测试模式
        callback: 进度回调函数 callback(provider_id, result)
        log_callback: CLI 日志回调 log_callback(level, text, name)
        stop_event: 停止信号，设置后跳过剩余测试

    Returns:
        汇总结果
    """
    providers = db.get_providers()
    if not providers:
        return {"total": 0, "ok": 0, "fail": 0, "results": []}

    # 读取并发数设置
    try:
        concurrency = int(db.get_setting("test_concurrency") or "3")
    except (ValueError, TypeError):
        concurrency = 3
    concurrency = max(1, min(concurrency, 10))

    results = []
    completed_count = 0
    total = len(providers)

    def _test_one(provider):
        nonlocal completed_count
        # 检查停止信号
        if stop_event and stop_event.is_set():
            return None

        pid = provider["id"]
        db.update_provider_status(pid, status="testing", detail="测试中...")

        if callback:
            callback(pid, {"status": "testing"})

        result = test_provider(pid, mode, log_callback=log_callback)
        result["id"] = pid
        completed_count += 1

        if callback:
            callback(pid, result)

        return result

    if concurrency <= 1:
        # 串行模式
        for provider in providers:
            if stop_event and stop_event.is_set():
                break
            result = _test_one(provider)
            if result:
                results.append(result)
    else:
        # 并发模式
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(_test_one, p): p for p in providers}
            for future in as_completed(futures):
                if stop_event and stop_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception:
                    pass

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    fail_count = sum(1 for r in results if r.get("status") == "fail")

    return {
        "total": len(results),
        "ok": ok_count,
        "fail": fail_count,
        "results": results,
    }
