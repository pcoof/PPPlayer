"""TSPlayer 入口 — 启动 Flask + pywebview 桌面窗口"""

import sys
import os
import json
import ctypes
import time
import threading
import webview
from urllib import parse
from src.server import create_app

FLASK_HOST = "127.0.0.1"
FLASK_PORT = 19527

# 应用版本（自动更新比对基准）。格式 YYYYMMDD.N，由 CI 工作流自动自增并同步。
__version__ = "20260823.1"

# 自动更新：GitHub Releases 检测
GITHUB_REPO = "pcoof/tsplayer-pywebview"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}"


def _parse_app_version(v):
    """把 'v20260820.1' / '20260820.1' 解析为 (日期int, 序号int) 元组，便于比较大小。"""
    if not v:
        return None
    v = str(v).lstrip('vV')
    try:
        parts = v.split('.')
        date_part = int(parts[0])
        n_part = int(parts[1]) if len(parts) > 1 else 0
        return (date_part, n_part)
    except Exception:
        return None


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


def run_flask():
    """在独立线程中启动 Flask 服务器"""
    app = create_app()
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
        self._main_window = main_window
        self._wnd_proc_refs = []  # 持有 WndProc 子类引用，防止被 GC
        self.bosskey = None       # BossKeyManager（老板键全局热键）
        self.tray = None          # TrayManager（系统托盘）

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
        """读取已保存的窗口几何信息"""
        try:
            if os.path.exists(self.GEOMETRY_FILE):
                with open(self.GEOMETRY_FILE, 'r', encoding='utf-8') as f:
                    return json.loads(f.read())
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
                    json.dump({"x": x, "y": y, "width": width, "height": height}, f)
        except Exception:
            pass

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
        """JS API: 打开独立无标题栏播放窗口，state_json 为播放状态 JSON 字符串"""
        from src.server import set_popout_state

        if state_json:
            try:
                state = json.loads(state_json)
                set_popout_state(state)
            except Exception:
                pass

        if self._player_window:
            try:
                self._run_on_ui(self._player_window.destroy)
            except Exception:
                pass

        player_url = f"http://{FLASK_HOST}:{self.port}/player"

        geom = self._load_geometry()
        # 每个窗口使用独立的 JS 桥接对象，避免多窗口共用 js_api 导致状态/目标串扰
        player_api = JsApi(self, 'player')
        kwargs = {
            "title": "TSPlayer",
            "url": player_url,
            "min_size": (360, 640),
            "resizable": True,
            "frameless": True,
            "easy_drag": False,
            "on_top": False,
            "js_api": player_api,
        }
        if geom:
            kwargs["x"] = geom.get("x", 0)
            kwargs["y"] = geom.get("y", 0)
            kwargs["width"] = geom.get("width", 480)
            kwargs["height"] = geom.get("height", 854)
        else:
            kwargs["width"] = 480
            kwargs["height"] = 854

        self._player_window = webview.create_window(**kwargs)
        player_api.attach(self._player_window)
        self._player_api = player_api

    def _resize_window_to_aspect(self, window, video_width, video_height):
        """根据视频宽高比调整指定窗口尺寸"""
        try:
            if not window:
                return
            ratio = video_width / video_height if video_height else 1
            if ratio >= 1.3:
                height = 500
                width = int(500 * ratio)
                if width > 1000:
                    width = 1000
            else:
                width = 480
                height = int(480 / ratio)
                if height > 900:
                    height = 900
            self._run_on_ui(lambda: window.resize(width, height))
            self._save_geometry(window)
        except Exception:
            pass

    def close_player(self):
        """关闭播放窗口，并通知主窗口清除正在播放状态（否则左上角标题残留）。"""
        if self._player_window:
            try:
                self._save_geometry()
                self._run_on_ui(self._player_window.destroy)
            except Exception:
                pass
            self._player_window = None
        # 通知主窗口清除 currentItem / 正在播放标题栏
        w = self._main_window
        if w:
            try:
                w.evaluate_js(
                    "if(window.__app){window.__app.currentItem=null;"
                    "window.__app.playerTitle='';}"
                )
            except Exception:
                pass

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

    def __init__(self, manager):
        self._mgr = manager
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
            self._notify.Text = "TSPlayer"
            self._notify.ContextMenu = self._menu
            # 图标：优先取项目自带 ico → 其次可执行文件关联图标 → 最后系统默认应用图标
            icon = None
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'favicon.ico')
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
        """后台线程查询 GitHub 最新 Release，发现新版本则弹气泡 + 在菜单插入下载项。

        全程不阻塞 UI：网络请求在 daemon 线程，气泡/菜单改动经 ui_invoke 回到 UI 线程。
        """
        import threading

        def _run():
            try:
                import requests
                resp = requests.get(
                    GITHUB_API_LATEST,
                    timeout=10,
                    headers={"Accept": "application/vnd.github+json"},
                )
                if resp.status_code != 200:
                    return
                data = resp.json()
                tag = data.get("tag_name") or ""
                cur = _parse_app_version(__version__)
                new = _parse_app_version(tag)
                if not new or not cur or new <= cur:
                    if show_no_update:
                        self._toast("TSPlayer", "当前已是最新版本", "info")
                    return
                # 优先取 .exe/.zip 资产，否则退回 release 页面
                dl = None
                for a in data.get("assets", []):
                    name = (a.get("name") or "").lower()
                    if name.endswith(".exe") or name.endswith(".zip"):
                        dl = a.get("browser_download_url")
                        break
                url = dl or (data.get("html_url") or GITHUB_RELEASES_URL)
                self._latest_update = {"version": tag.lstrip("vV"), "url": url}
                self._toast("TSPlayer", f"发现新版本 {tag.lstrip('vV')}，点击菜单下载", "info")
                self._add_update_menu_item(tag.lstrip("vV"), url)
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
            winreg.SetValueEx(key, "TSPlayer", 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, "TSPlayer")
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
            val, _ = winreg.QueryValueEx(key, "TSPlayer")
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
        self._restore_geom = None
        self._drag = None
        self._is_fullscreen = False
        self._fs_x = self._fs_y = self._fs_w = self._fs_h = 0
        self._was_maximized_before_fs = False  # 进入全屏前是否为最大化，退出时恢复

    def attach(self, window):
        self._w = window

    # —— 拖拽 / 缩放（Python 端完成坐标运算）——
    def drag_start(self, sx, sy):
        w = self._w
        if not w:
            return
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
        min_w, min_h = (360, 640) if self.kind == 'player' else (800, 600)
        nw = max(int(round(nw)), min_w)
        nh = max(int(round(nh)), min_h)
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
        frameless 若直接 WindowState=Maximized 会铺满整屏（含任务栏，像全屏），故手动移到 WorkingArea。"""
        w = self._w
        if not w or not getattr(w, 'native', None):
            return
        try:
            # Screen.FromHandle / WorkingArea 属于原生控件访问 → 切到 UI 线程读取
            def _work_area():
                import System.Windows.Forms as WinForms
                a = WinForms.Screen.FromHandle(w.native.Handle).WorkingArea
                return (int(a.X), int(a.Y), int(a.Width), int(a.Height))

            _wa = ui_invoke(w, _work_area)
            if not _wa:
                return bool(self._is_maximized)

            class _WA:
                X, Y, Width, Height = _wa

            wa = _WA
            target = bool(is_max)
            if self._is_maximized and not target:
                g = self._restore_geom or (120, 120, 900, 640)
                w.move(int(g[0]), int(g[1]))
                w.resize(int(g[2]), int(g[3]))
                self._is_maximized = False
            elif (not self._is_maximized) and target:
                self._restore_geom = (int(w.x), int(w.y), int(w.width), int(w.height))
                w.move(int(wa.X), int(wa.Y))
                w.resize(int(wa.Width), int(wa.Height))
                self._is_maximized = True
        except Exception:
            pass
        # 返回切换后的「真实最大化态」，让前端以 Python 端为准，杜绝 JS/Python 状态错位（点击两次才生效）。
        return bool(self._is_maximized)

    def close_window(self):
        if self.kind == 'player':
            self.manager.close_player()
        elif self._w:
            try:
                self._w.destroy()
            except Exception:
                pass

    def report_play_progress(self, item_json="", src_idx=0, ep_idx=0, current_time=0):
        """播放窗口上报当前播放进度 → 主窗口保存播放历史。

        播放窗口切换集数/开始播放时调用。传递完整的 item JSON（含 vod_id/vod_name/vod_pic 等），
        主窗口端直接设置 currentItem 再调用 saveHistory，避免因 currentItem 为空而跳过保存。
        """
        w = self.manager._main_window
        if not w:
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
        w = self._w
        if w:
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
                    self._is_maximized = True
                else:
                    # 进入前是正常态 → 恢复正常几何
                    native.SetBounds(int(self._fs_x), int(self._fs_y), int(self._fs_w), int(self._fs_h))
                    self._is_maximized = False
            else:
                # —— 进入全屏 ——
                self._is_fullscreen = True
                b = screen.Bounds
                native.SetBounds(int(b.X), int(b.Y), int(b.Width), int(b.Height))
            # 返回当前状态供前端同步（最大化态 + 全屏态）
            return {'maximized': bool(self._is_maximized), 'fullscreen': bool(self._is_fullscreen)}

        return ui_invoke(w, _apply)

    def get_window_state(self):
        """返回当前窗口状态（最大化 / 全屏），供前端初始化或同步时查询。"""
        return {
            'maximized': bool(self._is_maximized),
            'fullscreen': bool(self._is_fullscreen),
        }

def main() -> None:
    """主入口：启动 Flask → 创建 pywebview 窗口"""
    flask_thread = threading.Thread(target=run_flask, daemon=True)
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
        title="TSPlayer",
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

    # 系统托盘：在 webview.start 之前创建对象，start 之后在 UI 线程初始化图标
    player_mgr.tray = TrayManager(player_mgr)

    # 允许下载：pywebview 默认 ALLOW_DOWNLOADS=False，会把网页发起的下载（含
    # blob: 导出）直接 Cancel，导致桌面端「导出」毫无反应。开启后即便前端走
    # <a download> 兜底路径也能弹出原生「另存为」。
    try:
        webview.settings['ALLOW_DOWNLOADS'] = True
    except Exception as e:
        print(f"[Settings] enable downloads failed: {e}")

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
            print("[Tray] window handle not ready, create on current thread as fallback")
            player_mgr.tray.create()
        except Exception as e:
            print(f"[Tray] init in loaded failed: {e}")

    webview.start(debug=True, func=_on_loaded)

    sys.exit(0)


if __name__ == "__main__":
    main()
