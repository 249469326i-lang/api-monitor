"""系统托盘：关窗隐藏到托盘，托盘菜单控制显示/退出/快速测试。"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Optional


def _resource_path(*parts: str) -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, *parts)


def _load_icon():
    """优先用 assets/icon.ico，否则生成简单图标。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    ico = _resource_path("assets", "icon.ico")
    if os.path.isfile(ico):
        try:
            return Image.open(ico)
        except Exception:
            pass

    # 简单生成：深色底 + 绿色圆点
    size = 64
    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)
    margin = 10
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(34, 197, 94, 255))
    return img


class TrayController:
    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        on_quick_test: Optional[Callable[[], None]] = None,
        tooltip: str = "CC Switch Monitor",
    ):
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_quick_test = on_quick_test
        self._tooltip = tooltip
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            if self._started or self._icon is not None:
                return True
            try:
                import pystray
                from pystray import MenuItem as item
            except Exception as e:
                print(f"[tray] pystray unavailable: {e}")
                return False

            image = _load_icon()
            if image is None:
                print("[tray] no icon image available")
                return False

            menu_items = [
                item("显示主窗口", self._handle_show, default=True),
            ]
            if self._on_quick_test:
                menu_items.append(item("快速测试全部", self._handle_quick_test))
            menu_items.append(item("退出", self._handle_quit))

            icon = pystray.Icon(
                "api_monitor_cc_switch",
                image,
                self._tooltip,
                pystray.Menu(*menu_items),
            )
            self._icon = icon
            self._started = True

            def _run():
                try:
                    icon.run()
                except Exception as e:
                    print(f"[tray] icon.run error: {e}")
                finally:
                    with self._lock:
                        if self._icon is icon:
                            self._icon = None
                        self._started = False

            self._thread = threading.Thread(target=_run, daemon=True, name="TrayIcon")
            self._thread.start()
            return True

    def stop(self):
        with self._lock:
            icon = self._icon
            self._icon = None
            self._started = False
        if icon is not None:
            try:
                icon.visible = False
            except Exception:
                pass
            try:
                icon.stop()
            except Exception:
                pass

    def notify(self, title: str, message: str):
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    def _handle_show(self, icon=None, item=None):
        try:
            self._on_show()
        except Exception as e:
            print(f"[tray] show error: {e}")

    def _handle_quit(self, icon=None, item=None):
        try:
            self._on_quit()
        except Exception as e:
            print(f"[tray] quit error: {e}")

    def _handle_quick_test(self, icon=None, item=None):
        if not self._on_quick_test:
            return
        try:
            self._on_quick_test()
        except Exception as e:
            print(f"[tray] quick test error: {e}")
