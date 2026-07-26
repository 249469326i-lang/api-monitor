r"""数据目录统一管理与历史目录迁移。

对外品牌已统一为 API Monitor，数据目录同步从历史的
%APPDATA%\.cc-switch-monitor 迁移到 %APPDATA%\.api-monitor。

迁移策略（首次启动时执行一次）：
- 新目录已有 providers.db → 视为已迁移，直接用新目录；
- 旧目录存在且新目录无数据 → 优先整目录改名（原子、最快）；
  改名失败（如旧目录内有文件被占用）则逐项复制核心数据
  （providers.db 及其 WAL/SHM、backups/、app.log），旧目录保留不删。

DPAPI 加密与 Windows 用户绑定、与路径无关，迁移后 Key 仍可解密。
"""

import logging
import os
import shutil

logger = logging.getLogger(__name__)

_OLD_DIR_NAME = ".cc-switch-monitor"
_NEW_DIR_NAME = ".api-monitor"

_migration_done = False


def _appdata_root() -> str:
    return os.environ.get("APPDATA") or os.path.expanduser("~")


def get_data_dir() -> str:
    """返回应用数据目录（自动完成历史目录迁移）。

    支持 API_MONITOR_DATA_DIR 环境变量覆盖（验证脚本/测试隔离用），
    覆盖时不执行迁移。
    """
    override = os.environ.get("API_MONITOR_DATA_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override

    new_dir = os.path.join(_appdata_root(), _NEW_DIR_NAME)
    _migrate_legacy_dir(new_dir)
    os.makedirs(new_dir, exist_ok=True)
    return new_dir


def _migrate_legacy_dir(new_dir: str) -> None:
    """把 .cc-switch-monitor 的数据迁移到 .api-monitor（只执行一次）。"""
    global _migration_done
    if _migration_done:
        return
    _migration_done = True

    old_dir = os.path.join(_appdata_root(), _OLD_DIR_NAME)
    if not os.path.isdir(old_dir):
        return
    # 新目录已有数据库 → 已迁移过，绝不二次迁移（避免覆盖新数据）
    if os.path.isfile(os.path.join(new_dir, "providers.db")):
        return

    # 方案一：整目录改名（同一卷上是原子操作，含 webview2 缓存一并带走）
    if not os.path.exists(new_dir):
        try:
            os.rename(old_dir, new_dir)
            logger.info(f"数据目录已迁移: {old_dir} -> {new_dir}")
            return
        except OSError as e:
            logger.warning(f"整目录迁移失败（{e}），改为复制核心数据")

    # 方案二：逐项复制核心数据（旧目录保留，webview2 缓存可再生不复制）
    try:
        os.makedirs(new_dir, exist_ok=True)
        for name in ("providers.db", "providers.db-wal", "providers.db-shm", "app.log"):
            src = os.path.join(old_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(new_dir, name))
        old_backups = os.path.join(old_dir, "backups")
        if os.path.isdir(old_backups):
            shutil.copytree(
                old_backups, os.path.join(new_dir, "backups"), dirs_exist_ok=True
            )
        logger.info(f"核心数据已复制迁移: {old_dir} -> {new_dir}（旧目录保留）")
    except Exception:
        logger.exception("数据目录迁移失败，继续使用新空目录")
