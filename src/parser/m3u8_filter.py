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
"""

from collections import Counter
from urllib.parse import urljoin, urlparse, quote
import posixpath
import re

import requests as req

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 关键词兜底：命中这些特征的直接判为广告（与智能识别互补，不会误删正片）
AD_KEYWORDS = ["/ad/", "/gg/", "/advert/", "/advert", "ad.m3u8", "ad_", "_ad"]
AD_DOMAINS = ["douyinvod.com", "bytedance.com"]


def _fetch_text(url: str, referer: str = "", timeout: int = 15) -> str:
    """请求 URL 返回文本"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    base_origin = f"{urlparse(url).scheme}://{urlparse(url).hostname}"
    headers["Origin"] = base_origin

    resp = req.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    if not text or len(text) < 10:
        raise ValueError(f"EMPTY_RESPONSE: {url}")
    return text


def _resolve_relative(lines: list[str], base_url: str) -> list[str]:
    """将相对路径 TS/URL 转为绝对路径"""
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("http"):
            result.append(line)
            continue
        try:
            result.append(urljoin(base_url, stripped))
        except Exception:
            result.append(line)
    return result


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
    """对已经过 URL 绝对化的播放列表文本做智能去广告。

    返回只包含正片切片的干净播放列表；无可识别广告时原样透传。
    """
    lines = text.split("\n")
    lines = _resolve_relative(lines, base_url)

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


def _rewrite_master(text: str, base_url: str) -> str:
    """主播放列表（Master）：把所有变体 URI 改写为 /api/m3u8?url=<绝对地址>，
    这样 HLS.js/XGPlayer 选取变体时仍会经过后端过滤（广告切片在变体里）。

    注意：变体本身的远程拉取仍由后端完成；纯前端无法让 HLS.js 对变体走浏览器拉取，
    因此主播放列表场景依赖后端能拉到变体（公开 CDN 通常可以）。

    幂等：若变体行已经是本代理地址（含 /api/m3u8），保持原样，避免二次包装。
    否则把「/api/m3u8?url=<已编码>」再 urljoin 到 base_url 会得到
    https://<cdn>/api/m3u8?url=... 这种不存在的地址，导致 HLS 加载失败。
    """
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and (".m3u8" in s or s.startswith("http")):
            # 已经是经过本代理的地址 -> 保持原样，避免二次包装
            if "/api/m3u8" in s:
                out.append(s)
                continue
            abs_url = s if s.startswith("http") else urljoin(base_url, s)
            out.append("/api/m3u8?url=" + quote(abs_url, safe=""))
        else:
            out.append(line)
    return "\n".join(out)


def filter_m3u8_text(text: str, base_url: str = "", smart: bool = True) -> str:
    """对已获取的 M3U8 文本做过滤（不再发起远程请求）。

    - 主播放列表：改写变体地址走代理（smart 时）。
    - 媒体播放列表：智能去广告（smart 时）或直接透传。
    """
    base = base_url or ""
    if "#EXTM3U" in text and "#EXT-X-STREAM-INF" in text:
        # 主播放列表：变体地址统一改写为代理，保证变体内广告也被过滤
        return _rewrite_master(text, base)
    if smart:
        return _smart_filter(text, base or "http://localhost/")
    # 旧版兜底：按关键词剔除（保留 discontinuity）
    lines = text.split("\n")
    return "\n".join(_filter_ts_segments(lines, 0))


def filter_m3u8(raw_url: str, skip: int = 0, smart: bool = True) -> str:
    """
    下载并过滤 M3U8 文件（保留以兼容旧的直接调用）：
    1. 下载 M3U8
    2. 主播放列表改写变体地址走代理；媒体播放列表智能去广告
    3. 返回过滤后的 M3U8 文本
    """
    target = raw_url
    if not target.startswith("http"):
        target = f"https:{target}"

    text = _fetch_text(target)

    # 处理 Master Playlist：不再服务端递归下载变体，而是改写变体地址让播放器走代理
    if "#EXTM3U" in text and "#EXT-X-STREAM-INF" in text:
        if smart:
            return _rewrite_master(text, target)
        return text

    if smart:
        return _smart_filter(text, target)

    # 旧版兜底
    lines = text.split("\n")
    lines = _resolve_relative(lines, target)
    if skip > 0:
        lines = _filter_ts_segments(lines, skip)
    return "\n".join(lines)
