"""
自动定时测试调度器
"""

import threading
import time
from typing import Callable, Optional
from . import db


class Scheduler:
    """后台定时测试调度器"""

    def __init__(
        self,
        test_callback: Callable,
        log_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ):
        """
        Args:
            test_callback: 执行测试的回调函数 callback(mode="full")
            log_callback: 日志回调 log_callback(level, text, name)
            status_callback: 状态推送 status_callback() 每秒一次
        """
        self._test_callback = test_callback
        self._log_callback = log_callback
        self._status_callback = status_callback
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._interval = 0  # 秒, 0=关闭
        self._next_run = 0  # 下次执行时间戳
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def next_run_time(self) -> int:
        return self._next_run

    @property
    def interval(self) -> int:
        return self._interval

    def start(self, interval_seconds: int = 0):
        """启动定时器"""
        if interval_seconds <= 0:
            interval_seconds = int(db.get_setting("auto_test_interval") or "0")

        self._interval = interval_seconds
        if self._interval <= 0:
            return

        self._stop_event.clear()
        self._next_run = int(time.time()) + self._interval
        self._running = True

        if self._thread and self._thread.is_alive():
            self._push_status()
            return  # 已在运行

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._log("info", f"定时测试已启动，间隔 {self._interval}s")
        self._push_status()

    def stop(self):
        """停止定时器"""
        self._stop_event.set()
        self._running = False
        self._interval = 0
        self._next_run = 0
        self._log("info", "定时测试已停止")
        self._push_status()

    def update_interval(self, interval_seconds: int):
        """更新间隔（自动重启）"""
        was_running = self._running
        self.stop()
        if interval_seconds > 0 and was_running:
            self.start(interval_seconds)
        elif interval_seconds > 0:
            self.start(interval_seconds)
        else:
            self._push_status()

    def reset_countdown(self):
        """重置倒计时（用户手动测试时调用）"""
        if self._running and self._interval > 0:
            self._next_run = int(time.time()) + self._interval
            self._push_status()

    def get_status(self) -> dict:
        """获取调度器状态"""
        now = int(time.time())
        remaining = max(0, self._next_run - now) if self._running else 0
        return {
            "running": self._running,
            "interval": self._interval,
            "next_run": self._next_run,
            "remaining": remaining,
        }

    def _run_loop(self):
        """定时器主循环"""
        while not self._stop_event.is_set():
            now = int(time.time())
            if now >= self._next_run:
                self._log("info", f"⏰ 定时测试触发 ({self._interval}s 间隔)")
                try:
                    self._test_callback(mode="full")
                except Exception as e:
                    self._log("err", f"定时测试异常: {e}")
                # 计算下次执行时间
                self._next_run = int(time.time()) + self._interval

            self._push_status()
            # 每秒检查一次
            self._stop_event.wait(1)

        self._running = False
        self._push_status()

    def _push_status(self):
        if not self._status_callback:
            return
        try:
            self._status_callback()
        except Exception:
            pass

    def _log(self, level: str, text: str):
        if self._log_callback:
            try:
                self._log_callback(level, text, "调度器")
            except Exception:
                pass
