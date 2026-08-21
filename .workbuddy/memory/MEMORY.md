# 项目长期记忆 — tsplayer-pywebview

## 项目定位
pywebview + Flask 的桌面影音播放器（抖音式竖滑播放页）。WinForms/Chromium 壳，本地 Flask 服务 `127.0.0.1:19527` 提供 `static/`。

## 设计系统与前端结构
- 设计系统「纸上观影」：令牌/组件类全在 `static/css/style.css`（`:root` 浅 + `html.dark` 深；暖金 `--accent`）。**项目已彻底移除 Tailwind**，手写 `u-*` 工具类并入 `style.css`。多主题风格系统：`<html data-theme="...">` 覆盖令牌，与 light/dark 独立组合。
- 首页 `static/index.html`（Alpine.js）；播放页 `static/player.html`（**所有 JS 为内联 `<script>`，无独立 player.js**）。字体 Noto Sans/Serif SC。

## 关键架构约束
- **线程铁律（踩过 RPC_E_DISCONNECTED 的坑）**：`js_api` 回调运行在 WebView2 调度线程，**不是 UI(STA) 线程**。
  自定义原生 WinForms 操作必须走 `main.py` 的 `ui_invoke(window, fn)`（内部 `native.Invoke(System.Action)`）。
  在非 UI 线程改 `FormBorderStyle`/`SetBounds` → WinForms 重建句柄 → WebView2 HWND 被剥离 →
  `(0x80010108) RPC_E_DISCONNECTED`，表现为「窗口不见了进程还在」。
  且 **frameless 窗口重复赋同一枚举值（如已是 `FormBorderStyle.None` 再赋 None）也会触发句柄重建** → 必须先比较再赋值。
  **反向铁律**：pywebview 自带的 `show/hide/destroy/move/resize/evaluate_js` 内部已 Invoke 并等 UI 线程信号量，
  **绝不能**再套 `ui_invoke`（在 UI 线程同步调用 = 死锁）。`PlayerWindow._run_on_ui` 现在就是「直接调用」。
- 托盘 `NotifyIcon` 必须在 UI 线程创建（`webview.start(func=)` 的回调是工作线程！），
  故 `_on_loaded` 等 `native.IsHandleCreated` 后 `ui_invoke(window, tray.create)`。
  `SystemIcons` 属于 `System.Drawing`（不在 `WinForms` 上）；托盘单击用 `MouseClick`+左键过滤（`Click` 右键也触发）；
  窗口可见性只能读 `native.Visible`（pywebview 的 `show()/hide()` 不更新任何字段）。
- 桌面端下载被 pywebview 拦截：`webview.settings['ALLOW_DOWNLOADS']` 默认 False 会 Cancel 掉 blob 下载
  （＝导出按钮「无反应」）。已置 True，且导出统一走 `JsApi.save_text_file` + 前端 `saveJsonText()`（浏览器回退 Blob）。
- 浏览器/桌面差异标记：`<head>` 内联脚本用 **`window.chrome.webview`（WebView2 同步注入，早于 `window.pywebview`）**
  给 `<html>` 打 `is-desktop`/`is-web`；`html.is-web` 隐藏 `.ts-win-controls`/`.pp-win-desktop`/`.win-edge`。
  `WindowChrome.toggleFullscreen()` 在无 pywebview 时回退 DOM `requestFullscreen` 并监听 `fullscreenchange`。
- pywebview 序列化 `js_api` 跳过 `_` 前缀属性 → 窗口引用命名私有（`_main_window`/`_player_window`），否则卡死。
- JS 暴露的 API 名（`show_main_window`/`open_player_window`/`toggle_maximize_window`/`drag_to`/`resize_to` 等）不可改名。
- **首页 `index.html` 不加载 XGPlayer**（`window.Player` 仅 `player.html` 通过 `vendor/xgplayer` 注入）。故 `app.js` 在首页调用的 `destroyPlayers()` 必须先 `typeof Player !== 'undefined'` 判空再 `Player.destroy()`，否则点击卡片 `openPlayer` 会抛 `Player is not defined` 并中断流程。
- **播放历史记录路径**：`openPlayer → openPopoutPlayer` 直接 `saveHistory(0)`（桌面/浏览器通用，按 `vod_id` 去重）。独立播放窗口(`player.html`)后续通过 `api.report_play_progress` 回传进度更新 `last_time`；但浏览器直开 `/player` 时无 `window.pywebview`，`report_play_progress` 永不触发——故历史必须在 `openPopoutPlayer` 直接落库，不能只靠回传。
- 播放页竖滑结构：`#swiperWrap.swiper.verticalSwiper > #swiperWrapper.swiper-wrapper > .swiper-slide`。

## M3U8 / 同源代理架构（根上消除 CORS 与「无法加载响应数据」）
- 浏览器**只与本服务同源通信**，绝不直连 CDN：
  - 直链视频/切片/密钥/字幕 → `/api/media?url=`（字节转发 + Range 拖动，连接池 keep-alive 消除逐片握手卡顿）。
  - 播放列表（含无 `.m3u8` 后缀的分享链接）→ `/api/hls?url=`（后端拉取+去广告过滤，302 到 `/api/serve_m3u8/<sid>.m3u8`，让 XGPlayer 靠 `.m3u8` 后缀识别 HLS）。
- 去广告：`src/parser/m3u8_filter.py` `_smart_filter()` 按 `#EXT-X-DISCONTINUITY` 切段组→按切片目录归并「视频源」→正片=最长源，剔除广告并丢弃 discontinuity。
- Flask `threaded=True`；`_TEXT_CACHE`(20s TTL) 加速重复请求；开关 `smartAdRemove` 默认 true。
- 已删除：`/api/proxy`、`/api/m3u8`、`/api/m3u8_prepared`、`src/proxy.py`。

## 自定义标题栏与窗口拖拽/缩放（坐标运算在 Python 端）
- frameless + `FormBorderStyle(0)`；原生 `WM_NCLBUTTONDOWN` 不可行，唯一可靠路径是 pywebview `move()/resize()`。
- `static/js/window-chrome.js`：mousedown→`WindowChrome.begin`；mousemove 每帧把 `e.screenX/Y` 发给 `api.drag_to/resize_to`；mouseup→`api.drag_end`。
- 状态契约（防「点两次才生效」）：`drag_end` 返回 `{maximized,changed}`，JS 仅 `changed` 时回写；`toggle_maximize_window` 返回切换后真实态。标题栏 dblclick 与最大化按钮都走 `WindowChrome.toggleMaximize()`。
- 播放窗口双击视频区/全屏按钮 → `winFullscreenP()` → `WindowChrome.toggleFullscreen()`：
  桌面端走自实现的 `JsApi.toggle_fullscreen`（WebView2 不支持 DOM 全屏，必须改 Form 边界，且要 `ui_invoke`）；
  浏览器端回退 DOM `requestFullscreen`。

## 播放页关键不变量（改前端前必读）
- `.player-box` 必须 `height:100%`，否则 XGPlayer 容器高度 0（有声音无画面）。
- 分集渲染 `activateActiveSlide()`：预渲染 ±1（当前集 `initPlayer(box,true)` 播放、邻集先建好暂停、距当前 >2 销毁释放）。`canplay` 仅 `epIndex===currentActiveIndex` 才 `resize_to_aspect`。
- **键盘快捷键（空格=播放/暂停、←/→=±5s、F=全屏）：必须以「当前可视 slide」的 video 为唯一目标。** 实现：`getActiveVideo()` 在按键/点击当下读 `verticalSwiper.slides[activeIndex].querySelector('.player-box video')`（实时 DOM）。**勿改回依赖全局 `activePlayer.__box`**——`initPlayer` 是 async（内部 `await` 过滤 m3u8），播放器实例要等异步完成才进 `playerMap`；切到未预渲染的集时同步遍历拿不到新播放器，`activePlayer` 会残留上一集或置空，导致空格误控上一集/隐藏分集。本修复用实时查询彻底消除该竞态（`getActivePlayer()/getActiveSlideBox()` 同理用于「获取当前」与跳过片头片尾设置）。

## 编译 / 打包（PyInstaller）
- 入口 `main.py`；产物用 **onedir**（`build.spec` + `COLLECT`），不用 onefile。
  原因：`config_store`/`server` 用 `__file__` 定位 `data/` 与 `static/`，onefile 解包到临时目录且退出即删，会导致配置每次启动重置。
- `pythonnet` 是 `pywebview` 在 win32 下的隐式依赖（不在 pyproject 显式列出，但运行时必须），冻结时要用 `collect_all('pythonnet')` 收集 `Python.Runtime.dll`，并 hiddenimport `clr`/`pythonnet`/`webview.platforms.edgechromium`/`webview.platforms.winforms`/`src` 业务包。
- `data/` 已被 `.gitignore` 忽略（运行时生成的用户配置），**不打包**；`static/` 必须 `--add-data` 带出（落在 `dist/TSPlayer/_internal/static`）。
- 本地 `build.bat` 用 `python -m venv` 自建 venv 后 `pip install -r requirements.txt` 再 `pyinstaller build.spec`；GitHub Actions `build.yml` 在 `windows-latest` + Python 3.13 跑同样流程，打 tag 自动发 Release（压缩为 `TSPlayer-windows.zip`）。
- PyInstaller 6.x onedir 把依赖放进 `dist/TSPlayer/_internal/`，`src` 纯模块在 CArchive 内（非目录形式，属正常），`static` 为真实目录。

## 待办/观察
- `main.py` 中 `sys.setrecursionlimit(5000)` 会放大 CLR 递归冻结风险，建议保持默认（已靠私有属性规避）。
- 首页自动加载由 `app.cfg.autoLoadMore` 控制；`setupAutoLoad` 的 IntersectionObserver 仅依赖 `autoLoadMore`。
- CMS 分页总数读 `<list>` 的 `pagecount`/`totalpage`/`total` 属性（飞飞/苹果通用）。
