"""Windows 开机自启（当前用户 Run 注册表）。"""

from __future__ import annotations

import os
import sys
from typing import Optional

APP_NAME = "CC-Switch-Monitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe_command() -> str:
    """返回写入注册表的启动命令。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # 开发模式：用当前解释器启动 main.py
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{sys.executable}" "{main_py}"'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False


def set_enabled(enabled: bool) -> dict:
    """启用/禁用开机自启。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _exe_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return {"ok": True, "enabled": bool(enabled)}
    except Exception as e:
        return {"ok": False, "error": str(e), "enabled": is_enabled()}


def get_status() -> dict:
    return {"enabled": is_enabled(), "command": _exe_command() if is_enabled() else ""}
