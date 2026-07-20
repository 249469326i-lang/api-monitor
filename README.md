<div align="center">
  <img src="docs/images/logo.png" alt="API Monitor Logo" width="96" />

  # API Monitor

  **Windows 桌面端 API 供应商监控工具 —— 定时测速 · 故障切换 · 托盘常驻 · 密钥加密**

  [![Release](https://img.shields.io/github/v/release/249469326i-lang/api-monitor?style=flat-square)](https://github.com/249469326i-lang/api-monitor/releases)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
  [![Platform](https://img.shields.io/badge/platform-Windows%2010%2B-blue?style=flat-square)](#-系统要求)
  [![Python](https://img.shields.io/badge/python-3.10%2B-green?style=flat-square)](#-源码运行)

  [下载使用](#-下载与安装) · [功能特性](#-功能特性) · [源码构建](#-源码运行) · [贡献指南](CONTRIBUTING.md) · [问题反馈](https://github.com/249469326i-lang/api-monitor/issues)

  <img src="docs/images/screenshot.png" alt="API Monitor 主界面截图" width="900" />
</div>

---

## 📖 目录

- [功能特性](#-功能特性)
- [下载与安装](#-下载与安装)
- [系统要求](#-系统要求)
- [快速上手](#-快速上手)
- [源码运行](#-源码运行)
- [打包独立 EXE](#-打包独立-exe)
- [项目结构](#-项目结构)
- [配置与数据](#-配置与数据)
- [运行测试](#-运行测试)
- [安全说明](#-安全说明)
- [路线图](#-路线图)
- [参与贡献](#-参与贡献)
- [开源协议](#-开源协议)

## ✨ 功能特性

- 📡 **多供应商监控** — 同时监控多个 API 供应商 / 模型的延迟与可用性
- ⏱️ **定时探测** — 自定义探测间隔，失败自动重试
- 🔀 **故障转移** — 主线路异常时自动切换到备用供应商
- 🖥️ **托盘常驻** — 最小化到系统托盘，支持开机自启
- 🔐 **密钥加密** — API Key 使用 Windows DPAPI 本地加密存储，绝不明文落盘
- 📦 **单文件分发** — 打包为独立 `API-Monitor.exe`，下载即用，无需安装 Python
- 🎨 **像素风 UI** — 内置现代像素风格 Web 前端（PyWebView 渲染）

## 📥 下载与安装

**普通用户（推荐）：**

1. 前往 [Releases](https://github.com/249469326i-lang/api-monitor/releases) 页面
2. 下载最新版本的 `API-Monitor.exe`
3. 双击运行，无需安装

> ⚠️ **前置条件**：需要系统已安装 **Microsoft Edge WebView2 Runtime**（Windows 10/11 通常已自带；LTSC 或精简版系统可能缺失，需自行安装）。
>
> ⚠️ **关于 SmartScreen 提示**：程序未购买代码签名证书，首次运行可能出现 Windows SmartScreen 警告，点击「更多信息 → 仍要运行」即可。不放心的话欢迎直接从源码构建（见下文）。

首次运行会在 `%APPDATA%\.cc-switch-monitor\` 下创建本地配置与数据库。

## 💻 系统要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11 (x64) |
| 运行时 | Microsoft Edge WebView2 Runtime（Win10/11 通常已预装） |
| 磁盘空间 | ~50 MB |
| 网络 | 可访问你所配置的 API 端点 |

## 🚀 快速上手

1. 启动后点击「添加供应商」，填入 API 地址与 Key
2. 选择需要监控的模型，设置探测间隔
3. 主面板实时显示各线路延迟与状态，异常时自动故障切换
4. 关闭窗口自动最小化到托盘，右键托盘图标可退出

## 🛠️ 源码运行

```bash
# 1. 克隆仓库
git clone https://github.com/249469326i-lang/api-monitor.git
cd api-monitor

# 2. 安装依赖（建议 Python 3.10+）
python -m pip install -r requirements.txt

# 3. 运行
python main.py
```

## 📦 打包独立 EXE

```bash
# 安装完整依赖（含 PyInstaller）
python -m pip install -r requirements-dev.txt

# 一键打包（自动清理旧进程 → 构建 → 输出到项目根目录）
rebuild.bat

# 或者手动执行
pyinstaller --noconfirm --clean CC-Switch-Monitor.spec
```

产物为单文件 `API-Monitor.exe`（约 30–35 MB），已内置 `web/` 前端资源，可直接拷贝给没有 Python 环境的 Windows 机器使用。

> 正式版本一律通过 [GitHub Releases](https://github.com/249469326i-lang/api-monitor/releases) 分发，exe **不会**提交进源码仓库——请只从 Releases 页下载，谨防第三方镜像篡改。

## 🗂️ 项目结构

```text
api-monitor/
├── main.py                        # 程序入口（PyWebView + 后端 API 桥接）
├── core/                          # 后端核心模块
│   ├── providers.py               # 供应商管理
│   ├── testing.py                 # 延迟 / 可用性探测
│   ├── failover.py                # 故障转移
│   ├── scheduler.py               # 定时任务
│   ├── crypto.py                  # DPAPI 密钥加密
│   ├── tray.py                    # 系统托盘
│   ├── updater.py                 # 自动更新（检查 GitHub Releases）
│   └── ...
├── web/                           # 前端 UI（index.html / css / js / assets）
├── docs/                          # 设计文档、CODE_WIKI 与截图
├── CC-Switch-Monitor.spec         # PyInstaller 打包规格
├── rebuild.bat                    # 一键重打包脚本
├── verify_backend.py              # 后端 API 冒烟验证脚本
├── test_fetch_models_isolation.py # 模型拉取隔离性单元测试
├── bump_version.py                # 发版时统一更新各处版本号
└── requirements.txt               # 运行时依赖
```

更详细的架构说明见 [docs/CODE_WIKI.md](docs/CODE_WIKI.md)。

## ⚙️ 配置与数据

| 路径 | 用途 |
| --- | --- |
| `%APPDATA%\.cc-switch-monitor\` | 本地配置、加密后的 Key、历史探测数据 |
| 应用内「设置」 | 超时、重试次数、探测间隔、开机自启、自动更新等 |

所有数据均保存在用户目录，**不会**写入程序安装目录。网络请求仅发往两类目标：你在应用内自行配置的 API 端点（探测时需携带对应 Key，这是监控功能本身），以及本仓库的 GitHub Releases（自动更新检查，可在设置中关闭）。此外不向任何服务器发送数据。

## ✅ 运行测试

```bash
# 后端 API 冒烟测试（真实调用每个后端方法）
python verify_backend.py

# 单元测试（模型拉取隔离性）
python -m unittest test_fetch_models_isolation -v
```

## 🔐 安全说明

- API Key 使用 **Windows DPAPI** 加密后存储，仅当前 Windows 用户可解密
- 单实例互斥锁，避免重复启动产生多个托盘图标
- 自动更新仅查询本仓库的 GitHub Releases，不访问其他服务器
- 请勿将真实 API Key、`providers.db` 或本地配置提交到 git

发现安全问题？请阅读 [SECURITY.md](SECURITY.md)，**不要**直接开公开 Issue。

## 🗺️ 路线图

- [x] v3.0.0 — 像素风 UI、独立 Provider 存储、自动更新
- [ ] 探测历史图表与数据导出增强
- [ ] 更多供应商预设模板
- [ ] 多语言界面（欢迎 PR）

完整规划与进展见 [Issues](https://github.com/249469326i-lang/api-monitor/issues)。

## 🤝 参与贡献

欢迎 Issue 与 Pull Request！提交前请先阅读 [贡献指南](CONTRIBUTING.md)。

1. Fork 本仓库
2. 新建特性分支 `git checkout -b feature/your-feature`
3. 提交改动 `git commit -m "feat: add your feature"`
4. 推送分支 `git push origin feature/your-feature`
5. 发起 Pull Request

较大的改动建议先开 Issue 讨论方案。

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源 © 2026 249469326i-lang。
