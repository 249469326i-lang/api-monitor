"""
日志配置模块 - RotatingFileHandler + API Key 脱敏
"""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(level: int = logging.INFO) -> None:
    """
    配置应用日志

    日志文件: <数据目录>\\app.log（core.paths 管理，默认 %APPDATA%\\.api-monitor）
    轮转策略: 5MB x 3 份
    """
    from . import paths
    log_dir = paths.get_data_dir()
    log_path = os.path.join(log_dir, "app.log")

    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler
    if not root.handlers:
        root.addHandler(handler)


def mask_key(api_key: str) -> str:
    """
    API Key 脱敏：只显示前 4 位和后 4 位

    Examples:
        sk-abcdefgh1234 -> sk-a...1234
        short        -> ****
        ""           -> <empty>
    """
    if not api_key:
        return "<empty>"
    if len(api_key) <= 8:
        return "****"
    return api_key[:4] + "..." + api_key[-4:]
