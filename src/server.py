"""Flask 应用 — 挂载所有路由"""

from flask import Flask, request, jsonify, send_from_directory, Response, redirect
from urllib.parse import urlparse
import requests as req
from urllib3.util.retry import Retry
import os
import sys
import uuid

from .parser.sniffer import parse_play_url
from .parser.m3u8_filter import filter_m3u8, filter_m3u8_text, USER_AGENT
from .cms.detector import detect_cms
from .cms.base import CMSNoSearchError
from .cms.http import _TlsHappyEyeballsAdapter
from .config_store import load_all, save_all


def _is_cloudflare_challenge(body: str) -> bool:
    """粗判响应体是否为 Cloudflare 之类的「浏览器验证」拦截页。"""
    if not body:
        return False
    low = body[:3000].lower()
    return any(m in low for m in (
        "just a moment", "checking your browser", "cf-chl", "_cf_chl_",
        "challenge-platform", "verify you are human",
        "enable javascript and cookies to continue",
    ))


def _friendly_source_error(status: int, is_search: bool, source: str, body: str = "", non_ascii_wd: bool = False) -> str:
    """把源站返回的 HTTP 错误码翻译成友好中文提示。

    - body 若命中 Cloudflare 验证墙，直接点明「Python 后端无法通过，需浏览器代理」。
    - is_search=True 时（搜索场景）需区分两种截然不同的失败：
      * 搜索词含中文/非 ASCII 字符 → 多半是源站 WAF 按 URL 编码拦截（仅放行 ASCII 关键词），
        与「接口不支持搜索」是两回事，必须给出准确提示，避免误导用户去改源配置；
      * 纯 ASCII 搜索词仍失败 → 才可能是接口本身不支持搜索。
    """
    if status in (403, 503) and _is_cloudflare_challenge(body):
        return ("该 API 源由 Cloudflare 防护（浏览器会提示「需要验证 / Just a moment」），"
                "Python 后端无法自动通过浏览器验证。解决方式二选一："
                "① 改用未被 Cloudflare 拦截的源；"
                "② 安装 playwright 并在本机保留 Edge/Chrome 后开启「浏览器代理」模式（自动回退）。")
    # 搜索词含非 ASCII（中文等）且源站报错：源站 WAF 按 URL 编码拦截，与「接口不支持搜索」无关
    if is_search and non_ascii_wd and status in (401, 403, 404, 405, 500, 501, 502, 503):
        return ("该 API 源返回 %d：搜索词含中文/非 ASCII 字符时，源站 WAF（防盗链）会直接拦截"
                "（仅放行英文/数字等 ASCII 关键词）。这是源站侧限制，代理层无法绕过；"
                "可改用英文/数字关键词搜索，或在该源官网/浏览器内直接搜索。") % status
    msg = {
        401: "源站返回 401 未授权：该接口可能需要签名/密钥或登录态。",
        403: "源站返回 403 Forbidden：接口禁止访问，常见原因是防盗链/WAF 限制。",
        404: "源站返回 404：接口地址不存在，请检查源地址是否正确（是否少了 /api.php/provide/vod/ 之类路径）。",
        405: "源站返回 405：该接口不支持当前请求方法。",
        429: "源站返回 429 请求过于频繁：已被限流，请稍后再试。",
        500: "源站返回 500 内部错误。",
        502: "源站返回 502 网关错误（上游不可用）。",
        503: "源站返回 503 服务不可用。",
    }.get(status, "源站返回 %d 错误。" % status)
    if is_search and status in (401, 403, 404, 405, 500, 501, 502, 503):
        msg += "该 API 源接口本身可能不支持搜索（或不允许外部搜索）。"
    return msg

# 静态资源目录：
# - 开发态：项目根 /static
# - 冻结态：PyInstaller 把 static 打进 sys._MEIPASS（onefile 临时解包 / onedir 程序目录），从那里读取。
if getattr(sys, "frozen", False):
    _BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(_BASE, "static")

# ── 共享 HTTP 连接池（核心优化）───────────────────────────
# 以往每个 .ts 切片都用 req.get() 新建连接 → 每片都要重新 DNS+TCP+TLS 握手，
# 同主机（同一 CDN）却无法复用 keep-alive，导致 HLS 预取永远慢半拍、播放卡顿。
# 用带连接池的 Session：同一 CDN 主机的切片复用长连接，消除每次握手开销；
# 配合重试适配器，偶发抖动自动恢复，不再因单片失败而阻塞整条缓冲链。
_MEDIA_SESSION = req.Session()
# 与 cms_session 一致：绕开系统代理/VPN/爬虫工具，直连源站（详见 src/cms/http.py 注释）。
# 代理若在系统层做 MITM 重签，会让 .ts 切片 TLS 失败、播放卡顿；直连则跟浏览器行为一致。
_MEDIA_SESSION.trust_env = False
_MEDIA_ADAPTER = _TlsHappyEyeballsAdapter(
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
        try:
            pg = int(request.args.get("pg", "1"))
        except ValueError:
            pg = 1
        t = request.args.get("t", "")
        wd = request.args.get("wd", "")

        if not source:
            return jsonify({"ok": False, "error": "缺少 source 参数", "code": "BAD_REQUEST"}), 400

        is_search = bool(wd)
        # 搜索词含非 ASCII（中文等）：用于区分「源站 WAF 拦截中文搜索」与「接口真不支持搜索」
        non_ascii_wd = any(ord(c) > 127 for c in wd)
        try:
            cms = detect_cms(source)
            result = cms.fetch_videos(ac=ac, pg=pg, t=t, wd=wd)
            result.setdefault("ok", True)
            return jsonify(result)
        except req.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            body = (e.response.text if e.response is not None else "") or ""
            return jsonify({
                "ok": False,
                "error": _friendly_source_error(status, is_search, source, body, non_ascii_wd),
                "code": "SOURCE_HTTP_%d" % status,
                "status": status,
            }), 502
        except req.exceptions.ConnectionError:
            return jsonify({
                "ok": False,
                "error": "连接失败：无法连接到该 API 源（域名解析失败 / IP 不可达 / 被网络拦截）。",
                "code": "CONNECTION_ERROR",
            }), 502
        except req.exceptions.Timeout:
            return jsonify({
                "ok": False,
                "error": "请求超时：该 API 源响应过慢。",
                "code": "TIMEOUT",
            }), 504
        except CMSNoSearchError as e:
            # 源站明确返回「不支持搜索」（HTTP 200 但纯文本提示，非 JSON）—— 该源本身
            # 未开放搜索接口。返回非 2xx 让前端 api.js 走错误分支显示友好提示。
            return jsonify({
                "ok": False,
                "error": "该 API 源不支持搜索（源站返回：%s）" % str(e).strip(),
                "code": "SOURCE_NO_SEARCH",
            }), 502
        except ValueError as e:
            # 多为 JSON 解析失败（源站返回了非 JSON/HTML 错误页）
            msg = "该 API 源返回内容无法解析为列表数据"
            if is_search and not non_ascii_wd:
                msg += "；若持续失败，可能是该源接口本身不支持搜索（或不允许外部搜索）。"
            elif is_search and non_ascii_wd:
                msg += "；搜索词含中文/非 ASCII 时源站 WAF 常直接返回错误页，可改用英文/数字关键词。"
            return jsonify({
                "ok": False,
                "error": msg + "：" + str(e)[:200],
                "code": "PARSE_ERROR",
            }), 502
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": "获取视频列表时发生未知错误：" + str(e)[:200],
                "code": "UNKNOWN",
            }), 502

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

    # ── API: CMS 源检测（连通性 + 搜索探测，自动判断搜索开关）──
    @app.route("/api/cms/check")
    def api_cms_check():
        source = request.args.get("source", "")
        if not source:
            return jsonify({"error": "Missing source"}), 400
        try:
            cms = detect_cms(source)
            # 1) 连通性检测：拉分类列表
            classes = cms.fetch_classes()
            class_count = len(classes) if classes else 0

            # 2) 搜索探测：用 wd=00 实际请求一次，自动判断「搜索开关」
            #    与线上搜索逻辑一致（ac=videolist + wd），能正常返回即支持搜索；
            #    明确报「方法不存在 / 接口不支持」才判为不支持；网络/防火墙类失败则留空（未知）。
            support_search = None
            search_note = ""
            try:
                sres = cms.fetch_videos(ac="videolist", pg=1, wd="00")
                # 能解析出 list 字段即视为支持搜索（哪怕结果为空）
                if isinstance(sres, dict) and "list" in sres:
                    support_search = True
                else:
                    support_search = False
                    search_note = "搜索接口未返回预期的数据结构"
            except req.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                body = (e.response.text if e.response is not None else "") or ""
                low = body.lower()
                # 明确的「不支持搜索」信号
                if status in (404, 405, 410) or ("方法不存在" in body) or ("不存在" in body) \
                        or ("not exist" in low) or ("unknown method" in low):
                    support_search = False
                    search_note = "搜索接口返回「方法不存在」，判定不支持搜索"
                else:
                    # 其它 HTTP 错误（403/500/502 等）→ 多为网络/防火墙，无法判定，保持未知
                    support_search = None
                    search_note = "搜索探测受 HTTP %d 影响，无法判定是否支持" % status
            except (req.exceptions.ConnectionError, req.exceptions.Timeout) as e:
                support_search = None
                search_note = "搜索探测网络异常，无法判定"
            except CMSNoSearchError as e:
                # 源站明确返回「不支持搜索」纯文本（如 sdzyapi.com 返回「暂不支持搜索」）
                support_search = False
                search_note = "源站返回「不支持搜索」，判定不支持搜索"
            except ValueError as e:
                # JSON/XML 解析失败：多半不是标准搜索接口
                support_search = False
                search_note = "搜索接口返回非标准数据，判定不支持"
            except Exception as e:
                support_search = None
                search_note = "搜索探测失败：" + str(e)[:120]

            return jsonify({
                "status": "ok",
                "class_count": class_count,
                "support_search": support_search,   # true / false / null(未知)
                "search_note": search_note,
            })
        except req.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            return jsonify({"status": "error", "code": status_code, "message": str(e)})
        except req.exceptions.ConnectionError as e:
            return jsonify({"status": "error", "code": 0, "message": "连接失败：" + str(e)})
        except OSError as e:  # DNS 解析失败等系统层错误（requests 未统一包装）
            return jsonify({"status": "error", "code": 0, "message": "连接/解析失败：" + str(e)})
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

    @app.route("/api/version")
    def api_version():
        """返回应用版本与 GitHub 仓库信息，供「关于」页展示（值由 main.py 在启动时注入 app.config）。"""
        return jsonify({
            "version": app.config.get("APP_VERSION", "unknown"),
            "app": "PPPlayer",
            "repo": app.config.get("GITHUB_REPO", ""),
            "repo_url": app.config.get("GITHUB_REPO_URL", ""),
            "releases_url": app.config.get("GITHUB_RELEASES_URL", ""),
        })

    # ── 静态文件（兜底） ─────────────────────────────────────
    @app.route("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    return app
