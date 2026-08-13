# 项目长期记忆 — tsplayer-pywebview

## 项目定位
pywebview + Flask 的桌面影音播放器（抖音式竖滑播放页）。WinForms/Chromium 壳，本地 Flask 服务 `127.0.0.1:19527` 提供 `static/`。

## 设计系统（"纸上观影 / Paper & Reel"，极简文艺，深浅双主题）
- 设计上下文：` .impeccable.md`
- 令牌与组件类：`static/css/style.css`（`:root` 浅色 / `html.dark` 深色；暖纸底 + 单一暖金强调 `--accent`）
- 首页：`static/index.html`；样式**全部由单一 `static/css/style.css` 提供**（设计系统「纸上观影」+ 手写 `u-*` 工具类 + 9 套主题 `data-theme` 令牌覆盖），不再有任何独立 utilities.css；保留全部 `x-*` / `id` / `class`
- 播放页：`static/player.html`（**player.js 逻辑为内联 `<script>`，无独立 player.js**）
- 字体：Noto Sans SC（正文）+ Noto Serif SC（标题），经 Google Fonts，附 fallback

## 关键架构约束（改前端前必读）
- pywebview 序列化 `js_api` 时跳过 `_` 前缀属性 → 窗口引用必须命名为私有（`_main_window`/`_player_window`），否则卡死（见 2026-08-02 日志）
- 主题切换由 `app.js` 在 `<html>` 上切 `dark` class（深浅双主题，CSS 变量在 `style.css` 的 `:root` / `html.dark` 定义）。**项目已彻底移除 Tailwind**：原 `static/tailwind.css` / `static/tailwind.in.css` / `tailwind.config.js` 已删除，手写工具类（`u-*` 命名）也已并入 `static/css/style.css`（含一套替代 Preflight 的基础重置），全仓库无 Tailwind 命名残留（`hover:` 变体改为 `u-hover-*`、重要修饰 `!` 改为独立组件类如 `.ts-loadmore-btn`）。
- **多主题风格系统（2026-08-04 新增）**：`app.js` 的 `cfg.styleTheme`（`'default'`/`'nft'`/`'pixel'`/`'cyber'`/`'handdrawn'`/`'realsurreal'`/`'minimal'`/`'futurism'`/`'pop'`）经 `applyStyleTheme()` 在 `<html>` 上设 `data-theme`；`style.css` 用 `html[data-theme="x"]`（浅）+ `html.dark[data-theme="x"]`（深）覆盖设计令牌（`--paper/--ink/--accent/--font-*/--radius-card/--radius-btn/--glow` 等）。与 light/dark 独立组合。设置中心→基础 新增主题网格选择器（`themeList` + `setStyleTheme`）。改主题只需在 `style.css` 对应 `data-theme` 块调令牌，无需动组件类。
- 播放页竖滑依赖标准 Swiper 结构：`#swiperWrap.swiper.verticalSwiper > #swiperWrapper.swiper-wrapper > .swiper-slide`
- JS 暴露的 API 名（`show_main_window`/`open_player_window` 等）不可改名
- **M3U8 智能去广告（2026-08-10 修复，2026-08-11 真正接通看片路径）**：广告与正片是两套独立视频源，切片 URL 目录（含码率/随机流 ID）不同，且以 `#EXT-X-DISCONTINUITY` 分隔。识别逻辑在 `src/parser/m3u8_filter.py` 的 `_smart_filter()`：按 discontinuity 切段组 → 每段组按切片 URI 的 dirname 归并为「视频源」→ 正片 = 总时长最长的源（阈值 0.5×最长 + 夹心/边缘位置辅助判定），剔除其余（广告）切片并丢弃所有 `#EXT-X-DISCONTINUITY`，输出干净单源 VOD 列表；单一源（无可识别广告）原样透传。主播放列表（含 `#EXT-X-STREAM-INF`）走 `_rewrite_master()` 把变体地址改写为 `/api/m3u8?url=<绝对地址>`，让变体也经过滤。
- **真正生效的关键（2026-08-11）**：之前"还是有广告"的根因是**实际看片页 `static/player.html`（弹出窗口 XGPlayer 竖滑播放）直接 `url: ep.url` 把原始 m3u8 交给 XGPlayer，完全绕过了过滤**；只改 `player.js`（主窗 HLS.js）对看片毫无作用。修复方案（两套播放器通用）：
  1. 浏览器先 `fetch` 远程 m3u8（带正确 Referer/Cookie，CDN 一般只认浏览器）→ 避免「后端远程拉取被 CDN 拒」导致静默回退直连、广告照播；
  2. 文本 `POST /api/m3u8_prepared`（后端 `filter_m3u8_text()` 只过滤+暂存，不远程拉取）→ 返回 `/api/serve_m3u8/<sid>.m3u8`；
  3. 播放器加载这个 `.m3u8` 结尾的本地地址（XGPlayer/HLS.js 据此识别为 HLS，且切片已是后端绝对化后的 CDN 地址，浏览器直取）。
  - **为什么不用 Blob URL**：XGPlayer 靠 `.m3u8` 扩展名判定是否走 HLS；`blob:` 无扩展名 → 退化原生播放 → Chrome 无法播 HLS 黑屏。故必须经「以 .m3u8 结尾的本地服务地址」。
  - 开关 `smartAdRemove` 在 `cms_cfg`（`data/config.json` 默认 true），`/api/m3u8_prepared` 与 `/api/m3u8` 都读它。
  - `static/js/player.js` 与 `static/player.html` 各有 `fetchFilteredM3u8()`，`initPlayer()`(player.html) 已改为 `async` 先过滤再建 XGPlayer。`static/js/api.js` 的 `getM3u8()` 是死函数，勿依赖。
  - **二次包装 bug（2026-08-10 修复）**：master 播放列表变体曾被包两层 `/api/m3u8?url=`（浏览器 fetch 失败→回退 `/api/m3u8` 单层包装→又 POST 给 `/api/m3u8_prepared` 再 `urljoin` 拼到 CDN 主机→双层→HLS 拉到 `https://<cdn>/api/m3u8?...` 这种不存在地址→报错/退化直连）。**修复**：(1) `_rewrite_master()` 幂等——变体行已含 `/api/m3u8` 则保持原样不二次包装；(2) `fetchFilteredM3u8()` 回退路径（浏览器 fetch 失败）**直接返回 `/api/m3u8?url=<原始>` 单层代理地址，不再把已包装 master 二次 POST**；(3) 两处 catch 不再退化回原始带广告直链（避免广告照播，最坏黑屏而非广告）。验证：幂等、回退路径、prepared 路径三者均单层包装；媒体列表广告 0 泄漏、正片全保留。

## 自定义标题栏与窗口拖拽/缩放（2026-08-06 重构：坐标运算移到 Python 端）
- 主窗口与播放窗口均 `frameless=True`，头部/`.pp-topbar` 为自定义标题栏；窗口边缘由 8 个 `.win-edge` 热区（固定 6px）覆盖。
- **原生 `WM_NCLBUTTONDOWN` 完全不可行（勿再用）**：pywebview 的 frameless 把 `FormBorderStyle` 设为 `None`，**连标题栏区域都移除**，所以 `HTCAPTION`（拖标题）和 `HTLEFT/...`（缩放）全部是 no-op；之前 ctypes 64 位句柄截断只是雪上加霜。唯一可靠路径是 pywebview 的 `move()/resize()` 公共 API。
- **新架构（最稳）**：坐标运算全部在 Python 端完成，JS 只做转发。
  - `static/js/window-chrome.js`：标题栏/边缘 `mousedown` → 调 `WindowChrome`（`begin`）→ 之后 `mousemove` 用 `requestAnimationFrame` 合并，每帧把 `e.screenX/e.screenY`（光标绝对屏幕坐标，窗口移动后仍稳定）发给 `api.drag_to / api.resize_to`；`mouseup` 调 `api.drag_end`。**无 inFlight 锁、无坐标系换算**；Aero Snap 判定放在 Python 端 `drag_to`。
  - **状态同步契约（防"点两次才生效"竞态，2026-08-07 修复）**：`drag_end` 返回 `{maximized:bool, changed:bool}`，JS 仅在 `changed===true`（吸附真正改了态）时回写 `maximized`；`toggle_maximize_window` 返回 `bool`(切换后真实态)，JS `toggleMaximize` 以该返回值为准（兜底用乐观值）。**切勿改回"drag_end 每次回写最大化态"或"JS 仅本地乐观置位"**，否则双击/按钮会再次需点两次。**标题栏 dblclick 与最大化/还原按钮都走 `WindowChrome.toggleMaximize()`（主窗 winMax、播放窗 winMaxP），双击即可最大化⇄还原。**
  - `main.py` 的 `JsApi` 类：收到屏幕坐标后，以「按下瞬间 `w.x/w.y` + 光标位移增量」算目标几何并 `w.move/w.resize`。`resize_to` 按窗口类型强制最小尺寸：播放窗 360×640、主窗 800×600。
  - **每个窗口独立 `JsApi` 实例**（主窗 `main_api` / 播放窗 `player_api`），消除多窗口共用一个 `js_api` 时的状态/目标串扰（见 `open_player_window` 新建 `JsApi(self,'player')` 并 `attach`）。
- 已暴露 API（名字不要改）：`drag_start`、`drag_to`、`resize_start`、`resize_to`、`drag_end`、`get_window_rect`、`minimize_window`、`toggle_maximize_window`、`close_window`、`show_main_window`、`open_player_window`、`close_player`、`resize_to_aspect`。
  - `toggle_maximize_window(is_max)`：手动把窗口 `move/resize` 到 `Screen.WorkingArea`（留任务栏，非全屏），保存/还原用 `_restore_geom`/`_is_maximized`（**per-window 状态**，不再共用）。`is_max` 由 JS 端 `WindowChrome.maximized` 传入，经 `WindowChrome.toggleMaximize()` 统一入口（标题栏 `dblclick` 与窗口"最大化"按钮都走它）。**返回切换后的真实 `bool(_is_maximized)`，供 JS 同步。**
  - **最大化时禁止边缘缩放**：`begin()` 中 `type==='resize' && maximized` 直接 return。
- **Aero Snap 已实现**（2026-08-06 晚）：判定放在 Python 端 `drag_to` 内，用 `Screen.FromPoint(光标)` 取光标所在屏的 `WorkingArea`（排除任务栏）做 zone 判定——`sy<=顶部+T`→`max` 最大化；`sx<=左缘+T`→`left` 贴左半屏；`sx>=右缘-T`→`right` 贴右半屏（`T=16` 逻辑像素）。
  - 进入吸附区时记录 `pre_geom`（自由几何），区内直接 `move/resize` 到吸附几何；离开吸附区则还原 `pre_geom` 并把锚点重置为当前光标使继续拖拽平滑；`drag_end` 在 `max` 区释放则提交 `_is_maximized=True`、否则解除。
  - **从最大化窗口拖出还原的时机很关键（2026-08-06 晚修复）**：还原动作**不能放在 `drag_start`/`mousedown`**（否则「最大化后点一下标题栏就还原」）。正确做法：在 `drag_start` 只记录 `was_max`+`maxGeom`；`drag_to` 仅在「位移超过 4px 阈值」时才还原 `_restore_geom` 并让光标保持在标题栏同相对位置跟随；`drag_end` 只有 `d['_restored']` 为真才清除 `_is_maximized`，**单击（无位移）保持最大化不变**。
  - 从最大化拖出后游标可能仍在顶部吸附区，若直接判 `zone='max'` 会瞬间重新最大化（抖动）。加 `_maxGate`：拖出时置 True 屏蔽顶部吸附，待游标离开顶部区（`sy>wa.y+T`）才解除屏蔽，之后拖回顶部释放仍可正常重新最大化。
  - `toggleMaximize` 必须传「目标态=!maximized」给 `toggle_maximize_window`（旧版传当前态导致双击/按钮失效，已修）。`drag_end` 返回 `bool(_is_maximized)` 经 JS Promise 回写 `window.WindowChrome.setMaximized`，保证最大化态下边缘缩放被正确禁用。
  - 多屏：依赖 `Screen.FromPoint` 自动选对屏工作区。
- **播放窗口全屏/下载处理**：XGPlayer 配置 `download:false` 关闭自带下载按钮、`ignores:['fullscreen']` 关闭自带全屏（含双击全屏），使视频永不出窗口；最大化由标题栏 dblclick / 窗口按钮经 `toggleMaximize` 控制。已删除 `player.on('fullscreen')` 调 `toggle_window_fullscreen` 的钩子与死代码 `downloadEpisode()`。CSS 已对 `.pp-stage`/`.player-box` 加 `overflow:hidden` 兜底。
- 窗口控制按钮 z-index 须高于 `.win-edge`(60)（主窗 `.ts-win-controls.header-win` 已设 70），否则角区会遮挡按钮。

## 待办/观察
- `main.py` 中 `sys.setrecursionlimit(5000)` 会放大 CLR 递归冻结风险，建议保持默认或仅在确认无 CLR 下钻时调整（已靠私有属性修复，未改此项）
- 播放页 XGPlayer 进度条/音量强调色已统一为暖金；若后续改强调色，同步改 `style.css` 与 `player.html` 内联变量
- 播放页 `.player-box` 必须 `height:100%`（否则 XGPlayer 容器高度为 0 → 有声音无画面、控件不可见）；改播放页布局务必保留此规则
- 播放页分集渲染：`activateActiveSlide()` 采用"预渲染窗口 ±1"——当前集 `initPlayer(box,true)` 真实播放、相邻集 `initPlayer(box,false)` 先建好但暂停（切到即见不空白）、距当前集 >2 销毁释放。`canplay` 仅 `epIndex===currentActiveIndex` 才 `resize_to_aspect`。右侧面板 `id="panelPoster"` 是整块模糊底图（`.pp-panel-bg` + 暗化渐变遮罩），`.pp-panel-inner` 须 `position:relative;z-index:1` 浮于其上。
- 首页"加载更多"自动加载由 `app.cfg.autoLoadMore` 控制：`setupAutoLoad` 的 IntersectionObserver 仅依赖 `autoLoadMore`（不再绑定 `loadMode==='waterfall'`）；手动"加载更多"按钮仅在 `!autoLoadMore` 时显示
