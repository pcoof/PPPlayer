"""M3U8 代理下载与广告过滤模块

智能去广告策略（smartAdRemove）：
- 解析所有切片，按 ``#EXT-X-DISCONTINUITY`` 边界把播放列表切成若干「段组(run)」。
- 每个段组用其切片 URI 的「目录签名」(dirname) 归并到同一「视频源(source)」。
  广告切片与正片切片通常是两套独立视频源，目录（含码率/随机流 ID）明显不同。
- 正片源 = 总时长最长的源（阈值 >= 0.5 * 最长）；其余（明显更短的段组）判为广告。
  这样片头广告、片中插播广告都能被识别并剔除，且不会误删正片。
- 重建播放列表时只保留正片源切片，并丢弃所有 ``#EXT-X-DISCONTINUITY`` 标记，
  输出一份干净的单源 VOD 列表交给播放器。
- 若全片只有一个源（无可识别广告），则原样透传（保留 discontinuity，避免误伤正常流）。

统一代理（关键 —— 解决 CDN 无 CORS 头导致的跨域失败）：
- 过滤后的播放列表里所有 CDN 地址（切片 .ts/.m4s、密钥 .key、字幕 .vtt、嵌套播放列表）
  都被改写为本服务的同源代理地址：
    * 媒体/字节类  -> /api/media?url=<编码后的绝对地址>
    * 嵌套播放列表 -> /api/hls?url=<编码后的绝对地址>（再 302 到 .m3u8 结尾地址）
- 浏览器全程只与本服务（127.0.0.1:19527）同源通信，从根本上消除 CORS，
  且后端可并发拉取（Flask threaded）+ 缓存，速度接近直连。
"""

from collections import Counter
from urllib.parse import urljoin, urlparse, quote
import posixpath
import re
import time

import requests as req
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 播放列表拉取也复用长连接（切集时反复拉取同一 CDN 的列表/变体，keep-alive 避免重复握手）。
_FETCH_SESSION = req.Session()
_FETCH_SESSION.mount(
    "http://",
    HTTPAdapter(pool_connections=20, pool_maxsize=20,
                max_retries=Retry(total=2, backoff_factor=0.3,
                                  status_forcelist=[429, 500, 502, 503, 504],
                                  allowed_methods=["GET", "HEAD"])),
)
_FETCH_SESSION.mount(
    "https://",
    HTTPAdapter(pool_connections=20, pool_maxsize=20,
                max_retries=Retry(total=2, backoff_factor=0.3,
                                  status_forcelist=[429, 500, 502, 503, 504],
                                  allowed_methods=["GET", "HEAD"])),
)

# 关键词兜底：命中这些特征的直接判为广告（与智能识别互补，不会误删正片）
AD_KEYWORDS = ["/ad/", "/gg/", "/advert/", "/advert", "ad.m3u8", "ad_", "_ad"]
AD_DOMAINS = ["douyinvod.com", "bytedance.com"]

# 后端已拉取播放列表的短期缓存（按 URL 去重）：
# HLS.js 初始化/错误重试/拉取变体时常对同一地址重复请求，单线程下每个请求都重走「后端→CDN」
# 整趟往返，叠加串行阻塞就是「有时加载不出 + 比原链接慢」的主因。VOD 列表短时内不变，
# 缓存 20s 即可让重复请求瞬时命中，既提速又降低 CDN 被限频的概率。
_TEXT_CACHE: dict = {}
_TEXT_TTL = 20

# ── 统一代理改写 ─────────────────────────────────────────────
# 媒体/字节类扩展名走 /api/media（后端按字节转发，支持 Range）；
# 其余（嵌套 .m3u8 播放列表、未知）走 /api/hls（后端再拉取+过滤+302 到 .m3u8 结尾地址）。
_MEDIA_EXT_RE = re.compile(
    r"\.(ts|m4s|m4a|aac|mp3|mp4|webm|mov|m4v|key|vtt|srt|jpe?g|png|webp|gif|cmfv|cmfa)(\?|#|$)",
    re.IGNORECASE,
)
# 标签行里 URI="..." / PREFIX="..." 形式的地址（如 #EXT-X-KEY URI="..."）
_TAG_URI_RE = re.compile(r'(URI|PREFIX)="([^"]*)"')


def _is_media_url(url: str) -> bool:
    return bool(_MEDIA_EXT_RE.search(url or ""))


def _to_proxy(abs_url: str) -> str:
    """把一个绝对的 CDN 地址改写为本服务同源代理地址。"""
    if _is_media_url(abs_url):
        return "/api/media?url=" + quote(abs_url, safe="")
    return "/api/hls?url=" + quote(abs_url, safe="")


def _proxy_tag_line(line: str, base_url: str) -> str:
    """把标签行里的 URI="..."/PREFIX="..." 改写为代理地址。

    三种来源都要覆盖：
    - 绝对地址 http(s)://...      -> 直接代理
    - 根相对路径 /foo/bar.key     -> 按 base_url 的 origin 绝对化后代理
      （HLS 的 AES-128 密钥 URI 常见写法，浏览器会把它拼到本服务地址导致 404，
        必须改写为 /api/media 代理，否则整条流无法解密）
    - 相对路径 foo/bar.ts          -> 按 base_url 目录绝对化后代理
    """
    def repl(m):
        attr, val = m.group(1), m.group(2)
        if base_url:
            # urljoin 同时正确处理 绝对地址 / 根相对路径(按 origin) / 相对路径(按目录)
            return f'{attr}="{_to_proxy(urljoin(base_url, val))}"'
        if val.startswith("http"):
            return f'{attr}="{_to_proxy(val)}"'
        return m.group(0)
    return _TAG_URI_RE.sub(repl, line)


def _proxy_all_urls(text: str, base_url: str = "") -> str:
    """把播放列表文本里的所有 CDN 地址改写为同源代理地址。

    - 标签行（# 开头）：改写其中的 URI="..."/PREFIX="..."（如加密密钥）。
    - 裸绝对地址行：直接改写。
    - 裸相对地址行：先用 base_url 绝对化再改写（规避相对地址被播放器误判到本服务路径）。
    """
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            out.append(_proxy_tag_line(line, base_url))
        elif s.startswith("http"):
            out.append(_to_proxy(s))
        elif s:
            if base_url:
                out.append(_to_proxy(urljoin(base_url, s)))
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


def _fetch_text(url: str, referer: str = "", timeout: int = 15) -> str:
    """请求 URL 返回文本（带连接/读取分离超时与一次重试，提升偶发网络抖动的健壮性）。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    base_origin = f"{urlparse(url).scheme}://{urlparse(url).hostname}"
    headers["Origin"] = base_origin

    last_err = None
    # 连接 6s、读取 20s 分离；超时/连接失败时重试一次（CDN 偶发抖动）。
    # 复用连接池 Session：同一 CDN 主机的列表/变体请求走 keep-alive，无需重复握手。
    for attempt in range(2):
        try:
            resp = _FETCH_SESSION.get(url, headers=headers, timeout=(6, timeout), allow_redirects=True)
            resp.raise_for_status()
            text = resp.text
            if not text or len(text) < 10:
                raise ValueError(f"EMPTY_RESPONSE: {url}")
            return text
        except (req.exceptions.Timeout, req.exceptions.ConnectionError,
                req.exceptions.ChunkedEncodingError, ValueError) as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.4)   # 短暂退避后重试一次
                continue
    raise last_err if last_err else RuntimeError("fetch failed: " + url)


def _source_sig(uri: str) -> str:
    """用切片 URI 的目录作为「视频源」签名。

    广告与正片是两套独立视频源，其 CDN 目录（含码率标记、随机流 ID）普遍不同，
    例如 ``/20260731/UTxI1Mxv/9567kb/hls/`` vs ``/20260808/fhH82EJz/1000kb/hls/``。
    """
    try:
        p = urlparse(uri).path
        d = posixpath.dirname(p)
    except Exception:
        d = uri
    return d or uri


def _is_ad_signature(sig: str) -> bool:
    low = (sig or "").lower()
    for kw in AD_KEYWORDS:
        if kw in low:
            return True
    for domain in AD_DOMAINS:
        if domain in low:
            return True
    return False


def _fmt_dur(dur: float) -> str:
    s = ("%.2f" % dur).rstrip("0").rstrip(".")
    return s if s else "0"


def _smart_filter(text: str, base_url: str) -> str:
    """对播放列表文本做智能去广告（不去绝对化/不改写代理，留给上层 _proxy_all_urls 统一处理）。

    返回只包含正片切片的干净播放列表；无可识别广告时原样透传。
    """
    lines = text.split("\n")

    header: list[str] = []
    segments: list[dict] = []
    pending: list[str] = []          # 紧跟在某切片前的属性标签（KEY/MAP/BYTERANGE）
    disc_pending = False
    in_header = True
    endlist = False

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#EXTINF"):
            m = re.search(r":\s*([0-9]+(?:\.[0-9]+)?)", s)
            dur = float(m.group(1)) if m else 0.0
            segments.append({
                "uri": None,
                "dur": dur,
                "tags": list(pending),
                "disc_before": disc_pending,
            })
            pending = []
            disc_pending = False
            in_header = False
            continue
        if s == "#EXT-X-DISCONTINUITY":
            disc_pending = True
            in_header = False
            continue
        if s == "#EXT-X-ENDLIST":
            endlist = True
            in_header = False
            continue
        if s.startswith("#"):
            if in_header:
                header.append(raw)
            else:
                pending.append(raw)
            continue
        # 普通 URI 行
        if segments and segments[-1]["uri"] is None:
            segments[-1]["uri"] = s
            if disc_pending:
                segments[-1]["disc_before"] = True
                disc_pending = False
        else:
            # 没有对应 #EXTINF 的游离 URI，放回头部
            header.append(raw)

    segments = [x for x in segments if x["uri"]]
    if not segments:
        out = list(header)
        if endlist:
            out.append("#EXT-X-ENDLIST")
        return "\n".join(out)

    for seg in segments:
        seg["sig"] = _source_sig(seg["uri"])

    # 按 discontinuity 边界切成段组 run
    runs: list[dict] = []
    cur = None
    for seg in segments:
        if seg["disc_before"] or cur is None:
            cur = {"segs": [], "sig": None, "dur": 0.0}
            runs.append(cur)
        cur["segs"].append(seg)
        cur["dur"] += seg["dur"]
    for r in runs:
        sigs = [s["sig"] for s in r["segs"]]
        r["sig"] = Counter(sigs).most_common(1)[0][0] if sigs else None

    # 把段组按签名归并到视频源
    sources: dict = {}
    for r in runs:
        src = sources.setdefault(r["sig"], {"dur": 0.0, "runs": []})
        src["dur"] += r["dur"]
        src["runs"].append(r)

    # 只有一个源 -> 无可识别广告 -> 原样透传（保留 discontinuity，避免误伤正常流）
    if len(sources) <= 1:
        return "\n".join(lines)

    max_dur = max(s["dur"] for s in sources.values())

    # 每个源对应哪些段组（run）下标，用于判断「夹心/边缘」位置
    run_index: dict = {sig: [] for sig in sources}
    for i, r in enumerate(runs):
        run_index[r["sig"]].append(i)
    n_runs = len(runs)

    def _is_ad_source(sig: str, s: dict) -> bool:
        if _is_ad_signature(sig):                 # 关键词命中直接判广告
            return True
        if s["dur"] >= 0.5 * max_dur:             # 总时长够长 -> 视为正片（避免误删多 CDN 正片）
            return False
        my = run_index[sig]
        # 夹心：存在另一源既在本源之前、又在本源之后出现 -> 本源是插入的广告
        sandwiched = any(
            other != sig
            and any(i < my[0] for i in run_index[other])
            and any(i > my[-1] for i in run_index[other])
            for other in run_index
        )
        # 边缘：短片头（最前）或短片尾（最后）也大概率是广告
        at_edge = my[0] == 0 or my[-1] == n_runs - 1
        return sandwiched or at_edge

    main_sigs = {sig for sig, s in sources.items() if not _is_ad_source(sig, s)}
    if not main_sigs:                   # 兜底：至少保留最长源
        longest = max(sources.items(), key=lambda kv: kv[1]["dur"])[0]
        main_sigs.add(longest)

    out = list(header)
    for r in runs:
        if r["sig"] in main_sigs:
            for seg in r["segs"]:
                out.extend(seg["tags"])
                out.append("#EXTINF:" + _fmt_dur(seg["dur"]) + ",")
                out.append(seg["uri"])
    if endlist:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out)


# ── 旧版「按关键词 + 跳前 N 片」兜底（smartAdRemove 关闭时使用）─────────────
def _is_ad_segment(uri: str) -> bool:
    lower = uri.lower()
    for kw in AD_KEYWORDS:
        if kw in lower:
            return True
    for domain in AD_DOMAINS:
        if domain in lower:
            return True
    return False


def _filter_ts_segments(lines: list[str], skip: int = 1) -> list[str]:
    out: list[str] = []
    skipped = 0
    for line in lines:
        stripped = line.strip()
        is_tag = stripped.startswith("#")
        is_ts = not is_tag and stripped and (".ts" in stripped or ".m4s" in stripped)
        if is_ts:
            if skipped < skip:
                skipped += 1
                continue
            if _is_ad_segment(stripped):
                continue
        if is_tag and "EXT-X-DISCONTINUITY" in stripped:
            out.append(line)
            continue
        out.append(line)
    return out


def filter_m3u8_text(text: str, base_url: str = "", smart: bool = True) -> str:
    """对已获取的 M3U8 文本做过滤（不再发起远程请求），并统一改写为同源代理地址。

    - 主播放列表：不去广告（广告在媒体列表），仅把内部地址代理化。
    - 媒体播放列表：智能去广告（smart 时）或直接透传；随后统一代理改写。
    """
    base = base_url or ""
    if "#EXTM3U" in text and "#EXT-X-STREAM-INF" in text:
        # 主播放列表：变体地址统一改写走 /api/hls（再 302 到 .m3u8 结尾地址），保证变体内广告也被过滤
        out = text
    elif smart:
        out = _smart_filter(text, base or "http://localhost/")
    else:
        # 旧版兜底：按关键词剔除（保留 discontinuity）
        out = "\n".join(_filter_ts_segments(text.split("\n"), 0))
    return _proxy_all_urls(out, base)


def filter_m3u8(raw_url: str, skip: int = 0, smart: bool = True) -> str:
    """
    下载并过滤 M3U8 文件（保留以兼容旧的直接调用）：
    1. 下载 M3U8
    2. 主播放列表原样透传（内部地址改写代理）；媒体播放列表智能去广告
    3. 统一把一切 CDN 地址改写为同源代理地址（/api/hls、/api/media），彻底规避跨域

    同一 URL（含 smart/skip 维度）的过滤结果做 20s TTL 缓存：HLS.js 初始化、错误重试、
    拉取变体都会重复请求同一地址，缓存命中可瞬时返回，避免每次都重走「后端→CDN」慢速往返。
    """
    cache_key = (raw_url, bool(smart), int(skip))
    now = time.time()
    cached = _TEXT_CACHE.get(cache_key)
    if cached and now - cached[1] < _TEXT_TTL:
        return cached[0]

    target = raw_url
    if not target.startswith("http"):
        target = f"https:{target}"

    text = _fetch_text(target)

    # 处理 Master Playlist：不去广告，交给上层统一代理改写（变体仍指向 /api/hls）。
    if "#EXTM3U" in text and "#EXT-X-STREAM-INF" in text:
        out = text
    elif smart:
        out = _smart_filter(text, target)
    else:
        # 旧版兜底
        lines = text.split("\n")
        if skip > 0:
            lines = _filter_ts_segments(lines, skip)
        out = "\n".join(lines)

    # 统一代理改写（所有 CDN 地址 -> 同源 /api/hls、/api/media）
    out = _proxy_all_urls(out, target)

    if len(_TEXT_CACHE) > 200:   # 长时间运行防止无限增长：超限即清空（VOD 列表短时不变，影响极小）
        _TEXT_CACHE.clear()
    _TEXT_CACHE[cache_key] = (out, now)
    return out
