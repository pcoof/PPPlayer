<h1 align="center">
  <img src="static/logo.svg" alt="ppplayer" width="300" />
  <br>
  <a href="https://github.com/pcoof/ppplayer/releases">PPPlayer 桌面版</a>
  <br>
</h1>
<div align="center">

[![](https://img.shields.io/github/v/release/pcoof/ppplayer?label=Release&logo=github)](/)
[![](https://img.shields.io/badge/license-MIT-blue.svg)]()
[![](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
</div>
<div align="center">

[![](https://raw.githubusercontent.com/CodePhiliaX/resource-trusteeship/main/readmex.svg)](https://readmex.com/pcoof/ppplayer)
[![](https://deepwiki.com/badge.svg)](https://deepwiki.com/pcoof/ppplayer)
[![](https://img.shields.io/badge/Ask_Zread-_.svg?style=flat&color=00b0aa&labelColor=000000&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTQuOTYxNTYgMS42MDAxSDIuMjQxNTZDMS44ODgxIDEuNjAwMSAxLjYwMTU2IDEuODg2NjQgMS42MDE1NiAyLjI0MDFWNC45NjAxQzEuNjAxNTYgNS4zMTM1NiAxLjg4ODEgNS42MDAxIDIuMjQxNTYgNS42MDAxSDQuOTYxNTZDNS4zMTUwMiA1LjYwMDEgNS42MDE1NiA1LjMxMzU2IDUuNjAxNTYgNC45NjAxVjIuMjQwMUM1LjYwMTU2IDEuODg2NjQgNS4zMTUwMiAxLjYwMDEgNC45NjE1NiAxLjYwMDFaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00Ljk2MTU2IDEwLjM5OTlIMi4yNDE1NkMxLjg4ODEgMTAuMzk5OSAxLjYwMTU2IDEwLjY4NjQgMS42MDE1NiAxMS4wMzk5VjEzLjc1OTlDMS42MDE1NiAxNC4xMTM0IDEuODg4MSAxNC4zOTk5IDIuMjQxNTYgMTQuMzk5OUg0Ljk2MTU2QzUuMzE1MDIgMTQuMzk5OSA1LjYwMTU2IDE0LjExMzQgNS42MDE1NiAxMy43NTk5VjExLjAzOTlDNS42MDE1NiAxMC42ODY0IDUuMzE1MDIgMTAuMzk5OSA0Ljk2MTU2IDEwLjM5OTlaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik0xMy43NTg0IDEuNjAwMUgxMS4wMzg0QzEwLjY4NSAxLjYwMDEgMTAuMzk4NCAxLjg4NjY0IDEwLjM5ODQgMi4yNDAxVjQuOTYwMUMxMC4zOTg0IDUuMzEzNTYgMTAuNjg1IDUuNjAwMSAxMS4wMzg0IDUuNjAwMUgxMy43NTg0QzE0LjExMTkgNS42MDAxIDE0LjM5ODQgNS4zMTM1NiAxNC4zOTg0IDQuOTYwMVYyLjI0MDFDMTQuMzk4NCAxLjg4NjY0IDE0LjExMTkgMS42MDAxIDEzLjc1ODQgMS42MDAxWiIgZmlsbD0iI2ZmZiIvPgo8cGF0aCBkPSJNNCAxMkwxMiA0TDQgMTJaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00IDEyTDEyIDQiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8L3N2Zz4K&logoColor=ffffff
)](https://zread.ai/pcoof/ppplayer)
[![](https://img.shields.io/badge/图表-_.svg?style=flat&color=00b0aa&labelColor=000000&logo=digitalocean)](https://gitdiagram.com/pcoof/PPPlayer)


</div>
# PPPlayer — pywebview 桌面版

> 本项目由 **AI 编写**，整体设计思路（架构、功能取舍、UI/交互方案）也来自 AI。它是对「用 Python + pywebview 做一个轻量桌面视频聚合播放器」这一想法的落地实现。

PPPlayer 是一款基于 **Python + pywebview** 的开源视频聚合播放桌面应用。它聚合多种影视 CMS API 接口（苹果CMS / 飞飞CMS / 海洋CMS 等），内置 M3U8 智能广告过滤与媒体嗅探，所有数据均保存在本地。

## 技术栈

- **Python 3.11+** + uv 包管理
- **pywebview** — 无边框桌面窗口容器（自定义标题栏、边缘缩放、Aero Snap）
- **Flask** — 内置 HTTP 服务器（`127.0.0.1:19527`，嵌入 pywebview）
- **前端** — 原生 CSS（单一设计系统 `style.css`，含语义化组件类 + 轻量工具类层 `u-*`）+ Alpine.js + HLS.js + XGPlayer（弹出播放页）

## 功能

- 多 CMS 源支持（苹果CMS JSON / 飞飞CMS XML / 海洋CMS 自动适配）
- 搜索、分类浏览、自动加载（滚动无限加载）/ 分页两种模式
- 收藏、播放历史、搜索历史
- 卡片标签自定义（四角 + 标题下方双行）
- CMS 源分类黑名单（按源独立配置）
- **源连接检测** — 设置「API 源」中可单源「检测连接」或「检测全部」，状态以彩色圆点（绿/红/灰）显示，检测中图标旋转
- 片头片尾跳过、自动连播、倍速记忆
- **M3U8 智能去广告**（默认开启）— 自动识别并剔除片头 / 片中广告切片（基于切片源目录签名差异与 `#EXT-X-DISCONTINUITY` 分段），只播放正片；关闭则原样加载
- 主题系统 — 浅色 / 深色 / 跟随系统，叠加 **九套视觉风格**（默认 / NFT / 像素风 / 赛博朋克 / 手绘插画 / 现实·超现实 / 简约 / 未来主义 / 波普艺术），独立组合
- 数据导入导出（JSON 格式，含收藏 / 历史 / 源 / 配置）
- **切源 / 切分类 / 搜索的毛玻璃等待层** — 整页重载时（切换 API 源、切换分类、搜索、首次加载）内容区与导航菜单叠加一层毛玻璃模糊 + 居中加载动画，弱响应期间给出明确反馈；该层被限制在主内容容器内，**绝不遮挡顶部标题栏**（窗口标题栏始终清晰可交互）。无限滚动「加载更多」为增量追加，不触发模糊层

## 🌴 注意事项

- 仅此Github发布，请勿上当受骗；请各管理者不要宣传及引流本软件。
- 强烈倡导合法观影，本软件仅作为播放工具，不涉及资源存储或分发。
- 仅供个人学习交流之用，24小时内请自觉卸载，勿作商业用途。
- 在开始使用前，请务必详读并同意用户协议，确保遵守相关规定。

## 安装

```bash
cd PPPlayer

# 安装依赖
uv sync
```

> 说明：仓库与本地目录名仍为 `PPPlayer`（GitHub 仓库未迁移），但应用显示名已统一为 **PPPlayer**。

## 运行

```bash
uv run main.py
```

首次启动会自动打开 pywebview 窗口（1280×800），标题为 "PPPlayer"。

## 构建与发布（Windows 单文件 exe）

本项目通过 GitHub Actions（`.github/workflows/build.yml`）自动构建并发布 **Windows 单文件 exe**。

流程（推送到 `main` 或手动触发 `Build & Release` 工作流即自动执行）：

1. `calc-version`：计算版本号 `YYYYMMDD.N`（检索当日最大 Tag 序号 +1）。
2. `build-package`：在 `windows-latest` 上用 `uv` + PyInstaller 产出单文件 exe（`--onefile --noconsole --icon=logo.ico`）；构建期把版本写回 `pyproject.toml` 的 `[project].version` 并随 exe 嵌入（源码不再写死版本号），再打包为便携 zip 上传 artifact。
3. `release`：生成 CHANGELOG、幂等创建 Git Tag、发布 GitHub Release 并上传全部产物。

产物：`ppplayer.exe`（单文件）与 `ppplayer-portable.zip`（便携包）。

本地手动构建（与 CI 等价）：

```bash
uv sync --frozen
uv run pyinstaller --onefile --noconsole --icon=logo.ico --name=ppplayer \
  --add-data "static;static" \
  --add-data "pyproject.toml;." \
  --hidden-import clr --hidden-import pythonnet \
  --hidden-import webview.platforms.edgechromium --hidden-import webview.platforms.winforms \
  --hidden-import src --hidden-import src.server --hidden-import src.config_store \
  --hidden-import src.parser.sniffer --hidden-import src.parser.m3u8_filter \
  --hidden-import src.cms --hidden-import src.cms.detector --hidden-import src.cms.base \
  --hidden-import src.cms.apple_cms --hidden-import src.cms.feifei_cms \
  --hidden-import src.cms.haiyang_cms --hidden-import src.cms.endpoint \
  --collect-all pythonnet \
  main.py --noconfirm --clean
```

## 项目结构

```
PPPlayer/
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
| `GET /api/hls?url=<url>` | 播放列表代理：后端拉取 + 智能去广告，并把内部所有 CDN 地址改写为同源代理；302 到 `.m3u8` 结尾地址供播放器识别为 HLS |
| `GET /api/media?url=<url>` | 媒体字节代理（切片 `.ts/.m4s`、密钥 `.key`、字幕 `.vtt` 等），转发 `Range` 请求支持拖动进度，同源返回规避跨域 |
| `GET /api/serve_m3u8/<sid>` | 返回已暂存的过滤后播放列表（`.m3u8` 结尾，供播放器识别为 HLS） |
| `GET /api/parse?url=<url>` | 媒体嗅探解析 |
| `GET /api/cms/classes?source=<url>` | 获取 CMS 分类 |
| `GET /api/cms/videos?source=<url>&ac=&pg=&t=&wd=` | 获取视频列表 |
| `GET /api/cms/detail?source=<url>&id=<id>` | 获取视频详情 |
| `GET /api/cms/check?source=<url>` | 检测 CMS 源连通性（设置中心「检测连接」调用） |
| `GET /player` | 弹出式竖滑播放页 |

## M3U8 智能去广告 + 同源代理

播放全程**只与本地 Flask 服务（127.0.0.1:19527）同源通信**，从根本上规避「CDN 不返回 `Access-Control-Allow-Origin` 头导致浏览器跨域失败」（典型表现：控制台报 CORS 错误、状态 200 但「无法加载响应数据」、或用 VLC 等能播但浏览器里黑屏）。

流程：

1. 前端把播放地址交给 `/api/hls?url=<编码地址>`（直链视频/切片则交给 `/api/media?url=`），**不再由浏览器直连 CDN**；
2. 后端 `m3u8_filter.py` 拉取播放列表、按 discontinuity 切段、以切片目录签名归并视频源、保留总时长最长的正片源、丢弃所有 discontinuity，输出干净的单一 VOD 列表；
3. 过滤后的列表里**所有 CDN 地址**（嵌套变体 → `/api/hls`、切片/密钥/字幕 → `/api/media`）被统一改写为本服务同源代理地址；
4. 浏览器只请求本服务，切片经 `/api/media` 转发（支持 `Range` 拖动），彻底无跨域。

> 智能去广告开关为设置中心 → 基础 → **智能去广告**（对应 `data/config.json` 中 `cms_cfg.smartAdRemove`，默认 `true`）。

> Flask 使用 `threaded=True` 并发处理「页面 + 播放列表 + 多切片」的并行请求，并对重复地址做 20s TTL 缓存，速度接近直连。

## 主题与风格

- 浅色 / 深色 / 跟随系统（设置中心 → 基础 → 主题）
- 九套视觉风格（设置中心 → 基础 → 主题风格），与明暗模式独立组合，由 `style.css` 中 `html[data-theme="..."]` 令牌覆盖实现

## 许可证

MIT
*（本项目由 AI 编写、设计思路亦来自 AI；内容仅供学习交流参考，请勿用于任何商业或侵权行为。）*
