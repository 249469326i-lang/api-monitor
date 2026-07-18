"""
数据库操作模块
"""

import sqlite3
import os
import time
from contextlib import contextmanager
from typing import List, Dict, Optional, Any


def get_db_path() -> str:
    """获取自有数据库路径"""
    db_dir = os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"),
        ".cc-switch-monitor",
    )
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "providers.db")


def get_ccswitch_db_path() -> str:
    """获取 cc-switch 数据库路径
    cc-switch 把数据库放在 %USERPROFILE%\\.cc-switch\\cc-switch.db (不是 %APPDATA%)
    """
    userprofile = os.path.expanduser("~")
    return os.path.join(userprofile, ".cc-switch", "cc-switch.db")


def discover_ccswitch_db() -> Optional[str]:
    """查找真实存在的 cc-switch 数据库文件,返回找到路径或 None"""
    # 1. 标准路径
    p = get_ccswitch_db_path()
    if os.path.exists(p):
        return p
    # 2. HOME/.cc-switch 下其它 .db (主库可能换名时)
    home_cs_dir = os.path.join(os.path.expanduser("~"), ".cc-switch")
    if os.path.isdir(home_cs_dir):
        for name in ("cc-switch.db",):
            fp = os.path.join(home_cs_dir, name)
            if os.path.exists(fp):
                return fp
    return None


@contextmanager
def get_connection():
    """获取数据库连接的 context manager，自动管理 commit/rollback/close"""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """初始化数据库表结构"""
    with get_connection() as conn:
        cursor = conn.cursor()

        # 启用 WAL 模式和外键约束
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")

        # providers 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                app_type TEXT NOT NULL DEFAULT 'claude',
                role TEXT NOT NULL DEFAULT '备用',
                endpoint TEXT,
                api_key TEXT,
                website TEXT,
                category TEXT,
                notes TEXT,
                default_model TEXT,
                api_format TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                latency INTEGER,
                last_test_time INTEGER,
                test_detail TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)

        # provider_endpoints 表（备用端点）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL,
                api_key TEXT,
                status TEXT DEFAULT 'pending',
                latency INTEGER,
                last_test_time INTEGER,
                FOREIGN KEY (provider_id) REFERENCES providers (id) ON DELETE CASCADE
            )
        """)

        # test_history 表（测试历史记录）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                latency INTEGER,
                error_type TEXT,
                error_detail TEXT,
                test_mode TEXT DEFAULT 'full',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (provider_id) REFERENCES providers (id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_provider_time
            ON test_history(provider_id, created_at)
        """)

        # settings 表（键值对设置）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        # notifications 表（应用内通知）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                provider_id INTEGER,
                is_read INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_time
            ON notifications(created_at DESC)
        """)

        # 迁移：删除已废弃的 profiles 表（如果存在）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'")
        if cursor.fetchone():
            cursor.execute("DROP TABLE profiles")

        # 迁移：为已有 providers 表添加 api_format 列
        cursor.execute("PRAGMA table_info(providers)")
        columns = {row[1] for row in cursor.fetchall()}
        if "api_format" not in columns:
            cursor.execute("ALTER TABLE providers ADD COLUMN api_format TEXT DEFAULT ''")


# ────────────────── SQL 字段白名单 ──────────────────

UPDATABLE_FIELDS = frozenset({
    "name", "app_type", "role", "endpoint", "api_key", "website",
    "category", "notes", "default_model", "api_format", "status", "latency",
    "last_test_time", "test_detail",
})


def _encrypt_api_key(plaintext: str) -> str:
    """加密 API Key（延迟导入避免循环依赖）"""
    from . import crypto
    return crypto.encrypt_key(plaintext)


def _decrypt_api_key(stored: str) -> str:
    """解密 API Key（延迟导入避免循环依赖）"""
    from . import crypto
    return crypto.decrypt_key(stored)


def _maybe_migrate_key(provider_id: int, api_key: str) -> str:
    """如果 API Key 是明文，加密后写回数据库，返回解密后的 Key"""
    from . import crypto
    if api_key and not crypto.is_encrypted(api_key):
        encrypted = crypto.encrypt_key(api_key)
        if encrypted != api_key:
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE providers SET api_key = ? WHERE id = ?",
                        (encrypted, provider_id),
                    )
            except Exception:
                pass
    return crypto.decrypt_key(api_key)


# ────────────────── 供应商 CRUD ──────────────────

def get_providers() -> List[Dict[str, Any]]:
    """获取供应商列表（自动解密 API Key，明文 Key 自动迁移为加密格式）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM providers ORDER BY id")
        rows = [dict(row) for row in cursor.fetchall()]

    for row in rows:
        if row.get("api_key"):
            row["api_key"] = _maybe_migrate_key(row["id"], row["api_key"])

    return rows


def get_provider_by_id(provider_id: int) -> Optional[Dict[str, Any]]:
    """根据 ID 获取供应商（自动解密 API Key）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM providers WHERE id = ?", (provider_id,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)

    if result.get("api_key"):
        result["api_key"] = _maybe_migrate_key(result["id"], result["api_key"])

    return result


def add_provider(provider_data: Dict[str, Any]) -> int:
    """添加供应商，返回新 ID（API Key 自动加密存储）"""
    now = int(time.time())
    # 加密 API Key
    api_key = provider_data.get("api_key", "")
    if api_key:
        api_key = _encrypt_api_key(api_key)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO providers (
                name, app_type, role, endpoint, api_key, website,
                category, notes, default_model, api_format, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider_data.get("name", ""),
            provider_data.get("app_type", "claude"),
            provider_data.get("role", "备用"),
            provider_data.get("endpoint", ""),
            api_key,
            provider_data.get("website", ""),
            provider_data.get("category", ""),
            provider_data.get("notes", ""),
            provider_data.get("default_model", ""),
            provider_data.get("api_format", ""),
            provider_data.get("status", "pending"),
            now,
            now,
        ))
        return cursor.lastrowid


def update_provider(provider_id: int, provider_data: Dict[str, Any]) -> bool:
    """更新供应商信息（API Key 自动加密存储）"""
    # 校验字段白名单
    invalid = set(provider_data.keys()) - UPDATABLE_FIELDS - {"id"}
    if invalid:
        raise ValueError(f"非法字段: {', '.join(invalid)}")

    # 加密 API Key（如果包含在更新数据中）
    data = dict(provider_data)
    if "api_key" in data and data["api_key"]:
        data["api_key"] = _encrypt_api_key(data["api_key"])

    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        fields = []
        values = []
        for key, value in data.items():
            if key != "id":
                fields.append(f"{key} = ?")
                values.append(value)

        fields.append("updated_at = ?")
        values.append(now)
        values.append(provider_id)

        cursor.execute(f"""
            UPDATE providers SET {', '.join(fields)} WHERE id = ?
        """, values)
        return cursor.rowcount > 0


def delete_provider(provider_id: int) -> bool:
    """删除供应商"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        return cursor.rowcount > 0


def update_provider_status(provider_id: int, status: str, latency: Optional[int] = None, detail: Optional[str] = None) -> bool:
    """更新供应商测试状态"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE providers SET status = ?, latency = ?, test_detail = ?, last_test_time = ?, updated_at = ?
            WHERE id = ?
        """, (status, latency, detail, now, now, provider_id))
        return cursor.rowcount > 0


def update_api_format(provider_id: int, api_format: str) -> bool:
    """更新供应商检测到的 API 格式"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE providers SET api_format = ?, updated_at = ? WHERE id = ?",
            (api_format, now, provider_id),
        )
        return cursor.rowcount > 0


def get_stats() -> Dict[str, int]:
    """获取统计数据"""
    providers = get_providers()
    total = len(providers)
    ok = sum(1 for p in providers if p.get("status") == "ok")
    fail = sum(1 for p in providers if p.get("status") == "fail")
    pending = sum(1 for p in providers if p.get("status") in ("pending", None))

    return {
        "total": total,
        "ok": ok,
        "fail": fail,
        "pending": pending,
    }


# ────────────────── 测试历史 ──────────────────

def add_test_history(provider_id: int, status: str, latency: Optional[int] = None,
                     error_type: str = "", error_detail: str = "", test_mode: str = "full") -> int:
    """记录一次测试结果到历史表"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO test_history (provider_id, status, latency, error_type, error_detail, test_mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (provider_id, status, latency, error_type, error_detail, test_mode, now))
        return cursor.lastrowid


def get_test_history(provider_id: int, since: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
    """获取指定供应商的测试历史（按时间倒序）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM test_history
            WHERE provider_id = ? AND created_at >= ?
            ORDER BY created_at DESC LIMIT ?
        """, (provider_id, since, limit))
        return [dict(r) for r in cursor.fetchall()]


def get_history_stats(provider_id: int, since: int) -> Dict[str, Any]:
    """计算指定供应商在时间范围内的统计指标"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_count,
                SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) as fail_count,
                AVG(CASE WHEN status = 'ok' THEN latency END) as avg_latency,
                MAX(CASE WHEN status = 'fail' THEN created_at END) as last_fail_time
            FROM test_history
            WHERE provider_id = ? AND created_at >= ?
        """, (provider_id, since))
        row = cursor.fetchone()

        # P95 延迟需要单独算
        cursor.execute("""
            SELECT latency FROM test_history
            WHERE provider_id = ? AND created_at >= ? AND status = 'ok' AND latency IS NOT NULL
            ORDER BY latency ASC
        """, (provider_id, since))
        latencies = [r[0] for r in cursor.fetchall()]

    total = row[0] or 0
    ok_count = row[1] or 0
    fail_count = row[2] or 0
    avg_latency = round(row[3]) if row[3] else None
    last_fail = row[4]

    p95_latency = None
    if latencies:
        idx = int(len(latencies) * 0.95)
        p95_latency = latencies[min(idx, len(latencies) - 1)]

    availability = round(ok_count / total * 100, 1) if total > 0 else None

    return {
        "total": total,
        "ok": ok_count,
        "fail": fail_count,
        "avg_latency": avg_latency,
        "p95_latency": p95_latency,
        "availability": availability,
        "last_fail_time": last_fail,
    }


def get_history_timeline(provider_id: int, since: int, bucket_seconds: int = 300) -> List[Dict[str, Any]]:
    """获取延迟时间线数据（按时间分桶取平均值）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                (created_at / ?) * ? as bucket,
                AVG(latency) as avg_latency,
                MIN(latency) as min_latency,
                MAX(latency) as max_latency,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_count
            FROM test_history
            WHERE provider_id = ? AND created_at >= ? AND latency IS NOT NULL
            GROUP BY bucket ORDER BY bucket ASC
        """, (bucket_seconds, bucket_seconds, provider_id, since))
        return [
            {
                "time": r[0] * bucket_seconds,
                "avg": round(r[1]),
                "min": r[2],
                "max": r[3],
                "count": r[4],
                "ok": r[5],
            }
            for r in cursor.fetchall()
        ]


def cleanup_old_history(days: int = 30) -> int:
    """清理超过指定天数的历史记录，返回删除行数"""
    cutoff = int(time.time()) - days * 86400
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM test_history WHERE created_at < ?", (cutoff,))
        return cursor.rowcount


# ────────────────── 设置 ──────────────────

# 默认设置值
DEFAULT_SETTINGS = {
    "auto_test_interval": "0",        # 0=关闭, 300=5min, 900=15min, 1800=30min, 3600=1h
    "failover_enabled": "1",          # 1=启用, 0=禁用
    "failover_need_confirm": "0",     # 1=切换前需确认, 0=静默切换
    "failover_max_switches": "3",     # 最大连续切换次数
    "failover_cooldown": "300",       # 切换冷却时间(秒)
    "notify_status_change": "1",      # 状态变化通知
    "notify_failover": "1",           # Failover 通知
    "notify_test_complete": "0",      # 测试完成通知
    "webhook_url": "",                # Webhook URL
    "webhook_events": "status_change,failover",  # Webhook 事件类型
    "history_retention_days": "30",   # 历史保留天数
    "test_concurrency": "3",          # 并发测试数
    "test_timeout": "30",             # 超时时间(秒)
    "test_retries": "2",              # 重试次数
    "ssl_verify": "1",                # SSL 验证
    "auto_sync_claude_on_startup": "1",  # 启动时自动同步 Claude Code 当前配置为供应商 1=开启 0=关闭
}


def get_setting(key: str) -> str:
    """获取设置值，不存在返回默认值"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
    if row:
        return row[0]
    return DEFAULT_SETTINGS.get(key, "")


def get_all_settings() -> Dict[str, str]:
    """获取所有设置（合并默认值）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        stored = {r[0]: r[1] for r in cursor.fetchall()}
    result = dict(DEFAULT_SETTINGS)
    result.update(stored)
    return result


def set_setting(key: str, value: str) -> None:
    """设置一个配置项"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, value, now))


def set_settings_batch(settings_dict: Dict[str, str]) -> None:
    """批量设置配置项"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        for key, value in settings_dict.items():
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, str(value), now))


# ────────────────── 通知 ──────────────────

def add_notification(notif_type: str, title: str, message: str, provider_id: int = None) -> int:
    """添加一条应用内通知"""
    now = int(time.time())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (type, title, message, provider_id, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (notif_type, title, message, provider_id, now))
        return cursor.lastrowid


def get_notifications(limit: int = 50, unread_only: bool = False) -> List[Dict[str, Any]]:
    """获取通知列表"""
    with get_connection() as conn:
        cursor = conn.cursor()
        where = "WHERE is_read = 0" if unread_only else ""
        cursor.execute(f"""
            SELECT * FROM notifications {where}
            ORDER BY created_at DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


def get_unread_count() -> int:
    """获取未读通知数"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE is_read = 0")
        return cursor.fetchone()[0]


def mark_notifications_read(notification_ids: List[int] = None) -> int:
    """标记通知为已读，传 None 则全部标记"""
    with get_connection() as conn:
        cursor = conn.cursor()
        if notification_ids:
            placeholders = ",".join("?" for _ in notification_ids)
            cursor.execute(f"UPDATE notifications SET is_read = 1 WHERE id IN ({placeholders})", notification_ids)
        else:
            cursor.execute("UPDATE notifications SET is_read = 1")
        return cursor.rowcount


def cleanup_old_notifications(days: int = 30) -> int:
    """清理过期通知"""
    cutoff = int(time.time()) - days * 86400
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
        return cursor.rowcount
