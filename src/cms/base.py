"""CMS 统一接口基类"""

from abc import ABC, abstractmethod
from typing import Any


class CMSNoSearchError(Exception):
    """源站明确返回「不支持搜索」类提示（HTTP 200 但 body 为纯文本提示而非 JSON/XML）。

    例如苹果 CMS 在搜索接口未开启时返回纯文本「暂不支持搜索」。此时不应当成
    JSON/XML 解析失败，而应视为「该源不支持搜索」，供检测功能关闭搜索开关、
    搜索时给出友好提示。
    """


# 源站「不支持搜索」类响应的特征串（命中即视为不支持搜索）
_NO_SEARCH_MARKERS = (
    "不支持搜索",
    "暂不支持搜索",
    "暂不支持",
    "未开放搜索",
    "未开启搜索",
    "禁止搜索",
    "搜索功能",
    "search not",
    "not support",
    "no search",
    "search disabled",
)


def looks_like_no_search(text: str) -> bool:
    """粗判一段响应文本是否为「源站不支持搜索」的提示。"""
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in _NO_SEARCH_MARKERS)


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
