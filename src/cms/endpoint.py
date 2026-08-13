"""CMS 端点解析与基于内容的类型探测。

历史上按「URL 子串」（如 /xml、/api.php/provide/vod）猜 CMS 类型并拼接请求地址，
在实际源上不可靠：很多源本身就是完整端点（/api/json.php、/api/xml.php、
/xxx/vod/json.html、/xinlangapi.php/provide/vod …），再补 /api.php/provide/vod/
会变成错误地址。

本模块改为：
1. resolve_endpoint() —— 只在「看起来是站点根」时才补标准苹果 CMS 路径；
   否则（已是完整端点）原样返回，避免错误拼接。
2. detect_format() —— 真正拉一次 ?ac=videolist 看返回是 JSON 还是 XML，
   以实际内容为准；结果按源地址做模块级缓存，避免每次 HTTP 请求都多发探针。
"""

from __future__ import annotations

import re
import requests as req

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 看起来是「完整 API 端点」的文件后缀
_ENDPOINT_EXT = re.compile(r"\.(php|html|htm|json|xml|asp|aspx|jsp)$", re.I)
# 路径最后一段是已知端点词（如 /api/json、/api/xml）
_ENDPOINT_WORDS = {"json", "xml", "vod", "list", "video", "api", "provide"}

# 探测结果缓存：key=base_url -> "json" | "xml"，避免每次请求都多发探针
_format_cache: dict[str, str] = {}


def resolve_endpoint(base_url: str) -> str:
    """把用户填写的源地址解析为真正的请求端点（不含查询参数）。

    规则（按地址识别不可靠，所以只在「明显是站点根」时才补标准路径）：
    - 已含 provide/vod          -> 已是端点，原样返回
    - 以脚本/端点文件名结尾     -> 已是端点，原样返回（json.php / xml.php / json.html …）
    - 末段是已知端点词          -> 已是端点，原样返回（/api/json / /api/xml …）
    - 其它（看起来是站点根）    -> 补标准苹果 CMS 路径 /api.php/provide/vod/
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return base
    low = base.lower()
    if "provide/vod" in low:
        return base
    if _ENDPOINT_EXT.search(low):
        return base
    if low.rsplit("/", 1)[-1] in _ENDPOINT_WORDS:
        return base
    return base + "/api.php/provide/vod/"


def _probe_format(endpoint: str) -> str | None:
    """探测单个端点的返回格式：'json' / 'xml' / None（无法判断）。"""
    try:
        resp = req.get(
            endpoint,
            params={"ac": "videolist", "pg": 1},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/xml, */*",
            },
            timeout=12,
        )
        text = (resp.text or "").strip()
    except Exception:
        return None
    if not text:
        return None
    low = text[:600].lower()
    if text.startswith("{"):
        return "json"
    if (
        "<?xml" in low
        or "<rss" in low
        or "<xml>" in low
        or "<video" in low
        or "<list" in low
    ):
        return "xml"
    return None


def detect_format(base_url: str) -> str:
    """基于内容探测源类型（json/xml），带缓存。

    地址识别不可靠时以实际返回为准；仅在站点根（被补了标准路径）时，
    额外把 /xml/ 作为飞飞 XML 的兜底候选。
    """
    if base_url in _format_cache:
        return _format_cache[base_url]

    base = (base_url or "").strip().rstrip("/")
    main = resolve_endpoint(base)
    candidates = [main]
    if main != base:  # 站点根（被补了 /api.php/provide/vod/），补 /xml/ 兜底
        candidates.append(base + "/xml/")

    result = "json"  # 默认 JSON（苹果 CMS 最普遍）
    for ep in candidates:
        fmt = _probe_format(ep)
        if fmt == "json":
            result = "json"
            break
        if fmt == "xml":
            result = "xml"
            break
    _format_cache[base_url] = result
    return result
