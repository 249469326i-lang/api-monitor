"""
API Key 加密模块 - 基于 Windows DPAPI

使用 Windows 当前用户凭据加密/解密 API Key。
加密后的 Key 以 "enc:" 前缀 + Base64 存储在数据库中。
非 Windows 平台自动降级为明文存储。
"""

import base64
import sys
from typing import Optional

_ENCRYPTED_PREFIX = "enc:"


def is_encrypted(value: str) -> bool:
    """检查值是否已被加密"""
    return bool(value) and value.startswith(_ENCRYPTED_PREFIX)


def encrypt_key(plaintext: str) -> str:
    """
    使用 DPAPI 加密 API Key

    Returns:
        加密后的字符串 "enc:" + base64
        非 Windows 平台返回原文
    """
    if not plaintext:
        return plaintext

    if sys.platform != "win32":
        return plaintext

    try:
        import ctypes
        import ctypes.wintypes

        class _DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        data = plaintext.encode("utf-8")
        blob_in = _DATA_BLOB(len(data), ctypes.create_string_buffer(data, len(data)))
        blob_out = _DATA_BLOB()

        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return plaintext

        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

        return _ENCRYPTED_PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        return plaintext


def decrypt_key(stored: str) -> str:
    """
    解密 DPAPI 加密的 API Key

    自动检测：如果没有 "enc:" 前缀则视为明文直接返回（兼容迁移期）

    Returns:
        解密后的明文
        非 Windows 平台返回原文
    """
    if not stored:
        return stored

    if not is_encrypted(stored):
        return stored

    if sys.platform != "win32":
        return stored

    try:
        import ctypes
        import ctypes.wintypes

        class _DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        raw = base64.b64decode(stored[len(_ENCRYPTED_PREFIX):])
        blob_in = _DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        blob_out = _DATA_BLOB()

        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return stored

        decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

        return decrypted.decode("utf-8")
    except Exception:
        return stored


def migrate_provider_keys(providers: list, update_fn) -> int:
    """
    自动迁移明文 API Key 为加密格式

    Args:
        providers: 供应商列表（含明文 api_key）
        update_fn: 更新函数 update_fn(provider_id, api_key_encrypted)

    Returns:
        迁移的 Key 数量
    """
    migrated = 0
    for p in providers:
        key = p.get("api_key", "")
        if key and not is_encrypted(key):
            encrypted = encrypt_key(key)
            if encrypted != key:
                update_fn(p["id"], encrypted)
                migrated += 1
    return migrated
