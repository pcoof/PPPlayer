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
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from .http import cms_session, cms_get

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


# 我们控制、需在源地址已有查询里覆盖（避免重复 ac/pg 等）的键
_OVERRIDE_KEYS = ("ac", "pg", "t", "wd", "ids", "h", "type", "wd", "page")


def merge_query(base_url: str, params: dict) -> str:
    """把动作参数合并进源地址，保证 ac/pg/t/wd 等由我们唯一决定。

    解决的问题：
    - 用户填的源地址若已含查询（如 `...?ac=videolist`），直接 requests(params=) 会
      产生 `ac=videolist&ac=search` 这种重复键，服务端取错值 → 搜索被当成列表（返回完整数据）。
    - 按源地址是否已含 `?` 正确选用 `?` / `&` 拼接（交给 urlencode 处理）。
    - **容错**：很多 CMS 演示站给的分享链接把 `?` 误写成 `&`（如
      `.../provide/vod&ac=videolist&pg=1`）。若整串没有 `?` 却含 `&`，把第一个 `&`
      当作查询起始符改成 `?`，否则会拼出 `/vod&ac=...` 这种路径，被 PHP 框架
      判为「方法不存在」。
    做法：先剔除源地址里与我们同名的键，再合并我们的参数，绝不产生重复 ac。
    """
    raw = (base_url or "").strip()
    # 容错：无 ? 但含 & → 首个 & 当作查询起始符（修复演示站误写的 &）
    if "?" not in raw and "&" in raw:
        raw = raw.replace("&", "?", 1)
    p = urlparse(raw)
    # 去掉路径末尾多余的斜杠：避免生成 `.../vod/?ac=` 这种与已知可用
    # `.../vod?ac=` 不一致的形态（部分 PHP 框架对 `/vod` 与 `/vod/` 路由区分，
    # 后者可能命中「方法不存在」）。仅当无查询串时才处理，避免误伤带 ? 的地址。
    if not p.query and p.path.endswith("/") and p.path != "/":
        p = p._replace(path=p.path[:-1])
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    for k in _OVERRIDE_KEYS:
        q.pop(k, None)
    for k, v in params.items():
        if v in ("", None):
            continue
        q[k] = v
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))



def _probe_format(endpoint: str) -> str | None:
    """探测单个端点的返回格式：'json' / 'xml' / None（无法判断）。"""
    try:
        url = merge_query(endpoint, {"ac": "videolist", "pg": 1})
        resp = cms_get(
            url,
            headers={"Accept": "application/json, text/xml, */*"},
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
