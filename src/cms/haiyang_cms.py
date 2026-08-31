"""海洋 CMS (XML/JSON 双接口) 适配器"""

from typing import Any

from .base import BaseCMS
from .apple_cms import AppleCMS
from .feifei_cms import FeifeiCMS
from .endpoint import detect_format


class HaiyangCMS(BaseCMS):
    """内容探测适配器 — 基于实际返回内容自动适配 JSON / XML 接口

    不再按 URL 子串猜类型（/xml、/api.php/provide/vod 等不可靠），
    而是拉一次 ?ac=videolist 看返回是 JSON 还是 XML。两个后端适配器
    （AppleCMS / FeifeiCMS）自己会用 resolve_endpoint() 决定请求地址。
    """

    def __init__(self, base_url: str, name: str = "") -> None:
        super().__init__(base_url, name)
        self._json_adapter = AppleCMS(base_url, name)
        self._xml_adapter = FeifeiCMS(base_url, name)
        self._is_json: bool | None = None

    def _detect_interface(self) -> bool:
        """基于内容探测接口类型（JSON / XML），结果按源地址模块级缓存。"""
        if self._is_json is None:
            self._is_json = detect_format(self.base_url) == "json"
        return self._is_json

    def fetch_classes(self) -> list[dict[str, Any]]:
        if self._detect_interface():
            return self._json_adapter.fetch_classes()
        else:
            return self._xml_adapter.fetch_classes()

    def fetch_videos(
        self, ac: str = "videolist", pg: int = 1, t: str = "", wd: str = ""
    ) -> dict[str, Any]:
        if self._detect_interface():
            return self._json_adapter.fetch_videos(ac, pg, t, wd)
        else:
            return self._xml_adapter.fetch_videos(ac, pg, t, wd)

    def fetch_detail(self, vod_id: str) -> dict[str, Any]:
        if self._detect_interface():
            return self._json_adapter.fetch_detail(vod_id)
        else:
            return self._xml_adapter.fetch_detail(vod_id)
