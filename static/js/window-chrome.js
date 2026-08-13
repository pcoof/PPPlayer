// window-chrome.js — 无边框窗口 拖拽 / 边缘缩放
// 设计：坐标运算全部在 Python 端完成（见 main.py 的 JsApi），JS 只负责把鼠标 screenX/screenY 转发。
// 这样最稳定：无坐标系误差、无 IPC 节流死锁，且不受 frameless 下原生 WM_NCLBUTTONDOWN 失效影响
// （pywebview 的 frameless 把 FormBorderStyle 设为 None，连标题栏区域都移除，HTCAPTION/HTLEFT 等命中码全部 no-op）。
//
// 行为对齐 Win 原生标题栏：
//  - 标题栏拖拽 → 移动窗口；拖到屏幕顶部→最大化、左/右边缘→贴半屏（Aero Snap，判定在 Python 端 drag_to）；
//  - 八向边缘热区 → 缩放（最大化时禁止缩放，与系统一致）；
//  - 双击标题栏 / 窗口「最大化」按钮 → 最大化⇄还原（由 titleBarDblClick / winMax 经 WindowChrome.toggleMaximize 触发）。
(function () {
  'use strict';

  var drag = null;
  var maximized = false;   // 与 Python 端 _is_maximized 对齐（per-window）
  var rafId = null;
  var lastEv = null;

  function api() {
    return (window.pywebview && window.pywebview.api) || null;
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
    if (type === 'resize' && maximized) return; // 最大化时禁止边缘缩放（与系统标题栏一致）
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
    setMaximized: function (v) { maximized = !!v; this.maximized = maximized; },
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
})();
