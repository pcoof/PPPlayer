"""CMS 统一 HTTP 客户端。

问题背景：Windows 上 requests/urllib3 默认按 getaddrinfo 返回顺序连接，常优先尝试
IPv6；而许多 CMS 源并不真正监听 IPv6，于是握手阶段就被对端 RST，表现为
`ConnectionResetError(10054) / 远程主机强迫关闭了一个现有的连接`。浏览器（Chrome）
有 Happy Eyeballs，会自动回退到可用的 IPv4，所以出现「浏览器能开、Python 请求 502」。

地址族策略（强制 IPv4，彻底规避「Windows 优先 IPv6 被源站 RST」）：

- **主机制：直接替换 `urllib3` 的 `create_connection`** 为 IPv4-only 实现
  （`_force_ipv4_create_connection`）：用 `socket.AF_INET` 解析、自实现连接循环，
  彻底绕过任何 IPv6 尝试；仅当主机确实无 IPv4 记录时才回退 urllib3 原逻辑。
  该写法**不依赖 urllib3 如何引用 `getaddrinfo`**（部分旧版 urllib3 在模块加载时
  直接 `from socket import getaddrinfo` 绑定了名字，仅 patch `socket.getaddrinfo`
  模块属性会失效），对所有 requests/urllib3 版本透明生效，且无 PoolKey 兼容问题。
- **辅助机制：`_prefer_ipv4` getaddrinfo 作用域补丁**（保留作为双保险）：每次请求期间
  把 `socket.getaddrinfo` 的结果过滤为「只用 IPv4」。与主机制叠加，互为兜底。
- **严禁使用 `socket_family=AF_INET` 注入连接池**：本应用实际部署的 urllib3 版本较旧，
  `PoolManager`/`PoolKey` 不认 `key_socket_family`，会直接抛
  `PoolKey.__new__() got an unexpected keyword argument 'key_socket_family'`
  （2026-09-15 实测：沙箱 urllib3 2.7.0 可注入，但用户运行环境旧版会崩溃，故弃用）。
- 代价：极少数「仅 IPv6 可达」的源无法连接——但本类 CMS/媒体源均经 IPv4 提供（浏览器
  Happy Eyeballs 同样走 IPv4 才正常），该取舍可接受。
"""
from __future__ import annotations

import contextlib
import json
import ssl
import socket

import requests as req
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import ProtocolError

import urllib3
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

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


# ── 强制 IPv4 连接（根治 Windows 优先 IPv6 被源站 RST 10054）────────────
# 仅靠给 urllib3 注入 socket_family 会触发 PoolKey 兼容崩溃（旧版 urllib3），
# 而仅 patch socket.getaddrinfo 在部分 urllib3 版本里因「直接 import getaddrinfo」
# 而失效。最稳的做法是直接替换 urllib3 的 create_connection：用 AF_INET 解析、
# 自实现连接循环，彻底绕过任何 IPv6 尝试；仅当主机确实无 IPv4 记录时才回退原逻辑。
def _install_ipv4_connection_patch():
    """把 urllib3.util.connection.create_connection 替换为 IPv4-only 版本（幂等）。

    覆盖 urllib3 在模块加载时把 create_connection 直接 import 进 connection 模块的写法，
    确保无论 urllib3 如何引用 getaddrinfo，连接都只走 IPv4。
    """
    global _orig_create_connection
    try:
        import urllib3.util.connection as _uc
    except Exception:
        return
    if getattr(_uc, "create_connection", None) is _force_ipv4_create_connection:
        return  # 已安装，幂等
    _orig_create_connection = _uc.create_connection
    _uc.create_connection = _force_ipv4_create_connection
    # 部分版本把 create_connection 直接 import 进 connection 模块，一并替换
    try:
        import urllib3.connection as _conn
        if getattr(_conn, "create_connection", None) is not None:
            _conn.create_connection = _force_ipv4_create_connection
    except Exception:
        pass
    # 旧版 urllib3 在 connection 模块里 `from socket import getaddrinfo` 把名字**绑定**到了
    # 模块命名空间，导致仅 patch `socket.getaddrinfo`（_prefer_ipv4 作用域补丁）对它完全无效
    # （这正是此前「IPv4 补丁在用户环境静默失效」的根因）。这里直接替换
    # urllib3.connection.getaddrinfo 为「IPv4 优先」版本，从根上覆盖旧版 urllib3。
    try:
        import urllib3.connection as _conn
        _gai = getattr(_conn, "getaddrinfo", None)
        if _gai is not None and getattr(_conn, "_tsp_ipv4_gai_patched", False) is False:
            def _gai_ipv4_only(host, port, family=socket.AF_UNSPEC, type=0, proto=0, flags=0):
                results = _gai(host, port, family, type, proto, flags)
                ipv4 = [r for r in results if r[0] == socket.AF_INET]
                return ipv4 if ipv4 else results
            _conn.getaddrinfo = _gai_ipv4_only
            try:
                _conn._tsp_ipv4_gai_patched = True
            except Exception:
                pass
    except Exception:
        pass


_orig_create_connection = None

# socket._GLOBAL_DEFAULT_TIMEOUT 是 CPython 的内部哨兵对象，直接传给 settimeout 会抛
# TypeError；需归一化为 None（使用系统默认超时）。不同版本 urllib3 可能传 None 或该哨兵。
_GLOBAL_DEFAULT_TIMEOUT = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", None)


def _force_ipv4_create_connection(address, *args, **kwargs):
    """强制仅 IPv4 的 create_connection（兼容 urllib3 各版本签名）。

    优先用 socket.AF_INET 解析、只连 IPv4；若该主机无任何 IPv4 记录，则回退到
    urllib3 原逻辑（可能走 IPv6）。连接循环为 urllib3 原实现的忠实复刻，
    支持 timeout / source_address / socket_options，HTTPS 的 SSL 包装不受影响。

    **防御性兜底**：本函数体内任何意外异常（含老版本 urllib3 传参差异导致的 TypeError 等）
    都会回退到 urllib3 原 create_connection，保证「至少不比修复前更差」——
    修复前 IPv4 源至少能连通，不会因为本补丁反而「全部连不上」。
    """
    try:
        return _force_ipv4_create_connection_impl(address, *args, **kwargs)
    except Exception:
        # 任何异常都回退 urllib3 原实现，绝不扩散成「全部连接失败」
        if _orig_create_connection is not None:
            return _orig_create_connection(address, *args, **kwargs)
        raise


def _force_ipv4_create_connection_impl(address, *args, **kwargs):
    host, port = address[:2]
    if host and host.startswith("["):
        host = host[1:-1]
    # 优先解析 IPv4
    infos = []
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        infos = []
    if not infos:
        # 无 IPv4 记录：回退到 urllib3 原逻辑（可能走 IPv6）
        if _orig_create_connection is not None:
            return _orig_create_connection(address, *args, **kwargs)
        raise OSError("getaddrinfo returns an empty list for %s" % (host,))
    # 解析参数（兼容位置 / 关键字两种传参）
    timeout = kwargs.get("timeout")
    if timeout is None and len(args) > 0:
        timeout = args[0]
    # 归一化超时：哨兵对象 / None 都当作「使用系统默认」，避免把哨兵直接传给 settimeout 抛 TypeError
    if timeout is _GLOBAL_DEFAULT_TIMEOUT or timeout is None:
        timeout = None
    source_address = kwargs.get("source_address")
    if source_address is None and len(args) > 1:
        source_address = args[1]
    socket_options = kwargs.get("socket_options")
    if socket_options is None and len(args) > 2:
        socket_options = args[2]

    err = None
    for res in infos:
        af, socktype, proto, _, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if socket_options:
                for opt in socket_options:
                    sock.setsockopt(*opt)
            if timeout:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
    if err is not None:
        raise err
    raise OSError("getaddrinfo returns an empty list")


# 模块导入即安装（幂等），覆盖 CMS 与媒体代理的全部 urllib3 连接
_install_ipv4_connection_patch()


@contextlib.contextmanager
def _prefer_ipv4():
    """在 with 作用域内把地址解析结果过滤为「只用 IPv4」。

    有 IPv4 结果时**彻底排除 IPv6**：urllib3 的 create_connection 只会拿到 IPv4 地址，
    连 IPv6 都不会去碰，从根上避免「Windows 优先试 IPv6 被源站 RST（10054）」。
    仅当该主机确实没有任何 IPv4 时，才回退到原始列表（兜住「仅 IPv6 可达」源）。
    该写法不依赖 urllib3 版本，对所有 requests 调用透明、无 PoolKey 兼容问题。
    """
    orig = socket.getaddrinfo

    def _gai_prefer(host, port, family=socket.AF_UNSPEC, type=0, proto=0, flags=0):
        results = orig(host, port, family, type, proto, flags)
        ipv4 = [r for r in results if r[0] == socket.AF_INET]
        return ipv4 if ipv4 else results

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


# ── TLS 层 Happy Eyeballs（根治「CDN 多 IPv4、部分节点 TLS 握手被 RST」）──
# 背景：以 caiji.dyttzyapi.com 为例，其 CDN 解析出 3 个 IPv4，其中 2 个在 TLS 握手阶段
# 被对端 RST，仅 1 个能完成 TLS。urllib3 默认只在「TCP 连接错误」时换 IP 重试，TLS 握手
# 失败（SSLError）不算连接错误、不会换 IP——于是只要 DNS 把坏 IP 排前面就直接失败。
# 浏览器有 Happy Eyeballs 会自动换 IP，所以「浏览器能开、Python 检测不通过」。
# 修复：自定义 HTTPSConnection，在 super().connect() 抛 TLS/连接错误时，逐个把
# getaddrinfo 强制指向下一个 IPv4 重新走 urllib3 自己的完整 TLS 握手，选第一个 TLS 成功的。
# 全程复用 urllib3 的握手逻辑、不复制其内部结构；任何意外都回退到默认 connect，绝不扩散。
class _TlsHappyEyeballsHTTPSConnection(HTTPSConnection):
    def connect(self) -> None:
        try:
            super().connect()
            return
        except (ssl.SSLError, OSError, ProtocolError) as _first:
            self._connect_tls_fallback(_first)

    def _connect_tls_fallback(self, first_err):
        real_gai = socket.getaddrinfo
        last = first_err
        try:
            try:
                infos = real_gai(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
            except Exception:
                raise first_err
            ips = []
            for r in infos:
                sa = r[4]
                if sa[0] not in ips:
                    ips.append(sa[0])
            if not ips:
                raise first_err
            for ip in ips:
                # 临时把 getaddrinfo 钉到单一 IPv4，让 urllib3 走完整 TLS 握手试这个 IP
                def _one(host, port, family=socket.AF_UNSPEC, type=0, proto=0, flags=0, _ip=ip):
                    fam = socket.AF_INET if family == socket.AF_UNSPEC else family
                    return [(fam, socket.SOCK_STREAM, 6, "", (_ip, port))]

                socket.getaddrinfo = _one
                try:
                    # 先清理上一次可能残留的半握手 socket
                    try:
                        if self.sock is not None:
                            self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
                    super().connect()
                    return
                except (ssl.SSLError, OSError, ProtocolError) as _e:
                    last = _e
                    continue
                finally:
                    socket.getaddrinfo = real_gai
        except Exception:
            pass
        finally:
            socket.getaddrinfo = real_gai
        raise last


class _TlsHappyEyeballsHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _TlsHappyEyeballsHTTPSConnection


class _TlsHappyEyeballsAdapter(HTTPAdapter):
    """让 HTTPS 连接池使用支持 TLS 层 Happy Eyeballs 的连接类。"""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        try:
            pm = getattr(self, "poolmanager", None)
            if pm is not None and hasattr(pm, "pool_classes_by_scheme"):
                pm.pool_classes_by_scheme["https"] = _TlsHappyEyeballsHTTPSConnectionPool
        except Exception:
            pass


def _build_session() -> _PreferIpv4Session:
    s = _PreferIpv4Session()
    # 绕开系统代理/VPN/爬虫工具注入的 HTTP_PROXY/HTTPS_PROXY：本应用直连源站，
    # 由上面的 IPv4 强制 + TLS Happy Eyeballs 直接处理 CDN 多节点；若让请求走系统
    # 代理会触发 MITM 重签证书(之前那条 CERTIFICATE_VERIFY_FAILED)并破坏 Happy Eyeballs。
    # 代理只影响用户浏览器，不影响本软件。
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    # IPv4 强制由 _PreferIpv4Session.request 内的 getaddrinfo 补丁实现（版本无关、无
    # PoolKey 兼容风险）。HTTPS 额外启用「TLS 层 Happy Eyeballs」以规避 CDN 坏节点。
    adapter = _TlsHappyEyeballsAdapter(
        max_retries=_make_retry(), pool_connections=10, pool_maxsize=10
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# 单例：CMS 全链路（探测 / 分类 / 列表 / 详情）共用，享受 keep-alive 与自动重试。
cms_session: _PreferIpv4Session = _build_session()


# ── Cloudflare 等「浏览器验证」墙识别与浏览器引擎回退 ──────────
# 背景：部分源（如 api.wujinapi.me）套了 Cloudflare 的「Just a moment / 需要验证」
# JS 挑战页。requests / urllib3 / curl_cffi(TLS 伪装) / cloudscraper(已过时) 均无法
# 通过——只有真实浏览器引擎执行 JS 才能解出 cf_clearance。本机浏览器能开、Python 被 403
# 正是这个原因。因此这里在 requests 命中挑战页时，自动回退到 playwright + 系统 Edge/Chrome
# 求解。playwright 未安装时静默降级（保留原始 403，由上层给出友好提示），不影响其它源。
_CLOUDFLARE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-chl",
    "_cf_chl_",
    "challenge-platform",
    "verify you are human",
    "enable javascript and cookies to continue",
    "安全验证",
    "正在进行",
    "turnstile",
)


def _looks_like_challenge(resp) -> bool:
    """判断响应是否为 Cloudflare 之类的「浏览器验证」拦截页。"""
    if resp is None:
        return False
    code = getattr(resp, "status_code", 0)
    headers = getattr(resp, "headers", None) or {}
    ctype = headers.get("Content-Type", "") or ""
    text = (getattr(resp, "text", "") or "")[:3000].lower()
    if code in (403, 503) and "text/html" in ctype:
        return any(m in text for m in _CLOUDFLARE_MARKERS)
    if code == 200 and any(m in text for m in _CLOUDFLARE_MARKERS):
        return True
    return False


class _BrowserResponse:
    """用真实浏览器引擎取回的内容，伪装成 requests.Response 供适配器无缝使用。"""

    def __init__(self, status_code: int, text: str, url: str = ""):
        self.status_code = status_code
        self._text = text
        self.url = url
        self.headers = {"Content-Type": "application/json; charset=utf-8"}
        self.ok = 200 <= status_code < 300

    @property
    def content(self):
        return self._text.encode("utf-8", "replace")

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if not self.ok:
            raise req.exceptions.HTTPError("browser fetch status %d" % self.status_code)


_browser_runtime = None  # (playwright, browser, page) 懒加载单例


def _get_browser_runtime():
    global _browser_runtime
    if _browser_runtime is not None:
        return _browser_runtime
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError("未安装 playwright，无法用浏览器绕过 Cloudflare：" + str(e))
    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch(channel="msedge", headless=True, args=["--no-sandbox"])
    except Exception:
        browser = sp.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page()
    _browser_runtime = (sp, browser, page)
    return _browser_runtime


def _browser_get(url: str, headers=None, timeout: int = 30):
    """用真实浏览器引擎（playwright + 系统 Edge/Chrome）打开 URL，等待 Cloudflare
    验证完成后，把浏览器拿到的 cf_clearance 等 cookie 回灌进 requests 会话，再用
    requests 重新发起一次「带 clearance」的真实请求，从而返回干净的 JSON/XML 响应
    （而不是去解析浏览器渲染后的 innerText，避免 HTML 包裹 JSON 导致解析失败）。

    需 playwright 已安装且系统有 Edge/Chrome；否则由调用方捕获异常后降级为原始 403。
    """
    _, _, page = _get_browser_runtime()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
    except Exception:
        pass
    # 等待 Cloudflare 挑战自动解除（标题/正文不再含挑战标记）
    try:
        page.wait_for_function(
            "() => { const t = (document.title||'') + (document.body?document.body.innerText:''); "
            "return !/just a moment|checking your browser|cf-chl|verify you are human|安全验证|正在进行|turnstile/i.test(t); }",
            timeout=timeout * 1000,
        )
    except Exception:
        pass
    # 把浏览器上下文里的 cookie（含 cf_clearance）灌回 requests 会话
    try:
        for c in page.context.cookies(url):
            try:
                cms_session.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain"), path=c.get("path", "/"),
                )
            except Exception:
                pass
    except Exception:
        pass
    # 用带 clearance 的会话重新请求 → 拿到真实 JSON/XML 响应
    try:
        resp = cms_session.get(url, headers=headers, timeout=timeout)
    except req.exceptions.HTTPError as e:
        resp = e.response
    if resp is not None and _looks_like_challenge(resp):
        # 仍被拦截：返回 403，让上层走友好提示
        return _BrowserResponse(403, (getattr(resp, "text", "") or ""), url)
    return resp


def cms_get(url: str, headers=None, timeout: int = 30):
    """CMS 统一 GET：先走 requests（快）；若命中 Cloudflare 等 JS 验证墙，
    自动回退到浏览器引擎（playwright + 系统 Edge/Chrome）求解后再取数据。
    playwright 未安装或浏览器不可用时不抛错，保留原始 403 响应以触发友好提示。
    """
    try:
        resp = cms_session.get(url, headers=headers, timeout=timeout)
    except req.exceptions.HTTPError as e:
        resp = e.response
        if resp is not None and _looks_like_challenge(resp):
            try:
                return _browser_get(url, headers, timeout)
            except Exception:
                return resp
        raise
    if _looks_like_challenge(resp):
        try:
            return _browser_get(url, headers, timeout)
        except Exception:
            return resp
    return resp
