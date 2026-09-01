"""CMS 统一 HTTP 客户端。

问题背景：Windows 上 requests/urllib3 默认按 getaddrinfo 返回顺序连接，常优先尝试
IPv6；而许多 CMS 源并不真正监听 IPv6，于是握手阶段就被对端 RST，表现为
`ConnectionResetError(10054) / 远程主机强迫关闭了一个现有的连接`。浏览器（Chrome）
有 Happy Eyeballs，会自动回退到可用的 IPv4，所以出现「浏览器能开、Python 请求 502」。

地址族策略选择（IPv4 优先 + IPv6 兜底，即 Happy Eyeballs 行为）：
- 曾经用 `HTTPAdapter.init_poolmanager(socket_family=AF_INET)`，但旧版 urllib3 的
  `PoolKey` 不支持 `key_socket_family`，会直接抛
  `PoolKey.__new__() got an unexpected keyword argument 'key_socket_family'`
  （本仓库运行环境的 urllib3 即此情况）。
- 也曾经**强制只走 IPv4**（getaddrinfo 只返回 AF_INET），修好了「IPv6 被 RST、IPv4 正常」
  的那批源；但会误伤「IPv4 不可达、仅 IPv6 可达」的源（表现为 ConnectionError 连接失败）。
- 现改为**作用域补丁**：每次请求期间把 `socket.getaddrinfo` 的结果重排为「IPv4 在前、
  IPv6 在后」。urllib3 的 `socket.create_connection` 会逐地址回退——IPv4 优先避开 RST，
  IPv4 失败再试 IPv6 兜底。该写法不依赖 urllib3 版本，对所有 requests 调用透明。
"""
from __future__ import annotations

import contextlib
import socket

import requests as req
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 完整 Chrome UA —— 避免被源站/WAF 因 UA 异常（截断、空）而 RST/403。
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _make_retry() -> Retry:
    """构造自动重试策略，兼容新旧 urllib3（allowed_methods / method_whitelist）。"""
    common = dict(
        total=3,
        connect=3,
        read=2,
        status=0,  # 不按状态码重试，避免误重放
        other=1,
        backoff_factor=0.5,
    )
    try:
        return Retry(
            **common,
            allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
            raise_on_status=False,
        )
    except TypeError:
        # urllib3 < 1.26：参数名为 method_whitelist，且无 raise_on_status
        return Retry(
            **common,
            method_whitelist=frozenset(["GET", "HEAD", "OPTIONS"]),
        )


@contextlib.contextmanager
def _prefer_ipv4():
    """在 with 作用域内把地址解析结果重排为「IPv4 优先、IPv6 兜底」。

    不删除任何地址族：IPv4 在前的列表会让 urllib3 的 create_connection 先试 IPv4，
    IPv4 不可达时自动回退到 IPv6——既避开「Windows 优先 IPv6 被 RST」的坑，
    又保留对「仅 IPv6 可达」源的兼容性。
    """
    orig = socket.getaddrinfo

    def _gai_prefer(host, port, family=socket.AF_UNSPEC, type=0, proto=0, flags=0):
        results = orig(host, port, family, type, proto, flags)
        # 稳定性排序：AF_INET 排在前，其余（含 AF_INET6）排在后
        return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)

    socket.getaddrinfo = _gai_prefer
    try:
        yield
    finally:
        socket.getaddrinfo = orig


class _PreferIpv4Session(req.Session):
    """每次请求期间「IPv4 优先、IPv6 兜底」的 Session（对调用方透明）。"""

    def request(self, method, url, **kwargs):  # type: ignore[override]
        with _prefer_ipv4():
            return super().request(method, url, **kwargs)


def _build_session() -> _IPv4Session:
    s = _IPv4Session()
    s.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    adapter = HTTPAdapter(
        max_retries=_make_retry(), pool_connections=10, pool_maxsize=10
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# 单例：CMS 全链路（探测 / 分类 / 列表 / 详情）共用，享受 keep-alive 与自动重试。
cms_session: _PreferIpv4Session = _build_session()
