"""
供应商管理模块
"""

import sqlite3
import os
import glob
import json
import shutil
import subprocess
import sys
import hashlib
import datetime
import re
import tomllib
import time
import threading
import urllib.parse
from typing import List, Dict, Any, Optional
from . import db

# Codex 模型目录缓存：{endpoint_key: (models, expire_ts)}
# 避免每次「设为当前 Codex」都同步阻塞地打 /models；后台刷新命中缓存即可秒回。
_codex_catalog_cache: Dict[str, tuple] = {}
_codex_catalog_lock = threading.Lock()
_CODEX_CATALOG_TTL = 300  # 5 分钟


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
    # 只导入 claude / codex 类型
    if app_type not in ("claude", "codex"):
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
    codex_api_format = ""

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
        codex_api_format = "openai_responses"
    elif app_type == "gemini":
        endpoint = (env.get("GEMINI_BASE_URL") or env.get("GOOGLE_BASE_URL") or "").strip()
        api_key = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()
        default_model = (env.get("GEMINI_MODEL") or "").strip()
    else:
        # 通用兜底
        endpoint = (env.get("BASE_URL") or env.get("ENDPOINT") or "").strip()
        api_key = (env.get("API_KEY") or env.get("KEY") or "").strip()
        default_model = (env.get("MODEL") or "").strip()

    api_format = codex_api_format if app_type == "codex" else ""
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
        "api_format": api_format,
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
        # try/finally 保证任何异常路径都关闭连接
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            table_names = {r[0] for r in cursor.fetchall()}

            if "providers" not in table_names:
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
        finally:
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
                # 只导入 claude / codex 类型
                if (row_dict.get("app_type") or "claude") not in ("claude", "codex"):
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


def _get_current_codex_provider() -> Optional[Dict[str, Any]]:
    """当前 codex 绑定 role=当前的供应商,没有则返回 None。"""
    for p in db.get_providers():
        for b in p.get("apps") or []:
            if b.get("app_type") == "codex" and b.get("role") == "当前":
                return p
    return None


def _find_codex_executable() -> Optional[str]:
    """
    定位真实的 codex 可执行文件路径。

    优先真实 .exe: OpenAI/ChatGPT 桌面版安装的
    %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<hash>\\codex.exe。
    PATH 上的 `codex` 常是 npm 的 .cmd shim, CreateProcess / Windows Terminal
    无法直接执行(.cmd 需经 cmd.exe), 直接按裸命令名启动会报
    "[WinError 2] 系统找不到指定的文件"(0x80070002)。

    Returns:
        codex 可执行文件路径; 找不到返回 None(此时调用方可用 npx 兜底)。
    """
    # 1. OpenAI/ChatGPT 桌面版安装的 codex.exe
    try:
        pattern = os.path.expandvars(r"%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe")
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    except Exception:
        pass

    # 2. PATH 上的真实 codex.exe
    exe = shutil.which("codex.exe")
    if exe:
        return exe

    # 3. PATH 上的 codex(可能是 .cmd shim, 调用方需经 cmd.exe 执行)
    return shutil.which("codex") or None


def launch_codex() -> Dict[str, Any]:
    """
    启动 Codex CLI。

    Windows 优先: 定位真实 codex.exe(桌面版 %LOCALAPPDATA%\\OpenAI\\Codex\\bin)后
    通过 Windows Terminal 复用窗口启动, 避免 npm 的 .cmd shim 无法被 wt 直接执行。
    把当前 Codex 供应商的 API Key 注入子进程环境。
    失败则回退到直接启动 codex.exe / cmd /c codex / npx。

    Returns:
        启动结果
    """
    env = dict(os.environ)
    current = _get_current_codex_provider()
    api_key = (current.get("api_key") or "").strip() if current else ""
    if api_key:
        env["OPENAI_API_KEY"] = api_key

    try:
        if sys.platform == "win32":
            exe = _find_codex_executable()
            wt = shutil.which("wt") or shutil.which("wt.exe")
            if wt and exe and exe.lower().endswith(".exe"):
                # 用绝对路径, 不再依赖 PATH 上的 codex(.cmd shim)
                cmd = [wt, "-w", "0", "nt", "--", exe]
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(cmd, shell=False, creationflags=flags, env=env)
                return {"success": True, "message": "Codex 已启动"}
    except (FileNotFoundError, OSError):
        pass

    # 回退: 直接启动 codex
    exe = _find_codex_executable()
    if exe:
        if exe.lower().endswith(".exe"):
            commands = [[exe]]
        else:
            # .cmd/.bat shim 需经 cmd.exe 执行
            commands = [["cmd", "/c", exe]]
    else:
        commands = [["npx", "@openai/codex"]]

    for cmd in commands:
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    shell=False,
                    env=env,
                )
            else:
                subprocess.Popen(cmd, start_new_session=True, env=env)
            return {"success": True, "message": "Codex 已启动"}
        except (FileNotFoundError, OSError):
            continue

    return {
        "success": False,
        "error": "未找到 Codex，请确保已安装",
    }


CHATGPT_DESKTOP_AUMID = "OpenAI.Codex_2p2nqsd0c76g0!App"


def launch_chatgpt_desktop() -> Dict[str, Any]:
    """
    启动 ChatGPT 桌面版（Store 打包的 OpenAI.Codex 应用，即 ChatGPT + Codex 桌面端，
    jun ma 账号所在的那个应用）。

    通过 explorer.exe shell:AppsFolder 的 AUMID 启动，避免直接访问受 ACL 保护的
    WindowsApps 目录。包族/版本更新不影响 AUMID（发布者哈希不变）。
    回退: 若常量 AUMID 启动异常，动态解析 PackageFamilyName 再试一次。
    """
    aumids = [CHATGPT_DESKTOP_AUMID]
    # 兜底: 动态解析包族名（极端情况发布者哈希变化）
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-AppxPackage -Name 'OpenAI.Codex' | "
                "Select-Object -ExpandProperty PackageFamilyName -ErrorAction SilentlyContinue)",
            ],
            capture_output=True, text=True, timeout=10,
        )
        fam = (out.stdout or "").strip()
        if fam:
            aumids.insert(0, f"{fam}!App")
    except Exception:
        pass

    for aumid in aumids:
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"success": True, "message": "ChatGPT 桌面版已启动"}
        except (FileNotFoundError, OSError):
            continue
    return {"success": False, "error": "未找到 ChatGPT 桌面版应用，请确认已安装 OpenAI Codex 应用"}


def _get_claude_settings_path() -> str:
    """获取 settings.json 路径，默认 ~/.claude/settings.json"""
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _get_claude_base_url(endpoint: str) -> str:
    """Claude Code 会自行追加 /v1/messages，写入配置时去掉末尾 /v1。"""
    endpoint = (endpoint or "").rstrip("/")
    if endpoint.endswith("/v1"):
        return endpoint[:-3].rstrip("/")
    return endpoint


def _binding(provider: Dict[str, Any], app_type: str) -> Optional[Dict[str, Any]]:
    """取供应商在指定应用的绑定；缺则返回 None。"""
    for b in provider.get("apps") or []:
        if b.get("app_type") == app_type:
            return b
    return None


def _resolve_app_type(provider: Dict[str, Any], app_type: Optional[str] = None) -> str:
    """解析要设置的应用：显式 app_type 优先；缺省按 legacy app_type；'both' 缺省取 claude(主绑定)。"""
    if app_type:
        return app_type.lower()
    pa = (provider.get("app_type") or "claude").lower()
    if pa == "codex":
        return "codex"
    return "claude"


def set_current_provider(provider_id: int, app_type: Optional[str] = None) -> Dict[str, Any]:
    """将指定供应商设为指定应用的当前配置（app_type: 'claude' | 'codex'，可空向后兼容）。"""
    provider = db.get_provider_by_id(provider_id)
    if not provider:
        return {"success": False, "error": "供应商不存在"}
    app = _resolve_app_type(provider, app_type)
    binding = _binding(provider, app)
    if not binding:
        return {"success": False, "error": f"该供应商未绑定 {app} 应用"}
    if app == "codex":
        return _set_codex_current(provider, binding)
    return _set_claude_current(provider, binding)


def _set_claude_current(provider: Dict[str, Any], binding: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """将指定供应商设为当前 Claude Code 配置（写入 ~/.claude/settings.json）。
    binding 为 claude 绑定（含独立端点/模型），缺省回退 provider 顶层字段。"""
    b = binding or provider
    endpoint = (b.get("endpoint") or "").rstrip("/")
    claude_base_url = _get_claude_base_url(endpoint)
    api_key = provider.get("api_key") or ""
    model = b.get("default_model") or ""
    reasoning_effort = (b.get("reasoning_effort") or "").strip()
    context_length = b.get("context_length") or 0

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
    # 推理强度: Claude Code 通过 env 透传（部分代理/模型支持）
    if reasoning_effort:
        env["ANTHROPIC_REASONING_EFFORT"] = reasoning_effort
    elif "ANTHROPIC_REASONING_EFFORT" in env:
        del env["ANTHROPIC_REASONING_EFFORT"]
    # 上下文长度: 通过 CLAUDE_CODE_MAX_OUTPUT_TOKENS 控制输出上限
    if context_length and int(context_length) > 0:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(context_length)
    elif "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in env:
        del env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
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

    # 更新本库 role 字段（仅同应用内），并标记为第三方模式
    _mark_app_current(provider["id"], "claude")
    db.set_setting("claude_current_mode", "provider")

    name = provider.get("name", "")
    return {"success": True, "message": f"{name} 已设为当前 Claude Code 配置"}


def sync_current_from_settings() -> None:
    """启动时从 settings.json 同步 Claude 当前模式到本库（匹配 claude 绑定，不干扰 codex）。"""
    settings_path = _get_claude_settings_path()
    if not os.path.exists(settings_path):
        return

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        env = settings.get("env", {}) or {}
        current_url = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/").lower()
    except Exception:
        return

    try:
        # settings.json 无第三方覆盖 → 官方模式
        if not current_url:
            db.clear_app_roles("claude")
            db.set_setting("claude_current_mode", "official")
            return

        # 匹配 claude 绑定
        matched_id = None
        for p in db.get_providers():
            for b in p.get("apps") or []:
                if b.get("app_type") != "claude":
                    continue
                b_url = (b.get("endpoint") or "").rstrip("/").lower()
                if b_url == current_url:
                    matched_id = p["id"]
                    break
            if matched_id:
                break

        if matched_id:
            db.mark_app_current(matched_id, "claude")
            db.set_setting("claude_current_mode", "provider")
        else:
            # settings.json 有第三方覆盖但库里无匹配供应商 → 仍是第三方模式，
            # 提示用户「同步」或手动配置，而不是误判为官方。
            db.set_setting("claude_current_mode", "provider")
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


# ────────────────── Codex 配置支持 ──────────────────

CODEX_PROVIDER_SLUG = "api_monitor"
CODEX_ENV_KEY = "OPENAI_API_KEY"


def _get_codex_config_path() -> str:
    """Codex 配置路径: ~/.codex/config.toml"""
    return os.path.join(os.path.expanduser("~"), ".codex", "config.toml")


def _toml_encode(value) -> str:
    """把 Python 标量编码为 TOML 值: str → 基本字符串, bool/int/float → 裸字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _toml_set_keys(text: str, key_values: Dict[str, Any], section: Optional[str] = None) -> str:
    """
    在 TOML 文本中更新/插入 key = value 行,保留其余内容(注释/其它表/未知键)。
    - section=None: 顶层(非缩进)键
    - section="a.b": 该 [a.b] 表内的键
    缺失的顶层键插到第一个 [ 前;缺失的表追加到文件末尾。
    """
    lines = text.split("\n") if text else []
    current_section: Optional[str] = None
    section_found = False
    replaced = set()
    out = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            current_section = stripped.split("]", 1)[0][1:].strip()
            if current_section == section:
                section_found = True
            out.append(line)
            continue
        if current_section == section:
            m = re.match(r'^(\s*)([A-Za-z0-9_-]+)\s*=', line)
            if m and m.group(2) in key_values:
                key = m.group(2)
                if key in replaced:
                    # 该键已替换过 → 删除重复行（历史 BOM/残留可能产生重复 key）
                    continue
                val = key_values[key]
                replaced.add(key)
                if val is None:  # None 表示删除该键
                    continue
                out.append(f"{m.group(1)}{key} = {_toml_encode(val)}")
                continue
        elif section is None:
            m = re.match(r'^([A-Za-z0-9_-]+)\s*=', line)
            if m and m.group(1) in key_values:
                key = m.group(1)
                if key in replaced:
                    # 该键已替换过 → 删除重复行
                    continue
                val = key_values[key]
                replaced.add(key)
                if val is None:  # None 表示删除该键
                    continue
                out.append(f"{key} = {_toml_encode(val)}")
                continue
        out.append(line)

    # 顶层缺失键: 插到第一个 [ 表之前
    if section is None:
        missing = [k for k in key_values if k not in replaced and key_values[k] is not None]
        if missing:
            idx = len(out)
            for j, l in enumerate(out):
                if l.strip().startswith("["):
                    idx = j
                    break
            insert = [f"{k} = {_toml_encode(key_values[k])}" for k in missing]
            out[idx:idx] = insert

    # 表缺失: 追加到文件末尾
    if section is not None and not section_found:
        out.append(f"[{section}]")
        for k, v in key_values.items():
            if v is None:
                continue
            out.append(f"  {k} = {_toml_encode(v)}")
    # 表已存在但缺新键: 插到该表末尾(下一个 [ 之前或文件末尾)
    elif section is not None:
        missing = [k for k in key_values if k not in replaced and key_values[k] is not None]
        if missing:
            insert_idx = len(out)
            in_section = False
            for j, l in enumerate(out):
                if l.strip().startswith("["):
                    header = l.strip().split("]", 1)[0][1:].strip()
                    if header == section:
                        in_section = True
                    elif in_section:
                        insert_idx = j
                        break
            insert = [f"  {k} = {_toml_encode(key_values[k])}" for k in missing]
            out[insert_idx:insert_idx] = insert

    return "\n".join(out)


def _toml_remove_section(text: str, section: str) -> str:
    """从 TOML 文本中移除指定 [section] 及其所有缩进内容/嵌套子表，保留其它内容。"""
    lines = text.split("\n") if text else []
    out = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            header = stripped.split("]", 1)[0][1:].strip()
            if header == section or header.startswith(section + "."):
                skipping = True
                continue
            skipping = False
        if skipping:
            continue
        out.append(line)
    return "\n".join(out)


def _set_codex_plugins_enabled(text: str, enabled: bool) -> str:
    """批量设置 config.toml 中所有 [plugins."..."] 表的 enabled。

    第三方中继场景用不到 Codex 官方插件（browser/chrome/文档/表格/网站等），
    而它们自带的 skills 会占满 skills 上下文预算，触发 Codex 启动时的
    "Skill descriptions were shortened" 警告。切到第三方供应商时统一禁用，
    切回官方模式时（_clear_codex_official）恢复为启用。
    """
    try:
        cfg = tomllib.loads(text)
    except Exception:
        # 配置暂时解析不了（如残留重复 key），不强行改动，避免越改越坏
        return text
    plugins = cfg.get("plugins") or {}
    if not isinstance(plugins, dict):
        return text
    for name in plugins:
        text = _toml_set_keys(text, {"enabled": enabled}, section=f'plugins."{name}"')
    return text


def _collect_codex_skill_paths() -> List[str]:
    """收集 Codex 会加载的全部 SKILL.md 路径（agents / codex / marketplace）。

    主要来源是 ~/.agents/skills（用户 agent 环境的 skills 会被 Codex 一并加载），
    加上 ~/.codex/skills 与 marketplace 插件 skills。缺失目录安全跳过。
    """
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, ".agents", "skills"),
        os.path.join(home, ".codex", "skills"),
        os.path.join(home, ".codex", ".tmp", "bundled-marketplaces", "openai-bundled"),
        os.path.join(home, ".cache", "codex-runtimes", "codex-primary-runtime",
                     "plugins", "openai-primary-runtime"),
    ]
    found: List[str] = []
    for r in roots:
        if os.path.isdir(r):
            found += glob.glob(os.path.join(r, "**", "SKILL.md"), recursive=True)
    return sorted(set(found))


def _disable_codex_skills(text: str) -> str:
    """在 config.toml 末尾追加 [[skills.config]] enabled=false，禁用全部 skills。

    大量 skills（尤其 ~/.agents/skills 的几十个）会占满 Codex 的 skills 上下文预算
    （2%），触发 "Skill descriptions were shortened" 警告；第三方中继只支持
    function/web_search 工具，skills 也用不上，因此统一切换时禁用。
    切回官方模式时由 _remove_codex_skills_block 移除这些条目。
    幂等：已存在旧的 [[skills.config]] 块时先截断再重建。
    """
    paths = _collect_codex_skill_paths()
    if not paths:
        return text
    blocks = []
    for s in paths:
        blocks.append("\n[[skills.config]]\npath = %s\nenabled = false" % repr(s))
    idx = text.find("[[skills.config]]")
    base = text[:idx] if idx >= 0 else text
    return base.rstrip("\n") + "\n" + "".join(blocks) + "\n"


def _remove_codex_skills_block(text: str) -> str:
    """移除 config.toml 中的 [[skills.config]] 块（切回官方模式时恢复 skills）。"""
    lines = text.split("\n") if text else []
    out: List[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[[skills.config]]"):
            skip = True
            continue
        if skip:
            if stripped.startswith("[") and not stripped.startswith("[[skills.config]]"):
                skip = False
            else:
                continue
        out.append(line)
    return "\n".join(out)


def _read_codex_effective_config() -> Dict[str, str]:
    """
    读取 Codex 当前生效配置: ~/.codex/config.toml 的 model / model_provider /
    [model_providers.<active>] 块; API Key 从 env_key 对应环境变量读取。
    返回 {"endpoint","api_key","model","provider_name"}。
    """
    cfg: Dict = {}
    path = _get_codex_config_path()
    if os.path.exists(path):
        try:
            # utf-8-sig: 去掉 Codex 桌面版写入的 BOM，否则 tomllib 解析报 "Invalid statement"
            with open(path, "rb") as f:
                raw = f.read()
            cfg = tomllib.loads(raw.decode("utf-8-sig"))
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    provider_name = (cfg.get("model_provider") or "").strip()
    model = (cfg.get("model") or "").strip()

    endpoint = ""
    env_key = ""
    bearer_token = ""
    providers_map = cfg.get("model_providers")
    if provider_name and isinstance(providers_map, dict):
        prov = providers_map.get(provider_name)
        if isinstance(prov, dict):
            endpoint = (prov.get("base_url") or "").strip()
            env_key = (prov.get("env_key") or "").strip()
            bearer_token = (prov.get("experimental_bearer_token") or "").strip()

    api_key = ""
    if env_key:
        api_key = (os.environ.get(env_key) or "").strip()
    if not api_key:
        api_key = bearer_token
    if not api_key:
        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()

    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "model": model,
        "provider_name": provider_name,
    }


def _codex_catalog_entry(model: str) -> dict:
    """生成 Codex 模型目录（model_catalog_json）单条记录。

    字段集对齐 Codex 0.146 官方模型目录 schema（models_cache.json），
    缺字段会导致 Codex 启动时解析失败。
    """
    return {
        "slug": model,
        "display_name": model,
        "description": model,
        "base_instructions": (
            f"You are Codex, a coding agent based on {model}. "
            "You and the user share the same workspace and collaborate to achieve the user's goals."
        ),
        "model_messages": None,
        "include_skills_usage_instructions": False,
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"},
        ],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": None,
        "upgrade": None,
        "default_reasoning_summary": "none",
        "support_verbosity": False,
        "default_verbosity": "low",
        # apply_patch 在 0.146 只有 freeform（type:"custom"）一种形态，
        # 多数第三方中转拒绝 custom 工具，故置 null 不提供（用 shell 编辑）。
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text_and_image",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": False,
        "context_window": 200000,
        "max_context_window": 200000,
        "comp_hash": "3000",
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        # tool_mode: "code_mode_only" 会让 Codex 只发单个 custom 工具
        # （{type:"custom"}），多数第三方中转不支持；direct = 经典 function
        # 工具（execute_bash/apply_patch），兼容性最好。multi_agent_version
        # 置空避免附带 collaboration / spawn_agent namespace。
        "tool_mode": "direct",
        "multi_agent_version": None,
    }


def _get_codex_catalog_path() -> str:
    """Codex 自定义模型目录文件路径（应用数据目录内）。"""
    from . import paths
    return os.path.join(paths.get_data_dir(), "codex_model_catalog.json")


def _write_codex_catalog(models) -> str:
    """把模型列表写入 model_catalog_json 文件，返回文件路径；失败返回空串。

    目录仅包含本供应商真实支持的模型，让 Codex 模型选择器不再显示官方模型名。
    """
    if not models:
        return ""
    entries = []
    seen = set()
    for m in models:
        m = (m or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        entries.append(_codex_catalog_entry(m))
    if not entries:
        return ""
    path = _get_codex_catalog_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"models": entries}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return path
    except Exception:
        return ""


def _is_deepseek_endpoint(url: str) -> bool:
    """判断端点是否为 DeepSeek 官方 API（api.deepseek.com）。"""
    try:
        host = urllib.parse.urlparse(url or "").hostname or ""
    except Exception:
        host = (url or "").lower()
    host = (host or "").lower()
    return "deepseek.com" in host


def _deepseek_responses_supported(model_id: str) -> bool:
    """DeepSeek 的 Responses API 目前仅支持 deepseek-v4-flash。
    deepseek-v4-pro（及旧别名 deepseek-chat / deepseek-reasoner）尚不支持 Responses 接口，
    Codex 场景必须用 v4-flash 系列，否则 /responses 会被 DeepSeek 拒绝。"""
    return "deepseek-v4-flash" in (model_id or "").strip().lower()


def _resolve_codex_model(provider: Dict[str, Any], binding: Dict[str, Any],
                         force_fetch: bool = False) -> tuple:
    """解析要写入 config.toml 的默认模型。

    绑定有 default_model 直接用；为空时从端点 /models 拉取取首个回填。
    **安全**：DeepSeek 端点或能力过滤后发现配置模型不支持 Responses API 时，
    会同步纠正到兼容模型（写错模型会让 Codex /responses 直接报错）。
    **性能**：拉取走 _fetch_codex_models，带 5 分钟内存缓存，命中即秒回、不打网络。
    返回 (model, catalog_models, warning)；warning 为需要提示用户的信息（可为空串）。
    拉取失败且无配置时 model 为空。
    """
    b = binding or provider
    model = (b.get("default_model") or "").strip()
    endpoint = (b.get("endpoint") or "").strip()
    warning = ""

    catalog_models = None
    if endpoint:
        fetched = _fetch_codex_models(provider, b, use_cache=not force_fetch)
        if isinstance(fetched, dict) and fetched.get("success"):
            models = [m.strip() for m in (fetched.get("models") or []) if isinstance(m, str) and m.strip()]
            if models:
                responses_filtered = bool(fetched.get("responses_filtered"))
                # DeepSeek 特判：其官方 /models 不带 supports_responses 字段，导致上面
                # responses_only 的能力过滤失效；而 DeepSeek 的 Responses API 目前仅支持
                # deepseek-v4-flash（v4-pro / 旧别名 deepseek-chat 等均不支持）。这里
                # 显式把不支持 Responses 的模型剔除，避免把 v4-pro 写进 Codex 配置。
                ds = _is_deepseek_endpoint(endpoint)
                if ds:
                    ds_supported = [m for m in models if _deepseek_responses_supported(m)]
                    if ds_supported:
                        models = ds_supported
                        responses_filtered = True
                if not model:
                    model = models[0]
                elif model not in models:
                    if responses_filtered:
                        # 用户配置的模型不支持 Responses API → 优先换同系列兼容模型
                        match = next((m for m in models if m.startswith(model)), None)
                        if match is None:
                            match = models[0]
                        warning = (
                            f"{model} 不支持 Responses API，已改用 {match}。"
                            "Codex 自 2026-02 起仅支持 Responses 接口"
                        )
                        model = match
                    else:
                        # 无能力信息：模型列表可能只是别名，保留用户配置
                        models = [model] + models
                # DeepSeek 场景：用户显式配置了非 v4-flash 模型（如 deepseek-v4-pro /
                # deepseek-chat）时强制纠正，避免 /responses 被拒。
                elif ds and not _deepseek_responses_supported(model):
                    match = next((m for m in models if _deepseek_responses_supported(m)), models[0] if models else model)
                    warning = (
                        f"{model} 不支持 Responses API，已改用 {match} 供 Codex 使用。"
                        "DeepSeek 仅 deepseek-v4-flash 支持 Responses 接口"
                    )
                    model = match
                catalog_models = models
    if catalog_models is None and model:
        catalog_models = [model]
    return model, catalog_models, warning


def clear_codex_catalog_cache() -> None:
    """清空 Codex 模型目录缓存（测试隔离用）。"""
    with _codex_catalog_lock:
        _codex_catalog_cache.clear()


def _codex_catalog_key(endpoint: str, api_key: str) -> str:
    """缓存键：按端点 + api key 指纹分组，避免不同供应商串缓存。"""
    fp = hashlib.md5((api_key or "").encode("utf-8")).hexdigest()[:8]
    return f"{(endpoint or '').strip().rstrip('/').lower()}|{fp}"


def _fetch_codex_models(provider: Dict[str, Any], binding: Dict[str, Any],
                        use_cache: bool = True) -> Dict[str, Any]:
    """拉取 Codex 模型列表，带 5 分钟内存缓存。

    use_cache=True 时优先返回未过期的缓存，命中则不打网络；否则强制刷新。
    缓存仅存「模型 id 列表」，能力(supports_responses)信息不缓存（仍按实时响应判定），
    因此安全纠正逻辑不受缓存影响。
    """
    b = binding or provider
    endpoint = (b.get("endpoint") or "").strip()
    api_format = (b.get("api_format") or "").strip().lower()
    api_key = (provider.get("api_key") or "").strip()
    model = (b.get("default_model") or "").strip()
    if not endpoint:
        return {"success": False, "error": "未提供端点 URL"}

    key = _codex_catalog_key(endpoint, api_key)
    if use_cache:
        with _codex_catalog_lock:
            cached = _codex_catalog_cache.get(key)
            now = time.time()
            if cached and cached[1] > now:
                return {"success": True, "models": cached[0], "responses_filtered": False, "cached": True}

    try:
        from . import testing
        # Codex 只支持 Responses API：拉列表时按 supports_responses 能力过滤
        result = testing.fetch_models(
            endpoint, api_key, api_format,
            default_model=model, responses_only=True,
        )
        # 写入缓存（仅成功结果缓存 5 分钟）
        if isinstance(result, dict) and result.get("success"):
            models = [m.strip() for m in (result.get("models") or []) if isinstance(m, str) and m.strip()]
            with _codex_catalog_lock:
                _codex_catalog_cache[key] = (models, time.time() + _CODEX_CATALOG_TTL)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def _write_codex_config(provider: Dict[str, Any], binding: Optional[Dict[str, Any]] = None,
                        catalog_models: Optional[list] = None,
                        model: str = "") -> str:
    """
    把 provider 写入 ~/.codex/config.toml(仅更新 model / model_provider 与
    [model_providers.api_monitor] 表,保留注释与其它配置)。返回写入后的文本。
    binding 为 codex 绑定（含独立端点/模型/格式），缺省回退 provider 顶层字段。
    model 为已解析的默认模型（自动回填时绑定里可能还没有值）。
    """
    path = _get_codex_config_path()
    text = ""
    if os.path.exists(path):
        try:
            # utf-8-sig: 去掉 Codex 桌面版写入的 UTF-8 BOM，避免 \ufeff 粘在首个 key 上
            # 导致 _toml_set_keys 匹配不到而重复插入同 key（Codex 报 duplicate key 直接退出）
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except Exception:
            text = ""
    if not text.strip():
        text = ""

    b = binding or provider
    if not model:
        model = (b.get("default_model") or "").strip()
    endpoint = (b.get("endpoint") or "").strip().rstrip("/")
    name = (provider.get("name") or "").strip() or CODEX_PROVIDER_SLUG
    reasoning_effort = (b.get("reasoning_effort") or "").strip()
    context_length = b.get("context_length") or 0
    # Codex 自 2026-02 起仅支持 Responses API（wire_api="chat" 已被移除），
    # 无论绑定 api_format 是什么，写 Codex 配置一律用 responses。
    wire_api = "responses"

    top: Dict[str, Any] = {"model_provider": CODEX_PROVIDER_SLUG}
    if model:
        top["model"] = model
    # model_catalog_json: 指向只含本供应商模型的自定义目录，模型选择器不再显示官方模型名
    if catalog_models:
        catalog_path = _write_codex_catalog(catalog_models)
        if catalog_path:
            top["model_catalog_json"] = catalog_path
    # reasoning_effort 写入顶层（Codex 会透传给支持的模型）
    if reasoning_effort:
        top["model_reasoning_effort"] = reasoning_effort
    text = _toml_set_keys(text, top, section=None)

    # base_url: Codex 会在 base_url 后拼 "/responses"（Responses API），
    # 而中转厂标准路径是 /v1/responses（/v1/models 同理）。endpoint 不带
    # /v1 时补上，否则会打到 /responses 得到 405。
    # 例外：DeepSeek 官方 Responses API 的基地址是根路径 https://api.deepseek.com
    # （不带 /v1），追加 /v1 反而打到非文档化路径 /v1/responses。
    base_url = endpoint
    if (
        wire_api == "responses"
        and not _is_deepseek_endpoint(base_url)
        and not base_url.rstrip("/").endswith("/v1")
    ):
        base_url = base_url.rstrip("/") + "/v1"

    section = f"model_providers.{CODEX_PROVIDER_SLUG}"
    section_keys: Dict[str, Any] = {
        "name": name,
        "base_url": base_url,
        "wire_api": wire_api,
        "requires_openai_auth": False,
        # context_length: 控制 Codex 上下文窗口大小（0 = 不写入，用模型默认）
        "context_window": int(context_length) if context_length and int(context_length) > 0 else None,
        # 删除旧 env_key: ChatGPT 桌面版进程环境里没有该变量,会直接报
        # "Missing environment variable: OPENAI_API_KEY"; 改为把 key 内嵌为
        # experimental_bearer_token,任何入口(CLI / 桌面版)都不再依赖环境变量。
        "env_key": None,
    }
    api_key = (provider.get("api_key") or "").strip()
    if api_key:
        section_keys["experimental_bearer_token"] = api_key
    text = _toml_set_keys(text, section_keys, section=section)

    # 第三方中继通常只接受 function / web_search 工具。Codex 默认启用的
    # multi-agent 会把工具打包成 type:"namespace"（multi_agent_v1），freeform
    # apply_patch 发 type:"custom"，MCP 服务器工具也归到 namespace 下，
    # 都会被中继以 tool.namespace / tool.custom 拒绝，因此统一在配置里关闭。
    # 其余 features 为消除第三方模式（API key，非 ChatGPT 登录）下的启动警告：
    #   apps=false           关闭 codex_apps 连接器 MCP，否则启动即打 chatgpt.com 报 403
    #   remote_plugin=false  关闭远程插件目录同步（API key 不支持 ChatGPT 认证）
    #   shell_snapshot=false 关闭 shell 环境快照（PowerShell 不支持）
    #   memories=false       关闭记忆（写记忆需要官方模型 gpt-5.6-luna，第三方 API 没有）
    text = _toml_set_keys(text, {
        "multi_agent": False,
        "apply_patch_freeform": False,
        "apps": False,
        "remote_plugin": False,
        "shell_snapshot": False,
        "memories": False,
    }, section="features")
    text = _toml_set_keys(text, {
        "generate_memories": False,
        "use_memories": False,
    }, section="memories")
    # 彻底删除 node_repl MCP 服务器配置（含 env 子表），避免任何 MCP 加载尝试
    text = _toml_remove_section(text, "mcp_servers.node_repl")
    # Codex 官方插件（browser/chrome/文档/表格等）自带大量 skills，会占满
    # skills 上下文预算，触发 "Skill descriptions were shortened" 警告；
    # 第三方中继用不到这些插件，统一切换时禁用（官方模式由 _clear_codex_official 恢复）。
    text = _set_codex_plugins_enabled(text, False)
    # 全部 skills（~/.agents/skills 几十个 + marketplace）统一禁用，消除
    # "Skill descriptions were shortened" 警告并省上下文（官方模式恢复）。
    text = _disable_codex_skills(text)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    return text


def _set_codex_current(provider: Dict[str, Any], binding: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """将指定供应商设为当前 Codex 配置（写入 ~/.codex/config.toml）。
    binding 为 codex 绑定（含独立端点/模型/格式），缺省回退 provider 顶层字段。

    性能：已配置默认模型时本函数**不阻塞**于 /models 网络请求——立即写配置并返回成功，
    模型目录(catalog)在后台线程异步刷新（_refresh_codex_catalog_async）。
    仅当默认模型为空、必须靠 /models 才能确定模型时才在此同步拉取。
    """
    b = binding or provider
    if not (b.get("endpoint") or "").strip():
        return {"success": False, "error": "未配置端点"}
    # 默认模型为空时仍需同步拉取（否则写不进配置）；已配置则跳过网络，秒回。
    model, catalog_models, warning = _resolve_codex_model(provider, b)
    if not model:
        return {
            "success": False,
            "error": "该供应商未配置默认模型，且无法从端点获取模型列表。请先在 API Monitor 中填写默认模型",
        }
    # 回填实际生效的模型（自动填充或修正后），让 UI 与配置保持一致
    try:
        db.update_binding_default_model(provider["id"], "codex", model)
    except Exception:
        pass
    try:
        _write_codex_config(provider, b, catalog_models=catalog_models, model=model)
    except Exception as e:
        return {"success": False, "error": f"写入 config.toml 失败: {e}"}

    _mark_app_current(provider["id"], "codex")
    db.set_setting("codex_current_mode", "provider")

    name = provider.get("name", "")
    message = f"{name} 已设为当前 Codex 配置"
    if warning:
        message = f"{message}（{warning}）"
    return {"success": True, "message": message}


def _refresh_codex_catalog_async(provider_id: int,
                                 on_done: Optional[callable] = None) -> None:
    """后台异步刷新 Codex 模型目录并重写 config.toml。

    切换为当前 Codex 时主流程已用「已有的默认模型」秒写配置返回；这里在后台补全
    完整模型列表并写入 model_catalog_json，让 Codex 模型选择器显示该供应商真实模型。
    带过期守卫：若后台刷新期间用户又切到别的供应商，则不写回，避免覆盖成旧供应商。
    on_done(): 完成后回调（用于推送前端刷新），可选。
    """
    try:
        provider = db.get_provider_by_id(provider_id)
        if not provider:
            return
        binding = _binding(provider, "codex")
        if not binding:
            return
        # 强制刷新（绕过缓存），拿到最新模型列表
        model, catalog_models, warning = _resolve_codex_model(provider, binding, force_fetch=True)

        # 过期守卫：确认此刻仍是本供应商为当前 codex，否则丢弃结果（用户已切到别的供应商）
        if db.get_current_provider_id("codex") != provider_id:
            return

        if model and catalog_models:
            try:
                db.update_binding_default_model(provider_id, "codex", model)
            except Exception:
                pass
            try:
                _write_codex_config(provider, binding, catalog_models=catalog_models, model=model)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        if on_done:
            try:
                on_done()
            except Exception:
                pass


# ────────────────── 官方 / 第三方模式 ──────────────────

def _clear_claude_official() -> tuple:
    """从 settings.json 的 env 删除 ANTHROPIC_* 覆盖，让 Claude Code 走官方登录账号。"""
    settings_path = _get_claude_settings_path()
    if not os.path.exists(settings_path):
        return True, ""
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        if not isinstance(settings, dict):
            settings = {}
        env = settings.get("env", {})
        if not isinstance(env, dict):
            env = {}
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            env.pop(key, None)
        settings["env"] = env
        tmp_path = settings_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, settings_path)
        return True, ""
    except Exception as e:
        return False, f"写入 settings.json 失败: {e}"


def _clear_codex_official() -> tuple:
    """从 config.toml 清除全部第三方覆盖，让 Codex 走官方 OpenAI（auth.json 登录）：
    - 删顶层 model_provider / model / model_catalog_json（cc-switch 残留目录会锁死模型选择器）
    - 删整个 [model_providers.api_monitor] 表（不再定义 DeepSeek 等第三方供应商）
    """
    path = _get_codex_config_path()
    text = ""
    if os.path.exists(path):
        try:
            # utf-8-sig: 去掉 BOM（见 _write_codex_config 注释）
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except Exception:
            text = ""
    text = _toml_set_keys(
        text,
        {"model_provider": None, "model": None, "model_catalog_json": None},
        section=None,
    )
    text = _toml_remove_section(text, "model_providers.api_monitor")
    # 切回官方模式：恢复被第三方配置禁用的 Codex 官方插件与全部 skills
    text = _set_codex_plugins_enabled(text, True)
    text = _remove_codex_skills_block(text)
    # 恢复被第三方配置关闭的 features（官方 ChatGPT 登录下这些功能可用）
    text = _toml_set_keys(text, {
        "multi_agent": True,
        "apply_patch_freeform": True,
        "apps": True,
        "remote_plugin": True,
        "shell_snapshot": True,
        "memories": True,
    }, section="features")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True, ""
    except Exception as e:
        return False, f"写入 config.toml 失败: {e}"


def set_current_official(app_type: str) -> Dict[str, Any]:
    """把指定应用设为官方模式：清除第三方覆盖，走官方登录账号。"""
    app = (app_type or "").strip().lower()
    if app not in ("claude", "codex"):
        return {"success": False, "error": "无效的应用类型"}
    if app == "claude":
        ok, err = _clear_claude_official()
    else:
        ok, err = _clear_codex_official()
    if not ok:
        return {"success": False, "error": err}
    db.clear_app_roles(app)
    db.set_setting(f"{app}_current_mode", "official")
    return {"success": True, "message": "已切换为官方账号模式"}


def get_current_modes() -> Dict[str, Dict[str, Any]]:
    """返回每应用的当前模式: {"claude": {"mode": 'official'|'provider', "provider_name": str|None}, ...}。
    模式设置缺失时按绑定角色推导（有 role=当前 的绑定 → provider，否则 official）。"""
    modes: Dict[str, Dict[str, Any]] = {}
    for app in ("claude", "codex"):
        provider_name = None
        current_found = False
        for p in db.get_providers():
            for b in p.get("apps") or []:
                if b.get("app_type") == app and b.get("role") == "当前":
                    provider_name = p.get("name")
                    current_found = True
                    break
            if current_found:
                break
        mode = (db.get_setting(f"{app}_current_mode") or "").strip()
        if not mode:
            # 模式设置缺失时按绑定角色推导
            mode = "provider" if current_found else "official"
        modes[app] = {"mode": mode, "provider_name": provider_name}
    return modes


def _key_fingerprint(api_key: str) -> str:
    """生成 api key 指纹:前8位 + sha256前8位,避免明文到处比对。"""
    if not api_key:
        return ""
    head = api_key[:8]
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    return f"{head}:{digest}"


def _host_from_endpoint(endpoint: str, fallback: str = "claude") -> str:
    """从 endpoint 提取 host,用于自动命名供应商。"""
    from urllib.parse import urlparse
    raw = (endpoint or "").strip()
    if not raw:
        return fallback
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).netloc or "").split("@")[-1].split(":")[0].strip().lower()
    except Exception:
        host = ""
    return host or fallback


def _unique_provider_name(base: str) -> str:
    """避免重名: base, base (2), base (3)..."""
    names = {p.get("name") or "" for p in db.get_providers()}
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


def _mark_app_current(provider_id: int, app_type: str) -> None:
    """把指定应用下的供应商标为当前,其余(同应用)标备用（走 provider_apps 绑定）。"""
    db.mark_app_current(provider_id, app_type)


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
        # 配置未变但仍是第三方配置 → 确保模式标记正确
        db.set_setting("claude_current_mode", "provider")
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
            for b in p.get("apps") or []:
                if b.get("app_type") != "claude":
                    continue
                b_url = (b.get("endpoint") or "").strip().lower()
                b_norm = _get_claude_base_url(b_url).rstrip("/").lower() if b_url else ""
                if b_norm == norm_endpoint:
                    url_match = p
                    break
            if url_match:
                break
            if key_fp and key_match is None and _key_fingerprint(p.get("api_key") or "") == key_fp:
                key_match = p

        # URL 命中: 同一供应商,更新差异字段并标当前
        if url_match:
            pid = url_match["id"]
            existing_b = next((b for b in (url_match.get("apps") or []) if b.get("app_type") == "claude"), None)
            target_b = {
                "app_type": "claude",
                "endpoint": store_endpoint,
                "default_model": model,
                "api_format": (existing_b or {}).get("api_format") or "anthropic_messages",
            }
            update_data: Dict[str, Any] = {}
            if api_key and _key_fingerprint(api_key) != _key_fingerprint(url_match.get("api_key") or ""):
                update_data["api_key"] = api_key
            # 绑定确实变化才更新 apps
            if (
                existing_b is None
                or (existing_b.get("endpoint") or "") != target_b["endpoint"]
                or (existing_b.get("default_model") or "") != target_b["default_model"]
            ):
                apps = [dict(b) for b in (url_match.get("apps") or []) if b.get("app_type") != "claude"]
                apps.append(target_b)
                update_data["apps"] = apps

            changed = bool(update_data)
            if changed:
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

            _mark_app_current(pid, "claude")
            db.set_setting("claude_current_mode", "provider")
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
            apps = [dict(b) for b in (key_match.get("apps") or []) if b.get("app_type") != "claude"]
            claude_b = next((b for b in (key_match.get("apps") or []) if b.get("app_type") == "claude"), None)
            if claude_b is None:
                claude_b = {"app_type": "claude", "endpoint": store_endpoint, "default_model": model, "api_format": "anthropic_messages"}
            else:
                claude_b["endpoint"] = store_endpoint
                if model:
                    claude_b["default_model"] = model
            apps.append(claude_b)
            update_data: Dict[str, Any] = {"apps": apps}
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
            _mark_app_current(pid, "claude")
            db.set_setting("claude_current_mode", "provider")
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
            "apps": [{
                "app_type": "claude",
                "endpoint": store_endpoint,
                "default_model": model,
                "api_format": "anthropic_messages",
            }],
            "api_key": api_key,
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
        _mark_app_current(new_id, "claude")
        db.set_setting("claude_current_mode", "provider")
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


def auto_detect_codex_provider(force: bool = False) -> Dict[str, Any]:
    """
    检测 Codex 当前生效配置(~/.codex/config.toml),并与本库 codex 供应商对比:
    - URL 匹配: 视为同一供应商,必要时更新 key/model,标为当前(不新建)。
    - URL 未命中但 api key 指纹命中: 更新该 provider 的 endpoint/model。
    - URL 与 key 都未命中: 新建一条 codex 供应商。
    force=False(启动默认): 指纹与上次相同则跳过。force=True(手动导入): 始终比对。
    """
    from . import validators

    cfg = _read_codex_effective_config()
    endpoint = (cfg.get("endpoint") or "").strip()
    api_key = cfg.get("api_key") or ""
    model = cfg.get("model") or ""

    if not endpoint:
        return {
            "success": False,
            "action": "error",
            "reason": "无可同步的端点",
            "error": "未检测到 Codex 配置(无 base_url)。请检查 ~/.codex/config.toml 的 [model_providers.<name>].base_url。",
            "message": "未检测到 Codex 配置(无 base_url)",
        }

    # Codex 的 base_url 不做去 /v1 处理: Codex 会自行追加 /responses
    store_endpoint = endpoint.rstrip("/")
    norm_endpoint = store_endpoint.lower()
    key_fp = _key_fingerprint(api_key)
    current_fingerprint = f"{norm_endpoint}|{model}|{key_fp}"

    # 指纹与上次相同则完全跳过(配置未变)——仅自动同步路径
    last_fp = db.get_setting("last_synced_codex_fingerprint") or ""
    if not force and last_fp == current_fingerprint:
        # 配置未变但仍是第三方配置 → 确保模式标记正确
        db.set_setting("codex_current_mode", "provider")
        return {
            "success": True,
            "action": "skipped",
            "reason": "配置未变,跳过",
            "message": "Codex 配置未变化,已跳过",
            "fingerprint": current_fingerprint,
        }

    try:
        all_providers = db.get_providers()
        url_match = None
        key_match = None
        for p in all_providers:
            for b in p.get("apps") or []:
                if b.get("app_type") != "codex":
                    continue
                b_url = (b.get("endpoint") or "").strip().lower()
                if b_url == norm_endpoint:
                    url_match = p
                    break
            if url_match:
                break
            if key_fp and key_match is None and _key_fingerprint(p.get("api_key") or "") == key_fp:
                key_match = p

        # URL 命中: 同一供应商,更新差异字段并标当前
        if url_match:
            pid = url_match["id"]
            existing_b = next((b for b in (url_match.get("apps") or []) if b.get("app_type") == "codex"), None)
            target_b = {
                "app_type": "codex",
                "endpoint": store_endpoint,
                "default_model": model,
                "api_format": (existing_b or {}).get("api_format") or "openai_responses",
            }
            update_data: Dict[str, Any] = {}
            if api_key and _key_fingerprint(api_key) != _key_fingerprint(url_match.get("api_key") or ""):
                update_data["api_key"] = api_key
            if (
                existing_b is None
                or (existing_b.get("endpoint") or "") != target_b["endpoint"]
                or (existing_b.get("default_model") or "") != target_b["default_model"]
            ):
                apps = [dict(b) for b in (url_match.get("apps") or []) if b.get("app_type") != "codex"]
                apps.append(target_b)
                update_data["apps"] = apps

            changed = bool(update_data)
            if changed:
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

            _mark_app_current(pid, "codex")
            db.set_setting("codex_current_mode", "provider")
            db.set_setting("last_synced_codex_fingerprint", current_fingerprint)
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
            apps = [dict(b) for b in (key_match.get("apps") or []) if b.get("app_type") != "codex"]
            codex_b = next((b for b in (key_match.get("apps") or []) if b.get("app_type") == "codex"), None)
            if codex_b is None:
                codex_b = {"app_type": "codex", "endpoint": store_endpoint, "default_model": model, "api_format": "openai_responses"}
            else:
                codex_b["endpoint"] = store_endpoint
                if model:
                    codex_b["default_model"] = model
            apps.append(codex_b)
            update_data: Dict[str, Any] = {"apps": apps}
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
            _mark_app_current(pid, "codex")
            db.set_setting("codex_current_mode", "provider")
            db.set_setting("last_synced_codex_fingerprint", current_fingerprint)
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
        host = _host_from_endpoint(store_endpoint, fallback="codex")
        base_name = f"Codex · {host}"
        provider_data = {
            "name": _unique_provider_name(base_name),
            "apps": [{
                "app_type": "codex",
                "endpoint": store_endpoint,
                "default_model": model,
                "api_format": "openai_responses",
            }],
            "api_key": api_key,
            "notes": f"同步自 Codex 当前配置 ({today})",
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
        _mark_app_current(new_id, "codex")
        db.set_setting("codex_current_mode", "provider")
        db.set_setting("last_synced_codex_fingerprint", current_fingerprint)
        return {
            "success": True,
            "action": "created",
            "reason": "新增供应商",
            "message": f"已新建供应商「{provider_data['name']}」并导入当前 Codex 配置",
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


def sync_codex_provider(force: bool = True) -> Dict[str, Any]:
    """
    导入入口: 主动读取 Codex 当前配置,与已有供应商对比,
    不同则新建,相同则标记/更新。默认 force=True(手动导入不跳过)。
    """
    return auto_detect_codex_provider(force=force)


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
        # 每应用绑定（分页过滤用）：[{app_type,endpoint,default_model,api_format,reasoning_effort,context_length,role}]
        "apps": [
            {
                "app_type": b.get("app_type"),
                "endpoint": b.get("endpoint", ""),
                "default_model": b.get("default_model", ""),
                "api_format": b.get("api_format", ""),
                "reasoning_effort": b.get("reasoning_effort", ""),
                "context_length": b.get("context_length", 0),
                "role": b.get("role", "备用"),
            }
            for b in (p.get("apps") or [])
        ],
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
        p = db.get_provider_by_id(int(provider_id))
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的供应商 ID"}
    if not p:
        return {"success": False, "error": "供应商不存在"}
    return {"success": True, "api_key": p.get("api_key", "")}
