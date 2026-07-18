# API Monitor v4.0 UI 改版实施计划书

> 创建时间: 2026-06-30
> 状态: 待确认
> 参考设计稿: `C:\Users\AOTEM\Pictures\微信图片_20260625112700_190_3.png`
> 预览 Mockup: `outputs/api-monitor-ui-preview.html`

---

## 一、改版总览

### 1.1 设计方向

从当前的 **Neo-Brutalist Pop** 风格（粗边框、硬阴影、鲜明撞色）转变为 **Modern Glass Dashboard** 风格（柔和渐变、圆角卡片、毛玻璃导航栏、轻阴影层次）。

### 1.2 布局架构变更

| 维度 | 当前 (v3.0) | 目标 (v4.0) |
|------|------------|------------|
| 整体布局 | 左侧 Sidebar (180px) + 右侧 Main | 全宽 TopNav + 主内容区 |
| 导航 | 侧边栏垂直菜单 | 顶部水平导航栏（毛玻璃效果） |
| 统计展示 | 4 个独立 stat-card 一行排列 | 中央指标大卡（渐变背景 + 底部指标条） |
| 内容区 | 左侧提供商表格 + 右侧详情面板 (1fr + 260px) | 左侧提供商表格 + 右侧详情面板 (1fr + 1fr) |
| 窗口控制 | 固定在右上角 (frameless) | 保持固定右上角，融入导航栏 |
| 拖拽区域 | 顶部 30px drag-strip | 导航栏整体可拖拽（排除交互元素） |

### 1.3 涉及文件

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `web/index.html` | **重写** | 整体结构重组，新 DOM 树 |
| `web/css/style.css` | **重写** | 全新设计令牌 + 组件样式 |
| `web/js/app.js` | **中改** | 适配新 DOM 结构，更新渲染方法 |
| `main.py` | **微改** | 窗口参数调整（尺寸/标题） |
| `CC-Switch-Monitor.spec` | **无改** | 构建配置不涉及 UI |

---

## 二、设计令牌（Design Tokens）

### 2.1 色彩系统

```css
/* === 旧版 (Neo-Brutalist) === */
--cream: #f5f0e8;       /* 背景 */
--ink: #1a1a1a;         /* 主文字/边框 */
--yellow: #facc15;      /* 强调色 */
--pink: #ec4899;        /* 错误/危险 */
--lime: #84cc16;        /* 成功 */
--sky: #38bdf8;         /* 信息 */
--lavender: #c084fc;    /* 装饰 */

/* === 新版 (Modern Dashboard) === */
--bg-primary: #f0f4f8;          /* 主背景 */
--bg-card: #ffffff;             /* 卡片背景 */
--bg-nav: rgba(255,255,255,0.7); /* 导航栏（毛玻璃） */
--bg-hover: #edf2f7;            /* 悬浮态 */
--bg-selected: #ebf8ff;         /* 选中态 */
--bg-subtle: #f7fafc;           /* 次级背景 */

--text-primary: #1a202c;        /* 主文字 */
--text-secondary: #4a5568;      /* 次级文字 */
--text-muted: #718096;          /* 弱文字 */
--text-placeholder: #a0aec0;    /* 占位符 */

--accent-green: #38a169;        /* 品牌/成功 */
--accent-green-light: #f0fff4;
--accent-blue: #4299e1;         /* 信息/主要操作 */
--accent-blue-light: #ebf8ff;
--accent-orange: #ed8936;       /* 警告/强调 */
--accent-orange-light: #fffaf0;
--accent-red: #e53e3e;          /* 错误/危险 */
--accent-red-light: #fff5f5;
--accent-yellow: #ecc94b;       /* 辅助强调 */

--border-light: rgba(0,0,0,0.06);     /* 轻边框 */
--border-focus: #4299e1;               /* 聚焦边框 */
--shadow-sm: 0 1px 3px rgba(0,0,0,0.04);
--shadow-md: 0 4px 12px rgba(0,0,0,0.06);
--shadow-lg: 0 8px 24px rgba(0,0,0,0.08);
--radius-sm: 8px;
--radius-md: 12px;
--radius-lg: 18px;
--radius-xl: 20px;
```

### 2.2 字体系统

```css
--font-primary: -apple-system, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
--font-mono: ui-monospace, 'SF Mono', 'Cascadia Code', 'Consolas', monospace;

/* 字号层级 */
--text-xs: 10px;    /* 标签/微文 */
--text-sm: 11px;    /* 表格内容 */
--text-base: 13px;  /* 正文/按钮 */
--text-md: 15px;    /* 小标题 */
--text-lg: 20px;    /* 标题 */
--text-xl: 36px;    /* 大标题 */
--text-hero: 64px;  /* 核心指标数字 */
```

### 2.3 间距系统

```css
--space-xs: 4px;
--space-sm: 8px;
--space-md: 12px;
--space-lg: 16px;
--space-xl: 20px;
--space-2xl: 24px;
--space-3xl: 32px;
```

---

## 三、HTML 结构变更 (`web/index.html`)

### 3.1 整体结构对比

```
旧版结构:                              新版结构:
├── .drag-strip                        ├── nav.topnav
├── aside.sidebar                      │   ├── .nav-brand (API Monitor)
│   ├── .brand                         │   ├── .nav-btn (Features)
│   ├── nav.menu                       │   ├── .nav-btn (Notices)
│   └── .sidebar-footer               │   ├── .nav-search
├── main.main                          │   ├── .nav-spacer
│   ├── header.header                  │   ├── .nav-icon-btn (Shield)
│   │   ├── .header-left              │   ├── .nav-dashboard
│   │   └── .header-right             │   └── .nav-avatar
│   │       ├── .header-actions       ├── .window-controls (固定右上角)
│   │       └── .window-controls      └── .content
│   ├── section.stats-grid (4 cards)       ├── .metric-card (中央指标大卡)
│   └── section.content-grid               │   ├── .metric-top
│       ├── .provider-panel                │   ├── .metric-icons
│       └── .result-panel                  │   ├── .metric-center (大数字)
├── modals (保持不变)                      │   ├── .metric-action
├── .notif-dropdown                       │   └── .metric-strip (底部指标条)
└── .toast                                └── .bottom-grid
                                              ├── .provider-list-card
                                              │   ├── .plc-header
                                              │   └── .plc-table-wrap
                                              └── .detail-card
                                                  ├── .dc-tabs
                                                  ├── .dc-body
                                                  └── .dc-actions
```

### 3.2 新增 DOM 元素

**顶部导航栏 (`nav.topnav`)**:
- `.nav-brand`: 绿色品牌按钮，含图标 + "API Monitor" + 下拉箭头
- `.nav-btn` × 2: Features (功能面板) 和 Notices (通知中心)
- `.nav-search`: 搜索框，含搜索图标，保留 `#searchInput` ID
- `.nav-icon-btn`: 安全/设置图标按钮，含通知徽章 `#notifBadge`
- `.nav-dashboard`: 蓝色 Dashboard 按钮
- `.nav-avatar`: 用户头像圆形按钮

**中央指标卡 (`.metric-card`)**:
- `.metric-top`: Connections 计数 + Settings/Editor 链接
- `.metric-icons`: 4 个状态指示图标（全部/在线/离线/星标）
- `.metric-center`: 核心指标标签 + 大数字（如平均延迟 286.47ms）
- `.metric-action`: 橙色 "Stats & More" 按钮
- `.metric-strip`: 5 项底部指标条（Providers / Avg Latency / Uptime / Success Rate / Failover）

**底部双卡布局 (`.bottom-grid`)**:
- `.provider-list-card`: 左侧提供商表格卡（含操作按钮组）
- `.detail-card`: 右侧详情卡（含 Tab 切换 + 操作按钮组）

### 3.3 移除 DOM 元素

- `.drag-strip` → 由导航栏整体承担拖拽
- `aside.sidebar` 整个侧边栏 → 包括 `.brand`、`nav.menu`、`.sidebar-footer`
- `header.header` → 包括 `.header-left`（问候语）、`.header-right`（操作按钮组）
- `section.stats-grid` → 4 个独立统计卡
- 独立的 `.header-actions` 按钮组 → 分散到导航栏和底部卡片的操作区

### 3.4 保持不变的 DOM 元素

- 所有 Modal (Provider / About / Confirm / Settings) — 仅样式更新，结构不变
- `.notif-dropdown` — 通知下拉框，调整定位
- `.toast` — 底部提示条
- `.window-controls` — 窗口控制按钮（位置微调）

### 3.5 ID 映射表

| 旧 ID | 新 ID / 新选择器 | 用途 |
|-------|-----------------|------|
| `#greeting` | 移除 | 问候语不再显示 |
| `#serviceDot` | `.nav-brand .status-dot` | 服务状态指示点 |
| `#serviceLabel` | `.nav-brand .status-text` | 服务状态文本 |
| `#serviceTime` | 移除或整合到指标卡 | 服务运行时间 |
| `#statTotal` | `#metricProviders` | 提供商总数（指标条） |
| `#statOk` | `#metricUptime` | 可用数 → 运行时间率 |
| `#statFail` | `#metricFailover` | 失败数 → 故障切换次数 |
| `#statWait` | 移除 | 未测试数（可从表格状态筛选） |
| `#providerCount` | `#plcCount` | 提供商计数 |
| `#searchInput` | `#searchInput`（不变） | 搜索框 |
| `#providerTableBody` | `#providerTableBody`（不变） | 表格 body |
| `#detailInfo` | `#detailInfo`（不变） | 详情内容区 |
| `#refreshBtn` | `.plc-action-btn[title="Refresh"]` | 刷新按钮 |
| `#addBtn` | `.plc-action-btn[title="Add"]` | 新增按钮 |
| `#fastTestBtn` / `#fullTestBtn` | 移至 `.dc-actions` 或导航 | 测试按钮 |
| `#launchBtn` | `.nav-dashboard` 或新位置 | 启动服务 |
| `#stopBtn` | 保留在测试进行中显示 | 停止按钮 |
| `#notifBtn` | `#notifBtn`（移到导航栏） | 通知按钮 |
| `#schedulerBox` | 整合到指标卡或导航 | 自动测试倒计时 |

---

## 四、CSS 样式变更 (`web/css/style.css`)

### 4.1 整体重写策略

当前 CSS 为 1571 行，按以下分区重写：

| 分区 | 旧版行数 | 新版操作 | 说明 |
|------|---------|---------|------|
| 设计令牌 (`:root`) | 1-30 | **替换** | 新色彩/间距/圆角/阴影系统 |
| 基础重置 | 31-50 | **保留** | 基本 box-sizing / font 不变 |
| 布局 Shell (`.app`) | 51-70 | **重写** | 去掉 sidebar flex，改为 column 布局 |
| 拖拽区域 | 61-89 | **重写** | 导航栏拖拽 + 排除规则 |
| 窗口控制 | 91-122 | **微调** | 位置不变，样式微调融入导航栏 |
| 侧边栏 | 124-287 | **删除** | 整个侧边栏样式不再需要 |
| 主内容区 | 289-302 | **重写** | 新 content 区样式 |
| Header | 304-326 | **删除** | 旧 Header 不再需要 |
| 按钮系统 | 330-391 | **重写** | 柔和按钮样式 |
| Stats Grid | 393-450 | **删除** | 旧统计卡不再需要 |
| Content Grid | 452-460 | **重写** | 新的 bottom-grid 布局 |
| 面板/表格 | 462-648 | **重写** | 柔和面板 + 圆角行表格 |
| 详情面板 | 651-737 | **重写** | 柔和 Tab + 新详情布局 |
| 滚动条 | 822-831 | **微调** | 保持窄滚动条 |
| 响应式 | 833-846 | **重写** | 新断点系统 |
| Modal | 848-949 | **微调** | 圆角/阴影更新，结构不变 |
| 表单 | 951-1074 | **微调** | 柔和输入框样式 |
| Toast | 1077-1103 | **微调** | 柔和阴影 |
| 通知 | 1132-1255 | **微调** | 定位调整 |
| 设置 | 1257-1365 | **微调** | 柔和样式 |
| 趋势图 | 1367-1447 | **重写** | Canvas 容器新样式 |
| 其他 | 1449-1571 | **微调** | Failover banner/批量/更新等 |

### 4.2 关键新样式模块

#### 4.2.1 毛玻璃导航栏

```css
.topnav {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 24px;
    background: rgba(255, 255, 255, 0.7);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid rgba(0, 0, 0, 0.06);
    -webkit-app-region: drag;  /* 整个导航栏可拖拽 */
    flex-shrink: 0;
    z-index: 100;
}
/* 导航栏内交互元素排除拖拽 */
.topnav button,
.topnav input,
.topnav .nav-search,
.topnav .nav-brand,
.topnav .nav-btn,
.topnav .nav-icon-btn,
.topnav .nav-dashboard,
.topnav .nav-avatar {
    -webkit-app-region: no-drag;
}
```

#### 4.2.2 渐变指标卡

```css
.metric-card {
    background: linear-gradient(135deg, #e8f4fd 0%, #d4f1e8 40%, #e2f0d9 100%);
    border-radius: var(--radius-xl);
    padding: 28px 32px;
    position: relative;
    overflow: hidden;
    flex-shrink: 0;
}
/* 装饰性径向渐变 */
.metric-card::before {
    content: '';
    position: absolute;
    top: -60px; right: -40px;
    width: 200px; height: 200px;
    background: radial-gradient(circle, rgba(99,179,237,0.12) 0%, transparent 70%);
    border-radius: 50%;
}
```

#### 4.2.3 圆角表格行

```css
.provider-table tbody tr {
    background: var(--bg-subtle);
    border-radius: var(--radius-sm);
}
.provider-table tbody tr:hover { background: var(--bg-hover); }
.provider-table tbody tr.selected { background: var(--bg-selected); }
.provider-table td:first-child { border-radius: 8px 0 0 8px; }
.provider-table td:last-child { border-radius: 0 8px 8px 0; }
```

#### 4.2.4 柔和状态标签

```css
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 700;
    border: none;  /* 去掉旧版硬边框 */
}
.status-pill.ok { background: var(--accent-green-light); color: var(--accent-green); }
.status-pill.fail { background: var(--accent-red-light); color: var(--accent-red); }
```

### 4.3 响应式断点调整

```css
/* 旧版断点 */
@media (max-width: 1200px) { /* stats 2列, content 1列 */ }
@media (max-width: 820px)  { /* 隐藏 sidebar */ }

/* 新版断点 */
@media (max-width: 1100px) {
    .bottom-grid { grid-template-columns: 1fr; }  /* 上下堆叠 */
}
@media (max-width: 768px) {
    .topnav { padding: 8px 12px; gap: 6px; }
    .nav-btn { display: none; }  /* 隐藏次要导航按钮 */
    .metric-card { padding: 18px 20px; }
    .metric-value { font-size: 42px; }
    .metric-action { display: none; }
}
```

---

## 五、JavaScript 变更 (`web/js/app.js`)

### 5.1 需要修改的方法

| 方法 | 行号 | 改动说明 |
|------|------|---------|
| `bindEvents()` | 76 | 更新事件绑定：移除 sidebar 菜单逻辑，新增导航栏按钮事件；将 header 操作按钮的事件迁移到新位置 |
| `startMenuLogic()` | 284 | **移除或重构**：旧版侧边栏菜单逻辑不再需要；若保留 Features/Notices 按钮的交互则适配为导航栏下拉 |
| `updateGreeting()` | 364 | **移除**：新版无问候语元素 |
| `renderStats(stats)` | 442 | **重写**：更新指标卡内的统计数字（Connections/Providers/Latency/Uptime/Success/Failover） |
| `animateNumber()` | 449 | **微调**：元素 ID 映射更新 |
| `renderProviders()` | 510 | **中改**：更新表格行 HTML 模板（新 class 名、新状态标签样式、圆角行） |
| `updateSingleProvider()` | 552 | **中改**：匹配新表格结构 |
| `updateStatsIncremental()` | 584 | **重写**：更新指标条而非 4 个 stat card |
| `renderProviderDetail()` | 666 | **中改**：更新详情面板 HTML 模板（新 section 样式、新 class 名） |
| `renderCliTab()` | 325 | **微调**：CLI 日志容器样式更新 |
| `renderTrendTab()` | 1346 | **中改**：趋势图容器样式更新 |
| `_renderTrendStats()` | 1387 | **微调**：趋势统计卡片样式 |
| `_drawTrendChart()` | 1414 | **微调**：Canvas 配色更新（线条颜色/网格颜色适配新主题） |
| `renderNotifications()` | 1220 | **微调**：通知下拉样式 |
| `updateSchedulerStatus()` | 1518 | **中改**：调度器状态显示位置变更 |
| `setTestingState()` | 745 | **微调**：测试状态按钮位置变更 |
| `onMenuSelect()` | 294 | **重构**：菜单选择改为导航栏按钮的对应行为 |

### 5.2 需要新增的方法

| 方法 | 用途 |
|------|------|
| `renderMetricCard(stats)` | 渲染中央指标大卡（Connections/Latency/Uptime/Success/Failover 计算与展示） |
| `renderMetricStrip(stats)` | 渲染底部指标条 |
| `updateMetricCard()` | 增量更新指标卡数据（测试完成后） |

### 5.3 可移除的方法

| 方法 | 原因 |
|------|------|
| `updateGreeting()` | 新版无问候语 UI |
| `startMenuLogic()` | 新版无侧边栏菜单 |
| `startClock()` | 服务时间不再在侧边栏显示（可整合到指标卡或移除） |

### 5.4 事件绑定变更详表

```javascript
// 旧版绑定 → 新版绑定

// 移除
'click #fastTestBtn'      → 迁移到 detail-card 操作区或导航栏
'click #fullTestBtn'      → 同上
'click #importBtn'        → 迁移到 provider-list-card 操作区
'click #addBtn'           → 迁移到 provider-list-card 操作区
'click #launchBtn'        → 迁移到导航栏 Dashboard 按钮
'menu-item click'         → 不再需要

// 新增
'click .nav-brand'        → 打开品牌下拉/About
'click .nav-btn[Features]'→ 打开功能面板（或导入/导出）
'click .nav-btn[Notices]' → 切换通知下拉框
'click .nav-dashboard'    → 启动 Claude 或打开仪表盘视图
'click .nav-avatar'       → 打开设置或用户菜单
'click .metric-settings'  → 打开设置 Modal
'click .plc-action-btn'   → 刷新/新增/批量测试
'click .dc-btn'           → 测试/编辑/设为当前/删除
```

### 5.5 Canvas 趋势图配色更新

```javascript
// _drawTrendChart() 中的颜色需要更新
// 旧版 Neo-Brutalist 配色
gridColor: '#e8e4dc'      →  '#edf2f7'
lineColor: '#1a1a1a'      →  '#4299e1'
fillColor: 'rgba(250,204,21,0.15)' → 'rgba(66,153,225,0.08)'
dotColor: '#1a1a1a'       →  '#4299e1'
avgLineColor: '#ec4899'    →  '#ed8936'
textColor: '#888'         →  '#a0aec0'
```

---

## 六、Python 后端变更 (`main.py`)

### 6.1 窗口参数调整

```python
# 旧版
webview.create_window(
    'API Monitor',
    url,
    js_api=api,
    width=1100,
    height=720,
    min_size=(800, 520),
    frameless=True,
    ...
)

# 新版建议
webview.create_window(
    'API Monitor',
    url,
    js_api=api,
    width=1200,          # 加宽以适应全宽布局
    height=780,          # 加高
    min_size=(900, 580), # 最小尺寸调整
    frameless=True,      # 保持无边框
    ...
)
```

### 6.2 无需改动的模块

以下 `core/` 模块不涉及 UI，完全不需要修改：

- `db.py` — 数据库操作
- `testing.py` — 提供商测试
- `providers.py` — 提供商管理
- `failover.py` — 故障切换
- `notifications.py` — 通知系统
- `scheduler.py` — 调度器
- `export.py` — 数据导出
- `crypto.py` — 加密
- `validators.py` — 验证
- `logging_config.py` — 日志
- `updater.py` — 更新检查

---

## 七、实施阶段

### Phase 1: 结构重组（HTML）
**预计工作量**: 约 2 小时

1. 创建新 `index.html` 骨架（导航栏 + 指标卡 + 底部双卡）
2. 迁移并保留所有 Modal 结构
3. 更新所有 ID 和 class 映射
4. 确保 PyWebView drag/no-drag 区域正确设置
5. 验证所有 JS 事件绑定目标 ID 存在

### Phase 2: 设计令牌与核心样式（CSS）
**预计工作量**: 约 3 小时

1. 重写 `:root` 设计令牌
2. 实现新布局 Shell（去掉 sidebar，全宽 column）
3. 实现毛玻璃导航栏样式
4. 实现渐变指标卡样式
5. 实现圆角表格和新状态标签
6. 实现新详情面板样式
7. 更新 Modal/Toast/通知 样式
8. 更新趋势图 Canvas 容器样式

### Phase 3: 逻辑适配（JavaScript）
**预计工作量**: 约 2 小时

1. 更新 `bindEvents()` 事件绑定
2. 重写 `renderStats()` → `renderMetricCard()`
3. 更新 `renderProviders()` 表格行模板
4. 更新 `renderProviderDetail()` 详情模板
5. 移除/重构 `startMenuLogic()` 和 `updateGreeting()`
6. 更新 Canvas 趋势图配色
7. 测试 MockBackend 在新 UI 下的完整流程

### Phase 4: 集成与构建
**预计工作量**: 约 1 小时

1. 调整 `main.py` 窗口参数
2. 完整功能测试（PyWebView 环境）
3. 构建新 exe: `pyinstaller --noconfirm --clean CC-Switch-Monitor.spec`
4. 移动 `dist/API-Monitor.exe` 到项目根目录
5. 最终 UI 走查与像素级比对

---

## 八、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| PyWebView 拖拽区域在新布局下失效 | 窗口无法拖动 | 导航栏设 drag，所有交互子元素设 no-drag |
| 旧 JS 事件绑定 ID 在新 HTML 中不存在 | 功能按钮无响应 | Phase 3 逐步映射，逐个验证 |
| 趋势图 Canvas 在新容器尺寸下绘制异常 | 图表显示错误 | 保持 Canvas 自适应逻辑，仅改配色 |
| 小屏幕下布局挤压 | 体验下降 | 新断点系统 1100px/768px 两档适配 |
| 毛玻璃效果在旧版 Windows 不支持 | 导航栏无透明效果 | fallback: `background: rgba(255,255,255,0.95)` |

---

## 九、验收标准

1. 应用启动后显示新的 Modern Dashboard 布局
2. 顶部导航栏所有按钮可点击且有正确响应
3. 中央指标卡正确显示 Connections / Latency / Uptime / Success Rate / Failover
4. 提供商表格正确渲染，支持搜索、排序、选中
5. 详情面板 4 个 Tab（基本信息/测试结果/趋势/CLI）均正常工作
6. 新增/编辑/删除提供商 Modal 正常弹出和保存
7. 快速测试和完整测试流程正常执行
8. 通知系统、设置面板、关于弹窗正常工作
9. 窗口拖拽、最小化、最大化、关闭正常
10. 趋势图 Canvas 正确绘制，配色适配新主题
11. `pyinstaller` 构建成功，exe 可正常启动
12. 窗口缩放到 900×580 最小尺寸时布局不崩溃
