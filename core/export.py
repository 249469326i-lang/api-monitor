"""
数据导出模块 - CSV / JSON 导出 + 数据库备份恢复
"""

import csv
import json
import os
import shutil
import sqlite3
import time
from typing import Dict, List, Optional
from . import db


# ────────────────── 导出 ──────────────────

def export_providers_csv(output_path: str, include_api_key: bool = False) -> Dict:
    """导出供应商列表为 CSV"""
    providers = db.get_providers()
    if not providers:
        return {"success": False, "error": "没有可导出的供应商"}

    fields = ["id", "name", "app_type", "role", "endpoint", "website",
              "category", "notes", "default_model", "status", "latency",
              "last_test_time", "test_detail"]
    if include_api_key:
        fields.insert(5, "api_key")

    try:
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for p in providers:
                row = {k: p.get(k, "") for k in fields}
                # 时间戳转可读格式
                if row.get("last_test_time"):
                    row["last_test_time"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(int(row["last_test_time"]))
                    )
                writer.writerow(row)
        return {"success": True, "count": len(providers), "path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def export_providers_json(output_path: str, include_api_key: bool = False) -> Dict:
    """导出供应商列表为 JSON"""
    providers = db.get_providers()
    if not providers:
        return {"success": False, "error": "没有可导出的供应商"}

    if not include_api_key:
        for p in providers:
            p.pop("api_key", None)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(providers, f, ensure_ascii=False, indent=2)
        return {"success": True, "count": len(providers), "path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def export_test_history_csv(output_path: str, provider_id: int = None, since: int = 0) -> Dict:
    """导出测试历史为 CSV"""
    with db.get_connection() as conn:
        cursor = conn.cursor()

        if provider_id:
            cursor.execute("""
                SELECT h.*, p.name as provider_name
                FROM test_history h LEFT JOIN providers p ON h.provider_id = p.id
                WHERE h.provider_id = ? AND h.created_at >= ?
                ORDER BY h.created_at DESC
            """, (provider_id, since))
        else:
            cursor.execute("""
                SELECT h.*, p.name as provider_name
                FROM test_history h LEFT JOIN providers p ON h.provider_id = p.id
                WHERE h.created_at >= ?
                ORDER BY h.created_at DESC
            """, (since,))

        rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return {"success": False, "error": "没有可导出的测试历史"}

    fields = ["provider_name", "status", "latency", "error_type", "error_detail",
              "test_mode", "created_at"]

    try:
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                row = {k: r.get(k, "") for k in fields}
                if row.get("created_at"):
                    row["created_at"] = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(int(row["created_at"]))
                    )
                writer.writerow(row)
        return {"success": True, "count": len(rows), "path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ────────────────── 备份恢复 ──────────────────

def get_backup_dir() -> str:
    """获取备份目录"""
    from . import paths
    backup_dir = os.path.join(paths.get_data_dir(), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def create_backup(target_path: str = None) -> Dict:
    """备份数据库"""
    src = db.get_db_path()
    if not os.path.exists(src):
        return {"success": False, "error": "数据库文件不存在"}

    if not target_path:
        backup_dir = get_backup_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        target_path = os.path.join(backup_dir, f"providers_backup_{ts}.db")

    try:
        # 数据库启用了 WAL，直接 copy 主文件会丢失未 checkpoint 的写入。
        # 使用 sqlite3 在线备份 API，保证备份是完整一致的快照。
        src_conn = sqlite3.connect(src)
        try:
            dst_conn = sqlite3.connect(target_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        # 清理旧备份（保留最近 7 份）
        _cleanup_old_backups(keep=7)
        return {"success": True, "path": target_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def restore_backup(backup_path: str) -> Dict:
    """从备份恢复数据库"""
    if not os.path.exists(backup_path):
        return {"success": False, "error": "备份文件不存在"}

    current = db.get_db_path()

    try:
        # 先备份当前数据库（用在线备份保证一致性）
        safety_path = current + ".pre_restore"
        if os.path.exists(current):
            src_conn = sqlite3.connect(current)
            try:
                dst_conn = sqlite3.connect(safety_path)
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()

        # 清掉残留的 WAL/SHM，否则下次打开时旧 WAL 会回放到刚恢复的库上
        for suffix in ("-wal", "-shm"):
            stale = current + suffix
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass

        # 恢复
        shutil.copy2(backup_path, current)
        return {"success": True, "safety_backup": safety_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_backups() -> List[Dict]:
    """列出所有备份文件"""
    backup_dir = get_backup_dir()
    backups = []
    for name in sorted(os.listdir(backup_dir), reverse=True):
        if name.endswith(".db"):
            path = os.path.join(backup_dir, name)
            size = os.path.getsize(path)
            mtime = int(os.path.getmtime(path))
            backups.append({
                "name": name,
                "path": path,
                "size": size,
                "time": mtime,
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
            })
    return backups


def _cleanup_old_backups(keep: int = 7):
    """保留最近 N 份备份，删除旧的"""
    backups = list_backups()
    if len(backups) <= keep:
        return
    for old in backups[keep:]:
        try:
            os.remove(old["path"])
        except Exception:
            pass


def create_auto_backup() -> dict:
    """按设置创建自动备份（供定时器调用）"""
    enabled = db.get_setting("auto_backup_enabled")
    if enabled not in ("true", "1"):
        return {"success": False, "skipped": True, "error": "auto backup disabled"}
    try:
        keep = int(db.get_setting("auto_backup_keep") or "10")
    except Exception:
        keep = 10
    result = create_backup()
    if result.get("success"):
        _cleanup_old_backups(max(1, keep))
    return result
