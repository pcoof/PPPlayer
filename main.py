"""PPPlayer 入口 — 启动 Flask + pywebview 桌面窗口"""

import sys
import os
import tomllib
import json
import ctypes
import time
import threading
import webview
from urllib import parse
from src.server import create_app, clear_popout_state

FLASK_HOST = "127.0.0.1"
FLASK_PORT = 19527

# 应用版本（自动更新比对基准）。格式 YYYYMMDD.N。
# 不写死源码：版本以 pyproject.toml 的 [project].version 为唯一来源，
# CI 构建期改写该字段并随 exe 嵌入（--add-data "pyproject.toml;."），
# 运行时由 get_app_version() 读取，确保「发布产物内置版本 == Git Tag」，避免更新器误报。


def _resource_path(rel):
    """冻结态（PyInstaller onefile）从 _MEIPASS 取附带文件；开发态从仓库根取。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def get_app_version():
    """读取 pyproject.toml 的 [project].version 作为版本号（随 exe 嵌入）。
    开发态回退到环境变量 PPPLAYER_VERSION 或 'dev'。"""
    try:
        with open(_resource_path("pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        v = (data.get("project") or {}).get("version", "")
        if v:
            return str(v)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return os.environ.get("PPPLAYER_VERSION", "dev")

# 自动更新：GitHub Releases 检测（必须与本仓库一致，否则会去查错误仓库而误报更新）
GITHUB_REPO = "pcoof/PPPlayer"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}"

# 独立播放器窗口：视频画面铺满整个窗口（标题栏/控制栏均以浮层叠加在画面上），
# 因此窗口宽高比直接等于视频原生宽高比，不再为控件条预留额外高度。
# 注：XGPlayer 自带的 .xg-video-container 默认 bottom:48px 会预留控件条空间，
# 已由 style.css 覆盖为 bottom:0，与这里的比例计算配套。


def ui_invoke(window, fn):
    """把「触碰原生 WinForms 控件」的操作强制切回 UI(STA) 线程执行，返回 fn 的结果。

    为什么必须这样做：
    pywebview 的 js_api 回调运行在 WebView2 的调度线程上，不是承载 WinForms Form 的
    UI 线程。若在该线程直接改 native.FormBorderStyle / native.SetBounds，WinForms 会
    重建窗口句柄（RecreateHandle），把子控件 WebView2 的 HWND 剥离，之后就抛：
        WebView2 initialization failed with exception:
        (0x80010108) 被调用的对象已与其客户端断开连接 (RPC_E_DISCONNECTED)
    表现正是「运行一段时间后窗口不见了、进程还在」。
    pywebview 自己的 move/resize/show/hide/maximize 全都用 self.Invoke(...) 做线程编组，
    这里为自定义的原生操作补齐同样的保护。
    """
    native = getattr(window, 'native', None)
    box = {}

    def _run():
        try:
            box['v'] = fn()
        except Exception as e:
            box['e'] = e

    if native is None:
        _run()
    else:
        try:
            if native.InvokeRequired:
                from System import Action
                native.Invoke(Action(_run))
            else:
                _run()
        except Exception as e:
            print(f"[ui_invoke] marshal to UI thread failed: {e}")
            if 'v' not in box and 'e' not in box:
                _run()

    if 'e' in box:
        print(f"[ui_invoke] call failed: {box['e']}")
        return None
    return box.get('v')


def _set_window_dwm_border(native, hide: bool):
    """最大化/全屏铺满工作区时去掉 frameless 窗口那圈 1px 半透明 DWM 边框与圆角；
    还原普通态时完整恢复。必须在 UI(STA) 线程调用（由调用方 ui_invoke 保证）。

    根因：pywebview create_window 默认 shadow=True，创建时用
    DwmExtendFrameIntoClientArea(hwnd, MARGINS(1,1,1,1)) 给窗口四周扩展 1px 边框。
    我们的最大化用 native.SetBounds(WorkingArea) 把窗口精确贴到工作区，但那圈 1px
    半透明边框仍留在窗口外沿——底边被任务栏挡住，于是只看得到顶部和右侧的透明边。
    pywebview 自带全屏（winforms.py toggle_fullscreen）铺满时也显式调用
    DwmSetWindowAttribute(33,1) 禁圆角 + (34,NONE) 隐藏边框解决同类问题，
    这里把同样的处理补到自定义最大化/全屏路径，并在还原时恢复，保证普通态仍有阴影/圆角。
    """
    if os.name != 'nt':
        return
    try:
        from ctypes import wintypes, Structure, c_int, byref
        hwnd = native.Handle.ToInt32()
        dwm = ctypes.windll.dwmapi

        class _MARGINS(Structure):
            _fields_ = [
                ('cxLeftWidth', c_int), ('cxRightWidth', c_int),
                ('cyTopHeight', c_int), ('cyBottomHeight', c_int),
            ]

        # 撤销 / 恢复 pywebview 设的 1px 边框扩展（全 0 = 不扩展；1 = pywebview 默认值）
        m = _MARGINS()
        if not hide:
            m.cxLeftWidth = m.cxRightWidth = m.cyTopHeight = m.cyBottomHeight = 1
        dwm.DwmExtendFrameIntoClientArea(hwnd, byref(m))

        # attr 33 = DWMWA_WINDOW_CORNER_PREFERENCE：1=DONOTROUND（铺满不要圆角），0=默认
        # attr 34 = DWMWA_BORDER_COLOR：0xFFFFFFFE=DWMWA_COLOR_NONE（隐藏边框），0xFFFFFFFF=默认
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ]
        corner = c_int(1 if hide else 0)
        border = c_int(0xFFFFFFFE if hide else 0xFFFFFFFF)
        dwm.DwmSetWindowAttribute(hwnd, 33, byref(corner), 4)
        dwm.DwmSetWindowAttribute(hwnd, 34, byref(border), 4)
    except Exception as e:
        # DWM 属性在个别老系统/主题下不可用；绝不能因它影响最大化本身
        print(f"[_set_window_dwm_border] skipped: {e}")


def _logical_work_area(window):
    """返回窗口所在屏幕的 WorkingArea，并换算成 pywebview 使用的逻辑像素。

    pywebview 的 create_window / window.move / window.resize 与 window.x/y/width/height
    都是逻辑像素；WinForms 的 Screen.WorkingArea 是物理像素。DPI 非 100% 时若混用，
    窗口会被二次放大且居中偏移。这里用 GetDpiForWindow(handle)/96 与 pywebview 后端
    保持同一套换算；任何异常都回退 None，让调用方使用原来的保守兜底。
    """
    if not window or os.name != 'nt':
        return None
    try:
        native = getattr(window, 'native', None)
        if native is None:
            return None
        import System.Windows.Forms as WinForms
        scr = WinForms.Screen.FromHandle(native.Handle)
        wa = scr.WorkingArea
        scale = 1.0
        try:
            hwnd = int(native.Handle.ToInt32())
            dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
            if dpi > 0:
                scale = dpi / 96.0
        except Exception:
            pass
        return (
            int(round(wa.X / scale)),
            int(round(wa.Y / scale)),
            int(round(wa.Width / scale)),
            int(round(wa.Height / scale)),
        )
    except Exception as e:
        print(f"[_logical_work_area] skipped: {e}")
        return None


def _centered_origin(work_area, width, height):
    """在逻辑工作区内计算居中起点，并做基本边界钳制。"""
    if not work_area:
        return None
    ax, ay, aw, ah = work_area
    x = ax + max(0, (int(aw) - int(width)) // 2)
    y = ay + max(0, (int(ah) - int(height)) // 2)
    return int(x), int(y)


def run_flask(updater=None, gist_sync=None):
    """在独立线程中启动 Flask 服务器"""
    # 启动时加载系统代理策略（cms/media/updater/gist 的 Session 已在导入时绑定）
    try:
        from src import net_proxy
        net_proxy.reload()
    except Exception:
        pass
    app = create_app()
    # 把版本与仓库信息注入 app.config，供 /api/version 暴露给前端「关于」页
    app.config['APP_VERSION'] = get_app_version()
    app.config['GITHUB_REPO'] = GITHUB_REPO
    app.config['GITHUB_REPO_URL'] = GITHUB_REPO_URL
    app.config['GITHUB_RELEASES_URL'] = GITHUB_RELEASES_URL
    # 应用内更新器单例：与托盘共用同一实例（前端轮询 /api/update/status 取状态）
    app.config['UPDATER'] = updater
    # GitHub Gist 数据同步单例（凭据独立存储于 data/gist_sync.json）
    app.config['GIST_SYNC'] = gist_sync
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.WARNING)

    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=False,
        use_reloader=False,
        threaded=True,   # 并发处理：播放起始时浏览器会同时请求页面/静态资源/m3u8(后端拉CDN)/变体，
                         # 单线程会串行排队→CDN 稍慢就表现为「一直加载不出」；开启后并发，消除阻塞。
    )


class PlayerWindow:
    """管理独立播放器窗口（作为 manager，持有主窗口/播放窗口引用与几何持久化）"""

    GEOMETRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'player_geometry.json')

    def __init__(self, port, main_window=None):
        self.port = port
        self._player_window = None
        self._active_playback_session = ''  # 当前活动播放会话 ID（关闭清理/进度上报都按它校验）
        self._main_window = main_window
        self._player_topmost = False   # 独立播放器窗口「窗口置顶」偏好（持久化到 player_geometry.json）
        self.bosskey = None       # BossKeyManager（老板键全局热键）
        self.tray = None          # TrayManager（系统托盘）

    # —— 关于页桥接：转交系统托盘（TrayManager 持有真实实现）——
    def check_update(self, show_no_update=True):
        """前端「关于」页「检查更新」按钮：转交托盘更新检查逻辑。"""
        if self.tray:
            self.tray.check_update(show_no_update=bool(show_no_update))

    def _open_url(self, url):
        """前端「关于」页外链：转交托盘的浏览器打开逻辑。"""
        if self.tray:
            self.tray._open_url(str(url))

    def _run_on_ui(self, func, *args, **kwargs):
        """调用 pywebview 自带的窗口方法（show / hide / restore / destroy …）。

        注意：这里**不能**再套一层 ui_invoke。pywebview 的窗口方法内部已经用
        Form.Invoke 编组到 UI 线程；而 destroy / evaluate_js 之类还会等待「由 UI
        线程投递的回调」置位信号量 —— 若在 UI 线程上同步调用它们，UI 线程既要等
        信号又无法泵消息，会直接死锁。所以自定义的原生操作用 ui_invoke，
        pywebview 自己的窗口方法则直接调用。

        原实现为了「唤醒」窗口顺手调了一次 evaluate_js('void(0)')：那是一次阻塞式
        IPC，窗口正在销毁 / WebView2 无响应时会把调用方一起拖住，已移除。
        """
        return func(*args, **kwargs)

    def _load_geometry(self):
        """读取已保存的窗口几何信息（含独立播放器窗口的置顶偏好）"""
        try:
            if os.path.exists(self.GEOMETRY_FILE):
                with open(self.GEOMETRY_FILE, 'r', encoding='utf-8') as f:
                    geom = json.loads(f.read())
                    if isinstance(geom, dict):
                        self._player_topmost = bool(geom.get('topmost', False))
                    return geom
        except Exception:
            pass
        return None

    def _save_geometry(self, window=None):
        """保存窗口几何信息（默认保存播放窗口）"""
        try:
            window = window or self._player_window
            if window:
                x = window.x
                y = window.y
                width = window.width
                height = window.height
                os.makedirs(os.path.dirname(self.GEOMETRY_FILE), exist_ok=True)
                with open(self.GEOMETRY_FILE, 'w', encoding='utf-8') as f:
                    json.dump({
                        "x": x, "y": y, "width": width, "height": height,
                        "topmost": bool(self._player_topmost),
                    }, f)
        except Exception:
            pass

    def set_player_topmost(self, enabled):
        """JS API 后端：设置独立播放器窗口始终置顶（WinForms TopMost）。

        只对播放窗口生效；主窗口不受影响。设置同时持久化，
        下次 open_player_window 重建窗口时直接带入 on_top 初值。
        """
        enabled = bool(enabled)
        self._player_topmost = enabled
        w = self._player_window
        if w:
            # on_top 的 setter 直接操作 WinForms Form.TopMost（未经 pywebview 线程编组），
            # 必须切回 UI(STA) 线程，否则跨线程改原生窗口有 RPC_E_DISCONNECTED 风险。
            ui_invoke(w, lambda: setattr(w, 'on_top', enabled))
        self._save_geometry(w)

    def show_main_window(self):
        """JS API: 显示并聚焦主窗口"""
        if self._main_window:
            try:
                self._run_on_ui(lambda: (
                    self._main_window.show(),
                    self._main_window.restore()
                ))
            except Exception:
                pass

    def show_player_window(self):
        """JS API: 显示并聚焦播放窗口（最小化/隐藏后恢复）。"""
        if self._player_window:
            try:
                self._run_on_ui(lambda: (
                    self._player_window.show(),
                    self._player_window.restore()
                ))
            except Exception:
                pass

    def toggle_window_fullscreen(self):
        """JS API: 切换播放窗口全屏状态"""
        if self._player_window:
            try:
                self._run_on_ui(self._player_window.toggle_fullscreen)
            except Exception:
                pass

    def open_player_window(self, state_json=""):
        """JS API: 打开独立无标题栏播放窗口，state_json 为播放状态 JSON 字符串。

        单实例约束：已有播放窗口时先走完整 close_player()（保存几何、清后端状态槽、
        清主界面播放条），再创建新窗口。旧实现直接 destroy，绕过清理，导致 bar 残留。
        """
        from src.server import set_popout_state

        # 先完整关闭旧会话，避免旧窗口迟到事件污染新会话
        if self._player_window:
            self.close_player()

        session_id = ''
        if state_json:
            try:
                state = json.loads(state_json)
                set_popout_state(state)
                if isinstance(state, dict):
                    session_id = str(state.get('sessionId') or '')
            except Exception:
                pass
        self._active_playback_session = session_id

        player_url = f"http://{FLASK_HOST}:{self.port}/player"

        geom = self._load_geometry() or {}
        # 每个窗口使用独立的 JS 桥接对象，避免多窗口共用 js_api 导致状态/目标串扰
        player_api = JsApi(self, 'player')
        kwargs = {
            "title": "PPPlayer",
            "url": player_url,
            "min_size": (320, 240),  # 横屏视频也需要能缩到 720p 以下，避免被迫产生黑边
            "resizable": True,
            "frameless": True,
            "easy_drag": False,
            "on_top": bool(self._player_topmost),  # 窗口置顶偏好（跨重开窗口保持）
            "js_api": player_api,
        }

        # 打开即居中：几何文件只保留宽高/置顶，x/y 由当前工作区重新计算。
        # 参考窗口优先取主窗口所在屏（播放器通常从主窗口弹出）；拿不到时让
        # pywebview 自己 CenterScreen。所有尺寸/坐标均为逻辑像素。
        width = max(320, int(geom.get("width", 480) or 480))
        height = max(240, int(geom.get("height", 854) or 854))
        wa = _logical_work_area(self._main_window or self._player_window)
        if wa:
            src_w, src_h = width, height
            max_w = max(320, int(wa[2] * 0.8))
            max_h = max(240, int(wa[3] * 0.8))
            if src_w > max_w:
                width, height = max_w, int(round(src_h * max_w / float(src_w)))
            if height > max_h:
                width, height = int(round(width * max_h / float(height))), max_h
            width = max(320, int(width))
            height = max(240, int(height))
            origin = _centered_origin(wa, width, height)
            if origin:
                kwargs["x"], kwargs["y"] = origin
        kwargs["width"] = width
        kwargs["height"] = height

        self._player_window = webview.create_window(**kwargs)
        player_api.attach(self._player_window)
        self._player_api = player_api

        # 原生 X / Alt+F4 关闭也要清理主界面状态。closing 事件在 UI(STA) 线程同步执行，
        # 这里只起一个守护线程做清理，避免 evaluate_js 在 UI 线程上等待自身消息泵而死锁。
        def _on_player_closing(sid=session_id, win=self._player_window):
            if self._player_window is not win:
                return  # 已经被新会话替换，旧窗口事件直接忽略

            def _cleanup():
                self._on_player_window_closing(sid, win)

            threading.Thread(target=_cleanup, daemon=True).start()

        try:
            self._player_window.events.closing += _on_player_closing
        except Exception:
            pass

    def _clear_main_playing_state(self, session_id=''):
        """通知主窗口清除 .now-playing-bar 所依赖的播放状态（幂等、按 session 校验）"""
        w = self._main_window
        if not w:
            return
        try:
            js = (
                "if(window.__app && typeof window.__app.clearPlayingState==='function'){"
                "window.__app.clearPlayingState(%s);}"
            ) % json.dumps(str(session_id or ''), ensure_ascii=False)
            w.evaluate_js(js)
        except Exception:
            pass

    def _on_player_window_closing(self, session_id='', window=None):
        """统一的播放窗口关闭后清理（可由 API 关闭或原生 closing 事件触发）。"""
        if window is not None and self._player_window is not window:
            return
        sid = str(session_id or self._active_playback_session or '')
        self._player_window = None
        if sid:
            self._active_playback_session = ''
        try:
            clear_popout_state(sid or None)
        except Exception:
            pass
        self._clear_main_playing_state(sid)

    def _resize_window_to_aspect(self, window, video_width, video_height):
        """根据视频原生分辨率把独立播放器窗口调整成相同宽高比（画面铺满、无黑边）。

        视频铺满整个窗口（标题栏/控制栏均为浮层叠加），因此窗口宽高比直接等于
        视频宽高比。竖屏以约 720px 视频高为基准，横屏以约 1280px 视频宽为基准，
        并限制在当前屏幕工作区的 80% 内、居中显示。
        """
        try:
            if not window or not video_width or not video_height:
                return
            ratio = float(video_width) / float(video_height)
            if ratio <= 0:
                return
            # 统一使用逻辑像素：WorkingArea 是物理像素，不能直接喂给 window.move/resize。
            wa = _logical_work_area(window)
            max_w = max(320, int(wa[2] * 0.8)) if wa else 1000
            max_h = max(240, int(wa[3] * 0.8)) if wa else 900
            w, h = 720.0 * ratio, 720.0
            if w > max_w:
                w, h = float(max_w), float(max_w) / ratio
            if h > max_h:
                w, h = float(max_h) * ratio, float(max_h)
            w = max(int(round(w)), 320)
            h = max(int(round(h)), 240)
            # 用整数尺寸再精确对齐一次视频比例
            h = max(int(round(w / ratio)), 240)
            if h > max_h:
                h = max_h
                w = max(int(round(h * ratio)), 320)
            origin = _centered_origin(wa, w, h) if wa else None
            if origin:
                self._run_on_ui(lambda: (window.move(origin[0], origin[1]), window.resize(w, h)))
            else:
                self._run_on_ui(lambda: window.resize(w, h))
            self._save_geometry(window)
        except Exception:
            pass

    def close_player(self):
        """关闭播放窗口，并通知主窗口清除正在播放状态（否则左上角标题残留）。"""
        sid = str(self._active_playback_session or '')
        win = self._player_window
        if win:
            try:
                self._save_geometry(win)
                self._run_on_ui(win.destroy)
            except Exception:
                pass
        # 统一清理：原生 closing 事件也会调用它，但这里是幂等的。
        self._on_player_window_closing(sid, win)

    def open_main_settings(self, tab="sources"):
        """JS API：唤起主窗口设置中心并切到指定标签页（播放窗口「编辑 API 源」调用）。"""
        w = self._main_window
        if not w:
            return
        try:
            w.show()
            w.restore()
            js = "if(window.__app){window.__app.showSettings=true;window.__app.settingsTab=%s;}" % repr(str(tab))
            w.evaluate_js(js)
        except Exception:
            pass


class BossKeyManager:
    """老板键 — 全局 OS 热键，按下隐藏/再次按下显示主窗口与播放窗口。

    用 ctypes + user32.RegisterHotKey 注册线程级热键，并由独立守护线程跑
    GetMessage 消息循环接收 WM_HOTKEY（必须在「注册热键的同一线程」里收消息，
    因此注册动作也放在该线程内完成，主线程只通过 PostThreadMessage 通知重载）。
    避免引入额外依赖；非 Windows 平台优雅降级（不可用）。
    """

    WM_HOTKEY = 0x0312
    WM_REREG = 0x0400 + 1

    MOD = {"ctrl": 2, "control": 2, "alt": 1, "shift": 4, "win": 8, "meta": 8}

    def __init__(self, manager):
        self._mgr = manager
        self._ctypes = ctypes
        self._user32 = None
        self._kernel32 = None
        try:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
        except Exception:
            self._user32 = None
        self._combo = ""
        self._hotkey_id = 1
        self._mods = 0
        self._vk = 0
        self._thread = None
        self._thread_id = None
        self._hidden = False

    # —— 解析 "Ctrl+Shift+H" 类组合为 (modifiers, virtualKey) ——
    def _parse(self, combo):
        parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
        mods = 0
        vk = None
        for p in parts:
            if p in self.MOD:
                mods |= self.MOD[p]
            elif len(p) == 1 and p.isalpha():
                vk = ord(p.upper())
            elif len(p) == 1 and p.isdigit():
                vk = ord(p)
            elif p.startswith("f") and p[1:].isdigit():
                n = int(p[1:])
                if 1 <= n <= 12:
                    vk = 0x70 + (n - 1)
            else:
                vk = None
        return mods, vk

    def _do_register(self):
        self._unregister()
        mods, vk = self._parse(self._combo)
        if vk is None:
            return
        try:
            if self._user32.RegisterHotKey(None, self._hotkey_id, mods, vk):
                self._mods, self._vk = mods, vk
        except Exception:
            pass

    def _unregister(self):
        if self._user32 and self._vk:
            try:
                self._user32.UnregisterHotKey(None, self._hotkey_id)
            except Exception:
                pass
        self._vk = 0

    def set_combo(self, combo):
        """主线程调用：设置老板键组合（空串即清除）。"""
        if not self._user32:
            return
        self._combo = combo or ""
        if self._thread is None:
            self._start_thread()
        else:
            try:
                self._user32.PostThreadMessageW(self._thread_id, self.WM_REREG, 0, 0)
            except Exception:
                pass

    def _start_thread(self):
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        from ctypes.wintypes import MSG
        user32 = self._user32
        self._thread_id = self._kernel32.GetCurrentThreadId()
        self._do_register()  # 在「注册线程」内完成首次注册（WM_HOTKEY 会回到本线程队列）
        m = MSG()
        while True:
            r = user32.GetMessageW(self._ctypes.byref(m), None, 0, 0)
            if r == 0:
                break
            if m.message == self.WM_HOTKEY:
                self._toggle()
            elif m.message == self.WM_REREG:
                self._do_register()
            user32.TranslateMessage(self._ctypes.byref(m))
            user32.DispatchMessageW(self._ctypes.byref(m))

    def _toggle(self):
        try:
            main = self._mgr._main_window
            player = self._mgr._player_window
            if not self._hidden:
                if main:
                    try:
                        main.hide()
                    except Exception:
                        pass
                if player:
                    try:
                        player.hide()
                    except Exception:
                        pass
                self._hidden = True
            else:
                if main:
                    try:
                        main.show()
                        main.restore()
                    except Exception:
                        try:
                            main.show()
                        except Exception:
                            pass
                if player:
                    try:
                        player.show()
                        player.restore()
                    except Exception:
                        try:
                            player.show()
                        except Exception:
                            pass
                self._hidden = False
        except Exception:
            pass


class TrayManager:
    """系统托盘图标 + 右键菜单（用 .NET NotifyIcon，pywebview 已依赖 .NET，无需额外安装）。

    右键菜单：显示窗口 | 网页打开 | GitHub | 退出
    单击托盘图标 → 显示/隐藏主窗口
    主窗口关闭时 → 最小化到托盘（受设置 closeToTray 控制）
    """

    GITHUB_URL = GITHUB_REPO_URL

    def __init__(self, manager, updater=None):
        self._mgr = manager
        self._updater = updater     # 与前端共用的 Updater 单例（无则降级为纯浏览器下载）
        self._notify = None
        self._menu = None
        self._ctx = None  # WindowsFormsContext
        self._mi_exit = None       # 退出项引用，便于在它前面插入「下载新版本」
        self._latest_update = None  # {'version': str, 'url': str}

    def create(self):
        """创建托盘图标。

        必须在 UI(STA) 线程调用：NotifyIcon 依赖 WinForms 的消息循环，
        在工作线程创建的典型表现就是「托盘图标根本不出现 / 菜单点了没反应」。
        webview.start(func=...) 的回调跑在工作线程，因此 main() 里等窗口句柄
        就绪后再 Invoke 到 UI 线程调用本方法（见 _on_loaded）。
        """
        if self._notify is not None:
            return True
        try:
            import System.Windows.Forms as WinForms
            # SystemIcons 在 System.Drawing 命名空间下，WinForms 上并没有这个属性；
            # 之前写成 WinForms.SystemIcons.Application，一旦 ExtractAssociatedIcon 失败
            # 就会抛 AttributeError 被外层吞掉 → 托盘整体创建失败、图标永不出现。
            from System.Drawing import Icon, SystemIcons

            self._menu = WinForms.ContextMenu()

            # 显示窗口
            mi_show = WinForms.MenuItem("显示主窗口")
            mi_show.Click += self._on_show
            self._menu.MenuItems.Add(mi_show)

            # 网页打开（在默认浏览器打开当前 web 地址）
            mi_web = WinForms.MenuItem("网页打开")
            mi_web.Click += self._on_web_open
            self._menu.MenuItems.Add(mi_web)

            # GitHub
            mi_github = WinForms.MenuItem("GitHub")
            mi_github.Click += self._on_github
            self._menu.MenuItems.Add(mi_github)

            # 检查更新（手动触发，后台查询 GitHub Releases）
            mi_check = WinForms.MenuItem("检查更新")
            mi_check.Click += self._on_check_update
            self._menu.MenuItems.Add(mi_check)

            self._menu.MenuItems.Add("-")

            # 退出
            mi_exit = WinForms.MenuItem("退出")
            mi_exit.Click += self._on_exit
            self._menu.MenuItems.Add(mi_exit)
            self._mi_exit = mi_exit

            self._notify = WinForms.NotifyIcon()
            self._notify.Text = "PPPlayer"
            self._notify.ContextMenu = self._menu
            # 图标：优先取项目自带 ico → 其次可执行文件关联图标 → 最后系统默认应用图标
            icon = None
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '', 'logo.ico')
            if os.path.exists(ico_path):
                try:
                    icon = Icon(ico_path)
                except Exception:
                    icon = None
            if icon is None:
                try:
                    icon = Icon.ExtractAssociatedIcon(sys.executable)
                except Exception:
                    icon = None
            self._notify.Icon = icon if icon is not None else SystemIcons.Application
            # 左键单击才切换窗口；右键交给上下文菜单（旧代码用 Click，右键也会触发 → 一右键窗口就被隐藏）
            self._notify.MouseClick += self._on_tray_mouse_click
            self._notify.Visible = True
            print("[Tray] icon created")
            # 启动后静默自动检测新版本（后台线程 + UI 线程气泡，不阻塞启动）
            self.check_update(show_no_update=False)
            return True
        except Exception as e:
            print(f"[Tray] create failed: {e}")
            self._notify = None
            return False

    def _on_show(self, sender, e):
        self._mgr.show_main_window()
        self._sync_bosskey(False)

    def _on_web_open(self, sender, e):
        """在默认浏览器打开 web 端地址。"""
        try:
            import webbrowser
            webbrowser.open(f"http://{FLASK_HOST}:{FLASK_PORT}")
        except Exception:
            pass

    def _on_github(self, sender, e):
        try:
            import webbrowser
            webbrowser.open(self.GITHUB_URL)
        except Exception:
            pass

    def _on_exit(self, sender, e):
        """彻底退出：销毁托盘 + 关闭所有窗口 + 退出进程。"""
        self.destroy()
        try:
            if self._mgr._player_window:
                self._mgr.close_player()
        except Exception:
            pass
        try:
            if self._mgr._main_window:
                self._mgr._main_window.destroy()
        except Exception:
            pass
        os._exit(0)

    def _on_check_update(self, sender, e):
        """右键菜单「检查更新」→ 显式检查并提示结果。"""
        self.check_update(show_no_update=True)

    def check_update(self, show_no_update=True):
        """检查 GitHub 最新 Release：发现新版本即后台无感下载，并弹气泡 + 在托盘菜单插入下载项。

        与前端「关于」页、标题栏按钮共用 self._updater 单例：下载在 daemon 线程进行，
        进度经 /api/update/status 供前端轮询，全程不阻塞 UI。下载完成前前端不打扰用户，
        仅当 ready 时标题栏才出现「更新」按钮。
        """
        import threading

        def _run():
            upd = self._updater
            if not upd:
                return
            try:
                status = upd.check()
                state = status.get("state")
                if state == "available":
                    # 后台无感下载：不弹下载框；下载完成后前端标题栏才出现「更新」按钮
                    upd.start_download()
                    ver = status.get("version") or ""
                    url = status.get("url") or GITHUB_RELEASES_URL
                    self._latest_update = {"version": ver, "url": url}
                    self._toast("PPPlayer", f"发现新版本 {ver}，正在后台下载更新…", "info")
                    self._add_update_menu_item(ver, url)
                elif state == "none" and show_no_update:
                    self._toast("PPPlayer", "当前已是最新版本", "info")
            except Exception as e:
                print(f"[Update] check failed: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _toast(self, title, text, kind="info"):
        """在 UI 线程弹出托盘气泡（必须 UI 线程，否则 WinForms 抛跨线程异常）。"""
        w = self._mgr._main_window

        def _act():
            try:
                from System.Windows.Forms import ToolTipIcon
                icon = ToolTipIcon.Info if kind == "info" else ToolTipIcon.Warning
                self._notify.ShowBalloonTip(5000, title, text, icon)
            except Exception as e:
                print(f"[Update] toast failed: {e}")

        if self._notify is None:
            return
        if w is not None:
            ui_invoke(w, _act)
        else:
            try:
                _act()
            except Exception:
                pass

    def _add_update_menu_item(self, ver, url):
        """在「退出」项之前插入「下载新版本 vX」菜单项（必须在 UI 线程操作 WinForms）。"""
        w = self._mgr._main_window

        def _act():
            try:
                import System.Windows.Forms as WinForms
                for mi in list(self._menu.MenuItems):
                    if (mi.Text or "").startswith("下载新版本"):
                        self._menu.MenuItems.Remove(mi)
                mi_dl = WinForms.MenuItem(f"下载新版本 {ver}")
                mi_dl.Click += lambda s, e: self._open_url(url)
                idx = self._menu.MenuItems.IndexOf(self._mi_exit) if self._mi_exit else self._menu.MenuItems.Count - 1
                self._menu.MenuItems.Add(idx, mi_dl)
            except Exception as e:
                print(f"[Update] menu add failed: {e}")

        if self._notify is None:
            return
        if w is not None:
            ui_invoke(w, _act)
        else:
            try:
                _act()
            except Exception:
                pass

    def _open_url(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    def _sync_bosskey(self, hidden):
        """让老板键的隐藏态与托盘操作保持一致，避免下次按老板键方向反了。"""
        try:
            if self._mgr.bosskey:
                self._mgr.bosskey._hidden = bool(hidden)
        except Exception:
            pass

    def _on_tray_mouse_click(self, sender, e):
        """左键单击托盘图标 → 切换主窗口显示/隐藏（右键不处理，留给上下文菜单）。"""
        try:
            import System.Windows.Forms as WinForms
            if e.Button != WinForms.MouseButtons.Left:
                return
        except Exception:
            pass

        w = self._mgr._main_window
        if not w:
            return
        # pywebview 的 Window 不维护可见性（show()/hide() 不会更新 hidden 字段），
        # 旧代码 getattr(w,'visible',True) 恒为 True → 单击永远只会「隐藏」。
        # 这里直接读原生 Form.Visible（切到 UI 线程读取）。
        visible = ui_invoke(w, lambda: bool(w.native.Visible))
        if visible is None:
            visible = True
        if visible:
            try:
                w.hide()
            except Exception:
                pass
            self._sync_bosskey(True)
        else:
            self._mgr.show_main_window()
            self._sync_bosskey(False)

    def destroy(self):
        try:
            if self._notify:
                n = self._notify

                def _kill():
                    n.Visible = False
                    n.Dispose()

                ui_invoke(self._mgr._main_window, _kill)
                self._notify = None
        except Exception:
            pass


def set_autostart(enable: bool) -> bool:
    """设置/取消开机自启（Windows 注册表 CurrentVersion\\Run）。

    成功返回 True，失败（非 Windows / 权限不足）返回 False。
    """
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        exe_path = sys.executable
        # 如果是 python 脚本运行，用 pythonw.exe 避免黑框
        if exe_path.lower().endswith('python.exe'):
            exe_path = exe_path.replace('python.exe', 'pythonw.exe')
        script_path = os.path.abspath(sys.argv[0])
        value = f'"{exe_path}" "{script_path}"'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, "PPPlayer", 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, "PPPlayer")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[Autostart] set failed: {e}")
        return False


def downloads_dir() -> str:
    """返回系统「下载」目录（读注册表 Shell Folders，失败回退 ~/Downloads）。"""
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                p = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
                if p and os.path.isdir(p):
                    return p
        except Exception:
            pass
    p = os.path.join(os.path.expanduser('~'), 'Downloads')
    return p if os.path.isdir(p) else os.path.expanduser('~')


def get_autostart() -> bool:
    """查询当前是否已设置开机自启。"""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, "PPPlayer")
            winreg.CloseKey(key)
            return bool(val)
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False


class JsApi:
    """每窗口独立的 JS 桥接对象。

    坐标运算放在 Python 端完成（Python 永远持有窗口的真实几何，最可靠），
    JS 仅把鼠标的 screenX/screenY 转发过来驱动 move/resize。

    这样彻底规避：前端坐标系误差、IPC 节流死锁（rAF/inFlight）、以及 frameless 下
    原生 WM_NCLBUTTONDOWN 失效（pywebview 的 frameless 把 FormBorderStyle 设为 None，
    连标题栏区域都移除，HTCAPTION/HTLEFT 等命中码全部 no-op）等问题。
    """

    def __init__(self, manager, kind):
        self.manager = manager
        self.kind = kind          # 'main' | 'player'
        self._w = None
        self._is_maximized = False
        self._is_snapped = False       # 自绘 Snap 菜单贴靠态（半屏/象限），区别于最大化
        self._restore_geom = None
        self._drag = None
        self._is_fullscreen = False
        self._fs_x = self._fs_y = self._fs_w = self._fs_h = 0
        self._was_maximized_before_fs = False  # 进入全屏前是否为最大化，退出时恢复
        # 独立播放器窗口的原生视频比例（videoWidth / videoHeight），用于拖动缩放时锁定比例、消除黑边
        self._video_aspect = None
        self._video_px = None          # (videoWidth, videoHeight) 原始分辨率，仅记录
        self._aspect_syncing = False   # 防止 sync_window_aspect 自触发的 resize 递归

    def attach(self, window):
        self._w = window

    # —— 外部链接 / 更新 ——
    def open_external(self, url):
        """在系统默认浏览器中打开外部链接（关于页的项目主页 / 更新日志等）。"""
        if url and self.manager:
            self.manager._open_url(str(url))

    def check_update(self, show_no_update=True):
        """前端「关于」页「检查更新」按钮：复用 AppManager 的更新检查逻辑。"""
        if self.manager:
            self.manager.check_update(show_no_update=bool(show_no_update))

    # —— 拖拽 / 缩放（Python 端完成坐标运算）——
    def drag_start(self, sx, sy):
        w = self._w
        if not w:
            return
        # 一旦开始自由拖动就不再是「菜单贴靠」态（与自研 Aero Snap 保持一致）
        self._is_snapped = False
        # 仅记录按下时的几何与光标；【不在此时还原】。
        # 与 Win 原生一致：单击（无移动）保持原样；只有真正拖动才还原（见 drag_to）。
        self._drag = {
            'x': int(w.x), 'y': int(w.y),
            'w': int(w.width), 'h': int(w.height),
            'sx': int(sx), 'sy': int(sy),
            'was_max': bool(self._is_maximized),
            'maxGeom': (int(w.x), int(w.y), int(w.width), int(w.height)),
            'snapped': None,
        }

    def drag_to(self, sx, sy):
        w = self._w
        d = self._drag
        if not w or not d or d.get('mode') == 'resize':
            return
        sx, sy = int(sx), int(sy)

        # ── 从「最大化」拖出：仅在真正移动（超过阈值）时才还原 ──
        # 单击（位移 < 阈值）视为普通点击，保持最大化，不还原（修复「最大化后点一下就还原」）。
        if d.get('was_max') and self._is_maximized and not d.get('_restored'):
            dx0, dy0 = sx - d['sx'], sy - d['sy']
            if abs(dx0) < 4 and abs(dy0) < 4:
                return  # 视为单击
            # 真正开始拖动：还原到正常几何，并让光标保持在标题栏同一相对位置跟随
            g = self._restore_geom or (
                d['sx'] - 200, d['sy'] - 200,
                max(int(int(w.width) * 0.6), 360 if self.kind == 'player' else 800),
                max(int(int(w.height) * 0.6), 640 if self.kind == 'player' else 600),
            )
            maxL, maxT, maxW, maxH = d['maxGeom']
            fx = (sx - maxL) / maxW if maxW else 0.5
            new_left = sx - fx * g[2]
            new_top = maxT  # 光标保持在标题栏同偏移（maxT 即工作区顶）
            try:
                w.move(int(round(new_left)), int(round(new_top)))
                w.resize(int(g[2]), int(g[3]))
            except Exception:
                pass
            self._is_maximized = False
            d['_restored'] = True
            d['_maxGate'] = True  # 刚拖出，顶部吸附暂禁用，避免瞬间又被重新最大化
            d['x'], d['y'] = int(round(new_left)), int(round(new_top))
            d['w'], d['h'] = int(g[2]), int(g[3])
            d['sx'], d['sy'] = sx, sy
            return  # 本帧只做还原+定位，下一帧进入正常拖拽

        # ── 贴边吸附（Aero Snap）：基于光标所在屏幕的「工作区」（排除任务栏）判定 ──
        # 拖到屏幕顶部 → 最大化；拖到左/右边缘 → 贴左/右半屏（与 Win 原生一致）。
        zone = None
        wa = self._screen_working_area(sx, sy)
        if wa is not None:
            T = 16  # 吸附阈值（逻辑像素）；越大越容易触发
            if sy <= wa['y'] + T:
                zone = 'max'
            elif sx <= wa['x'] + T:
                zone = 'left'
            elif sx >= wa['x'] + wa['width'] - T:
                zone = 'right'
            # 刚从最大化拖出时，顶部吸附门控：离开顶部区后才恢复顶部吸附能力，
            # 否则光标还在顶部区会导致一拖出就瞬间重新最大化（抖动）。
            if zone == 'max' and d.get('_maxGate'):
                zone = None
            if d.get('_maxGate') and sy > wa['y'] + T:
                d['_maxGate'] = False

        if zone:
            if d.get('snapped') != zone:
                # 进入吸附区的瞬间，记录当前「自由拖拽几何」，便于离开吸附区时还原
                free_x = d['x'] + (sx - d['sx'])
                free_y = d['y'] + (sy - d['sy'])
                d['pre_geom'] = (free_x, free_y, int(w.width), int(w.height))
                d['snapped'] = zone
            try:
                if zone == 'max':
                    w.move(int(wa['x']), int(wa['y']))
                    w.resize(int(wa['width']), int(wa['height']))
                elif zone == 'left':
                    w.move(int(wa['x']), int(wa['y']))
                    w.resize(int(wa['width'] // 2), int(wa['height']))
                elif zone == 'right':
                    w.move(int(wa['x'] + wa['width'] // 2), int(wa['y']))
                    w.resize(int(wa['width'] - wa['width'] // 2), int(wa['height']))
            except Exception:
                pass
        else:
            # 离开吸附区：还原到进入前的自由几何，并把锚点重置为当前光标，使继续拖拽平滑
            if d.get('snapped') is not None:
                pg = d.get('pre_geom')
                if pg:
                    try:
                        w.move(int(round(pg[0])), int(round(pg[1])))
                        w.resize(int(pg[2]), int(pg[3]))
                    except Exception:
                        pass
                    d['x'], d['y'] = int(round(pg[0])), int(round(pg[1]))
                    d['sx'], d['sy'] = sx, sy
                d['snapped'] = None
            nx = d['x'] + (sx - d['sx'])
            ny = d['y'] + (sy - d['sy'])
            try:
                w.move(int(round(nx)), int(round(ny)))
            except Exception:
                pass

    def _screen_working_area(self, sx, sy):
        """返回光标所在屏幕的工作区矩形（排除任务栏），逻辑像素。失败返回 None。"""
        try:
            import System.Windows.Forms as WinForms
            from System.Drawing import Point
            scr = WinForms.Screen.FromPoint(Point(int(sx), int(sy)))
            wa = scr.WorkingArea
            return {'x': int(wa.X), 'y': int(wa.Y),
                    'width': int(wa.Width), 'height': int(wa.Height)}
        except Exception:
            return None

    def resize_start(self, hit, sx, sy):
        w = self._w
        if not w or self._is_maximized:
            return
        self._drag = {
            'mode': 'resize',
            'hit': int(hit),
            'x': int(w.x), 'y': int(w.y),
            'w': int(w.width), 'h': int(w.height),
            'sx': int(sx), 'sy': int(sy),
        }

    def _aspect_fit(self, base, pointer=None, by_width=True):
        """按视频原生宽高比，把拖拽驱动的维度(base)换算成合适的窗口 (宽, 高)。

        仅在独立播放器窗口、拿到 videoWidth/videoHeight 后调用。
        约束：最小 320x240、不超过光标所在屏幕工作区；超界时换另一维作基准，
        保证最终比例与视频一致（无多余黑边）。
        """
        ratio = float(self._video_aspect or 1)
        min_w, min_h = 320, 240
        wa = self._screen_working_area(pointer[0], pointer[1]) if pointer else None
        max_w = max(min_w, (wa['width'] - 8) if wa else 4096)
        max_h = max(min_h + 8, (wa['height'] - 8) if wa else 2160)
        if by_width:
            w = min(max(int(round(base)), min_w), max_w)
            h = int(round(w / ratio))
            if h < min_h:
                h = min_h
                w = int(round(h * ratio))
            if w > max_w:
                w = max_w
                h = int(round(w / ratio))
            w = min(max(w, min_w), max_w)
            h = min(max(int(round(w / ratio)), min_h), max_h)
        else:
            h = min(max(int(round(base)), min_h), max_h)
            w = int(round(h * ratio))
            if w < min_w:
                w = min_w
                h = int(round(w / ratio))
            if w > max_w:
                w = max_w
                h = int(round(w / ratio))
            if h > max_h:
                h = max_h
                w = int(round(h * ratio))
        return max(int(w), min_w), min(max(int(h), min_h), max_h)

    def resize_to(self, sx, sy):
        w = self._w
        d = self._drag
        if not w or not d or d.get('mode') != 'resize':
            return
        dx = int(sx) - d['sx']
        dy = int(sy) - d['sy']
        nw, nh, nx, ny = d['w'], d['h'], d['x'], d['y']
        hit = d['hit']
        if hit in (10, 13, 16):      # 左
            nw = d['w'] - dx
            nx = d['x'] + dx
        if hit in (11, 14, 17):      # 右
            nw = d['w'] + dx
        if hit in (12, 13, 14):      # 上
            nh = d['h'] - dy
            ny = d['y'] + dy
        if hit in (15, 16, 17):      # 下
            nh = d['h'] + dy
        min_w, min_h = (320, 240) if self.kind == 'player' else (800, 600)
        nw = max(int(round(nw)), min_w)
        nh = max(int(round(nh)), min_h)

        # 独立播放器：按视频原生比例规整尺寸，拖动边缘时画面等比缩放、不产生黑边。
        # 左右边缘 → 宽度驱动；上下边缘 → 高度驱动；四角 → 取相对位移更大的维度驱动。
        if self.kind == 'player' and self._video_aspect and not self._is_maximized:
            by_width = hit in (10, 11)
            if hit in (13, 14, 16, 17):  # 四角：相对变化更大的一维作基准
                by_width = abs(dx) / max(d['w'], 1) >= abs(dy) / max(d['h'], 1)
            base = nw if by_width else nh
            nw, nh = self._aspect_fit(base, (int(sx), int(sy)), by_width)
            # 左/上边缘锚点跟着新尺寸走，避免缩放时窗口"跳动"
            if hit in (10, 13, 16):
                nx = d['x'] + (d['w'] - nw)
            if hit in (12, 13, 14):
                ny = d['y'] + (d['h'] - nh)
        try:
            w.move(int(round(nx)), int(round(ny)))
            w.resize(int(nw), int(nh))
        except Exception:
            pass

    def drag_end(self):
        d = self._drag
        changed = False
        if d:
            zone = d.get('snapped')
            if zone == 'max':
                # 在顶部吸附区释放 → 提交为「最大化」（边缘缩放随之被禁止）
                if d.get('pre_geom'):
                    self._restore_geom = (int(d['pre_geom'][0]), int(d['pre_geom'][1]),
                                          int(d['pre_geom'][2]), int(d['pre_geom'][3]))
                elif self._w:
                    self._restore_geom = (int(self._w.x), int(self._w.y),
                                          int(self._w.width), int(self._w.height))
                self._is_maximized = True
                changed = True
            elif d.get('_restored'):
                # 本次拖拽确实从最大化拖出并还原了 → 释放后处于正常态
                self._is_maximized = False
                changed = True
            # 否则（如：单击未移动、或非吸附区普通拖动）保持原 _is_maximized 不变
        self._drag = None
        # 仅当「吸附动作真正改变了最大化态」时才让前端同步，避免普通单击/双击的
        # 异步 drag_end 回调把 toggleMaximize 刚刚设定的状态又覆盖回去（导致需点两次）。
        return {'maximized': bool(self._is_maximized), 'changed': changed}

    def get_window_rect(self):
        w = self._w
        if w:
            try:
                return {'x': int(w.x), 'y': int(w.y), 'width': int(w.width), 'height': int(w.height)}
            except Exception:
                pass
        return None

    def set_window_topmost(self, enabled):
        """JS API：独立播放器窗口「始终置顶」。主窗口调用直接忽略。

        浏览器打开 player.html 时 pywebview 不存在，前端不会走这里。
        """
        if self.kind != 'player' or not self.manager:
            return False
        try:
            self.manager.set_player_topmost(bool(enabled))
            return True
        except Exception:
            return False

    def sync_window_aspect(self):
        """JS API：窗口尺寸变化后把独立播放窗口校正到视频原生宽高比。

        仅在「有原生分辨率 / 非最大化 / 非全屏 / 非拖拽中」时生效；
        前端 resize 事件做了防抖，这里再做一次偏差阈值判断，避免 resize 递归。
        """
        if self.kind != 'player' or not self._video_aspect or self._is_maximized or self._is_fullscreen or self._drag:
            return False
        w = self._w
        if not w or self._aspect_syncing:
            return False
        try:
            ratio = float(self._video_aspect)
            if ratio <= 0:
                return False
            cw, ch = int(w.width), int(w.height)
            if cw <= 0 or ch <= 0:
                return False
            # 视频铺满全窗：窗口宽高比直接等于视频宽高比，无控件条补偿
            nw_by_w, nh_by_w = cw, int(round(cw / ratio))
            nw_by_h, nh_by_h = int(round(ch * ratio)), ch
            dw = abs(nw_by_w - cw) + abs(nh_by_w - ch)
            dh = abs(nw_by_h - cw) + abs(nh_by_h - ch)
            by_width = dw <= dh
            base = cw if by_width else ch
            nw, nh = self._aspect_fit(base, None, by_width)
            if abs(nw - cw) <= 2 and abs(nh - ch) <= 2:
                return False
            self._aspect_syncing = True
            try:
                w.resize(nw, nh)
            finally:
                self._aspect_syncing = False
            return True
        except Exception:
            return False

    def minimize_window(self):
        w = self._w
        if w:
            try:
                w.minimize()
            except Exception:
                pass

    def hide_window(self):
        """隐藏窗口（到托盘，任务栏也不显示）。"""
        w = self._w
        if w:
            try:
                w.hide()
            except Exception:
                pass

    def toggle_maximize_window(self, is_max=False):
        """在「最大化（贴合工作区，保留任务栏）」与「还原」之间切换。
        is_max 由 JS 端传入（window-chrome.js 维护的 per-window 状态），避免主窗/播放窗串扰。

        关键修复（最大化后遮挡任务栏）：
        frameless 窗口若直接 WindowState=Maximized 会铺满整屏（含任务栏，等同全屏），
        必须手动贴合 WorkingArea。旧实现读取 WorkingArea 后改用 pywebview 的
        w.move()/w.resize() 落地——这套封装按 DPI 逻辑像素解释尺寸，在缩放≠100% 的
        屏幕上会把物理像素的 WorkingArea 二次放大，窗口超出工作区盖住任务栏。
        现改为在 UI 线程内用 native.SetBounds(...) 直接设置，与「进入全屏铺满整屏」
        「退出全屏恢复最大化」走完全相同的 API + 坐标系（见 toggle_fullscreen），
        尺寸来源与消费端均为 Screen 的物理像素，缩放多少屏都不会溢出。
        """
        w = self._w
        if not w or not getattr(w, 'native', None):
            return bool(self._is_maximized)
        target = bool(is_max)

        def _apply():
            import System.Windows.Forms as WinForms
            native = w.native
            # frameless 窗口已是 FormBorderStyle.None；仅在样式确实不同时才赋值，
            # 否则重复赋值会触发 RecreateHandle 剥离 WebView2 HWND（RPC_E_DISCONNECTED）。
            none_style = WinForms.FormBorderStyle(0)  # None
            if native.FormBorderStyle != none_style:
                native.FormBorderStyle = none_style
            normal_state = WinForms.FormWindowState(0)  # Normal
            if native.WindowState != normal_state:
                native.WindowState = normal_state

            if self._is_maximized and not target:
                # —— 还原到最大化前的正常几何 ——
                g = self._restore_geom or (120, 120, 900, 640)
                native.SetBounds(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                _set_window_dwm_border(native, False)  # 恢复普通态的 1px 阴影边框与圆角
                self._is_maximized = False
            elif (not self._is_maximized) and target:
                # —— 最大化：先记录正常几何，再贴合当前显示器工作区 ——
                b = native.Bounds
                self._restore_geom = (int(b.X), int(b.Y), int(b.Width), int(b.Height))
                wa = WinForms.Screen.FromHandle(native.Handle).WorkingArea
                native.SetBounds(int(wa.X), int(wa.Y), int(wa.Width), int(wa.Height))
                _set_window_dwm_border(native, True)  # 去掉 1px 透明边框 + 圆角，贴满工作区不留缝
                self._is_maximized = True
                self._is_snapped = False  # 从贴靠态点最大化 → 归入最大化态
            # 返回切换后的真实最大化态，让前端以 Python 端为准，杜绝 JS/Python 状态错位。
            return bool(self._is_maximized)

        try:
            return bool(ui_invoke(w, _apply))
        except Exception as e:
            print(f"[toggle_maximize_window] failed: {e}")
            return bool(self._is_maximized)

    @staticmethod
    def _snap_rect(zone, X, Y, W, H, min_w, min_h):
        """纯函数：计算自绘 Snap 菜单各 zone 相对工作区的贴靠矩形。
        返回 (rect_dict, effective_zone, final_rect)；restore 返回 (None, 'restore', None)。
        含 min_size 退化兜底：象限放不下→半屏，半屏放不下→整屏，绝不越界盖任务栏。
        """
        if zone == 'restore':
            return None, 'restore', None
        half_w, half_h = W // 2, H // 2
        rects = {
            'max':   (X, Y, W, H),
            'left':  (X, Y, half_w, H),
            'right': (X + half_w, Y, W - half_w, H),
            'tl': (X, Y, half_w, half_h),
            'tr': (X + half_w, Y, half_w, half_h),
            'bl': (X, Y + half_h, half_w, H - half_h),
            'br': (X + half_w, Y + half_h, half_w, H - half_h),
        }
        if zone not in rects:
            zone = 'max'
        effective = zone
        # 象限放不下 → 对应半屏
        if effective in ('tl', 'bl', 'tr', 'br'):
            r = rects[effective]
            if r[2] < min_w or r[3] < min_h:
                effective = 'left' if effective in ('tl', 'bl') else 'right'
        # 半屏放不下 → 整屏
        if effective in ('left', 'right'):
            r = rects[effective]
            if r[2] < min_w or r[3] < min_h:
                effective = 'max'
        return rects, effective, rects[effective]

    def snap_layout(self, zone):
        """自绘 Snap Layout 菜单：按 zone 把窗口贴靠到工作区对应分区。
        zone ∈ max|left|right|tl|tr|bl|br|restore。返回 {maximized, snapped}。
        走 ui_invoke + native.SetBounds（WorkingArea 物理像素），与 toggle_maximize_window 同一坐标系，
        绝不用 w.move/w.resize（DPI 逻辑像素会二次放大盖住任务栏）。
        """
        w = self._w
        if not w or not getattr(w, 'native', None):
            return {'maximized': bool(self._is_maximized), 'snapped': bool(self._is_snapped)}
        zone = str(zone or '')

        def _apply():
            import System.Windows.Forms as WinForms
            native = w.native
            min_w, min_h = (800, 600) if self.kind == 'main' else (320, 240)
            wa = WinForms.Screen.FromHandle(native.Handle).WorkingArea
            X, Y, W, H = int(wa.X), int(wa.Y), int(wa.Width), int(wa.Height)

            if zone == 'restore':
                g = self._restore_geom
                if g:
                    native.SetBounds(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                _set_window_dwm_border(native, False)
                self._is_maximized = False
                self._is_snapped = False
                return {'maximized': False, 'snapped': False}

            _rects, effective, rect = self._snap_rect(zone, X, Y, W, H, min_w, min_h)
            if not rect:
                return {'maximized': bool(self._is_maximized), 'snapped': bool(self._is_snapped)}
            rx, ry, rw, rh = rect

            # 仅「普通态」记录还原几何；最大化/已贴靠态保留此前几何，供 restore 回到原始窗口
            if not self._is_maximized and not self._is_snapped:
                b = native.Bounds
                self._restore_geom = (int(b.X), int(b.Y), int(b.Width), int(b.Height))

            native.SetBounds(rx, ry, rw, rh)
            if effective == 'max':
                _set_window_dwm_border(native, True)   # 最大化：去 1px 边框 + 圆角
                self._is_maximized = True
                self._is_snapped = False
            else:
                _set_window_dwm_border(native, False)  # 半屏/象限：保留圆角 + 阴影
                self._is_maximized = False
                self._is_snapped = True
            return {'maximized': bool(self._is_maximized), 'snapped': bool(self._is_snapped)}

        try:
            result = ui_invoke(w, _apply)
            if isinstance(result, dict):
                return result
        except Exception as e:
            print(f"[snap_layout] failed: {e}")
        return {'maximized': bool(self._is_maximized), 'snapped': bool(self._is_snapped)}

    def close_window(self):
        if self.kind == 'player':
            self.manager.close_player()
        elif self._w:
            try:
                self._w.destroy()
            except Exception:
                pass

    def report_play_progress(self, item_json="", src_idx=0, ep_idx=0, current_time=0, session_id=""):
        """播放窗口上报当前播放进度 → 主窗口保存播放历史。

        播放窗口切换集数/开始播放时调用。传递完整的 item JSON（含 vod_id/vod_name/vod_pic 等），
        主窗口端直接设置 currentItem 再调用 saveHistory，避免因 currentItem 为空而跳过保存。

        session_id 用于丢弃迟到上报：窗口关闭后旧播放器的进度不得把播放条重新复活。
        """
        w = self.manager._main_window
        if not w:
            return
        sid = str(session_id or '')
        active = str(getattr(self.manager, '_active_playback_session', '') or '')
        if sid:
            # 有 session 的调用必须在活动会话内；无 session 的老版本调用保持兼容。
            if not active or active != sid:
                return
        try:
            import json
            item = json.loads(item_json) if item_json else {}
            # 用 JSON 字符串安全注入，避免特殊字符导致 JS 语法错误
            item_js = json.dumps(item, ensure_ascii=False)
            js = (
                "if(window.__app){"
                "if(!window.__app.currentItem || window.__app.currentItem.vod_id!==%s){"
                "window.__app.currentItem=%s;"
                "}"
                "window.__app.playSrc=%d;"
                "window.__app.playEp=%d;"
                "window.__app.saveHistory(%s);"
                "}"
            ) % (
                json.dumps(str(item.get('vod_id', '')), ensure_ascii=False),
                item_js,
                int(src_idx),
                int(ep_idx),
                repr(float(current_time or 0))
            )
            w.evaluate_js(js)
        except Exception as e:
            print(f"[report_play_progress] failed: {e}")

    # —— 跨窗口操作转发给 manager ——
    def show_main_window(self):
        self.manager.show_main_window()

    def show_player_window(self):
        self.manager.show_player_window()

    def set_boss_key(self, combo=""):
        """JS API：设置老板键组合（空串清除）。由主窗口设置中心调用。"""
        if self.manager.bosskey:
            self.manager.bosskey.set_combo(combo or "")

    def set_autostart(self, enable=False):
        """JS API：设置开机自启。"""
        return set_autostart(bool(enable))

    def get_autostart(self):
        """JS API：查询当前是否已设置开机自启。"""
        return get_autostart()

    def save_text_file(self, filename="export.json", content=""):
        """JS API：把文本保存到用户选择的文件（桌面端「导出源 / 导出全部」走这里）。

        为什么需要它：WebView2 里 `<a download href=blob:...>` 走的是浏览器下载通道，
        而 pywebview 默认 settings['ALLOW_DOWNLOADS']=False，会在 DownloadStarting 里
        直接 args.Cancel = True —— 于是桌面端点「导出」完全没反应（浏览器里却正常）。
        这里改由 Python 端弹原生「另存为」对话框并落盘，行为可控且有明确反馈。

        返回 {'ok': bool, 'path': str, 'canceled': bool, 'error': str}
        """
        w = self._w or self.manager._main_window
        safe_name = os.path.basename(str(filename or 'export.json')).strip() or 'export.json'
        text = content if isinstance(content, str) else str(content or '')
        init_dir = downloads_dir()

        def _pick():
            # SaveFileDialog 是模态对话框，必须在 UI 线程 ShowDialog，否则可能挂死/抛异常
            try:
                try:
                    from webview import FileDialog
                    dialog_type = FileDialog.SAVE
                except Exception:
                    dialog_type = 30  # FileDialog.SAVE
                res = w.create_file_dialog(
                    dialog_type, init_dir, False, safe_name,
                    ('JSON 文件 (*.json)', '所有文件 (*.*)'),
                )
            except Exception as e:
                return {'state': 'error', 'msg': str(e)}
            if not res:
                return {'state': 'cancel'}
            path = res if isinstance(res, str) else res[0]
            return {'state': 'ok', 'path': str(path)}

        picked = ui_invoke(w, _pick) if w else {'state': 'error', 'msg': 'no window'}
        picked = picked or {'state': 'error', 'msg': 'dialog failed'}

        target = None
        if picked.get('state') == 'cancel':
            return {'ok': False, 'canceled': True, 'path': '', 'error': ''}
        if picked.get('state') == 'ok':
            target = picked.get('path')
        else:
            # 对话框不可用时兜底：直接写到「下载」目录，绝不让用户感觉「点了没反应」
            base, ext = os.path.splitext(safe_name)
            target = os.path.join(init_dir, safe_name)
            i = 1
            while os.path.exists(target):
                target = os.path.join(init_dir, f"{base}({i}){ext}")
                i += 1

        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            with open(target, 'w', encoding='utf-8') as f:
                f.write(text)
            return {'ok': True, 'canceled': False, 'path': target, 'error': ''}
        except Exception as e:
            return {'ok': False, 'canceled': False, 'path': target or '', 'error': str(e)}

    def quit_app(self):
        """JS API：彻底退出应用（托盘菜单的退出也走这里）。"""
        if self.manager.tray:
            self.manager.tray.destroy()
        os._exit(0)

    def open_main_settings(self, tab="sources"):
        self.manager.open_main_settings(tab)

    def open_player_window(self, state_json=""):
        self.manager.open_player_window(state_json)

    def close_player(self):
        self.manager.close_player()

    def resize_to_aspect(self, vw, vh):
        """JS API：拿到视频原生分辨率 → 记录比例并把独立播放器窗口对齐该比例。"""
        w = self._w
        if not w or self.kind != 'player':
            return
        try:
            vw, vh = int(vw), int(vh)
        except Exception:
            return
        if vw <= 0 or vh <= 0:
            return
        self._video_px = (vw, vh)
        self._video_aspect = float(vw) / float(vh)
        # 最大化/全屏/拖拽中不要强行把窗口拉回普通居中；比例已记录，
        # 退出这些状态后 sync_window_aspect 仍可用它校正尺寸。
        if self._is_maximized or self._is_fullscreen or self._drag:
            return
        self.manager._resize_window_to_aspect(w, vw, vh)

    def toggle_fullscreen(self):
        """切换窗口真正的显示器全屏（覆盖整个显示器，含任务栏）。

        解决 WebView2 内核不支持网页 DOM requestFullscreen 的问题，也规避
        pywebview 自带 toggle_fullscreen() 在 frameless 窗口下还原异常/不生效。
        自行用 WinForms 直接设置 Form 边界到整块显示器（screen.Bounds，含任务栏），
        并记录进入前的几何与最大化态，退出时精确还原——保证「双击进入 / 再次双击还原」都可靠。

        与最大化联动：进入全屏前若已最大化，记录正常几何(_restore_geom)而非最大化几何，
        退出全屏时恢复到进入前的最大化/正常态，避免「全屏退出后窗口尺寸错乱」。
        """
        w = self._w
        if not w or not getattr(w, 'native', None):
            return None

        # 进入/退出全屏前先把「正常态几何」在当前线程读好（w.x/w.width 是 Python 侧缓存，读取安全）
        if not self._is_fullscreen:
            self._was_maximized_before_fs = bool(self._is_maximized)
            if self._is_maximized and self._restore_geom:
                # 当前是最大化：记录正常几何（而非最大化几何），便于退出全屏后还原到正常态
                self._fs_x, self._fs_y = int(self._restore_geom[0]), int(self._restore_geom[1])
                self._fs_w, self._fs_h = int(self._restore_geom[2]), int(self._restore_geom[3])
            else:
                try:
                    self._fs_x, self._fs_y = int(w.x), int(w.y)
                    self._fs_w, self._fs_h = int(w.width), int(w.height)
                except Exception:
                    pass

        def _apply():
            import System.Windows.Forms as WinForms
            native = w.native
            screen = WinForms.Screen.FromHandle(native.Handle)

            # 关键：frameless 窗口本身已是 FormBorderStyle.None。重复赋值同一枚举值
            # 依然会触发 WinForms 重建窗口句柄，把子控件 WebView2 的 HWND 剥离 →
            # RPC_E_DISCONNECTED（窗口消失、进程残留）。故仅在样式确实不同时才赋值。
            none_style = WinForms.FormBorderStyle(0)  # None
            if native.FormBorderStyle != none_style:
                native.FormBorderStyle = none_style
            normal_state = WinForms.FormWindowState(0)  # Normal
            if native.WindowState != normal_state:
                native.WindowState = normal_state

            if self._is_fullscreen:
                # —— 退出全屏 ——
                self._is_fullscreen = False
                if self._was_maximized_before_fs:
                    # 进入前是最大化 → 恢复最大化（贴合工作区，保留任务栏）
                    wa = screen.WorkingArea
                    native.SetBounds(int(wa.X), int(wa.Y), int(wa.Width), int(wa.Height))
                    _set_window_dwm_border(native, True)  # 仍是最大化态，保持无 1px 边框
                    self._is_maximized = True
                else:
                    # 进入前是正常态 → 恢复正常几何
                    native.SetBounds(int(self._fs_x), int(self._fs_y), int(self._fs_w), int(self._fs_h))
                    _set_window_dwm_border(native, False)  # 恢复正常态阴影边框/圆角
                    self._is_maximized = False
            else:
                # —— 进入全屏 ——
                self._is_fullscreen = True
                b = screen.Bounds
                native.SetBounds(int(b.X), int(b.Y), int(b.Width), int(b.Height))
                _set_window_dwm_border(native, True)  # 铺满整屏同样去掉圆角/边框，避免露边
            # 返回当前状态供前端同步（最大化态 + 全屏态）
            return {'maximized': bool(self._is_maximized), 'fullscreen': bool(self._is_fullscreen)}

        return ui_invoke(w, _apply)

    def get_window_state(self):
        """返回当前窗口状态（最大化 / 贴靠 / 全屏），供前端初始化或同步时查询。"""
        return {
            'maximized': bool(self._is_maximized),
            'snapped': bool(self._is_snapped),
            'fullscreen': bool(self._is_fullscreen),
        }

def main() -> None:
    """主入口：启动 Flask → 创建 pywebview 窗口"""
    # 单实例：Windows 命名互斥体。已存在实例时（ERROR_ALREADY_EXISTS=183）静默退出，
    # 不弹错误；非 Windows 平台 ctypes.windll 不存在，捕获后放行。互斥体句柄全程不关闭，
    # 进程退出才释放，以此保证全局唯一。
    try:
        _kernel32 = ctypes.windll.kernel32
        _mutex = _kernel32.CreateMutexW(None, False, "Global\\PPPlayer_SingleInstance")
        if _mutex == 0 or ctypes.GetLastError() == 183:
            print("[SingleInstance] 已有实例在运行，退出。")
            sys.exit(0)
    except Exception as e:
        print(f"[SingleInstance] 检查失败（非 Windows 或缺少依赖，放行）：{e}")

    # 应用内更新器单例：与托盘、前端共用同一实例（检查 / 后台下载 / 应用重启）
    from src.updater import Updater
    _current_version = get_app_version()
    print(f"[App] 当前版本：{_current_version}")
    updater = Updater(_current_version, GITHUB_REPO, "PPPlayer")
    # GitHub Gist 数据同步单例（与前端「设置中心-数据」共用）
    from src.gist_sync import GistSync
    gist_sync = GistSync("PPPlayer")

    flask_thread = threading.Thread(target=lambda: run_flask(updater, gist_sync), daemon=True)
    flask_thread.start()

    player_mgr = PlayerWindow(FLASK_PORT)

    # 老板键：启动时读取已保存的组合并注册全局热键
    player_mgr.bosskey = BossKeyManager(player_mgr)
    try:
        from src.config_store import get_item
        _saved_cfg = get_item("cms_cfg", {})
        if isinstance(_saved_cfg, dict) and _saved_cfg.get("bossKey"):
            player_mgr.bosskey.set_combo(_saved_cfg["bossKey"])
    except Exception:
        pass

    # 主窗口使用独立的 js_api 实例
    main_api = JsApi(player_mgr, 'main')
    window = webview.create_window(
        title="PPPlayer",
        url=f"http://{FLASK_HOST}:{FLASK_PORT}",
        width=1280,
        height=800,
        min_size=(800, 600),
        resizable=True,
        confirm_close=False,
        js_api=main_api,
        frameless=True,
        easy_drag=False,
    )
    main_api.attach(window)
    player_mgr._main_window = window
    player_mgr._main_api = main_api
    # 窗口引用交给更新器：下载完成时主动推送前端（轮询之外的一道保险）
    updater.set_window(window)

    # 系统托盘：在 webview.start 之前创建对象，start 之后在 UI 线程初始化图标
    player_mgr.tray = TrayManager(player_mgr, updater)

    # 允许下载：pywebview 默认 ALLOW_DOWNLOADS=False，会把网页发起的下载（含
    # blob: 导出）直接 Cancel，导致桌面端「导出」毫无反应。开启后即便前端走
    # <a download> 兜底路径也能弹出原生「另存为」。
    try:
        webview.settings['ALLOW_DOWNLOADS'] = True
    except Exception as e:
        print(f"[Settings] 启用下载功能失败： {e}")

    # webview.start(func=...) 的回调运行在「工作线程」，而 NotifyIcon 必须在 UI(STA)
    # 线程创建，否则托盘图标常常不显示。这里等窗口原生句柄就绪，再 Invoke 过去创建。
    def _on_loaded():
        try:
            for _ in range(200):  # 最多等 20s
                native = getattr(window, 'native', None)
                if native is not None and native.IsHandleCreated:
                    ok = ui_invoke(window, player_mgr.tray.create)
                    if not ok:
                        print("[Tray] create returned falsy")
                    return
                time.sleep(0.1)
            print("[Tray] 窗口句柄尚未就绪，作为备用方案在当前线程创建")
            player_mgr.tray.create()
        except Exception as e:
            print(f"[Tray] 加载中的初始化失败: {e}")

    webview.start(func=_on_loaded,icon='/logo.ico')

    sys.exit(0)


if __name__ == "__main__":
    main()
