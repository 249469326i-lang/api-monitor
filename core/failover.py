"""
自动故障切换模块
当「当前」供应商测试失败时，自动切换到最优备用供应商
"""

import time
import threading
from typing import Dict, Optional

from . import db, providers, notifications


def _flag(key: str, default: str = "false") -> bool:
    """兼容 1/true/yes 与 0/false/no。"""
    val = db.get_setting(key)
    if val is None or val == "":
        val = default
    return str(val).lower() in ("1", "true", "yes", "on")


_last_switch_time = 0
_consecutive_switches = 0
_pending_confirm: Optional[dict] = None
_lock = threading.Lock()


def get_status() -> dict:
    """Failover 状态摘要（供 UI 展示）"""
    enabled = _flag("failover_enabled", "false")
    need_confirm = _flag("failover_need_confirm", "false")
    cooldown = int(db.get_setting("failover_cooldown") or "300")
    max_switches = int(db.get_setting("failover_max_switches") or "3")
    now = int(time.time())
    cooldown_remaining = 0
    if _last_switch_time > 0:
        cooldown_remaining = max(0, cooldown - (now - _last_switch_time))
    pending = None
    with _lock:
        if _pending_confirm:
            pending = dict(_pending_confirm)
    return {
        "enabled": enabled,
        "need_confirm": need_confirm,
        "cooldown": cooldown,
        "cooldown_remaining": cooldown_remaining,
        "max_switches": max_switches,
        "consecutive_switches": _consecutive_switches,
        "last_switch_time": _last_switch_time,
        "pending_confirm": pending,
    }


def reset_counter():
    """重置连续切换计数器（手动测试成功时调用）"""
    global _consecutive_switches, _last_switch_time, _pending_confirm
    with _lock:
        _consecutive_switches = 0
        _last_switch_time = 0
        _pending_confirm = None


def cancel_pending():
    global _pending_confirm
    with _lock:
        _pending_confirm = None


def confirm_pending(log_callback=None) -> dict:
    """用户确认待切换"""
    global _pending_confirm
    with _lock:
        pending = _pending_confirm
        _pending_confirm = None
    if not pending:
        return {"switched": False, "reason": "没有待确认的切换"}
    return _do_switch(
        failed_name=pending.get("from", ""),
        candidate=pending["candidate"],
        log_callback=log_callback,
        reason="用户确认切换",
    )


def check_and_failover(failed_provider_id: int, log_callback=None) -> dict:
    """
    检查是否需要 Failover，如果需要则执行切换

    Args:
        failed_provider_id: 测试失败的供应商 ID
        log_callback: 日志回调 log_callback(level, text, name)

    Returns:
        {"switched": bool, "from": str, "to": str, "to_id": int, "reason": str}
    """
    global _last_switch_time, _consecutive_switches, _pending_confirm

    def log(level, text):
        if log_callback:
            try:
                log_callback(level, text, "Failover")
            except Exception:
                pass

    # 兼容 true/1
    if not _flag("failover_enabled", "false"):
        return {"switched": False, "reason": "Failover 未启用"}

    failed = db.get_provider_by_id(failed_provider_id)
    if not failed:
        return {"switched": False, "reason": "供应商不存在"}

    # 仅当前供应商失败才切换
    if failed.get("role") != "当前":
        return {"switched": False, "reason": "非当前供应商，无需切换"}

    cooldown = int(db.get_setting("failover_cooldown") or "300")
    now = int(time.time())
    if _last_switch_time > 0 and now - _last_switch_time < cooldown:
        remaining = cooldown - (now - _last_switch_time)
        return {
            "switched": False,
            "reason": f"冷却中，还需等待 {remaining}s",
            "cooldown_remaining": remaining,
        }

    max_switches = int(db.get_setting("failover_max_switches") or "3")
    if _consecutive_switches >= max_switches:
        return {
            "switched": False,
            "reason": f"已达最大连续切换次数({max_switches})",
        }

    candidate = _find_best_candidate(exclude_id=failed_provider_id)
    if not candidate:
        return {"switched": False, "reason": "没有可用的备用供应商"}

    need_confirm = _flag("failover_need_confirm", "false")
    if need_confirm:
        with _lock:
            _pending_confirm = {
                "from": failed.get("name", ""),
                "from_id": failed_provider_id,
                "to": candidate.get("name", ""),
                "to_id": candidate.get("id"),
                "candidate": candidate,
                "ts": now,
            }
        log("warn", f"⚡ 建议切换到 [{candidate.get('name')}]，等待确认…")
        try:
            notifications.notify(
                "failover",
                "等待确认故障切换",
                f"当前 [{failed.get('name')}] 不可用，建议切换到 [{candidate.get('name')}]",
                failed_provider_id,
            )
        except Exception:
            pass
        return {
            "switched": False,
            "need_confirm": True,
            "reason": "需要确认后切换",
            "from": failed.get("name", ""),
            "to": candidate.get("name", ""),
            "to_id": candidate.get("id"),
        }

    log(
        "warn",
        f"⚡ 当前供应商 [{failed.get('name', '')}] 不可用，正在切换到 [{candidate.get('name', '')}]...",
    )
    return _do_switch(
        failed_name=failed.get("name", ""),
        candidate=candidate,
        log_callback=log_callback,
        reason="自动故障切换",
    )


def _do_switch(failed_name: str, candidate: dict, log_callback=None, reason: str = "") -> dict:
    global _last_switch_time, _consecutive_switches, _pending_confirm

    def log(level, text):
        if log_callback:
            try:
                log_callback(level, text, "Failover")
            except Exception:
                pass

    result = providers.set_current_provider(candidate["id"])
    if result.get("success"):
        latency = candidate.get("latency")
        latency_str = f"{latency}ms" if latency is not None else "?"
        _last_switch_time = int(time.time())
        _consecutive_switches += 1
        with _lock:
            _pending_confirm = None

        try:
            db.add_notification(
                "failover",
                "自动故障切换",
                f"从 [{failed_name}] 切换到 [{candidate.get('name', '')}]，延迟 {latency_str}",
            )
        except Exception:
            pass
        try:
            notifications.notify(
                "failover",
                "自动故障切换",
                f"从 [{failed_name}] 切换到 [{candidate.get('name', '')}]",
                candidate.get("id"),
            )
        except Exception:
            pass

        log("ok", f"✓ 已切换到 [{candidate.get('name', '')}]")
        return {
            "switched": True,
            "from": failed_name,
            "to": candidate.get("name", ""),
            "to_id": candidate.get("id"),
            "reason": reason or "切换成功",
            "consecutive": _consecutive_switches,
        }

    err = result.get("error", "未知错误")
    log("err", f"✗ 切换失败: {err}")
    return {"switched": False, "reason": f"切换失败: {err}"}


def _find_best_candidate(exclude_id: int) -> Optional[dict]:
    """
    查找最优备用供应商
    优先级: 最近测试 OK → 延迟最低 → ID 最小
    """
    candidates = []
    for p in db.get_providers():
        if p.get("id") == exclude_id:
            continue
        if p.get("status") != "ok":
            continue
        # 跳过禁用
        if p.get("enabled") in (0, False, "0", "false"):
            continue
        candidates.append(p)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x.get("latency") is None,
            x.get("latency") if x.get("latency") is not None else 10**9,
            x.get("id") or 0,
        )
    )
    return candidates[0]
