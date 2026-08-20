"""飞飞 CMS (XML API) 适配器"""

from typing import Any
import requests as req
from lxml import etree
from .base import BaseCMS
from .endpoint import resolve_endpoint

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class FeifeiCMS(BaseCMS):
    """飞飞 CMS — XML 接口 /xml/ 或 /api.php"""

    def _request_xml(self, params: dict[str, Any]) -> etree._Element:
        """请求返回 XML Element

        端点由 resolve_endpoint() 决定：已是完整端点（/at/xml、/api/xml.php 等）
        则原样使用；仅当 resolve 判定为站点根（补了标准路径）时，才改用 /xml/。
        """
        base = self.base_url.rstrip("/")
        ep = resolve_endpoint(base)
        xml_url = ep if ep == base else (base + "/xml/")
        resp = req.get(
            xml_url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml"},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.text
        # 处理可能的 BOM
        if text.startswith("\ufeff"):
            text = text[1:]
        return etree.fromstring(text.encode("utf-8"))

    @staticmethod
    def _parse_class_element(el: etree._Element) -> dict[str, Any]:
        return {
            "type_id": el.get("id", el.findtext("id", "")),
            "type_name": el.get("name") or el.findtext("name") or (el.text or "").strip() or el.tag,
        }

    @staticmethod
    def _parse_play(el: etree._Element) -> tuple[str, str]:
        """解析播放地址。

        兼容两种 XML 结构：
        1. 苹果 CMS 风格：<dl><dd flag="线路">名称$地址#名称$地址</dd>...</dl>
           —— 每个 <dd> 是一项播放源（flag 即线路名），其文本含多集（# 分隔）。
        2. 飞飞 CMS 风格：<playfrom>线路1$$$线路2</playfrom><playurl>...$$$...</playurl>
        """
        dl = el.find("dl")
        if dl is not None:
            froms, urls = [], []
            for dd in dl.findall("dd"):
                flag = (dd.get("flag") or "").strip()
                block = (dd.text or "").strip()
                if not block:
                    continue
                froms.append(flag or ("线路" + str(len(froms) + 1)))
                urls.append(block)
            if froms:
                return "$$$".join(froms), "$$$".join(urls)

        def _text(tag: str) -> str:
            node = el.find(tag)
            return node.text.strip() if node is not None and node.text else ""

        return (_text("playfrom") or _text("from")), (_text("playurl") or _text("url"))

    def _parse_video_element(self, el: etree._Element) -> dict[str, Any]:
        def _text(tag: str) -> str:
            node = el.find(tag)
            return node.text.strip() if node is not None and node.text else ""

        play_from, play_url = self._parse_play(el)

        return {
            "vod_id": el.get("id", _text("id")),
            "vod_name": el.get("name", _text("name")),
            "vod_pic": el.get("pic", _text("pic")),
            "vod_year": _text("year"),
            "vod_area": _text("area"),
            "vod_lang": _text("lang"),
            "vod_type": _text("type"),
            "vod_actor": _text("actor"),
            "vod_director": _text("director"),
            "vod_content": _text("des"),
            "vod_blurb": _text("blurb"),
            "vod_play_from": play_from,
            "vod_play_url": play_url,
            "vod_hits": _text("hits"),
            "type_name": _text("type"),
        }

    def fetch_classes(self) -> list[dict[str, Any]]:
        root = self._request_xml({})
        # 飞飞 XML 中分类通常在 <class> 节点下
        class_node = root.find(".//class")
        if class_node is None:
            class_els = root.findall(".//ty")
        else:
            class_els = class_node.findall("ty")
        return [self._parse_class_element(el) for el in class_els]

    def fetch_videos(
        self, ac: str = "videolist", pg: int = 1, t: str = "", wd: str = ""
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"ac": ac, "pg": pg}
        if t:
            params["t"] = t
        if wd:
            params["wd"] = wd

        root = self._request_xml(params)

        video_els = root.findall(".//video")

        # 分页总数：优先读 <list pagecount="..."> 属性（苹果 CMS XML 标准格式），
        # 兼容飞飞 XML 的 <total> 子节点文本；都取不到再回退 1。
        pagecount = None
        list_el = root.find(".//list")
        if list_el is not None:
            pc = (
                list_el.get("pagecount")
                or list_el.get("totalpage")
                or list_el.get("total")
            )
            if pc:
                pagecount = pc
        if pagecount is None:
            pagecount = root.findtext(".//total")
        try:
            pagecount = int(pagecount) if pagecount else 1
        except (ValueError, TypeError):
            pagecount = 1

        return {
            "list": [self._parse_video_element(el) for el in video_els],
            "pagecount": pagecount,
        }

    def fetch_detail(self, vod_id: str) -> dict[str, Any]:
        root = self._request_xml({"ac": "detail", "ids": vod_id})
        video_el = root.find(".//video")
        if video_el is None:
            return {}
        return self._parse_video_element(video_el)
