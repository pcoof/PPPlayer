"""CMS 统一 HTTP 客户端。

问题背景：Windows 上 requests/urllib3 默认按 getaddrinfo 返回顺序连接，常优先尝试
IPv6；而许多 CMS 源并不真正监听 IPv6，于是握手阶段就被对端 RST，表现为
`ConnectionResetError(10054) / 远程主机强迫关闭了一个现有的连接`。浏览器（Chrome）
有 Happy Eyeballs，会自动回退到可用的 IPv4，所以出现「浏览器能开、Python 请求 502」。

本模块强制 IPv4（socket_family=AF_INET）并启用自动重试，统一为完整 Chrome UA，
规避上述坑。CMS 全链路（探测 / 分类 / 列表 / 详情）共用单例会话，享受 keep-alive。
"""
from __future__ import annotations

import socket

import requests as req
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 完整 Chrome UA —— 避免被源站/WAF 因 UA 异常（截断、空）而 RST/403。
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class _IPv4HTTPAdapter(HTTPAdapter):
    """强制仅解析到 IPv4，避免连到服务器不支持的 IPv6 地址被 RST。"""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_family"] = socket.AF_INET
        return super().init_poolmanager(*args, **kwargs)


def _build_session() -> req.Session:
    s = req.Session()
    s.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=0,  # 不按状态码重试，避免误重放
        other=1,
        backoff_factor=0.5,
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter = _IPv4HTTPAdapter(
        max_retries=retry, pool_connections=10, pool_maxsize=10
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# 单例：CMS 全链路共用，享受 keep-alive 与自动重试。
cms_session: req.Session = _build_session()
