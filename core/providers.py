"""
供应商管理模块
"""

import sqlite3
import os
import json
import shutil
import subprocess
import sys
import hashlib
import datetime
from typing import List, Dict, Any, Optional
from . import db


def import_from_ccswitch() -> Dict[str, Any]:
    """从 cc-switch 数据库导入供应商 (位于 %USERPROFILE%\\.cc-switch\\cc-switch.db)"""
    found = db.discover_ccswitch_db()
    if not found:
        std = db.get_ccswitch_db_path()
        return {
            "success": False,
            "error": f"未找到 cc-switch 数据库 (期望位置 {std}),请先安装 cc-switch 或手动选择 .db 文件",
            "imported": 0,
            "missing_db": True,
        }
    return import_from_path(found)


def _extract_provider_from_ccswitch_row(row_dict: dict) -> Optional[dict]:
    """
    从 cc-switch 真实 providers 表的行 (列含 settings_config JSON) 抽取本库可用的 provider 数据

    cc-switch 各 app_type 的 settings_config 结构:
      - hermes  : {"base_url":..., "api_key":..., "model":..., "models":[...]}
      - claude  : {"env": {"ANTHROPIC_BASE_URL":..., "ANTHROPIC_API_KEY":..., "ANTHROPIC_AUTH_TOKEN":..., "ANTHROPIC_MODEL":...}}
      - codex   : {"auth":..., "config":..., 有时 env 含 OPENAI_BASE_URL}
      - gemini  : {"env": {...}, "config": {...}}
    同表还有顶层字段: name / app_type / category / notes / website_url / is_current / sort_index
    """
    name = (row_dict.get("name") or "").strip()
    if not name:
        return None

    app_type = row_dict.get("app_type") or "claude"
    # 只导入 claude 类型
    if app_type != "claude":
        return None
    is_current = bool(row_dict.get("is_current"))
    role = "当前" if is_current else "备用"

    # 解析 settings_config JSON
    sc_raw = row_dict.get("settings_config")
    sc = {}
    if sc_raw and isinstance(sc_raw, str):
        try:
            sc = json.loads(sc_raw)
        except Exception:
            sc = {}
    elif isinstance(sc_raw, dict):
        sc = sc_raw

    endpoint = ""
    api_key = ""
    default_model = ""

    env = sc.get("env", {}) if isinstance(sc, dict) else {}
    if not isinstance(env, dict):
        env = {}

    if app_type == "claude":
        endpoint = (env.get("ANTHROPIC_BASE_URL") or "").strip()
        # API Key 优先 ANTHROPIC_API_KEY, 没有则用 AUTH_TOKEN (许多供应商只配后者)
        api_key = (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
        default_model = (env.get("ANTHROPIC_MODEL") or "").strip()
    elif app_type == "hermes":
        endpoint = (sc.get("base_url") or env.get("OPENAI_BASE_URL") or "").strip()
        api_key = (sc.get("api_key") or env.get("OPENAI_API_KEY") or "").strip()
        default_model = (sc.get("model") or "").strip()
    elif app_type == "codex":
        endpoint = (env.get("OPENAI_BASE_URL") or env.get("ANTHROPIC_BASE_URL") or "").strip()
        api_key = (env.get("OPENAI_API_KEY") or env.get("ANTHROPIC_API_KEY") or "").strip()
        default_model = (env.get("OPENAI_MODEL") or env.get("ANTHROPIC_MODEL") or "").strip()
    elif app_type == "gemini":
        endpoint = (env.get("GEMINI_BASE_URL") or env.get("GOOGLE_BASE_URL") or "").strip()
        api_key = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()
        default_model = (env.get("GEMINI_MODEL") or "").strip()
    else:
        # 通用兜底
        endpoint = (env.get("BASE_URL") or env.get("ENDPOINT") or "").strip()
        api_key = (env.get("API_KEY") or env.get("KEY") or "").strip()
        default_model = (env.get("MODEL") or "").strip()

    return {
        "name": name,
        "app_type": app_type,
        "role": role,
        "endpoint": endpoint,
        "api_key": api_key,
        "website": row_dict.get("website_url") or "",
        "category": row_dict.get("category") or "",
        "notes": row_dict.get("notes") or "",
        "default_model": default_model,
        "status": "pending",
    }


def import_from_path(db_path: str) -> Dict[str, Any]:
    """
    从指定 SQLite 数据库文件导入供应商
    支持两种 schema:
    1. cc-switch 标准: providers 表 + settings_config JSON (主要路径)
    2. 简化版: providers 表直接含 endpoint/api_key 字段
    """
    if not db_path or not os.path.exists(db_path):
        return {
            "success": False,
            "error": "数据库文件不存在",
            "imported": 0,
        }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {r[0] for r in cursor.fetchall()}

        if "providers" not in table_names:
            conn.close()
            return {
                "success": False,
                "error": "数据库表结构不兼容 (未找到 providers 表)",
                "imported": 0,
            }

        # 按 is_current desc, sort_index asc, id asc 排序 —— 让当前 provider 排第一
        try:
            cursor.execute("SELECT * FROM providers ORDER BY is_current DESC, sort_index ASC, id ASC")
        except sqlite3.OperationalError:
            cursor.execute("SELECT * FROM providers ORDER BY id")

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {
                "success": True,
                "message": "源数据库为空,没有可导入的供应商",
                "imported": 0,
                "skipped": 0,
            }

        # 判断是 cc-switch 真实 schema (含 settings_config) 还是简化 schema
        first_keys = set(rows[0].keys())
        has_settings_config = "settings_config" in first_keys

        existing_providers = db.get_providers()
        existing_names = {p["name"] for p in existing_providers}

        imported_count = 0
        skipped_count = 0

        for row in rows:
            row_dict = dict(row)

            if has_settings_config:
                provider_data = _extract_provider_from_ccswitch_row(row_dict)
                if not provider_data:
                    skipped_count += 1
                    continue
            else:
                # 简化 schema: 直接读顶层的 endpoint/api_key 等
                # 只导入 claude 类型
                if (row_dict.get("app_type") or "claude") != "claude":
                    skipped_count += 1
                    continue
                name = (row_dict.get("name") or "").strip()
                if not name:
                    skipped_count += 1
                    continue
                provider_data = {
                    "name": name,
                    "app_type": row_dict.get("app_type", "claude"),
                    "role": row_dict.get("role", "备用"),
                    "endpoint": row_dict.get("endpoint", ""),
                    "api_key": row_dict.get("api_key", ""),
                    "website": row_dict.get("website", ""),
                    "category": row_dict.get("category", ""),
                    "notes": row_dict.get("notes", ""),
                    "default_model": row_dict.get("default_model", ""),
                    "status": "pending",
                }

            name = provider_data["name"]
            if name in existing_names:
                skipped_count += 1
                continue

            db.add_provider(provider_data)
            imported_count += 1

        return {
            "success": True,
            "imported": imported_count,
            "skipped": skipped_count,
            "message": f"成功导入 {imported_count} 个供应商，跳过 {skipped_count} 个",
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "imported": 0,
        }


def _launch_via_windows_terminal_ccps() -> Dict[str, Any]:
    """
    通过 Windows Terminal 复用窗口，在 PowerShell 7 中执行 ccps。
    ccps 定义在用户 profile 中: claude --permission-mode bypassPermissions
    """
    wt = shutil.which("wt") or shutil.which("wt.exe")
    pwsh = shutil.which("pwsh") or shutil.which("pwsh.exe")
    if not wt:
        return {"success": False, "error": "未找到 Windows Terminal (wt)"}
    if not pwsh:
        return {"success": False, "error": "未找到 PowerShell 7 (pwsh)"}

    # -w 0: 复用已有 WT 窗口(无则新建); nt: 新开标签页
    # 不使用 -NoProfile，以便加载 profile 中的 ccps 函数
    cmd = [
        wt,
        "-w", "0",
        "nt",
        "--",
        pwsh,
        "-NoExit",
        "-Command", "ccps",
    ]
    try:
        # CREATE_NO_WINDOW 避免从 GUI 启动时闪一下控制台，不影响 WT 窗口本身
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(cmd, shell=False, creationflags=flags)
        return {
            "success": True,
            "message": "已在 Windows Terminal (PowerShell 7) 中执行 ccps",
        }
    except (FileNotFoundError, OSError) as e:
        return {"success": False, "error": str(e)}


def launch_claude_code() -> Dict[str, Any]:
    """
    启动 Claude Code。

    Windows 优先: Windows Terminal 复用窗口 + PowerShell 7 执行 ccps
    (ccps = claude --permission-mode bypassPermissions)。
    失败则回退到 claude / npx 直接启动。

    Returns:
        启动结果
    """
    try:
        if sys.platform == "win32":
            result = _launch_via_windows_terminal_ccps()
            if result.get("success"):
                return result

        # 回退: 直接启动 claude
        commands = [
            ["claude"],
            ["claude", "code"],
            ["npx", "@anthropic-ai/claude-code"],
        ]

        for cmd in commands:
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                        shell=False,
                    )
                else:
                    subprocess.Popen(cmd, start_new_session=True)

                return {"success": True, "message": "Claude Code 已启动"}
            except (FileNotFoundError, OSError):
                continue

        return {
            "success": False,
            "error": "未找到 Claude Code，请确保已安装",
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def _get_claude_settings_path() -> str:
    """获取 settings.json 路径，默认 ~/.claude/settings.json"""
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _get_claude_base_url(endpoint: str) -> str:
    """Claude Code 会自行追加 /v1/messages，写入配置时去掉末尾 /v1。"""
    endpoint = (endpoint or "").rstrip("/")
    if endpoint.endswith("/v1"):
        return endpoint[:-3].rstrip("/")
    return endpoint


def set_current_provider(provider_id: int) -> Dict[str, Any]:
    """将指定供应商设为当前 Claude Code 配置（写入 ~/.claude/settings.json）"""
    provider = db.get_provider_by_id(provider_id)
    if not provider:
        return {"success": False, "error": "供应商不存在"}

    endpoint = (provider.get("endpoint") or "").rstrip("/")
    claude_base_url = _get_claude_base_url(endpoint)
    api_key = provider.get("api_key") or ""
    model = provider.get("default_model") or ""

    if not endpoint:
        return {"success": False, "error": "未配置端点"}

    # 读取现有 settings.json
    settings_path = _get_claude_settings_path()
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    # 更新 env
    env = settings.get("env", {})
    env["ANTHROPIC_BASE_URL"] = claude_base_url
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
        # 同时设置 AUTH_TOKEN 以兼容部分代理
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    if model:
        env["ANTHROPIC_MODEL"] = model
    settings["env"] = env

    # 原子写入：先写临时文件再 os.replace，避免写入中途崩溃/并发留下半个 JSON
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        tmp_path = settings_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, settings_path)
    except Exception as e:
        return {"success": False, "error": f"写入 settings.json 失败: {e}"}

    # 更新本库 role 字段
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE providers SET role='备用'")
            cursor.execute("UPDATE providers SET role='当前' WHERE id=?", (provider_id,))
    except Exception:
        pass

    name = provider.get("name", "")
    return {"success": True, "message": f"{name} 已设为当前配置"}


def sync_current_from_settings() -> None:
    """启动时从 settings.json 同步当前配置到本库"""
    settings_path = _get_claude_settings_path()
    if not os.path.exists(settings_path):
        return

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        env = settings.get("env", {})
        current_url = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/").lower()
    except Exception:
        return

    if not current_url:
        return

    # 匹配 provider
    try:
        providers = db.get_providers()
        matched_id = None
        for p in providers:
            p_url = (p.get("endpoint") or "").rstrip("/").lower()
            if p_url == current_url:
                matched_id = p["id"]
                break

        # 更新 role
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE providers SET role='备用'")
            if matched_id:
                cursor.execute("UPDATE providers SET role='当前' WHERE id=?", (matched_id,))
    except Exception:
        pass


def _read_claude_effective_config() -> Dict[str, str]:
    """
    读取 Claude Code 当前生效配置:进程环境变量优先,settings.json 的 env 块兜底。
    返回 {"endpoint","api_key","model"}。
    """
    # 先尝试 settings.json 的 env 块作为兜底来源
    settings_env: Dict[str, str] = {}
    settings_path = _get_claude_settings_path()
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings_env = (json.load(f) or {}).get("env", {}) or {}
        except Exception:
            settings_env = {}

    def pick(env_key: str) -> str:
        # 环境变量优先,settings.json 兜底
        v = os.environ.get(env_key)
        if v:
            return v.strip()
        return (settings_env.get(env_key) or "").strip()

    endpoint = pick("ANTHROPIC_BASE_URL")
    # API Key 优先 ANTHROPIC_API_KEY,没有则用 AUTH_TOKEN
    api_key = pick("ANTHROPIC_API_KEY") or pick("ANTHROPIC_AUTH_TOKEN")
    model = pick("ANTHROPIC_MODEL")
    return {"endpoint": endpoint, "api_key": api_key, "model": model}


def _key_fingerprint(api_key: str) -> str:
    """生成 api key 指纹:前8位 + sha256前8位,避免明文到处比对。"""
    if not api_key:
        return ""
    head = api_key[:8]
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    return f"{head}:{digest}"


def _host_from_endpoint(endpoint: str) -> str:
    """从 endpoint 提取 host,用于自动命名供应商。"""
    from urllib.parse import urlparse
    raw = (endpoint or "").strip()
    if not raw:
        return "claude"
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).netloc or "").split("@")[-1].split(":")[0].strip().lower()
    except Exception:
        host = ""
    return host or "claude"


def _unique_provider_name(base: str) -> str:
    """避免重名: base, base (2), base (3)..."""
    names = {p.get("name") or "" for p in db.get_providers()}
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


def _mark_provider_current(provider_id: int) -> None:
    """把指定供应商标为当前,其余标备用。"""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE providers SET role='备用'")
            cursor.execute("UPDATE providers SET role='当前' WHERE id=?", (provider_id,))
    except Exception:
        pass


def auto_detect_current_provider(force: bool = False) -> Dict[str, Any]:
    """
    检测 Claude Code 当前生效配置,并与本库供应商对比:
    - URL 匹配: 视为同一供应商,必要时更新 key/model,标为当前(不新建)。
    - URL 未命中但 api key 指纹命中: 更新该 provider 的 endpoint/model。
    - URL 与 key 都未命中: 新建一条供应商,写入当前配置。
    force=False(启动默认): 指纹与上次相同则跳过,避免重复写入。
    force=True(手动导入): 忽略指纹跳过,始终比对并返回可读 message。
    """
    from . import validators

    cfg = _read_claude_effective_config()
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = cfg.get("api_key") or ""
    model = cfg.get("model") or ""

    if not endpoint:
        return {
            "success": False,
            "action": "error",
            "reason": "无可同步的端点",
            "error": "未检测到 Claude Code 配置(无 ANTHROPIC_BASE_URL)。请检查 ~/.claude/settings.json 的 env 块。",
            "message": "未检测到 Claude Code 配置(无 ANTHROPIC_BASE_URL)",
        }

    # 归一化 base_url(去尾 /v1 与斜杠),用于匹配与指纹
    store_endpoint = _get_claude_base_url(endpoint)
    norm_endpoint = store_endpoint.rstrip("/").lower()
    key_fp = _key_fingerprint(api_key)
    current_fingerprint = f"{norm_endpoint}|{model}|{key_fp}"

    # 指纹与上次相同则完全跳过(配置未变)——仅自动同步路径
    last_fp = db.get_setting("last_synced_claude_fingerprint") or ""
    if not force and last_fp == current_fingerprint:
        return {
            "success": True,
            "action": "skipped",
            "reason": "配置未变,跳过",
            "message": "Claude Code 配置未变化,已跳过",
            "fingerprint": current_fingerprint,
        }

    try:
        all_providers = db.get_providers()
        url_match = None
        key_match = None
        for p in all_providers:
            p_url = (p.get("endpoint") or "").rstrip("/").lower()
            p_norm = _get_claude_base_url(p_url).rstrip("/").lower() if p_url else ""
            if p_norm == norm_endpoint:
                url_match = p
                break
            if key_fp and key_match is None and _key_fingerprint(p.get("api_key") or "") == key_fp:
                key_match = p

        # URL 命中: 同一供应商,更新差异字段并标当前
        if url_match:
            pid = url_match["id"]
            update_data: Dict[str, Any] = {}
            if api_key and _key_fingerprint(api_key) != _key_fingerprint(url_match.get("api_key") or ""):
                update_data["api_key"] = api_key
            if model and model != (url_match.get("default_model") or ""):
                update_data["default_model"] = model
            # 存储统一去 /v1 的 base,与 set_current_provider 一致
            existing_norm = _get_claude_base_url(url_match.get("endpoint") or "").rstrip("/")
            if existing_norm != store_endpoint:
                update_data["endpoint"] = store_endpoint

            changed = False
            if update_data:
                ok, err = validators.validate_provider(update_data, is_update=True)
                if not ok:
                    return {
                        "success": False,
                        "action": "error",
                        "reason": f"校验失败: {err}",
                        "error": err,
                        "message": f"更新供应商失败: {err}",
                    }
                db.update_provider(pid, update_data)
                changed = True

            _mark_provider_current(pid)
            db.set_setting("last_synced_claude_fingerprint", current_fingerprint)
            name = url_match.get("name") or str(pid)
            if changed:
                msg = f"已匹配供应商「{name}」并同步 key/模型,标为当前"
                action = "updated"
            else:
                msg = f"配置已存在于供应商「{name}」,已标为当前"
                action = "exists"
            return {
                "success": True,
                "action": action,
                "reason": "URL 已存在",
                "message": msg,
                "provider_id": pid,
                "fingerprint": current_fingerprint,
            }

        # key 指纹命中: 换端点但同一 key,更新已有 provider
        if key_match:
            pid = key_match["id"]
            update_data = {"endpoint": store_endpoint}
            if model:
                update_data["default_model"] = model
            if api_key:
                update_data["api_key"] = api_key
            ok, err = validators.validate_provider(update_data, is_update=True)
            if not ok:
                return {
                    "success": False,
                    "action": "error",
                    "reason": f"校验失败: {err}",
                    "error": err,
                    "message": f"更新供应商失败: {err}",
                }
            db.update_provider(pid, update_data)
            _mark_provider_current(pid)
            db.set_setting("last_synced_claude_fingerprint", current_fingerprint)
            name = key_match.get("name") or str(pid)
            return {
                "success": True,
                "action": "updated",
                "reason": "key 指纹命中,已更新端点",
                "message": f"已根据同一 API Key 更新供应商「{name}」的端点",
                "provider_id": pid,
                "fingerprint": current_fingerprint,
            }

        # 都未命中: 新建
        today = datetime.date.today().isoformat()
        host = _host_from_endpoint(store_endpoint)
        base_name = f"Claude · {host}"
        provider_data = {
            "name": _unique_provider_name(base_name),
            "app_type": "claude",
            "role": "当前",
            "endpoint": store_endpoint,
            "api_key": api_key,
            "default_model": model,
            "api_format": "anthropic_messages",
            "notes": f"同步自 Claude Code 当前配置 ({today})",
            "status": "pending",
        }
        ok, err = validators.validate_provider(provider_data, is_update=False)
        if not ok:
            return {
                "success": False,
                "action": "error",
                "reason": f"校验失败: {err}",
                "error": err,
                "message": f"新建供应商失败: {err}",
            }
        new_id = db.add_provider(provider_data)
        _mark_provider_current(new_id)
        db.set_setting("last_synced_claude_fingerprint", current_fingerprint)
        return {
            "success": True,
            "action": "created",
            "reason": "新增供应商",
            "message": f"已新建供应商「{provider_data['name']}」并导入当前 Claude Code 配置",
            "provider_id": new_id,
            "fingerprint": current_fingerprint,
        }

    except Exception as e:
        return {
            "success": False,
            "action": "error",
            "reason": f"异常: {e}",
            "error": str(e),
            "message": f"同步失败: {e}",
        }


def sync_claude_code_provider(force: bool = True) -> Dict[str, Any]:
    """
    导入入口: 主动读取 Claude Code 当前配置,与已有供应商对比,
    不同则新建,相同则标记/更新。默认 force=True(手动导入不跳过)。
    """
    return auto_detect_current_provider(force=force)


def _format_provider(p: Dict) -> Dict[str, Any]:
    """格式化单个供应商数据（用于增量更新）"""
    status = p.get("status", "pending")
    latency = p.get("latency")
    latency_str = f"{latency}ms" if latency else "-"

    api_key = p.get("api_key", "")
    if api_key and len(api_key) > 8:
        key_masked = api_key[:4] + "..." + api_key[-4:]
    elif api_key:
        key_masked = "****"
    else:
        key_masked = "-"

    detail_map = {
        "ok": p.get("test_detail") or "正常",
        "fail": p.get("test_detail") or "连接失败",
        "testing": "测试中...",
        "pending": "未测试",
    }
    detail = detail_map.get(status, status)

    return {
        "id": str(p["id"]),
        "name": p.get("name", ""),
        "app_type": p.get("app_type", "claude"),
        "role": p.get("role", "备用"),
        "endpoint": p.get("endpoint", ""),
        "key": key_masked,
        "api_format": p.get("api_format", ""),
        "status": status,
        "latency": latency_str,
        "detail": detail,
        "category": p.get("category", ""),
        "notes": p.get("notes", ""),
        "default_model": p.get("default_model", ""),
        "last_test_time": p.get("last_test_time"),
        "created_at": p.get("created_at"),
    }


def get_provider_list_formatted() -> List[Dict[str, Any]]:
    """
    获取格式化的供应商列表（用于前端展示）

    Returns:
        格式化后的供应商列表
    """
    # 复用 _format_provider：不下发明文 api_key，前端需要时
    # 通过 get_provider_key(id) 按需获取
    return [_format_provider(p) for p in db.get_providers()]


def get_provider_key(provider_id: int) -> Dict[str, Any]:
    """按 ID 返回单个供应商的明文 API Key（仅供前端复制/显示时按需调用）"""
    try:
        p = db.get_provider(int(provider_id))
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的供应商 ID"}
    if not p:
        return {"success": False, "error": "供应商不存在"}
    return {"success": True, "api_key": p.get("api_key", "")}
