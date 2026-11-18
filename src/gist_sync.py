"""GitHub Gist 数据同步 — 单例。

把应用的全部本地数据（data/config.json，含源 / 收藏 / 历史 / 搜索记录 / 设置）
同步到用户自己的一个私密 Gist，实现多设备间的数据同步。

设计要点：
  - **凭据独立存储**：token / gist_id / 同步状态存在独立文件 data/gist_sync.json，
    绝不放进 config.json——否则「拉取」用云端数据覆盖 config.json 时会把凭据一起
    冲掉，导致拉完就失联。
  - **token 绝不明文回显**：get_status() 只返回掩码（后 4 位），前端不接触完整 token。
  - **避免重复创建 Gist**：以固定标记(description)+固定文件名作为「同步 Gist」指纹，
    配置 token 时先分页扫描当前用户的所有 gist，命中即绑定；上传时若仍未绑定也先找后建。
  - **走私密 Gist**（public=false）：同步内容含观看历史 / 源地址，不应公开。
  - **绕开系统代理 + SSL 系统根兜底**：与 updater 一致，避免代理 / MITM 证书导致失败。
"""

import os
import sys
import json
import ssl
import threading

import requests
from requests.exceptions import SSLError, RequestException
from requests.adapters import HTTPAdapter

from .config_store import load_all, save_all, _PROJECT_ROOT


API_BASE = "https://api.github.com"
# 同步 Gist 的指纹：description 含此标记 + 文件名固定。用于自动查找、避免重复创建。
GIST_MARKER = "PPPlayer-Sync-DoNotEdit"
GIST_FILENAME = "ppplayer-data.json"
GIST_DESCRIPTION = "PPPlayer 数据同步（由 PPPlayer 自动管理，请勿手动修改） " + GIST_MARKER

DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
STATE_FILE = os.path.join(DATA_DIR, "gist_sync.json")

_NO_PROXY = {"http": None, "https": None}


class _SystemCertAdapter(HTTPAdapter):
    """SSL 校验兜底：公司代理自签根 CA 装在系统根存储时可用。仅用于只读/受控请求。"""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


class GistSync:
    def __init__(self, app_name="PPPlayer"):
        self.app_name = app_name
        self._lock = threading.RLock()
        self._state = self._load_state()

    # ——— 凭据/状态的本地持久化 ———
    def _load_state(self):
        default = {
            "token": "",
            "gist_id": "",
            "gist_url": "",
            "last_sync_at": "",
            "last_action": "",
            "last_status": "idle",   # idle | ok | error
            "last_error": "",
        }
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    default.update({k: data[k] for k in default if k in data})
        except Exception:
            pass
        return default

    def _save_state(self):
        try:
            if not os.path.isdir(DATA_DIR):
                os.makedirs(DATA_DIR, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GistSync] 保存凭据失败：{e}")

    # ——— 对外状态（token 只回掩码） ———
    def get_status(self):
        with self._lock:
            token = self._state.get("token", "")
            masked = ("••••" + token[-4:]) if len(token) >= 4 else ("••••" if token else "")
            return {
                "configured": bool(token),
                "token_mask": masked,
                "bound": bool(self._state.get("gist_id")),
                "gist_id": self._state.get("gist_id", ""),
                "gist_url": self._state.get("gist_url", ""),
                "last_sync_at": self._state.get("last_sync_at", ""),
                "last_action": self._state.get("last_action", ""),
                "last_status": self._state.get("last_status", "idle"),
                "last_error": self._state.get("last_error", ""),
            }

    def _set_result(self, action, ok, error=""):
        from datetime import datetime
        self._state["last_action"] = action
        self._state["last_status"] = "ok" if ok else "error"
        self._state["last_error"] = "" if ok else str(error)[:300]
        if ok:
            self._state["last_sync_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_state()

    # ——— HTTP：绕代理 + SSL 系统根兜底 ———
    def _session(self):
        s = requests.Session()
        s.proxies = _NO_PROXY
        s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"{self.app_name}-GistSync",
        })
        return s

    def _request(self, method, url, token=None, **kwargs):
        """发起请求；SSLError 时用系统根证书存储重试一次。"""
        s = self._session()
        if token:
            s.headers.update({"Authorization": f"Bearer {token}"})
        kwargs.setdefault("timeout", (12, 30))
        kwargs.setdefault("proxies", _NO_PROXY)
        try:
            return s.request(method, url, **kwargs)
        except SSLError:
            s.mount("https://", _SystemCertAdapter())
            return s.request(method, url, **kwargs)

    @staticmethod
    def _friendly_http_error(resp):
        code = resp.status_code
        if code in (401, 403):
            return "凭据无效或权限不足：请确认 Token 有效且勾选了 gist 权限（Fine-grained token 需「Gists: write」）"
        if code == 404:
            return "Gist 不存在或无权访问（可能已被删除，或 Token 无权限），请重新绑定"
        if code == 422:
            return "GitHub 拒绝了请求（422），请检查 Gist 内容或 Token 权限"
        return f"GitHub 请求失败（HTTP {code}）：{resp.text[:160]}"

    # ——— 配置 Token：校验 + 自动查找并绑定已有 Gist ———
    def configure(self, token):
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "Token 不能为空"}

        s = self._session()
        s.headers.update({"Authorization": f"Bearer {token}"})
        try:
            resp = s.get(API_BASE + "/user", timeout=(12, 30), proxies=_NO_PROXY)
        except SSLError:
            s.mount("https://", _SystemCertAdapter())
            try:
                resp = s.get(API_BASE + "/user", timeout=(12, 30), proxies=_NO_PROXY)
            except RequestException as e:
                return {"ok": False, "error": f"连接 GitHub 失败：{e}"}
        except RequestException as e:
            return {"ok": False, "error": f"连接 GitHub 失败：{e}"}

        if resp.status_code in (401, 403):
            return {"ok": False, "error": "Token 校验失败：请确认已正确复制且未过期（需要 gist 权限）"}
        if resp.status_code != 200:
            return {"ok": False, "error": self._friendly_http_error(resp)}

        scopes = resp.headers.get("X-OAuth-Scopes", "")
        # 保存 token（先存，后续查找失败也保留，便于重试绑定）
        with self._lock:
            self._state["token"] = token
            self._save_state()

        # 自动查找已有同步 Gist
        gist, err = self._find_sync_gist(token)
        if err:
            return {"ok": True, "bound": False, "warning": err}
        if gist:
            self._bind(gist)
            return {"ok": True, "bound": True, "gist_url": gist.get("html_url", ""),
                    "created": False, "scopes": scopes}
        # 未找到已有 gist：不立即创建，等首次「上传」时创建（避免只是配置就产生副作用）
        return {"ok": True, "bound": False, "created": False, "scopes": scopes,
                "warning": "未找到已有的同步 Gist，点击「上传到 Gist」后将自动创建"}

    def _find_sync_gist(self, token):
        """分页扫描当前用户的 gist，返回带同步标记的第一个（最近更新优先）。"""
        page = 1
        while page <= 10:  # 上限 1000 个，防止无限分页
            try:
                resp = self._request("GET", API_BASE + "/gists", token=token,
                                     params={"per_page": 100, "page": page})
            except RequestException as e:
                return None, f"查找已有 Gist 失败：{e}"
            if resp.status_code in (401, 403):
                return None, self._friendly_http_error(resp)
            if resp.status_code != 200:
                return None, self._friendly_http_error(resp)
            try:
                items = resp.json()
            except Exception:
                return None, "解析 Gist 列表失败"
            if not isinstance(items, list):
                return None, "Gist 列表格式异常"
            for g in items:
                desc = g.get("description") or ""
                files = g.get("files") or {}
                if GIST_MARKER in desc and GIST_FILENAME in files:
                    return g, None
            if len(items) < 100:
                break
            page += 1
        return None, None

    def _bind(self, gist):
        with self._lock:
            self._state["gist_id"] = gist.get("id", "")
            self._state["gist_url"] = gist.get("html_url", "")
            self._save_state()

    def unbind(self):
        with self._lock:
            self._state["gist_id"] = ""
            self._state["gist_url"] = ""
            self._save_state()
        return {"ok": True}

    def clear_token(self):
        with self._lock:
            self._state["token"] = ""
            self._state["gist_id"] = ""
            self._state["gist_url"] = ""
            self._save_state()
        return {"ok": True}

    # ——— 上传：把本地 config.json 写入 Gist ———
    def upload(self):
        with self._lock:
            token = self._state.get("token", "")
            gist_id = self._state.get("gist_id", "")
        if not token:
            return {"ok": False, "error": "请先在下方配置 GitHub Token"}
        data = load_all() or {}
        content = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            if gist_id:
                resp = self._request(
                    "PATCH", API_BASE + "/gists/" + gist_id, token=token,
                    json={"files": {GIST_FILENAME: {"content": content}},
                          "description": GIST_DESCRIPTION},
                )
                if resp.status_code == 404:
                    # gist 被删：清除绑定，走新建
                    with self._lock:
                        self._state["gist_id"] = ""
                        self._state["gist_url"] = ""
                        self._save_state()
                    gist_id = ""
                else:
                    if resp.status_code != 200:
                        err = self._friendly_http_error(resp)
                        self._set_result("上传", False, err)
                        return {"ok": False, "error": err}
                    self._bind(resp.json())
                    self._set_result("上传", True)
                    return {"ok": True, "gist_url": self._state.get("gist_url", ""),
                            "created": False}
            # 未绑定：先找一次，仍无则新建
            gist, ferr = self._find_sync_gist(token)
            if ferr:
                self._set_result("上传", False, ferr)
                return {"ok": False, "error": ferr}
            if gist:
                self._bind(gist)
                resp = self._request(
                    "PATCH", API_BASE + "/gists/" + gist.get("id", ""), token=token,
                    json={"files": {GIST_FILENAME: {"content": content}},
                          "description": GIST_DESCRIPTION},
                )
                if resp.status_code != 200:
                    err = self._friendly_http_error(resp)
                    self._set_result("上传", False, err)
                    return {"ok": False, "error": err}
                self._bind(resp.json())
                self._set_result("上传", True)
                return {"ok": True, "gist_url": self._state.get("gist_url", ""),
                        "created": False}
            resp = self._request(
                "POST", API_BASE + "/gists", token=token,
                json={"description": GIST_DESCRIPTION, "public": False,
                      "files": {GIST_FILENAME: {"content": content}}},
            )
            if resp.status_code not in (200, 201):
                err = self._friendly_http_error(resp)
                self._set_result("上传", False, err)
                return {"ok": False, "error": err}
            self._bind(resp.json())
            self._set_result("上传", True)
            return {"ok": True, "gist_url": self._state.get("gist_url", ""),
                    "created": True}
        except RequestException as e:
            err = f"上传失败：{e}"
            self._set_result("上传", False, err)
            return {"ok": False, "error": err}
        except Exception as e:
            err = f"上传失败：{e}"
            self._set_result("上传", False, err)
            return {"ok": False, "error": err}

    # ——— 拉取：从 Gist 读取并覆盖本地 config.json ———
    def pull(self):
        with self._lock:
            token = self._state.get("token", "")
            gist_id = self._state.get("gist_id", "")
        if not token:
            return {"ok": False, "error": "请先在下方配置 GitHub Token"}
        if not gist_id:
            gist, ferr = self._find_sync_gist(token)
            if ferr:
                self._set_result("拉取", False, ferr)
                return {"ok": False, "error": ferr}
            if not gist:
                err = "尚未绑定同步 Gist，请先上传一次或点击「查找并绑定」"
                self._set_result("拉取", False, err)
                return {"ok": False, "error": err}
            self._bind(gist)
            gist_id = gist.get("id", "")
        try:
            resp = self._request("GET", API_BASE + "/gists/" + gist_id, token=token)
            if resp.status_code != 200:
                err = self._friendly_http_error(resp)
                self._set_result("拉取", False, err)
                return {"ok": False, "error": err}
            gj = resp.json()
            files = gj.get("files") or {}
            f = files.get(GIST_FILENAME)
            if not f:
                err = f"同步 Gist 中缺少数据文件 {GIST_FILENAME}"
                self._set_result("拉取", False, err)
                return {"ok": False, "error": err}
            raw = f.get("content") or ""
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("云端数据格式异常")
            # 用云端数据整体覆盖 config.json（凭据独立存储，不受影响）
            save_all(data)
            self._set_result("拉取", True)
            return {"ok": True, "counts": {
                "sources": len(data.get("cms_src") or []),
                "fav": len(data.get("cms_fav") or []),
                "his": len(data.get("cms_his") or []),
            }}
        except RequestException as e:
            err = f"拉取失败：{e}"
            self._set_result("拉取", False, err)
            return {"ok": False, "error": err}
        except Exception as e:
            err = f"拉取失败：{e}"
            self._set_result("拉取", False, err)
            return {"ok": False, "error": err}
