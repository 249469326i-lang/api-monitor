"""
测试模块 - 供应商连接测试
支持并发测试、可配置 SSL 验证、重试机制和优雅中断
"""

import urllib.request
import urllib.error
import urllib.parse
import ssl
import json
import re
import socket
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Tuple
from . import db

logger = logging.getLogger(__name__)


# 设置短 TTL 缓存：full 测试单个供应商会发几十个请求，
# 每个请求读 2-3 个设置各建一次 DB 连接，批测时是几百次无谓开销
_settings_cache: Dict[str, tuple] = {}
_settings_cache_lock = threading.Lock()
_SETTINGS_CACHE_TTL = 5.0


def _cached_setting(key: str):
    now = time.monotonic()
    with _settings_cache_lock:
        entry = _settings_cache.get(key)
        if entry and now - entry[1] < _SETTINGS_CACHE_TTL:
            return entry[0]
    val = db.get_setting(key)
    with _settings_cache_lock:
        _settings_cache[key] = (val, now)
    return val


def _create_ssl_context() -> ssl.SSLContext:
    """创建 SSL 上下文，根据 ssl_verify 设置决定是否验证证书"""
    verify = _cached_setting("ssl_verify")
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
        return int(_cached_setting("test_timeout") or "30")
    except (ValueError, TypeError):
        return 30


def _get_retries() -> int:
    """从设置中获取重试次数"""
    try:
        return int(_cached_setting("test_retries") or "2")
    except (ValueError, TypeError):
        return 2


def _has_win_or_errno(text: str, *codes: str) -> bool:
    """精确匹配 WinError/Errno 数字，避免 110 误伤 11001。"""
    for code in codes:
        # 明确标注：WinError 10060 / [Errno 11001] / errno=10061
        if re.search(rf"(?:winerror|errno|error)[\s:=#]*{code}\b", text, flags=re.I):
            return True
        if re.search(rf"\[(?:winerror|errno)\s+{code}\]", text, flags=re.I):
            return True
        # 仅对 5 位常见 Windows 套接字码允许裸数字（10060/10061/11001…）
        if len(code) >= 5 and re.search(rf"(?<!\d){code}(?!\d)", text):
            if not re.search(rf"http\s+{code}\b", text, flags=re.I):
                return True
    return False


def _normalize_error_text(error_str) -> str:
    """把 URLError/OSError/超时等原始异常压成可读中文主因。"""
    if error_str is None:
        return ""

    if isinstance(error_str, BaseException):
        parts = []
        cur = error_str
        seen = set()
        while isinstance(cur, BaseException) and id(cur) not in seen and len(parts) < 4:
            seen.add(id(cur))
            msg = str(cur).strip()
            if msg:
                parts.append(msg)
            nxt = getattr(cur, "reason", None)
            if nxt is None and getattr(cur, "args", None):
                nxt = cur.args[0] if cur.args else None
            # 仅继续解包嵌套异常，避免把 int/str 再当异常循环
            cur = nxt if isinstance(nxt, BaseException) else None
            if not isinstance(nxt, BaseException) and nxt is not None and not parts:
                parts.append(str(nxt).strip())
        text = " ".join(p for p in parts if p)
    else:
        text = str(error_str).strip()

    if not text:
        return ""

    # 去掉常见 urllib 包装
    if text.startswith("<urlopen error ") and text.endswith(">"):
        text = text[len("<urlopen error "):-1].strip()
    elif text.startswith("urlopen error "):
        text = text[len("urlopen error "):].strip()

    low = text.lower()
    # DNS 必须先于 timeout：errno 11001 含 "110" 子串，不可用 startswith 粗匹配
    if (
        _has_win_or_errno(text, "11001", "11002", "11003", "11004")
        or "getaddrinfo" in low
        or "name or service not known" in low
        or "nodename nor servname" in low
        or "找不到主机" in text
        or "无法解析" in text
    ):
        return "域名无法解析（DNS 失败）"
    if (
        _has_win_or_errno(text, "10060")
        or "etimedout" in low
        or re.search(r"(?:winerror|errno|error)[\s:=#]*(?:110|60)\b", text, flags=re.I)
        or "没有正确答复" in text
        or ("没有响应" in text and "连接" in text)
    ):
        return "连接超时（对方无响应）"
    if (
        _has_win_or_errno(text, "10061")
        or "econnrefused" in low
        or re.search(r"(?:winerror|errno|error)[\s:=#]*(?:111|61)\b", text, flags=re.I)
        or "积极拒绝" in text
        or "拒绝连接" in text
    ):
        return "连接被拒绝（目标端口未监听）"
    if _has_win_or_errno(text, "10051", "10065") or "enetunreach" in low or "ehostunreach" in low:
        return "网络不可达"
    if (
        _has_win_or_errno(text, "10054")
        or "econnreset" in low
        or re.search(r"(?:winerror|errno|error)[\s:=#]*(?:104|54)\b", text, flags=re.I)
        or "强迫关闭" in text
    ):
        return "连接被重置"
    if "certificate_verify_failed" in low or "sslcertverificationerror" in low:
        return "SSL 证书校验失败"
    if "ssl" in low and ("wrong version" in low or "eof" in low or "handshake" in low):
        return "SSL/TLS 握手失败"
    if "timed out" in low or "timeout" in low or "超时" in text:
        return "连接超时"
    if "connection refused" in low or "actively refused" in low or "积极拒绝" in text:
        return "连接被拒绝"
    if "connection reset" in low or "broken pipe" in low or "remote end closed" in low:
        return "连接被重置"
    if "no route to host" in low or "network is unreachable" in low or "无法连接的网络" in text:
        return "网络不可达"
    if "proxyerror" in low or "tunnel connection failed" in low:
        return "代理连接失败"
    return " ".join(text.split())


def _classify_error(error_str: str) -> str:
    """将错误信息分类为具体错误类型（兼容中英文 / WinError）"""
    if not error_str:
        return "unknown"
    # 先规范化再分类，避免 WinError 原文落成 unknown
    normalized = _normalize_error_text(error_str)
    e = f"{error_str} {normalized}".lower()
    raw = f"{error_str} {normalized}"

    if (
        "timed out" in e or "timeout" in e or "超时" in raw
        or "10060" in raw or "etimedout" in e
    ):
        return "timeout"
    if (
        "name or service not known" in e or "getaddrinfo" in e
        or "nodename nor servname" in e or "域名无法解析" in raw
        or "11001" in raw or "11002" in raw or "11003" in raw or "11004" in raw
    ):
        return "dns_failure"
    if "ssl" in e or "certificate" in e or "tls" in e or "证书" in raw:
        return "ssl_error"
    if (
        "connection refused" in e or "actively refused" in e
        or "连接被拒绝" in raw or "10061" in raw or "econnrefused" in e
        or "积极拒绝" in raw
    ):
        return "connection_refused"
    if (
        "connection reset" in e or "broken pipe" in e
        or "remote end closed" in e or "连接被重置" in raw
        or "10054" in raw or "econnreset" in e
    ):
        return "connection_reset"
    if (
        "no route to host" in e or "network is unreachable" in e
        or "网络不可达" in raw or "10051" in raw or "10065" in raw
        or "enetunreach" in e or "ehostunreach" in e
    ):
        return "network_unreachable"
    if "proxy" in e and ("fail" in e or "error" in e or "tunnel" in e or "连接" in raw):
        return "proxy_error"
    if (
        "http 401" in e or "http 403" in e
        or "api_key_required" in e or "invalid api key" in e
        or "invalid_api_key" in e or "authentication" in e
        or "unauthorized" in e or "api key is required" in e
        or "incorrect api key" in e or "api key not valid" in e
        or "permission_denied" in e
        or "未配置 api key" in e or "缺少 api key" in e or "无 api key" in e
        or ("api key" in e and ("无效" in raw or "缺失" in raw or "过期" in raw or "未配置" in raw))
        or ("密钥" in raw and ("无效" in raw or "缺失" in raw or "未配置" in raw))
        or "鉴权" in raw
    ):
        return "auth_error"
    if (
        "http 429" in e or "rate limit" in e or "rate_limit" in e
        or "too many requests" in e or "限流" in raw
    ):
        return "rate_limited"
    if (
        ("model" in e and ("not found" in e or "does not exist" in e or "invalid" in e or "unknown" in e or "not available" in e))
        or "model_not_found" in e
        or "模型" in raw and ("不存在" in raw or "无效" in raw or "不可用" in raw or "错误" in raw)
    ):
        return "model_error"
    if (
        "insufficient" in e or "quota" in e or "balance" in e
        or "billing" in e or "payment" in e
        or "余额" in raw or "积分" in raw or "额度" in raw or "欠费" in raw
    ):
        return "quota_error"
    if "http 404" in e or "not found" in e:
        return "not_found"
    if "http 5" in e:
        return "server_error"
    if "http 4" in e:
        return "client_error"
    if "无 ai 回复" in e or "no_content" in e or "无回复内容" in raw:
        return "empty_response"
    if "所有 api 格式均无法匹配" in e:
        return "format_mismatch"
    return "unknown"


_ERROR_HINTS = {
    "timeout": "连接超时，可增大超时时间或检查网络/代理",
    "dns_failure": "域名无法解析，请检查端点 URL 或 DNS/代理设置",
    "ssl_error": "SSL/TLS 证书错误，可在设置中关闭 SSL 验证后重试",
    "connection_refused": "连接被拒绝，服务器未运行或端口/地址错误",
    "connection_reset": "连接被重置，可能是防火墙、代理或上游中断",
    "network_unreachable": "网络不可达，请检查本机网络或代理",
    "proxy_error": "代理隧道失败，请检查系统/环境代理配置",
    "auth_error": "API Key 无效、缺失或已过期",
    "rate_limited": "请求频率过高，已被限流，请稍后重试",
    "model_error": "模型名可能不正确，请检查默认模型配置",
    "quota_error": "额度/余额不足，请检查账户配额",
    "not_found": "路径或资源不存在，请检查端点 URL / API 路径",
    "server_error": "服务器内部错误，请稍后重试",
    "client_error": "请求被拒绝，请检查端点、模型或请求格式",
    "empty_response": "接口可达但未返回 AI 文本，请检查模型或中继配置",
    "format_mismatch": "未能匹配可用 API 格式，请指定正确格式或检查端点",
    "unknown": "未知错误，请查看完整错误原文",
}


def _error_type_priority(error_type: str) -> int:
    """主因优先级：鉴权/配额/模型 > 网络类 > 404 噪声。数值越大越优先。"""
    order = {
        "auth_error": 100,
        "quota_error": 95,
        "rate_limited": 90,
        "model_error": 85,
        "server_error": 70,
        "timeout": 60,
        "connection_refused": 58,
        "connection_reset": 56,
        "proxy_error": 55,
        "ssl_error": 54,
        "dns_failure": 52,
        "network_unreachable": 50,
        "client_error": 40,
        "empty_response": 35,
        "not_found": 20,
        "format_mismatch": 10,
        "unknown": 0,
    }
    return order.get(error_type, 0)


def _hint_redundant(error: str, hint: str) -> bool:
    """hint 已包含在 error 中时不再重复拼接。"""
    if not hint:
        return True
    e = (error or "").lower()
    h = hint.lower()
    # 主因已写明同类信息
    if hint in (error or ""):
        return True
    keys = ("超时", "dns", "证书", "拒绝", "重置", "不可达", "api key", "密钥", "限流", "模型", "额度", "余额")
    return any(k in e and k in h for k in keys)


def _looks_like_attempt_summary(error: str) -> bool:
    """多格式尝试摘要 / 已格式化的 HTTP 错误，勿再做网络文案归一化。"""
    if not error:
        return False
    s = str(error).strip()
    # 仅识别我们自己加的 [format/path/auth] 标签，排除 [WinError …]/[Errno …]
    if s.startswith("["):
        low = s.lower()
        if low.startswith("[winerror") or low.startswith("[errno") or low.startswith("[error"):
            return False
        # 标签形如 [openai_chat/gpt] 或 [GET /v1/models | auth=Bearer]
        if "]" in s:
            label = s[1:s.find("]")]
            if "/" in label or "auth=" in label.lower() or label.lower().startswith("get ") or label.lower().startswith("post "):
                return True
    if s.upper().startswith("HTTP "):
        return True
    if " | " in s:
        return True
    return False


def _build_error_detail(error: str, error_type: str = "") -> str:
    """组装面向 CLI/详情页的最终错误文案：主因 + 非冗余 hint。"""
    raw = (error or "").strip() or "连接失败"
    if not _looks_like_attempt_summary(raw):
        raw = _normalize_error_text(raw) or raw
    et = error_type or _classify_error(raw)
    # unknown / 已足够具体的文案不再硬塞「未知错误」
    if et == "unknown":
        return raw
    hint = _ERROR_HINTS.get(et, "")
    if hint and not _hint_redundant(raw, hint):
        return f"{raw}（{hint}）"
    return raw


def _read_http_error_body(exc: "urllib.error.HTTPError", limit: int = 800) -> str:
    """安全读取 HTTPError 响应体（最多 limit 字符）"""
    try:
        raw = exc.read()
        if not raw:
            return ""
        return raw.decode("utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _extract_api_error_message(body_text: str) -> str:
    """从 API 错误响应 JSON/文本中提取可读 message。"""
    if not body_text:
        return ""
    text = body_text.strip()
    if not text:
        return ""

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # 非 JSON：HTML 错误页只取 title / 首行文本
        low = text.lower()
        if "<html" in low or "<!doctype" in low or "<title" in low:
            m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
            if m:
                title = " ".join(m.group(1).split())
                if title:
                    return title[:240]
            # Cloudflare / 网关常见文案
            for marker in ("error code", "attention required", "access denied", "just a moment"):
                if marker in low:
                    return f"上游返回 HTML 错误页（{marker}）"[:240]
            return "上游返回 HTML 错误页（非 API JSON）"
        compact = " ".join(text.split())
        return compact[:240]

    def _from_obj(obj, depth=0):
        if depth > 4 or obj is None:
            return ""
        if isinstance(obj, str):
            s = obj.strip()
            return s if s else ""
        if isinstance(obj, list):
            for item in obj:
                got = _from_obj(item, depth + 1)
                if got:
                    return got
            return ""
        if not isinstance(obj, dict):
            return ""

        # OpenAI / 常见中继: error.message / error.code
        err = obj.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
        if isinstance(err, dict):
            for key in ("message", "msg", "detail", "description", "error"):
                val = err.get(key)
                if isinstance(val, str) and val.strip():
                    code = err.get("code") or err.get("type") or obj.get("code")
                    if code and str(code) not in val:
                        return f"{val.strip()} [{code}]"
                    return val.strip()
            nested = _from_obj(err, depth + 1)
            if nested:
                return nested

        for key in ("message", "msg", "detail", "description", "error_description"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                code = obj.get("code") or obj.get("type") or obj.get("error_code")
                if code and str(code) not in val:
                    return f"{val.strip()} [{code}]"
                return val.strip()

        # Gemini: {error: {message, status, code}}
        for key in ("status", "reason"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip() and " " in val:
                return val.strip()

        return ""

    msg = _from_obj(data)
    if msg:
        return msg[:240]
    # 兜底：压缩 JSON
    try:
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return compact[:240]
    except Exception:
        return text[:240]


def _format_http_error(code: int, body_text: str = "") -> str:
    """格式化为：HTTP 401: message..."""
    msg = _extract_api_error_message(body_text)
    if msg:
        # 避免 "HTTP 401: HTTP 401 ..." 重复
        if msg.lower().startswith(f"http {code}"):
            return msg
        return f"HTTP {code}: {msg}"
    return f"HTTP {code}"


def _summarize_attempt_errors(attempts: list, max_items: int = 3) -> str:
    """合并多次尝试错误：主因优先（鉴权/配额/模型），弱化 404 噪声。"""
    if not attempts:
        return "所有 API 格式均无法匹配"

    cleaned = []
    seen = set()
    for item in attempts:
        text = (item or "").strip()
        if not text:
            continue
        # 对网络类原文做归一化，保留 [label] 前缀
        label = ""
        body = text
        if text.startswith("[") and "]" in text:
            bracket_end = text.find("]")
            label = text[: bracket_end + 1]
            body = text[bracket_end + 1 :].strip()
        if body and not _looks_like_attempt_summary(body):
            body = _normalize_error_text(body) or body
        text = f"{label} {body}".strip() if label else body
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)

    if not cleaned:
        return "所有 API 格式均无法匹配"

    def _score(s: str) -> int:
        sl = s.lower()
        et = _classify_error(s)
        score = _error_type_priority(et) * 10
        if "http " in sl:
            score += 8
        # 有具体 message 比纯状态码更有价值
        if ":" in s and len(s) > 12:
            score += 6
        if any(k in sl for k in ("invalid", "incorrect", "expired", "quota", "balance", "rate limit", "model")):
            score += 4
        # 纯 404 / Not Found 作为噪声降权
        if "http 404" in sl or sl.endswith("not found") or " 404" in sl:
            score -= 25
        return score

    cleaned.sort(key=_score, reverse=True)
    primary = cleaned[0]
    primary_type = _classify_error(primary)

    # 强主因（鉴权/配额/限流/模型）只展示一条，避免 404 噪声淹没
    if primary_type in ("auth_error", "quota_error", "rate_limited", "model_error") or max_items <= 1:
        return primary

    # 其余：补 1~2 条不同类别的次要错误
    picked = [primary]
    seen_types = {primary_type}
    for item in cleaned[1:]:
        if len(picked) >= max_items:
            break
        et = _classify_error(item)
        if et in seen_types and et in ("not_found", "client_error", "unknown"):
            continue
        if et == "not_found" and primary_type not in ("not_found", "format_mismatch", "unknown"):
            continue
        picked.append(item)
        seen_types.add(et)

    if len(picked) == 1:
        return picked[0]
    return " | ".join(picked)


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

    def log(level, text):
        if log_callback:
            try:
                log_callback(level, text, name)
            except Exception:
                pass

    # 重试逻辑：指数退避
    max_retries = _get_retries()
    last_result = None

    start_time = time.time()

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = 1.0 * (2 ** (attempt - 1))  # 1s, 2s, 4s...
            log("info", f"↻ 第 {attempt + 1} 次尝试 (等待 {wait}s)")
            time.sleep(wait)

        # 每次尝试单独计时:latency 只反映本次请求耗时,
        # 不含退避 sleep 和之前失败尝试(否则历史图表/failover 排序失真)
        start_time = time.time()

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
            # 归一化错误文案，便于分类与后续展示
            if result.get("error") and not _looks_like_attempt_summary(result["error"]):
                result["error"] = _normalize_error_text(result["error"]) or result["error"]
            last_result = result
            # auth/quota/rate 不重试（重试也没用）；连接类错误同理
            error_type = _classify_error(result.get("error", ""))
            if error_type in (
                "auth_error", "rate_limited", "quota_error",
                "dns_failure", "connection_refused", "network_unreachable",
            ):
                break

        except Exception as e:
            last_result = {"success": False, "error": _normalize_error_text(e) or str(e)}
            error_type = _classify_error(last_result["error"])
            if error_type in ("auth_error", "rate_limited", "dns_failure", "quota_error"):
                break

    latency = int((time.time() - start_time) * 1000)

    if last_result and last_result.get("success"):
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
            error_msg = _build_error_detail("API 可达但无 AI 回复内容", "empty_response")
            log("err", f"✗ {latency}ms · {error_msg}")
            db.update_provider_status(
                provider_id,
                status="fail",
                latency=latency,
                detail=error_msg,
            )
            db.add_test_history(provider_id, "fail", latency, "empty_response", error_msg, mode)
            return {
                "success": False,
                "status": "fail",
                "detail": error_msg,
                "error_type": "empty_response",
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
        error = (last_result or {}).get("error", "连接失败") or "连接失败"
        if not _looks_like_attempt_summary(error):
            error = _normalize_error_text(error) or error
        error_type = _classify_error(error)
        detail_msg = _build_error_detail(error, error_type)
        # CLI：主因一行；若摘要含多段，拆成主因 + 次要信息
        if " | " in detail_msg:
            parts = [p.strip() for p in detail_msg.split(" | ") if p.strip()]
            log("err", f"✗ {latency}ms · {parts[0]}")
            for extra in parts[1:3]:
                log("info", f"  ↳ 其他尝试: {extra}")
        else:
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
            # Gemini 认证只走 x-goog-api-key 头，不把 Key 拼进 URL
            # （URL 会进代理/上游访问日志）
            if fmt_key == "gemini_native" and api_key:
                headers["x-goog-api-key"] = api_key

            for url in paths:
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

    # 构建认证方式（带名称，便于 CLI 标注失败路径）
    auth_list = []
    if api_key:
        auth_list.append(("Bearer", {"Authorization": f"Bearer {api_key}"}))
        auth_list.append(("x-api-key", {"x-api-key": api_key}))
    else:
        auth_list.append(("none", {}))

    ctx = _create_ssl_context()
    timeout = _get_timeout()
    all_404 = True
    last_error = ""
    attempt_errors = []

    def _path_tail(url: str) -> str:
        try:
            p = urllib.parse.urlparse(url).path or url
            return p if p.startswith("/") else f"/{p}"
        except Exception:
            return url

    def _label(url: str, auth_name: str) -> str:
        return f"GET {_path_tail(url)} | auth={auth_name}"

    for auth_name, auth_h in auth_list:
        headers = {"User-Agent": _USER_AGENT, "anthropic-version": "2023-06-01"}
        headers.update(auth_h)

        for url in paths:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    if response.status == 200:
                        # 200 不代表可用:Cloudflare 挑战页/登录页也返回 200。
                        # 必须能解析出 JSON 才判定正常，否则按无效响应处理
                        try:
                            parsed = json.loads(body)
                            if isinstance(parsed, (dict, list)):
                                return {"success": True, "detail": "正常"}
                        except (ValueError, TypeError):
                            pass
                        err = "响应非 JSON（可能是网页/挑战页）"
                        last_error = err
                        attempt_errors.append(f"[{_label(url, auth_name)}] {err}")
                        all_404 = False
                        continue
                    err = _format_http_error(response.status, body[:800])
                    last_error = err
                    attempt_errors.append(f"[{_label(url, auth_name)}] {err}")
                    all_404 = False
            except urllib.error.HTTPError as e:
                body = _read_http_error_body(e)
                err = _format_http_error(e.code, body)
                if e.code != 404:
                    all_404 = False
                    # 认证/限流类错误：直接返回，避免被后续 404 覆盖
                    if e.code in (401, 403, 429):
                        return {"success": False, "error": f"[{_label(url, auth_name)}] {err}"}
                last_error = err
                attempt_errors.append(f"[{_label(url, auth_name)}] {err}")
            except urllib.error.URLError as e:
                err = _normalize_error_text(e.reason if e.reason is not None else e) or str(e.reason)
                return {"success": False, "error": f"[{_label(url, auth_name)}] {err}"}
            except Exception as e:
                err = _normalize_error_text(e) or str(e)
                return {"success": False, "error": f"[{_label(url, auth_name)}] {err}"}

    # 所有路径都 404 → 该提供商不支持 /models，用 HEAD 请求做纯连通性检测
    if all_404:
        return _test_connectivity_fallback(base, api_key)

    if attempt_errors:
        return {"success": False, "error": _summarize_attempt_errors(attempt_errors)}
    if last_error:
        return {"success": False, "error": last_error if _looks_like_attempt_summary(last_error) else (_normalize_error_text(last_error) or last_error)}
    return {"success": False, "error": "连接失败"}


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
            # 若是明确鉴权失败，仍返回错误信息
            if e.code in (401, 403):
                body = _read_http_error_body(e)
                return {"success": False, "error": _format_http_error(e.code, body)}
            return {"success": True, "detail": "端点可达(无 /models)"}
        body = _read_http_error_body(e)
        return {"success": False, "error": _format_http_error(e.code, body)}
    except urllib.error.URLError as e:
        return {"success": False, "error": _normalize_error_text(e.reason if e.reason is not None else e) or str(e.reason)}
    except Exception as e:
        return {"success": False, "error": _normalize_error_text(e) or str(e)}


def _test_chat_endpoint(endpoint: str, api_key: str, model: str = "", app_type: str = "claude", api_format: str = "") -> Dict[str, any]:
    """测试聊天端点（完整测试）
    手动指定 api_format 时优先尝试该格式；否则按 app_type 优先级依次尝试:
      - claude: Anthropic Messages → OpenAI Chat Completions → OpenAI Responses → Gemini
      - hermes/codex: OpenAI Chat Completions → OpenAI Responses → Anthropic Messages → Gemini
      - gemini: Gemini → OpenAI Chat Completions → OpenAI Responses → Anthropic Messages
    返回结果含 api_format 字段标识命中的格式。
    """

    if not (api_key or "").strip():
        # 缺 key 时不要误报「格式不匹配」
        return {"success": False, "error": "未配置 API Key"}

    auth_methods = []
    auth_methods.append(("Bearer", {"Authorization": f"Bearer {api_key}"}))
    auth_methods.append(("x-api-key", {"x-api-key": api_key}))

    attempt_errors = []
    # 死端点短路：DNS 解析失败/连接拒绝/网络不可达意味着同一 host 的
    # 所有格式×路径×认证组合都会失败，没必要按 30s 超时逐个耗完。
    # 连续超时 2 次同样视为端点不可用。
    _fatal = {"dead": False, "timeouts": 0}

    def _record_error(label: str, result: dict):
        err = (result or {}).get("error") or ""
        if not err:
            # 成功但无 snippet 也记一条，方便排查
            if result and result.get("success") and not result.get("response_snippet"):
                err = "HTTP 200 但无 AI 回复内容"
            else:
                return
        if not _looks_like_attempt_summary(err):
            err = _normalize_error_text(err) or err
        text = f"[{label}] {err}" if label else err
        attempt_errors.append(text)

        etype = _classify_error(err)
        if etype in ("dns_failure", "connection_refused", "network_unreachable"):
            _fatal["dead"] = True
        elif etype == "timeout":
            _fatal["timeouts"] += 1
            if _fatal["timeouts"] >= 2:
                _fatal["dead"] = True
        else:
            _fatal["timeouts"] = 0

    def _model_candidates(default_model: str) -> list:
        candidates = []
        seen = set()

        for item in (default_model or "").split(","):
            item = item.strip()
            if item and item not in seen:
                seen.add(item)
                candidates.append(item)
        return candidates

    def _try_anthropic_messages():
        model_names = _model_candidates(model) or ["claude-haiku-4-5"]
        paths = ["messages"] if endpoint.endswith("/v1") else ["v1/messages", "messages"]
        for claude_model in model_names:
            body = {"model": claude_model, "max_tokens": 32, "messages": [{"role": "user", "content": "你是谁呀，小朋友"}]}
            for path in paths:
                for auth_name, auth_h in auth_methods:
                    if _fatal["dead"]:
                        return None
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
                            _record_error(f"anthropic/{path}/{auth_name}/{claude_model}", result)
                        else:
                            _record_error(f"anthropic/{path}/{auth_name}/{claude_model}", result)
                    except Exception as e:
                        _record_error(f"anthropic/{path}/{auth_name}/{claude_model}", {"error": str(e)})
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
            if _fatal["dead"]:
                return None
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            path_tail = urllib.parse.urlparse(url).path or url
            try:
                result = _send_post_request(url, headers, body)
                if result["success"]:
                    snippet = _extract_response_snippet(result.get("body"), "auto")
                    if snippet:
                        return {"success": True, "detail": "AI 正常回复 (OpenAI)", "response_snippet": snippet, "api_format": "openai_chat"}
                    _record_error(f"openai_chat{path_tail}/{oai_model}", result)
                else:
                    _record_error(f"openai_chat{path_tail}/{oai_model}", result)
            except Exception as e:
                _record_error(f"openai_chat{path_tail}/{oai_model}", {"error": str(e)})
        return None

    def _try_openai_responses():
        oai_model = model if model else "gpt-4o-mini"
        body = {"model": oai_model, "input": "你是谁呀，小朋友"}
        paths = ["responses"]
        if not endpoint.endswith("/v1"):
            paths.append("v1/responses")
        for path in paths:
            if _fatal["dead"]:
                return None
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
                    _record_error(f"openai_responses/{path}/{oai_model}", result)
                else:
                    _record_error(f"openai_responses/{path}/{oai_model}", result)
            except Exception as e:
                _record_error(f"openai_responses/{path}/{oai_model}", {"error": str(e)})
        return None

    def _try_gemini():
        gemini_model = model if model else "gemini-2.0-flash"
        body = {"contents": [{"parts": [{"text": "你是谁呀，小朋友"}]}]}
        model_names = [gemini_model] if model else ["gemini-2.0-flash", "gemini-1.5-flash"]
        for m in model_names:
            if _fatal["dead"]:
                return None
            url = f"{endpoint}/models/{m}:generateContent"
            headers = {"Content-Type": "application/json"}
            if api_key:
                # 只用 header 认证，Key 不进 URL（避免落入代理/访问日志）
                headers["x-goog-api-key"] = api_key
            try:
                result = _send_post_request(url, headers, body)
                if result["success"]:
                    snippet = _extract_response_snippet(result.get("body"), "auto")
                    if snippet:
                        return {"success": True, "detail": "AI 正常回复 (Gemini)", "response_snippet": snippet, "api_format": "gemini_native"}
                    _record_error(f"gemini/models/{m}:generateContent", result)
                else:
                    _record_error(f"gemini/models/{m}:generateContent", result)
            except Exception as e:
                _record_error(f"gemini/models/{m}:generateContent", {"error": str(e)})
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
        if _fatal["dead"]:
            break
        result = format_funcs[fmt_key]()
        if result:
            return result

    # 把真实上游错误带到外层，而不是只剩“所有 API 格式均无法匹配”
    detail = _summarize_attempt_errors(attempt_errors)
    if detail and detail != "所有 API 格式均无法匹配":
        return {"success": False, "error": detail}
    return {"success": False, "error": "所有 API 格式均无法匹配（无具体上游错误，请检查端点/密钥/模型）"}


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
            body = _read_http_error_body(e)
            return None, _format_http_error(e.code, body)
        except urllib.error.URLError as e:
            return None, _normalize_error_text(e.reason if e.reason is not None else e) or str(e.reason)
        except Exception as e:
            return None, _normalize_error_text(e) or str(e)

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
    """发送 POST 请求,返回包含响应体；失败时尽量带回上游错误正文"""
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
                # 部分中继会用 200 返回业务错误 JSON
                try:
                    parsed = json.loads(body_text) if body_text else None
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    # OpenAI 风格: {"error": {...}} 且无 choices/content
                    if parsed.get("error") and not any(k in parsed for k in ("choices", "content", "output", "candidates")):
                        return {"success": False, "error": _format_http_error(200, body_text), "body": body_text}
                return {"success": True, "body": body_text}
            else:
                return {"success": False, "error": _format_http_error(response.status, body_text), "body": body_text}
    except urllib.error.HTTPError as e:
        body_text = _read_http_error_body(e)
        # 401/403：对完整聊天测试应视为失败并返回上游 message，
        # 这样 UI 能显示真实鉴权原因，而不是“格式不匹配”。
        # 注意：此前把 401/403 当 success 会掩盖 key 问题，且 full 模式会落到 no_content。
        return {
            "success": False,
            "error": _format_http_error(e.code, body_text),
            "body": body_text,
            "http_status": e.code,
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "error": _normalize_error_text(e.reason if e.reason is not None else e) or str(e.reason),
        }
    except Exception as e:
        return {"success": False, "error": _normalize_error_text(e) or str(e)}


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
    total = len(providers)

    def _test_one(provider):
        # 检查停止信号
        if stop_event and stop_event.is_set():
            return None

        pid = provider["id"]
        db.update_provider_status(pid, status="testing", detail="测试中...")

        # 回调（含 failover 检查）异常不能吞掉本 provider 的测试结果
        if callback:
            try:
                callback(pid, {"status": "testing"})
            except Exception:
                logger.exception(f"testing callback failed for provider {pid}")

        result = test_provider(pid, mode, log_callback=log_callback)
        result["id"] = pid

        if callback:
            try:
                callback(pid, result)
            except Exception:
                logger.exception(f"result callback failed for provider {pid}")

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
                    logger.exception(
                        f"test task failed for provider {futures[future].get('id')}"
                    )

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    fail_count = sum(1 for r in results if r.get("status") == "fail")

    return {
        "total": len(results),
        "ok": ok_count,
        "fail": fail_count,
        "results": results,
    }
