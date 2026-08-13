"""HTTP 反向代理模块 — 转发请求并添加 CORS 头"""

from flask import Response, request
import requests as req

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def proxy_request() -> Response:
    """代理任意 HTTP/HTTPS 请求并添加 CORS 头"""
    target = request.args.get("u", "")
    if not target:
        return Response("Missing u parameter", status=400, headers=CORS_HEADERS)

    target = req.utils.unquote(target) if "%" in target else target

    try:
        resp = req.get(
            target,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=30,
            allow_redirects=True,
        )
        content_type = resp.headers.get("Content-Type", "application/json; charset=utf-8")
        return Response(
            resp.text,
            status=resp.status_code,
            headers={
                "Content-Type": content_type,
                "Cache-Control": "no-store",
                **CORS_HEADERS,
            },
        )
    except req.RequestException as e:
        return Response(f"Proxy Error: {e}", status=502, headers=CORS_HEADERS)
