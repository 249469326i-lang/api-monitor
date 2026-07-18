"""
自动更新模块 - 检查 GitHub Releases 获取新版本
"""

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


def _compare_versions(current: str, latest: str) -> bool:
    """简单语义化版本比较，返回 latest 是否比 current 新"""
    try:
        cur_parts = [int(x) for x in current.split(".")]
        lat_parts = [int(x) for x in latest.split(".")]
        # 补齐长度
        while len(cur_parts) < 3:
            cur_parts.append(0)
        while len(lat_parts) < 3:
            lat_parts.append(0)
        return lat_parts > cur_parts
    except (ValueError, AttributeError):
        return False
