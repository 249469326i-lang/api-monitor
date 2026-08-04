# API Monitor — 默认工作流

## 核心规则：每次修改代码后必须重新打包并重启

本应用通过 `API-Monitor.exe`（PyInstaller 单文件打包）运行，且 `web/index.html`、`web/css/style.css`、`web/js/app.js` 及资源全部内嵌进 EXE（见 `API-Monitor.spec`）。**修改任何下列文件后，必须重新打包才会生效**：

- `core/**/*.py`、`main.py`、`*.spec`
- `web/index.html`、`web/css/style.css`、`web/js/app.js`
- `web/assets/`（图标、字体、背景图）

### 收尾步骤（每次修改完强制执行）

```powershell
# 1. 运行测试（必须全绿）
python -m unittest discover -p "test_*.py"

# 2. 重新打包并重启（自动完成：杀进程 → PyInstaller 构建 → 启动新 EXE）
.\rebuild.bat
```

`rebuild.bat` 会自动 `taskkill` 正在运行的 `API-Monitor.exe`，删除旧文件，用 `pyinstaller --clean API-Monitor.spec` 重新构建，并把 `dist\API-Monitor.exe` 移回根目录后自动启动新版本。构建需 30-60 秒。

### 例外

- 只修改 `docs/`、`*.md`、测试文件本身、`.github/` 等不影响打包产物的文件时，无需重新打包，但改动测试后仍须运行测试套件确认全绿。
- 修改 `requirements.txt` / `requirements-dev.txt` 需重新安装依赖后再打包。

## 其他约定

- 测试命令：`python -m unittest discover -p "test_*.py"`（不要用 pytest，本项目用 unittest）。
- 提交前检查 `git status` / `git diff`，只暂存有意的改动，不提交密钥。
