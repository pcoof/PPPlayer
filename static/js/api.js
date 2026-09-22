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

    /** 获取 CMS 视频列表 */
    async getVideos(sourceUrl, params = {}) {
        const qs = new URLSearchParams({ source: sourceUrl, ...params });
        const resp = await fetch('/api/cms/videos?' + qs.toString());
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            // 后端已返回结构化中文错误（含「接口本身不支持搜索」等），直接透传
            const msg = (data && (data.error || data.message)) || `请求失败（HTTP ${resp.status}）`;
            throw new Error(msg);
        }
        return data;
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
