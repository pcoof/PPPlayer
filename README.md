
# TSPlayer — pywebview 桌面版

pywebview 桌面应用。聚合播放器，支持苹果CMS / 飞飞CMS / 海洋CMS 等多种影视 CMS API 接口，内置 M3U8 智能广告过滤与媒体嗅探。

## 技术栈

- **Python 3.11+** + uv 包管理
- **pywebview** — 无边框桌面窗口容器（自定义标题栏、边缘缩放、Aero Snap）
- **Flask** — 内置 HTTP 服务器（`127.0.0.1:19527`，嵌入 pywebview）
- **前端** — 原生 CSS（单一设计系统 `style.css`，含语义化组件类 + 轻量工具类层 `u-*`，**无 Tailwind 构建**）+ Alpine.js + HLS.js + XGPlayer（弹出播放页）

## 功能

- 多 CMS 源支持（苹果CMS JSON / 飞飞CMS XML / 海洋CMS 自动适配）
- 搜索、分类浏览、自动加载（滚动无限加载）/ 分页两种模式
- 收藏、播放历史、搜索历史
- 卡片标签自定义（四角 + 标题下方双行）
- CMS 源分类黑名单（按源独立配置）
- **源连接检测** — 设置「API 源」中可单源「检测连接」或「检测全部」，状态以彩色圆点（绿/红/灰）显示，检测中图标旋转
- 片头片尾跳过、自动连播、倍速记忆
- **M3U8 智能去广告**（默认开启）— 自动识别并剔除片头 / 片中广告切片（基于切片源目录签名差异与 `#EXT-X-DISCONTINUITY` 分段），只播放正片；关闭则原样加载
- 媒体嗅探（DPlayer / ArtPlayer / CKplayer / JWPlayer / iframe 等）
- 主题系统 — 浅色 / 深色 / 跟随系统，叠加 **九套视觉风格**（默认 / NFT / 像素风 / 赛博朋克 / 手绘插画 / 现实·超现实 / 简约 / 未来主义 / 波普艺术），独立组合
- 数据导入导出（JSON 格式，含收藏 / 历史 / 源 / 配置）

## 安装

```bash
cd tsplayer-pywebview

# 安装依赖
uv sync
```

## 运行

```bash
uv run python main.py
```

首次启动会自动打开 pywebview 窗口（1280×800），标题为 "TSPlayer"。

## 项目结构

```
tsplayer-pywebview/
├── pyproject.toml           # uv 项目配置
├── main.py                  # 入口：启动 Flask + pywebview
├── README.md
├── data/
│   └── config.json          # 本地配置（源、收藏、历史、设置）
├── src/
│   ├── server.py            # Flask 应用，挂载所有路由
│   ├── proxy.py             # HTTP 代理转发（绕过跨域）
│   ├── cms/
│   │   ├── base.py          # CMS 抽象基类
│   │   ├── apple_cms.py     # 苹果CMS (JSON API)
│   │   ├── feifei_cms.py    # 飞飞CMS (XML API)
│   │   ├── haiyang_cms.py   # 海洋CMS (XML/JSON API)
│   │   └── detector.py      # CMS 类型自动检测
│   └── parser/
│       ├── sniffer.py       # 媒体嗅探器
│       └── m3u8_filter.py   # M3U8 智能广告过滤
└── static/
    ├── index.html           # 前端主页面（Alpine.js）
    ├── player.html          # 弹出播放页（XGPlayer 竖滑）
    ├── css/
    │   └── style.css        # 设计系统「纸上观影 / Paper & Reel」+ 工具类层
    └── js/
        ├── api.js           # API 调用封装
        ├── app.js           # 主应用逻辑 (Alpine.js)
        └── window-chrome.js # 无边框窗口拖拽 / 缩放 / Aero Snap
```

## API 路由

| 路由 | 说明 |
|---|---|
| `GET /` | 返回 index.html |
| `GET /static/<path>` | 静态文件 |
| `GET /api/proxy?u=<url>` | HTTP 代理转发（绕过跨域） |
| `GET /api/m3u8?url=<url>&skip=<n>` | M3U8 代理 + 广告过滤（浏览器 fetch 失败时的回退通道） |
| `POST /api/m3u8_prepared` | 接收远程 m3u8 文本，过滤后暂存并返回 `{"url":"/api/serve_m3u8/<sid>.m3u8"}` |
| `GET /api/serve_m3u8/<sid>` | 返回已过滤的播放列表（`.m3u8` 结尾，供播放器识别为 HLS） |
| `GET /api/parse?url=<url>` | 媒体嗅探解析 |
| `GET /api/cms/classes?source=<url>` | 获取 CMS 分类 |
| `GET /api/cms/videos?source=<url>&ac=&pg=&t=&wd=` | 获取视频列表 |
| `GET /api/cms/detail?source=<url>&id=<id>` | 获取视频详情 |
| `GET /api/cms/check?source=<url>` | 检测 CMS 源连通性（设置中心「检测连接」调用） |
| `GET /player` | 弹出式竖滑播放页 |

## M3U8 智能去广告

广告切片与正片通常是**两套独立的视频源**（切片 URI 目录、分辨率、码率、时长不同），以 `#EXT-X-DISCONTINUITY` 分隔。过滤流程：

1. 浏览器先 `fetch` 远程 m3u8（携带正确的 Referer / Cookie，规避 CDN 对服务端拉取的鉴权拒绝）；
2. 文本 `POST /api/m3u8_prepared`，后端 `m3u8_filter.py` 按 discontinuity 切段、以切片目录签名归并视频源、保留总时长最长的正片源、丢弃所有 discontinuity，输出干净的单一 VOD 列表；
3. 播放器加载返回的 `/api/serve_m3u8/<sid>.m3u8`（以 `.m3u8` 结尾，确保 XGPlayer / HLS.js 识别为 HLS）。

开关为设置中心 → 基础 → **智能去广告**（对应 `data/config.json` 中 `cms_cfg.smartAdRemove`，默认 `true`）。

## CMS 源配置

在设置面板「API 源」中添加。直接粘贴源提供的**完整接口地址**即可，系统会自动按返回内容识别 JSON / XML 类型并决定请求地址，**无需手动补 `/api.php/provide/vod/` 等路径**：

- 标准苹果 CMS：`https://example.com/api.php/provide/vod/`
- 完整端点（已含脚本/路径）：`https://example.com/api/json.php`、`https://example.com/api/xml.php`、`https://example.com/xxx/vod/json.html`、`https://example.com/xinlangapi.php/provide/vod` 等
- 纯站点根（不带接口路径）：`https://example.com` —— 系统会补标准苹果 CMS 路径探测

> 注意：识别基于实际返回内容（拉一次 `?ac=videolist` 看是 JSON 还是 XML），不再依赖 URL 子串猜测；站点根会自动尝试 `/api.php/provide/vod/` 与 `/xml/` 两个常见位置。

系统会自动检测 CMS 类型并适配对应接口；可在每个源上单独配置海报比例、片头片尾跳过与分类黑名单。

## 主题与风格

- 浅色 / 深色 / 跟随系统（设置中心 → 基础 → 主题）
- 九套视觉风格（设置中心 → 基础 → 主题风格），与明暗模式独立组合，由 `style.css` 中 `html[data-theme="..."]` 令牌覆盖实现

## 许可证

MIT
*（内容由AI生成，仅供参考）*
