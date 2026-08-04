"""
数据库操作模块
"""

import sqlite3
import os
import time
from contextlib import contextmanager
from typing import List, Dict, Optional, Any


def get_db_path() -> str:
    """获取自有数据库路径（数据目录统一由 core.paths 管理，含历史迁移）"""
    from . import paths
    return os.path.join(paths.get_data_dir(), "providers.db")


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
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    # foreign_keys 是每连接属性，必须在每个连接上启用，
    # 否则 ON DELETE CASCADE 全部失效
    conn.execute("PRAGMA foreign_keys=ON")
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

        # provider_apps 表（每应用绑定：端点/模型/格式/角色 各自独立。
        # 一个供应商可同时绑定 claude 与 codex，每端一套配置。）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provider_apps (
                provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                app_type TEXT NOT NULL,
                endpoint TEXT,
                default_model TEXT,
                api_format TEXT DEFAULT '',
                reasoning_effort TEXT DEFAULT '',
                context_length INTEGER DEFAULT 0,
                role TEXT DEFAULT '备用',
                PRIMARY KEY (provider_id, app_type)
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
        if "reasoning_effort" not in columns:
            cursor.execute("ALTER TABLE providers ADD COLUMN reasoning_effort TEXT DEFAULT ''")
        if "context_length" not in columns:
            cursor.execute("ALTER TABLE providers ADD COLUMN context_length INTEGER DEFAULT 0")

        # 迁移：为已有供应商回填 provider_apps（幂等；仅回填单应用行，
        # 双端 'both' 供应商一定已有绑定行，避免插出 app_type='both' 的脏行）
        cursor.execute("""
            INSERT OR IGNORE INTO provider_apps
                (provider_id, app_type, endpoint, default_model, api_format, role)
            SELECT id, app_type, endpoint, default_model, api_format, role
            FROM providers WHERE app_type IN ('claude', 'codex')
        """)

        # 迁移：为已有 provider_apps 表添加 reasoning_effort / context_length 列
        cursor.execute("PRAGMA table_info(provider_apps)")
        pa_columns = {row[1] for row in cursor.fetchall()}
        if "reasoning_effort" not in pa_columns:
            cursor.execute("ALTER TABLE provider_apps ADD COLUMN reasoning_effort TEXT DEFAULT ''")
        if "context_length" not in pa_columns:
            cursor.execute("ALTER TABLE provider_apps ADD COLUMN context_length INTEGER DEFAULT 0")


# ────────────────── SQL 字段白名单 ──────────────────

UPDATABLE_FIELDS = frozenset({
    "name", "app_type", "role", "endpoint", "api_key", "website",
    "category", "notes", "default_model", "api_format",
    "reasoning_effort", "context_length",
    "status", "latency", "last_test_time", "test_detail",
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


# ────────────────── 每应用绑定 (provider_apps) ──────────────────

def _get_provider_apps_map(provider_ids) -> Dict[int, List[Dict[str, Any]]]:
    """按 provider_id 批量取 provider_apps 绑定，返回 {id: [binding,...]}。"""
    ids = [pid for pid in (provider_ids or []) if pid is not None]
    if not ids:
        return {}
    with get_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in ids)
        cursor.execute(
            f"SELECT provider_id, app_type, endpoint, default_model, api_format, reasoning_effort, context_length, role "
            f"FROM provider_apps WHERE provider_id IN ({placeholders}) ORDER BY app_type",
            ids,
        )
        rows = cursor.fetchall()
    result: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(r["provider_id"], []).append({
            "app_type": r["app_type"],
            "endpoint": r["endpoint"] or "",
            "default_model": r["default_model"] or "",
            "api_format": r["api_format"] or "",
            "reasoning_effort": r["reasoning_effort"] or "",
            "context_length": r["context_length"] or 0,
            "role": r["role"] or "备用",
        })
    return result


def _app_type_from_bindings(bindings: List[Dict[str, Any]]) -> str:
    """由绑定列表得出 providers.app_type: 只 claude→'claude', 只 codex→'codex', 都有→'both'。"""
    types = sorted({b.get("app_type") for b in bindings if b.get("app_type") in ("claude", "codex")})
    if len(types) == 2:
        return "both"
    return types[0] if types else "claude"


def _primary_binding(bindings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """主绑定 = 绑了 claude 取 claude，否则取 codex；空则返回空 dict。"""
    for want in ("claude", "codex"):
        for b in bindings:
            if b.get("app_type") == want:
                return b
    return {}


def _sync_provider_mirrors(cursor, provider_id: int) -> None:
    """把 provider_apps 的主绑定同步回 providers 的镜像列
    (app_type/role/endpoint/default_model/api_format/reasoning_effort/context_length)，保持旧代码读取兼容。"""
    cursor.execute(
        "SELECT app_type, endpoint, default_model, api_format, reasoning_effort, context_length, role FROM provider_apps "
        "WHERE provider_id=? ORDER BY (app_type='claude') DESC, app_type",
        (provider_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return
    primary = rows[0]
    cursor.execute(
        "UPDATE providers SET app_type=?, role=?, endpoint=?, default_model=?, api_format=?, reasoning_effort=?, context_length=? WHERE id=?",
        (
            _app_type_from_bindings([dict(r) for r in rows]),
            primary["role"] or "备用",
            primary["endpoint"] or "",
            primary["default_model"] or "",
            primary["api_format"] or "",
            primary["reasoning_effort"] or "",
            primary["context_length"] or 0,
            provider_id,
        ),
    )


def set_binding_role(provider_id: int, app_type: str, role: str = "当前") -> None:
    """把指定供应商在指定应用的绑定设为角色，并同步该供应商的镜像列。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE provider_apps SET role=? WHERE provider_id=? AND app_type=?",
            (role, provider_id, app_type),
        )
        _sync_provider_mirrors(cursor, provider_id)


def update_binding_default_model(provider_id: int, app_type: str, default_model: str) -> None:
    """更新指定应用绑定的默认模型，并同步 providers 镜像列。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE provider_apps SET default_model=? WHERE provider_id=? AND app_type=?",
            (default_model or "", provider_id, app_type),
        )
        _sync_provider_mirrors(cursor, provider_id)


def mark_app_current(provider_id: int, app_type: str) -> None:
    """指定应用下：其余绑定置备用，目标绑定置当前；同步所有受影响供应商镜像。"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT provider_id FROM provider_apps WHERE app_type=? AND provider_id<>?",
                (app_type, provider_id),
            )
            affected = [r[0] for r in cursor.fetchall()]
            cursor.execute("UPDATE provider_apps SET role='备用' WHERE app_type=?", (app_type,))
            cursor.execute(
                "UPDATE provider_apps SET role='当前' WHERE provider_id=? AND app_type=?",
                (provider_id, app_type),
            )
            _sync_provider_mirrors(cursor, provider_id)
            for pid in affected:
                _sync_provider_mirrors(cursor, pid)
    except Exception:
        pass


def clear_app_roles(app_type: str) -> None:
    """把某应用下所有绑定置备用（官方模式），并同步受影响供应商镜像。"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT provider_id FROM provider_apps WHERE app_type=?", (app_type,)
            )
            affected = [r[0] for r in cursor.fetchall()]
            cursor.execute("UPDATE provider_apps SET role='备用' WHERE app_type=?", (app_type,))
            for pid in affected:
                _sync_provider_mirrors(cursor, pid)
    except Exception:
        pass


# ────────────────── 供应商 CRUD ──────────────────

def get_providers() -> List[Dict[str, Any]]:
    """获取供应商列表（自动解密 API Key，明文 Key 自动迁移为加密格式）
    每行额外带 `apps`：按应用绑定的 [{app_type,endpoint,default_model,api_format,role}]。"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM providers ORDER BY id")
        rows = [dict(row) for row in cursor.fetchall()]

    apps_map = _get_provider_apps_map([r.get("id") for r in rows])
    for row in rows:
        row["apps"] = apps_map.get(row.get("id"), [])
        if row.get("api_key"):
            row["api_key"] = _maybe_migrate_key(row["id"], row["api_key"])

    return rows


def get_provider_by_id(provider_id: int) -> Optional[Dict[str, Any]]:
    """根据 ID 获取供应商（自动解密 API Key，附带 apps 绑定）"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM providers WHERE id = ?", (provider_id,))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)

    result["apps"] = _get_provider_apps_map([result.get("id")]).get(result.get("id"), [])
    if result.get("api_key"):
        result["api_key"] = _maybe_migrate_key(result["id"], result["api_key"])

    return result


def add_provider(provider_data: Dict[str, Any]) -> int:
    """添加供应商，返回新 ID（API Key 自动加密存储）
    支持 `apps`：每应用绑定 [{app_type,endpoint,default_model,api_format}]；缺省按单应用旧字段。"""
    now = int(time.time())
    # 加密 API Key
    api_key = provider_data.get("api_key", "")
    if api_key:
        api_key = _encrypt_api_key(api_key)

    apps = provider_data.get("apps")
    if apps:
        bindings = [b for b in apps if isinstance(b, dict) and b.get("app_type") in ("claude", "codex")]
        if not bindings:
            bindings = [{
                "app_type": "claude",
                "endpoint": provider_data.get("endpoint", ""),
                "default_model": provider_data.get("default_model", ""),
                "api_format": provider_data.get("api_format", ""),
                "role": provider_data.get("role", "备用"),
            }]
        for b in bindings:
            b.setdefault("role", "备用")
        row_app_type = _app_type_from_bindings(bindings)
        primary = _primary_binding(bindings)
        row_role = primary.get("role") or "备用"
        row_endpoint = primary.get("endpoint") or ""
        row_model = primary.get("default_model") or ""
        row_format = primary.get("api_format") or ""
    else:
        bindings = None
        row_app_type = provider_data.get("app_type", "claude")
        row_role = provider_data.get("role", "备用")
        row_endpoint = provider_data.get("endpoint", "")
        row_model = provider_data.get("default_model", "")
        row_format = provider_data.get("api_format", "")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO providers (
                name, app_type, role, endpoint, api_key, website,
                category, notes, default_model, api_format, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider_data.get("name", ""),
            row_app_type,
            row_role,
            row_endpoint,
            api_key,
            provider_data.get("website", ""),
            provider_data.get("category", ""),
            provider_data.get("notes", ""),
            row_model,
            row_format,
            provider_data.get("status", "pending"),
            now,
            now,
        ))
        pid = cursor.lastrowid

        if bindings is not None:
            for b in bindings:
                cursor.execute(
                    "INSERT INTO provider_apps (provider_id, app_type, endpoint, default_model, api_format, reasoning_effort, context_length, role) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (pid, b.get("app_type"), b.get("endpoint") or "", b.get("default_model") or "",
                     b.get("api_format") or "", b.get("reasoning_effort") or "", b.get("context_length") or 0,
                     b.get("role") or "备用"),
                )
        else:
            cursor.execute(
                "INSERT INTO provider_apps (provider_id, app_type, endpoint, default_model, api_format, reasoning_effort, context_length, role) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (pid, row_app_type, row_endpoint, row_model, row_format, "", 0, row_role),
            )
        _sync_provider_mirrors(cursor, pid)
        return pid


def update_provider(provider_id: int, provider_data: Dict[str, Any]) -> bool:
    """更新供应商信息（API Key 自动加密存储）
    支持 `apps`：整体替换每应用绑定（删除已移除、upsert 保留的，role 保持不变）。"""
    # 校验字段白名单（apps 单独处理）
    invalid = set(provider_data.keys()) - UPDATABLE_FIELDS - {"id", "apps"}
    if invalid:
        raise ValueError(f"非法字段: {', '.join(invalid)}")

    # 加密 API Key（如果包含在更新数据中）
    data = dict(provider_data)
    if "api_key" in data and data["api_key"]:
        data["api_key"] = _encrypt_api_key(data["api_key"])

    apps = data.pop("apps", None)

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

        if apps is not None:
            bindings = [b for b in apps if isinstance(b, dict) and b.get("app_type") in ("claude", "codex")]
            if not bindings:
                bindings = [{
                    "app_type": "claude",
                    "endpoint": data.get("endpoint", ""),
                    "default_model": data.get("default_model", ""),
                    "api_format": data.get("api_format", ""),
                }]
            keep_types = [b.get("app_type") for b in bindings]
            if "claude" not in keep_types:
                cursor.execute("DELETE FROM provider_apps WHERE provider_id=? AND app_type='claude'", (provider_id,))
            if "codex" not in keep_types:
                cursor.execute("DELETE FROM provider_apps WHERE provider_id=? AND app_type='codex'", (provider_id,))
            for b in bindings:
                # 保留已有 role（设为当前由 set_current 管理，表单不覆盖）
                cursor.execute(
                    "INSERT INTO provider_apps (provider_id, app_type, endpoint, default_model, api_format, reasoning_effort, context_length, role) "
                    "VALUES (?,?,?,?,?,?,?,'备用') "
                    "ON CONFLICT(provider_id, app_type) DO UPDATE SET "
                    "endpoint=excluded.endpoint, default_model=excluded.default_model, api_format=excluded.api_format, "
                    "reasoning_effort=excluded.reasoning_effort, context_length=excluded.context_length",
                    (provider_id, b.get("app_type"), b.get("endpoint") or "",
                     b.get("default_model") or "", b.get("api_format") or "",
                     b.get("reasoning_effort") or "", b.get("context_length") or 0),
                )
            # 同步 providers 镜像列（app_type/role/endpoint/default_model/api_format）
            _sync_provider_mirrors(cursor, provider_id)

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
    """获取统计数据（纯 SQL 聚合，不解密任何 Key）"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM providers GROUP BY status"
        ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    ok = counts.get("ok", 0)
    fail = counts.get("fail", 0)
    total = sum(counts.values())
    return {
        "total": total,
        "ok": ok,
        "fail": fail,
        "pending": total - ok - fail,
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
    "test_timeout": "30",             # 请求读取超时时间(秒)
    "test_connect_timeout": "5",      # TCP/TLS 建连超时(秒)
    "test_max_duration": "60",        # 单供应商测试总时长上限(秒)
    "test_retries": "2",              # 重试次数
    "ssl_verify": "1",                # SSL 验证
    "auto_sync_claude_on_startup": "1",  # 启动时自动同步 Claude Code 当前配置为供应商 1=开启 0=关闭
    "auto_sync_codex_on_startup": "1",   # 启动时自动同步 Codex 当前配置为供应商 1=开启 0=关闭
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
