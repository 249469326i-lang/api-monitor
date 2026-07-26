"""
后端 API 验证脚本 - 逐个真实调用 main.API 的每个方法
用法: python verify_backend.py
退出码 0 = 全部通过,否则打印失败项

所有 DB 操作都在临时目录中进行(API_MONITOR_DATA_DIR),不触碰真实数据。
"""
import sys
import os
import json
import tempfile

# 必须在 import main 之前设置:把数据目录重定向到临时目录,
# 避免增删改/导入操作污染 %APPDATA% 下的真实数据库
_tmp_dir = tempfile.mkdtemp(prefix="api_monitor_verify_")
os.environ["API_MONITOR_DATA_DIR"] = _tmp_dir

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import API, __version__


def step(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" :: {detail}" if detail else ""))
    return ok


def main():
    print(f"=== Provider Monitor v{__version__} - backend verification ===")
    api = API()
    passed = 0
    failed = 0

    # 1. get_version
    v = api.get_version()
    if step("get_version", isinstance(v, str) and v):
        passed += 1
    else:
        failed += 1

    # 2. get_about_info
    info = api.get_about_info()
    ok = isinstance(info, dict) and info.get("name") and info.get("version")
    if step("get_about_info", ok, str(info)):
        passed += 1
    else:
        failed += 1

    # 3. get_providers (空库应返回 [])
    providers = api.get_providers()
    ok = isinstance(providers, list)
    if step("get_providers", ok, f"返回 {len(providers)} 个"):
        passed += 1
    else:
        failed += 1

    # 4. get_stats (空库应为 {total:0, ok:0, fail:0, pending:0})
    stats = api.get_stats()
    ok = isinstance(stats, dict) and "total" in stats
    if step("get_stats", ok, str(stats)):
        passed += 1
    else:
        failed += 1

    # 5. add_provider
    add_r = api.add_provider({
        "name": "_verify_test_provider",
        "app_type": "claude",
        "role": "备用",
        "endpoint": "https://api.test.example/v1",
        "api_key": "sk-verify-test-001",
    })
    ok = isinstance(add_r, dict) and add_r.get("success") and add_r.get("id")
    if step("add_provider", ok, str(add_r)):
        passed += 1
        new_id = add_r["id"]
    else:
        failed += 1
        new_id = None

    # 6. get_providers 应包含新加的
    if new_id:
        providers = api.get_providers()
        got = any(str(p.get("id")) == str(new_id) for p in providers)
        if step("get_providers_after_add", got):
            passed += 1
        else:
            failed += 1

    # 7. update_provider
    if new_id:
        upd_r = api.update_provider(new_id, {"name": "_verify_test_renamed"})
        ok = isinstance(upd_r, dict) and upd_r.get("success")
        if step("update_provider", ok, str(upd_r)):
            passed += 1
        else:
            failed += 1

    # 8. test_provider (单条,真实跑网络测试 - 失败也算成功只要返回结构正确)
    if new_id:
        try:
            t_r = api.test_provider(new_id)
            ok = isinstance(t_r, dict) and "success" in t_r and ("status" in t_r or "error" in t_r)
            if step("test_provider", ok, str(t_r)):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            if step("test_provider", False, f"exception: {e}"):
                passed += 1
            else:
                failed += 1

    # 9. import_from_ccswitch - 自动发现 cc-switch/ccmesh 安装位置
    from core import db as _db
    discovered = _db.discover_ccswitch_db()
    print(f"     discover_ccswitch_db: {discovered}")
    imp_r = api.import_from_ccswitch()
    ok = isinstance(imp_r, dict) and ("success" in imp_r) and (
        imp_r.get("success") or imp_r.get("error")
    )
    # 若发现到 db,导入必须成功;否则才允许 missing_db
    if discovered:
        ok = ok and imp_r.get("success") and imp_r.get("imported", 0) >= 0
    else:
        if isinstance(imp_r, dict) and not imp_r.get("success"):
            ok = ok and imp_r.get("missing_db") in (True, False)
    if step("import_from_ccswitch", ok, str(imp_r)):
        passed += 1
    else:
        failed += 1

    # 9b. import_from_path - 不存在路径应明确失败
    from core import providers as _prov
    fp_r = _prov.import_from_path("/nonexistent/path/foo.db")
    ok = isinstance(fp_r, dict) and not fp_r.get("success") and fp_r.get("error")
    if step("import_from_path_missing", ok, str(fp_r)):
        passed += 1
    else:
        failed += 1

    # 9c. choose_and_import_from_file - 窗口未就绪时应返回明确错误而非崩溃
    cif_r = api.choose_and_import_from_file()
    ok = isinstance(cif_r, dict) and not cif_r.get("success") and cif_r.get("error")
    if step("choose_and_import_from_file_no_window", ok, str(cif_r)):
        passed += 1
    else:
        failed += 1

    # 10. test_all (异步,启动 + 立即 stop)
    ta_r = api.test_all("fast")
    ok = isinstance(ta_r, dict) and ta_r.get("success")
    if step("test_all_start", ok, str(ta_r)):
        passed += 1
    else:
        failed += 1

    # 11. stop_testing
    st_r = api.stop_testing()
    ok = isinstance(st_r, dict) and st_r.get("success")
    if step("stop_testing", ok, str(st_r)):
        passed += 1
    else:
        failed += 1

    # 12. delete_provider
    if new_id:
        del_r = api.delete_provider(new_id)
        ok = isinstance(del_r, dict) and del_r.get("success")
        if step("delete_provider", ok, str(del_r)):
            passed += 1
        else:
            failed += 1

    # 13. launch_claude - 只验证方法存在且可调用;不真实执行
    # (真实执行会弹出终端窗口,不适合验证脚本)
    ok = callable(getattr(api, "launch_claude", None))
    if step("launch_claude_exists", ok):
        passed += 1
    else:
        failed += 1

    print(f"\n=== 结果: {passed} passed / {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())