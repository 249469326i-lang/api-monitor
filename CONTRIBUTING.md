# 贡献指南

感谢你考虑为 API Monitor 做出贡献！🎉

## 开发环境搭建

```bash
git clone https://github.com/249469326i-lang/api-monitor.git
cd api-monitor
python -m pip install -r requirements-dev.txt
python main.py
```

要求：Windows 10/11、Python 3.10+、WebView2 Runtime。

## 提交改动前

1. **跑通验证脚本**

   ```bash
   python verify_backend.py
   python -m unittest test_fetch_models_isolation -v
   ```

2. **确认打包正常**（涉及 `main.py` / `core/` / `web/` 改动时）

   ```bash
   rebuild.bat
   ```

   确认 `web/` 前端资源已打入 exe。

3. **不要提交**：
   - 真实 API Key、`providers.db`、本地配置
   - `API-Monitor.exe` 等构建产物（已在 `.gitignore` 中排除）
   - IDE 配置目录（`.idea/`、`.vscode/` 等）

## 代码约定

- 后端：Python，面向 `main.py` 的 `API` 类桥接前端调用；新后端能力优先放进 `core/` 对应模块
- 前端：原生 HTML/CSS/JS（无构建步骤），改动集中在 `web/`
- 提交信息建议使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：`feat:` / `fix:` / `docs:` / `refactor:` 等

## Pull Request 流程

1. Fork 仓库并新建分支：`git checkout -b feature/your-feature`
2. 完成改动并通过上方验证
3. 提交 PR，描述清楚：**动机、改动内容、测试方式、截图（如涉及 UI）**
4. 较大的架构调整请先开 Issue 讨论

## 报告 Bug

使用 [Bug Report 模板](https://github.com/249469326i-lang/api-monitor/issues/new) 提交，请包含：

- 系统版本与应用版本
- 复现步骤
- 期望行为 vs 实际行为
- 日志或截图（注意打码 API Key）

## 安全漏洞

请**不要**开公开 Issue，参见 [SECURITY.md](SECURITY.md)。

## 发布流程（维护者）

1. 更新 `main.py` 中 `__version__`、`file_version_info.txt`、`rebuild.bat` 中的版本号
2. `rebuild.bat` 构建 `API-Monitor.exe`
3. 在 GitHub 新建 Release（tag 形如 `v3.x.x`），上传 exe 作为 Release Asset
4. 客户端自动更新仅检查 GitHub Releases，Release 发布即生效
