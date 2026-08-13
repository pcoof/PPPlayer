/**
 * TSPlayer API 封装模块
 * 所有与后端 Flask API 的通信集中在此
 */

const API = {
    /** 代理请求（代替原来 Cloudflare Worker 的 /?u= 参数） */
    async proxy(url) {
        const resp = await fetch('/api/proxy?u=' + encodeURIComponent(url));
        if (!resp.ok) throw new Error(`Proxy error: ${resp.status}`);
        return resp;
    },

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
        if (!resp.ok) throw new Error(`Videos error: ${resp.status}`);
        return resp.json();
    },

    /** 获取视频详情 */
    async getDetail(sourceUrl, vodId) {
        const qs = new URLSearchParams({ source: sourceUrl, id: vodId });
        const resp = await fetch('/api/cms/detail?' + qs.toString());
        if (!resp.ok) throw new Error(`Detail error: ${resp.status}`);
        return resp.json();
    },

    /** M3U8 代理 + 广告过滤 */
    async getM3u8(url, skip = 1) {
        const qs = new URLSearchParams({ url: url, skip: String(skip) });
        const resp = await fetch('/api/m3u8?' + qs.toString());
        if (!resp.ok) throw new Error(`M3U8 error: ${resp.status}`);
        return resp.text();
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
