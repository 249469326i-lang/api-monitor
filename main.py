"""
API Monitor - Web Version
Entry point for PyWebView application
v3.0.0 - 商业增值功能
"""

import os
import re
from pathlib import Path
import sys
import threading
import time
import json
import ctypes
import atexit
import webview

# pywebview 6.x + WebView2 的 util.get_functions() 会递归遍历 js_api 对象
# 的所有公开属性。如果 API 类持有 window 引用(公开属性),它会遍历到
# native.AccessibilityObject.Bounds.Empty.Empty... 导致无限递归卡死。
# 修复: 将 window 引用改为 _window (下划线前缀属性会被 get_functions 跳过)
sys.setrecursionlimit(10000)

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db, testing, providers, failover, notifications, scheduler, export
from core import autostart, tray as tray_mod
from core.logging_config import setup_logging, mask_key
from core.validators import validate_provider
import logging

logger = logging.getLogger(__name__)

__version__ = "3.0.2"
APP_REPO = "https://github.com/249469326i-lang/api-monitor"

WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 780
_SINGLE_INSTANCE_MUTEX = None


def ensure_single_instance() -> bool:
    """Prevent multiple app processes from creating duplicate tray icons."""
    global _SINGLE_INSTANCE_MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    mutex = kernel32.CreateMutexW(None, False, "Global\\API_Monitor_CC_Switch_Monitor")
    if not mutex:
        return True
    already_exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    if already_exists:
        try:
            kernel32.CloseHandle(mutex)
        except Exception:
            pass
        return False
    _SINGLE_INSTANCE_MUTEX = mutex

    def _release_mutex():
        global _SINGLE_INSTANCE_MUTEX
        if _SINGLE_INSTANCE_MUTEX:
            try:
                kernel32.CloseHandle(_SINGLE_INSTANCE_MUTEX)
            except Exception:
                pass
            _SINGLE_INSTANCE_MUTEX = None

    atexit.register(_release_mutex)
    return True


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def get_centered_window_position(width, height):
    """Return coordinates that center the window in the primary work area."""
    work_area = RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
        screen_width = work_area.right - work_area.left
        screen_height = work_area.bottom - work_area.top
        return (
            work_area.left + max((screen_width - width) // 2, 0),
            work_area.top + max((screen_height - height) // 2, 0),
        )

    screen_width = ctypes.windll.user32.GetSystemMetrics(0)
    screen_height = ctypes.windll.user32.GetSystemMetrics(1)
    return (max((screen_width - width) // 2, 0), max((screen_height - height) // 2, 0))


class API:
    """Backend API exposed to frontend"""

    def __init__(self):
        self._window = None  # 必须用下划线前缀,防止 pywebview 递归遍历
        self._testing = False
        self._testing_lock = threading.Lock()  # 保护 _testing 状态
        self._test_thread = None
        self._stop_event = threading.Event()  # 替代原 _stop_flag 布尔值
        self._tray = None
        self._force_quit = False
        self._closing = False
        self._auto_backup_timer = None

        # Initialize database
        db.init_db()

        # 启动时从 ~/.claude/settings.json 同步当前配置
        providers.sync_current_from_settings()

        # 启动时自动检测 Claude Code 当前生效配置,未匹配则新增为供应商
        if db.get_setting("auto_sync_claude_on_startup") == "1":
            try:
                providers.auto_detect_current_provider()
            except Exception:
                pass

        # 初始化定时测试调度器
        self._scheduler = scheduler.Scheduler(
            test_callback=self._run_scheduled_test,
            log_callback=self._push_cli_log,
            status_callback=self._push_scheduler_tick,
        )

        # 启动时自动开始定时测试（如果配置了间隔）
        interval = int(db.get_setting("auto_test_interval") or "0")
        if interval > 0:
            self._scheduler.start(interval)

        # 启动时清理过期数据
        try:
            days = int(db.get_setting("history_retention_days") or "30")
            db.cleanup_old_history(days)
            db.cleanup_old_notifications(days)
        except Exception:
            pass

        # 自动备份定时器
        self._setup_auto_backup_timer()

    def close_window(self):
        """标题栏关闭：默认隐藏到托盘；托盘不可用时真正退出。"""
        self.hide_to_tray()

    def quit_app(self):
        """真正退出程序（托盘菜单 / 强制退出）"""
        self._force_quit = True
        self._closing = True
        try:
            if self._auto_backup_timer:
                self._auto_backup_timer.cancel()
        except Exception:
            pass
        try:
            self._scheduler.stop()
        except Exception:
            pass
        try:
            if self._tray:
                self._tray.stop()
                self._tray = None
        except Exception:
            pass
        try:
            if self._window:
                self._window.destroy()
        except Exception:
            pass

    def hide_to_tray(self):
        """隐藏主窗口到托盘"""
        if not self._window:
            return {"success": False}
        try:
            if db.get_setting("minimize_to_tray") in ("false", "0"):
                self.quit_app()
                return {"success": True, "hidden": False}
        except Exception:
            pass
        # 确保托盘已启动
        if not self._tray:
            self.start_tray()
        if self._tray:
            try:
                self._window.hide()
                return {"success": True, "hidden": True}
            except Exception as e:
                logger.warning(f"hide_to_tray failed: {e}")
        # 托盘不可用则直接退出
        self.quit_app()
        return {"success": True, "hidden": False}

    def show_window(self):
        """从托盘恢复主窗口"""
        if not self._window:
            return {"success": False}
        try:
            self._window.show()
            try:
                self._window.restore()
            except Exception:
                pass
            return {"success": True}
        except Exception as e:
            logger.warning(f"show_window failed: {e}")
            return {"success": False, "error": str(e)}

    def minimize_window(self):
        """最小化窗口"""
        try:
            self._window.minimize()
        except Exception:
            pass

    def maximize_window(self):
        """切换最大化"""
        try:
            self._window.toggle_fullscreen()
        except Exception:
            pass

    def _get_hwnd(self):
        """取 WinForms 原生句柄（兼容 32/64 位）。"""
        if not self._window:
            return None
        native = getattr(self._window, "native", None)
        if native is None:
            return None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        # Prefer ToInt64 to avoid truncation on 64-bit; fall back to ToInt32/int.
        try:
            return int(handle.ToInt64())
        except Exception:
            try:
                return int(handle.ToInt32())
            except Exception:
                try:
                    return int(handle)
                except Exception:
                    return None

    def _run_on_ui(self, fn):
        """把 Win32 调用切到 UI 线程执行（WebView2 桥接线程上 ReleaseCapture 会失败）。"""
        native = getattr(self._window, "native", None) if self._window else None
        if native is not None and hasattr(native, "Invoke") and hasattr(native, "InvokeRequired"):
            try:
                if native.InvokeRequired:
                    native.Invoke(fn)
                    return
            except Exception:
                pass
        fn()

    def start_window_drag(self):
        """无边框窗口：由标题栏空白处 mousedown 触发原生拖拽。

        调用 Win32 ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION)，让系统接管
        拖拽循环。比 JS 手动 move 平滑，且支持 Win11 贴靠布局。仅在 Windows 下生效。
        必须在 UI 线程执行，否则 ReleaseCapture 无效。
        """
        try:
            hwnd = self._get_hwnd()
            if not hwnd:
                return

            def _drag():
                user32 = ctypes.windll.user32
                user32.ReleaseCapture.argtypes = []
                user32.ReleaseCapture.restype = ctypes.c_bool
                # HWND 用 c_size_t / c_void_p，避免 64 位截断
                user32.SendMessageW.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
                ]
                user32.SendMessageW.restype = ctypes.c_void_p
                WM_NCLBUTTONDOWN = 0x00A1
                HTCAPTION = 0x0002
                user32.ReleaseCapture()
                user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)

            self._run_on_ui(_drag)
        except Exception as e:
            logger.warning(f"start_window_drag failed: {e}")

    def move_window_to(self, x, y):
        """将窗口移动到屏幕坐标 (x, y)。JS 跟手拖拽兜底用。"""
        try:
            if self._window:
                self._window.move(int(x), int(y))
        except Exception as e:
            logger.warning(f"move_window_to failed: {e}")

    def move_window_by(self, dx, dy, absolute_from_mousedown=False):
        """按位移移动窗口。

        absolute_from_mousedown=True 时，dx/dy 视为相对 mousedown 起点的累计位移，
        后端用窗口起点 + 累计位移定位，避免逐帧误差累积。
        """
        try:
            if not self._window:
                return
            hwnd = self._get_hwnd()
            if hwnd is None:
                # 回退到 pywebview 高层 API（无起点缓存）
                try:
                    # window.x / window.y 可能可用
                    cx = int(getattr(self._window, "x", 0) or 0)
                    cy = int(getattr(self._window, "y", 0) or 0)
                    self._window.move(cx + int(dx), cy + int(dy))
                except Exception:
                    pass
                return

            user32 = ctypes.windll.user32
            rect = RECT()
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
            user32.GetWindowRect.restype = ctypes.c_bool
            if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
                return

            if absolute_from_mousedown:
                if not hasattr(self, "_drag_origin") or self._drag_origin is None:
                    self._drag_origin = (rect.left, rect.top)
                ox, oy = self._drag_origin
                nx, ny = ox + int(dx), oy + int(dy)
            else:
                nx, ny = rect.left + int(dx), rect.top + int(dy)

            def _move():
                try:
                    self._window.move(int(nx), int(ny))
                except Exception:
                    user32.SetWindowPos.argtypes = [
                        ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint
                    ]
                    user32.SetWindowPos.restype = ctypes.c_bool
                    SWP_NOSIZE = 0x0001
                    SWP_NOZORDER = 0x0004
                    user32.SetWindowPos(
                        ctypes.c_void_p(hwnd), None, int(nx), int(ny), 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER
                    )

            self._run_on_ui(_move)
        except Exception as e:
            logger.warning(f"move_window_by failed: {e}")

    def end_window_drag(self):
        """结束 JS 跟手拖拽，清理起点缓存。"""
        self._drag_origin = None

    def set_window(self, window):
        self._window = window
        self._drag_origin = None

    def start_tray(self):
        """启动系统托盘"""
        if self._tray:
            return True

        def on_show():
            self.show_window()

        def on_quit():
            self.quit_app()

        def on_quick_test():
            try:
                self.show_window()
                self.test_all("fast")
            except Exception as e:
                logger.warning(f"tray quick test failed: {e}")

        controller = tray_mod.TrayController(
            on_show=on_show,
            on_quit=on_quit,
            on_quick_test=on_quick_test,
            tooltip=f"API Monitor v{__version__}",
        )
        if controller.start():
            self._tray = controller
            logger.info("System tray started")
            return True
        logger.warning("System tray failed to start")
        return False

    def on_window_closing(self):
        """pywebview closing 事件：默认拦截并隐藏到托盘。"""
        if self._force_quit or self._closing:
            return True
        # 用户关闭“到托盘”时直接退出
        try:
            if db.get_setting("minimize_to_tray") in ("false", "0"):
                self._closing = True
                try:
                    if self._tray:
                        self._tray.stop()
                        self._tray = None
                except Exception:
                    pass
                return True
        except Exception:
            pass
        # 返回 False 取消关闭
        try:
            if not self._tray:
                self.start_tray()
            if self._tray and self._window:
                self._window.hide()
                return False
        except Exception as e:
            logger.warning(f"on_window_closing hide failed: {e}")
        # 无法托盘化则允许退出
        self._closing = True
        try:
            if self._tray:
                self._tray.stop()
        except Exception:
            pass
        return True

    def get_version(self):
        """Return app version"""
        return __version__

    # ────────────────── 供应商 CRUD ──────────────────

    def get_providers(self):
        """Get all providers"""
        return providers.get_provider_list_formatted()

    def get_stats(self):
        """Get statistics"""
        return db.get_stats()

    def add_provider(self, data):
        """Add a new provider"""
        try:
            # 输入校验
            ok, err = validate_provider(data, is_update=False)
            if not ok:
                return {"success": False, "error": err}

            provider_id = db.add_provider(data)
            logger.info(f"Provider added: id={provider_id}, name={data.get('name')}")
            self._refresh_frontend()
            return {"success": True, "id": provider_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_provider(self, provider_id):
        """Delete a provider"""
        try:
            pid = int(provider_id)
            success = db.delete_provider(pid)
            if success:
                logger.info(f"Provider deleted: id={pid}")
            self._refresh_frontend()
            return {"success": success}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_provider(self, provider_id, provider_data):
        """Update a provider"""
        try:
            pid = int(provider_id)
            # 输入校验
            ok, err = validate_provider(provider_data, is_update=True)
            if not ok:
                return {"success": False, "error": err}

            success = db.update_provider(pid, provider_data)
            if success:
                logger.info(f"Provider updated: id={pid}")
            self._refresh_frontend()
            return {"success": success}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 测试 ──────────────────

    def fetch_models(self, endpoint, api_key="", default_model="", api_format=""):
        """从提供商端点获取可用模型列表"""
        try:
            target_base = endpoint.rstrip("/")

            # 只从本应用 DB 收集精确匹配端点的 default_model 和 api_format；
            # 不隐式合并 cc-switch/ccmesh 数据，避免同 base_url 的外部配置污染模型列表。
            all_models = []
            seen_models = set()
            api_format = (api_format or "").strip()

            def add_model_candidates(value):
                for m in (value or "").split(","):
                    m = m.strip()
                    if m and m not in seen_models:
                        seen_models.add(m)
                        all_models.append(m)

            add_model_candidates(default_model)

            try:
                providers = db.get_providers()
                for p in providers:
                    ep = p.get("endpoint", "").rstrip("/")
                    if ep == target_base:
                        add_model_candidates(p.get("default_model", ""))
                        if not api_format:
                            api_format = p.get("api_format", "")
                        if not api_key:
                            api_key = p.get("api_key", "")
            except Exception:
                pass

            merged_default_model = ",".join(all_models) if all_models else ""
            return testing.fetch_models(endpoint, api_key, api_format, merged_default_model)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_provider(self, provider_id):
        """Test a single provider (full mode: send Hi, verify AI response)"""
        try:
            pid = int(provider_id)
            old_provider = db.get_provider_by_id(pid)
            old_status = old_provider.get("status") if old_provider else None

            result = testing.test_provider(pid, "full", log_callback=self._push_cli_log)

            # 状态变化时发送通知
            new_status = result.get("status")
            if old_status and old_status != new_status and old_status != "pending":
                name = old_provider.get("name", "")
                if new_status == "fail":
                    notifications.notify("status_change", f"{name} 状态异常", result.get("detail", "测试失败"), pid)
                elif new_status == "ok" and old_status == "fail":
                    notifications.notify("status_change", f"{name} 恢复正常", f"延迟 {result.get('latency', '?')}ms", pid)

            # 测试失败且是当前供应商，尝试 Failover
            if new_status == "fail":
                fo_result = failover.check_and_failover(pid, log_callback=self._push_cli_log)
                if fo_result.get("switched"):
                    notifications.notify(
                        "failover", "自动故障切换",
                        f"从 [{fo_result['from']}] 切换到 [{fo_result['to']}]",
                        fo_result.get("to_id"),
                    )
                    self._push_failover_event(fo_result)

            self._refresh_frontend()
            self._push_unread_count()
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_all(self, mode="fast"):
        """Test all providers"""
        with self._testing_lock:
            if self._testing:
                return {"success": False, "error": "已有测试正在进行"}
            self._testing = True
        self._stop_event.clear()

        def run_tests():
            try:
                def callback(pid, result):
                    if self._stop_event.is_set():
                        return
                    self._push_single_update(pid)

                    # 测试失败且是当前供应商，尝试 Failover
                    if result.get("status") == "fail":
                        fo_result = failover.check_and_failover(pid, log_callback=self._push_cli_log)
                        if fo_result.get("switched"):
                            notifications.notify(
                                "failover", "自动故障切换",
                                f"从 [{fo_result['from']}] 切换到 [{fo_result['to']}]",
                                fo_result.get("to_id"),
                            )
                            self._push_failover_event(fo_result)

                testing.test_all_providers(
                    mode, callback=callback, log_callback=self._push_cli_log,
                    stop_event=self._stop_event,
                )
            finally:
                with self._testing_lock:
                    self._testing = False
                self._push_testing_complete()

                # 发送测试完成通知
                stats = db.get_stats()
                notifications.notify(
                    "test_complete", "批量测试完成",
                    f"共 {stats['total']} 个，正常 {stats['ok']}，失败 {stats['fail']}",
                )
                self._push_unread_count()

        self._test_thread = threading.Thread(target=run_tests, daemon=True)
        self._test_thread.start()

        return {"success": True, "message": f"已开始{mode}测试"}

    def stop_testing(self):
        """Stop ongoing tests"""
        self._stop_event.set()
        # 等待测试线程优雅退出
        if self._test_thread and self._test_thread.is_alive():
            self._test_thread.join(timeout=5)
        with self._testing_lock:
            self._testing = False
        return {"success": True}

    # ────────────────── 导入 / 启动 ──────────────────

    def get_about_info(self):
        """Return about info"""
        return {
            "name": "API Monitor",
            "version": __version__,
            "description": "Windows 桌面端 API 供应商监控与故障切换",
            "author": "API Monitor Project",
            "repo": APP_REPO,
        }

    def get_failover_status(self):
        """Failover 状态摘要"""
        try:
            return failover.get_status()
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    def reset_failover_counter(self):
        """重置 failover 计数/冷却"""
        try:
            failover.reset_counter()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def confirm_failover(self):
        """确认待执行的故障切换"""
        try:
            result = failover.confirm_pending(log_callback=self._push_cli_log)
            if result.get("switched"):
                self._push_failover_event(result)
                self._refresh_frontend()
            return result
        except Exception as e:
            return {"switched": False, "reason": str(e)}

    def cancel_failover(self):
        """取消待确认切换"""
        try:
            failover.cancel_pending()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_autostart(self):
        """开机自启状态"""
        try:
            return autostart.get_status()
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    def set_autostart(self, enabled):
        """设置开机自启"""
        try:
            result = autostart.set_enabled(bool(enabled))
            if result.get("ok"):
                db.set_setting("start_on_boot", "true" if enabled else "false")
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_auto_backup_now(self):
        """立即执行一次自动备份（尊重开关）"""
        try:
            return export.create_auto_backup()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def sync_claude_code_provider(self):
        """从 Claude Code 当前配置同步/导入供应商(主导入路径)"""
        try:
            result = providers.sync_claude_code_provider(force=True)
            # 同步 role 标记后,再按 settings 二次校准当前
            try:
                providers.sync_current_from_settings()
            except Exception:
                pass
            if result.get("success"):
                logger.info(
                    "Sync Claude Code provider: action=%s provider_id=%s",
                    result.get("action"),
                    result.get("provider_id"),
                )
                self._refresh_frontend()
            else:
                logger.warning("Sync Claude Code provider failed: %s", result.get("error") or result.get("reason"))
            return result
        except Exception as e:
            logger.exception("sync_claude_code_provider failed")
            return {"success": False, "action": "error", "error": str(e), "message": f"同步失败: {e}"}

    def import_from_ccswitch(self):
        """Import from cc-switch (secondary / advanced path)"""
        result = providers.import_from_ccswitch()
        if result.get("success"):
            logger.info(f"Import from cc-switch: {result.get('imported', 0)} imported, {result.get('skipped', 0)} skipped")
            self._refresh_frontend()
        return result

    def choose_and_import_from_file(self):
        """弹出文件选择对话框,让用户手动选 .db 文件导入"""
        if not self._window:
            return {"success": False, "error": "窗口未就绪"}

        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    ("SQLite Database", "*.db"),
                    ("SQLite Files", "*.sqlite"),
                    ("All Files", "*.*"),
                ),
            )
        except Exception as e:
            return {"success": False, "error": f"打开文件对话框失败: {e}"}

        if not result:
            return {"success": False, "error": "未选择文件"}

        path = result[0] if isinstance(result, (list, tuple)) else result
        r = providers.import_from_path(path)
        if r.get("success"):
            self._refresh_frontend()
        return r

    def launch_claude(self):
        """Launch Claude Code"""
        return providers.launch_claude_code()

    def set_current_provider(self, provider_id):
        """Set a provider as the current Claude Code config"""
        try:
            pid = int(provider_id)
            result = providers.set_current_provider(pid)
            if result.get("success"):
                failover.reset_counter()
                logger.info(f"Current provider set: id={pid}")
                self._refresh_frontend()
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 测试历史 ──────────────────

    def get_test_history(self, provider_id, range_hours=24):
        """获取指定供应商的测试历史"""
        try:
            pid = int(provider_id)
            since = int(time.time()) - int(range_hours) * 3600
            history = db.get_test_history(pid, since=since)
            return {"success": True, "data": history}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_history_stats(self, provider_id, range_hours=24):
        """获取指定供应商的统计指标"""
        try:
            pid = int(provider_id)
            since = int(time.time()) - int(range_hours) * 3600
            stats = db.get_history_stats(pid, since)
            return {"success": True, "data": stats}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_history_timeline(self, provider_id, range_hours=24):
        """获取延迟时间线数据"""
        try:
            pid = int(provider_id)
            since = int(time.time()) - int(range_hours) * 3600
            hours = int(range_hours)
            if hours <= 24:
                bucket = 300
            elif hours <= 168:
                bucket = 1800
            else:
                bucket = 7200
            timeline = db.get_history_timeline(pid, since, bucket)
            return {"success": True, "data": timeline}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 通知 ──────────────────

    def get_notifications(self, limit=50, unread_only=False):
        """获取通知列表"""
        try:
            data = db.get_notifications(limit=int(limit), unread_only=bool(unread_only))
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_unread_count(self):
        """获取未读通知数"""
        try:
            count = db.get_unread_count()
            return {"success": True, "count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mark_notifications_read(self, notification_ids=None):
        """标记通知为已读"""
        try:
            affected = db.mark_notifications_read(notification_ids)
            self._push_unread_count()
            return {"success": True, "affected": affected}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 设置 ──────────────────

    def get_all_settings(self):
        """获取所有设置"""
        try:
            settings = db.get_all_settings()
            try:
                settings["start_on_boot"] = "true" if autostart.is_enabled() else "false"
            except Exception:
                settings.setdefault("start_on_boot", "false")
            settings.setdefault("auto_backup_enabled", "false")
            settings.setdefault("auto_backup_interval_hours", "24")
            settings.setdefault("auto_backup_keep", "10")
            settings.setdefault("minimize_to_tray", "true")
            return {"success": True, "data": settings}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_settings(self):
        """兼容旧前端调用"""
        result = self.get_all_settings()
        if result.get("success"):
            return result.get("data", {})
        return {}

    def save_settings(self, settings_dict):
        """保存设置"""
        try:
            data = dict(settings_dict or {})
            bool_keys = {
                "failover_enabled", "failover_need_confirm",
                "notify_status_change", "notify_failover", "notify_test_complete",
                "ssl_verify", "auto_update", "auto_backup_enabled",
                "start_on_boot", "minimize_to_tray",
            }
            for k in list(data.keys()):
                if k in bool_keys:
                    v = data[k]
                    if isinstance(v, bool):
                        data[k] = "true" if v else "false"
                    elif str(v).lower() in ("1", "true", "yes", "on"):
                        data[k] = "true"
                    else:
                        data[k] = "false"

            db.set_settings_batch(data)

            if "auto_test_interval" in data:
                interval = int(data["auto_test_interval"] or 0)
                if interval > 0:
                    self._scheduler.start(interval)
                else:
                    self._scheduler.stop()

            if "start_on_boot" in data:
                autostart.set_enabled(data["start_on_boot"] == "true")

            if any(k in data for k in ("auto_backup_enabled", "auto_backup_interval_hours", "auto_backup_keep")):
                self._setup_auto_backup_timer()

            logger.info(f"Settings saved: {list(data.keys())}")
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 自动更新 ──────────────────

    def check_update(self):
        """检查是否有新版本"""
        try:
            from core import updater
            result = updater.check_for_update(__version__)
            if result.get("has_update"):
                logger.info(f"Update available: {result.get('latest_version')} (current: {__version__})")
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 定时测试 ──────────────────

    def get_scheduler_status(self):
        """获取调度器状态"""
        status = self._scheduler.get_status()
        try:
            status = dict(status)
            status["failover"] = failover.get_status()
        except Exception:
            pass
        return {"success": True, "data": status}

    def start_scheduler(self, interval_seconds=0):
        """启动定时测试"""
        try:
            interval = int(interval_seconds or 0)
            if interval > 0:
                self._scheduler.start(interval)
                db.set_setting("auto_test_interval", str(interval))
            else:
                self._scheduler.stop()
                db.set_setting("auto_test_interval", "0")
            return {"success": True, "data": self._scheduler.get_status()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_scheduler(self):
        """停止定时测试"""
        self._scheduler.stop()
        db.set_setting("auto_test_interval", "0")
        return {"success": True, "data": self._scheduler.get_status()}

    # ────────────────── 导出 & 备份 ──────────────────

    def export_providers(self, format="csv", include_api_key=False):
        """导出供应商列表"""
        try:
            if not self._window:
                return {"success": False, "error": "窗口未就绪"}

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"providers_{time.strftime('%Y%m%d')}.{'csv' if format == 'csv' else 'json'}",
                file_types=(
                    ("CSV Files", "*.csv") if format == "csv" else ("JSON Files", "*.json"),
                ),
            )
            if not result:
                return {"success": False, "error": "未选择保存位置"}

            path = result if isinstance(result, str) else result[0]
            if format == "csv":
                return export.export_providers_csv(path, include_api_key)
            else:
                return export.export_providers_json(path, include_api_key)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def export_history(self, provider_id=0, range_hours=720):
        """导出测试历史"""
        try:
            if not self._window:
                return {"success": False, "error": "窗口未就绪"}

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"test_history_{time.strftime('%Y%m%d')}.csv",
                file_types=(("CSV Files", "*.csv"),),
            )
            if not result:
                return {"success": False, "error": "未选择保存位置"}

            path = result if isinstance(result, str) else result[0]
            pid = int(provider_id) if provider_id else None
            since = int(time.time()) - int(range_hours) * 3600
            return export.export_test_history_csv(path, pid, since)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_backup(self):
        """创建数据库备份"""
        try:
            if not self._window:
                return {"success": False, "error": "窗口未就绪"}

            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"api_monitor_backup_{time.strftime('%Y%m%d_%H%M%S')}.db",
                file_types=(("SQLite Database", "*.db"),),
            )
            if not result:
                return {"success": False, "error": "未选择保存位置"}

            path = result if isinstance(result, str) else result[0]
            return export.create_backup(path)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def export_history_csv(self, provider_id=None):
        """导出测试历史 CSV（可选供应商）"""
        try:
            pid = int(provider_id) if provider_id not in (None, "", 0, "0") else None
        except Exception:
            pid = None
        try:
            if not self._window:
                return {"success": False, "error": "窗口未就绪"}
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=f"test_history_{time.strftime('%Y%m%d')}.csv",
                file_types=(("CSV Files", "*.csv"),),
            )
            if not result:
                return {"success": False, "error": "未选择保存位置"}
            path = result if isinstance(result, str) else result[0]
            return export.export_test_history_csv(path, provider_id=pid)
        except Exception as e:
            return {"success": False, "error": str(e)}


    def restore_backup(self):
        """从备份恢复数据库"""
        try:
            if not self._window:
                return {"success": False, "error": "窗口未就绪"}

            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    ("SQLite Database", "*.db"),
                    ("All Files", "*.*"),
                ),
            )
            if not result:
                return {"success": False, "error": "未选择备份文件"}

            path = result[0] if isinstance(result, (list, tuple)) else result
            r = export.restore_backup(path)
            if r.get("success"):
                self._refresh_frontend()
            return r
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_backups(self):
        """列出备份文件"""
        try:
            backups = export.list_backups()
            return {"success": True, "data": backups}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────── 内部方法 ──────────────────

    def _run_scheduled_test(self, mode="full"):
        """定时测试回调"""
        with self._testing_lock:
            if self._testing:
                self._push_cli_log("warn", "定时测试跳过：已有测试在进行", "调度器")
                return
        self.test_all(mode)
        self._scheduler.reset_countdown()

    def _setup_auto_backup_timer(self):
        """按设置启动/停止自动备份定时器"""
        try:
            if getattr(self, "_auto_backup_timer", None):
                self._auto_backup_timer.cancel()
                self._auto_backup_timer = None
        except Exception:
            pass

        enabled = db.get_setting("auto_backup_enabled") in ("true", "1")
        if not enabled:
            return
        try:
            hours = float(db.get_setting("auto_backup_interval_hours") or "24")
        except Exception:
            hours = 24.0
        if hours <= 0:
            hours = 24.0
        interval_sec = max(3600, int(hours * 3600))

        def _tick():
            try:
                result = export.create_auto_backup()
                if result.get("success"):
                    self._push_cli_log("ok", f"自动备份完成: {result.get('path', '')}", "备份")
                elif not result.get("skipped"):
                    self._push_cli_log("err", f"自动备份失败: {result.get('error', '')}", "备份")
            except Exception as e:
                logger.warning(f"auto backup tick failed: {e}")
            finally:
                self._setup_auto_backup_timer()

        self._auto_backup_timer = threading.Timer(interval_sec, _tick)
        self._auto_backup_timer.daemon = True
        self._auto_backup_timer.start()

    def _refresh_frontend(self):
        if self._window:
            try:
                self._window.evaluate_js("if (window.app) { window.app.loadData(); }")
            except Exception as e:
                logger.warning(f"Push refresh_frontend failed: {e}")

    def _push_single_update(self, provider_id):
        if not self._window:
            return
        try:
            p = db.get_provider_by_id(provider_id)
            if not p:
                return
            from core.providers import _format_provider
            formatted = _format_provider(p)
            stats = db.get_stats()
            js = f"""
                if (window.app) {{
                    window.app.updateSingleProvider({json.dumps(formatted)});
                    window.app.updateStatsIncremental({json.dumps(stats)});
                }}
            """
            self._window.evaluate_js(js)
        except Exception as e:
            logger.warning(f"Push single_update failed for provider {provider_id}: {e}")

    def _push_cli_log(self, level, text, name=""):
        if not self._window:
            return
        try:
            js = f"""
                if (window.app) {{
                    window.app.addCliLog({json.dumps(level)}, {json.dumps(text)}, {json.dumps(name)});
                }}
            """
            self._window.evaluate_js(js)
        except Exception as e:
            logger.warning(f"Push cli_log failed: {e}")

    def _push_testing_complete(self):
        if self._window:
            try:
                self._window.evaluate_js("if (window.app) { window.app.testingComplete(); }")
            except Exception as e:
                logger.warning(f"Push testing_complete failed: {e}")

    def _push_unread_count(self):
        if not self._window:
            return
        try:
            count = db.get_unread_count()
            self._window.evaluate_js(f"if (window.app) {{ window.app.updateUnreadCount({count}); }}")
        except Exception as e:
            logger.warning(f"Push unread_count failed: {e}")

    def _push_failover_event(self, fo_result):
        if not self._window:
            return
        try:
            self._window.evaluate_js(f"if (window.app) {{ window.app.onFailoverEvent({json.dumps(fo_result)}); }}")
        except Exception as e:
            logger.warning(f"Push failover_event failed: {e}")

    def _push_scheduler_tick(self):
        if not self._window:
            return
        try:
            status = self._scheduler.get_status()
            try:
                status = dict(status)
                status["failover"] = failover.get_status()
            except Exception:
                pass
            self._window.evaluate_js(f"if (window.app) {{ window.app.updateSchedulerStatus({json.dumps(status)}); }}")
        except Exception as e:
            logger.warning(f"Push scheduler_tick failed: {e}")


def center_native_window(window):
    hwnd = int(window.native.Handle.ToInt32())
    rect = RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))

    monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
    monitor_info = MONITORINFO()
    monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
    ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info))

    work_area = monitor_info.rcWork
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    x = work_area.left + max(((work_area.right - work_area.left) - width) // 2, 0)
    y = work_area.top + max(((work_area.bottom - work_area.top) - height) // 2, 0)

    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def center_window(window):
    time.sleep(1)
    try:
        center_native_window(window)
    except Exception:
        x, y = get_centered_window_position(window.width, window.height)
        window.move(x, y)





def main():
    setup_logging()
    if not ensure_single_instance():
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

    x, y = get_centered_window_position(WINDOW_WIDTH, WINDOW_HEIGHT)

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
            # 默认启用托盘（可设置关闭）
            minimize_to_tray = db.get_setting("minimize_to_tray")
            if minimize_to_tray not in ("false", "0"):
                api.start_tray()
            api._setup_auto_backup_timer()
            api._push_scheduler_tick()
        except Exception as e:
            logger.warning(f"post-start init failed: {e}")
        # 原有居中逻辑
        center_window(window)

    # 独立存储目录，避免沿用旧 WebView2 缓存导致前端文案不更新
    storage_path = os.path.join(
        os.environ.get('APPDATA', ''),
        '.cc-switch-monitor',
        'webview2',
    )
    try:
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
