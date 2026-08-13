/**
 * TSPlayer 播放器控制模块
 * 封装原生 Video 播放器 & ArtPlayer & HLS.js
 */

/**
 * 智能去广告核心：先把 m3u8 文本取回来，再交给后端过滤，并以 .m3u8 结尾的地址回放。
 * 为什么这样做：
 *   - 用浏览器直接 fetch 播放列表（带正确的 Referer/Cookie，CDN 通常只允许浏览器访问），
 *     避免「后端远程拉取被 CDN 拒」导致静默回退直连、广告照播。
 *   - 后端只做过滤 + 暂存，返回一个以 .m3u8 结尾的本地地址；播放器（HLS.js / XGPlayer）
 *     据此识别为 HLS 并来拉取，切片地址已是被后端绝对化后的 CDN 地址，浏览器可直接取。
 * 任何一步失败都尽量回退到原始链接（最坏情况是有广告而非黑屏）。
 */
async function fetchFilteredM3u8(url) {
    if (!/\.m3u8/i.test(url)) return url;
    const proxyUrl = '/api/m3u8?url=' + encodeURIComponent(url);
    try {
        // 1) 优先用浏览器直接拉播放列表（带正确 Referer/Cookie，CDN 通常只认浏览器）
        const r = await fetch(url, { credentials: 'include', mode: 'cors' });
        if (!r.ok) throw new Error('browser fetch ' + r.status);
        const rawText = await r.text();
        // 2) 后端只做过滤+暂存，返回以 .m3u8 结尾的本地地址（不会再远程拉取，也不会二次包装）
        const pr = await fetch('/api/m3u8_prepared', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: rawText, base_url: url, smart: true })
        });
        if (pr.ok) {
            const j = await pr.json();
            if (j && j.url) return j.url;
        }
        // 预处理失败 -> 直接走后端代理过滤（不再把已包装的 master 二次 POST）
        return proxyUrl;
    } catch (e) {
        // 浏览器拉取失败（CORS 等）-> 走后端代理过滤（服务端拉取，变体只包装一层）
        console.warn('m3u8 浏览器拉取失败，回退后端代理:', e);
        return proxyUrl;
    }
}

function isProxiedM3u8(url) {
    return url.startsWith('/api/') || url.startsWith('blob:');
}

const Player = {
    _art: null,
    _hls: null,

    /** 销毁所有播放器实例 */
    destroy() {
        if (this._art) {
            try { this._art.destroy(false); } catch (e) {}
            this._art = null;
        }
        if (this._hls) {
            try { this._hls.destroy(); } catch (e) {}
            this._hls = null;
        }
    },

    /** 渲染原生播放器 */
    renderNative(url, container, skipSettings = {}, onTimeUpdate = null) {
        this.destroy();
        const mount = container.querySelector('#playerMount') || container;
        mount.innerHTML =
            '<video id="pv" controls autoplay playsinline webkit-playsinline class="w-full h-full bg-black object-contain"></video>';
        const v = document.getElementById('pv');

        if (url.includes('.m3u8') && window.Hls && Hls.isSupported()) {
            this._hls = new Hls();
            if (isProxiedM3u8(url)) {
                this._hls.loadSource(url);
            } else {
                // 经后端代理做智能去广告过滤后再播放
                fetchFilteredM3u8(url).then((src) => {
                    if (this._hls) this._hls.loadSource(src);
                }).catch((e) => {
                    // 不再退化回原始直链（否则广告照播）；仅记录错误
                    console.warn('m3u8 代理失败:', e);
                });
            }
            this._hls.attachMedia(v);
        } else {
            v.src = url;
        }

        v.playbackRate = 1;

        // 片头跳过
        let introSkipped = false;
        v.addEventListener('loadedmetadata', () => {
            if (!introSkipped && skipSettings.skipIntro && skipSettings.introTime > 0) {
                introSkipped = true;
                if (skipSettings.introTime < v.duration - 5) {
                    v.currentTime = skipSettings.introTime;
                }
            }
        }, { once: true });

        // 时间更新回调
        let outroFired = false;
        v.ontimeupdate = () => {
            const ct = v.currentTime || 0;
            if (onTimeUpdate) onTimeUpdate(ct);
            if (!outroFired && skipSettings.skipOutro && skipSettings.outroTime > 0 && v.duration > 0) {
                if (ct >= v.duration - skipSettings.outroTime) {
                    outroFired = true;
                    // 触发连播回调
                    if (typeof window._onOutroTrigger === 'function') {
                        window._onOutroTrigger();
                    }
                }
            }
        };
        v.onended = () => {
            if (typeof window._onVideoEnded === 'function') {
                window._onVideoEnded();
            }
        };
        v.onratechange = () => {
            if (typeof window._onRateChange === 'function') {
                window._onRateChange(v.playbackRate);
            }
        };
    },

    /** 渲染 ArtPlayer */
    renderArt(url, container, skipSettings = {}, onTimeUpdate = null) {
        this.destroy();
        let introSkipped = false;
        let outroFired = false;

        this._art = new Artplayer({
            container: container,
            url: url,
            autoplay: true,
            autoSize: true,
            fullscreen: true,
            fullscreenWeb: true,
            playbackRate: true,
            setting: true,
            pip: true,
            mutex: true,
            customType: {
                m3u8: async function (video, m3u8Url) {
                    if (window.Hls && Hls.isSupported()) {
                        const hls = new Hls();
                        let src = m3u8Url;
                        if (!isProxiedM3u8(m3u8Url)) {
                            try {
                                src = await fetchFilteredM3u8(m3u8Url);
                            } catch (e) {
                                // 不再退化回原始直链（否则广告照播）；仅记录错误
                                console.warn('m3u8 代理失败:', e);
                            }
                        }
                        hls.loadSource(src);
                        hls.attachMedia(video);
                    } else {
                        video.src = m3u8Url;
                    }
                }
            }
        });

        this._art.on('ready', () => {
            try {
                if (!introSkipped && skipSettings.skipIntro && skipSettings.introTime > 0) {
                    introSkipped = true;
                    const dur = this._art.video.duration;
                    if (skipSettings.introTime < dur - 5) {
                        this._art.video.currentTime = skipSettings.introTime;
                    }
                }
            } catch (e) {}
        });

        this._art.on('video:timeupdate', () => {
            try {
                const ct = this._art.video.currentTime || 0;
                if (onTimeUpdate) onTimeUpdate(ct);
                if (!outroFired && skipSettings.skipOutro && skipSettings.outroTime > 0 && this._art.video.duration > 0) {
                    if (ct >= this._art.video.duration - skipSettings.outroTime) {
                        outroFired = true;
                        if (typeof window._onOutroTrigger === 'function') {
                            window._onOutroTrigger();
                        }
                    }
                }
            } catch (e) {}
        });

        this._art.on('video:ended', () => {
            if (typeof window._onVideoEnded === 'function') {
                window._onVideoEnded();
            }
        });
    },

    /** 获取当前播放时间 */
    getCurrentTime() {
        if (this._art && this._art.video) return Math.floor(this._art.video.currentTime || 0);
        const v = document.getElementById('pv');
        if (v) return Math.floor(v.currentTime || 0);
        return 0;
    },

    /** 获取视频总时长 */
    getDuration() {
        if (this._art && this._art.video) return Math.floor(this._art.video.duration || 0);
        const v = document.getElementById('pv');
        if (v) return Math.floor(v.duration || 0);
        return 0;
    },

    /** 获取剩余时间 */
    getRemainingTime() {
        const dur = this.getDuration();
        const cur = this.getCurrentTime();
        if (dur <= 0 || cur <= 0) return 0;
        return Math.max(0, dur - cur);
    }
};
