"""Flask 应用 — 挂载所有路由"""

from flask import Flask, request, jsonify, send_from_directory, Response, redirect
from urllib.parse import urlparse
import requests as req
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
import uuid

from .parser.sniffer import parse_play_url
from .parser.m3u8_filter import filter_m3u8, filter_m3u8_text, USER_AGENT
from .cms.detector import detect_cms
from .config_store import load_all, save_all

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

# ── 共享 HTTP 连接池（核心优化）───────────────────────────
# 以往每个 .ts 切片都用 req.get() 新建连接 → 每片都要重新 DNS+TCP+TLS 握手，
# 同主机（同一 CDN）却无法复用 keep-alive，导致 HLS 预取永远慢半拍、播放卡顿。
# 用带连接池的 Session：同一 CDN 主机的切片复用长连接，消除每次握手开销；
# 配合重试适配器，偶发抖动自动恢复，不再因单片失败而阻塞整条缓冲链。
_MEDIA_SESSION = req.Session()
_MEDIA_ADAPTER = HTTPAdapter(
    pool_connections=40,
    pool_maxsize=40,
    max_retries=Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    ),
)
_MEDIA_SESSION.mount("http://", _MEDIA_ADAPTER)
_MEDIA_SESSION.mount("https://", _MEDIA_ADAPTER)

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

    # ── API: 播放列表代理（后端拉取 + 智能去广告 + 内部 URL 改写为本服务代理）──
    # 浏览器只与本服务同源通信（规避 CDN 无 CORS 头导致的跨域失败）；
    # 302 到 .m3u8 结尾地址，让播放器据此识别为 HLS。嵌套的变体/切片均改写为 /api/hls、/api/media。
    @app.route("/api/hls")
    def api_hls():
        raw_url = request.args.get("url", "")
        if not raw_url:
            return jsonify({"error": "Missing url parameter"}), 400
        cfg = load_all()
        smart = (cfg.get("cms_cfg") or {}).get("smartAdRemove", True)
        try:
            text = filter_m3u8(raw_url, skip=0, smart=smart)
            sid = uuid.uuid4().hex
            _m3u8_store[sid] = text
            if len(_m3u8_store) > 500:   # 会话级暂存，防止无限增长
                _m3u8_store.clear()
            return redirect("/api/serve_m3u8/" + sid + ".m3u8", code=302)
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # ── API: 媒体字节代理（切片 .ts/.m4s、密钥 .key、字幕 .vtt 等）──
    # 转发 Range 请求以支持拖动进度；同源返回并带 ACAO:*，彻底消除 TS 切片的跨域失败。
    # 关键：复用模块级连接池 Session（同一 CDN 长连接复用，消除每片 TLS 握手），流式转发。
    @app.route("/api/media")
    def api_media():
        raw_url = request.args.get("url", "")
        if not raw_url:
            return jsonify({"error": "Missing url parameter"}), 400
        url = raw_url if raw_url.startswith("http") else "https:" + raw_url
        try:
            host = urlparse(url).hostname or ""
            origin = f"{urlparse(url).scheme}://{host}"
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": origin,
                "Origin": origin,
            }
            rng = request.headers.get("Range")
            if rng:
                headers["Range"] = rng
            # 复用连接池：同一 CDN 主机自动 keep-alive，避免每片重新握手
            resp = _MEDIA_SESSION.get(
                url, headers=headers, timeout=(8, 60),
                allow_redirects=True, stream=True,
            )
            ct = resp.headers.get("Content-Type") or "application/octet-stream"
            out_headers = {
                "Content-Type": ct,
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=300",
                "Accept-Ranges": "bytes",
            }
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range")
                if cr:
                    out_headers["Content-Range"] = cr
                cl = resp.headers.get("Content-Length")
                if cl:
                    out_headers["Content-Length"] = cl
                else:
                    out_headers.pop("Content-Length", None)
            else:
                cl = resp.headers.get("Content-Length")
                if cl:
                    out_headers["Content-Length"] = cl
                else:
                    out_headers.pop("Content-Length", None)
            def gen():
                try:
                    # 256KB 分块，减少 yield 次数、降低 per-chunk 调度开销
                    for chunk in resp.iter_content(262144):
                        if chunk:
                            yield chunk
                finally:
                    try:
                        resp.close()   # 归还连接到池中，供下一切片复用
                    except Exception:
                        pass
            return Response(gen(), status=resp.status_code, headers=out_headers)
        except Exception as e:
            return str(e), 502

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
