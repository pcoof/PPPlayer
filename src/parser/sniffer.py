"""媒体嗅探器 — 爬取网页提取真实视频播放地址。

完全复刻 worker.js 中 parsePlayUrl 和 sniffMediaUrl 的逻辑。
支持：DPlayer, ArtPlayer, CKplayer, Aliplayer, JWPlayer 等。
"""

from urllib.parse import urljoin, urlparse
from typing import Any
import re
import json
import requests as req

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _extract_video_from_json(data: Any) -> str | None:
    """从 JSON 对象中递归提取视频地址"""
    if not data or not isinstance(data, (dict, list)):
        return None

    video_fields = [
        "url", "src", "file", "playUrl", "videoUrl", "video_url",
        "play_url", "stream", "hls", "m3u8", "mp4", "source",
        "data", "info", "player",
    ]

    if isinstance(data, dict):
        for field in video_fields:
            if field in data:
                val = data[field]
                result = _check_value(val)
                if result:
                    return result

        for key, val in data.items():
            if isinstance(val, str):
                if _is_video_url(val):
                    return val
            elif isinstance(val, (dict, list)):
                result = _extract_video_from_json(val)
                if result:
                    return result

    elif isinstance(data, list):
        for item in data:
            result = _extract_video_from_json(item)
            if result:
                return result

    return None


def _check_value(val: Any) -> str | None:
    """检查值是否为视频 URL"""
    if isinstance(val, str):
        if _is_video_url(val):
            return val
    elif isinstance(val, (dict, list)):
        return _extract_video_from_json(val)
    return None


def _is_video_url(s: str) -> bool:
    """判断字符串是否为视频直链"""
    if not s or not isinstance(s, str):
        return False
    lower = s.lower()
    if ".m3u8" in lower or ".mp4" in lower or ".webm" in lower or ".mov" in lower:
        return True
    if s.startswith("http") and len(s) > 20:
        if not any(kw in lower for kw in ["json", "config", "css", "example", "ico", "png", "jpg"]):
            return True
    return False


def parse_play_url(url: str) -> dict[str, Any]:
    """解析播放地址，返回统一格式结果。

    Returns:
        {
            "type": "direct|webpage|webpage_with_m3u8|player_video|api_video|iframe|js_redirect|unknown",
            "url": str,
            "m3u8": str | None,
            "origin": str | None,
            "resolved": bool,
            "error": str | None
        }
    """
    result: dict[str, Any] = {
        "type": "unknown",
        "url": url,
        "m3u8": None,
        "origin": None,
        "resolved": False,
        "error": None,
    }

    lower_url = url.lower()

    # 直链 — m3u8 / mp4 / ts
    if ".m3u8" in lower_url or ".ts" in lower_url or ".mp4" in lower_url:
        result["type"] = "direct"
        result["resolved"] = True
        result["origin"] = urlparse(url).netloc
        return result

    # 网页播放页
    if any(kw in lower_url for kw in [".html", ".htm", "/play/", "/player/"]):
        result["type"] = "webpage"

        try:
            resp = req.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=10,
                allow_redirects=True,
            )
            html = resp.text

            # 1. 正则提取 m3u8 直链
            m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            if m3u8_match:
                result["m3u8"] = m3u8_match.group(1)
                result["type"] = "webpage_with_m3u8"
                result["resolved"] = True
                return result

            # 2. 常见播放器 JS 配置提取
            player_patterns = [
                r'''Video\s*url["']?\s*[:=]\s*["']([^"']+)["']''',
                r'''video\s*:\s*["']([^"']+\.(?:mp4|m3u8|webm)[^"']*)["']''',
                r'''src\s*:\s*["']([^"']+\.(?:mp4|m3u8|webm)[^"']*)["']''',
                r'''player\.src\s*\(\s*["']([^"']+)["']''',
                r'''new\s+DPlayer\s*\([^)]*url\s*:\s*["']([^"']+)["']''',
                r'''new\s+Artplayer\s*\([^)]*url\s*:\s*["']([^"']+)["']''',
            ]
            for pattern in player_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    video_url = match.group(1)
                    if video_url.startswith("http"):
                        result["m3u8"] = video_url
                        result["type"] = "player_video"
                        result["resolved"] = True
                        return result

            # 3. JS 变量中的 JSON 对象提取
            json_match = re.search(r'var\s+\w+\s*=\s*(\{[\s\S]*?\});', html)
            if json_match:
                try:
                    json_data = json.loads(json_match.group(1))
                    extracted = _extract_video_from_json(json_data)
                    if extracted:
                        result["m3u8"] = extracted
                        result["type"] = "api_video"
                        result["resolved"] = True
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass

            # 4. iframe 嵌套
            iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if iframe_match:
                result["type"] = "iframe"
                result["url"] = iframe_match.group(1)
                result["resolved"] = False
                return result

            # 5. JS 重定向
            js_match = re.search(
                r'(?:window\.|document\.)(?:location|href|src)\s*=\s*["\']([^"\']+)["\']',
                html,
            )
            if js_match:
                result["type"] = "js_redirect"
                result["url"] = js_match.group(1)
                result["resolved"] = False
                return result

            # 6. 尝试 API 端点
            api_match = re.search(r'/play/([a-zA-Z0-9]+)', url)
            if api_match:
                video_id = api_match.group(1)
                base = urlparse(url).netloc
                base_origin = f"{urlparse(url).scheme}://{base}"
                api_urls = [
                    f"{base_origin}/api/player/{video_id}",
                    f"{base_origin}/api/vid/{video_id}",
                    f"{base_origin}/player/{video_id}/info",
                    f"{base_origin}/api/source/{video_id}",
                    f"{base_origin}/api/video/{video_id}",
                ]
                for api_url in api_urls:
                    try:
                        api_resp = req.get(
                            api_url,
                            headers={"User-Agent": USER_AGENT},
                            timeout=5,
                        )
                        if api_resp.status_code == 200:
                            try:
                                api_data = api_resp.json()
                                extracted = _extract_video_from_json(api_data)
                                if extracted:
                                    result["m3u8"] = extracted
                                    result["type"] = "api_video"
                                    result["resolved"] = True
                                    return result
                            except (json.JSONDecodeError, ValueError):
                                continue
                    except req.RequestException:
                        continue

        except req.RequestException as e:
            result["error"] = str(e)

        return result

    return result
