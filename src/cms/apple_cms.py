"""苹果 CMS (JSON API) 适配器"""

from typing import Any
import requests as req
from .base import BaseCMS
from .endpoint import resolve_endpoint

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class AppleCMS(BaseCMS):
    """苹果 CMS — 标准 JSON 接口 /api.php/provide/vod/"""

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """请求 CMS JSON 接口

        端点由 resolve_endpoint() 决定：站点根才补 /api.php/provide/vod/，
        已是完整端点（json.html / json.php / provide/vod 等）则原样使用。
        """
        url = resolve_endpoint(self.base_url)
        resp = req.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_classes(self) -> list[dict[str, Any]]:
        data = self._request({})
        classes = data.get("class", [])
        if not classes:
            classes = data.get("data", {}).get("class", [])
        return classes

    def fetch_videos(
        self, ac: str = "videolist", pg: int = 1, t: str = "", wd: str = ""
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"ac": ac, "pg": pg}
        if t:
            params["t"] = t
        if wd:
            params["wd"] = wd

        data = self._request(params)

        # 统一字段名
        result: dict[str, Any] = {}
        result["list"] = data.get("list", [])
        if not result["list"]:
            result["list"] = data.get("data", {}).get("list", [])

        result["pagecount"] = data.get(
            "pagecount", data.get("totalpage", data.get("data", {}).get("pagecount", 1))
        )
        return result

    def fetch_detail(self, vod_id: str) -> dict[str, Any]:
        data = self._request({"ac": "detail", "ids": vod_id})
        items = data.get("list", [])
        if not items:
            items = data.get("data", {}).get("list", [])
        return items[0] if items else {}
