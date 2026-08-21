# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# ── pythonnet / clr ─────────────────────────────────────────────
# 项目在 Windows 上通过 .NET (System.Windows.Forms) 实现托盘/无边框窗体控制，
# 依赖 pythonnet。它含有 Python.Runtime 本地 dll，必须整包收集，否则冻结后
# 运行会报 “Failed to load Python.Runtime.dll”。
pythonnet_datas, pythonnet_bins, pythonnet_hidden = collect_all('pythonnet')

# ── 需要随包带出的资源目录 ────────────────────────────────────
# static 是前端（Alpine + XGPlayer 等），必须一起打包；
# data/ 是运行时生成的用户配置（已在 .gitignore），不打包，由程序运行时自建。
added_datas = [
    ('static', 'static'),
]
added_datas += pythonnet_datas

added_bins = list(pythonnet_bins)

# ── 隐藏导入 ──────────────────────────────────────────────────
# pywebview 的平台后端、src 业务包均为「按需/相对」导入，需显式声明，
# 否则冻结后启动即缺模块。
hiddenimports = [
    'clr',
    'pythonnet',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'src',
    'src.server',
    'src.config_store',
    'src.parser.sniffer',
    'src.parser.m3u8_filter',
    'src.cms',
    'src.cms.detector',
    'src.cms.base',
    'src.cms.apple_cms',
    'src.cms.feifei_cms',
    'src.cms.haiyang_cms',
    'src.cms.endpoint',
] + pythonnet_hidden

a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath('.')],
    binaries=added_bins,
    datas=added_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TSPlayer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 在 CI 环境中通常不存在，关闭以避免报错（本地有 UPX 可改 True）。
    upx=False,
    # 纯 GUI 应用，不弹控制台；如需排错可临时改 True。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ── 采用 onedir（文件夹）而非 onefile ──────────────────────────
# 原因：config_store / server 都以 __file__ 定位 data/ 与 static/，
# onefile 会把脚本解包到临时目录且退出即删，导致配置每次启动都重置。
# onedir 下 dist/TSPlayer/ 常驻，data/ 与 static/ 均稳定可读写。
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TSPlayer',
)
