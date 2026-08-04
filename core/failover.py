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
# RLock：check_and_failover 全程持锁（含 _do_switch），防止并发批测下
# 多个失败回调同时通过冷却/次数检查导致重复切换
_lock = threading.RLock()


def _int_setting(key: str, default: int) -> int:
    """安全读取整数设置，坏值回退默认（避免回调线程被 ValueError 炸掉）"""
    try:
        return int(db.get_setting(key) or default)
    except (TypeError, ValueError):
        return default


def get_status() -> dict:
    """Failover 状态摘要（供 UI 展示）"""
    enabled = _flag("failover_enabled", "false")
    need_confirm = _flag("failover_need_confirm", "false")
    cooldown = _int_setting("failover_cooldown", 300)
    max_switches = _int_setting("failover_max_switches", 3)
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
        app_type=pending.get("app_type"),
    )


def check_and_failover(failed_provider_id: int, app_type: Optional[str] = None, log_callback=None) -> dict:
    """
    检查是否需要 Failover，如果需要则执行切换

    Args:
        failed_provider_id: 测试失败的供应商 ID
        app_type: 被测试的应用 'claude' | 'codex'。非空时只在该应用绑定
                  role=当前 的情况下才触发切换（测试备用绑定不再误触发
                  另一应用的切换）；为空或 'both' 时按旧逻辑（任一绑定
                  为当前即视为当前）。
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

    # 'both' 无具体应用语义，按旧逻辑处理
    if app_type == "both":
        app_type = None

    # 仅「当前」供应商失败才切换：传入 app_type 时只认该应用的绑定，
    # 避免在 A 应用测试备用绑定失败时误触发 B 应用的切换
    if app_type:
        if not _is_current_for_app(failed, app_type):
            return {"switched": False, "reason": "该应用下为非当前供应商，无需切换"}
    elif not _provider_is_current(failed):
        return {"switched": False, "reason": "非当前供应商，无需切换"}

    # 失败应用：优先用传入的 app_type；否则双端供应商取 role=当前 的绑定
    failed_app = app_type or (failed.get("app_type") or "claude")
    if not app_type and failed_app == "both":
        failed_app = "claude"
        for b in failed.get("apps") or []:
            if b.get("role") == "当前":
                failed_app = b.get("app_type")
                break

    # 冷却检查、计数检查到实际切换必须整段持锁：
    # 并发批测下多个失败回调会同时到达这里
    with _lock:
        cooldown = _int_setting("failover_cooldown", 300)
        now = int(time.time())
        if _last_switch_time > 0 and now - _last_switch_time < cooldown:
            remaining = cooldown - (now - _last_switch_time)
            return {
                "switched": False,
                "reason": f"冷却中，还需等待 {remaining}s",
                "cooldown_remaining": remaining,
            }

        max_switches = _int_setting("failover_max_switches", 3)
        if _consecutive_switches >= max_switches:
            return {
                "switched": False,
                "reason": f"已达最大连续切换次数({max_switches})",
            }

        candidate = _find_best_candidate(
            exclude_id=failed_provider_id,
            app_type=failed_app,
        )
        if not candidate:
            return {"switched": False, "reason": "没有可用的备用供应商"}

        need_confirm = _flag("failover_need_confirm", "false")
        if need_confirm:
            _pending_confirm = {
                "from": failed.get("name", ""),
                "from_id": failed_provider_id,
                "to": candidate.get("name", ""),
                "to_id": candidate.get("id"),
                "candidate": candidate,
                "app_type": failed_app,
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
            app_type=failed_app,
        )


def _do_switch(failed_name: str, candidate: dict, log_callback=None, reason: str = "", app_type=None) -> dict:
    global _last_switch_time, _consecutive_switches, _pending_confirm

    def log(level, text):
        if log_callback:
            try:
                log_callback(level, text, "Failover")
            except Exception:
                pass

    result = providers.set_current_provider(candidate["id"], app_type)
    if result.get("success"):
        latency = candidate.get("latency")
        latency_str = f"{latency}ms" if latency is not None else "?"
        with _lock:
            _last_switch_time = int(time.time())
            _consecutive_switches += 1
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


def _provider_is_current(p: dict) -> bool:
    """供应商是否当前：任一绑定的 role=当前（兼容旧顶层 role 字段）。"""
    if p.get("role") == "当前":
        return True
    for b in p.get("apps") or []:
        if b.get("role") == "当前":
            return True
    return False


def _is_current_for_app(p: dict, app_type: str) -> bool:
    """供应商在指定应用下是否当前。

    优先按 apps 绑定判断；无绑定的旧数据回退到顶层 role/app_type。
    """
    apps = p.get("apps") or []
    if apps:
        for b in apps:
            if b.get("app_type") == app_type and b.get("role") == "当前":
                return True
        return False
    return p.get("role") == "当前" and (
        (p.get("app_type") or "claude") == app_type
        or (p.get("app_type") or "") == "both"
    )


def _matches_app(p: dict, app_type: Optional[str]) -> bool:
    """供应商是否服务于指定应用（按 apps 绑定判断，兼容旧顶层 app_type）。"""
    if not app_type:
        return True
    for b in p.get("apps") or []:
        if b.get("app_type") == app_type:
            return True
    return (p.get("app_type") or "claude") == app_type


def _find_best_candidate(exclude_id: int, app_type: Optional[str] = None) -> Optional[dict]:
    """
    查找最优备用供应商
    优先级: 最近测试 OK → 延迟最低 → ID 最小
    app_type 非空时只考虑同应用类型(Claude/Codex 各自独立切换)。
    """
    candidates = []
    for p in db.get_providers():
        if p.get("id") == exclude_id:
            continue
        if not _matches_app(p, app_type):
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
