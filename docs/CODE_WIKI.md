# API Monitor (CC Switch Monitor) — Code Wiki

> 版本: v3.0.0 | 文档日期: 2026-07-02
> 定位: 一款基于 PyWebView 的 Windows 桌面应用，用于实时监控 Claude Code / 各类 AI API 提供商的可用性与服务质量，并支持自动故障切换（Failover）。

---

## 目录

1. [项目概述](#1-项目概述)
2. [项目整体架构](#2-项目整体架构)
3. [目录结构](#3-目录结构)
4. [核心模块职责](#4-核心模块职责)
5. [关键类与函数说明](#5-关键类与函数说明)
6. [数据模型（数据库 Schema）](#6-数据模型数据库-schema)
7. [前后端交互机制](#7-前后端交互机制)
8. [依赖关系](#8-依赖关系)
9. [项目运行方式](#9-项目运行方式)
10. [关键业务流程](#10-关键业务流程)
11. [外部数据与文件位置](#11-外部数据与文件位置)

---

## 1. 项目概述

**API Monitor** 是一个面向 Claude Code 用户和 AI API 中继使用者的桌面监控工具。它的核心能力包括：

- **供应商管理**：CRUD 管理 AI API 提供商列表（端点、API Key、模型等）。
- **连通性测试**：支持快速测试（`/models` 探测）与完整测试（发送 "Hi" 验证 AI 回复），兼容 Anthropic / OpenAI Chat / OpenAI Responses / Gemini 四种 API 格式。
- **自动故障切换**：当前供应商失败时，按延迟与状态自动切换到最优备用供应商，写入 `~/.claude/settings.json`。
- **定时测试调度**：后台线程按配置间隔自动执行全量测试。
- **历史趋势与可用率**：记录每次测试历史，支持 P95 延迟、可用率统计与延迟趋势图。
- **通知系统**：应用内通知中心 + Windows Toast + Webhook（Slack/飞书/钉钉）。
- **数据安全**：API Key 通过 Windows DPAPI 加密存储；SSL 证书验证可配置；SQL 字段白名单防注入；输入校验层。
- **数据导出与备份**：CSV/JSON 导出供应商与历史；数据库一键备份/恢复。
- **自动更新**：启动时检查 GitHub Releases 新版本。

技术栈：**Python + SQLite** 后端，**单文件 HTML/JS/CSS** 前端，**PyWebView** 作为桌面壳层，**PyInstaller** 打包为单文件 EXE。

---

## 2. 项目整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    PyWebView 窗口 (main.py)                   │
│  ┌────────────────────────┐    ┌───────────────────────────┐  │
│  │   前端 (web/index.html)  │    │   后端 API 类 (main.API)  │  │
│  │   web/css/style.css     │◄──►│   js_api 暴露给前端        │  │
│  │   web/js/app.js         │    │   通过 evaluate_js 推送    │  │
│  │   - App 类 (真实后端)    │    │                           │  │
│  │   - MockBackend (浏览器) │    │   持有 Scheduler / 锁 /    │  │
│  └────────────────────────┘    │   测试线程 / window 引用    │  │
│                                └────────────┬──────────────┘  │
└─────────────────────────────────────────────┼────────────────┘
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
                   │  core/db    │     │ core/testing│     │core/providers│
                   │  SQLite 操作 │     │  连通性测试  │     │ 供应商/导入   │
                   └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
                          │                   │                   │
                   ┌──────┴──────┐      ┌──────┴──────┐     ┌──────┴──────┐
            ┌─────►│core/failover│      │core/crypto  │     │~/.claude/   │
            │      └─────────────┘      │ DPAPI 加密  │     │settings.json│
            │      ┌─────────────┐      └─────────────┘     └─────────────┘
            │      │core/notif   │      ┌─────────────┐     ┌─────────────┐
            │      │Toast+Webhook│      │core/scheduler│◄───►│~/.cc-switch/│
            │      └─────────────┘      │ 定时调度    │     │cc-switch.db │
            │      ┌─────────────┐      └─────────────┘     └─────────────┘
            │      │core/export  │      ┌─────────────┐
            │      │CSV/备份恢复  │      │core/updater │
            │      └─────────────┘      │ GitHub 检查  │
            │      ┌─────────────┐      └─────────────┘
            │      │core/validators│     ┌─────────────┐
            └──────┤ 输入校验    │      │core/logging │
                   └─────────────┘      │ RotatingLog │
                                        └─────────────┘
```

### 分层说明

| 层 | 位置 | 职责 |
|----|------|------|
| **壳层（Shell）** | `main.py` | 创建 PyWebView 窗口、定义 `API` 类（前端↔后端桥）、启动调度器、生命周期管理 |
| **业务逻辑层** | `core/*.py` | 数据库、测试、供应商、Failover、通知、调度、导出、校验、加密、更新、日志 |
| **表现层** | `web/*` | 单页应用 UI（HTML/CSS/JS），`App` 类驱动渲染，`MockBackend` 提供浏览器预览 |
| **数据层** | SQLite (`providers.db`) | providers / test_history / settings / notifications / provider_endpoints 表 |
| **外部集成** | 文件系统 | 读写 `~/.claude/settings.json`、读取 `~/.cc-switch/cc-switch.db`、DPAPI 加密、Windows Toast |

---

## 3. 目录结构

```
CC Switch Monitor/
├── main.py                    # 应用入口：定义 API 类 + main() 启动 PyWebView
├── verify_backend.py          # 后端 API 验证脚本（逐个真实调用 main.API 方法）
├── requirements.txt           # 依赖（仅 pywebview>=4.0）
├── CC-Switch-Monitor.spec     # PyInstaller 打包配置
├── rebuild.bat                # Windows 一键重建 EXE 脚本
├── cc_switch_icon.ico         # 应用图标
├── file_version_info.txt      # EXE 版本信息资源
├── API-Monitor.exe            # 已构建的可执行文件
├── core/                      # 核心业务逻辑模块
│   ├── __init__.py            # 导出所有子模块
│   ├── db.py                  # SQLite 数据库操作（CRUD + 历史统计 + 设置 + 通知）
│   ├── testing.py             # 供应商连通性测试（并发/重试/多格式/错误分类）
│   ├── providers.py           # 供应商管理、cc-switch 导入、写入 settings.json
│   ├── failover.py            # 自动故障切换
│   ├── notifications.py       # Windows Toast + Webhook + 应用内通知
│   ├── scheduler.py           # 后台定时测试调度器（Scheduler 类）
│   ├── export.py              # CSV/JSON 导出 + 数据库备份/恢复
│   ├── validators.py          # 输入校验（端点/名称/Key/模型名）
│   ├── crypto.py              # Windows DPAPI 加解密 API Key
│   ├── updater.py             # GitHub Releases 新版本检查
│   └── logging_config.py      # RotatingFileHandler 配置 + Key 脱敏
├── web/                       # 前端单页应用
│   ├── index.html            # 主页面（含顶栏/指标卡/列表/详情面板/模态框）
│   ├── css/style.css         # 样式（Neo-Brutalist 风格）
│   ├── js/app.js             # 前端逻辑（App 类 + MockBackend 类）
│   └── prototypes/           # UI 原型设计稿（设计参考，非运行时依赖）
├── docs/                      # 设计文档与实施计划
└── build/, dist/              # PyInstaller 构建产物（临时）
```

---

## 4. 核心模块职责

### 4.1 `main.py` — 应用入口与后端 API 桥

**职责**：创建 PyWebView 窗口，将 `API` 类通过 `js_api` 暴露给前端 JavaScript 调用；管理测试线程、调度器、窗口引用；通过 `evaluate_js` 主动向前端推送事件。

**关键点**：
- `API` 类的 `window` 引用必须用下划线前缀（`_window`），否则 pywebview 6.x 的 `get_functions()` 会递归遍历导致卡死（见代码注释 L15-L19）。
- 线程安全：使用 `threading.Lock`（`_testing_lock`）保护 `_testing` 状态，`threading.Event`（`_stop_event`）替代布尔标志实现优雅停止。
- 启动时自动从 `~/.claude/settings.json` 同步当前供应商角色。

### 4.2 `core/db.py` — 数据库操作模块

**职责**：封装所有 SQLite 操作，包含建表/迁移、供应商 CRUD、测试历史记录与统计、设置键值存储、应用内通知管理。

**关键点**：
- 使用 `@contextmanager` 封装 `get_connection()`，自动 commit/rollback/close。
- 启用 WAL 模式与外键约束（`PRAGMA journal_mode=WAL` / `PRAGMA foreign_keys=ON`）。
- API Key 自动加密：写入时加密，读取时解密；并对明文历史 Key 自动迁移（`_maybe_migrate_key`）。
- `UPDATABLE_FIELDS` 白名单防止 SQL 注入。
- 历史统计支持 P95 延迟、可用率计算与按时间分桶的时间线聚合。

### 4.3 `core/testing.py` — 供应商连通性测试

**职责**：测试单个/全部供应商，支持快速模式（`/models`）与完整模式（发送 "Hi" 验证 AI 回复），兼容四种 API 格式。

**关键点**：
- **并发测试**：`ThreadPoolExecutor`，并发数从设置读取（1-10，默认 3）。
- **重试机制**：指数退避（1s/2s/4s），`auth_error` / `rate_limited` 不重试。
- **错误分类**：`_classify_error()` 将异常分为 timeout/dns_failure/ssl_error/auth_error/rate_limited 等，每类附排查建议。
- **多格式兼容**：`_test_chat_endpoint()` 按 `app_type` 决定优先级，依次尝试 Anthropic Messages / OpenAI Chat / OpenAI Responses / Gemini。
- **API 格式探测**：`_probe_api_format()` 快速测试后轻量探测，通过错误码（400/401/403/422/429）判定端点格式。
- **伪装 UA**：使用 Chrome UA 避免 Cloudflare 1010 拦截 Python-urllib 默认 UA。
- **优雅中断**：通过 `stop_event` 跳过剩余测试并 `cancel_futures`。

### 4.4 `core/providers.py` — 供应商管理与导入

**职责**：供应商数据格式化、从 cc-switch 数据库导入、写入 Claude Code 配置、启动 Claude Code。

**关键点**：
- **cc-switch 导入**：`_extract_provider_from_ccswitch_row()` 解析 cc-switch 的 `settings_config` JSON 结构，支持 claude/hermes/codex/gemini 四种 app_type 的配置提取（仅导入 claude 类型）。
- **写入当前配置**：`set_current_provider()` 更新 `~/.claude/settings.json` 的 `env` 字段（`ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`），同时更新库内 role 字段。
- **同步当前配置**：`sync_current_from_settings()` 启动时根据 settings.json 的 URL 反向匹配库内供应商，标记为「当前」。
- **API Key 掩码**：`_format_provider()` / `get_provider_list_formatted()` 输出前4后4掩码。

### 4.5 `core/failover.py` — 自动故障切换

**职责**：当前供应商测试失败时，按优先级（最近 OK → 延迟最低 → ID 最小）自动切换到最优备用供应商。

**关键点**：
- 模块级状态：`_last_switch_time` / `_consecutive_switches` 记录切换历史。
- **冷却机制**：冷却时间内（默认 300s）不重复切换。
- **连续切换上限**：达到 `failover_max_switches`（默认 3）后停止切换，防止全部失败时无限切换。
- 仅对 `role == "当前"` 的供应商触发切换。
- `reset_counter()` 在手动切换供应商或手动测试成功时重置计数。

### 4.6 `core/notifications.py` — 通知系统

**职责**：统一通知入口，同时触发应用内通知（写库）、Windows Toast、Webhook。

**关键点**：
- **Windows Toast**：通过 PowerShell 调用 `Windows.UI.Notifications.ToastNotificationManager`，无额外依赖；后台线程执行避免阻塞。
- **Webhook**：HTTP POST，支持 Slack/飞书/钉钉/通用 JSON；按 `webhook_events` 配置过滤事件类型；失败不阻塞主流程。
- 事件类型：`status_change` / `failover` / `test_complete`，每种事件可独立开关。

### 4.7 `core/scheduler.py` — 定时测试调度器

**职责**：`Scheduler` 类，后台线程按配置间隔自动触发全量测试。

**关键点**：
- 基于 `threading.Event` + `wait(1)` 实现可中断的定时循环。
- 支持 start/stop/update_interval/reset_countdown。
- `get_status()` 返回运行状态与下次执行倒计时。

### 4.8 `core/export.py` — 数据导出与备份

**职责**：供应商列表导出（CSV/JSON）、测试历史导出（CSV）、数据库备份与恢复。

**关键点**：
- CSV 使用 `utf-8-sig` 编码（兼容 Excel）。
- 备份保留最近 7 份（`_cleanup_old_backups`）。
- 恢复前自动备份当前数据库为 `.pre_restore` 安全副本。

### 4.9 `core/validators.py` — 输入校验

**职责**：对前端提交的供应商数据做格式与长度校验。

**校验规则**：
- 端点：http/https 协议，长度 ≤ 500。
- 名称：非空，长度 ≤ 100。
- API Key：长度 ≤ 2000。
- 模型名：仅字母/数字/`._:/-`，长度 ≤ 100。

### 4.10 `core/crypto.py` — API Key 加密

**职责**：基于 Windows DPAPI（`CryptProtectData` / `CryptUnprotectData`）加密 API Key。

**关键点**：
- 加密后以 `"enc:"` 前缀 + Base64 存储。
- 非 Windows 平台自动降级为明文。
- `migrate_provider_keys()` 批量迁移明文 Key。

### 4.11 `core/updater.py` — 自动更新检查

**职责**：调用 GitHub Releases API 检查新版本。

**关键点**：
- 优先返回 `.exe` 资源下载链接。
- `_compare_versions()` 语义化版本比较。

### 4.12 `core/logging_config.py` — 日志配置

**职责**：`RotatingFileHandler`（5MB×3）+ API Key 脱敏函数。

---

## 5. 关键类与函数说明

### 5.1 `main.API` 类（后端 API 桥）

| 方法 | 说明 |
|------|------|
| `__init__()` | 初始化数据库、同步当前配置、启动调度器、清理过期数据 |
| `set_window(window)` | 注入 PyWebView 窗口引用（必须下划线前缀） |
| `get_providers()` | 返回格式化供应商列表 |
| `get_stats()` | 返回统计 {total, ok, fail, pending} |
| `add_provider(data)` / `update_provider(id, data)` / `delete_provider(id)` | 供应商 CRUD（含校验与加密） |
| `test_provider(provider_id)` | 测试单个供应商（full 模式），触发状态变化通知与 Failover |
| `test_all(mode)` | 异步批量测试（fast/full），返回后线程执行，通过 JS 推送增量更新 |
| `stop_testing()` | 设置 stop_event 并等待测试线程退出 |
| `fetch_models(endpoint, api_key)` | 获取可用模型列表 |
| `import_from_ccswitch()` / `choose_and_import_from_file()` | 从 cc-switch 导入 |
| `set_current_provider(id)` | 设为当前配置（写 settings.json） |
| `get_test_history` / `get_history_stats` / `get_history_timeline` | 历史查询 |
| `get_notifications` / `get_unread_count` / `mark_notifications_read` | 通知管理 |
| `get_all_settings` / `save_settings` | 设置读写 |
| `start_scheduler` / `stop_scheduler` / `get_scheduler_status` | 调度控制 |
| `export_providers` / `export_history` / `create_backup` / `restore_backup` / `list_backups` | 导出与备份 |
| `check_update()` | 检查新版本 |
| `launch_claude()` | 启动 Claude Code |
| `_push_*` 内部方法 | 通过 `evaluate_js` 向前端推送事件（single_update/cli_log/testing_complete/unread_count/failover_event/scheduler_tick） |

### 5.2 `core/scheduler.Scheduler` 类

| 成员 | 说明 |
|------|------|
| `start(interval_seconds)` | 启动定时器，间隔 ≤0 时从设置读取 |
| `stop()` | 停止定时器 |
| `update_interval(seconds)` | 更新间隔（自动重启） |
| `reset_countdown()` | 重置倒计时（手动测试时调用） |
| `get_status()` | 返回 {running, interval, next_run, remaining} |
| `_run_loop()` | 后台循环：到点调用 `test_callback(mode="full")` |

### 5.3 `core/db.py` 关键函数

| 函数 | 说明 |
|------|------|
| `get_db_path()` | 返回 `%APPDATA%\.cc-switch-monitor\providers.db` |
| `discover_ccswitch_db()` | 查找 cc-switch 数据库文件 |
| `init_db()` | 建表 + 迁移（WAL/外键/废弃表清理/列迁移） |
| `get_providers()` / `get_provider_by_id(id)` | 读取（自动解密 + 明文迁移） |
| `add_provider(data)` / `update_provider(id, data)` / `delete_provider(id)` | 写入（自动加密 + 白名单校验） |
| `update_provider_status(id, status, latency, detail)` | 更新测试状态 |
| `add_test_history` / `get_test_history` / `get_history_stats` / `get_history_timeline` | 历史记录与统计 |
| `get_setting` / `set_setting` / `get_all_settings` / `set_settings_batch` | 设置键值存储（含 DEFAULT_SETTINGS 默认值） |
| `add_notification` / `get_notifications` / `get_unread_count` / `mark_notifications_read` | 通知管理 |
| `cleanup_old_history(days)` / `cleanup_old_notifications(days)` | 过期数据清理 |

### 5.4 `core/testing.py` 关键函数

| 函数 | 说明 |
|------|------|
| `test_provider(provider_id, mode, log_callback)` | 测试单个供应商（重试 + 错误分类 + 历史写入） |
| `test_all_providers(mode, callback, log_callback, stop_event)` | 并发测试全部供应商 |
| `fetch_models(endpoint, api_key)` | 拉取模型列表（多路径/多认证兼容） |
| `_test_models_endpoint(endpoint, api_key)` | 快速测试 `/models` 端点 |
| `_test_chat_endpoint(endpoint, api_key, model, app_type)` | 完整测试聊天端点（四格式优先级尝试） |
| `_probe_api_format(endpoint, api_key, model, app_type)` | 轻量 API 格式探测 |
| `_classify_error(error_str)` | 错误类型分类 |
| `_extract_response_snippet(body_str, style)` | 提取 AI 回复片段（兼容四格式） |
| `_send_post_request(url, headers, body)` | 发送 POST 请求（401/403 视为可达） |

### 5.5 `core/providers.py` 关键函数

| 函数 | 说明 |
|------|------|
| `import_from_ccswitch()` | 自动发现并导入 cc-switch 数据库 |
| `import_from_path(db_path)` | 从指定路径导入（支持 cc-switch 真实 schema 与简化 schema） |
| `_extract_provider_from_ccswitch_row(row_dict)` | 解析 cc-switch settings_config JSON |
| `set_current_provider(provider_id)` | 写入 `~/.claude/settings.json` 并更新 role |
| `sync_current_from_settings()` | 启动时反向同步当前配置 |
| `launch_claude_code()` | 启动 Claude Code（claude / npx 两种方式） |
| `get_provider_list_formatted()` / `_format_provider(p)` | 格式化输出（含 Key 掩码） |

### 5.6 `core/failover.py` 关键函数

| 函数 | 说明 |
|------|------|
| `check_and_failover(failed_provider_id, log_callback)` | 检查并执行切换，返回 {switched, from, to, to_id, reason} |
| `_find_best_candidate(exclude_id)` | 查找最优备用（OK → 延迟最低 → ID 最小） |
| `reset_counter()` | 重置连续切换计数 |

### 5.7 前端 `web/js/app.js` 关键类

#### `App` 类（真实后端模式）

| 方法 | 说明 |
|------|------|
| `init()` | 绑定事件、监听 pywebviewready、定时刷新调度器/未读数、静默检查更新 |
| `backend()` | 返回 pywebview API 或 MockBackend（浏览器预览兜底） |
| `bindEvents()` | 绑定所有 UI 事件（窗口控制/按钮/快捷键/搜索） |
| `loadData()` | 加载供应商列表与统计 |
| `renderProviders(list)` / `renderStats(stats)` | 渲染列表与统计卡片 |
| `updateSingleProvider(data)` | 增量更新单条供应商（测试回调时） |
| `addCliLog(level, text, name)` / `pushLog` | CLI 日志面板 |
| `testAll(mode)` / `stopTesting()` | 触发/停止测试 |
| `selectProvider(id)` / `renderProviderDetail(provider)` | 选中并渲染详情 |
| `_renderAvailCards(providerId)` | 渲染可用率/趋势卡片 |
| `_drawTrendChart(timeline)` | Canvas 绘制延迟趋势图（无外部图表库） |
| `onFailoverEvent(data)` | Failover 事件回调 |
| `updateSchedulerStatus(status)` | 更新调度器状态显示 |
| `exportProviders(format)` / `backupDb()` / `restoreDb()` | 导出与备份 |
| `batchTest` / `batchDelete` / `batchSetBackup` | 批量操作 |
| `showConfirm(message, title, okLabel)` | 自定义确认弹窗（Promise） |

#### `MockBackend` 类（浏览器预览模式）

当 PyWebView 后端不可用时（如在浏览器直接打开 index.html），提供基于 `localStorage` 的模拟实现，覆盖所有 API 方法，便于 UI 原型预览。导出/备份类方法返回不支持错误。

---

## 6. 数据模型（数据库 Schema）

数据库路径：`%APPDATA%\.cc-switch-monitor\providers.db`（SQLite，WAL 模式）

### 6.1 `providers` 表（供应商主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK AUTO | 主键 |
| name | TEXT | 名称 |
| app_type | TEXT | 应用类型（claude/hermes/codex/gemini），默认 claude |
| role | TEXT | 角色（"当前" / "备用"） |
| endpoint | TEXT | API 端点 URL |
| api_key | TEXT | 加密后的 API Key（"enc:"+base64） |
| website | TEXT | 官网 |
| category | TEXT | 分类 |
| notes | TEXT | 备注 |
| default_model | TEXT | 默认模型 |
| api_format | TEXT | 检测到的 API 格式（anthropic_messages/openai_chat/openai_responses/gemini_native） |
| status | TEXT | 状态（pending/testing/ok/fail） |
| latency | INTEGER | 延迟（ms） |
| last_test_time | INTEGER | 最后测试时间戳 |
| test_detail | TEXT | 测试详情/错误信息 |
| created_at / updated_at | INTEGER | 时间戳 |

### 6.2 `provider_endpoints` 表（备用端点）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| provider_id | INTEGER FK | 关联 providers.id（ON DELETE CASCADE） |
| endpoint | TEXT | 备用端点 |
| api_key | TEXT | 备用 Key |
| status / latency / last_test_time | - | 同 providers |

### 6.3 `test_history` 表（测试历史）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| provider_id | INTEGER FK | 关联 providers.id |
| status | TEXT | ok/fail |
| latency | INTEGER | 延迟 |
| error_type | TEXT | 错误分类 |
| error_detail | TEXT | 错误详情 |
| test_mode | TEXT | fast/full |
| created_at | INTEGER | 时间戳 |

索引：`idx_history_provider_time` (provider_id, created_at)

### 6.4 `settings` 表（键值设置）

| 字段 | 类型 | 说明 |
|------|------|------|
| key | TEXT PK | 配置键 |
| value | TEXT | 配置值 |
| updated_at | INTEGER | 时间戳 |

**默认设置**（`DEFAULT_SETTINGS`）：

| 键 | 默认值 | 说明 |
|----|--------|------|
| auto_test_interval | 0 | 定时间隔（秒，0=关闭） |
| failover_enabled | 1 | 启用故障切换 |
| failover_need_confirm | 0 | 切换前确认 |
| failover_max_switches | 3 | 最大连续切换次数 |
| failover_cooldown | 300 | 切换冷却（秒） |
| notify_status_change | 1 | 状态变化通知 |
| notify_failover | 1 | Failover 通知 |
| notify_test_complete | 0 | 测试完成通知 |
| webhook_url | (空) | Webhook URL |
| webhook_events | status_change,failover | Webhook 事件 |
| history_retention_days | 30 | 历史保留天数 |
| test_concurrency | 3 | 并发测试数 |
| test_timeout | 30 | 超时（秒） |
| test_retries | 2 | 重试次数 |
| ssl_verify | 1 | SSL 验证 |

### 6.5 `notifications` 表（应用内通知）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 主键 |
| type | TEXT | 事件类型（status_change/failover/test_complete） |
| title | TEXT | 标题 |
| message | TEXT | 内容 |
| provider_id | INTEGER | 关联供应商 |
| is_read | INTEGER | 已读标志（0/1） |
| created_at | INTEGER | 时间戳 |

索引：`idx_notifications_time` (created_at DESC)

---

## 7. 前后端交互机制

### 7.1 前端 → 后端（RPC 调用）

PyWebView 将 `API` 类实例通过 `js_api=api` 注入前端。前端通过 `window.pywebview.api.<method>(args)` 异步调用后端方法（返回 Promise）。方法名使用 snake_case（如 `api.get_providers()`、`api.test_all('fast')`）。

### 7.2 后端 → 前端（事件推送）

后端通过 `window.evaluate_js()` 主动调用前端挂载在 `window.app` 上的方法：

| 后端推送方法 | 前端接收方法 | 触发场景 |
|-------------|-------------|---------|
| `_refresh_frontend()` | `window.app.loadData()` | 数据变更后全量刷新 |
| `_push_single_update(id)` | `window.app.updateSingleProvider(data)` + `updateStatsIncremental(stats)` | 单个供应商测试完成 |
| `_push_cli_log(level, text, name)` | `window.app.addCliLog(level, text, name)` | 测试日志实时输出 |
| `_push_testing_complete()` | `window.app.testingComplete()` | 批量测试全部结束 |
| `_push_unread_count()` | `window.app.updateUnreadCount(count)` | 未读通知数变化 |
| `_push_failover_event(result)` | `window.app.onFailoverEvent(data)` | Failover 发生 |
| `_push_scheduler_tick()` | `window.app.updateSchedulerStatus(status)` | 调度器状态更新 |

### 7.3 前端初始化时序

1. `DOMContentLoaded` → `new App()` → `init()` → 立即用 `MockBackend` 渲染（不阻塞）。
2. 监听 `pywebviewready` 事件 → 绑定真实 API → `loadData()` 覆盖。
3. 兜底：200ms 后再次检测 `window.pywebview.api` 是否就绪。
4. 启动后：3s 后静默检查更新；5s 定时刷新调度器状态与未读数。

---

## 8. 依赖关系

### 8.1 外部依赖

| 依赖 | 用途 | 来源 |
|------|------|------|
| `pywebview` (>=4.0) | 桌面 WebView 壳层（Windows 使用 EdgeChromium/WinForms） | requirements.txt |

**注意**：项目刻意保持单一外部依赖。其余功能全部使用 Python 标准库实现：
- `sqlite3` — 数据库
- `urllib.request` — HTTP 请求（测试/更新/webhook）
- `ssl` — SSL 上下文
- `concurrent.futures` — 并发测试
- `threading` — 线程/锁/Event
- `ctypes` — Windows DPAPI 加密 / Toast 通知
- `csv` / `json` — 导出
- `logging` / `logging.handlers` — 日志
- `subprocess` — 启动 Claude Code / PowerShell Toast
- `hashlib` — 隐式使用

### 8.2 模块内部依赖关系

```
main.py
 ├── core.db              (数据读写)
 ├── core.testing         (测试) ──► core.db
 ├── core.providers       (供应商) ──► core.db
 ├── core.failover        (切换) ──► core.db, core.providers
 ├── core.notifications   (通知) ──► core.db
 ├── core.scheduler       (调度) ──► core.db
 ├── core.export          (导出) ──► core.db
 ├── core.validators      (校验)
 ├── core.logging_config  (日志)
 └── core.updater         (更新，延迟导入)

core.db ──► core.crypto (延迟导入，避免循环依赖)
```

`crypto` 与 `db` 之间存在延迟导入（`db._encrypt_api_key` 内 `from . import crypto`），以避免循环依赖。

---

## 9. 项目运行方式

### 9.1 开发模式（直接运行）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动应用
python main.py
```

启动后弹出 PyWebView 窗口（1200×780，无边框），加载 `web/index.html`。

### 9.2 浏览器预览模式

直接用浏览器打开 `web/index.html`，前端自动降级为 `MockBackend`（基于 localStorage），可预览 UI 但无真实后端能力（测试/导出/备份等返回不支持）。

### 9.3 打包为 EXE（PyInstaller）

```bash
# 方式一：使用 spec 文件
pyinstaller --noconfirm --clean CC-Switch-Monitor.spec

# 方式二：一键脚本（停止旧进程→删除→构建→移动→启动）
rebuild.bat
```

构建产物：`dist/API-Monitor.exe`（单文件，UPX 压缩，无控制台窗口）。`rebuild.bat` 会自动移动到根目录 `API-Monitor.exe` 并启动。

**打包要点**（见 `CC-Switch-Monitor.spec`）：
- 将 `web/` 目录作为 data 文件打包进 EXE。
- `hiddenimports` 显式声明 `webview.platforms.edgechromium`、`webview.platforms.winforms` 等。
- 运行时通过 `sys._MEIPASS`（frozen 模式）定位 web 资源路径。

### 9.4 后端验证

```bash
python verify_backend.py
```

逐个真实调用 `main.API` 的方法（版本/统计/CRUD/测试/导入/启动等），退出码 0 表示全部通过。

---

## 10. 关键业务流程

### 10.1 单供应商测试流程（full 模式）

```
test_provider(id)
  ├── 读取供应商 (db.get_provider_by_id)
  ├── 重试循环 (max_retries + 1 次，指数退避)
  │     ├── _test_chat_endpoint() 按 app_type 优先级尝试 4 种 API 格式
  │     │     └── _send_post_request() → _extract_response_snippet()
  │     └── 成功则跳出；auth_error/rate_limited 不重试
  ├── 更新状态 (db.update_provider_status) + 写历史 (db.add_test_history)
  ├── 保存检测到的 api_format (db.update_api_format)
  ├── 状态变化时发送通知 (notifications.notify)
  └── 失败且是当前供应商 → failover.check_and_failover()
        └── 切换成功 → providers.set_current_provider() + 通知 + 推送 failover_event
```

### 10.2 批量测试流程

```
test_all(mode)
  ├── 加锁检查 _testing 状态
  ├── 启动后台线程 run_tests()
  │     ├── ThreadPoolExecutor 并发 (concurrency 数)
  │     ├── 每完成一个 → callback(pid, result) → _push_single_update
  │     ├── 失败 → failover.check_and_failover()
  │     └── stop_event 检查 → 优雅中断
  └── _push_testing_complete() + 完成通知
```

### 10.3 Failover 切换流程

```
测试失败 (role == "当前")
  └── failover.check_and_failover(id)
        ├── 检查 failover_enabled
        ├── 冷却时间检查 (failover_cooldown)
        ├── 连续切换上限检查 (failover_max_switches)
        ├── _find_best_candidate() (OK → 延迟最低 → ID 最小)
        ├── providers.set_current_provider(to_id) (写 settings.json + 更新 role)
        ├── 写通知 (db.add_notification)
        └── 返回 {switched, from, to, to_id}
```

### 10.4 定时测试流程

```
Scheduler._run_loop()
  ├── 每秒检查 _stop_event
  ├── 到达 _next_run → _test_callback(mode="full") → API._run_scheduled_test()
  │     └── test_all("full") + reset_countdown()
  └── 更新 _next_run
```

---

## 11. 外部数据与文件位置

| 路径 | 用途 |
|------|------|
| `%APPDATA%\.cc-switch-monitor\providers.db` | 本应用数据库（供应商/历史/设置/通知） |
| `%APPDATA%\.cc-switch-monitor\app.log` | 应用日志（RotatingFileHandler，5MB×3） |
| `%APPDATA%\.cc-switch-monitor\backups\` | 自动备份目录（保留最近 7 份） |
| `~/.claude/settings.json` | Claude Code 配置（`set_current_provider` 写入 `env` 字段） |
| `~/.cc-switch/cc-switch.db` | cc-switch 数据库（导入源，仅读取） |

---

## 附录：技术约束与设计决策

1. **单文件依赖**：刻意保持仅 `pywebview` 一个外部依赖，便于 PyInstaller 单文件打包，减小体积。
2. **前端单文件结构**：`index.html` + `style.css` + `app.js`，不引入构建工具与外部图表库（趋势图用原生 Canvas 绘制）。
3. **Neo-Brutalist UI 风格**：前端设计风格基线，原型见 `web/prototypes/`。
4. **数据库迁移**：`init_db()` 内置迁移逻辑（废弃表删除、列新增），兼容旧数据，无独立迁移脚本。
5. **明文 Key 兼容**：读取时检测明文 Key 自动加密回写，平滑升级。
6. **窗口引用下划线前缀**：规避 pywebview 6.x `get_functions()` 递归遍历公开属性导致卡死的 bug。
7. **伪装 UA**：使用 Chrome UA 规避 Cloudflare 对 Python-urllib 默认 UA 的 1010 拦截。
8. **401/403 视为可达**：POST 测试时，鉴权失败也说明端点可达，避免误判为连接失败。
