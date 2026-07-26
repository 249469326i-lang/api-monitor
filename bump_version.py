"""
版本号统一更新脚本 —— 以命令行参数为唯一来源，同步全部版本号位置。

用法:
    python bump_version.py 3.1.0

会更新:
    core/version.py       __version__（单一来源，main.py 从这里导入）
    file_version_info.txt filevers / prodvers / FileVersion / ProductVersion
    (rebuild.bat 的版本提示已改为运行时读取，无需更新)

漏改 __version__ 会导致自动更新用旧版本比较，用户永远收不到
更新提示，因此发版时必须使用本脚本而不是手动逐处修改。
"""
import re
import sys
from pathlib import Path


def _replace(path: Path, pattern: str, repl: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(pattern, repl, text)
    if n != count:
        raise SystemExit(f"[FAIL] {path.name}: 模式 {pattern!r} 匹配 {n} 处，预期 {count} 处")
    path.write_text(text, encoding="utf-8")
    print(f"[OK] {path.name}")


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        raise SystemExit("用法: python bump_version.py <x.y.z>   例如: python bump_version.py 3.1.0")
    new = sys.argv[1]
    root = Path(__file__).resolve().parent
    ver_tuple = f"({', '.join(new.split('.'))}, 0)"

    _replace(root / "core" / "version.py", r'__version__ = "[^"]+"', f'__version__ = "{new}"')
    _replace(root / "file_version_info.txt", r"filevers=\(\d+, \d+, \d+, \d+\)", f"filevers={ver_tuple}")
    _replace(root / "file_version_info.txt", r"prodvers=\(\d+, \d+, \d+, \d+\)", f"prodvers={ver_tuple}")
    _replace(root / "file_version_info.txt", r"(StringStruct\(u'FileVersion', u')[^']+(')", rf"\g<1>{new}\g<2>")
    _replace(root / "file_version_info.txt", r"(StringStruct\(u'ProductVersion', u')[^']+(')", rf"\g<1>{new}\g<2>")

    print(f"\n版本号已统一更新为 {new}")
    print("接下来: rebuild.bat 构建 -> 提交 -> 新建 GitHub Release (tag v" + new + ") 并上传 exe")


if __name__ == "__main__":
    main()
