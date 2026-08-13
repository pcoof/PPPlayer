"""TSPlayer 入口 — 启动 Flask + pywebview 桌面窗口"""

import sys
import os
import json
import ctypes
import threading
import webview
from urllib import parse
from src.server import create_app

FLASK_HOST = "127.0.0.1"
FLASK_PORT = 19527


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
    )


class PlayerWindow:
    """管理独立播放器窗口（作为 manager，持有主窗口/播放窗口引用与几何持久化）"""

    GEOMETRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'player_geometry.json')

    def __init__(self, port, main_window=None):
        self.port = port
        self._player_window = None
        self._main_window = main_window
        self._wnd_proc_refs = []  # 持有 WndProc 子类引用，防止被 GC

    def _run_on_ui(self, func, *args, **kwargs):
        """在 UI 线程上执行窗口操作"""
        try:
            if self._player_window and hasattr(self._player_window, 'native'):
                hwnd = self._player_window.native.Handle
                WM_USER = 0x0400

                if hasattr(self._player_window, 'evaluate_js'):
                    self._player_window.evaluate_js('void(0)')
        except Exception:
            pass

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
        """关闭播放窗口"""
        if self._player_window:
            try:
                self._save_geometry()
                self._run_on_ui(self._player_window.destroy)
            except Exception:
                pass
            self._player_window = None


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

    def toggle_maximize_window(self, is_max=False):
        """在「最大化（贴合工作区，保留任务栏）」与「还原」之间切换。
        is_max 由 JS 端传入（window-chrome.js 维护的 per-window 状态），避免主窗/播放窗串扰。
        frameless 若直接 WindowState=Maximized 会铺满整屏（含任务栏，像全屏），故手动移到 WorkingArea。"""
        w = self._w
        if not w or not getattr(w, 'native', None):
            return
        try:
            import System.Windows.Forms as WinForms
            screen = WinForms.Screen.FromHandle(w.native.Handle)
            wa = screen.WorkingArea
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

    # —— 跨窗口操作转发给 manager ——
    def show_main_window(self):
        self.manager.show_main_window()

    def open_player_window(self, state_json=""):
        self.manager.open_player_window(state_json)

    def close_player(self):
        self.manager.close_player()

    def resize_to_aspect(self, vw, vh):
        w = self._w
        if w:
            self.manager._resize_window_to_aspect(w, vw, vh)

    def toggle_fullscreen(self):
        """切换窗口真正的显示器全屏（OS 级，覆盖整个显示器含任务栏）。

        解决 WebView2 内核不支持网页 DOM requestFullscreen 的问题：
        XGPlayer 默认调用浏览器原生全屏在 WebView2 下无效，故由 Python 端
        调用 pywebview 原生窗口全屏替代，实现真正的「显示器全屏」。"""
        w = self._w
        if w:
            try:
                w.toggle_fullscreen()
            except Exception:
                pass


def main() -> None:
    """主入口：启动 Flask → 创建 pywebview 窗口"""
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    player_mgr = PlayerWindow(FLASK_PORT)

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

    sys.setrecursionlimit(5000)
    webview.start(debug=True)

    sys.exit(0)


if __name__ == "__main__":
    main()
