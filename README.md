# API Monitor

[![Platform](https://img.shields.io/badge/platform-Windows%2010%2B-blue)](#system-requirements)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-green)](#build-from-source)

Windows 桌面端 **API 供应商监控工具**：定时测速、故障切换、托盘常驻、本地加密保存 API Key。

<p align="center">
  <img src="docs/images/screenshot.png" alt="API Monitor screenshot" width="900" />
</p>

## Features

- 多供应商 / 多模型延迟与可用性监控
- 定时探测、失败重试与故障转移
- 系统托盘常驻，支持开机自启
- API Key 使用 Windows DPAPI 本地加密存储
- 单文件 `API-Monitor.exe` 打包，下载即可运行
- 现代像素风 UI（内置 Web 前端）

## Download & Install

1. 打开 [Releases](https://github.com/249469326i-lang/api-monitor/releases)
2. 下载最新的 `API-Monitor.exe`
3. 双击运行（无需安装）

> 首次运行会在 `%APPDATA%\.cc-switch-monitor\` 创建本地配置与数据库。  
> 需要系统已安装 **Microsoft Edge WebView2 Runtime**（Windows 10/11 通常已自带）。

## System Requirements

| Item | Requirement |
| --- | --- |
| OS | Windows 10 / 11 (x64) |
| Runtime | WebView2 Runtime |
| Disk | ~50 MB |
| Network | 访问你配置的 API 端点 |

## Quick Start (Source)

```bash
# 1. clone
git clone https://github.com/249469326i-lang/api-monitor.git
cd api-monitor

# 2. install deps
python -m pip install -r requirements.txt

# 3. run
python main.py
```

### Build standalone EXE

```bash
python -m pip install -r requirements-dev.txt
# 或直接：
rebuild.bat
```

打包产物：`API-Monitor.exe`（单文件，约 30–35 MB）。

## Project Layout

```text
api-monitor/
├── main.py                 # 入口（PyWebView）
├── core/                   # 后端：探测、存储、托盘、更新等
├── web/                    # 前端 UI（index.html / css / js / assets）
├── docs/                   # 文档与截图
├── CC-Switch-Monitor.spec  # PyInstaller 规格
├── rebuild.bat             # 一键重打包
├── requirements.txt
└── LICENSE
```

## Configuration & Data

| Path | Purpose |
| --- | --- |
| `%APPDATA%\.cc-switch-monitor\` | 本地配置、加密 Key、历史数据 |
| 应用内「设置」 | 超时、重试、探测间隔、开机自启等 |

**不会**把 API Key 明文写进安装目录或 git 仓库。

## Security

- API Key：Windows DPAPI 加密
- 单实例互斥：避免重复托盘图标
- 自动更新：仅检查本仓库 GitHub Releases

详见 [SECURITY.md](SECURITY.md)。

## Roadmap / Notes

- 源码版本：`3.0.0`
- 二进制发布走 GitHub Releases（exe 不进源码仓库）
- Issue / PR 欢迎；大改动建议先开 discussion

## License

[MIT](LICENSE) © 2026 249469326i-lang
