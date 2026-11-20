"""统一出站网络策略：系统代理 + 系统根证书。

历史与问题背景
--------------
早期为避免「VPN/代理/爬虫工具注入 HTTP_PROXY/HTTPS_PROXY」导致的 MITM 重签证书错误、
TLS Happy Eyeballs 被破坏、请求转圈，本应用把所有 Session 都写死为：

    session.trust_env = False
    proxies = {"http": None, "https": None}   # 强制禁用代理

代价是：当用户开启系统代理后，**浏览器走代理能正常打开 API 源，应用却始终直连**，
表现为「检测超时 / 连接失败」。用户真正想要的是「浏览器能通，应用也能通」。

策略
----
- ``useSystemProxy``（默认 True）：与浏览器一致，读取系统代理（Windows 注册表 /
  HTTP_PROXY 环境变量 / NO_PROXY）。所有 HTTPS 适配器统一改用 **系统根证书存储**
  （``ssl.create_default_context()``），因此代理工具自签的根 CA 也能通过校验——
  这正是浏览器信任 MITM 代理的原因；certifi 自带证书不包含该根 CA，故不能沿用。
  使用系统根证书在无代理时也无害（浏览器同样如此），因此适配器只配置一次。
- 关闭时恢复旧行为（强制直连），兼容此前因代理干扰而必须使用直连的环境。
- CDN 多节点的 IPv4 强制 / TLS Happy Eyeballs 与代理策略相互独立，始终启用。

实现要点
--------
- 代理连接由 ``HTTPAdapter.proxy_manager_for`` 单独创建 ProxyManager，仅覆盖
  ``init_poolmanager`` 注入的 ssl_context **不会**作用到代理连接，必须同时覆盖两者。
- requests 2.34.2 的 ``HTTPAdapter(ssl_context=...)`` 构造器不接受该参数（TypeError），
  因此必须通过子类方法注入，不能直接传参。
- 策略切换只需改 ``session.trust_env``，不重挂适配器，避免打断 keep-alive 连接池。
"""
from __future__ import annotations

import os
import ssl
import threading

from requests.adapters import HTTPAdapter

from .config_store import load_all

# 配置项（存于 data/config.json 的 cms_cfg 内）
CONFIG_KEY = "useSystemProxy"
# 环境变量覆盖（便于排查/脚本化；优先级高于配置文件）
ENV_KEY = "PPPLAYER_USE_SYSTEM_PROXY"


def inject_system_ssl_context(kwargs: dict) -> None:
    """向连接池 kwargs 注入系统根证书 ssl_context；失败时静默降级为默认校验。

    单独抽成函数，供需要保留自身 pool class 逻辑的适配器（如 TLS Happy Eyeballs）
    复用，避免各处重复 try/except。
    """
    try:
        kwargs["ssl_context"] = ssl.create_default_context()
    except Exception:
        pass


class SystemCertAdapter(HTTPAdapter):
    """用系统根证书存储校验 TLS 的适配器（兼容代理工具的 MITM 自签根 CA）。

    同时覆盖 ``init_poolmanager``（直连连接池）与 ``proxy_manager_for``（代理连接池）：
    走系统代理时 requests 使用 ProxyManager，只改前者会导致 ssl_context 丢失、仍报
    CERTIFICATE_VERIFY_FAILED。
    """

    def init_poolmanager(self, *args, **kwargs):
        inject_system_ssl_context(kwargs)
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        inject_system_ssl_context(proxy_kwargs)
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _read_config() -> bool:
    """从环境变量 / 配置文件读取策略，默认 True（与浏览器行为一致）。"""
    env = os.environ.get(ENV_KEY)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "n")
    try:
        cfg = load_all().get("cms_cfg") or {}
        return cfg.get(CONFIG_KEY, True) is not False
    except Exception:
        return True


_lock = threading.RLock()
_enabled: bool | None = None
_sessions: list = []


def enabled() -> bool:
    """当前是否使用系统代理（首次调用惰性从配置读取）。"""
    global _enabled
    with _lock:
        if _enabled is None:
            _enabled = _read_config()
        return bool(_enabled)


def configure(value: bool) -> None:
    """实时更新策略并下发到所有已绑定的 Session（仅改 trust_env，不重挂适配器）。"""
    global _enabled
    with _lock:
        _enabled = bool(value)
        sessions = list(_sessions)
    for s in sessions:
        try:
            s.trust_env = bool(_enabled)
        except Exception:
            pass


def reload() -> None:
    """从配置文件重新读取并应用（应用启动时调用）。"""
    configure(_read_config())


def bind_session(session) -> None:
    """绑定一个长生命周期 Session：注册并在将来策略变更时同步其 trust_env。"""
    with _lock:
        _sessions.append(session)
        value = enabled()
    try:
        session.trust_env = bool(value)
    except Exception:
        pass


def proxy_kwargs() -> dict | None:
    """返回传给 requests 的 ``proxies`` 参数。

    - 开启：返回 None，让 requests 通过 trust_env 读取系统/环境代理（含 NO_PROXY）。
    - 关闭：返回显式禁用字典，彻底绕开任何残留的代理环境变量。
    """
    return None if enabled() else {"http": None, "https": None}
