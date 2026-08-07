"""自动更新模块单元测试。

覆盖 core/updater.py 的：
- 版本解析与比较（含预发布）
- 下载 URL 安全校验
- SHA-256 完整性校验
- check_for_update（mock 网络）：无 exe 资产不提示、带 digest 时返回 sha256
- download_update（mock 网络）：成功、SHA 不匹配报错、不安全 URL 拒绝
- build_update_script：批处理脚本内容与回滚/清理逻辑
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import updater


class ParseVersionTest(unittest.TestCase):
    def test_parse_basic(self):
        nums, pre, _ = updater._parse_version("3.3.0")
        self.assertEqual(nums, (3, 3, 0))
        self.assertFalse(pre)

    def test_parse_short(self):
        self.assertEqual(updater._parse_version("3.1")[0], (3, 1, 0))

    def test_parse_pre_release(self):
        nums, pre, tag = updater._parse_version("3.4.0-beta")
        self.assertEqual(nums, (3, 4, 0))
        self.assertTrue(pre)
        self.assertEqual(tag, "beta")

    def test_parse_v_prefix(self):
        nums, _, _ = updater._parse_version("v3.3.0")
        self.assertEqual(nums, (3, 3, 0))


class CompareVersionsTest(unittest.TestCase):
    def test_newer_major(self):
        self.assertTrue(updater._compare_versions("3.3.0", "4.0.0"))

    def test_newer_minor(self):
        self.assertTrue(updater._compare_versions("3.3.0", "3.4.0"))

    def test_newer_patch(self):
        self.assertTrue(updater._compare_versions("3.3.0", "3.3.1"))

    def test_same_no_update(self):
        self.assertFalse(updater._compare_versions("3.3.0", "3.3.0"))

    def test_current_newer_no_update(self):
        self.assertFalse(updater._compare_versions("3.4.0", "3.3.0"))

    def test_pre_to_release_updates(self):
        self.assertTrue(updater._compare_versions("3.4.0-beta", "3.4.0"))

    def test_release_to_pre_no_update(self):
        self.assertFalse(updater._compare_versions("3.4.0", "3.4.0-beta"))

    def test_garbage_latest_no_update(self):
        self.assertFalse(updater._compare_versions("3.3.0", "not-a-version"))


class ValidateDownloadUrlTest(unittest.TestCase):
    def test_valid_github(self):
        self.assertTrue(updater._validate_download_url(
            "https://github.com/a/b/releases/download/v3.3.0/API-Monitor.exe"))

    def test_valid_objects_host(self):
        self.assertTrue(updater._validate_download_url(
            "https://objects.githubusercontent.com/abc/API-Monitor.exe"))

    def test_http_rejected(self):
        self.assertFalse(updater._validate_download_url(
            "http://github.com/a/b/API-Monitor.exe"))

    def test_evil_domain_rejected(self):
        self.assertFalse(updater._validate_download_url(
            "https://evil.com/API-Monitor.exe"))


class Sha256VerifyTest(unittest.TestCase):
    def _temp_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".exe")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path

    def test_match(self):
        path = self._temp_file(b"hello updater")
        try:
            digest = updater._sha256_file(path)
            self.assertEqual(digest, hashlib.sha256(b"hello updater").hexdigest())
            self.assertTrue(updater.verify_sha256(path, digest))
        finally:
            os.remove(path)

    def test_mismatch(self):
        path = self._temp_file(b"hello")
        try:
            self.assertFalse(updater.verify_sha256(path, "0" * 64))
        finally:
            os.remove(path)

    def test_no_digest_skips(self):
        path = self._temp_file(b"x")
        try:
            self.assertTrue(updater.verify_sha256(path, ""))
            self.assertTrue(updater.verify_sha256(path, "not-a-sha"))
            self.assertTrue(updater.verify_sha256(path, None))
        finally:
            os.remove(path)

    def test_missing_file(self):
        self.assertFalse(updater.verify_sha256("Z:\\nonexistent\\x.exe", "0" * 64))


class _FakeResp:
    """模拟 urllib 响应：支持 read(size) 分块与 Content-Length 头。"""

    def __init__(self, data, total=None):
        self._data = data
        self._pos = 0
        self.headers = {"Content-Length": str(total if total is not None else len(data))}

    def read(self, n=-1):
        if self._pos >= len(self._data):
            return b""
        if n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _release_json(tag, assets):
    return json.dumps({
        "tag_name": tag,
        "name": tag,
        "body": "changelog line 1\nchangelog line 2",
        "published_at": "2026-08-07T00:00:00Z",
        "assets": assets,
    }).encode("utf-8")


class CheckForUpdateTest(unittest.TestCase):
    def test_newer_exe_with_digest(self):
        payload = _release_json("v3.4.0", [{
            "name": "API-Monitor.exe",
            "browser_download_url": "https://github.com/249469326i-lang/api-monitor/releases/download/v3.4.0/API-Monitor.exe",
            "digest": "sha256:" + "a" * 64,
        }])
        with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            r = updater.check_for_update("3.3.0")
        self.assertTrue(r["has_update"])
        self.assertEqual(r["latest_version"], "3.4.0")
        self.assertEqual(r["sha256"], "a" * 64)
        self.assertTrue(r["download_url"].endswith(".exe"))
        self.assertTrue(r["changelog"])

    def test_no_exe_asset_no_update(self):
        # 只有源码包、没有 exe 资产：无法自动更新，不提示
        payload = _release_json("v3.4.0", [{
            "name": "source.zip",
            "browser_download_url": "https://github.com/a/b/archive/refs/tags/v3.4.0.zip",
        }])
        with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            r = updater.check_for_update("3.3.0")
        self.assertFalse(r["has_update"])

    def test_same_version_no_update(self):
        payload = _release_json("v3.3.0", [{
            "name": "API-Monitor.exe",
            "browser_download_url": "https://github.com/a/b/releases/download/v3.3.0/API-Monitor.exe",
        }])
        with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            r = updater.check_for_update("3.3.0")
        self.assertFalse(r["has_update"])

    def test_network_error_graceful(self):
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            r = updater.check_for_update("3.3.0")
        self.assertFalse(r["has_update"])
        self.assertIn("error", r)


class DownloadUpdateTest(unittest.TestCase):
    URL = "https://github.com/249469326i-lang/api-monitor/releases/download/v3.3.0/API-Monitor.exe"

    def test_download_ok(self):
        exe_bytes = b"MZ" + b"\x00" * 4096
        with patch("urllib.request.urlopen", return_value=_FakeResp(exe_bytes)):
            path = updater.download_update(self.URL)
        try:
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), exe_bytes)
        finally:
            os.remove(path)
            os.rmdir(os.path.dirname(path))

    def test_download_sha_mismatch_raises_and_cleans(self):
        exe_bytes = b"corrupt content"
        with patch("urllib.request.urlopen", return_value=_FakeResp(exe_bytes)):
            with self.assertRaises(RuntimeError):
                updater.download_update(self.URL, sha256="0" * 64)

    def test_download_sha_match_ok(self):
        exe_bytes = b"good content"
        digest = hashlib.sha256(exe_bytes).hexdigest()
        with patch("urllib.request.urlopen", return_value=_FakeResp(exe_bytes)):
            path = updater.download_update(self.URL, sha256=digest)
        try:
            self.assertTrue(os.path.isfile(path))
        finally:
            os.remove(path)
            os.rmdir(os.path.dirname(path))

    def test_unsafe_url_rejected(self):
        with self.assertRaises(ValueError):
            updater.download_update("http://evil.com/API-Monitor.exe")

    def test_progress_callback_called(self):
        exe_bytes = b"MZ" + b"\x00" * 65536 * 3
        seen = []
        with patch("urllib.request.urlopen", return_value=_FakeResp(exe_bytes)):
            path = updater.download_update(
                self.URL, progress_callback=lambda d, t: seen.append((d, t)))
        try:
            self.assertTrue(seen)
            self.assertEqual(seen[-1][0], len(exe_bytes))
        finally:
            os.remove(path)
            os.rmdir(os.path.dirname(path))


class BuildUpdateScriptTest(unittest.TestCase):
    def test_contains_expected_statements(self):
        script = updater.build_update_script(
            "C:\\temp\\apimonitor_update_123\\API-Monitor.exe",
            "C:\\API Monitor\\API-Monitor.exe",
        )
        # 核心步骤：结束进程、备份、替换、启动
        self.assertIn("taskkill /f /im", script)
        self.assertIn('copy /Y "C:\\temp\\apimonitor_update_123\\API-Monitor.exe" "C:\\API Monitor\\API-Monitor.exe"', script)
        self.assertIn('"C:\\API Monitor\\API-Monitor.exe.bak"', script)
        self.assertIn('start "" "C:\\API Monitor\\API-Monitor.exe"', script)
        self.assertIn(":rollback", script)
        self.assertIn("set _MEIPASS=", script)

    def test_rollback_restores_backup(self):
        script = updater.build_update_script("C:\\t\\new.exe", "C:\\app\\API-Monitor.exe")
        # 回滚分支里必须包含「从 .bak 恢复并启动旧版」
        self.assertIn('if exist "C:\\app\\API-Monitor.exe.bak" copy /Y "C:\\app\\API-Monitor.exe.bak" "C:\\app\\API-Monitor.exe"', script)


if __name__ == "__main__":
    unittest.main()
