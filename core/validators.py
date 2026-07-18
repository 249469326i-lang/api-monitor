"""
输入校验模块 - 对前端提交的数据进行格式和长度校验
"""

import re
from typing import Tuple
from urllib.parse import urlparse


def validate_endpoint(url: str) -> Tuple[bool, str]:
    """校验端点 URL"""
    if not url:
        return False, "端点 URL 不能为空"
    if len(url) > 500:
        return False, "端点 URL 长度不能超过 500"
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, "端点 URL 必须是 http 或 https 协议"
        if not parsed.netloc:
            return False, "端点 URL 格式无效"
    except Exception:
        return False, "端点 URL 格式无效"
    return True, ""


def validate_name(name: str) -> Tuple[bool, str]:
    """校验名称"""
    if not name or not name.strip():
        return False, "名称不能为空"
    if len(name) > 100:
        return False, "名称长度不能超过 100"
    return True, ""


def validate_api_key(key: str) -> Tuple[bool, str]:
    """校验 API Key"""
    if len(key) > 2000:
        return False, "API Key 长度不能超过 2000"
    return True, ""


VALID_API_FORMATS = {
    "",
    "anthropic_messages",
    "openai_chat",
    "openai_responses",
    "gemini_native",
}


def validate_api_format(api_format: str) -> Tuple[bool, str]:
    """校验 API 格式标识。空字符串表示自动检测。"""
    if api_format is None:
        return True, ""
    if api_format not in VALID_API_FORMATS:
        return False, "API 格式无效"
    return True, ""


def validate_model(model: str) -> Tuple[bool, str]:
    """校验模型名，支持逗号分隔的候选模型列表"""
    if not model:
        return True, ""  # 可选字段
    if len(model) > 500:
        return False, "模型名长度不能超过 500"

    parts = [item.strip() for item in model.split(",")]
    if not any(parts):
        return True, ""

    for item in parts:
        if not item:
            continue
        if not re.match(r'^[a-zA-Z0-9._:/@-]+$', item):
            return False, "模型名只能包含字母、数字、点、短横线、冒号、斜杠、@ 和下划线；多个模型用逗号分隔"
    return True, ""


def validate_provider(data: dict, is_update: bool = False) -> Tuple[bool, str]:
    """
    聚合校验供应商数据

    Args:
        data: 供应商数据字典
        is_update: 是否为更新操作（更新时部分字段可选）

    Returns:
        (是否通过, 错误信息)
    """
    # 名称校验
    if not is_update or "name" in data:
        ok, err = validate_name(data.get("name", ""))
        if not ok:
            return False, err

    # 端点校验
    if not is_update or "endpoint" in data:
        endpoint = data.get("endpoint", "")
        if endpoint:  # 更新时端点可以为空（表示不修改）
            ok, err = validate_endpoint(endpoint)
            if not ok:
                return False, err

    # API Key 校验
    if "api_key" in data:
        ok, err = validate_api_key(data.get("api_key", ""))
        if not ok:
            return False, err

    # 模型名校验
    if "default_model" in data:
        ok, err = validate_model(data.get("default_model", ""))
        if not ok:
            return False, err

    # API 格式校验
    if "api_format" in data:
        ok, err = validate_api_format(data.get("api_format", ""))
        if not ok:
            return False, err

    return True, ""
