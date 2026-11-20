/**
 * PPPlayer API 封装模块
 * 所有与后端 Flask API 的通信集中在此
 */

const API = {
    /** 获取 CMS 分类列表 */
    async getClasses(sourceUrl) {
        const resp = await fetch('/api/cms/classes?source=' + encodeURIComponent(sourceUrl));
        if (!resp.ok) throw new Error(`Classes error: ${resp.status}`);
        return resp.json();
    },

    /** 获取 CMS 视频列表（支持外部取消 + 前端硬超时） */
    async getVideos(sourceUrl, params = {}, options = {}) {
        const qs = new URLSearchParams({ source: sourceUrl, ...params });
        const controller = new AbortController();
        let timedOut = false;
        const timer = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, Number(options.timeoutMs) || 35000);

        const externalSignal = options.signal || null;
        const onExternalAbort = () => controller.abort();
        if (externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else externalSignal.addEventListener('abort', onExternalAbort, { once: true });
        }

        try {
            const resp = await fetch('/api/cms/videos?' + qs.toString(), {
                signal: controller.signal,
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                // 后端已返回结构化中文错误（含「接口本身不支持搜索」等），直接透传
                const msg = (data && (data.error || data.message)) || `请求失败（HTTP ${resp.status}）`;
                const err = new Error(msg);
                err.code = (data && data.code) || '';
                err.status = resp.status;
                err.payload = data;
                throw err;
            }
            return data;
        } catch (e) {
            if (timedOut && !(externalSignal && externalSignal.aborted)) {
                const err = new Error('请求超时，请稍后重试');
                err.name = 'TimeoutError';
                err.code = 'TIMEOUT';
                throw err;
            }
            throw e;
        } finally {
            clearTimeout(timer);
            if (externalSignal) {
                try { externalSignal.removeEventListener('abort', onExternalAbort); } catch (e) {}
            }
        }
    },

    /** 获取视频详情 */
    async getDetail(sourceUrl, vodId) {
        const qs = new URLSearchParams({ source: sourceUrl, id: vodId });
        const resp = await fetch('/api/cms/detail?' + qs.toString());
        if (!resp.ok) throw new Error(`Detail error: ${resp.status}`);
        return resp.json();
    },

    /** 媒体嗅探解析 */
    async parseUrl(rawUrl) {
        const resp = await fetch('/api/parse?url=' + encodeURIComponent(rawUrl));
        if (!resp.ok) throw new Error(`Parse error: ${resp.status}`);
        return resp.json();
    },

    /** 加载全部配置（从后端 JSON 文件） */
    async loadAllConfig() {
        const resp = await fetch('/api/config/load');
        return await resp.json();
    },

    /** 保存全部配置到后端 JSON 文件 */
    async saveAllConfig(data) {
        await fetch('/api/config/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
    },

    /** CMS源连接检测 */
    async checkSource(url) {
        // 前端兜底超时：即使后端因极端情况未返回，也不会让 UI 永远停在「检测中」。
        // 略大于后端 CHECK_TIMEOUT，避免误伤正常（较慢）检测。
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 60000);
        try {
            const resp = await fetch('/api/cms/check?source=' + encodeURIComponent(url), {
                signal: controller.signal,
            });
            return await resp.json();
        } catch (e) {
            if (e && e.name === 'AbortError') {
                return { status: 'error', code: 0, message: '检测超时：前端等待超时，已中止' };
            }
            throw e;
        } finally {
            clearTimeout(timer);
        }
    }
};
