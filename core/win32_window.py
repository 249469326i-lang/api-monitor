"""Win32 窗口辅助：单实例互斥、居中、任务栏最小化样式。

从 main.py 拆出的纯 ctypes 窗口层，不含业务逻辑。
"""

import atexit
import ctypes
import logging
import time

logger = logging.getLogger(__name__)

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


def enable_taskbar_minimize(hwnd) -> bool:
    """让无边框窗口支持任务栏图标再次点击最小化。

    pywebview frameless 会去掉系统标题栏，通常也丢掉 WS_MINIMIZEBOX。
    没有最小化框时，任务栏按钮只会激活窗口，不会在「显示 ↔ 最小化」间切换。
    标题栏自定义缩小按钮走的是程序化 minimize()，所以仍可用。
    """
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        WS_MINIMIZEBOX = 0x00020000
        WS_SYSMENU = 0x00080000
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020

        # 64 位下优先 Get/SetWindowLongPtrW
        try:
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_ssize_t
            set_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        except AttributeError:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
            get_long.restype = ctypes.c_long
            set_long.restype = ctypes.c_long
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]

        style = int(get_long(ctypes.c_void_p(hwnd), GWL_STYLE))
        new_style = style | WS_MINIMIZEBOX | WS_SYSMENU
        if new_style == style:
            return True
        set_long(ctypes.c_void_p(hwnd), GWL_STYLE, new_style)
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            None,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception as e:
        logger.warning(f"enable_taskbar_minimize failed: {e}")
        return False


def center_native_window(window):
    # 64 位句柄高位非零时 ToInt32 会抛 OverflowException，优先 ToInt64
    handle = window.native.Handle
    try:
        hwnd = int(handle.ToInt64())
    except Exception:
        hwnd = int(handle.ToInt32())
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
