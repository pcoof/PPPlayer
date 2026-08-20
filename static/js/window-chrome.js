// window-chrome.js — 无边框窗口 拖拽 / 边缘缩放 / 全屏状态同步
// 设计：坐标运算全部在 Python 端完成（见 main.py 的 JsApi），JS 只负责把鼠标 screenX/screenY 转发。
// 这样最稳定：无坐标系误差、无 IPC 节流死锁，且不受 frameless 下原生 WM_NCLBUTTONDOWN 失效影响
// （pywebview 的 frameless 把 FormBorderStyle 设为 None，连标题栏区域都移除，HTCAPTION/HTLEFT 等命中码全部 no-op）。
//
// 行为对齐 Win 原生标题栏：
//  - 标题栏拖拽 → 移动窗口；拖到屏幕顶部→最大化、左/右边缘→贴半屏（Aero Snap，判定在 Python 端 drag_to）；
//  - 八向边缘热区 → 缩放（最大化/全屏时禁止缩放，与系统一致）；
//  - 双击标题栏 / 窗口「最大化」按钮 → 最大化⇄还原（由 titleBarDblClick / winMax 经 WindowChrome.toggleMaximize 触发）；
//  - 全屏按钮 / 双击视频区 → 全屏⇄退出（由 winFullscreen 经 WindowChrome.toggleFullscreen 触发）；
//  - 全屏时自动隐藏标题栏与边缘热区，退出时恢复（通过 body.is-fullscreen class 驱动 CSS）。
(function () {
  'use strict';

  var drag = null;
  var maximized = false;   // 与 Python 端 _is_maximized 对齐（per-window）
  var fullscreen = false;  // 与 Python 端 _is_fullscreen 对齐（per-window）
  var rafId = null;
  var lastEv = null;

  function api() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  // —— 运行环境判定：桌面壳(pywebview/WebView2) vs 普通浏览器 ——
  // WebView2 会在页面里同步注入 window.chrome.webview（比 window.pywebview 更早可用），
  // 因此用它做首帧判定，避免「浏览器里先闪出一排最小化/最大化/关闭按钮」。
  function isDesktop() {
    return !!((window.chrome && window.chrome.webview) || window.pywebview);
  }

  function markEnv() {
    var el = document.documentElement;
    var desktop = isDesktop();
    el.classList.toggle('is-desktop', desktop);
    el.classList.toggle('is-web', !desktop);
  }

  markEnv();
  // pywebview 注入 api 是异步的：就绪后再确认一次环境标记
  window.addEventListener('pywebviewready', markEnv);

  // —— 浏览器下的原生 DOM 全屏兜底 ——
  function fsElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function domToggleFullscreen() {
    try {
      if (!fsElement()) {
        var el = document.documentElement;
        var req = el.requestFullscreen || el.webkitRequestFullscreen;
        if (req) {
          var p = req.call(el);
          if (p && p.catch) p.catch(function () {});
        }
      } else {
        var exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (exit) {
          var q = exit.call(document);
          if (q && q.catch) q.catch(function () {});
        }
      }
    } catch (e) {}
  }

  // 浏览器里按 ESC / F11 退出全屏也要同步 body.is-fullscreen（否则标题栏一直隐藏）
  ['fullscreenchange', 'webkitfullscreenchange'].forEach(function (evt) {
    document.addEventListener(evt, function () {
      if (!api()) {
        fullscreen = !!fsElement();
        window.WindowChrome.fullscreen = fullscreen;
        applyFullscreenClass(fullscreen);
        if (typeof window.WindowChrome.onStateChange === 'function') {
          try { window.WindowChrome.onStateChange({ maximized: maximized, fullscreen: fullscreen }); } catch (e) {}
        }
      }
    });
  });

  // 同步全屏状态到 DOM：添加/移除 body.is-fullscreen，CSS 据此隐藏标题栏与边缘热区
  function applyFullscreenClass(v) {
    if (v) {
      document.body.classList.add('is-fullscreen');
    } else {
      document.body.classList.remove('is-fullscreen');
    }
  }

  // 用 rAF 合并同帧的 mousemove，每帧最多发一次 IPC
  function frame() {
    rafId = null;
    if (drag && lastEv) {
      var a = api();
      if (a) {
        if (drag.type === 'move') {
          if (a.drag_to) a.drag_to(lastEv.screenX, lastEv.screenY);
        } else {
          if (a.resize_to) a.resize_to(lastEv.screenX, lastEv.screenY);
        }
      }
    }
  }

  function onMove(e) {
    if (!drag) return;
    e.preventDefault();
    lastEv = e;
    if (rafId === null) rafId = requestAnimationFrame(frame);
  }

  function onUp() {
    drag = null;
    lastEv = null;
    var a = api();
    if (a && a.drag_end) {
      // drag_end 返回 {maximized, changed}：只有当「吸附动作真正改变了最大化态」(changed=true)
      // 时前端才同步。普通单击/双击不会产生 changed，从而不会用旧状态覆盖
      // toggleMaximize 刚刚设定的结果（否则会出现「点两次才生效 / 双击最大化后还原要两次」）。
      var p = a.drag_end();
      function apply(r) {
        if (r && r.changed && window.WindowChrome) window.WindowChrome.setMaximized(r.maximized);
      }
      if (p && typeof p.then === 'function') {
        p.then(apply);
      } else {
        apply(p);
      }
    }
    document.removeEventListener('mousemove', onMove, true);
    document.removeEventListener('mouseup', onUp, true);
    document.body.style.userSelect = '';
  }

  function begin(e, type, hit) {
    if (e.button !== 0) return;
    var a = api();
    if (!a) return;
    // 最大化或全屏时禁止边缘缩放（与系统标题栏一致）
    if (type === 'resize' && (maximized || fullscreen)) return;
    e.preventDefault();
    if (type === 'resize') {
      if (a.resize_start) a.resize_start(hit, e.screenX, e.screenY);
      drag = { type: 'resize', hit: hit };
    } else {
      if (a.drag_start) a.drag_start(e.screenX, e.screenY);
      drag = { type: 'move' };
    }
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);
  }

  window.WindowChrome = {
    maximized: false,
    fullscreen: false,
    setMaximized: function (v) {
      maximized = !!v;
      this.maximized = maximized;
      // 通知外部（如 player.html 的最大化按钮图标切换）
      if (typeof this.onStateChange === 'function') {
        try { this.onStateChange({ maximized: maximized, fullscreen: fullscreen }); } catch (e) {}
      }
    },
    setFullscreen: function (v) {
      fullscreen = !!v;
      this.fullscreen = fullscreen;
      applyFullscreenClass(fullscreen);
      if (typeof this.onStateChange === 'function') {
        try { this.onStateChange({ maximized: maximized, fullscreen: fullscreen }); } catch (e) {}
      }
    },
    // 供标题栏双击 / 窗口「最大化」按钮调用。把「目标态」(=当前态取反) 传给 Python，
    // 并以 Python 返回的「切换后真实最大化态」为准更新本地标志，避免 JS/Python 错位
    // （这正是一度「点两次才生效」的根因：JS 乐观置位被后续异步回调覆盖）。
    toggleMaximize: function () {
      var a = api();
      var fallback = !maximized;   // IPC 无返回时的兜底值
      function apply(v) {
        var m = (typeof v === 'boolean') ? v : fallback;
        window.WindowChrome.setMaximized(m);
      }
      if (a && a.toggle_maximize_window) {
        var next = !maximized;
        var p = a.toggle_maximize_window(next);   // Python 返回切换后的真实态
        if (p && typeof p.then === 'function') p.then(apply);
        else apply(p);
      } else if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_maximize_window) {
        window.pywebview.api.toggle_maximize_window();
      }
    },
    // 全屏切换：调用 Python 端 toggle_fullscreen，以返回的 {maximized, fullscreen} 为准同步本地状态。
    toggleFullscreen: function () {
      var a = api();
      function apply(r) {
        if (r && typeof r === 'object') {
          if (typeof r.fullscreen === 'boolean') window.WindowChrome.setFullscreen(r.fullscreen);
          if (typeof r.maximized === 'boolean') window.WindowChrome.setMaximized(r.maximized);
        } else {
          // 无返回时兜底：翻转本地全屏态
          window.WindowChrome.setFullscreen(!fullscreen);
        }
      }
      if (a && a.toggle_fullscreen) {
        var p = a.toggle_fullscreen();
        if (p && typeof p.then === 'function') p.then(apply);
        else apply(p);
        return;
      }
      // 普通浏览器（无 pywebview）：退回标准 DOM 全屏 API。
      // 这是「浏览器打开播放器双击全屏失效」的根因——window.WindowChrome 在浏览器里
      // 同样存在，旧代码走到这里发现拿不到 api 就静默返回，什么也没做。
      domToggleFullscreen();
    },
    // 从 Python 端查询当前窗口状态并同步（页面加载后调用一次，避免刷新后状态丢失）
    syncState: function () {
      var a = api();
      if (a && a.get_window_state) {
        var p = a.get_window_state();
        function apply(r) {
          if (r) {
            if (typeof r.maximized === 'boolean') window.WindowChrome.setMaximized(r.maximized);
            if (typeof r.fullscreen === 'boolean') window.WindowChrome.setFullscreen(r.fullscreen);
          }
        }
        if (p && typeof p.then === 'function') p.then(apply);
        else apply(p);
      }
    },
    titlebarMouseDown: function (e) { begin(e, 'move', 2); },
    startTitleDrag: function (e) { begin(e, 'move', 2); },
    startEdgeDrag: function (e, hit) { begin(e, 'resize', hit); }
  };

  function bindEdges() {
    document.querySelectorAll('.win-edge').forEach(function (z) {
      z.addEventListener('mousedown', function (e) {
        var hit = parseInt(z.dataset.hit, 10);
        if (window.WindowChrome) window.WindowChrome.startEdgeDrag(e, hit);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindEdges);
  } else {
    bindEdges();
  }

  // 页面加载后同步一次窗口状态（防止刷新后最大化/全屏态丢失）
  if (document.readyState === 'complete') {
    setTimeout(function () { if (window.WindowChrome) window.WindowChrome.syncState(); }, 100);
  } else {
    window.addEventListener('load', function () {
      setTimeout(function () { if (window.WindowChrome) window.WindowChrome.syncState(); }, 100);
    });
  }
})();
