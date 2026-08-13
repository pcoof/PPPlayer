"""Flask 应用 — 挂载所有路由"""

from flask import Flask, request, jsonify, send_from_directory
import requests as req
import os
import uuid

from .proxy import proxy_request
from .parser.sniffer import parse_play_url
from .parser.m3u8_filter import filter_m3u8, filter_m3u8_text
from .cms.detector import detect_cms
from .config_store import load_all, save_all

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

# 弹出窗口播放状态中转（pywebview 独立窗口 localStorage 不共享）
_popout_state = None

# 已过滤的 M3U8 文本暂存（key=sid, value=播放列表文本）。
# 浏览器先把远程播放列表拉回来，后端只做「过滤+暂存」，再以 .m3u8 结尾的 URL
# 提供给播放器（XGPlayer/HLS.js 按扩展名识别为 HLS）。不做远程拉取，避免 CDN 鉴权导致服务端拉取失败。
_m3u8_store: dict[str, str] = {}


def set_popout_state(state):
    """由 PlayerWindow 调用，存储弹出窗口的播放状态"""
    global _popout_state
    _popout_state = state


def get_popout_state():
    """返回并清除当前存储的弹出窗口播放状态"""
    global _popout_state
    state = _popout_state
    _popout_state = None
    return state


def create_app() -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

    # ── 页面路由 ─────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    # ── API: HTTP 代理转发 ───────────────────────────────────
    @app.route("/api/proxy")
    def api_proxy():
        return proxy_request()

    # ── API: M3U8 代理 + 广告过滤 ────────────────────────────
    @app.route("/api/m3u8")
    def api_m3u8():
        raw_url = request.args.get("url", "")
        skip_str = request.args.get("skip", "0")
        if not raw_url:
            return jsonify({"error": "Missing url parameter"}), 400

        try:
            skip = max(0, int(skip_str))
        except (ValueError, TypeError):
            skip = 0

        # 智能去广告开关（设置中心 -> 基础 -> 智能去广告），默认开启
        cfg = load_all()
        smart = (cfg.get("cms_cfg") or {}).get("smartAdRemove", True)

        try:
            text = filter_m3u8(raw_url, skip=skip, smart=smart)
            return text, 200, {
                "Content-Type": "application/vnd.apple.mpegurl; charset=utf-8",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            }
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # ── API: M3U8 预处理（浏览器已拉取，后端过滤+暂存，返回 .m3u8 服务地址）──
    @app.route("/api/m3u8_prepared", methods=["POST"])
    def api_m3u8_prepared():
        try:
            data = request.get_json(force=True) or {}
            text = data.get("text", "")
            base_url = data.get("base_url", "")
            smart = bool(data.get("smart", True))
            if not text:
                return jsonify({"error": "Missing text"}), 400
            cfg = load_all()
            if (cfg.get("cms_cfg") or {}).get("smartAdRemove", True) is False:
                smart = False
            out = filter_m3u8_text(text, base_url=base_url, smart=smart)
            sid = uuid.uuid4().hex
            _m3u8_store[sid] = out
            return jsonify({"url": "/api/serve_m3u8/" + sid + ".m3u8"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── API: 提供已暂存的过滤后播放列表（URL 以 .m3u8 结尾，播放器据此识别为 HLS）──
    @app.route("/api/serve_m3u8/<sid>")
    def api_serve_m3u8(sid: str):
        sid = sid.split(".")[0]          # 兼容 .m3u8 后缀
        text = _m3u8_store.get(sid)
        if text is None:
            return jsonify({"error": "not found"}), 404
        return text, 200, {
            "Content-Type": "application/vnd.apple.mpegurl; charset=utf-8",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        }

    # ── API: 媒体嗅探解析 ───────────────────────────────────
    @app.route("/api/parse")
    def api_parse():
        raw_url = request.args.get("url", "")
        if not raw_url:
            return jsonify({"error": "Missing url parameter"}), 400

        url = raw_url
        if not url.startswith("http"):
            url = "https://" + url

        try:
            result = parse_play_url(url)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "url": url}), 500

    # ── API: CMS 分类 ───────────────────────────────────────
    @app.route("/api/cms/classes")
    def api_cms_classes():
        source = request.args.get("source", "")
        if not source:
            return jsonify({"error": "Missing source parameter"}), 400

        try:
            cms = detect_cms(source)
            classes = cms.fetch_classes()
            return jsonify({"class": classes})
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # ── API: CMS 视频列表 ───────────────────────────────────
    @app.route("/api/cms/videos")
    def api_cms_videos():
        source = request.args.get("source", "")
        ac = request.args.get("ac", "videolist")
        pg = int(request.args.get("pg", "1"))
        t = request.args.get("t", "")
        wd = request.args.get("wd", "")

        if not source:
            return jsonify({"error": "Missing source parameter"}), 400

        try:
            cms = detect_cms(source)
            result = cms.fetch_videos(ac=ac, pg=pg, t=t, wd=wd)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # ── API: CMS 视频详情 ───────────────────────────────────
    @app.route("/api/cms/detail")
    def api_cms_detail():
        source = request.args.get("source", "")
        vod_id = request.args.get("id", "")

        if not source or not vod_id:
            return jsonify({"error": "Missing source or id parameter"}), 400

        try:
            cms = detect_cms(source)
            detail = cms.fetch_detail(vod_id)
            return jsonify(detail)
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # ── API: CMS 源检测 ─────────────────────────────────────
    @app.route("/api/cms/check")
    def api_cms_check():
        source = request.args.get("source", "")
        if not source:
            return jsonify({"error": "Missing source"}), 400
        try:
            cms = detect_cms(source)
            classes = cms.fetch_classes()
            return jsonify({"status": "ok", "class_count": len(classes) if classes else 0})
        except req.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            return jsonify({"status": "error", "code": status_code, "message": str(e)})
        except req.exceptions.ConnectionError:
            return jsonify({"status": "error", "code": 0, "message": "连接失败"})
        except req.exceptions.Timeout:
            return jsonify({"status": "error", "code": 0, "message": "连接超时"})
        except Exception as e:
            return jsonify({"status": "error", "code": 0, "message": str(e)})

    # ── 独立播放窗口页面 ──────────────────────────────────
    @app.route("/player")
    def player_page():
        return send_from_directory(STATIC_DIR, "player.html")

    # ── API: 弹出窗口播放状态中转 ─────────────────────────
    @app.route("/api/popout_state")
    def api_popout_state():
        """返回弹出窗口播放状态（JSON），消费后返回 404"""
        state = get_popout_state()
        if state:
            return jsonify(state)
        return jsonify(None), 404

    # ── API: 配置持久化 ────────────────────────────────────
    @app.route("/api/config/load", methods=["GET"])
    def api_config_load():
        """获取全部配置"""
        try:
            data = load_all()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/config/save", methods=["POST"])
    def api_config_save():
        """保存全部配置"""
        try:
            data = request.get_json(force=True) or {}
            save_all(data)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── 静态文件（兜底） ─────────────────────────────────────
    @app.route("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    return app
