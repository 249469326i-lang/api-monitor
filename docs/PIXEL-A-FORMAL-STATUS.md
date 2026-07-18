# API Monitor · Direction A 正式版状态

> 更新：2026-07-18  
> 结论：像素风 Arcade/Cyber UI 已作为 `web/` 正式前端启用。

## 1. 正式文件

| 路径 | 角色 |
|------|------|
| `web/index.html` | 正式结构（含 metric-scene / SCENE BG 设置） |
| `web/css/style.css` | 正式像素样式 |
| `web/js/app.js` | 正式交互（功能钩子保留） |
| `web/assets/metric-bg/*.gif` | 默认赛博场景循环图 |
| `web/css/style.css.bak-before-pixel-a-*` | 替换前旧样式备份 |

## 2. Code Review 结论（2026-07-18）

### 通过
- `node --check web/js/app.js` 语法通过
- 关键 id / class 钩子与 app.js 对齐（列表、测试、设置、failover、toast）
- 无正 `border-radius`、无模糊阴影（像素硬规则）
- `CC-Switch-Monitor.spec` 打包整个 `web/` 目录，assets 会一并带上
- 场景背景路径改为相对路径，兼容 pywebview 本地加载与 http.server
- `applySceneBackgrounds()` 直接写 `.metric-scene` / `.empty-stage` 样式，避免 CSS 变量失效

### 已修问题
- GIF 背景被 `background:#fff` 简写与过厚白遮罩盖住 → 独立 scene 层
- `/assets/...` 绝对路径在 file/pywebview 下失效 → 相对路径 + URL resolve
- 状态栏/设置文案与像素风不统一 → HUD 标签化

### 已知非阻塞
- 详情页部分文案仍有中文长句（趋势/可用性），不影响功能
- 浏览器预览走 MockBackend，真实测试需 exe / `python main.py`
- `latClass` 仍在 ping cell 使用（正常，非死代码）

## 3. 回归清单（建议人工点一次）

1. 顶部 4 指标卡 GIF 可见、数字可读
2. 未选节点时右侧详情空态有 detail 场景
3. 快测 / 停止 切换不抖布局，扫描条出现
4. 中英无关；主题无切换；设置打开/保存
5. 设置 → 07 SCENE BG → PREVIEW / RESET / SAVE
6. 提供商增删改、导入、failover 横幅、toast

## 4. 回滚

恢复旧样式：

```bat
copy /Y "web\css\style.css.bak-before-pixel-a-20260718111437" "web\css\style.css"
```

（HTML/JS 若也需回滚，请从版本管理或更早备份恢复。）
