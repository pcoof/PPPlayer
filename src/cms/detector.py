"""CMS 类型自动检测器

不再按 URL 子串（/xml、/api.php/provide/vod）猜测类型 —— 那种方式不可靠
（例如 /api/xml.php 不含 /xml 却被当 JSON、/at/xml 含 /provide/vod 却被当苹果）。

改为统一返回内容探测适配器 HaiyangCMS：它会在首次请求时拉一次
?ac=videolist，按实际返回内容（JSON / XML）选择对应的解析后端，
请求地址也由 endpoint.resolve_endpoint() 智能决定（只在站点根补标准路径）。
"""

from .base import BaseCMS
from .haiyang_cms import HaiyangCMS


def detect_cms(base_url: str, name: str = "") -> BaseCMS:
    """返回内容探测适配器。类型与端点均由实际内容/结构决定。"""
    return HaiyangCMS(base_url, name)
