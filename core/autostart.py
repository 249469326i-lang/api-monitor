"""Windows 开机自启（当前用户 Run 注册表）。"""

from __future__ import annotations

import os
import sys
from typing import Optional

# 启动项显示名；LEGACY 仅用于识别/清理旧版本写入的键，避免丢自启状态
APP_NAME = "API Monitor"
LEGACY_APP_NAMES = ("CC-Switch-Monitor",)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe_command() -> str:
    """返回写入注册表的启动命令。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # 开发模式：用当前解释器启动 main.py
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{sys.executable}" "{main_py}"'


def _value_exists(key, name: str) -> bool:
    try:
        import winreg
        winreg.QueryValueEx(key, name)
        return True
    except FileNotFoundError:
        return False


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            if _value_exists(key, APP_NAME):
                return True
            return any(_value_exists(key, legacy) for legacy in LEGACY_APP_NAMES)
    except Exception:
        return False


def set_enabled(enabled: bool) -> dict:
    """启用/禁用开机自启。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _exe_command())
                # 清理旧键，避免启动项里同时出现两个名字
                for legacy in LEGACY_APP_NAMES:
                    try:
                        winreg.DeleteValue(key, legacy)
                    except FileNotFoundError:
                        pass
            else:
                for name in (APP_NAME, *LEGACY_APP_NAMES):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        return {"ok": True, "enabled": bool(enabled)}
    except Exception as e:
        return {"ok": False, "error": str(e), "enabled": is_enabled()}


def get_status() -> dict:
    return {"enabled": is_enabled(), "command": _exe_command() if is_enabled() else ""}
