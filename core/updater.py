"""
自动更新模块 - 检查 GitHub Releases 获取新版本
"""

import re
import urllib.request
import urllib.error
import json
import logging
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

        # 提取下载 URL (优先 .exe 资源)
        download_url = ""
        assets = data.get("assets", [])
        for asset in assets:
            asset_name = asset.get("name", "").lower()
            if asset_name.endswith(".exe"):
                download_url = asset.get("browser_download_url", "")
                break
        if not download_url:
            download_url = data.get("html_url", "")

        # 版本比较
        has_update = _compare_versions(current_version, tag)

        return {
            "has_update": has_update,
            "latest_version": tag,
            "download_url": download_url,
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
