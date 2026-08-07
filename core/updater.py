"""
自动更新模块 - 检查 GitHub Releases 获取新版本
"""

import re
import urllib.request
import urllib.error
import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# GitHub Releases API (this project)
_RELEASES_URL = "https://api.github.com/repos/249469326i-lang/api-monitor/releases/latest"

# 设置项 key
_SETTING_AUTO_CHECK = "auto_update_check"
_SETTING_LAST_CHECK = "last_update_check"


def check_for_update(current_version: str) -> Dict[str, any]:
    """
    检查是否有新版本

    Args:
        current_version: 当前版本号 (如 "3.0.0")

    Returns:
        {
            "has_update": bool,
            "latest_version": str,
            "download_url": str,
            "changelog": str,
            "published_at": str,
        }
    """
    try:
        req = urllib.request.Request(
            _RELEASES_URL,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "API-Monitor-Update-Checker",
            },
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        tag = data.get("tag_name", "").lstrip("vV")
        name = data.get("name", "")
        body = data.get("body", "")
        published = data.get("published_at", "")

        # 提取下载 URL (优先 .exe 资源) 与 SHA-256 摘要
        download_url = ""
        sha256 = ""
        assets = data.get("assets", [])
        for asset in assets:
            asset_name = asset.get("name", "").lower()
            if asset_name.endswith(".exe"):
                download_url = asset.get("browser_download_url", "")
                digest = asset.get("digest", "") or ""
                # GitHub 资产 digest 形如 "sha256:<hex>"
                if digest.startswith("sha256:"):
                    sha256 = digest[len("sha256:"):].strip().lower()
                break

        # 无 exe 资产（如 Release 只挂源码包）时无法自动更新，不提示
        has_update = bool(download_url) and _compare_versions(current_version, tag)

        return {
            "has_update": has_update,
            "latest_version": tag,
            "download_url": download_url,
            "sha256": sha256,
            "changelog": body[:500] if body else "",
            "published_at": published,
            "release_name": name,
        }

    except urllib.error.HTTPError as e:
        logger.warning(f"Update check HTTP error: {e.code}")
        return {"has_update": False, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        logger.warning(f"Update check network error: {e.reason}")
        return {"has_update": False, "error": "网络不可用"}
    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return {"has_update": False, "error": str(e)}


def _parse_version(version: str):
    """解析版本号为 (数字段元组, 是否预发布, 预发布标签)

    兼容 "3.1.0"、"3.1.0-beta"、"3.1.0rc1"、"3.1.0.post1" 等形式：
    数字段取每个 . 分段开头的数字，非数字后缀视为预发布标记。
    """
    version = (version or "").strip().lstrip("vV")
    parts = []
    pre_release = ""
    for seg in version.split("."):
        m = re.match(r"(\d+)(.*)", seg)
        if not m:
            # 整段非数字（如 "beta"）：记为预发布标记
            pre_release = pre_release or seg
            break
        parts.append(int(m.group(1)))
        if m.group(2):
            # 数字后带后缀（如 "0-beta"、"0rc1"）
            pre_release = pre_release or m.group(2).lstrip("-_")
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4]), bool(pre_release), pre_release


def _compare_versions(current: str, latest: str) -> bool:
    """语义化版本比较，返回 latest 是否比 current 新

    支持预发布后缀：同数字段下，正式版 > 预发布版
    （3.1.0 比 3.1.0-beta 新；3.1.0-beta 比 3.0.2 新）。
    """
    try:
        cur_nums, cur_pre, _ = _parse_version(current)
        lat_nums, lat_pre, _ = _parse_version(latest)
        if not lat_nums or lat_nums == (0, 0, 0):
            return False
        if lat_nums != cur_nums:
            return lat_nums > cur_nums
        # 数字段相同：仅当当前是预发布、最新是正式版时算有更新
        return cur_pre and not lat_pre
    except Exception:
        return False


import tempfile
import shutil
import threading


def _validate_download_url(url: str) -> bool:
    """验证下载 URL 是否来自安全的 GitHub 域名"""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme != "https":
            return False
        host = p.hostname or ""
        return host in ("github.com", "objects.githubusercontent.com") or host.endswith(".githubusercontent.com")
    except Exception:
        return False


def _sha256_file(file_path: str) -> str:
    """计算文件的 SHA-256（小写十六进制）"""
    import hashlib
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(file_path: str, expected_hex: str) -> bool:
    """校验文件 SHA-256 是否与期望一致

    Args:
        file_path: 待校验文件路径
        expected_hex: 期望的 SHA-256（小写十六进制，64 位）

    Returns:
        True 表示一致（或未提供可靠期望值而跳过校验）
    """
    try:
        expected = (expected_hex or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            # 没有可靠期望值时跳过校验，避免误杀合法下载
            return True
        return _sha256_file(file_path) == expected
    except Exception:
        return False


def build_update_script(temp_exe_path: str, current_exe: str) -> str:
    """生成替换 exe 并重启的批处理脚本内容

    特点：
    - 先备份当前 exe 为 .bak，安装失败可回滚恢复
    - copy 失败（文件被占用）时先 rename 再 copy
    - 安装成功后清理备份并启动新版；失败则从 .bak 恢复旧版并启动
    - 清除 _MEIPASS/_MEIPASS2 环境变量，避免新版找不到旧解压目录的 dll

    Args:
        temp_exe_path: 已下载到临时目录的新 exe
        current_exe: 当前正在运行的 exe 完整路径

    Returns:
        批处理脚本内容
    """
    import os

    current_exe_name = os.path.basename(current_exe)
    current_exe_dir = os.path.dirname(current_exe)
    old_exe_path = os.path.join(current_exe_dir, "API-Monitor.old.exe")
    backup_path = current_exe + ".bak"

    return f"""@echo off
chcp 65001 >nul 2>&1
set _MEIPASS=
set _MEIPASS2=

REM 直接结束进程，等待 2 秒让文件锁释放
taskkill /f /im "{current_exe_name}" >nul 2>&1
ping 127.0.0.1 -n 3 >nul 2>&1

REM 先备份当前 exe，安装失败时可回滚
if exist "{current_exe}" copy /Y "{current_exe}" "{backup_path}" >nul 2>&1

REM 清理上次更新可能遗留的旧文件
if exist "{old_exe_path}" del /f /q "{old_exe_path}" >nul 2>&1

copy /Y "{temp_exe_path}" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    REM 新 exe 可能被占用，先把旧 exe 改名再拷贝
    rename "{current_exe}" "API-Monitor.old.exe" >nul 2>&1
    copy /Y "{temp_exe_path}" "{current_exe}" >nul 2>&1
    if errorlevel 1 goto rollback
    del "{old_exe_path}" >nul 2>&1
)

REM 校验新 exe 已就位（不存在或大小为 0 视为失败，回滚）
if not exist "{current_exe}" goto rollback
if exist "{current_exe}" for %%F in ("{current_exe}") do if %%~zF EQU 0 goto rollback

REM 安装成功：清理备份并启动新版
del "{backup_path}" >nul 2>&1
start "" "{current_exe}"
del "{temp_exe_path}" >nul 2>&1
del "%~f0" >nul 2>&1
exit /b 0

:rollback
REM 安装失败：从备份恢复旧版并启动，保证应用不丢
if exist "{backup_path}" copy /Y "{backup_path}" "{current_exe}" >nul 2>&1
del "{old_exe_path}" >nul 2>&1
del "{backup_path}" >nul 2>&1
del "{temp_exe_path}" >nul 2>&1
start "" "{current_exe}"
del "%~f0" >nul 2>&1
exit /b 1
"""


def download_update(download_url: str, progress_callback=None, cancel_event=None, sha256: str = "") -> str:
    """下载更新文件到临时目录

    Args:
        download_url: .exe 文件的下载链接
        progress_callback: 回调函数 (downloaded_bytes, total_bytes)
        cancel_event: threading.Event，设置时取消下载
        sha256: 期望的 SHA-256（来自 GitHub API digest），非空则下载后校验

    Returns:
        下载完成的临时文件路径

    Raises:
        ValueError: URL 验证失败
        RuntimeError: 下载被取消、网络错误或校验失败
    """
    if not _validate_download_url(download_url):
        raise ValueError("下载链接不安全，已阻止")

    # 获取文件名
    from urllib.parse import urlparse, unquote
    filename = "API-Monitor-update.exe"
    path_part = urlparse(download_url).path
    if path_part:
        candidate = unquote(path_part.rsplit("/", 1)[-1])
        if candidate.lower().endswith(".exe"):
            filename = candidate

    temp_dir = tempfile.mkdtemp(prefix="apimonitor_update_")
    temp_path = os.path.join(temp_dir, filename)

    try:
        req = urllib.request.Request(
            download_url,
            headers={
                "User-Agent": "API-Monitor-Updater",
                "Accept": "application/octet-stream",
            },
        )

        with urllib.request.urlopen(req, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 65536  # 64KB

            with open(temp_path, "wb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise RuntimeError("下载已取消")

                    chunk = response.read(chunk_size)
                    if not chunk:
                        break

                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback:
                        # 每 ~100KB 或完成时回调
                        if downloaded % (chunk_size * 2) < chunk_size or downloaded >= total:
                            progress_callback(downloaded, total)

        logger.info(f"Update downloaded to: {temp_path} ({downloaded} bytes)")

        # 完整性校验：下载内容与 Release 资产摘要一致才允许安装
        if sha256 and not verify_sha256(temp_path, sha256):
            logger.error("Update SHA-256 mismatch, download discarded")
            try:
                os.remove(temp_path)
                os.rmdir(temp_dir)
            except OSError:
                pass
            raise RuntimeError("下载文件校验失败（SHA-256 不匹配），已阻止安装")

        return temp_path

    except RuntimeError:
        # 取消或网络错误，清理临时文件
        try:
            os.remove(temp_path)
            os.rmdir(temp_dir)
        except OSError:
            pass
        raise
    except Exception as e:
        logger.error(f"Download failed: {e}")
        try:
            os.remove(temp_path)
            os.rmdir(temp_dir)
        except OSError:
            pass
        raise RuntimeError(f"下载失败: {e}")
