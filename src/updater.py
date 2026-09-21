"""PPPlayer 应用内更新器 — 单例。

职责：
  1) 检查 GitHub Releases 是否有比当前版本更新的 Release；
  2) 后台线程流式下载更新包（.exe / .zip），实时回报进度，绝不阻塞 UI；
  3) 提供 get_status() 供前端轮询；
  4) apply() 在下载完成后「等待当前进程退出 → 覆盖 exe → 重启」，实现一键更新并重启。

设计要点：
  - 与 main.py 的 TrayManager 共用同一个 Updater 实例：托盘「检查更新」与
    前端「关于」页 / 标题栏按钮都走这里，避免重复查询 GitHub。
  - 下载走 daemon 线程，前端用轮询（/api/update/status）获取状态，简单稳健。
  - 版本比较复用 main._parse_app_version 的同款逻辑（(日期int, 序号int) 元组比较）。
  - SSL 证书校验失败（uv 环境缺 CA / 公司代理重签）时回退系统根证书存储，与 TrayManager 一致。
  - 真机（PyInstaller onefile，sys.frozen=True）：apply 会写一份自删 bat，
    由它等待本进程退出后把新 exe 复制到 sys.executable 并启动，再删除自身。
  - 开发态（python main.py，未冻结）：无法替换自身，apply 直接打开发布页。
"""

import os
import sys
import json
import threading
import tempfile
import subprocess
import ctypes

import requests
from requests.exceptions import SSLError, RequestException
from requests.adapters import HTTPAdapter
import ssl


ERROR_ALREADY_EXISTS = 183  # Windows ERROR_ALREADY_EXISTS


class _SystemCertAdapter(HTTPAdapter):
    """证书校验兜底：改用系统根证书存储（含公司代理自签根 CA）。仅用于只读查询最新版本。"""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


class Updater:
    def __init__(self, current_version, github_repo, app_name="PPPlayer"):
        self.current_version = current_version
        self.github_repo = github_repo
        self.app_name = app_name
        self.api_latest = f"https://api.github.com/repos/{github_repo}/releases/latest"
        self.releases_url = f"https://github.com/{github_repo}/releases"
        self._lock = threading.Lock()
        # state: none | available | downloading | ready | error | applying
        self.state = "none"
        self.latest_version = ""
        self.download_url = ""
        self.notes = ""
        self.progress = 0          # 0-100
        self.error = ""
        self._last_error = ""       # 最近一次检查的网络错误（用于区分「无更新」与「检查失败」）
        self.downloaded_path = ""
        self._download_thread = None
        self._window = None        # pywebview 窗口引用（可选，用于主动推送前端）

    # —— 版本解析（与 main._parse_app_version 同款）——
    @staticmethod
    def _parse(v):
        if not v:
            return None
        v = str(v).lstrip("vV")
        try:
            parts = v.split(".")
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except Exception:
            return None

    # —— 网络：直连 GitHub，绕开系统代理（代理只影响浏览器）——
    def _fetch_latest_info(self):
        """返回 (tag_lstrip_v, url, notes) 或 None（无更新 / 网络失败）。"""
        # 测试覆盖：设置 PPPLAYER_FAKE_UPDATE=下载地址 可强制进入「有更新」状态，
        # 便于在未发布新版时本地验证整条下载→进度→应用流程（仅开发使用，生产环境忽略）。
        fake = os.environ.get("PPPLAYER_FAKE_UPDATE")
        if fake:
            return (os.environ.get("PPPLAYER_FAKE_VERSION", "99999999.0"), fake,
                    "（本地测试覆盖）模拟新版本更新包")

        no_proxy = {"http": None, "https": None}

        def _get():
            try:
                return requests.get(
                    self.api_latest, timeout=10,
                    headers={"Accept": "application/vnd.github+json"},
                    proxies=no_proxy,
                )
            except SSLError:
                s = requests.Session()
                s.proxies = no_proxy
                s.mount("https://", _SystemCertAdapter())
                return s.get(
                    self.api_latest, timeout=10,
                    headers={"Accept": "application/vnd.github+json"},
                )

        try:
            resp = _get()
        except (SSLError, RequestException) as e:
            self._last_error = str(e)
            print(f"[Updater] 检查更新失败（可忽略）：{e}")
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        tag = data.get("tag_name") or ""
        cur = self._parse(self.current_version)
        new = self._parse(tag)
        if not new or not cur or new <= cur:
            return None
        # 优先取 .exe / .zip 资产，否则退回 release 页面
        dl = None
        for a in data.get("assets", []):
            name = (a.get("name") or "").lower()
            if name.endswith(".exe") or name.endswith(".zip"):
                dl = a.get("browser_download_url")
                break
        url = dl or (data.get("html_url") or self.releases_url)
        notes = data.get("body") or ""
        return (tag.lstrip("vV"), url, notes)

    # —— 检查：不阻塞，立即返回当前状态 ——
    def check(self):
        with self._lock:
            # 已在下载 / 就绪 / 检查中：直接返回，避免重复查询
            if self.state in ("downloading", "ready", "checking"):
                return self.get_status()
            self.state = "checking"   # 立即进入「检查中」，让前端轮询在结果回来前保持存活（关键点）
            self.error = ""
            self._last_error = ""
        # 主动推送一次：唤醒可能因看到 'none' 而提前停掉的轮询（解耦「检查中」与「空闲无更新」）
        self._notify_frontend()
        info = self._fetch_latest_info()
        with self._lock:
            if info:
                self.latest_version, self.download_url, self.notes = info
                self.state = "available"
                self.error = ""
            elif self._last_error:
                # 网络/SSL 等请求失败：单独呈现，不再与「无更新」混淆（否则用户看到的是静默「没反应」）
                self.state = "error"
                self.error = f"检查更新失败：{self._last_error}"[:200]
            else:
                self.state = "none"
        self._notify_frontend()
        return self.get_status()

    # —— 启动后台下载（幂等）——
    def start_download(self):
        with self._lock:
            if self.state in ("downloading", "ready", "applying"):
                return
            if self.state != "available" or not self.download_url:
                info = self._fetch_latest_info()
                if not info:
                    self.state = "none"
                    return
                self.latest_version, self.download_url, self.notes = info
                self.state = "available"
            self.state = "downloading"
            self.progress = 0
            self.error = ""
        # 推送一次：让前端从「检查中 / 可用」过渡到「下载中」进度条
        self._notify_frontend()
        if self._download_thread and self._download_thread.is_alive():
            return
        self._download_thread = threading.Thread(target=self._download, daemon=True)
        self._download_thread.start()

    def _download(self):
        try:
            url = self.download_url
            name = url.split("?")[0].rstrip("/").split("/")[-1] or f"{self.app_name}-update.bin"
            tmpdir = os.path.join(tempfile.gettempdir(), f"{self.app_name}_update")
            os.makedirs(tmpdir, exist_ok=True)
            # 清理上次残留
            for f in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, f))
                except Exception:
                    pass
            dest = os.path.join(tmpdir, name)
            no_proxy = {"http": None, "https": None}
            resp = requests.get(url, stream=True, timeout=(15, 60), proxies=no_proxy)
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    with self._lock:
                        if total:
                            self.progress = min(100, int(done * 100 / total))
                        else:
                            self.progress = min(99, self.progress + 1)
            with self._lock:
                self.downloaded_path = dest
                self.progress = 100
                self.state = "ready"
            self._notify_frontend()
        except Exception as e:
            with self._lock:
                self.state = "error"
                self.error = str(e)[:200]

    def _notify_frontend(self):
        w = self._window
        if not w:
            return
        try:
            w.evaluate_js(
                "if(window.__app&&window.__app.onUpdateStatusChanged)window.__app.onUpdateStatusChanged();"
            )
        except Exception:
            pass

    def set_window(self, window):
        self._window = window

    def get_status(self):
        with self._lock:
            return {
                "state": self.state,
                "version": self.latest_version,
                "progress": self.progress,
                "notes": self.notes,
                "error": self.error,
                "url": self.download_url,
                "frozen": bool(getattr(sys, "frozen", False)),
            }

    # —— 应用更新并重启 ——
    def apply(self):
        """返回 True 表示已触发退出+重启；False 表示未触发（dev 模式或前置条件不满足）。"""
        with self._lock:
            if self.state != "ready" or not self.downloaded_path:
                return False
            src = self.downloaded_path
        if not getattr(sys, "frozen", False):
            # 开发态：无法替换正在运行的 Python 脚本，转打开发布页
            import webbrowser
            try:
                webbrowser.open(self.releases_url)
            except Exception:
                pass
            return False
        exe = sys.executable
        bat = self._write_apply_script(src, exe)
        try:
            DETACHED = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                ["cmd", "/c", bat],
                creationflags=DETACHED | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except Exception as e:
            print(f"[Updater] 启动更新助手失败：{e}")
            return False
        # 退出当前进程，交由助手等待本进程退出后覆盖并重启
        with self._lock:
            self.state = "applying"
        os._exit(0)

    def _write_apply_script(self, src, exe):
        """写一份自删 bat：等待当前 PID 退出 → 复制新 exe 覆盖 → 启动 → 清理。"""
        tmpdir = os.path.join(tempfile.gettempdir(), f"{self.app_name}_update")
        os.makedirs(tmpdir, exist_ok=True)
        bat = os.path.join(tmpdir, f"{self.app_name}_apply.bat")
        pid = os.getpid()
        # 用 tasklist /fi 判定 PID 是否还在；find "PID" 命中表头即表示进程仍存在。
        template = (
            "@echo off\r\n"
            'set "SRC={SRC}"\r\n'
            'set "DST={DST}"\r\n'
            'set "PID={PID}"\r\n'
            ":wait\r\n"
            'tasklist /fi "PID eq %PID%" | find "PID" >nul\r\n'
            "if errorlevel 1 goto docopy\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            "goto wait\r\n"
            ":docopy\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            'copy /Y "%SRC%" "%DST%" >nul\r\n'
            'if exist "%DST%" start "" "%DST%"\r\n'
            'del "%SRC%" >nul 2>nul\r\n'
            '(goto) 2>nul & del "%~f0" >nul 2>nul\r\n'
        )
        content = template.replace("{SRC}", src).replace("{DST}", exe).replace("{PID}", str(pid))
        with open(bat, "w", encoding="utf-8") as f:
            f.write(content)
        return bat
