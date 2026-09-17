"""苹果 CMS (JSON API) 适配器"""

from typing import Any
from urllib.parse import urlparse

from .base import BaseCMS, CMSNoSearchError, looks_like_no_search
from .endpoint import resolve_endpoint, merge_query
from .http import cms_session, cms_get


class AppleCMS(BaseCMS):
    """苹果 CMS — 标准 JSON 接口 /api.php/provide/vod/"""

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """请求 CMS JSON 接口

        端点由 resolve_endpoint() 决定：站点根才补 /api.php/provide/vod/，
        已是完整端点（json.html / json.php / provide/vod 等）则原样使用。
        动作参数用 merge_query 合并（剔除源地址自带的 ac/pg 等，避免重复键），
        并带同源 Referer 以降低部分源站的防盗链 403。
        """
        url = merge_query(resolve_endpoint(self.base_url), params)
        origin = "{0.scheme}://{0.netloc}".format(urlparse(self.base_url))
        resp = cms_get(
            url,
            headers={"Accept": "application/json, text/json, */*", "Referer": origin + "/"},
            timeout=30,
        )
        resp.raise_for_status()
        text = (resp.text or "").strip()
        try:
            return resp.json()
        except ValueError:
            # 非 JSON：可能是源站明确返回的「不支持搜索」纯文本提示
            if looks_like_no_search(text):
                raise CMSNoSearchError(text or "该 API 源明确返回不支持搜索")
            raise

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
