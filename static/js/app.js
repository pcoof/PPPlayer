/**
 * TSPlayer 主应用逻辑 — Alpine.js 组件
 */

function app() {
    return {
        placeholderImg: 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzAwIiBoZWlnaHQ9IjQ1MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMzAwIiBoZWlnaHQ9IjQ1MCIgZmlsbD0iI2U1ZTdlYiIvPjwvc3ZnPg==',
        sources: [],
        cfg: {},
        currentUrlType: 'unknown',
        sniffedUrl: null,
        sniffedUrlType: '',
        fav: [],
        his: [],
        searchHistory: [],
        classes: [],
        allClassesList: [],
        visibleClasses: [],
        hiddenClasses: [],
        videos: [],
        page: 1,
        totalPages: 1,
        hasMore: true,
        currentType: '0',
        searchWd: '',
        showFav: false,
        showHist: false,
        showSettings: false,
        showSourceEdit: false,
        showPlayer: false,
        showMoreTypes: false,
        showSearchHist: false,
        showSourceBlacklistPicker: false,
        settingsTab: 'basic',
        bossKeyListening: false,
        themeList: [
            { id: 'default', name: '主题风格', swatch: 'background:linear-gradient(135deg,#f6f3ec 0 50%,#b0823c 50% 100%)' },
            { id: 'nft', name: 'NFT风格', swatch: 'background:linear-gradient(135deg,#f3eefb 0 50%,#7c3aed 50% 100%)' },
            { id: 'pixel', name: '像素风', swatch: 'background:linear-gradient(135deg,#e8f0d8 0 50%,#2f7d32 50% 100%)' },
            { id: 'cyber', name: '赛博朋克', swatch: 'background:linear-gradient(135deg,#070b16 0 50%,#22d3ee 50% 100%)' },
            { id: 'handdrawn', name: '手绘插画', swatch: 'background:linear-gradient(135deg,#fbf6ef 0 50%,#e08a3c 50% 100%)' },
            { id: 'realsurreal', name: '现实/超现实风', swatch: 'background:linear-gradient(135deg,#f5f5f4 0 50%,#5b7c99 50% 100%)' },
            { id: 'minimal', name: '简约风', swatch: 'background:linear-gradient(135deg,#ffffff 0 50%,#4f7cff 50% 100%)' },
            { id: 'futurism', name: '未来主义', swatch: 'background:linear-gradient(135deg,#eef1f6 0 50%,#2f6bff 50% 100%)' },
            { id: 'pop', name: '波普艺术', swatch: 'background:linear-gradient(135deg,#fff8e1 0 50%,#ff2d55 50% 100%)' },
            { id: 'cyber1', name: '朋克赛博', swatch: 'background:linear-gradient(135deg,#008d7e 0 50%,#070b16 50% 100%)' },
            { id: 'brutalist', name: '野兽风格', swatch: 'background:linear-gradient(135deg,#ffd23f 0 50%,#070b16 50% 100%)' }
        ],
        editingIdx: null,
        editingSource: { name: '', url: '' },
        playGroups: [],
        playSrc: 0,
        playEp: 0,
        playSpeed: '1',
        playerTitle: '',
        currentItem: null,
        isWebPlayer: false,
        showPlayerSettings: false,
        showEpSort: false,
        epSortAsc: true,
        playerHover: false,
        showPlayerSidebar: true,
        loading: false,
        sourceClassesFetched: [],
        sourceClassesLoading: false,
        _currentPlayUrl: '',
        _skip: {},
        _observer: null,
        _urlFetchTimer: null,
        isMaximized: false,  // 窗口最大化状态，由 WindowChrome.onStateChange 同步

        async init() {
            const defCfg = { theme: "auto", styleTheme: "default", autoNext: true, rememberSpeed: true, playerType: "native", loadMode: "waterfall", hiddenTypes: "", autoLoadMore: true, smartAdRemove: true, skipIntro: false, skipOutro: false, introTime: 0, outroTime: 0, cms_spd: "1", maxHistory: 200, maxFav: 500, autostart: false, closeToTray: true, cardTags: { tl: "area", tr: "", bl: "", br: "hits", sub1: "type", sub2: "year" } };
            let saved = {};
            try { saved = await API.loadAllConfig(); } catch (e) { console.warn('load config error:', e); }

            this.sources = Array.isArray(saved.cms_src) ? saved.cms_src : [];
            // 确保每个 source 有默认 listLayout 和运行时 _status 字段
            this.sources = this.sources.map(s => ({ listLayout: 'poster', _status: 'unchecked', _statusCode: null, ...s }));
            this.cfg = Object.assign({}, defCfg, saved.cms_cfg || {});
            this.cfg.cardTags = Object.assign({}, defCfg.cardTags, (saved.cms_cfg && saved.cms_cfg.cardTags) || (this.cfg.cardTags || {}));
            this.fav = Array.isArray(saved.cms_fav) ? saved.cms_fav : [];
            this.his = Array.isArray(saved.cms_his) ? saved.cms_his : [];
            this.searchHistory = Array.isArray(saved.cms_search_hist) ? saved.cms_search_hist : [];
            this.playSpeed = this.cfg.cms_spd || '1';

            this.applyTheme();
            this.applyStyleTheme();
            this.normalizeSourceEnabled();

            // 同步开机自启状态（注册表可能被外部修改）
            if (window.pywebview && window.pywebview.api && window.pywebview.api.get_autostart) {
                window.pywebview.api.get_autostart().then(v => { this.cfg.autostart = !!v; }).catch(()=>{});
            }

            // 同步自定义标题栏的最大化状态到 Alpine（用于最大化按钮图标切换）
            var self = this;
            function bindChromeState() {
                if (window.WindowChrome) {
                    window.WindowChrome.onStateChange = function(state) {
                        self.isMaximized = !!state.maximized;
                    };
                    // 主动查询一次当前状态
                    if (window.WindowChrome.syncState) window.WindowChrome.syncState();
                }
            }
            if (window.WindowChrome) bindChromeState();
            else window.addEventListener('load', bindChromeState);

            if (this.sources.length) {
                const src = this.getActiveSource();
                if (src && src.name) document.title = src.name;
                this.loadClasses().then(() => this.loadData());
            }
            try {
                window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
                    if (this.cfg.theme === 'auto') this.applyTheme();
                });
            } catch (e) {}
            window.addEventListener('resize', () => this.calcClasses());

            // 窗口拖拽/缩放由 window-chrome.js 转发鼠标 screenX/screenY，坐标运算在 main.py 的 JsApi 完成，
            // 与窗口样式无关、稳定无抖动（见 index.html 的 .win-edge 与标题栏绑定）。

            window._onOutroTrigger = () => { if (this.cfg.autoNext) this.nextEp(); };
            window._onVideoEnded = () => { if (this.cfg.autoNext) this.nextEp(); };
            window._onRateChange = (rate) => {
                this.playSpeed = String(rate);
                if (this.cfg.rememberSpeed) { this.cfg.cms_spd = this.playSpeed; this.saveAllDataDebounced(); }
            };

            this.$nextTick(() => this.setupAutoLoad());

            // 暴露组件实例，供播放窗口「编辑 API 源」回调唤起本窗口设置中心
            window.__app = this;
            this.applyBossKey();
        },

        // ── 老板键：捕获组合键 + 注册全局热键 ──
        startBossKeyCapture() {
            if (this.bossKeyListening) return;
            this.bossKeyListening = true;
            const self = this;
            // 轻微延迟，避免本次 click 的 keydown 被误捕获
            setTimeout(() => {
                const handler = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return;
                    const parts = [];
                    if (e.ctrlKey) parts.push('Ctrl');
                    if (e.altKey) parts.push('Alt');
                    if (e.shiftKey) parts.push('Shift');
                    if (e.metaKey) parts.push('Win');
                    let key = e.key;
                    if (key === ' ') key = 'Space';
                    else if (key.length === 1) key = key.toUpperCase();
                    parts.push(key);
                    self.cfg.bossKey = parts.join('+');
                    self.bossKeyListening = false;
                    window.removeEventListener('keydown', handler, true);
                    self.saveCfg();
                    self.applyBossKey();
                };
                window.addEventListener('keydown', handler, true);
            }, 60);
        },

        resetBossKey() {
            this.cfg.bossKey = '';
            this.saveCfg();
            this.applyBossKey();
        },

        applyBossKey() {
            if (window.pywebview && window.pywebview.api && window.pywebview.api.set_boss_key) {
                window.pywebview.api.set_boss_key(this.cfg.bossKey || '');
            }
        },

        _saveTimer: null,
        saveAllDataDebounced() {
            if (this._saveTimer) clearTimeout(this._saveTimer);
            this._saveTimer = setTimeout(() => {
                API.saveAllConfig({
                    cms_cfg: this.cfg,
                    cms_src: this.sources,
                    cms_fav: this.fav,
                    cms_his: this.his,
                    cms_search_hist: this.searchHistory
                }).catch(e => console.warn('save config error:', e));
            }, 500);
        },

        setupAutoLoad() {
            // 清理上一次的监听，避免重复绑定导致多次加载
            if (this._observer) { try { this._observer.disconnect(); } catch (e) {} this._observer = null; }
            if (this._scrollHandler) {
                if (this._scrollRoot) this._scrollRoot.removeEventListener('scroll', this._scrollHandler);
                window.removeEventListener('scroll', this._scrollHandler);
                this._scrollHandler = null; this._scrollRoot = null;
            }
            // 仅「自动加载」模式启用无限滚动；「上下页」模式用分页控件，不自动加载。
            if (this.cfg.loadMode !== 'waterfall') return;

            const content = document.querySelector('.ts-content');
            const sentinel = document.getElementById('loadSentinel');

            // 真正滚动的宿主：本布局由内层 .ts-content（overflow-y:auto）承载滚动，窗口并不滚动。
            // 若错误地用 window 维度判定，则 scrollY 恒为 0、documentElement.scrollHeight≈视口高，
            // 条件永远为真→未发生滚动也会无限触发加载。故必须动态识别真正的滚动容器。
            const activeScrollHost = () => {
                if (content && content.scrollHeight - content.clientHeight > 4) return content;
                return window;
            };
            const nearBottom = (host) => {
                if (host === window) {
                    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 300;
                }
                return host.scrollTop + host.clientHeight >= host.scrollHeight - 300;
            };

            let raf = 0;
            const tryLoad = () => {
                if (this.loading || !this.hasMore) return;
                if (raf) return;
                raf = requestAnimationFrame(() => {
                    raf = 0;
                    if (nearBottom(activeScrollHost())) this.loadMore();
                });
            };

            // 主触发：监听真正的滚动容器（内层 .ts-content）；window 监听仅作为退化兜底
            this._scrollRoot = content;
            this._scrollHandler = tryLoad;
            if (content) content.addEventListener('scroll', tryLoad, { passive: true });
            window.addEventListener('scroll', tryLoad, { passive: true });

            // 初始化补加载：首屏内容不足一屏（底部已进入阈值）时立即触发一次，避免「到底了却不加载」
            this.$nextTick(tryLoad);

            // 兜底触发：IntersectionObserver 的根设为真正的滚动容器，确保内层滚动时哨兵进入视口才触发
            const obsRoot = (content && content.scrollHeight - content.clientHeight > 4) ? content : null;
            if (sentinel) {
                this._observer = new IntersectionObserver((entries) => {
                    if (entries.some(en => en.isIntersecting)) tryLoad();
                }, { root: obsRoot, rootMargin: '300px' });
                this._observer.observe(sentinel);
            }
        },
        // 切换主题
        toggleThemeMode() {
            if (this.cfg.theme === 'auto') this.cfg.theme = 'light';
            else if (this.cfg.theme === 'light') this.cfg.theme = 'dark';
            else this.cfg.theme = 'auto';
            this.saveCfg();
        },

        // ── 自定义标题栏（无边框窗口）──
        titleBarMouseDown(e) {
            if (e.target.closest('input,button,a,select,textarea,[data-no-drag]')) return;
            if (e.button !== 0) return;
            // 标题栏拖拽：由 window-chrome.js 经 pywebview 公共 API 移动窗口（稳定无抖动）；
            // 最大化状态下拖拽会先还原再跟随光标，与系统标题栏一致
            if (window.WindowChrome) {
                window.WindowChrome.titlebarMouseDown(e);
            }
        },
        titleBarDblClick(e) {
            if (e.target.closest('input,button,a,select,textarea,[data-no-drag]')) return;
            if (window.WindowChrome) {
                window.WindowChrome.toggleMaximize();
            } else if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize_window) {
                window.pywebview.api.toggle_maximize_window();
            }
        },
        winMin() { if (window.pywebview && window.pywebview.api) window.pywebview.api.minimize_window(); },
        winMax() { if (window.WindowChrome) { window.WindowChrome.toggleMaximize(); } else if (window.pywebview && window.pywebview.api) window.pywebview.api.toggle_maximize_window(); },
        winClose() {
            // 关闭到托盘：开启时隐藏主窗口（托盘图标仍可恢复），否则真正退出
            if (this.cfg.closeToTray && window.pywebview && window.pywebview.api) {
                if (window.pywebview.api.close_player) window.pywebview.api.close_player();
                try { window.pywebview.api.hide_window(); } catch(e) {}
            } else if (window.pywebview && window.pywebview.api) {
                window.pywebview.api.quit_app();
            }
        },
        async toggleAutostart() {
            if (window.pywebview && window.pywebview.api && window.pywebview.api.set_autostart) {
                const ok = await window.pywebview.api.set_autostart(!!this.cfg.autostart);
                if (!ok) {
                    alert('设置开机自启失败，请检查权限。');
                    this.cfg.autostart = !this.cfg.autostart;
                }
            }
            this.saveCfg();
        },
        showPlayerWindow() {
            // 显示已打开的播放窗口（最小化/隐藏后都能恢复）
            if (window.pywebview && window.pywebview.api && window.pywebview.api.show_main_window) {
                // show_main_window 是显示主窗口，这里需要显示播放窗口
                // 通过 evaluate_js 或专用 API
                if (window.pywebview.api.show_player_window) {
                    window.pywebview.api.show_player_window();
                }
            }
        },
        closePlayingBar() {
            // 关闭播放窗口并清除当前播放状态
            if (window.pywebview && window.pywebview.api && window.pywebview.api.close_player) {
                window.pywebview.api.close_player();
            }
            this.currentItem = null;
            this.playerTitle = '';
        },

        applyTheme() {
            const dark = this.cfg.theme === 'dark' || (this.cfg.theme === 'auto' && matchMedia('(prefers-color-scheme:dark)').matches);
            document.documentElement.classList.toggle('dark', dark);
        },

        applyStyleTheme() {
            const t = this.cfg.styleTheme;
            if (t && t !== 'default') document.documentElement.setAttribute('data-theme', t);
            else document.documentElement.removeAttribute('data-theme');
        },

        setStyleTheme(id) {
            this.cfg.styleTheme = id;
            this.saveCfg();
            this.applyStyleTheme();
        },

        saveCfg() { this.saveAllDataDebounced(); this.applyTheme(); },
        saveSources() { this.saveAllDataDebounced(); },

        normalizeSourceEnabled() {
            if (!Array.isArray(this.sources)) this.sources = [];
            let enabledIndex = this.sources.findIndex(s => s.enabled);
            if (enabledIndex === -1 && this.sources.length) enabledIndex = 0;
            this.sources = this.sources.map((s, i) => ({ ...s, enabled: i === enabledIndex }));
            this.saveSources();
        },

        setActiveSource(i) {
            const src = this.sources[i];
            // Fix3: 只有状态ok的源才能激活
            if (src._status !== 'ok') {
                alert('请先检测该源连接状态，确认可用后再切换');
                return;
            }
            this.sources = this.sources.map((s, idx) => ({ ...s, enabled: idx === i }));
            this.saveSources();
            if (src && src.name) document.title = src.name;
            this.page = 1; this.currentType = '0'; this.searchWd = '';
            this.loadClasses().then(() => this.loadData());
        },

        // Fix2: 删除源时只在删除激活源时才触发重新加载
        deleteSource(index) {
            if (!confirm('确定删除此源？')) return;
            const wasEnabled = this.sources[index].enabled;
            this.sources.splice(index, 1);
            if (wasEnabled && this.sources.length > 0) {
                this.sources[0].enabled = true;
                this.normalizeSourceEnabled();
                this.page = 1;
                this.loadClasses().then(() => this.loadData());
            } else {
                this.normalizeSourceEnabled();
                this.saveSources();
            }
            if (this.sources.length === 0) {
                this.classes = [];
                this.videos = [];
                this.saveSources();
            }
            this.showSourceEdit = false;
        },

        getActiveSource() { return this.sources.find(s => s.enabled); },

        // ── 海报布局 ── 每个源独立存储
        currentListLayout() {
            const src = this.getActiveSource();
            return (src && src.listLayout) || 'poster';
        },

        setListLayout(val) {
            const src = this.getActiveSource();
            if (src) { src.listLayout = val; this.saveSources(); }
        },

        makeApiUrl(base, params) {
            const url = new URL(base);
            Object.entries(params).forEach(e => url.searchParams.set(e[0], e[1]));
            return url.toString();
        },

        filterClasses(classes) {
            const hidden = String(this.cfg.hiddenTypes || '').split(',').map(s => s.trim()).filter(Boolean);
            const src = this.getActiveSource();
            if (src && src.blacklist) {
                const srcBlack = String(src.blacklist).split(',').map(s => s.trim()).filter(Boolean);
                for (const b of srcBlack) if (!hidden.includes(b)) hidden.push(b);
            }
            if (!hidden.length) return classes || [];
            return (classes || []).filter(c => !hidden.includes(String(c.type_id)) && !hidden.includes(String(c.type_name)));
        },

        currentTypeName() {
            if (this.currentType === '0') return '全部';
            const c = this.classes.find(x => String(x.type_id) === String(this.currentType));
            return c ? c.type_name : '全部';
        },

        calcClasses() {
            const container = document.getElementById('typeNavContainer');
            if (!container || !this.classes.length) { this.visibleClasses = this.classes || []; this.hiddenClasses = []; return; }
            const m = document.createElement('button');
            m.className = 'ts-cat';
            m.style.cssText = 'position:absolute;visibility:hidden;left:0;top:0;';
            document.body.appendChild(m);
            m.innerHTML = '<svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg>';
            const moreBtnWidth = m.offsetWidth;
            const moreSectionWidth = moreBtnWidth + 8;
            const flexRow = container.querySelector('.u-flex.u-items-center.u-py-2');
            if (!flexRow) { this.visibleClasses = this.classes; this.hiddenClasses = []; document.body.removeChild(m); return; }
            const maxWidth = flexRow.clientWidth - moreSectionWidth;
            this.visibleClasses = []; this.hiddenClasses = [];
            let totalWidth = 0;
            const gap = 4;
            m.textContent = '全部';
            totalWidth += m.offsetWidth + gap;
            for (const c of this.classes) {
                m.textContent = c.type_name;
                const w = m.offsetWidth + gap;
                if (totalWidth + w <= maxWidth) { totalWidth += w; this.visibleClasses.push(c); }
                else { this.hiddenClasses.push(c); }
            }
            document.body.removeChild(m);
        },

        async loadClasses() {
            const activeSource = this.getActiveSource();
            if (!activeSource) { this.classes = []; this.allClassesList = []; return; }
            const cacheKey = 'cms_cls_' + activeSource.url;
            const cached = localStorage.getItem(cacheKey);
            if (cached) {
                try {
                    const cls = JSON.parse(cached);
                    this.allClassesList = cls;
                    this.classes = this.filterClasses(cls);
                    this.$nextTick(() => this.calcClasses());
                    return;
                } catch (e) {}
            }
            try {
                const data = await API.getClasses(activeSource.url);
                let cls = [];
                if (Array.isArray(data.class)) cls = data.class;
                else if (Array.isArray(data.list)) cls = data.list;
                else if (data.data && Array.isArray(data.data.class)) cls = data.data.class;
                else if (data.data && Array.isArray(data.data.list)) cls = data.data.list;
                else if (Array.isArray(data.data)) cls = data.data;
                else cls = [];
                localStorage.setItem(cacheKey, JSON.stringify(cls));
                this.allClassesList = cls;
                this.classes = this.filterClasses(cls);
                if (this.currentType !== '0' && !this.classes.some(c => String(c.type_id) === String(this.currentType))) {
                    this.currentType = '0';
                }
                this.$nextTick(() => this.calcClasses());
            } catch (e) {
                console.warn('loadClasses error:', e);
                this.classes = []; this.allClassesList = [];
            }
        },

        async loadData(loadMore = false) {
            const activeSource = this.getActiveSource();
            if (!activeSource) { this.videos = []; this.totalPages = 1; this.hasMore = false; return; }
            this.loading = true;
            try {
                const params = { ac: this.searchWd ? 'search' : 'videolist', pg: this.page };
                if (this.currentType !== '0') params.t = this.currentType;
                if (this.searchWd) params.wd = this.searchWd;
                const d = await API.getVideos(activeSource.url, params);
                let list = [];
                if (Array.isArray(d.list)) list = d.list;
                else if (d.data && Array.isArray(d.data.list)) list = d.data.list;
                else if (Array.isArray(d.data)) list = d.data;
                // 区分「后端真实返回了 pagecount」与「字段缺失被兜底成 1」，后者不可信
                const rawPageCount = d.pagecount || d.totalpage || d.page_count || (d.data && (d.data.pagecount || d.data.totalpage || d.data.page_count)) || null;
                this.totalPages = Number(rawPageCount) || 1;
                const pageItems = list.length;
                if (loadMore) {
                    const merged = this.videos.concat(list);
                    const seen = new Set();
                    this.videos = merged.filter(item => {
                        const key = String(item.vod_id || '') + '_' + String(item.vod_name || '');
                        if (seen.has(key)) return false; seen.add(key); return true;
                    });
                } else {
                    this.videos = list;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
                // 推断是否还有更多页：
                // 1) 本页为空 → 无更多；2) 后端真实 pagecount 且 > 当前页 → 有；3) 后端明确末页 → 无；
                // 4) pagecount 缺失/不可信 → 以「本页有返回」乐观续载，遇到空页才停（很多定制 CMS 不返回 pagecount，
                //    此前 totalPages 恒为 1，导致分页按钮隐藏、自动加载永不触发）。
                if (pageItems === 0) {
                    this.hasMore = false;
                    if (!loadMore) this.totalPages = 1;
                } else if (rawPageCount != null && this.totalPages > this.page) {
                    this.hasMore = true;
                } else if (rawPageCount != null) {
                    this.hasMore = false;
                    this.totalPages = this.page;
                } else {
                    this.hasMore = true;
                    this.totalPages = this.page + 1;
                }
            } catch (e) {
                console.warn('loadData error:', e);
                if (!loadMore) { this.videos = []; this.totalPages = 1; }
                this.hasMore = false;
            } finally {
                this.loading = false;
                this.$nextTick(() => {
                    this.setupAutoLoad();
                });
            }
        },

        async loadMore() {
            if (this.loading || !this.hasMore) return;
            if (this._loadMoreThrottle) return;
            this._loadMoreThrottle = true;
            setTimeout(() => { this._loadMoreThrottle = false; }, 250);
            this.page++; await this.loadData(true);
        },

        doSearch() {
            if (!this.searchWd.trim()) return;
            this.searchHistory = this.searchHistory.filter(t => t !== this.searchWd);
            this.searchHistory.unshift(this.searchWd);
            if (this.searchHistory.length > 20) this.searchHistory = this.searchHistory.slice(0, 20);
            this.saveAllDataDebounced();
            this.page = 1; this.currentType = '0'; this.showSearchHist = false;
            this.loadData();
        },

        clearSearchHistory() { this.searchHistory = []; this.saveAllDataDebounced(); },
        removeSearchHistory(term) { this.searchHistory = this.searchHistory.filter(t => t !== term); this.saveAllDataDebounced(); },

        pageRange() {
            const r = [];
            for (let i = Math.max(1, this.page - 2); i <= Math.min(this.totalPages, this.page + 2); i++) r.push(i);
            return r;
        },

        isFav(id) { return this.fav.some(f => String(f.vod_id) === String(id)); },

        tagVal(field, item) {
            if (!item || !field) return '';
            const map = { type: item.type_name, hits: item.vod_hits, lang: item.vod_lang, year: item.vod_year, area: item.vod_area, actor: item.vod_actor, director: item.vod_director, tag: item.vod_tag, duration: item.vod_duration, state: item.vod_state, remarks: item.vod_remarks, pubdate: item.vod_pubdate, vod_blurb: item.vod_blurb, vod_content: item.vod_content };
            let v = map[field];
            if (v === undefined || v === null) return '';
            return String(v);
        },

        activeCardTags() { const src = this.getActiveSource(); return (src && src.cardTags) ? src.cardTags : (this.cfg.cardTags || {}); },

        setCardTag(slot, val) {
            const src = this.getActiveSource();
            if (src) { if (!src.cardTags) src.cardTags = {}; src.cardTags[slot] = val; this.saveSources(); }
            else { if (!this.cfg.cardTags) this.cfg.cardTags = {}; this.cfg.cardTags[slot] = val; this.saveCfg(); }
        },

        cardTagText(cornerOrSub, item) { const f = this.activeCardTags()[cornerOrSub]; return this.tagVal(f, item); },

        isSourceBlacklisted(c) { const bl = this.editingSource.blacklist || ''; const arr = String(bl).split(',').map(s => s.trim()).filter(Boolean); return arr.includes(String(c.type_id)); },

        toggleSourceBlacklist(c) {
            const bl = String(this.editingSource.blacklist || '');
            let arr = bl.split(',').map(s => s.trim()).filter(Boolean);
            const tid = String(c.type_id);
            if (this.isSourceBlacklisted(c)) arr = arr.filter(x => x !== tid);
            else arr.push(tid);
            this.editingSource.blacklist = arr.join(',');
        },

        // ── 拉取源对应的分类（用于编辑源时的黑名单选择器） ──
        async fetchSourceClasses(url) {
            if (!url || url.length < 10) return;
            if (this._urlFetchTimer) clearTimeout(this._urlFetchTimer);
            this.sourceClassesLoading = true;
            this._urlFetchTimer = setTimeout(async () => {
                try {
                    const data = await API.getClasses(url);
                    let cls = [];
                    if (Array.isArray(data.class)) cls = data.class;
                    else if (Array.isArray(data.list)) cls = data.list;
                    else if (data.data && Array.isArray(data.data.class)) cls = data.data.class;
                    else if (data.data && Array.isArray(data.data.list)) cls = data.data.list;
                    else if (Array.isArray(data.data)) cls = data.data;
                    else cls = [];
                    this.sourceClassesFetched = cls;
                } catch (e) {
                    console.warn('fetchSourceClasses error:', e);
                    this.sourceClassesFetched = [];
                } finally {
                    this.sourceClassesLoading = false;
                }
            }, 600);
        },

        toggleFav(item) {
            const idx = this.fav.findIndex(f => String(f.vod_id) === String(item.vod_id));
            if (idx > -1) this.fav.splice(idx, 1);
            else {
                this.fav.unshift({ ...item, fav_time: Date.now() });
                // 按设置中的最大收藏数截断（默认500，可在设置→数据中修改）
                const max = Number(this.cfg.maxFav) || 500;
                if (this.fav.length > max) this.fav = this.fav.slice(0, max);
            }
            this.saveAllDataDebounced();
        },

        clearHist() { if (!confirm('确定清空所有播放历史？')) return; this.his = []; this.saveAllDataDebounced(); },
        formatTime(ts) { return ts ? new Date(ts).toLocaleString() : ''; },

        addSource() {
            this.editingIdx = null;
            this.editingSource = { name: '', url: '', skipIntro: false, introTime: 0, skipOutro: false, outroTime: 0, blacklist: '', listLayout: 'poster' };
            this.sourceClassesFetched = [];
            this.sourceClassesLoading = false;
            this.showSourceBlacklistPicker = false;
            this.showSourceEdit = true;
        },

        editSource(i) {
            this.editingIdx = i;
            this.editingSource = { skipIntro: false, introTime: 0, skipOutro: false, outroTime: 0, blacklist: '', listLayout: 'poster', ...this.sources[i] };
            this.sourceClassesFetched = [];
            this.sourceClassesLoading = false;
            this.showSourceBlacklistPicker = true; // 编辑时自动展开
            this.showSourceEdit = true;
            if (this.editingSource.url) {
                this.fetchSourceClasses(this.editingSource.url);
            }
        },

        saveSource() {
            if (!this.editingSource.name || !this.editingSource.url) { alert('请填写名称和地址'); return; }
            const hadEnabledBefore = this.sources.some(s => s.enabled);
            if (this.editingIdx !== null) {
                this.sources[this.editingIdx] = { ...this.sources[this.editingIdx], ...this.editingSource };
            } else {
                this.sources.push({
                    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                    ...this.editingSource,
                    enabled: !hadEnabledBefore,
                    _status: 'unchecked',
                    _statusCode: null
                });
            }
            this.normalizeSourceEnabled(); this.saveSources(); this.showSourceEdit = false;
            // 仅当「保存的源即当前激活源」或「新增后成为首个激活源」时才刷新主视图；
            // 编辑 / 新增其它非激活源时静默保存，不打扰当前正在浏览的内容。
            const savedIsActive = (this.editingIdx !== null)
                ? !!(this.sources[this.editingIdx] && this.sources[this.editingIdx].enabled)
                : !hadEnabledBefore;
            if (savedIsActive && this.getActiveSource()) {
                this.page = 1; this.currentType = '0'; this.searchWd = '';
                this.loadClasses().then(() => this.loadData());
            }
        },

        // 统一导出通道：桌面端(pywebview)走 Python 原生「另存为」，浏览器走 Blob 下载。
        // 桌面端必须走 API —— WebView2 里 <a download href="blob:..."> 属于浏览器下载通道，
        // 被 pywebview 拦截（默认 ALLOW_DOWNLOADS=False 直接 Cancel），点了完全没反应。
        saveJsonText(filename, text) {
            const api = window.pywebview && window.pywebview.api;
            if (api && api.save_text_file) {
                Promise.resolve(api.save_text_file(filename, text)).then(r => {
                    if (!r) { alert('导出失败：桌面端未返回结果'); return; }
                    if (r.canceled) return;               // 用户在另存为对话框点了取消
                    if (r.ok) alert('已导出到：\n' + r.path);
                    else alert('导出失败：' + (r.error || '未知错误'));
                }).catch(err => alert('导出失败：' + err));
                return;
            }
            const blob = new Blob([text], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        },

        exportSources() {
            this.saveJsonText('cms_sources.json', JSON.stringify(this.sources, null, 2));
        },

        importSources(e) {
            const file = e.target.files[0]; if (!file) return;
            const reader = new FileReader();
            reader.onload = ev => {
                try {
                    const data = JSON.parse(ev.target.result);
                    if (Array.isArray(data)) {
                        this.sources = this.sources.concat(data.map(s => ({
                            id: s.id || Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                            name: s.name || '未命名源', url: s.url || '',
                            enabled: false, listLayout: 'poster', _status: 'unchecked', _statusCode: null,
                            ...s
                        })).filter(s => s.url));
                        this.normalizeSourceEnabled(); this.saveSources();
                        this.page = 1; this.loadClasses().then(() => this.loadData());
                    } else { alert('文件格式错误'); }
                } catch (err) { alert('文件格式错误'); }
            };
            reader.readAsText(file); e.target.value = '';
        },

        exportAllData() {
            const data = { cms_cfg: this.cfg, cms_src: this.sources, cms_fav: this.fav, cms_his: this.his, cms_search_hist: this.searchHistory };
            const name = 'cms_backup_' + new Date().toISOString().slice(0, 10) + '.json';
            this.saveJsonText(name, JSON.stringify(data, null, 2));
        },

        importAllData(e) {
            const file = e.target.files[0]; if (!file) return;
            const reader = new FileReader();
            reader.onload = ev => {
                try {
                    const d = JSON.parse(ev.target.result);
                    if (d.cms_cfg) { this.cfg = Object.assign({}, this.cfg, d.cms_cfg); this.saveCfg(); }
                    if (Array.isArray(d.cms_src)) {
                        this.sources = d.cms_src.map(s => ({
                            id: s.id || Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                            name: s.name || '未命名源', url: s.url || '',
                            enabled: false, listLayout: 'poster', _status: 'unchecked', _statusCode: null,
                            ...s
                        })).filter(s => s.url);
                        this.normalizeSourceEnabled(); this.saveSources();
                    }
                    if (Array.isArray(d.cms_fav)) { this.fav = d.cms_fav; }
                    if (Array.isArray(d.cms_his)) { this.his = d.cms_his; }
                    if (Array.isArray(d.cms_search_hist)) { this.searchHistory = d.cms_search_hist; }
                    this.saveAllDataDebounced();
                    alert('导入成功，即将刷新页面应用');
                    setTimeout(() => location.reload(), 300);
                } catch (err) { alert('文件格式错误'); }
            };
            reader.readAsText(file); e.target.value = '';
        },

        // ── API源连接检测 ──
        async checkSourceStatus(index) {
            const src = this.sources[index];
            if (!src || !src.url) return;
            src._status = 'checking';
            try {
                const result = await API.checkSource(src.url);
                src._status = result.status === 'ok' ? 'ok' : 'error';
                src._statusCode = result.code;
            } catch (e) {
                src._status = 'error';
                src._statusCode = 0;
            }
        },

        getStatusColor(source) {
            if (!source) return 'ts-status-unknown';
            switch (source._status) {
                case 'ok': return 'ts-status-ok';
                case 'error': return 'ts-status-error';
                case 'checking': return 'ts-status-checking';
                default: return 'ts-status-unknown';
            }
        },

        async checkAllSources() {
            const promises = this.sources.map((s, i) => this.checkSourceStatus(i));
            await Promise.all(promises);
        },

        parsePlayGroups(item) {
            const f = String(item.vod_play_from || '').split('$$$').filter(Boolean);
            const u = String(item.vod_play_url || '').split('$$$').filter(Boolean);
            return u.map((g, j) => ({
                sn: f[j] || ('线路' + (j + 1)),
                ep: g.split('#').map((e, k) => {
                    const x = e.indexOf('$');
                    if (x < 0) return { n: '第' + (k + 1) + '集', u: e.trim() };
                    return { n: e.slice(0, x).trim() || ('第' + (k + 1) + '集'), u: e.slice(x + 1).trim() };
                }).filter(e => e.u)
            })).filter(g => g.ep.length);
        },

        detectUrlType(url) {
            if (!url) { this.currentUrlType = 'unknown'; return 'unknown'; }
            const u = url.toLowerCase();
            if (u.includes('.m3u8')) { this.currentUrlType = 'm3u8'; return 'm3u8'; }
            if (u.includes('.mp4') || u.includes('.webm') || u.includes('.mov') || u.includes('.avi')) { this.currentUrlType = 'direct'; return 'direct'; }
            if (u.includes('.html') || u.includes('.htm') || u.includes('/play/') || u.includes('/player/')) { this.currentUrlType = 'webpage'; return 'webpage'; }
            if (u.includes('iframe') || u.includes('<iframe')) { this.currentUrlType = 'iframe'; return 'iframe'; }
            this.currentUrlType = 'unknown'; return 'unknown';
        },

        async sniffMediaUrl(rawUrl) {
            try {
                this.sniffedUrl = null; this.sniffedUrlType = '';
                const data = await API.parseUrl(rawUrl);
                if (data.m3u8) { this.sniffedUrl = data.m3u8; this.sniffedUrlType = 'm3u8'; }
                else if (data.url && data.resolved) { this.sniffedUrl = data.url; this.sniffedUrlType = data.type || 'unknown'; }
                else { this.sniffedUrl = rawUrl; this.sniffedUrlType = 'original'; }
            } catch (e) { this.sniffedUrl = rawUrl; this.sniffedUrlType = 'original'; }
        },

        playSniffedUrl() { if (!this.sniffedUrl) return; this.renderPlayerWithUrl(this.sniffedUrl); },

        renderPlayerWithUrl(url) {
            const box = document.getElementById('playerBox');
            Player.destroy();
            box.innerHTML = '<div class="u-absolute u-inset u-flex u-items-center u-justify-center"><div class="u-w-full u-h-full" id="playerMount"></div></div>';
            this.isWebPlayer = false;
            this._currentPlayUrl = url;
            const skip = this.getActiveSkipSettings();
            if (this.cfg.playerType === 'art') {
                Player.renderArt(url, box, skip, (ct) => this.saveHistory(ct));
            } else {
                Player.renderNative(url, box, skip, (ct) => this.saveHistory(ct));
            }
            this.saveHistory(0);
        },

        openPlayer(item) {
            const g = this.parsePlayGroups(item);
            if (!g.length) { alert('无播放地址'); return; }
            this.openPopoutPlayer(item);
        },

        isWebPlayerUrl(url) { if (!url) return false; const u = url.toLowerCase(); return u.includes('.html') || u.includes('.htm') || u.includes('/play/') || u.includes('/player/') || u.includes('iframe'); },

        currentEps() { return this.playGroups[this.playSrc] ? this.playGroups[this.playSrc].ep : []; },

        getActiveSkipSettings() {
            const src = this.getActiveSource();
            if (src && (src.skipIntro || src.skipOutro)) {
                return { skipIntro: !!src.skipIntro, introTime: Number(src.introTime) || 0, skipOutro: !!src.skipOutro, outroTime: Number(src.outroTime) || 0 };
            }
            return { skipIntro: this.cfg.skipIntro, introTime: this.cfg.introTime, skipOutro: this.cfg.skipOutro, outroTime: this.cfg.outroTime };
        },

        async renderPlayer() {
            try {
                const eps = this.currentEps();
                if (!eps[this.playEp]) return;
                const rawUrl = eps[this.playEp].u;
                this._skip = this.getActiveSkipSettings();
                const box0 = document.getElementById('playerBox');
                if (box0) box0.innerHTML = '<div class="u-absolute u-inset u-flex u-items-center u-justify-center u-text-gray-400 u-text-sm">加载中...</div>';
                this.detectUrlType(rawUrl);
                let playUrl = rawUrl;
                if (this.currentUrlType !== 'm3u8' && this.currentUrlType !== 'direct') {
                    try {
                        await this.sniffMediaUrl(rawUrl);
                        if (this.sniffedUrl && this.sniffedUrlType !== 'original') {
                            playUrl = this.sniffedUrl;
                            this.detectUrlType(playUrl);
                        }
                    } catch (e) { this.sniffedUrl = rawUrl; this.sniffedUrlType = 'original'; }
                } else { this.sniffedUrl = null; this.sniffedUrlType = ''; }
                const url = playUrl;
                this._currentPlayUrl = url;
                const box = document.getElementById('playerBox');
                Player.destroy();
                box.innerHTML = '<div class="u-absolute u-inset u-flex u-items-center u-justify-center"><div class="u-w-full u-h-full" id="playerMount"></div></div>';
                if (this.currentUrlType === 'webpage' || this.currentUrlType === 'iframe' || this.currentUrlType === 'unknown') {
                    if (this.sniffedUrlType === 'original' || !this.sniffedUrl) {
                        this.isWebPlayer = true;
                        box.innerHTML = '<iframe src="' + playUrl + '" class="u-w-full u-h-full u-border-0" allowfullscreen allow="autoplay; fullscreen; picture-in-picture"></iframe>';
                        this.saveHistory(0); return;
                    }
                }
                this.isWebPlayer = false;
                if (this.cfg.playerType === 'art') {
                    Player.renderArt(url, box, this._skip, (ct) => this.saveHistory(ct));
                } else {
                    Player.renderNative(url, box, this._skip, (ct) => this.saveHistory(ct));
                }
                this.saveHistory(0);
            } catch (err) {
                const box = document.getElementById('playerBox');
                if (box) box.innerHTML = '<div class="u-absolute u-inset u-flex u-items-center u-justify-center u-text-red-400 u-text-sm u-p-4 u-text-center">播放失败：' + (err && err.message ? err.message : err) + '</div>';
            }
        },

        // ── 弹出独立播放窗口 ──
        openPopoutPlayer(item) {
            const videoItem = item || this.currentItem;
            if (!videoItem) return;

            const g = this.parsePlayGroups(videoItem);
            if (!g.length) { alert('无播放地址'); return; }

            this.playGroups = g;
            this.playerTitle = videoItem.vod_name || '未命名';
            this.currentItem = videoItem;

            let srcIdx = 0, epIdx = 0;
            const h = this.his.find(x => String(x.vod_id) === String(videoItem.vod_id));
            if (h) {
                srcIdx = Math.min(h.last_source_index || 0, g.length - 1);
                epIdx = Math.min(h.last_episode_index || 0, (g[srcIdx] ? g[srcIdx].ep.length : 1) - 1);
            }
            this.playSrc = srcIdx;
            this.playEp = epIdx;

            const eps = g[srcIdx] ? g[srcIdx].ep : [];
            if (!eps[epIdx]) { alert('无播放地址'); return; }

            const skip = this.getActiveSkipSettings();
            const playSpeed = this.cfg.rememberSpeed ? (this.cfg.cms_spd || '1') : '1';

            // 将完整播放状态写入 localStorage
            const popoutState = {
                playGroups: g,
                playSrc: srcIdx,
                playEp: epIdx,
                playerTitle: this.playerTitle,
                currentItem: videoItem,
                skip: skip,
                playerType: this.cfg.playerType || 'native',
                playSpeed: playSpeed,
                rememberSpeed: this.cfg.rememberSpeed,
                autoNext: this.cfg.autoNext
            };
            localStorage.setItem('cms_popout_state', JSON.stringify(popoutState));

            // 立即写入播放历史：当前页(index)不内嵌播放器，弹窗式播放由独立窗口负责。
            // 桌面端独立窗口后续会通过 report_play_progress 回传进度（更新 last_time），
            // 但浏览器直开 /player 时无 pywebview、report_play_progress 不会触发，
            // 故在此直接记录一次，确保「打开即入历史」在两种环境都成立（按 vod_id 去重，不会重复）。
            this.saveHistory(0);

            // 弹出播放窗口
            if (typeof window.pywebview !== 'undefined' && window.pywebview.api) {
                window.pywebview.api.open_player_window(JSON.stringify(popoutState));
            } else {
                window.open('/player', '_blank', 'width=900,height=600');
            }

            this.closePlayer();
        },

        prevEp() {
            let ne = this.playEp - 1; let ns = this.playSrc;
            if (ne < 0) { ns--; if (ns < 0) return; ne = (this.playGroups[ns] ? this.playGroups[ns].ep.length : 1) - 1; }
            this.playSrc = ns; this.playEp = ne; this.renderPlayer();
        },

        nextEp() {
            let ne = this.playEp + 1; let ns = this.playSrc;
            if (ne >= this.currentEps().length) { ns++; ne = 0; if (ns >= this.playGroups.length) return; }
            this.playSrc = ns; this.playEp = ne; this.renderPlayer();
        },

        saveHistory(currentTime = 0) {
            if (!this.currentItem) return;
            this.his = this.his.filter(h => String(h.vod_id) !== String(this.currentItem.vod_id));
            this.his.unshift({ ...this.currentItem, last_watch_time: Date.now(), last_source_index: this.playSrc, last_episode_index: this.playEp, last_time: currentTime || 0 });
            const max = Number(this.cfg.maxHistory) || 200;
            if (this.his.length > max) this.his = this.his.slice(0, max);
            this.saveAllDataDebounced();
        },

        fallbackCopy(text) {
            const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
            try { document.execCommand('copy'); alert('已复制'); } catch (e) { alert('复制失败'); }
            document.body.removeChild(ta);
        },

        destroyPlayers() {
            // 首页(index.html)只负责「打开独立播放窗口 + 记录历史」，并不内嵌播放器，
            // 因此不会加载 XGPlayer，window.Player 在此页恒为 undefined。
            // 直接调用 Player.destroy() 会抛 ReferenceError 并中断 openPlayer 流程，
            // 故先判空再销毁（独立播放窗口页 player.html 仍会正常加载 Player）。
            if (typeof Player !== 'undefined' && Player) {
                try { Player.destroy(); } catch (e) { /* 忽略已销毁等情况 */ }
            }
        },

        closePlayer() {
            this.showPlayer = false; this.destroyPlayers();
            this.sniffedUrl = null; this.sniffedUrlType = '';
            this._currentPlayUrl = '';
            const box = document.getElementById('playerBox'); if (box) box.innerHTML = '';
        },

        sortedEps() { let eps = [...(this.currentEps() || [])]; if (!this.epSortAsc) eps.reverse(); return eps; },
        toggleEpSort() { this.epSortAsc = !this.epSortAsc; },
        getPlayerTime() { return Player.getCurrentTime(); },
        getDuration() { return Player.getDuration(); },
        getRemainingTime() { return Player.getRemainingTime(); },

        shareCurrent() {
            const text = location.origin + '?v=' + encodeURIComponent(this.currentItem ? this.currentItem.vod_id : '');
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => alert('分享链接已复制')).catch(() => this.fallbackCopy(text));
            } else { this.fallbackCopy(text); }
        },

        downloadCurrent() { const eps = this.currentEps(); if (!eps[this.playEp]) return; window.open(eps[this.playEp].u, '_blank'); },

        applyPortraitNarrow() {
            const pb = document.getElementById('playerBox');
            if (pb && window.innerWidth < 640) { pb.style.height = (window.innerWidth * 9 / 16) + 'px'; }
        }
    };
}
