"""
API Monitor - Web Version
Entry point for PyWebView application

引导层：只负责日志、单实例、窗口创建与 webview.start。
业务 API 在 core.app_api，Win32 窗口细节在 core.win32_window。
"""

import os
import sys
import threading
import time
from pathlib import Path

import webview

# pywebview 6.x + WebView2 的 util.get_functions() 会递归遍历 js_api 对象
# 的所有公开属性。如果 API 类持有 window 引用(公开属性),它会遍历到
# native.AccessibilityObject.Bounds.Empty.Empty... 导致无限递归卡死。
# 修复: 将 window 引用改为 _window (下划线前缀属性会被 get_functions 跳过)
sys.setrecursionlimit(10000)

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db, win32_window
from core.app_api import API
from core.logging_config import setup_logging
from core.version import __version__, APP_REPO
import logging

logger = logging.getLogger(__name__)

WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 780


def main():
    setup_logging()
    if not win32_window.ensure_single_instance():
        logger.info("API Monitor is already running; exiting duplicate instance")
        return
    logger.info(f"API Monitor v{__version__} starting...")

    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    web_path = os.path.join(base_path, 'web')
    html_path = os.path.join(web_path, 'index.html')
    if not os.path.isfile(html_path):
        logger.error(f"Frontend not found: {html_path}")
        raise FileNotFoundError(f"Frontend not found: {html_path}")
    # 用 file:// 加载；不要用 html=（WebView2 下 css/js 相对路径会失效导致无样式）
    # 注意：WebView2 的 file:// 不支持 URL query（?v=...），附加后会 ERR_FILE_NOT_FOUND
    # 前端缓存刷新请改 index.html 内 css/js 的 ?v= 版本号
    html_url = Path(html_path).resolve().as_uri()
    logger.info(f"Loading UI: {html_url}")

    api = API()

    x, y = win32_window.get_centered_window_position(WINDOW_WIDTH, WINDOW_HEIGHT)

    window = webview.create_window(
        f'API Monitor v{__version__}',
        html_url,
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        x=x,
        y=y,
        min_size=(900, 580),
        background_color='#12141f',
        text_select=True,
        frameless=True,
        easy_drag=False,
    )

    api.set_window(window)

    # 关窗默认进托盘
    try:
        window.events.closing += api.on_window_closing
    except Exception as e:
        logger.warning(f"bind closing event failed: {e}")

    def _on_started():
        try:
            # 无边框窗口补 WS_MINIMIZEBOX，使任务栏图标可再次点击最小化
            if not api._ensure_taskbar_minimize():
                def _retry_taskbar_style():
                    for _ in range(10):
                        time.sleep(0.3)
                        if api._ensure_taskbar_minimize():
                            return
                threading.Thread(
                    target=_retry_taskbar_style,
                    daemon=True,
                    name="TaskbarMinimizeStyle",
                ).start()
            # 默认启用托盘（可设置关闭）
            minimize_to_tray = db.get_setting("minimize_to_tray")
            if minimize_to_tray not in ("false", "0"):
                api.start_tray()
            api._setup_auto_backup_timer()
            api._push_scheduler_tick()
        except Exception as e:
            logger.warning(f"post-start init failed: {e}")
        # 原有居中逻辑
        win32_window.center_window(window)

    # 独立存储目录，避免沿用旧 WebView2 缓存导致前端文案不更新
    from core import paths
    try:
        storage_path = os.path.join(paths.get_data_dir(), 'webview2')
        os.makedirs(storage_path, exist_ok=True)
    except Exception:
        storage_path = None

    try:
        webview.start(
            _on_started,
            debug=False,
            private_mode=True,
            storage_path=storage_path,
        )
    except TypeError:
        # 旧版 pywebview 不支持 storage_path
        try:
            webview.start(_on_started, debug=False, private_mode=True)
        except Exception:
            webview.start(_on_started, gui="mshtml", debug=False)
    except Exception:
        try:
            webview.start(_on_started, gui="mshtml", debug=False)
        except Exception:
            raise


if __name__ == '__main__':
    main()
