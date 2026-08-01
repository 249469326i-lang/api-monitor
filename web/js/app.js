/**
 * API Monitor - Frontend App
 * Direction A · Arcade Cabinet — topnav + hero metrics + provider list + detail
 */

const API_FORMAT_LABELS = {
    '': '自动检测',
    anthropic_messages: 'Anthropic Messages',
    openai_chat: 'OpenAI Chat Completions',
    openai_responses: 'OpenAI Responses API',
    gemini_native: 'Gemini generateContent',
};

class App {
    constructor() {
        this.providers = [];
        this.filteredProviders = [];
        this.selectedProviderId = null;
        this.activeDetailTab = 'test';
        this.isTesting = false;
        this.cliLogs = [];          // [{time, name, level, text}]
        this.selectedIds = new Set();
        this._lastClickedIndex = null;
        this._selectionMode = false;
        this.api = window.pywebview?.api || null;
        this.activeApp = 'claude';   // 当前分页: claude | codex
        this.currentModes = { claude: { mode: 'official', provider_name: null }, codex: { mode: 'official', provider_name: null } };

        this.init();
    }

    async init() {
        this.bindEvents();

        // pywebview 注入 api 是异步的: DOMContentLoaded 时通常还没就绪
        // 监听 ready 事件延后绑定并重新加载数据
        window.addEventListener('pywebviewready', () => {
            const newApi = window.pywebview?.api || null;
            if (newApi && typeof newApi.get_stats === 'function') {
                this.api = newApi;
                this.pushLog('info', 'PyWebView 后端已连接');
                // 延迟加载数据,避免 pywebview 主线程死锁
                setTimeout(() => this.loadData(), 50);
            }
        });

        // 兜底: 延迟检测 pywebview API 是否已就绪
        // Windows 上 pywebviewready 事件可能延迟或不触发
        const tryConnect = () => {
            const api = window.pywebview?.api;
            if (api && typeof api.get_stats === 'function') {
                this.api = api;
                this.loadData();
            }
        };
        setTimeout(tryConnect, 200);
        setTimeout(tryConnect, 800);

        // 首次加载:若 pywebview 已就绪则直接用真实后端;
        // 否则等待 1.5s（桌面版 api 注入是异步的），超时仍未就绪才
        // 回退 MockBackend（浏览器预览场景）。
        // 避免桌面版启动时闪现 Mock 假数据又被真实数据覆盖。
        if (this.api) {
            this.loadData();
        } else {
            setTimeout(() => {
                if (!this.api) this.loadData();
            }, 1500);
        }
        setTimeout(() => this.initRuntimeStatus(), 1200);

        // 启动时静默检查更新（延迟 3 秒，不阻塞主流程）
        setTimeout(() => this._checkForUpdate(), 3000);

        // 兜底轮询调度器状态和未读数：后端有主动推送
        // (_push_scheduler_tick/_push_unread_count)，这里 30s 低频兜底即可，
        // 且窗口隐藏（托盘）时跳过，不白耗桥接调用
        setInterval(() => {
            if (document.hidden) return;
            this._refreshSchedulerStatus();
            this._refreshUnreadCount();
        }, 30000);
        // 从托盘恢复显示时立即刷新一次
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                this._refreshSchedulerStatus();
                this._refreshUnreadCount();
            }
        });
    }

    async _refreshUnreadCount() {
        try {
            const r = await this.backend().get_unread_count();
            if (r?.success) this.updateUnreadCount(r.count);
        } catch {}
    }

    /* --------------------- Backend abstraction ------------------------ */
    // 优先使用 PyWebView 后端; 不可用时使用浏览器内置 MockBackend (localStorage 持久化)
    backend() {
        return this.api || this._mock || (this._mock = new MockBackend());
    }

    /* --------------------- Window tray transition -------------------- */
    /** 由后端 evaluate_js 调用；也可本地预播。kind: hide|show|reset */
    playWindowAnim(kind) {
        const root = document.documentElement;
        const body = document.body;
        root.classList.remove('window-anim-hide', 'window-anim-show');
        body.classList.remove('window-anim-hide', 'window-anim-show');
        if (kind === 'hide') {
            root.classList.add('window-anim-hide');
            body.classList.add('window-anim-hide');
        } else if (kind === 'show') {
            root.classList.add('window-anim-show');
            body.classList.add('window-anim-show');
        }
    }

    /* ----------------------------- Events ----------------------------- */
    bindEvents() {
        // 点击空白处关闭启动/同步下拉菜单
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.nav-menu') && !e.target.closest('.tb-menu')) {
                document.querySelectorAll('.menu-dropdown.show').forEach(dd => dd.classList.remove('show'));
            }
        });

        // Window controls (frameless mode)
        document.getElementById('winMinBtn')?.addEventListener('click', () => {
            if (this.api?.minimize_window) this.api.minimize_window();
        });
        document.getElementById('winMaxBtn')?.addEventListener('click', () => {
            if (this.api?.maximize_window) this.api.maximize_window();
        });
        document.getElementById('winCloseBtn')?.addEventListener('click', async () => {
            try {
                // 先本地预播，再交给后端做整窗缩向托盘（WebView2 下 Opacity 无效）
                this.playWindowAnim('hide');
                if (this.api?.hide_to_tray) await this.api.hide_to_tray();
                else if (this.api?.close_window) this.api.close_window();
            } catch (e) {
                try { this.api?.close_window?.(); } catch (_) {}
            }
        });

        // 无边框窗口拖拽：
        // 主路径: 空白区 / 品牌区挂 .pywebview-drag-region，由 pywebview 内置
        // customize.js 在 JS 侧跟踪 mousemove 并调用 window.move（同步可靠）。
        // 注意: 经 pywebview JS API 异步调用 Win32 ReleaseCapture 会因桥接延迟
        // 失效（鼠标往往已抬起），因此不再作为主路径。
        // 兜底: 若内置 drag region 未生效，空白处用 move_window_by 跟手移动。
        const topnav = document.querySelector('.topnav');
        if (topnav) {
            const notBlank = 'button, input, a, .nav-search, .nav-avatar, .window-controls, .win-btn';
            const isInteractive = (el) => !!(el && el.closest && el.closest(notBlank));
            const isDragRegion = (el) => !!(el && el.closest && el.closest('.pywebview-drag-region'));

            topnav.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return;
                if (isInteractive(e.target)) return;

                // 已命中官方 drag region 时交给 pywebview，避免双通道抢鼠标
                if (isDragRegion(e.target) && window.pywebview) return;

                const startX = e.screenX;
                const startY = e.screenY;
                let dragging = true;
                let moved = false;

                const onMove = (ev) => {
                    if (!dragging) return;
                    const dx = ev.screenX - startX;
                    const dy = ev.screenY - startY;
                    if (!moved && Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
                    moved = true;
                    const moveApi = this.api || window.pywebview?.api;
                    if (moveApi?.move_window_by) {
                        moveApi.move_window_by(dx, dy, true);
                    }
                };
                const onUp = () => {
                    dragging = false;
                    window.removeEventListener('mousemove', onMove);
                    window.removeEventListener('mouseup', onUp);
                    const endApi = this.api || window.pywebview?.api;
                    try { endApi?.end_window_drag?.(); } catch (_) {}
                };
                window.addEventListener('mousemove', onMove);
                window.addEventListener('mouseup', onUp);
            });

            // 双击空白处切换最大化（与窗口标题栏行为一致）
            topnav.addEventListener('dblclick', (e) => {
                if (isInteractive(e.target)) return;
                const api = this.api || window.pywebview?.api;
                if (api?.maximize_window) api.maximize_window();
            });
        }

        // Settings avatar (topnav)
        document.getElementById('settingsAvatar')?.addEventListener('click', () => this.openSettings());

        // Features button (topnav)
        document.getElementById('featuresBtn')?.addEventListener('click', () => this.openAboutModal());

        // Metric card buttons
        document.getElementById('mcFullTestBtn')?.addEventListener('click', () => this.testAll('full'));
        document.getElementById('stopBtn')?.addEventListener('click', () => this.stopTesting());

        // Provider list card actions
        document.getElementById('fastTestBtn')?.addEventListener('click', () => this.testAll('fast'));
        document.getElementById('refreshBtn')?.addEventListener('click', () => this.loadData());
        document.getElementById('importBtn')?.addEventListener('click', (e) => this.toggleMenu('importDropdown', e));
        document.getElementById('addBtn')?.addEventListener('click', () => this.openAddModal());
        document.getElementById('launchBtn')?.addEventListener('click', (e) => e?.stopPropagation());
        document.getElementById('selectModeBtn')?.addEventListener('click', () => this.toggleSelectionMode());
        // 启动/同步 下拉菜单项
        document.querySelectorAll('#launchDropdown .menu-option').forEach(el => {
            el.addEventListener('click', () => {
                const t = el.dataset.target;
                if (t === 'codex-cli') this.launchCodex();
                else if (t === 'chatgpt-desktop') this.launchChatGPTDesktop();
                else this.launchClaude();
            });
        });
        document.querySelectorAll('#importDropdown .menu-option').forEach(el => {
            el.addEventListener('click', () => {
                const t = el.dataset.target;
                if (t === 'codex') this.importFromCodex();
                else this.importFromClaudeCode();
            });
        });

        // 应用分页：Claude Code / Codex
        document.querySelectorAll('#appTabs .app-tab').forEach(el => {
            el.addEventListener('click', () => this.switchAppTab(el.dataset.app));
        });
        // 每应用模式条：官方 / 第三方
        document.querySelectorAll('#modeBar .mode-btn').forEach(el => {
            el.addEventListener('click', () => this.switchMode(el.dataset.mode));
        });
        // 新增/编辑表单：应用勾选切换对应输入组
        ['fAppClaude', 'fAppCodex'].forEach(id => {
            document.getElementById(id)?.addEventListener('change', () => this._syncAppFields());
        });
        // 获取模型（每个应用一个按钮，data-app 定位）
        document.querySelectorAll('.btn-fetch-models').forEach(btn => {
            btn.addEventListener('click', () => this.fetchModels(btn.dataset.app));
        });

        // Detail card head actions
        document.getElementById('testOneBtn')?.addEventListener('click', () => this.testSelected());
        document.getElementById('setCurrentBtn')?.addEventListener('click', () => this.setCurrentProvider());
        document.getElementById('editBtn')?.addEventListener('click', () => this.openEditModal());
        document.getElementById('deleteBtn')?.addEventListener('click', () => this.deleteSelected());

        // Batch operations
        document.getElementById('selectAllCheck')?.addEventListener('change', (e) => this.toggleSelectAll(e));
        document.getElementById('batchTestBtn')?.addEventListener('click', () => this.batchTest());
        document.getElementById('batchDeleteBtn')?.addEventListener('click', () => this.batchDelete());
        document.getElementById('batchBackupBtn')?.addEventListener('click', () => this.batchSetBackup());
        document.getElementById('batchClearBtn')?.addEventListener('click', () => this.clearSelection());

        // Search（120ms 防抖：每击键全表重建，节点多时会卡顿）
        document.getElementById('searchInput')?.addEventListener('input', (e) => {
            clearTimeout(this._searchDebounce);
            this._searchDebounce = setTimeout(() => this.filterProviders(e.target.value), 120);
        });

        // 表单 API Key 显示/隐藏切换
        document.getElementById('fApiKeyToggle')?.addEventListener('click', () => {
            const input = document.getElementById('fApiKey');
            if (input) input.type = input.type === 'password' ? 'text' : 'password';
        });

        // Detail card tabs (was .result-tab)
        document.querySelectorAll('.dc-tab[data-tab]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.switchDetailTab(e.currentTarget.dataset.tab);
            });
        });

        // Modal
        document.getElementById('modalCloseBtn')?.addEventListener('click', () => this.closeProviderModal());
        document.getElementById('modalCancelBtn')?.addEventListener('click', () => this.closeProviderModal());
        document.getElementById('modalSaveBtn')?.addEventListener('click', () => this.saveProviderFromModal());
        document.getElementById('providerModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'providerModal') this.closeProviderModal();
        });
        document.getElementById('aboutCloseBtn')?.addEventListener('click', () => this.closeAboutModal());
        document.getElementById('aboutModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'aboutModal') this.closeAboutModal();
        });

        // Confirm modal
        document.getElementById('confirmCancelBtn')?.addEventListener('click', () => this._closeConfirm(false));
        document.getElementById('confirmOkBtn')?.addEventListener('click', () => this._closeConfirm(true));
        document.getElementById('confirmModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'confirmModal') this._closeConfirm(false);
        });

        // Empty state hint links + arcade CTAs
        document.getElementById('emptyAddLink')?.addEventListener('click', (e) => {
            e.preventDefault();
            this.openAddModal();
        });
        document.getElementById('emptyImportLink')?.addEventListener('click', (e) => {
            e.preventDefault();
            this.importFromClaudeCode();
        });
        document.getElementById('emptyImportCodexLink')?.addEventListener('click', (e) => {
            e.preventDefault();
            this.importFromCodex();
        });
        document.getElementById('emptyAddBtn')?.addEventListener('click', () => this.openAddModal());
        document.getElementById('emptyImportBtn')?.addEventListener('click', () => this.importFromClaudeCode());
        document.getElementById('emptyImportCodexBtn')?.addEventListener('click', () => this.importFromCodex());
        document.getElementById('emptyTestBtn')?.addEventListener('click', () => this.testAll('fast'));
        document.getElementById('bgResetBtn')?.addEventListener('click', () => this.resetSceneBackgrounds());
        document.getElementById('bgPreviewBtn')?.addEventListener('click', () => {
            this.applySceneBackgrounds(this._readSceneBackgroundInputs());
            this.toast('SCENE PREVIEW', 'success');
        });
        // Scene backgrounds (defaults + local custom URLs)
        this.applySceneBackgrounds(this.loadSceneBackgrounds());
        // 桌面版 private_mode 下 localStorage 可能不持久：
        // 后端就绪后用 bg_* 设置覆盖（后端是持久可信来源）
        window.addEventListener('pywebviewready', () => {
            setTimeout(() => this._applyBackendSceneBackgrounds(), 300);
        });

        // Notification bell
        document.getElementById('notifBtn')?.addEventListener('click', () => this.toggleNotifDropdown());
        document.getElementById('markAllReadBtn')?.addEventListener('click', () => this.markAllRead());

        // Settings modal
        document.getElementById('settingsCloseBtn')?.addEventListener('click', () => this.closeSettings());
        document.getElementById('settingsCancelBtn')?.addEventListener('click', () => this.closeSettings());
        document.getElementById('settingsSaveBtn')?.addEventListener('click', () => this.saveSettings());
        document.getElementById('settingsModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'settingsModal') this.closeSettings();
        });

        // Export & Backup
        document.getElementById('exportCsvBtn')?.addEventListener('click', () => this.exportProviders('csv'));
        document.getElementById('exportJsonBtn')?.addEventListener('click', () => this.exportProviders('json'));
        document.getElementById('exportHistoryBtn')?.addEventListener('click', () => this.exportHistoryCsv());
        document.getElementById('backupBtn')?.addEventListener('click', () => this.backupDb());
        document.getElementById('restoreBtn')?.addEventListener('click', () => this.restoreDb());
        document.getElementById('backupNowBtn')?.addEventListener('click', () => this.backupDb());
        document.getElementById('failoverConfirmBtn')?.addEventListener('click', () => this.confirmFailover());
        document.getElementById('failoverCancelBtn')?.addEventListener('click', () => this.cancelFailover());

        // Close notif dropdown when clicking outside
        document.addEventListener('click', (e) => {
            const dd = document.getElementById('notifDropdown');
            const btn = document.getElementById('notifBtn');
            if (dd && dd.style.display !== 'none' && !dd.contains(e.target) && !btn.contains(e.target)) {
                dd.style.display = 'none';
            }
        });

        // Custom selects
        this._initCustomSelects();
        this._enableTextSelectionZones();

        // ── Keyboard shortcuts ──
        document.addEventListener('keydown', (e) => this._handleKeydown(e));
    }

    /* ------------------------- Custom Select Logic ------------------------- */
    _initCustomSelects() {
        document.querySelectorAll('.custom-select').forEach(sel => {
            const display = sel.querySelector('.custom-select-display');
            const dropdown = sel.querySelector('.custom-select-dropdown');
            const options = Array.from(dropdown.querySelectorAll('.custom-select-option'));

            // 键盘可达性：div 结构补 tabindex/role/键盘操作
            display.setAttribute('tabindex', '0');
            display.setAttribute('role', 'combobox');
            display.setAttribute('aria-haspopup', 'listbox');
            display.setAttribute('aria-expanded', 'false');
            dropdown.setAttribute('role', 'listbox');
            options.forEach(o => o.setAttribute('role', 'option'));

            const setOpen = (open) => {
                sel.classList.toggle('open', open);
                display.setAttribute('aria-expanded', open ? 'true' : 'false');
            };

            // Toggle dropdown on click
            display.addEventListener('click', (e) => {
                e.stopPropagation();
                // Close all other custom selects first
                document.querySelectorAll('.custom-select.open').forEach(other => {
                    if (other !== sel) other.classList.remove('open');
                });
                setOpen(!sel.classList.contains('open'));
            });

            // 键盘操作：Enter/Space 开合，↑↓ 移动选择，Esc 关闭
            display.addEventListener('keydown', (e) => {
                const isOpen = sel.classList.contains('open');
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setOpen(!isOpen);
                } else if (e.key === 'Escape' && isOpen) {
                    e.preventDefault();
                    setOpen(false);
                } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    const cur = options.findIndex(o => o.dataset.value === sel.dataset.value);
                    const dir = e.key === 'ArrowDown' ? 1 : -1;
                    const next = Math.min(options.length - 1, Math.max(0, (cur < 0 ? 0 : cur + dir)));
                    const opt = options[next];
                    if (opt) this._setCustomSelectValue(sel.id, opt.dataset.value);
                }
            });

            // Select option
            options.forEach(opt => {
                opt.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const val = opt.dataset.value;
                    this._setCustomSelectValue(sel.id, val);
                    setOpen(false);
                });
            });
        });

        // Close custom selects when clicking outside
        document.addEventListener('click', () => {
            document.querySelectorAll('.custom-select.open').forEach(s => {
                s.classList.remove('open');
                s.querySelector('.custom-select-display')?.setAttribute('aria-expanded', 'false');
            });
        });
    }

    _setCustomSelectValue(id, value) {
        const sel = document.getElementById(id);
        if (!sel) return;
        sel.dataset.value = value;
        const textEl = sel.querySelector('.custom-select-text');
        let selected = null;
        sel.querySelectorAll('.custom-select-option').forEach(opt => {
            if (opt.dataset.value === value) selected = opt;
        });
        if (textEl) textEl.textContent = selected ? selected.textContent : value;
        // Update selected state on options
        sel.querySelectorAll('.custom-select-option').forEach(opt => {
            opt.classList.toggle('selected', opt.dataset.value === value);
        });
    }

    _getCustomSelectValue(id) {
        const sel = document.getElementById(id);
        return sel ? (sel.dataset.value || '') : '';
    }

    _enableTextSelectionZones() {
        const selectors = [
            '#detailInfo', '#detailInfo *',
            '#dcProviderHeader', '#dcProviderHeader *',
            '#providerTableBody td', '#providerTableBody td *',
            '#notifDropdown', '#notifDropdown *',
            '#aboutBody', '#aboutBody *',
            '.backup-list', '.backup-list *',
        ].join(',');
        document.addEventListener('mousedown', (e) => {
            if (e.target.closest(selectors)) e.stopPropagation();
        }, true);
        document.addEventListener('selectstart', (e) => {
            if (e.target.closest(selectors)) e.stopPropagation();
        }, true);
    }

    _apiFormatLabel(value, fallback = '-') {
        return API_FORMAT_LABELS[value || ''] || value || fallback;
    }

    /* ------------------------- Keyboard shortcuts ------------------------- */
    _isModalOpen(id) {
        const el = document.getElementById(id);
        return el && el.classList.contains('show');
    }

    _anyModalOpen() {
        return this._isModalOpen('confirmModal') || this._isModalOpen('providerModal')
            || this._isModalOpen('settingsModal') || this._isModalOpen('aboutModal');
    }

    _isInputFocused() {
        const tag = document.activeElement?.tagName;
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    }

    _handleKeydown(e) {
        // ── Esc: close topmost modal / dropdown ──
        if (e.key === 'Escape') {
            if (this._isModalOpen('confirmModal')) { this._closeConfirm(false); e.preventDefault(); return; }
            if (this._isModalOpen('providerModal')) { this.closeProviderModal(); e.preventDefault(); return; }
            if (this._isModalOpen('settingsModal')) { this.closeSettings(); e.preventDefault(); return; }
            if (this._isModalOpen('aboutModal')) { this.closeAboutModal(); e.preventDefault(); return; }
            const dd = document.getElementById('notifDropdown');
            if (dd && dd.style.display !== 'none') { dd.style.display = 'none'; e.preventDefault(); return; }
        }

        // ── Enter: confirm confirm modal / save provider modal ──
        if (e.key === 'Enter' && !this._isInputFocused()) {
            if (this._isModalOpen('confirmModal')) { this._closeConfirm(true); e.preventDefault(); return; }
            if (this._isModalOpen('providerModal')) { this.saveProviderFromModal(); e.preventDefault(); return; }
        }

        // ── Focus trap inside modals (Tab) ──
        if (e.key === 'Tab' && this._anyModalOpen()) {
            const modalId = ['confirmModal','providerModal','settingsModal','aboutModal'].find(id => this._isModalOpen(id));
            if (modalId) {
                const modal = document.getElementById(modalId);
                const focusable = modal.querySelectorAll('input,select,textarea,button,[tabindex]:not([tabindex="-1"])');
                if (focusable.length === 0) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (e.shiftKey) {
                    if (document.activeElement === first) { e.preventDefault(); last.focus(); }
                } else {
                    if (document.activeElement === last) { e.preventDefault(); first.focus(); }
                }
            }
            return;
        }

        // Skip remaining shortcuts if input is focused or modal is open
        if (this._isInputFocused() || this._anyModalOpen()) return;

        // ── Arrow Up/Down: navigate table rows ──
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            const rows = Array.from(document.querySelectorAll('#providerTableBody tr'));
            if (rows.length === 0) return;
            e.preventDefault();
            let idx = rows.findIndex(r => r.dataset.id === String(this.selectedProviderId));
            if (e.key === 'ArrowDown') idx = idx < rows.length - 1 ? idx + 1 : 0;
            else idx = idx > 0 ? idx - 1 : rows.length - 1;
            const pid = rows[idx].dataset.id;
            this.selectProvider(pid);
            rows[idx].scrollIntoView({ block: 'nearest' });
            return;
        }

        // ── Ctrl+F: focus search ──
        if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
            e.preventDefault();
            const input = document.getElementById('searchInput');
            if (input) { input.focus(); input.select(); }
            return;
        }

        // ── Ctrl+Shift+T: full test ──
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
            e.preventDefault();
            this.testAll('full');
            return;
        }

        // ── Ctrl+T: quick test ──
        if ((e.ctrlKey || e.metaKey) && e.key === 't') {
            e.preventDefault();
            this.testAll('fast');
            return;
        }

        // ── Delete: delete selected provider ──
        if (e.key === 'Delete' && this.selectedProviderId) {
            e.preventDefault();
            this.deleteSelected();
            return;
        }

        // ── , : open settings ──
        if (e.key === ',' || e.key === '，') {
            e.preventDefault();
            this.openSettings();
            return;
        }

        // ── R: refresh ──
        if (e.key === 'r' || e.key === 'R') {
            e.preventDefault();
            this.loadData(false);
            this.toast('已刷新', 'success');
            return;
        }

        // ── T: quick test (no modifier) ──
        if (e.key === 't' || e.key === 'T') {
            e.preventDefault();
            this.testAll('fast');
            return;
        }
    }

    /* ------------------------------ Toast ------------------------------ */
    async initRuntimeStatus() {
        try {
            const st = await this.backend().get_scheduler_status();
            if (st && st.success) this.updateSchedulerStatus(st.data || {});
            else if (st) this.updateSchedulerStatus(st);
        } catch (_) {}
        try {
            const api = this.backend();
            if (api.get_failover_status) {
                const fo = await api.get_failover_status();
                if (fo) this.updateFailoverStatus(fo);
            }
        } catch (_) {}
    }

    toast(message, type = 'info') {
        const el = document.getElementById('toast');
        if (!el) return;
        el.textContent = message;
        el.className = `toast show ${type === 'success' ? 'success' : type === 'error' ? 'error' : ''}`;
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => { el.className = 'toast'; }, 2400);
    }

    /* ------------------------------ Copy ------------------------------ */
    // 按需向后端获取单个供应商的明文 Key（不缓存、不落 DOM）
    async _fetchProviderKey(id) {
        try {
            const r = await this.backend().get_provider_key(id);
            if (r?.success) return r.api_key || '';
            this.toast(r?.error || '获取 Key 失败', 'error');
        } catch (e) {
            this.toast('获取 Key 失败', 'error');
        }
        return '';
    }

    async copyToClipboard(text, label) {
        if (!text || text === '-') return;
        try {
            await navigator.clipboard.writeText(text);
            this.toast(`${label || '内容'}已复制`, 'success');
        } catch {
            // fallback
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            this.toast(`${label || '内容'}已复制`, 'success');
        }
    }

    /* --------------------------- CLI log ------------------------------- */
    pushLog(level, text, name) {
        const ts = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        this.cliLogs.unshift({
            time: `${pad(ts.getHours())}:${pad(ts.getMinutes())}:${pad(ts.getSeconds())}`,
            level, text, name: name || '',
        });
        if (this.cliLogs.length > 200) this.cliLogs.length = 200;
        if (this.activeDetailTab === 'cli' && this.selectedProviderId != null) {
            this.renderCliTab();
        }
    }

    renderCliTab() {
        const detailInfo = document.getElementById('detailInfo');
        if (!detailInfo) return;
        const filtered = this.selectedProviderId
            ? this.cliLogs.filter(l => !l.name || l.name === this._selectedName())
            : this.cliLogs;
        if (!filtered.length) {
            detailInfo.innerHTML = `
                <div class="cli-empty">
                    <div class="cli-empty-icon">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
                        </svg>
                    </div>
                    <div class="cli-empty-title">暂无 CLI 日志</div>
                    <div class="cli-empty-desc">执行快速测试或完整测试后，请求/响应日志会显示在这里</div>
                </div>
            `;
            return;
        }
        const html = `<div class="cli-log">${filtered.map(l => {
            const cls = l.level === 'ok' ? 'ok' : l.level === 'err' ? 'err' : 'info';
            const tag = l.name ? `[${this.escape(l.name)}] ` : '';
            return `<span class="log-line ${cls}">${this.escape(l.time)} ${tag}${this.escape(l.text)}</span>`;
        }).join('')}</div>`;
        detailInfo.innerHTML = html;

        // Stop pywebview easy-drag so CLI text can be selected/copied.
        detailInfo.querySelector('.cli-log')?.addEventListener('mousedown', (e) => {
            e.stopPropagation();
        });
    }

    _selectedName() {
        const p = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        return p ? p.name : '';
    }

    /* ------------------------------ Data ------------------------------ */
    async loadData(animate = true) {
        try {
            const backend = this.backend();
            const stats = await backend.get_stats();
            const providers = await backend.get_providers();

            this.providers = providers;
            this.applyAppFilter();
            this.renderStats(stats, animate);
            this.renderProviders(this.filteredProviders, animate);
            this.toggleEmptyHint(providers.length === 0);
            await this._loadCurrentModes();
            this._loadDataFailed = false;
        } catch (error) {
            console.error('Failed to load data:', error);
            // 只提示一次，避免后端持续不可用时 toast 刷屏
            if (!this._loadDataFailed) {
                this._loadDataFailed = true;
                this.toast('后端连接失败，数据可能不是最新', 'error');
            }
        }
    }

    /* ========================= Auto Update ========================= */

    async _checkForUpdate() {
        try {
            const r = await this.backend().check_update();
            if (r?.success && r.data?.has_update) {
                this._showUpdateBanner(r.data);
            }
        } catch {}
    }

    _showUpdateBanner(data) {
        // 避免重复显示
        if (document.getElementById('updateBanner')) return;
        const banner = document.createElement('div');
        banner.id = 'updateBanner';
        banner.className = 'update-banner';
        banner.innerHTML = `
            <div class="update-banner-content">
                <span class="update-icon">↓</span>
                <div class="update-info">
                    <span class="update-text">新版本 v${this.escape(data.latest_version)} 可用</span>
                    <span class="update-changelog" title="${this.escape(data.changelog || '')}">${this.escape((data.changelog || '').split('\n')[0] || '查看更新内容')}</span>
                </div>
                <div class="update-actions">
                    <button class="btn btn-sm btn-primary" id="updateDownloadBtn">下载</button>
                    <button class="btn btn-sm" id="updateDismissBtn">忽略</button>
                </div>
            </div>
        `;
        // Insert before metric-card in content area (replaces old .main target)
        const content = document.querySelector('.content');
        if (content) content.insertBefore(banner, content.firstChild);

        document.getElementById('updateDownloadBtn')?.addEventListener('click', () => {
            // 只打开 GitHub 域名的下载链接，防止更新元数据被篡改后跳转任意 URL
            try {
                const u = new URL(data.download_url);
                if (u.protocol === 'https:' && (u.hostname === 'github.com' || u.hostname.endsWith('.github.com') || u.hostname === 'objects.githubusercontent.com')) {
                    window.open(data.download_url, '_blank');
                } else {
                    this.toast('下载链接域名异常，已阻止打开', 'error');
                }
            } catch { /* 无效 URL 直接忽略 */ }
        });
        document.getElementById('updateDismissBtn')?.addEventListener('click', () => {
            banner.remove();
        });
    }

    /* --------------------------- Stats render -------------------------- */
    renderStats(stats, animate = true) {
        const total = stats.total || 0;
        const ok = stats.ok || 0;
        const fail = stats.fail || 0;

        this.animateNumber('metricProviderCount', total, animate);
        this.animateNumber('mcTotal', total, animate);
        this.animateNumber('mcOk', ok, animate);
        this.animateNumber('mcFail', fail, animate);
        this._applyMetricVisuals(total, ok, fail);
    }

    _avgLatency() {
        let avgLatency = 0;
        let latencyCount = 0;
        for (const p of this.providers) {
            const num = this._latencyNum(p.latency);
            if (num < 999999) {
                avgLatency += num;
                latencyCount++;
            }
        }
        return latencyCount > 0 ? Math.round(avgLatency / latencyCount) : null;
    }

    _applyMetricVisuals(total, ok, fail) {
        const avg = this._avgLatency();
        const mcAvgLatency = document.getElementById('mcAvgLatency');
        if (mcAvgLatency) mcAvgLatency.textContent = avg != null ? `${avg}ms` : '--';
        const mcLatencyValue = document.getElementById('mcLatencyValue');
        if (mcLatencyValue) {
            mcLatencyValue.innerHTML = avg != null
                ? `${avg}<span class="mc-unit">ms</span>`
                : `--<span class="mc-unit">ms</span>`;
        }
        const avgChip = document.getElementById('mcAvgLatencyChip');
        if (avgChip) avgChip.textContent = avg != null ? `AVG ${avg}ms` : 'AVG --';

        const rateNum = total > 0 ? ((ok / total) * 100) : null;
        const rateText = rateNum == null ? '--' : (Number.isInteger(rateNum) ? String(rateNum) : rateNum.toFixed(1));
        const mcSuccessRate = document.getElementById('mcSuccessRate');
        if (mcSuccessRate) mcSuccessRate.innerHTML = rateText + '<span class="metric-unit">%</span>';

        const progressBar = document.getElementById('mcProgressBar');
        const progressWrap = document.getElementById('mcProgressWrap');
        if (progressBar) {
            progressBar.style.width = rateNum == null ? '0%' : `${rateNum}%`;
        }
        if (progressWrap) {
            progressWrap.classList.toggle('is-empty', rateNum == null);
            progressWrap.classList.toggle('is-full', rateNum != null && rateNum >= 99.5);
        }

        const summary = document.getElementById('mcRateSummary');
        if (summary) {
            if (this.isTesting) summary.textContent = 'SCANNING…';
            else if (total === 0) summary.textContent = 'NO NODES';
            else if (fail > 0) summary.textContent = `${fail} ERR · ${ok} OK`;
            else summary.textContent = 'ALL CLEAR';
        }

        // 12-slot brick tracks (data-driven, not decorative)
        this._renderBricks('mcTotalBricks', 'mcTotalCap', total, Math.max(total, 12), 'blue');
        this._renderBricks('mcOkBricks', 'mcOkCap', ok, Math.max(total, 12), 'green');
        this._renderBricks('mcFailBricks', 'mcFailCap', fail, Math.max(total, 12), 'red');

        const offlineCard = document.getElementById('mcOfflineCard');
        if (offlineCard) {
            offlineCard.classList.toggle('is-alert', fail > 0);
            offlineCard.classList.toggle('is-quiet', fail === 0);
        }
        const onlineCard = document.querySelector('.mc-online');
        if (onlineCard) onlineCard.classList.toggle('is-hot', ok > 0 && fail === 0);
    }

    _renderBricks(trackId, capId, filled, slots, tone) {
        const track = document.getElementById(trackId);
        const cap = document.getElementById(capId);
        if (!track) return;
        const n = Math.max(1, Math.min(24, slots || 12));
        const on = Math.max(0, Math.min(n, filled || 0));
        let html = '';
        for (let i = 0; i < n; i++) {
            html += `<i class="brick${i < on ? ' on' : ''}" style="--i:${i}"></i>`;
        }
        track.dataset.tone = tone || 'blue';
        track.innerHTML = html;
        if (cap) cap.textContent = `${on}/${n}`;
    }

    animateNumber(elementId, target, animate = true) {
        const el = document.getElementById(elementId);
        if (!el) return;
        if (!animate) {
            el.textContent = target;
            return;
        }

        const start = parseInt(el.textContent, 10) || 0;
        const duration = 280;
        const startTime = performance.now();
        const steps = 7;

        function update(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            // stepped (pixel) interpolation instead of smooth ease
            const stepped = Math.round(progress * steps) / steps;
            const current = Math.round(start + (target - start) * stepped);
            el.textContent = current;
            if (progress < 1) requestAnimationFrame(update);
            else el.textContent = target;
        }
        requestAnimationFrame(update);
    }

    updateProviderCount(count) {
        const el = document.getElementById('providerCount');
        if (el) el.textContent = `${count} NODES`;
    }

    toggleEmptyHint(isEmpty) {
        const hint = document.getElementById('emptyHint');
        if (hint) hint.style.display = isEmpty ? 'block' : 'none';
    }

    /* --------------------------- Filter/Render ------------------------- */
    filterProviders(query) {
        this._searchKeywords = [];
        query = query.trim();
        // 作用域限当前分页（Claude Code / Codex）
        const appFiltered = this.providers.filter(p => this._providerForApp(p, this.activeApp));
        if (!query) {
            this.filteredProviders = appFiltered;
        } else {
            const keywords = query.toLowerCase().split(/\s+/).filter(Boolean);
            this._searchKeywords = keywords;
            this.filteredProviders = appFiltered.filter(p => {
                const haystack = [
                    p.name, p.endpoint, p.category, p.notes,
                    p.default_model, p.api_format, this._apiFormatLabel(p.api_format, ''), p.app_type, p.role, p.detail,
                ].map(s => (s || '').toLowerCase()).join(' ');
                return keywords.every(kw => haystack.includes(kw));
            });
        }
        this.renderProviders(this.filteredProviders);
        this.updateProviderCount(this.filteredProviders.length);
    }

    _highlight(text, keywords) {
        if (!keywords || !keywords.length || !text) return this.escape(text);
        // 先按关键词分段、再对每段单独转义：不能在转义后的串上做替换，
        // 否则搜 "amp"/"lt" 会命中 &amp; 等实体内部产生乱码
        const valid = keywords.filter(Boolean);
        if (!valid.length) return this.escape(text);
        const pattern = valid.map(kw => kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
        const re = new RegExp(`(${pattern})`, 'gi');
        return String(text).split(re).map((seg, i) =>
            i % 2 === 1 ? `<mark>${this.escape(seg)}</mark>` : this.escape(seg)
        ).join('');
    }

    _latencyClass(latencyStr) {
        const num = this._latencyNum(latencyStr);
        if (num >= 999999) return '';
        if (num < 200) return 'good';
        if (num < 500) return 'medium';
        return 'bad';
    }

    _latencyBars(latencyStr) {
        const num = this._latencyNum(latencyStr);
        if (num >= 999999) {
            return '<span class="lat-hp lat-hp--na" title="无延迟数据" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>';
        }
        const cls = this._latencyClass(latencyStr);
        // 5-segment HP bar: more segments = healthier (lower latency)
        const segs = cls === 'good' ? 5 : cls === 'medium' ? 3 : 1;
        let cells = '';
        for (let i = 0; i < 5; i++) {
            cells += `<i class="${i < segs ? 'on' : ''}"></i>`;
        }
        return `<span class="lat-hp lat-hp--${cls || 'na'}" title="${this.escape(String(latencyStr))}" aria-hidden="true">${cells}</span>`;
    }

    _pingCell(latencyStr) {
        const raw = (latencyStr && String(latencyStr).trim() && String(latencyStr) !== '-')
            ? String(latencyStr).trim()
            : '--';
        const latClass = this._latencyClass(raw === '--' ? '' : raw);
        return `<span class="ping-cell">` +
            `<span class="latency ${latClass}">${this.escape(raw)}</span>` +
            `${this._latencyBars(raw === '--' ? '' : raw)}` +
            `</span>`;
    }

    /* --------------------- 应用分页 & 官方/第三方模式 -------------------- */

    _providerForApp(p, app) {
        if (!app) return false;
        if (Array.isArray(p.apps) && p.apps.length) {
            return p.apps.some(b => b.app_type === app);
        }
        // 兼容旧数据：无 apps 时按顶层 app_type
        const t = p.app_type || 'claude';
        return t === app || t === 'both';
    }

    applyAppFilter() {
        this.filteredProviders = this.providers.filter(p => this._providerForApp(p, this.activeApp));
        this.updateProviderCount(this.filteredProviders.length);
    }

    _loadCurrentModes() {
        const backend = this.backend();
        if (!backend.get_current_modes) return Promise.resolve();
        return backend.get_current_modes().then(r => {
            if (r && r.success && r.data) {
                this.currentModes = Object.assign(this.currentModes, r.data);
            }
            this.renderModeBar();
        }).catch(() => this.renderModeBar());
    }

    renderModeBar() {
        const app = this.activeApp;
        const st = this.currentModes[app] || { mode: 'official', provider_name: null };
        const cur = document.getElementById('modeCur');
        if (cur) {
            if (st.mode === 'provider') {
                cur.textContent = st.provider_name ? `当前: ${st.provider_name}` : '第三方';
            } else {
                cur.textContent = '官方账号';
            }
        }
        document.querySelectorAll('#modeBar .mode-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.mode === st.mode);
        });
        document.querySelectorAll('#appTabs .app-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.app === app);
        });
    }

    switchAppTab(app) {
        if (app === this.activeApp) return;
        this.activeApp = app;
        // 若当前选中的供应商不属于新分页，清空选中
        const sel = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (sel && !this._providerForApp(sel, app)) {
            this.selectedProviderId = null;
            this.clearProviderDetail();
        }
        this.filterProviders('');
        this.renderModeBar();
    }

    async switchMode(mode) {
        if (mode === 'official') {
            const app = this.activeApp;
            try {
                const r = await this.backend().set_current_official(app);
                if (r && r.success) {
                    await this.loadData(false);
                    this.toast('已切换为官方账号模式', 'success');
                    this.pushLog('info', `已将 ${app === 'codex' ? 'Codex' : 'Claude Code'} 切换为官方账号模式`);
                } else {
                    this.toast((r && r.error) || '切换失败', 'error');
                }
            } catch (e) {
                this.toast('切换失败: ' + e.message, 'error');
            }
        } else {
            // 第三方模式由「设为当前」进入，这里引导用户
            this.toast('在下方选择一个供应商，点 ★ 设为当前', 'info');
        }
    }

    _syncAppFields() {
        document.querySelectorAll('.app-fields').forEach(g => {
            const app = g.dataset.app;
            const cb = document.getElementById(app === 'codex' ? 'fAppCodex' : 'fAppClaude');
            g.style.display = (cb && cb.checked) ? '' : 'none';
        });
    }

    renderProviders(providers, animate = true) {
        const tbody = document.getElementById('providerTableBody');
        if (!tbody) return;
        tbody.innerHTML = '';

        const sorted = this.sortProviders([...providers]);
        sorted.forEach((provider, index) => {
            const tr = document.createElement('tr');
            tr.dataset.id = provider.id;
            if (animate) {
                tr.style.animationDelay = `${index * 30}ms`;
                tr.classList.add('fade-in');
            }

            const sc = this.statusClass(provider.status);
            const st = this.statusText(provider.status);
            // Add status-based row class for colored left border
            if (sc === 'ok') tr.classList.add('row-ok');
            else if (sc === 'fail') tr.classList.add('row-fail');
            else if (sc === 'testing') tr.classList.add('row-testing');
            const roleClass   = provider.role === '当前' ? 'current' : 'backup';
            const isChecked = this.selectedIds.has(String(provider.id));
            if (provider.role === '当前') tr.classList.add('row-current');

            tr.innerHTML = `
                <td class="col-check"><input type="checkbox" class="row-check" data-id="${provider.id}" ${isChecked ? 'checked' : ''}></td>
                <td><span class="plc-name"><span class="row-caret" aria-hidden="true">▶</span>${this._highlight(provider.name, this._searchKeywords)}</span></td>
                <td><span class="plc-endpoint" title="${this.escape(provider.endpoint || '')}">${this._highlight(provider.endpoint || '-', this._searchKeywords)}</span></td>
                <td><span class="role-badge ${roleClass}">${provider.role === '当前' ? 'CUR' : this.escape(provider.role || '-')}</span></td>
                <td class="col-ping">${this._pingCell(provider.latency)}</td>
                <td><span class="status-pill ${sc}">${this.statusIcon(sc)} ${st}</span></td>
            `;

            // 复选框点击：阻止冒泡（不触发行选中），处理 shift/ctrl 多选
            const checkbox = tr.querySelector('.row-check');
            checkbox.addEventListener('click', (e) => {
                e.stopPropagation();
                this._handleCheckClick(e, provider.id, index);
            });

            tr.addEventListener('click', () => this.selectProvider(provider.id));
            tbody.appendChild(tr);
        });
        this._syncSelectionModeUI();
    }

    /* ------------------------- Incremental updates ------------------------ */
    // 由后端通过 evaluate_js 调用,只更新单行,不重绘整表,消除闪烁

    updateSingleProvider(providerData) {
        // 更新内存中的 provider 数据
        const idx = this.providers.findIndex(p => String(p.id) === String(providerData.id));
        if (idx >= 0) {
            this.providers[idx] = { ...this.providers[idx], ...providerData };
        }

        // 增量更新表格行(如果存在)
        const tbody = document.getElementById('providerTableBody');
        if (!tbody) return;
        const existingRow = tbody.querySelector(`tr[data-id="${providerData.id}"]`);
        if (existingRow) {
            const p = idx >= 0 ? this.providers[idx] : providerData;
            const sc = this.statusClass(p.status);
            const st = this.statusText(p.status);
            // 同步行状态类（左侧彩色边框），与 renderProviders 保持一致
            existingRow.classList.remove('row-ok', 'row-fail', 'row-testing');
            if (sc === 'ok') existingRow.classList.add('row-ok');
            else if (sc === 'fail') existingRow.classList.add('row-fail');
            else if (sc === 'testing') existingRow.classList.add('row-testing');
            existingRow.classList.toggle('row-current', p.role === '当前');
            const cells = existingRow.querySelectorAll('td');
            if (cells.length >= 6) {
                // 名称 (index 1, 因为 0 是复选框)：复用整表渲染的模板（含插入符与搜索高亮）
                cells[1].innerHTML = `<span class="plc-name"><span class="row-caret" aria-hidden="true">▶</span>${this._highlight(p.name || '', this._searchKeywords)}</span>`;
                // 延迟 (index 4)
                cells[4].classList.add('col-ping');
                cells[4].innerHTML = this._pingCell(p.latency);
                // 状态药丸 (index 5)
                cells[5].innerHTML = `<span class="status-pill ${sc}">${this.statusIcon(sc)} ${st}</span>`;
            }
        }

        // 如果当前选中的就是这个 provider,刷新详情面板
        if (String(this.selectedProviderId) === String(providerData.id)) {
            const p = this.providers[idx];
            if (p) this.renderProviderDetail(p);
        }
    }

    updateStatsIncremental(stats) {
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };
        const total = stats.total || 0;
        const ok = stats.ok || 0;
        const fail = stats.fail || 0;

        set('metricProviderCount', total);
        set('mcTotal', total);
        set('mcOk', ok);
        set('mcFail', fail);
        // 平均延迟等指标统一由 _applyMetricVisuals 更新，不再重复计算
        this._applyMetricVisuals(total, ok, fail);
    }

    addCliLog(level, text, name) {
        this.pushLog(level, text, name);
    }

    testingComplete() {
        this.setTestingState(false);
        // 测试结束后做一次完整刷新(排序可能变化)
        this.loadData(false);
    }

    statusClass(s) {
        return s === 'ok' ? 'ok' :
               s === 'fail' ? 'fail' :
               s === 'testing' ? 'testing' : 'pending';
    }

    statusText(s) {
        return s === 'ok' ? 'OK' :
               s === 'fail' ? 'ERR' :
               s === 'testing' ? 'SCAN' : 'IDLE';
    }

    statusIcon(s) {
        if (s === 'ok') return '<span class="px-ico px-ico-ok" aria-hidden="true">OK</span>';
        if (s === 'fail') return '<span class="px-ico px-ico-fail" aria-hidden="true">ERR</span>';
        if (s === 'testing') return '<span class="px-ico px-ico-test" aria-hidden="true">…</span>';
        return '<span class="px-ico px-ico-na" aria-hidden="true">·</span>';
    }

    escape(str) {
        return String(str).replace(/[&<>"']/g, (c) =>
            ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    /* ------------------------------ Sort ------------------------------- */
    // 优先级: ok(可用) > testing(测试中) > pending(未测) > fail(失败)
    // 同状态内: ok 按延迟升序,其他按 id 原顺序
    sortProviders(list) {
        const priority = (s) => s === 'ok' ? 0 : s === 'testing' ? 1 : s === 'pending' ? 2 : 3;
        return list.sort((a, b) => {
            const pa = priority(a.status), pb = priority(b.status);
            if (pa !== pb) return pa - pb;
            if (a.status === 'ok' && b.status === 'ok') {
                const la = this._latencyNum(a.latency), lb = this._latencyNum(b.latency);
                if (la !== lb) return la - lb;
            }
            // 保留原始顺序(按 id 数值)
            return (parseInt(a.id) || 0) - (parseInt(b.id) || 0);
        });
    }

    _latencyNum(str) {
        if (!str) return 999999;
        const m = String(str).match(/(\d+)/);
        return m ? parseInt(m[1]) : 999999;
    }

    /* ----------------------------- Selection --------------------------- */
    selectProvider(id) {
        document.querySelectorAll('#providerTableBody tr').forEach(tr => {
            tr.classList.toggle('selected', tr.dataset.id === String(id));
        });
        this.selectedProviderId = id;
        const actions = document.getElementById('dcActions');
        if (actions) actions.style.display = id != null ? 'flex' : 'none';
        const provider = this.providers.find(p => String(p.id) === String(id));
        if (provider) {
            this.renderProviderDetail(provider);
        } else {
            this.clearProviderDetail();
        }
    }

    clearProviderDetail() {
        const header = document.getElementById('dcProviderHeader');
        if (header) {
            header.classList.remove('visible');
            header.setAttribute('aria-hidden', 'true');
        }
        const emptyState = document.getElementById('emptyState');
        const detailInfo = document.getElementById('detailInfo');
        if (emptyState) emptyState.style.display = 'flex';
        if (detailInfo) {
            detailInfo.style.display = 'none';
            detailInfo.innerHTML = '';
        }
        const actions = document.getElementById('dcActions');
        if (actions) actions.style.display = 'none';
        const avatar = document.getElementById('dcAvatar');
        if (avatar) avatar.textContent = '';
        const pName = document.getElementById('dcProviderName');
        if (pName) pName.textContent = '';
        const ep = document.getElementById('dcProviderEndpoint');
        if (ep) ep.textContent = '';
        const pStatus = document.getElementById('dcProviderStatus');
        if (pStatus) {
            pStatus.className = 'dc-provider-status';
            pStatus.innerHTML = '';
        }
    }

    switchDetailTab(tab) {
        this.activeDetailTab = tab;
        document.querySelectorAll('.dc-tab[data-tab]').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === tab);
        });
        const provider = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (provider) this.renderProviderDetail(provider);
    }

    async renderProviderDetail(provider) {
        const emptyState = document.getElementById('emptyState');
        const detailInfo = document.getElementById('detailInfo');
        if (!detailInfo) return;

        if (emptyState) emptyState.style.display = 'none';
        detailInfo.style.display = 'block';

        // Update provider header (only visible after a real selection)
        const header = document.getElementById('dcProviderHeader');
        if (header) {
            header.classList.add('visible');
            header.setAttribute('aria-hidden', 'false');
            const avatar = document.getElementById('dcAvatar');
            if (avatar) avatar.textContent = (provider.name || '?').charAt(0).toUpperCase();
            const pName = document.getElementById('dcProviderName');
            if (pName) pName.textContent = provider.name || '未命名';
            const ep = document.getElementById('dcProviderEndpoint');
            if (ep) ep.textContent = provider.endpoint || '未设置端点';
            const pStatus = document.getElementById('dcProviderStatus');
            const pSc = this.statusClass(provider.status);
            if (pStatus) {
                pStatus.className = 'dc-provider-status ' + pSc;
                const icon = pSc === 'ok'
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
                    : pSc === 'fail'
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
                    : '';
                pStatus.innerHTML = icon + ' ' + this.statusText(provider.status);
            }
        }

        const tab = this.activeDetailTab;
        const sc = this.statusClass(provider.status);
        const st = this.statusText(provider.status);

        // Helper: render a copy button (data-copy holds the raw text)
        const cBtn = (text, label) => {
            if (!text) return '';
            return `<button class="dc-copy-btn" data-copy="${this.escape(text)}" data-label="${this.escape(label)}" title="复制${label}">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            </button>`;
        };

        let html = '';
        if (tab === 'basic') {
            // Fetch global settings for timeout/max_retries display
            let timeout = '-', maxRetries = '-';
            try {
                const sr = await this.backend().get_all_settings();
                if (sr?.success) {
                    timeout = (sr.data.test_timeout || '30') + 's';
                    maxRetries = sr.data.test_retries || '2';
                }
            } catch {}

            // Format creation time
            let createdTime = '-';
            if (provider.created_at) {
                const d = new Date(provider.created_at * 1000);
                createdTime = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
            }

            // API format label
            const fmtLabel = this._apiFormatLabel(provider.api_format, '-');

            // API Key 区块：DOM 中只放掩码，明文通过 get_provider_key 按需获取
            // （复制/显示时才向后端请求，不落入 DOM 属性）
            let apiKeyHtml = '';
            try {
                const maskedKey = provider.key || '-';
                const hasKey = maskedKey !== '-';
                const escMasked = this.escape(maskedKey);
                apiKeyHtml = '<div class="info-item info-full">'
                    + '<div class="info-label">API Key</div>'
                    + '<div class="info-value mono api-key-wrap">'
                    + '<span class="api-key-text" id="apiKeyText" data-masked="' + escMasked + '">' + escMasked + '</span>'
                    + (hasKey ? '<button class="dc-copy-btn" id="apiKeyCopy" title="复制 API Key">'
                    + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
                    + '</button>'
                    + '<button class="api-key-toggle" id="apiKeyToggle" title="显示/隐藏">'
                    + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
                    + '</button>' : '')
                    + '</div></div>';
            } catch (e) {
                console.error('API key render error:', e);
                apiKeyHtml = '';
            }

            html = `
                <div class="dc-section">
                    <div class="dc-section-title">基本信息</div>
                    <div class="info-grid">
                        <div class="info-item info-full">
                            <div class="info-label">端点地址</div>
                            <div class="info-value mono">${this.escape(provider.endpoint || '-')}${cBtn(provider.endpoint, '端点')}</div>
                        </div>
                        ${apiKeyHtml}
                        <div class="info-item">
                            <div class="info-label">名称</div>
                            <div class="info-value">${this.escape(provider.name)}${cBtn(provider.name, '名称')}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">应用类型</div>
                            <div class="info-value">${this.escape(provider.app_type || '-')}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">API 格式</div>
                            <div class="info-value">${this.escape(fmtLabel)}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">部署模型</div>
                            <div class="info-value mono">${this.escape(provider.default_model || '-')}${cBtn(provider.default_model, '默认模型')}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">角色</div>
                            <div class="info-value"><span class="role-badge ${provider.role === '当前' ? 'current' : 'backup'}">${this.escape(provider.role || '-')}</span></div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">超时设置</div>
                            <div class="info-value mono">${timeout}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">最大重试</div>
                            <div class="info-value mono">${maxRetries}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">创建时间</div>
                            <div class="info-value mono">${createdTime}</div>
                        </div>
                        ${provider.notes ? `<div class="info-item info-full"><div class="info-label">备注</div><div class="info-value">${this.escape(provider.notes)}${cBtn(provider.notes, '备注')}</div></div>` : ''}
                    </div>
                </div>
                <div class="dc-section" style="margin-top:14px;">
                    <div class="dc-section-title">近 7 天可用性</div>
                    <div class="avail-cards" id="availCards">
                        <div class="avail-loading">加载中...</div>
                    </div>
                </div>
            `;
        } else if (tab === 'test') {
            const statusValClass = sc === 'ok' ? 'good' : sc === 'fail' ? 'bad' : '';
            const fmtLabel = this._apiFormatLabel(provider.api_format, '未检测');
            const fmtClass = provider.api_format ? 'good' : '';
            const detailLabel = sc === 'ok' ? 'AI 回复' : sc === 'fail' ? '错误详情' : '详情';
            const detailText = provider.detail || '-';
            const displayDetailText = sc === 'ok'
                ? detailText.replace(/^AI\s*正常回复[:：]\s*/, '')
                : detailText;
            const detailCardClass = sc === 'ok' ? 'ai-reply-card' : sc === 'fail' ? 'error-detail-card' : '';
            let lastTestTime = '-';
            if (provider.last_test_time) {
                const d = new Date(provider.last_test_time * 1000);
                lastTestTime = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
            }

            // Parse latency number for bar chart
            const latNum = parseInt(provider.latency) || 0;
            const maxLat = Math.max(latNum * 2, 500);
            const barPct = latNum > 0 ? Math.min((latNum / maxLat) * 100, 100) : 0;
            const barColor = latNum < 200 ? 'tb-green' : latNum < 500 ? 'tb-blue' : latNum < 1000 ? 'tb-amber' : 'tb-red';
            const latColor = latNum < 200 ? 'var(--green)' : latNum < 500 ? 'var(--blue)' : latNum < 1000 ? 'var(--amber)' : 'var(--red)';

            html = `
                <div class="dc-section">
                    <div class="test-summary">
                        <div class="test-stat">
                            <div class="test-stat-val" style="color:${latColor}">${provider.latency || '-'}</div>
                            <div class="test-stat-label">延迟</div>
                        </div>
                        <div class="test-stat">
                            <div class="test-stat-val" style="color:${sc === 'ok' ? 'var(--green)' : sc === 'fail' ? 'var(--red)' : 'var(--muted)'}">${st}</div>
                            <div class="test-stat-label">状态</div>
                        </div>
                        <div class="test-stat">
                            <div class="test-stat-val" style="color:${fmtClass ? 'var(--green)' : 'var(--muted)'}; font-size:11px;">${this.escape(fmtLabel)}</div>
                            <div class="test-stat-label">API 格式</div>
                        </div>
                    </div>
                    ${latNum > 0 ? `
                    <div class="dc-section-title">延迟分布</div>
                    <div class="test-list">
                        <div class="test-row">
                            <span class="test-row-label">响应延迟</span>
                            <div class="test-bar-wrap">
                                <div class="test-bar ${barColor}" style="width:${barPct}%;">${latNum}ms</div>
                            </div>
                            <span class="test-row-val" style="color:${latColor}">${latNum}ms</span>
                        </div>
                    </div>
                    ` : ''}
                    <div style="margin-top:14px;">
                        <div class="dc-section-title">测试详情</div>
                        <div class="test-list">
                            <div class="test-row">
                                <span class="test-row-label">测试时间</span>
                                <div></div>
                                <span class="test-row-val mono">${this.escape(lastTestTime)}</span>
                            </div>
                            <div class="test-detail-row ${detailCardClass}">
                                <span class="test-row-label">${detailLabel}</span>
                                <div class="test-detail-card">
                                    ${sc === 'ok' ? '<span class="ai-reply-kicker">AI RESPONSE</span>' : ''}
                                    <div class="test-detail-text">${this.escape(displayDetailText)}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        } else if (tab === 'cli') {
            // CLI log rendered separately
            detailInfo.innerHTML = '';
            this.renderCliTab();
            return;
        } else if (tab === 'trend') {
            this.renderTrendTab();
            return;
        }

        detailInfo.innerHTML = html;

        // Bind copy button click events
        detailInfo.querySelectorAll('.dc-copy-btn').forEach(btn => {
            if (btn.id === 'apiKeyCopy') return; // API Key 复制走按需取 key 逻辑
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.copyToClipboard(btn.dataset.copy, btn.dataset.label);
            });
        });

        // API Key 复制：点击时才向后端取明文
        const keyCopyBtn = document.getElementById('apiKeyCopy');
        if (keyCopyBtn) {
            keyCopyBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const key = await this._fetchProviderKey(provider.id);
                if (key) this.copyToClipboard(key, 'API Key');
            });
        }

        // API Key 显示/隐藏：显示时才向后端取明文，隐藏时恢复掩码
        const toggleBtn = document.getElementById('apiKeyToggle');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const span = document.getElementById('apiKeyText');
                if (!span) return;
                if (toggleBtn.classList.toggle('revealed')) {
                    const key = await this._fetchProviderKey(provider.id);
                    if (key) {
                        span.textContent = key;
                        toggleBtn.title = '隐藏';
                    } else {
                        toggleBtn.classList.remove('revealed');
                    }
                } else {
                    span.textContent = span.dataset.masked || '-';
                    toggleBtn.title = '显示/隐藏';
                }
            });
        }

        // Fetch 7-day availability stats for basic tab
        if (tab === 'basic') {
            this._renderAvailCards(provider.id);
        }
    }

    async _renderAvailCards(providerId) {
        const el = document.getElementById('availCards');
        if (!el) return;
        try {
            const r = await this.backend().get_history_stats(providerId, 168);
            if (!r?.success || !r.data) {
                el.innerHTML = '<div class="avail-empty">暂无历史数据</div>';
                return;
            }
            const d = r.data;
            const avail = d.availability != null ? d.availability.toFixed(1) : '-';
            const availClass = d.availability >= 99 ? 'good' : d.availability >= 95 ? 'medium' : 'bad';
            const avgLat = d.avg_latency != null ? `${d.avg_latency}ms` : '-';
            const p95Lat = d.p95_latency != null ? `${d.p95_latency}ms` : '-';
            const total = d.total || 0;
            el.innerHTML = `
                <div class="avail-card">
                    <div class="avail-val ${availClass}">${avail}%</div>
                    <div class="avail-label">可用率</div>
                </div>
                <div class="avail-card">
                    <div class="avail-val">${avgLat}</div>
                    <div class="avail-label">平均延迟</div>
                </div>
                <div class="avail-card">
                    <div class="avail-val">${p95Lat}</div>
                    <div class="avail-label">P95 延迟</div>
                </div>
                <div class="avail-card">
                    <div class="avail-val">${total}</div>
                    <div class="avail-label">测试次数</div>
                </div>
            `;
        } catch {
            el.innerHTML = '<div class="avail-empty">加载失败</div>';
        }
    }

    /* ------------------------------ Testing ---------------------------- */
    async testAll(mode) {
        if (this.isTesting) return;
        this.setTestingState(true);
        this.pushLog('info', `开始批量${mode === 'full' ? '完整' : '快速'}测试 (${this.filteredProviders.length} 个)...`);
        this.toast(`开始${mode === 'full' ? '完整' : '快速'}测试`, 'info');
        try {
            const backend = this.backend();
            const r = await backend.test_all(mode);
            if (!r?.success) {
                this.pushLog('err', r?.error || '启动失败');
                this.setTestingState(false);
            }
            // 真实后端在测试中通过 _refresh_frontend 推送刷新;模拟后端自行驱动刷新
        } catch (error) {
            console.error('Test failed:', error);
            this.pushLog('err', '测试异常: ' + error.message);
            this.setTestingState(false);
        }
    }

    async stopTesting() {
        try {
            await this.backend().stop_testing();
        } catch (error) {
            console.error('Stop failed:', error);
        }
        this.setTestingState(false);
    }

    setTestingState(testing) {
        this.isTesting = testing;

        const stopBtn = document.getElementById('stopBtn');
        const fastBtn = document.getElementById('fastTestBtn');
        const mcFullBtn = document.getElementById('mcFullTestBtn');
        const dot = document.getElementById('serviceDot');
        const ribbon = document.getElementById('scanRibbon');
        const progressWrap = document.getElementById('mcProgressWrap');
        const brandOnline = document.getElementById('brandOnline');
        const app = document.querySelector('.app');

        // Keep layout stable: toggle hidden attr instead of display none on both
        if (stopBtn) {
            if (testing) stopBtn.removeAttribute('hidden');
            else stopBtn.setAttribute('hidden', '');
        }
        if (fastBtn) {
            if (testing) fastBtn.setAttribute('hidden', '');
            else fastBtn.removeAttribute('hidden');
        }
        if (mcFullBtn) {
            if (testing) mcFullBtn.setAttribute('hidden', '');
            else mcFullBtn.removeAttribute('hidden');
        }

        if (dot) dot.classList.toggle('testing', testing);
        if (ribbon) {
            ribbon.classList.toggle('is-on', testing);
            ribbon.setAttribute('aria-hidden', testing ? 'false' : 'true');
        }
        if (progressWrap) progressWrap.classList.toggle('is-scanning', testing);
        if (app) app.classList.toggle('is-testing', testing);
        if (brandOnline) {
            brandOnline.innerHTML = testing
                ? 'SCAN<span class="px-cursor">_</span>'
                : 'ONLINE<span class="px-cursor">_</span>';
            brandOnline.classList.toggle('is-scan', testing);
        }

        const summary = document.getElementById('mcRateSummary');
        if (summary && testing) summary.textContent = 'SCANNING…';
    }

    /* ------------------------------ Actions ---------------------------- */
    async importFromClaudeCode() {
        const ok = await this.showConfirm(
            '将读取当前 Claude Code 生效配置（~/.claude/settings.json 与环境变量），并与已有供应商对比：\n• 相同配置 → 标为「当前」\n• 不同配置 → 自动新建供应商并写入当前配置',
            '同步 Claude Code 配置',
            '开始同步',
            'primary'
        );
        if (!ok) return;
        try {
            const backend = this.backend();
            if (!backend.sync_claude_code_provider) {
                this.toast('当前版本后端不支持同步 Claude Code，请重新安装最新 exe', 'error');
                return;
            }
            const result = await backend.sync_claude_code_provider();
            if (result?.success) {
                await this.loadData(false);
                const action = result.action || '';
                const level = (action === 'created' || action === 'updated') ? 'success' : 'info';
                this.toast(result.message || '同步完成', level);
                if (result.provider_id != null) {
                    this.selectProvider(String(result.provider_id));
                }
                return;
            }
            this.toast(result?.error || result?.message || '同步失败', 'error');
        } catch (error) {
            console.error('Sync Claude Code failed:', error);
            this.toast('同步失败: ' + (error?.message || error), 'error');
        }
    }

    async importFromCodex() {
        const ok = await this.showConfirm(
            '将读取当前 Codex 生效配置（~/.codex/config.toml 与环境变量），并与已有 Codex 供应商对比：\n• 相同配置 → 标为「当前」\n• 不同配置 → 自动新建供应商',
            '同步 Codex 配置',
            '开始同步',
            'primary'
        );
        if (!ok) return;
        try {
            const backend = this.backend();
            if (!backend.sync_codex_provider) {
                this.toast('当前版本后端不支持同步 Codex，请重新安装最新 exe', 'error');
                return;
            }
            const result = await backend.sync_codex_provider();
            if (result?.success) {
                await this.loadData(false);
                const action = result.action || '';
                const level = (action === 'created' || action === 'updated') ? 'success' : 'info';
                this.toast(result.message || '同步完成', level);
                if (result.provider_id != null) {
                    this.selectProvider(String(result.provider_id));
                }
                return;
            }
            this.toast(result?.error || result?.message || '同步失败', 'error');
        } catch (error) {
            console.error('Sync Codex failed:', error);
            this.toast('同步失败: ' + (error?.message || error), 'error');
        }
    }

    async importFromCCSwitch() {
        // 次要路径: 从 cc-switch / 本地 .db 导入
        const ok = await this.showConfirm(
            '将从 cc-switch 数据库导入供应商配置，已存在的同名供应商将被跳过。确定继续？',
            '从 cc-switch 导入',
            '确认导入',
            'primary'
        );
        if (!ok) return;
        try {
            const backend = this.backend();
            const result = await backend.import_from_ccswitch();
            if (result?.success) {
                await this.loadData(false);
                this.toast(result.message || `成功导入 ${result.imported || 0} 个`, 'success');
                return;
            }

            // cc-switch 数据库仍未找到 - 弹文件选择框让用户手动选
            if (result?.missing_db && backend.choose_and_import_from_file) {
                const r2 = await backend.choose_and_import_from_file();
                if (r2?.success) {
                    await this.loadData(false);
                    this.toast(r2.message || `成功导入 ${r2.imported || 0} 个`, 'success');
                } else if (r2?.error && r2.error !== '未选择文件') {
                    this.toast(r2.error, 'error');
                }
                return;
            }

            this.toast(result?.error || '导入失败', 'error');
        } catch (error) {
            console.error('Import failed:', error);
            this.toast('导入失败: ' + error.message, 'error');
        }
    }

    /* --------------------------- Provider modal ------------------------ */
    openAddModal() {
        document.getElementById('modalTitle').textContent = '新增提供商';
        document.getElementById('modalProviderId').value = '';
        document.getElementById('fName').value = '';
        // 默认勾选当前分页的应用
        document.getElementById('fAppClaude').checked = this.activeApp === 'claude';
        document.getElementById('fAppCodex').checked = this.activeApp === 'codex';
        this._setCustomSelectValue('fApiFormatClaude', 'anthropic_messages');
        this._setCustomSelectValue('fApiFormatCodex', 'openai_responses');
        this._setCustomSelectValue('fReasoningEffortClaude', '');
        this._setCustomSelectValue('fContextLengthClaude', '0');
        this._setCustomSelectValue('fReasoningEffortCodex', '');
        this._setCustomSelectValue('fContextLengthCodex', '0');
        document.getElementById('fEndpointClaude').value = '';
        document.getElementById('fEndpointCodex').value = '';
        document.getElementById('fApiKey').value = '';
        document.getElementById('fDefaultModelClaude').value = '';
        document.getElementById('fDefaultModelCodex').value = '';
        document.getElementById('fCategory').value = '';
        document.getElementById('fNotes').value = '';
        this._syncAppFields();
        // Reset model dropdowns
        ['modelDropdownClaude', 'modelDropdownCodex'].forEach(id => {
            const dd = document.getElementById(id);
            if (dd) { dd.style.display = 'none'; dd.innerHTML = ''; }
        });
        document.getElementById('providerModal').classList.add('show');
        setTimeout(() => document.getElementById('fName').focus(), 50);
    }

    openEditModal() {
        const p = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (!p) return;
        const apps = (Array.isArray(p.apps) && p.apps.length) ? p.apps : [];
        const claudeB = apps.find(b => b.app_type === 'claude');
        const codexB = apps.find(b => b.app_type === 'codex');
        const isClaude = !!claudeB || (p.app_type || 'claude') === 'claude' || (p.app_type || '') === 'both';
        const isCodex = !!codexB || (p.app_type || '') === 'codex' || (p.app_type || '') === 'both';

        document.getElementById('modalTitle').textContent = '编辑: ' + (p.name || '');
        document.getElementById('modalProviderId').value = p.id;
        document.getElementById('fName').value = p.name || '';
        document.getElementById('fAppClaude').checked = isClaude;
        document.getElementById('fAppCodex').checked = isCodex;
        document.getElementById('fEndpointClaude').value = (claudeB && claudeB.endpoint) || (isClaude ? (p.endpoint || '') : '');
        document.getElementById('fEndpointCodex').value = (codexB && codexB.endpoint) || (isCodex && !isClaude ? (p.endpoint || '') : '');
        this._setCustomSelectValue('fApiFormatClaude', (claudeB && claudeB.api_format) || 'anthropic_messages');
        this._setCustomSelectValue('fApiFormatCodex', (codexB && codexB.api_format) || 'openai_responses');
        this._setCustomSelectValue('fReasoningEffortClaude', (claudeB && claudeB.reasoning_effort) || '');
        this._setCustomSelectValue('fContextLengthClaude', String((claudeB && claudeB.context_length) || 0));
        this._setCustomSelectValue('fReasoningEffortCodex', (codexB && codexB.reasoning_effort) || '');
        this._setCustomSelectValue('fContextLengthCodex', String((codexB && codexB.context_length) || 0));
        document.getElementById('fDefaultModelClaude').value = (claudeB && claudeB.default_model) || '';
        document.getElementById('fDefaultModelCodex').value = (codexB && codexB.default_model) || '';
        document.getElementById('fApiKey').value = ''; // 安全:仅当用户填写则覆盖
        document.getElementById('fApiKey').placeholder = p.key && p.key !== '-' ? `当前: ${p.key}` : 'sk-...';
        document.getElementById('fCategory').value = p.category || '';
        document.getElementById('fNotes').value = p.notes || '';
        this._syncAppFields();
        // Reset model dropdowns
        ['modelDropdownClaude', 'modelDropdownCodex'].forEach(id => {
            const dd = document.getElementById(id);
            if (dd) { dd.style.display = 'none'; dd.innerHTML = ''; }
        });
        document.getElementById('providerModal').classList.add('show');
    }

    closeProviderModal() {
        document.getElementById('providerModal').classList.remove('show');
        ['modelDropdownClaude', 'modelDropdownCodex'].forEach(id => {
            const dd = document.getElementById(id);
            if (dd) { dd.style.display = 'none'; dd.innerHTML = ''; }
        });
    }

    async saveProviderFromModal() {
        const id = document.getElementById('modalProviderId').value;
        const name = document.getElementById('fName').value.trim();
        const apiKey = document.getElementById('fApiKey').value.trim();
        const category = document.getElementById('fCategory').value.trim();
        const notes = document.getElementById('fNotes').value.trim();
        if (!name) { this.toast('请填写名称', 'error'); return; }

        // 每应用独立输入组 → apps 绑定数组
        const readApp = (app) => {
            const cap = app === 'codex' ? 'Codex' : 'Claude';
            const binding = {
                app_type: app,
                endpoint: document.getElementById('fEndpoint' + cap).value.trim(),
                default_model: document.getElementById('fDefaultModel' + cap).value.trim(),
                api_format: this._getCustomSelectValue('fApiFormat' + cap),
                reasoning_effort: this._getCustomSelectValue('fReasoningEffort' + cap),
                context_length: parseInt(this._getCustomSelectValue('fContextLength' + cap)) || 0,
            };
            return binding;
        };
        const apps = [];
        if (document.getElementById('fAppClaude').checked) {
            const b = readApp('claude');
            if (!b.endpoint) { this.toast('请填写 Claude Code 端点 URL', 'error'); return; }
            apps.push(b);
        }
        if (document.getElementById('fAppCodex').checked) {
            const b = readApp('codex');
            if (!b.endpoint) { this.toast('请填写 Codex 端点 URL', 'error'); return; }
            apps.push(b);
        }
        if (!apps.length) { this.toast('请至少勾选一个应用', 'error'); return; }

        const data = { name, apps, category, notes };
        if (apiKey) data.api_key = apiKey;

        try {
            const backend = this.backend();
            let result;
            if (id) {
                result = await backend.update_provider(id, data);
            } else {
                result = await backend.add_provider(data);
            }
            if (result?.success) {
                this.closeProviderModal();
                this.toast(id ? '已更新' : '已添加', 'success');
                await this.loadData(false);
            } else {
                this.toast(result?.error || '保存失败', 'error');
            }
        } catch (e) {
            this.toast('保存失败: ' + e.message, 'error');
        }
    }

    /* --------------------------- Fetch Models ---------------------------- */
    async fetchModels(app) {
        const appType = app === 'codex' ? 'codex' : 'claude';
        const cap = appType === 'codex' ? 'Codex' : 'Claude';
        const endpoint = document.getElementById('fEndpoint' + cap)?.value.trim();
        const apiKey = document.getElementById('fApiKey')?.value.trim();
        const inputModel = document.getElementById('fDefaultModel' + cap)?.value.trim();
        const apiFormat = this._getCustomSelectValue('fApiFormat' + cap);
        const dropdown = document.getElementById('modelDropdown' + cap);
        const btn = document.querySelector(`.btn-fetch-models[data-app="${appType}"]`);

        if (!endpoint) {
            this.toast('请先填写端点 URL', 'error');
            return;
        }

        // Loading state
        if (btn) btn.classList.add('loading');
        if (dropdown) {
            dropdown.style.display = 'block';
            dropdown.innerHTML = '<div class="model-dropdown-empty">获取中...</div>';
        }

        try {
            const r = await this.backend().fetch_models(endpoint, apiKey || '', inputModel || '', apiFormat || '');
            if (btn) btn.classList.remove('loading');

            if (r?.from_fallback && r.models && r.models.length > 0) {
                // 端点不支持 /models 但返回了候选列表
                this._showModelDropdown(r.models, appType);
                this.toast(`端点未提供列表，已加载候选模型`, 'info');
            } else if (r?.success && r.models && r.models.length > 0) {
                this._showModelDropdown(r.models, appType);
                this.toast(`获取到 ${r.models.length} 个模型`, 'success');
            } else if (r?.no_models_endpoint) {
                // 端点不支持模型列表 — 在 dropdown 内友好提示，不弹错误 toast
                if (dropdown) {
                    dropdown.style.display = 'block';
                    dropdown.innerHTML = `<div class="model-dropdown-empty">${this.escape(r.error || '该端点不支持模型列表，请手动填写')}</div>`;
                }
            } else {
                if (dropdown) {
                    dropdown.style.display = 'block';
                    dropdown.innerHTML = `<div class="model-dropdown-empty">${this.escape(r?.error || '未找到模型')}</div>`;
                }
                this.toast(r?.error || '获取模型失败', 'error');
            }
        } catch (e) {
            if (btn) btn.classList.remove('loading');
            if (dropdown) {
                dropdown.style.display = 'block';
                dropdown.innerHTML = `<div class="model-dropdown-empty">${this.escape(e.message)}</div>`;
            }
            this.toast('获取模型失败: ' + e.message, 'error');
        }
    }

    _showModelDropdown(models, app) {
        const cap = app === 'codex' ? 'Codex' : 'Claude';
        const dropdown = document.getElementById('modelDropdown' + cap);
        if (!dropdown) return;

        dropdown.innerHTML = '';
        dropdown.style.display = 'block';

        models.forEach(name => {
            const item = document.createElement('div');
            item.className = 'model-dropdown-item';
            item.textContent = name;
            item.addEventListener('click', () => {
                const input = document.getElementById('fDefaultModel' + cap);
                if (input) input.value = name;
                dropdown.style.display = 'none';
            });
            dropdown.appendChild(item);
        });
    }

    /* ------------------------ Custom Confirm Dialog ----------------------- */
    showConfirm(message, title, okLabel, type) {
        return new Promise((resolve) => {
            const modal = document.getElementById('confirmModal');
            const msgEl = document.getElementById('confirmMessage');
            const titleEl = document.getElementById('confirmTitle');
            const okBtn = document.getElementById('confirmOkBtn');
            if (titleEl) titleEl.textContent = title || '确认操作';
            if (msgEl) msgEl.textContent = message;
            if (okBtn) {
                okBtn.textContent = okLabel || '确认删除';
                okBtn.className = (type === 'primary') ? 'btn-primary' : 'btn-danger';
            }
            this._confirmResolve = resolve;
            if (modal) modal.classList.add('show');
        });
    }

    _closeConfirm(result) {
        const modal = document.getElementById('confirmModal');
        if (modal) modal.classList.remove('show');
        if (this._confirmResolve) {
            this._confirmResolve(result);
            this._confirmResolve = null;
        }
    }

    /* --------------------------- Set Current ------------------------------ */
    async setCurrentProvider() {
        const p = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (!p) return;
        const app = this.activeApp;
        const appLabel = app === 'codex' ? 'Codex' : 'Claude Code';
        try {
            const r = await this.backend().set_current_provider(p.id, app);
            if (r?.success) {
                await this.loadData(false);
                this.selectProvider(p.id);
                this.toast(`${p.name} 已设为${appLabel}当前配置`, 'success');
                this.pushLog('info', `已将 ${p.name} 设为 ${appLabel} 当前配置`);
            } else {
                this.toast(r?.error || '设置失败', 'error');
            }
        } catch (e) {
            this.toast('设置失败: ' + e.message, 'error');
        }
    }

    async deleteSelected() {
        const p = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (!p) return;
        const ok = await this.showConfirm(
            `确定要删除「${p.name}」吗？此操作不可撤销。`,
            '删除提供商',
            '确认删除'
        );
        if (!ok) return;
        try {
            const r = await this.backend().delete_provider(p.id);
            if (r?.success) {
                this.selectedProviderId = null;
                this.clearProviderDetail();
                await this.loadData(false);
                this.toast('已删除', 'success');
            } else {
                this.toast(r?.error || '删除失败', 'error');
            }
        } catch (e) {
            this.toast('删除失败: ' + e.message, 'error');
        }
    }

    // ────────────────── 批量操作 ──────────────────

    _handleCheckClick(e, id, index) {
        const strId = String(id);
        if (e.shiftKey && this._lastClickedIndex !== null) {
            const start = Math.min(this._lastClickedIndex, index);
            const end = Math.max(this._lastClickedIndex, index);
            const rows = document.querySelectorAll('#providerTableBody tr');
            for (let i = start; i <= end; i++) {
                const rowId = rows[i]?.dataset.id;
                if (rowId) {
                    if (e.target.checked) this.selectedIds.add(String(rowId));
                    else this.selectedIds.delete(String(rowId));
                    const cb = rows[i].querySelector('.row-check');
                    if (cb) cb.checked = e.target.checked;
                }
            }
        } else {
            if (e.target.checked) this.selectedIds.add(strId);
            else this.selectedIds.delete(strId);
        }
        this._lastClickedIndex = index;
        this._updateBatchBar();
    }

    _syncSelectionModeUI() {
        const card = document.querySelector('.provider-list-card');
        const btn = document.getElementById('selectModeBtn');
        if (card) card.classList.toggle('selection-mode', this._selectionMode);
        if (btn) btn.classList.toggle('active', this._selectionMode);
    }

    _updateBatchBar() {
        const bar = document.getElementById('batchBar');
        const count = this.selectedIds.size;
        if (bar) bar.style.display = (this._selectionMode || count > 0) ? 'flex' : 'none';
        const countEl = document.getElementById('batchCount');
        if (countEl) countEl.textContent = `SEL ${String(count).padStart(2, '0')}`;
        // 更新全选复选框状态
        const selectAll = document.getElementById('selectAllCheck');
        if (selectAll) {
            const allChecks = document.querySelectorAll('.row-check');
            selectAll.checked = allChecks.length > 0 && count === allChecks.length;
            selectAll.indeterminate = count > 0 && count < allChecks.length;
        }
    }

    toggleSelectAll(e) {
        const checks = document.querySelectorAll('.row-check');
        checks.forEach(cb => {
            cb.checked = e.target.checked;
            const id = cb.dataset.id;
            if (e.target.checked) this.selectedIds.add(String(id));
            else this.selectedIds.delete(String(id));
        });
        this._updateBatchBar();
    }

    toggleSelectionMode() {
        this._selectionMode = !this._selectionMode;
        if (!this._selectionMode) {
            // Exiting selection mode: clear all selections
            this.selectedIds.clear();
            this._lastClickedIndex = null;
            document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
            const selectAll = document.getElementById('selectAllCheck');
            if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
        }
        this._syncSelectionModeUI();
        this._updateBatchBar();
    }

    clearSelection() {
        this.selectedIds.clear();
        this._lastClickedIndex = null;
        document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
        const selectAll = document.getElementById('selectAllCheck');
        if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
        // Exit selection mode
        this._selectionMode = false;
        this._syncSelectionModeUI();
        this._updateBatchBar();
    }

    async batchTest() {
        const ids = [...this.selectedIds];
        if (!ids.length) return;
        this.setTestingState(true);
        this.pushLog('info', `开始批量测试 ${ids.length} 个提供商...`);
        for (const id of ids) {
            if (!this.isTesting) break;
            // pushLog 第三参是供应商名（CLI 页签按名称过滤日志）
            const pName = this.providers.find(p => String(p.id) === String(id))?.name || '';
            try {
                const r = await this.backend().test_provider(id);
                if (r?.success) {
                    this.pushLog('ok', `OK · ${r.latency}ms · ${r.detail || ''}`, pName);
                } else {
                    this.pushLog('err', `失败 · ${r?.detail || '未知错误'}`, pName);
                }
            } catch (e) {
                this.pushLog('err', `异常: ${e.message}`, pName);
            }
        }
        this.setTestingState(false);
        this.clearSelection();
        await this.loadData(false);
        this.toast('批量测试完成', 'success');
    }

    async batchDelete() {
        const ids = [...this.selectedIds];
        if (!ids.length) return;
        const ok = await this.showConfirm(
            `确定要删除选中的 ${ids.length} 个提供商吗？此操作不可撤销。`,
            '批量删除',
            '确认删除'
        );
        if (!ok) return;
        let deleted = 0;
        for (const id of ids) {
            try {
                const r = await this.backend().delete_provider(parseInt(id));
                if (r?.success) deleted++;
            } catch (e) { /* continue */ }
        }
        this.clearSelection();
        await this.loadData(false);
        this.toast(`已删除 ${deleted} 个`, 'success');
    }

    async batchSetBackup() {
        const ids = [...this.selectedIds];
        if (!ids.length) return;
        for (const id of ids) {
            try {
                await this.backend().update_provider(parseInt(id), { role: '备用' });
            } catch (e) { /* continue */ }
        }
        this.clearSelection();
        await this.loadData(false);
        this.toast(`已将 ${ids.length} 个设为备用`, 'success');
    }

    async testSelected() {
        const p = this.providers.find(p => String(p.id) === String(this.selectedProviderId));
        if (!p) return;
        this.pushLog('info', `开始完整测试 · 发送 你是谁呀，小朋友 验证 AI 回复...`, p.name);
        try {
            const r = await this.backend().test_provider(p.id);
            if (r?.success) {
                this.pushLog('ok', `OK · 延迟 ${r.latency}ms · ${r.detail || ''}`, p.name);
                this.toast(`${p.name} 正常`, 'success');
            } else {
                this.pushLog('err', `失败 · ${r?.detail || r?.error || '未知错误'}`, p.name);
                this.toast(`${p.name} 失败`, 'error');
            }
            await this.loadData(false);
            this.selectProvider(p.id);
        } catch (e) {
            this.pushLog('err', `异常 · ${e.message}`, p.name);
            this.toast('测试异常', 'error');
        }
    }

    /* ---------------------------- About modal -------------------------- */
    async openAboutModal() {
        const body = document.getElementById('aboutBody');
        body.innerHTML = '<p>加载中...</p>';
        document.getElementById('aboutModal').classList.add('show');
        try {
            let info;
            const backend = this.backend();
            if (backend.get_about_info) {
                info = await backend.get_about_info();
            } else {
                info = {
                    name: 'API Monitor',
                    version: '2.0.0',
                    description: '实时监控 API 提供商状态和服务质量',
                };
            }
            body.innerHTML = `
                <div class="about-name">${this.escape(info.name || '')}</div>
                <div class="about-version">v${this.escape(info.version || '')}</div>
                <p>${this.escape(info.description || '')}</p>
                <div class="about-row"><span>版本</span><span>${this.escape(info.version || '-')}</span></div>
                <div class="about-row"><span>作者</span><span>${this.escape(info.author || '-')}</span></div>
                <div class="about-row"><span>仓库</span><span>${this.escape(info.repo || '-')}</span></div>
            `;
        } catch (e) {
            body.innerHTML = `<p>加载失败: ${this.escape(e.message)}</p>`;
        }
    }

    closeAboutModal() {
        document.getElementById('aboutModal').classList.remove('show');
    }

    async launchClaude() {
        try {
            const r = await this.backend().launch_claude();
            if (r?.success) this.toast('Claude Code 已启动', 'success');
            else this.toast(r?.error || '启动失败', 'error');
        } catch (error) {
            console.error('Launch failed:', error);
            this.toast('启动失败: ' + error.message, 'error');
        }
    }

    async launchCodex() {
        try {
            const r = await this.backend().launch_codex();
            if (r?.success) this.toast('Codex CLI 已启动', 'success');
            else this.toast(r?.error || '启动失败', 'error');
        } catch (error) {
            console.error('Launch Codex failed:', error);
            this.toast('启动失败: ' + error.message, 'error');
        }
    }

    async launchChatGPTDesktop() {
        try {
            const r = await this.backend().launch_chatgpt_desktop();
            if (r?.success) this.toast('ChatGPT 桌面版已启动', 'success');
            else this.toast(r?.error || '启动失败', 'error');
        } catch (error) {
            console.error('Launch ChatGPT desktop failed:', error);
            this.toast('启动失败: ' + error.message, 'error');
        }
    }

    toggleMenu(id, e) {
        e?.stopPropagation();
        document.querySelectorAll('.menu-dropdown').forEach(dd => {
            if (dd.id !== id) dd.classList.remove('show');
        });
        const dd = document.getElementById(id);
        if (dd) dd.classList.toggle('show');
    }

    /* ========================= Notifications ========================= */

    async toggleNotifDropdown() {
        const dd = document.getElementById('notifDropdown');
        if (!dd) return;
        const isOpen = dd.style.display !== 'none';
        if (isOpen) {
            dd.style.display = 'none';
        } else {
            dd.style.display = 'flex';
            await this.loadNotifications();
        }
    }

    async loadNotifications() {
        try {
            const r = await this.backend().get_notifications(50, false);
            if (r?.success) this.renderNotifications(r.data);
        } catch {}
    }

    renderNotifications(notifs) {
        const list = document.getElementById('notifList');
        if (!list) return;
        if (!notifs || !notifs.length) {
            list.innerHTML = '<div class="notif-empty">暂无通知</div>';
            return;
        }
        list.innerHTML = notifs.map(n => {
            const unread = n.is_read === 0 ? ' unread' : '';
            const typeLabel = n.type === 'failover' ? '故障切换' : n.type === 'status_change' ? '状态变化' : '测试完成';
            const ts = n.created_at ? new Date(n.created_at * 1000) : null;
            const timeStr = ts ? `${String(ts.getHours()).padStart(2,'0')}:${String(ts.getMinutes()).padStart(2,'0')}` : '';
            return `<div class="notif-item${unread}" data-id="${n.id}">
                <div class="notif-type ${n.type}">${typeLabel}</div>
                <div class="notif-msg">${this.escape(n.title)}: ${this.escape(n.message)}</div>
                <div class="notif-time">${timeStr}</div>
            </div>`;
        }).join('');
    }

    updateUnreadCount(count) {
        const badge = document.getElementById('notifBadge');
        if (!badge) return;
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : count;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    }

    async markAllRead() {
        try {
            await this.backend().mark_notifications_read(null);
            this.updateUnreadCount(0);
            await this.loadNotifications();
        } catch {}
    }

    /* ========================= Settings ========================= */

    async openSettings() {
        document.getElementById('settingsModal').classList.add('show');
        try {
            const r = await this.backend().get_all_settings();
            if (r?.success) this._populateSettings(r.data);
        } catch {}
    }

    closeSettings() {
        document.getElementById('settingsModal').classList.remove('show');
    }

    _populateSettings(s) {
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
        const setCheck = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            const on = val === '1' || val === 'true' || val === true || val === 1;
            el.checked = on;
        };
        set('setAutoInterval', s.auto_test_interval || '0');
        set('setConcurrency', s.test_concurrency || '3');
        set('setTimeout', s.test_timeout || '30');
        set('setRetries', s.test_retries || '2');
        setCheck('setSslVerify', s.ssl_verify);
        setCheck('setAutoSyncClaude', s.auto_sync_claude_on_startup);
        setCheck('setAutoSyncCodex', s.auto_sync_codex_on_startup);
        setCheck('setFailoverEnabled', s.failover_enabled);
        setCheck('setFailoverConfirm', s.failover_need_confirm);
        set('setMaxSwitches', s.failover_max_switches || '3');
        set('setFailoverCooldown', s.failover_cooldown || '300');
        setCheck('setNotifyStatus', s.notify_status_change);
        setCheck('setNotifyFailover', s.notify_failover);
        setCheck('setNotifyComplete', s.notify_test_complete);
        set('setWebhookUrl', s.webhook_url || '');
        set('setRetentionDays', s.history_retention_days || '30');
        setCheck('setAutoBackup', s.auto_backup_enabled);
        setCheck('setStartOnBoot', s.start_on_boot);
        // 默认开启关窗到托盘
        const minTray = s.minimize_to_tray;
        setCheck('setMinimizeToTray', minTray === undefined || minTray === null || minTray === '' ? '1' : minTray);
        // Scene backgrounds: prefer backend values, else localStorage defaults
        const scene = this.loadSceneBackgrounds();
        set('setBgRate', s.bg_rate || scene.rate || '');
        set('setBgNodes', s.bg_nodes || scene.nodes || '');
        set('setBgOk', s.bg_ok || scene.ok || '');
        set('setBgErr', s.bg_err || scene.err || '');
        set('setBgDetail', s.bg_detail || scene.detail || '');
        this.refreshBackupList();
    }

    _gatherSettings() {
        const get = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
        const getCheck = (id) => { const el = document.getElementById(id); return el?.checked ? '1' : '0'; };
        return {
            auto_test_interval: get('setAutoInterval'),
            test_concurrency: get('setConcurrency'),
            test_timeout: get('setTimeout'),
            test_retries: get('setRetries'),
            ssl_verify: getCheck('setSslVerify'),
            auto_sync_claude_on_startup: getCheck('setAutoSyncClaude'),
            auto_sync_codex_on_startup: getCheck('setAutoSyncCodex'),
            failover_enabled: getCheck('setFailoverEnabled'),
            failover_need_confirm: getCheck('setFailoverConfirm'),
            failover_max_switches: get('setMaxSwitches'),
            failover_cooldown: get('setFailoverCooldown'),
            notify_status_change: getCheck('setNotifyStatus'),
            notify_failover: getCheck('setNotifyFailover'),
            notify_test_complete: getCheck('setNotifyComplete'),
            webhook_url: get('setWebhookUrl'),
            history_retention_days: get('setRetentionDays'),
            auto_backup_enabled: getCheck('setAutoBackup'),
            start_on_boot: getCheck('setStartOnBoot'),
            minimize_to_tray: (document.getElementById('setMinimizeToTray') ? getCheck('setMinimizeToTray') : '1'),
            bg_rate: get('setBgRate'),
            bg_nodes: get('setBgNodes'),
            bg_ok: get('setBgOk'),
            bg_err: get('setBgErr'),
            bg_detail: get('setBgDetail'),
        };
    }

    async saveSettings() {
        const data = this._gatherSettings();
        // Always persist scene BGs locally so browser/mock and desktop both work
        this.saveSceneBackgrounds(this._readSceneBackgroundInputs());
        this.applySceneBackgrounds(this.loadSceneBackgrounds());
        try {
            const r = await this.backend().save_settings(data);
            if (r?.success) {
                this.closeSettings();
                this.toast('CONFIG SAVED', 'success');
                // Refresh scheduler status
                this._refreshSchedulerStatus();
            } else {
                this.toast(r?.error || 'SAVE FAILED', 'error');
            }
        } catch (e) {
            this.toast('SAVE FAILED: ' + e.message, 'error');
        }
    }

    /* ========================= Scene backgrounds ========================= */

    defaultSceneBackgrounds() {
        // Relative to index.html (works for pywebview local file + http.server)
        return {
            rate: 'assets/metric-bg/rate.gif',
            nodes: 'assets/metric-bg/nodes.gif',
            ok: 'assets/metric-bg/ok.gif',
            err: 'assets/metric-bg/err.gif',
            detail: 'assets/metric-bg/detail.gif',
        };
    }

    async _applyBackendSceneBackgrounds() {
        try {
            const r = await this.backend().get_all_settings();
            const s = r?.success ? r.data : null;
            if (!s) return;
            const hasAny = s.bg_rate || s.bg_nodes || s.bg_ok || s.bg_err || s.bg_detail;
            if (!hasAny) return;
            const defaults = this.defaultSceneBackgrounds();
            const scene = {
                rate: s.bg_rate || defaults.rate,
                nodes: s.bg_nodes || defaults.nodes,
                ok: s.bg_ok || defaults.ok,
                err: s.bg_err || defaults.err,
                detail: s.bg_detail || defaults.detail,
            };
            this.saveSceneBackgrounds(scene);
            this.applySceneBackgrounds(scene);
        } catch { /* 后端不可用时保持 localStorage 值 */ }
    }

    loadSceneBackgrounds() {
        const defaults = this.defaultSceneBackgrounds();
        try {
            const raw = localStorage.getItem('am-scene-bg');
            if (!raw) return { ...defaults };
            const parsed = JSON.parse(raw) || {};
            const fix = (v, fallback) => {
                let s = (v || '').trim();
                if (!s) return fallback;
                // migrate root-absolute paths from earlier builds
                if (s.startsWith('/assets/')) s = s.slice(1);
                return s;
            };
            return {
                rate: fix(parsed.rate, defaults.rate),
                nodes: fix(parsed.nodes, defaults.nodes),
                ok: fix(parsed.ok, defaults.ok),
                err: fix(parsed.err, defaults.err),
                detail: fix(parsed.detail, defaults.detail),
            };
        } catch {
            return { ...defaults };
        }
    }

    saveSceneBackgrounds(scene) {
        try {
            localStorage.setItem('am-scene-bg', JSON.stringify(scene || {}));
        } catch {}
    }

    _readSceneBackgroundInputs() {
        const get = (id) => {
            const el = document.getElementById(id);
            return el ? el.value.trim() : '';
        };
        const defaults = this.defaultSceneBackgrounds();
        const fix = (v, fallback) => {
            let s = (v || '').trim();
            if (!s) return fallback;
            if (s.startsWith('/assets/')) s = s.slice(1);
            return s;
        };
        return {
            rate: fix(get('setBgRate'), defaults.rate),
            nodes: fix(get('setBgNodes'), defaults.nodes),
            ok: fix(get('setBgOk'), defaults.ok),
            err: fix(get('setBgErr'), defaults.err),
            detail: fix(get('setBgDetail'), defaults.detail),
        };
    }

    resetSceneBackgrounds() {
        const defaults = this.defaultSceneBackgrounds();
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.value = val;
        };
        set('setBgRate', defaults.rate);
        set('setBgNodes', defaults.nodes);
        set('setBgOk', defaults.ok);
        set('setBgErr', defaults.err);
        set('setBgDetail', defaults.detail);
        this.saveSceneBackgrounds(defaults);
        this.applySceneBackgrounds(defaults);
        this.toast('SCENE RESET', 'success');
    }

    _resolveAssetUrl(path) {
        if (!path) return '';
        let clean = String(path).trim();
        while (clean.indexOf(String.fromCharCode(92)) !== -1) {
            clean = clean.replace(String.fromCharCode(92), '/');
        }
        if (!clean) return '';
        if (/^(https?:|data:|file:)/i.test(clean)) return clean;
        try {
            if (clean.charAt(0) === '/') clean = clean.slice(1);
            return new URL(clean, window.location.href).href;
        } catch {
            return clean;
        }
    }

    applySceneBackgrounds(scene) {
        const s = scene || this.loadSceneBackgrounds();
        const root = document.documentElement;
        const cssUrl = (path) => {
            const abs = this._resolveAssetUrl(path);
            if (!abs) return 'none';
            return `url("${abs.replace(/"/g, '\\"')}")`;
        };
        // CSS variables
        root.style.setProperty('--bg-rate', cssUrl(s.rate));
        root.style.setProperty('--bg-nodes', cssUrl(s.nodes));
        root.style.setProperty('--bg-ok', cssUrl(s.ok));
        root.style.setProperty('--bg-err', cssUrl(s.err));
        root.style.setProperty('--bg-detail', cssUrl(s.detail));

        // Direct element styles — most reliable across pywebview/file/http
        const map = {
            rate: s.rate,
            nodes: s.nodes,
            ok: s.ok,
            err: s.err,
        };
        document.querySelectorAll('.metric-scene[data-scene]').forEach((el) => {
            const key = el.getAttribute('data-scene');
            const url = this._resolveAssetUrl(map[key]);
            el.style.backgroundImage = url ? `url("${url.replace(/"/g, '\\"')}")` : '';
        });
        const stage = document.querySelector('.empty-stage');
        if (stage) {
            const url = this._resolveAssetUrl(s.detail);
            stage.style.backgroundImage = url ? `url("${url.replace(/"/g, '\\"')}")` : '';
        }
    }

    /* ========================= Trend Chart ========================= */

    _trendRange = 24;  // hours

    async renderTrendTab() {
        const detailInfo = document.getElementById('detailInfo');
        if (!detailInfo || !this.selectedProviderId) return;

        detailInfo.innerHTML = `
            <div class="trend-container">
                <div class="trend-header">
                    <span style="font-size:11px;font-weight:800;">延迟趋势</span>
                    <div class="trend-range-btns">
                        <button class="trend-range-btn${this._trendRange===24?' active':''}" data-range="24">24H</button>
                        <button class="trend-range-btn${this._trendRange===168?' active':''}" data-range="168">7D</button>
                        <button class="trend-range-btn${this._trendRange===720?' active':''}" data-range="720">30D</button>
                    </div>
                </div>
                <div class="trend-canvas-wrap">
                    <canvas id="trendCanvas" width="600" height="160"></canvas>
                </div>
                <div class="trend-stats" id="trendStats"></div>
            </div>
        `;

        // Bind range buttons
        detailInfo.querySelectorAll('.trend-range-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this._trendRange = parseInt(e.currentTarget.dataset.range);
                this.renderTrendTab();
            });
        });

        // Load data
        try {
            const [statsR, timelineR] = await Promise.all([
                this.backend().get_history_stats(this.selectedProviderId, this._trendRange),
                this.backend().get_history_timeline(this.selectedProviderId, this._trendRange),
            ]);

            if (statsR?.success) this._renderTrendStats(statsR.data);
            if (timelineR?.success) this._drawTrendChart(timelineR.data);
        } catch {}
    }

    _renderTrendStats(stats) {
        const el = document.getElementById('trendStats');
        if (!el) return;
        const avg = stats.avg_latency != null ? `${stats.avg_latency}ms` : '-';
        const p95 = stats.p95_latency != null ? `${stats.p95_latency}ms` : '-';
        const avail = stats.availability != null ? `${stats.availability}%` : '-';
        const availClass = stats.availability >= 99 ? 'good' : stats.availability >= 95 ? 'medium' : 'bad';
        el.innerHTML = `
            <div class="trend-stat-card">
                <div class="trend-stat-value">${avg}</div>
                <div class="trend-stat-label">平均延迟</div>
            </div>
            <div class="trend-stat-card">
                <div class="trend-stat-value">${p95}</div>
                <div class="trend-stat-label">P95 延迟</div>
            </div>
            <div class="trend-stat-card">
                <div class="trend-stat-value ${availClass}">${avail}</div>
                <div class="trend-stat-label">可用率</div>
            </div>
            <div class="trend-stat-card">
                <div class="trend-stat-value">${stats.total || 0}</div>
                <div class="trend-stat-label">测试次数</div>
            </div>
        `;
    }

    _drawTrendChart(timeline) {
        const canvas = document.getElementById('trendCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        // 按容器实际宽度 × DPR 设置位图尺寸，避免 CSS 拉伸导致线条发虚
        const dpr = window.devicePixelRatio || 1;
        const cssW = canvas.parentElement?.clientWidth || canvas.clientWidth || 600;
        const cssH = 160;
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        canvas.style.width = cssW + 'px';
        canvas.style.height = cssH + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        const W = cssW;
        const H = cssH;

        ctx.clearRect(0, 0, W, H);

        if (!timeline || !timeline.length) {
            ctx.fillStyle = '#a0aec0';
            ctx.font = '11px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('暂无数据，执行测试后这里会显示延迟趋势', W / 2, H / 2);
            return;
        }

        const pad = { top: 16, right: 12, bottom: 24, left: 44 };
        const cw = W - pad.left - pad.right;
        const ch = H - pad.top - pad.bottom;

        const values = timeline.map(t => t.avg);
        const maxVal = Math.max(...values, 100);
        const minVal = 0;

        // Grid lines
        ctx.strokeStyle = '#edf2f7';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad.top + (ch / 4) * i;
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(W - pad.right, y);
            ctx.stroke();
            // Labels
            const val = Math.round(maxVal - (maxVal / 4) * i);
            ctx.fillStyle = '#a0aec0';
            ctx.font = '9px monospace';
            ctx.textAlign = 'right';
            ctx.fillText(`${val}`, pad.left - 4, y + 3);
        }

        // Line chart
        if (values.length > 1) {
            ctx.beginPath();
            ctx.strokeStyle = '#4299e1';
            ctx.lineWidth = 2;
            ctx.lineJoin = 'round';
            values.forEach((v, i) => {
                const x = pad.left + (cw / (values.length - 1)) * i;
                const y = pad.top + ch - ((v - minVal) / (maxVal - minVal)) * ch;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.stroke();

            // Fill area
            ctx.lineTo(pad.left + cw, pad.top + ch);
            ctx.lineTo(pad.left, pad.top + ch);
            ctx.closePath();
            ctx.fillStyle = 'rgba(66,153,225,0.08)';
            ctx.fill();

            // Dots
            values.forEach((v, i) => {
                const x = pad.left + (cw / (values.length - 1)) * i;
                const y = pad.top + ch - ((v - minVal) / (maxVal - minVal)) * ch;
                ctx.beginPath();
                ctx.arc(x, y, 2.5, 0, Math.PI * 2);
                ctx.fillStyle = '#4299e1';
                ctx.fill();
                ctx.strokeStyle = '#f0f4f8';
                ctx.lineWidth = 1.5;
                ctx.stroke();
            });
        } else {
            // Single point
            const x = pad.left + cw / 2;
            const y = pad.top + ch - ((values[0] - minVal) / (maxVal - minVal)) * ch;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fillStyle = '#4299e1';
            ctx.fill();
            ctx.strokeStyle = '#f0f4f8';
            ctx.lineWidth = 2;
            ctx.stroke();
        }

        // Time labels
        if (timeline.length >= 2) {
            const first = new Date(timeline[0].time * 1000);
            const last = new Date(timeline[timeline.length - 1].time * 1000);
            const fmt = (d) => `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
            ctx.fillStyle = '#a0aec0';
            ctx.font = '9px monospace';
            ctx.textAlign = 'left';
            ctx.fillText(fmt(first), pad.left, H - 4);
            ctx.textAlign = 'right';
            ctx.fillText(fmt(last), W - pad.right, H - 4);
        }
    }

    /* ========================= Scheduler ========================= */

    updateSchedulerStatus(status) {
        if (!status) return;
        // 后端只在状态变化时推送；倒计时由本地每秒根据 next_run 自减渲染
        this._schedulerState = status;
        this._renderSchedulerChip();
        if (status.running && status.interval > 0) {
            if (!this._schedulerTicker) {
                this._schedulerTicker = setInterval(() => {
                    if (!document.hidden) this._renderSchedulerChip();
                }, 1000);
            }
        } else if (this._schedulerTicker) {
            clearInterval(this._schedulerTicker);
            this._schedulerTicker = null;
        }
        if (status.failover) this.updateFailoverStatus(status.failover);
    }

    _renderSchedulerChip() {
        const status = this._schedulerState;
        if (!status) return;
        const chip = document.getElementById('statusScheduler');
        const val = document.getElementById('statusSchedulerVal') || chip;
        if (!chip || !val) return;
        if (status.running && status.interval > 0) {
            // 优先用 next_run 现算，避免依赖推送时刻的 remaining 快照
            const rem = status.next_run
                ? Math.max(0, Math.floor(status.next_run - Date.now() / 1000))
                : Math.max(0, parseInt(status.remaining || 0, 10));
            const mm = String(Math.floor(rem / 60)).padStart(2, '0');
            const ss = String(rem % 60).padStart(2, '0');
            const intervalMin = Math.max(1, Math.round((status.interval || 0) / 60));
            val.textContent = `${mm}:${ss} /${intervalMin}M`;
            chip.classList.add('is-running');
            chip.classList.remove('is-off');
            chip.title = `定时测试：${mm}:${ss} 后下一次 · 每 ${intervalMin} 分钟`;
        } else {
            val.textContent = 'OFF';
            chip.classList.remove('is-running');
            chip.classList.add('is-off');
            chip.title = '定时测试：关闭';
        }
    }

    updateFailoverStatus(fo) {
        const chip = document.getElementById('statusFailover');
        const val = document.getElementById('statusFailoverVal') || chip;
        if (!chip || !val || !fo) return;
        if (!fo.enabled) {
            val.textContent = 'OFF';
            chip.classList.remove('is-warn', 'is-ok');
            chip.classList.add('is-off');
            chip.title = '故障切换：关闭';
            this.hideFailoverBanner();
            return;
        }
        const used = fo.consecutive_switches || 0;
        const max = fo.max_switches || 3;
        const cool = fo.cooldown_remaining || 0;
        let msg = `${used}/${max}`;
        if (cool > 0) msg += ` CD ${cool}S`;
        if (fo.pending_confirm) msg += ' WAIT';
        val.textContent = msg;
        chip.classList.remove('is-off');
        chip.classList.toggle('is-warn', !!fo.pending_confirm || cool > 0);
        chip.classList.toggle('is-ok', !fo.pending_confirm && cool <= 0);
        chip.title = fo.pending_confirm
            ? '故障切换：待确认'
            : (cool > 0 ? `故障切换：冷却 ${cool}s` : `故障切换：${used}/${max}`);
        if (fo.pending_confirm) {
            const p = fo.pending_confirm;
            this.showFailoverBanner(`建议切换到 ${p.to || p.provider_name || '备用供应商'}（当前 ${p.from || ''} 故障）`);
        } else {
            this.hideFailoverBanner();
        }
    }

    showFailoverBanner(msg) {
        const banner = document.getElementById('failoverBanner');
        const label = document.getElementById('failoverBannerText');
        if (label) label.textContent = msg || '建议故障切换';
        if (banner) banner.style.display = 'flex';
    }

    hideFailoverBanner() {
        const banner = document.getElementById('failoverBanner');
        if (banner) banner.style.display = 'none';
    }

    async confirmFailover() {
        try {
            const r = await this.backend().confirm_failover();
            if (r?.switched) {
                this.toast(`已切换到 ${r.to}`, 'success');
                this.hideFailoverBanner();
                await this.loadData(false);
            } else {
                this.toast(r?.reason || '无待确认切换', 'error');
            }
        } catch (e) {
            this.toast('确认切换失败: ' + e.message, 'error');
        }
    }

    async cancelFailover() {
        try {
            await this.backend().cancel_failover();
            this.hideFailoverBanner();
            this.toast('已取消切换', 'success');
        } catch (e) {
            this.toast('取消失败: ' + e.message, 'error');
        }
    }

    onFailoverEvent(result) {
        if (!result) return;
        if (result.switched) {
            this.toast(`Failover: ${result.from} → ${result.to}`, 'error');
            this.hideFailoverBanner();
            this.loadData(false);
        } else if (result.need_confirm) {
            this.showFailoverBanner(`建议切换到 ${result.to || '备用供应商'}（${result.from || '当前'} 故障）`);
            this.toast('检测到故障，等待确认切换', 'error');
        }
        const api = this.backend();
        if (api.get_failover_status) {
            api.get_failover_status().then(s => this.updateFailoverStatus(s)).catch(() => {});
        }
    }

    async _refreshSchedulerStatus() {
        try {
            const r = await this.backend().get_scheduler_status();
            if (r?.success) this.updateSchedulerStatus(r.data);
        } catch {}
        try {
            const api = this.backend();
            if (api.get_failover_status) {
                const fo = await api.get_failover_status();
                if (fo) this.updateFailoverStatus(fo);
            }
        } catch {}
    }

    /* ========================= Export & Backup ========================= */

    async exportProviders(format) {
        try {
            const r = await this.backend().export_providers(format, false);
            if (r?.success) this.toast(`已导出 ${r.count} 个供应商`, 'success');
            else this.toast(r?.error || '导出失败', 'error');
        } catch (e) {
            this.toast('导出失败: ' + e.message, 'error');
        }
    }

    async exportHistoryCsv() {
        try {
            const pid = this.selectedProviderId || null;
            const r = await this.backend().export_history_csv(pid);
            if (r?.success) this.toast('历史已导出' + (r.path ? `: ${r.path}` : ''), 'success');
            else this.toast(r?.error || '导出失败', 'error');
        } catch (e) {
            this.toast('导出失败: ' + e.message, 'error');
        }
    }

    async refreshBackupList() {
        const box = document.getElementById('backupList');
        if (!box) return;
        try {
            const r = await this.backend().list_backups();
            const backups = (r && r.success ? (r.backups || r.data) : r) || [];
            if (!Array.isArray(backups) || backups.length === 0) {
                box.innerHTML = '<div class="backup-empty">暂无备份</div>';
                return;
            }
            box.innerHTML = backups.slice(0, 8).map(b => {
                const name = b.name || (b.path || '').split(/[/\\]/).pop() || 'backup';
                const size = b.size ? `${Math.round(b.size / 1024)} KB` : '';
                const p = String(b.path || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
                return `<div class="backup-item" data-path="${p}" title="${p}"><span>${name}</span><span>${size}</span></div>`;
            }).join('');
            box.querySelectorAll('.backup-item').forEach(el => {
                el.addEventListener('click', async () => {
                    const p = el.getAttribute('data-path') || '';
                    try {
                        await navigator.clipboard.writeText(p);
                        this.toast('已复制备份路径', 'success');
                    } catch (_) {
                        this.toast(p, 'success');
                    }
                });
            });
        } catch (e) {
            box.innerHTML = '<div class="backup-empty">无法读取备份列表</div>';
        }
    }

    async backupDb() {
        try {
            const r = await this.backend().create_backup();
            if (r?.success) {
                this.toast('备份成功', 'success');
                await this.refreshBackupList();
            } else this.toast(r?.error || '备份失败', 'error');
        } catch (e) {
            this.toast('备份失败: ' + e.message, 'error');
        }
    }

    async restoreDb() {
        const ok = await this.showConfirm('恢复将覆盖当前数据库，确定继续？', '恢复备份', '确认恢复');
        if (!ok) return;
        try {
            const r = await this.backend().restore_backup();
            if (r?.success) {
                this.toast('已恢复，正在刷新...', 'success');
                await this.loadData(false);
            } else {
                this.toast(r?.error || '恢复失败', 'error');
            }
        } catch (e) {
            this.toast('恢复失败: ' + e.message, 'error');
        }
    }

    _maskKey(k) {
        if (!k) return '-';
        k = String(k);
        if (k.length > 8) return k.slice(0, 4) + '••••••••' + k.slice(-4);
        return '****';
    }
}

/* ============================================================
   MockBackend - 浏览器内置模拟后端 (localStorage 持久化)
   提供 PyWebView API 同名方法,使无 pywebview 时所有功能仍可用
   ============================================================ */
class MockBackend {
    constructor() {
        this.key = 'provider_monitor_data_v2';
        this._stopFlag = false;
        const existing = this._load();
        if (!existing || !existing.length) {
            this._save(this._seed());
        }
    }

    _load() {
        try { return JSON.parse(localStorage.getItem(this.key) || '[]'); }
        catch { return []; }
    }
    _save(list) {
        try { localStorage.setItem(this.key, JSON.stringify(list)); } catch {}
    }

    _seed() {
        const seed = (p) => ({
            ...p,
            apps: [{ app_type: p.app_type, endpoint: p.endpoint, default_model: p.default_model || '', api_format: p.api_format || '', role: p.role }],
        });
        return [
            seed({ id: 1, name: "Lucky-api",  app_type: "claude", role: "当前", endpoint: "https://openrouter.ai/api/v1",     api_key: "sk-lucky001",    status: "ok",      latency: 128, test_detail: "正常",     default_model: "claude-haiku-4-5", category: "主用", notes: "" }),
            seed({ id: 2, name: "ShareGPT",     app_type: "claude", role: "备用", endpoint: "https://api.siliconflow.cn/v1",    api_key: "sk-share002",    status: "ok",      latency: 89,  test_detail: "正常",     default_model: "", category: "", notes: "" }),
            seed({ id: 3, name: "阿里",          app_type: "claude", role: "备用", endpoint: "https://dashscope.aliyun.com/v1",  api_key: "sk-ali003",      status: "fail",    latency: null, test_detail: "连接超时", default_model: "", category: "", notes: "" }),
            seed({ id: 4, name: "ThatAPI",       app_type: "claude", role: "备用", endpoint: "https://api.thatapi.com/v1",      api_key: "sk-that004",     status: "pending", latency: null, test_detail: "未测试",   default_model: "", category: "", notes: "" }),
            seed({ id: 5, name: "GUPI",          app_type: "claude", role: "备用", endpoint: "https://api.gupi.com/v1",         api_key: "sk-gupi005",     status: "pending", latency: null, test_detail: "未测试",   default_model: "", category: "", notes: "" }),
            seed({ id: 7, name: "DeepSeek",     app_type: "claude", role: "备用", endpoint: "https://api.deepseek.com/anthropic", api_key: "sk-ds007",     status: "ok",      latency: 156, test_detail: "正常",     default_model: "deepseek-chat", category: "", notes: "" }),
            seed({ id: 8, name: "Moonshot",     app_type: "claude", role: "备用", endpoint: "https://api.moonshot.cn/v1",      api_key: "sk-mk008",       status: "pending", latency: null, test_detail: "未测试",   default_model: "", category: "", notes: "" }),
            seed({ id: 9, name: "DeepSeek-Codex", app_type: "codex", role: "当前", endpoint: "https://api.deepseek.com/v1",   api_key: "sk-ds009",       status: "ok",      latency: 150, test_detail: "正常",     default_model: "deepseek-v4-flash", api_format: "openai_responses", category: "", notes: "" }),
        ];
    }

    _nextId(list) {
        return (list.reduce((m, p) => Math.max(m, parseInt(p.id) || 0), 0) || 0) + 1;
    }

    _maskKey(k) {
        if (!k) return '-';
        if (k.length > 8) return k.slice(0, 4) + '••••••••' + k.slice(-4);
        return '****';
    }

    _format(list) {
        return list.map(p => ({
            id: String(p.id),
            name: p.name || '',
            app_type: p.app_type || 'claude',
            role: p.role || '备用',
            endpoint: p.endpoint || '',
            key: this._maskKey(p.api_key),
            status: p.status || 'pending',
            latency: p.latency ? `${p.latency}ms` : '-',
            detail: p.test_detail || (p.status === 'ok' ? '正常' : p.status === 'fail' ? '连接失败' : '未测试'),
            default_model: p.default_model || '',
            api_format: p.api_format || '',
            category: p.category || '',
            notes: p.notes || '',
            apps: (Array.isArray(p.apps) && p.apps.length)
                ? p.apps.map(b => ({ app_type: b.app_type, endpoint: b.endpoint || '', default_model: b.default_model || '', api_format: b.api_format || '', role: b.role || '备用' }))
                : [],
        }));
    }

    _findRaw(id) {
        const list = this._load();
        return list.find(p => String(p.id) === String(id));
    }

    // ---- Public API (PyWebView compatible, all async) ----

    async get_providers() {
        return this._format(this._load());
    }

    async get_provider_key(id) {
        const p = this._findRaw(id);
        if (!p) return { success: false, error: '供应商不存在' };
        return { success: true, api_key: p.api_key || '' };
    }

    async get_stats() {
        const list = this._load();
        return {
            total: list.length,
            ok: list.filter(p => p.status === 'ok').length,
            fail: list.filter(p => p.status === 'fail').length,
            pending: list.filter(p => p.status !== 'ok' && p.status !== 'fail').length,
        };
    }

    async add_provider(data) {
        const list = this._load();
        const id = this._nextId(list);
        // apps → 顶层镜像字段
        let appType = data.app_type || 'claude';
        let role = data.role || '备用';
        let endpoint = data.endpoint || '';
        let defaultModel = data.default_model || '';
        let apiFormat = data.api_format || '';
        let apps = data.apps;
        if (Array.isArray(apps) && apps.length) {
            const claude = apps.find(b => b.app_type === 'claude');
            const codex = apps.find(b => b.app_type === 'codex');
            appType = (claude && codex) ? 'both' : (codex ? 'codex' : 'claude');
            const primary = claude || codex || apps[0];
            endpoint = (primary && primary.endpoint) || endpoint;
            defaultModel = (primary && primary.default_model) || defaultModel;
            apiFormat = (primary && primary.api_format) || apiFormat;
            apps = apps.map(b => ({ app_type: b.app_type, endpoint: b.endpoint || '', default_model: b.default_model || '', api_format: b.api_format || '', role: b.role || '备用' }));
        } else {
            apps = [{ app_type: appType, endpoint, default_model: defaultModel, api_format: apiFormat, role }];
        }
        list.push({
            id, name: data.name || '', app_type: appType, role, endpoint,
            api_key: data.api_key || '', website: data.website || '',
            category: data.category || '', notes: data.notes || '',
            default_model: defaultModel, api_format: apiFormat,
            status: 'pending', latency: null, test_detail: '未测试', apps,
        });
        this._save(list);
        return { success: true, id };
    }

    async update_provider(id, data) {
        const list = this._load();
        const p = list.find(x => String(x.id) === String(id));
        if (!p) return { success: false, error: '未找到' };
        for (const k of ['name','app_type','role','endpoint','api_key','website','category','notes','default_model','api_format']) {
            if (data[k] !== undefined) p[k] = data[k];
        }
        if (Array.isArray(data.apps)) {
            p.apps = data.apps.map(b => ({ app_type: b.app_type, endpoint: b.endpoint || '', default_model: b.default_model || '', api_format: b.api_format || '', role: b.role || '备用' }));
            const claude = p.apps.find(b => b.app_type === 'claude');
            const codex = p.apps.find(b => b.app_type === 'codex');
            p.app_type = (claude && codex) ? 'both' : (codex ? 'codex' : 'claude');
            const primary = claude || codex;
            if (primary) {
                p.endpoint = primary.endpoint;
                p.default_model = primary.default_model;
                p.api_format = primary.api_format;
            }
        }
        this._save(list);
        return { success: true };
    }

    async delete_provider(id) {
        let list = this._load();
        const before = list.length;
        list = list.filter(p => String(p.id) !== String(id));
        this._save(list);
        return { success: list.length < before };
    }

    async test_provider(id, mode = 'fast') {
        const list = this._load();
        const p = list.find(x => String(x.id) === String(id));
        if (!p) return { success: false, error: '供应商不存在' };
        // 模拟延迟
        await new Promise(r => setTimeout(r, 200 + Math.random() * 400));
        const ok = Math.random() > 0.25;
        if (ok) {
            const lat = 80 + Math.floor(Math.random() * 250);
            p.status = 'ok'; p.latency = lat; p.test_detail = '正常(模拟)';
            this._save(list);
            return { success: true, status: 'ok', latency: lat, detail: p.test_detail };
        } else {
            p.status = 'fail'; p.latency = null; p.test_detail = '连接超时(模拟)';
            this._save(list);
            return { success: false, status: 'fail', detail: p.test_detail };
        }
    }

    async test_all(mode = 'fast', callback) {
        const list = this._load();
        this._stopFlag = false;
        (async () => {
            for (const p of list) {
                if (this._stopFlag) break;
                await new Promise(r => setTimeout(r, 250 + Math.random() * 250));
                const ok = Math.random() > 0.25;
                if (ok) {
                    p.status = 'ok'; p.latency = 80 + Math.floor(Math.random()*250); p.test_detail = '正常(模拟)';
                } else {
                    p.status = 'fail'; p.latency = null; p.test_detail = '连接超时(模拟)';
                }
                if (callback) try { callback(p.id, p); } catch {}
                this._save(list);
                // 触发 app 刷新
                if (window.app && typeof window.app.loadData === 'function') {
                    try { window.app.loadData(); } catch {}
                }
            }
            if (window.app && typeof window.app.setTestingState === 'function') {
                try { window.app.setTestingState(false); } catch {}
            }
        })();
        return { success: true, message: `已开始${mode==='full'?'完整':'快速'}测试` };
    }

    async stop_testing() {
        this._stopFlag = true;
        return { success: true };
    }

    async fetch_models(endpoint, api_key, default_model, api_format = '') {
        // 浏览器预览:返回模拟模型列表
        if (!endpoint) return { success: false, error: '未提供端点 URL' };
        const modelsByFormat = {
            anthropic_messages: ['claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
            openai_chat: ['gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
            openai_responses: ['gpt-4o', 'gpt-4o-mini', 'o3-mini'],
        };
        return {
            success: true,
            models: modelsByFormat[api_format] || modelsByFormat.anthropic_messages,
        };
    }

    async sync_claude_code_provider() {
        // 浏览器预览:模拟读取本地 Claude 配置
        return {
            success: false,
            action: 'error',
            error: '浏览器预览模式无法读取 Claude Code 配置',
            message: '浏览器预览模式无法读取 Claude Code 配置,请运行桌面版 exe',
        };
    }

    async sync_codex_provider() {
        // 浏览器预览:模拟读取本地 Codex 配置
        return {
            success: false,
            action: 'error',
            error: '浏览器预览模式无法读取 Codex 配置',
            message: '浏览器预览模式无法读取 Codex 配置,请运行桌面版 exe',
        };
    }

    async import_from_ccswitch() {
        // 浏览器预览:没有 cc-switch 数据库,直接返回提示
        return { success: false, error: '浏览器预览模式无法从 cc-switch 导入', imported: 0, missing_db: true };
    }

    async choose_and_import_from_file() {
        // 浏览器无文件系统访问能力,明确告知需要桌面版
        return { success: false, error: '浏览器预览模式无法选择文件,请运行桌面版 exe' };
    }

    async launch_claude() {
        // 浏览器预览中无法启动外部进程
        return { success: false, error: '需要 PyWebView 后端才能启动 Claude Code' };
    }

    async launch_codex() {
        // 浏览器预览中无法启动外部进程
        return { success: false, error: '需要 PyWebView 后端才能启动 Codex CLI' };
    }

    async launch_chatgpt_desktop() {
        // 浏览器预览中无法启动外部进程
        return { success: false, error: '需要 PyWebView 后端才能启动 ChatGPT 桌面版' };
    }

    async set_current_provider(id, app_type) {
        const list = this._load();
        const target = list.find(p => String(p.id) === String(id));
        if (!target) return { success: false, error: '供应商不存在' };
        const app = (app_type === 'codex') ? 'codex' : 'claude';
        list.forEach(p => {
            if (Array.isArray(p.apps) && p.apps.length) {
                p.apps.forEach(b => { if (b.app_type === app) b.role = '备用'; });
            } else if ((p.app_type || 'claude') === app || (p.app_type || '') === 'both') {
                p.role = '备用';
            }
        });
        if (Array.isArray(target.apps) && target.apps.length) {
            target.apps.forEach(b => { if (b.app_type === app) b.role = '当前'; });
            const primary = target.apps.find(b => b.app_type === 'claude') || target.apps[0];
            target.role = primary ? primary.role : '当前';
        } else {
            target.role = '当前';
        }
        this._mockModes = this._mockModes || { claude: { mode: 'official', provider_name: null }, codex: { mode: 'official', provider_name: null } };
        this._mockModes[app] = { mode: 'provider', provider_name: target.name };
        this._save(list);
        return { success: true, message: '已设为当前配置(预览)' };
    }

    async set_current_official(app_type) {
        const app = (app_type === 'codex') ? 'codex' : 'claude';
        const list = this._load();
        list.forEach(p => {
            if (Array.isArray(p.apps) && p.apps.length) {
                p.apps.forEach(b => { if (b.app_type === app) b.role = '备用'; });
            } else if ((p.app_type || 'claude') === app || (p.app_type || '') === 'both') {
                p.role = '备用';
            }
        });
        this._save(list);
        this._mockModes = this._mockModes || { claude: { mode: 'official', provider_name: null }, codex: { mode: 'official', provider_name: null } };
        this._mockModes[app] = { mode: 'official', provider_name: null };
        return { success: true };
    }

    async get_current_modes() {
        this._mockModes = this._mockModes || { claude: { mode: 'official', provider_name: null }, codex: { mode: 'official', provider_name: null } };
        return { success: true, data: this._mockModes };
    }

    // Window controls (frameless - no-op in browser)
    async minimize_window() {}
    async maximize_window() {}
    async close_window() {}
    async start_window_drag() {}
    async move_window_to() {}
    async move_window_by() {}
    async end_window_drag() {}

    async get_about_info() {
        return {
            name: 'API Monitor',
            version: '3.0.0 (mock)',
            description: '实时监控 API 提供商状态和服务质量 (浏览器预览模式)',
            author: 'API Monitor Project',
            repo: '本地预览',
        };
    }

    // ---- Settings ----
    async get_all_settings() {
        return {
            success: true,
            data: {
                auto_test_interval: '0', test_concurrency: '3', test_timeout: '30', test_retries: '2',
                failover_enabled: '1', failover_need_confirm: '0', failover_max_switches: '3', failover_cooldown: '300',
                notify_status_change: '1', notify_failover: '1', notify_test_complete: '0',
                webhook_url: '', webhook_events: 'status_change,failover', history_retention_days: '30', ssl_verify: '1',
                auto_sync_claude_on_startup: '1',
                auto_sync_codex_on_startup: '1',
            }
        };
    }
    async save_settings(d) { return { success: true }; }

    // ---- Notifications ----
    async get_notifications(limit, unreadOnly) {
        return { success: true, data: [] };
    }
    async get_unread_count() { return { success: true, count: 0 }; }
    async mark_notifications_read(ids) { return { success: true, affected: 0 }; }

    // ---- History ----
    async get_test_history(pid, hours) { return { success: true, data: [] }; }
    async get_history_stats(pid, hours) {
        return { success: true, data: { total: 0, ok: 0, fail: 0, avg_latency: null, p95_latency: null, availability: null, last_fail_time: null } };
    }
    async get_history_timeline(pid, hours) { return { success: true, data: [] }; }

    // ---- Scheduler ----
    async get_scheduler_status() {
        return { success: true, data: { running: false, interval: 0, next_run: 0, remaining: 0 } };
    }
    async start_scheduler(interval) { return { success: true }; }
    async stop_scheduler() { return { success: true }; }

    // ---- Export & Backup ----
    async export_providers(fmt, includeKey) { return { success: false, error: '浏览器预览模式不支持导出' }; }
    async export_history(pid, hours) { return { success: false, error: '浏览器预览模式不支持导出' }; }
    async create_backup() { return { success: false, error: '浏览器预览模式不支持备份' }; }
    async restore_backup() { return { success: false, error: '浏览器预览模式不支持恢复' }; }
    async list_backups() { return { success: true, data: [] }; }

    async check_update() { return { success: true, data: { has_update: false } }; }
}

// Boot
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { window.app = new App(); });
} else {
    window.app = new App();
}
