/**
 * TSPlayer API 封装模块
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
        const resp = await fetch('/api/cms/check?source=' + encodeURIComponent(url));
        return await resp.json();
    }
};
