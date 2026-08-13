"""CMS 统一接口基类"""

from abc import ABC, abstractmethod
from typing import Any


class BaseCMS(ABC):
    """CMS API 基类，定义统一接口。"""

    def __init__(self, base_url: str, name: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name

    @abstractmethod
    def fetch_classes(self) -> list[dict[str, Any]]:
        """获取分类列表

        返回格式：
        [{"type_id": "1", "type_name": "电影"}, ...]
        """
        ...

    @abstractmethod
    def fetch_videos(
        self, ac: str = "videolist", pg: int = 1, t: str = "", wd: str = ""
    ) -> dict[str, Any]:
        """获取视频列表

        参数：
        - ac: 动作（videolist / search）
        - pg: 页码
        - t: 分类 ID
        - wd: 搜索关键词

        返回格式：
        {
            "list": [{"vod_id": "1", "vod_name": "片名", ...}, ...],
            "pagecount": 10
        }
        """
        ...

    @abstractmethod
    def fetch_detail(self, vod_id: str) -> dict[str, Any]:
        """获取视频详情

        返回格式：
        {
            "vod_id": "1",
            "vod_name": "片名",
            "vod_play_from": "线路1$$$线路2",
            "vod_play_url": "第1集$url#第2集$url$$$第1集$url",
            ...
        }
        """
        ...
