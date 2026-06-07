const TOKEN_ALERT_DEFAULT = 100000;
const REFRESH_MS = 5000;
const MONITOR_WINDOW_WIDTH = 300;
const MONITOR_COMPACT_MIN_HEIGHT = 92;
const MONITOR_EXPANDED_MIN_HEIGHT = 176;
const MONITOR_MAX_HEIGHT = 320;
const monitorLang = localStorage.getItem('codex_gui_lang') === 'en' ? 'en' : 'zh';
const monitorCopy = {
    zh: {
        title: 'Token 监控',
        lastHourUsage: '近 1 小时用量',
        trackingDelta: '当前追踪增量',
        reuseUnavailable: '复用: -- · 新增: -- · 合计: -- · 不支持',
        reused: '复用',
        newReusable: '新增',
        totalSaved: '合计',
        source: '来源',
        sourceCodex: 'Codex 记录',
        sourceLocal: '本地复用记录',
        sourceOverlap: '来源可能重叠',
        contextLength: '上下文长度',
        contextUnavailable: '上下文长度: 暂未匹配模型列表',
        tokenReached: 'Token 已达到',
        compactTitle: '折叠',
        compact: '折叠 / 展开',
        refresh: '立即刷新',
        hide: '隐藏悬浮窗',
        main: '显示主窗口',
    },
    en: {
        title: 'Token Monitor',
        lastHourUsage: 'Last hour usage',
        trackingDelta: 'Current tracking change',
        reuseUnavailable: 'Reused: -- · new: -- · total: -- · not supported',
        reused: 'Reused',
        newReusable: 'New',
        totalSaved: 'Total',
        source: 'Source',
        sourceCodex: 'Codex records',
        sourceLocal: 'Local reuse history',
        sourceOverlap: 'sources may overlap',
        contextLength: 'Context length',
        contextUnavailable: 'Context length: model list not matched yet',
        tokenReached: 'Token reached',
        compactTitle: 'Collapse',
        compact: 'Collapse / Expand',
        refresh: 'Refresh now',
        hide: 'Hide monitor',
        main: 'Show main window',
    },
};

let lastAlertBucket = 0;
let monitorSettings = null;

function mt(key) {
    return (monitorCopy[monitorLang] && monitorCopy[monitorLang][key]) || monitorCopy.zh[key] || key;
}

function formatCompact(value) {
    const n = Number(value || 0);
    const abs = Math.abs(n);
    const units = monitorLang === 'en'
        ? [
            { value: 1000000000, suffix: 'B' },
            { value: 1000000, suffix: 'M' },
            { value: 1000, suffix: 'K' },
        ]
        : [
            { value: 100000000, suffix: '亿' },
            { value: 1000000, suffix: '百万' },
            { value: 1000, suffix: '千' },
        ];
    for (const unit of units) {
        if (abs >= unit.value) {
            const scaled = n / unit.value;
            const digits = Math.abs(scaled) < 10 ? 2 : 1;
            return Number(scaled.toFixed(digits)).toLocaleString(undefined, {
                minimumFractionDigits: 0,
                maximumFractionDigits: digits,
            }) + unit.suffix;
        }
    }
    return Math.round(n).toLocaleString();
}

function getTrackerState() {
    try {
        return JSON.parse(localStorage.getItem('token_tracker_state') || '{}');
    } catch {
        return {};
    }
}

function getThreshold() {
    const state = getTrackerState();
    const stored = Number(localStorage.getItem('token_alert_threshold') || state.threshold || TOKEN_ALERT_DEFAULT);
    return Number.isFinite(stored) && stored > 0 ? stored : TOKEN_ALERT_DEFAULT;
}

async function api(url) {
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}

async function loadMonitorSettings() {
    try {
        monitorSettings = await api('/api/settings');
    } catch {
        monitorSettings = {};
    }
}

function monitorFields() {
    return (monitorSettings && monitorSettings.monitor_fields) || {
        tokens: true,
        progress: true,
        threshold: true,
        cache: true,
        context_window: true,
        updated_at: true,
    };
}

function oneHourQuery() {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1000);
    const params = new URLSearchParams({
        start: start.toISOString(),
        end: end.toISOString(),
        granularity: 'total',
    });
    return `/api/token/current?${params.toString()}`;
}

function formatCacheLine(data) {
    if (!data || !data.cache_supported) {
        return mt('reuseUnavailable');
    }
    const read = formatCompact(data.cache_read_tokens || 0);
    const write = formatCompact(data.cache_creation_tokens || 0);
    const total = formatCompact(data.cache_total_tokens || 0);
    const sources = Array.isArray(data.cache_sources) ? data.cache_sources : [];
    const labels = sources.map(source => {
        if (source === 'codex_rollout') return mt('sourceCodex');
        if (source === 'cc_switch_db') return mt('sourceLocal');
        return source;
    }).filter(Boolean);
    const sourceText = labels.length ? ` · ${mt('source')}: ${labels.join(' + ')}` : '';
    const riskText = data.cache_overlap_risk ? ` · ${mt('sourceOverlap')}` : '';
    return `${mt('reused')}: ${read} · ${mt('newReusable')}: ${write} · ${mt('totalSaved')}: ${total}${sourceText}${riskText}`;
}

async function refreshMonitor() {
    const state = getTrackerState();
    const threshold = getThreshold();
    let value = 0;
    let data = {};
    let mode = mt('lastHourUsage');

    if (state.running && Number(state.baseTotalTokens || 0) >= 0) {
        data = await api('/api/token/current');
        value = Math.max(Number(data.total_tokens || 0) - Number(state.baseTotalTokens || 0), 0);
        mode = mt('trackingDelta');
    } else {
        data = await api(oneHourQuery());
        value = Number(data.total_tokens || 0);
    }

    render(value, threshold, mode, data);
}

function render(value, threshold, mode, data) {
    const card = document.getElementById('monitor-card');
    const valueEl = document.getElementById('monitor-value');
    const modeEl = document.getElementById('monitor-mode');
    const fillEl = document.getElementById('monitor-fill');
    const thresholdEl = document.getElementById('monitor-threshold');
    const updatedEl = document.getElementById('monitor-updated');
    const cacheEl = document.getElementById('monitor-cache');
    const contextEl = document.getElementById('monitor-context');
    const progressEl = document.querySelector('.progress');
    const fields = monitorFields();

    modeEl.textContent = mode;
    valueEl.textContent = formatCompact(value);
    valueEl.classList.remove('bump');
    void valueEl.offsetWidth;
    valueEl.classList.add('bump');
    setTimeout(() => valueEl.classList.remove('bump'), 180);

    const pct = threshold > 0 ? Math.min((value / threshold) * 100, 100) : 0;
    fillEl.style.width = `${pct}%`;
    card.classList.toggle('alert', threshold > 0 && value >= threshold);
    thresholdEl.textContent = `${formatCompact(value)} / ${formatCompact(threshold)}`;
    updatedEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    cacheEl.textContent = formatCacheLine(data);
    cacheEl.title = cacheEl.textContent;
    const contextWindow = Number(data.current_context_window || 0);
    contextEl.textContent = contextWindow
        ? `${mt('contextLength')}: ${formatCompact(contextWindow)} · ${data.current_model || '-'}`
        : mt('contextUnavailable');

    document.querySelector('.value-row').style.display = fields.tokens ? 'flex' : 'none';
    progressEl.style.display = fields.progress ? 'block' : 'none';
    thresholdEl.style.display = fields.threshold ? 'inline' : 'none';
    updatedEl.style.display = fields.updated_at ? 'inline' : 'none';
    cacheEl.style.display = fields.cache ? 'block' : 'none';
    contextEl.style.display = fields.context_window ? 'block' : 'none';

    maybeAlert(value, threshold);
    scheduleMonitorResize();
}

function maybeAlert(value, threshold) {
    if (!threshold || value < threshold) return;
    const bucket = Math.floor(value / threshold);
    if (bucket <= lastAlertBucket) return;
    lastAlertBucket = bucket;
    if (window.pywebview?.api?.notify_monitor_alert) {
        window.pywebview.api.notify_monitor_alert(`${mt('tokenReached')} ${formatCompact(value)}`);
    }
}

function setCompact(compact) {
    document.getElementById('monitor-card').classList.toggle('compact', compact);
    localStorage.setItem('desktop_token_monitor_compact', String(compact));
    document.getElementById('compact-glyph').textContent = compact ? '+' : '−';
    scheduleMonitorResize();
}

function toggleCompact() {
    const card = document.getElementById('monitor-card');
    setCompact(!card.classList.contains('compact'));
}

function showContextMenu(event) {
    event.preventDefault();
    const menu = document.getElementById('context-menu');
    menu.style.left = `${Math.min(event.clientX, window.innerWidth - 150)}px`;
    menu.style.top = `${Math.min(event.clientY, window.innerHeight - 150)}px`;
    menu.classList.add('open');
}

function hideContextMenu() {
    document.getElementById('context-menu').classList.remove('open');
}

function preferredMonitorHeight() {
    const card = document.getElementById('monitor-card');
    if (!card) return MONITOR_EXPANDED_MIN_HEIGHT;
    const compact = card.classList.contains('compact');
    const cardHeight = Math.ceil(card.getBoundingClientRect().height);
    const minimum = compact ? MONITOR_COMPACT_MIN_HEIGHT : MONITOR_EXPANDED_MIN_HEIGHT;
    return Math.min(Math.max(cardHeight + 14, minimum), MONITOR_MAX_HEIGHT);
}

function scheduleMonitorResize() {
    requestAnimationFrame(() => {
        if (!window.pywebview?.api?.resize_monitor) return;
        window.pywebview.api.resize_monitor(MONITOR_WINDOW_WIDTH, preferredMonitorHeight()).catch(() => {});
    });
}

async function runMenuAction(action) {
    hideContextMenu();
    if (action === 'compact') toggleCompact();
    if (action === 'refresh') refreshMonitor().catch(console.error);
    if (action === 'hide' && window.pywebview?.api?.hide_monitor) {
        await window.pywebview.api.hide_monitor();
    }
    if (action === 'main' && window.pywebview?.api?.show_main) {
        await window.pywebview.api.show_main();
    }
}

function applyMonitorCopy() {
    document.documentElement.lang = monitorLang === 'en' ? 'en' : 'zh-CN';
    document.title = mt('title');
    const kicker = document.querySelector('.monitor-kicker');
    if (kicker) kicker.textContent = mt('title');
    const compactBtn = document.getElementById('compact-btn');
    if (compactBtn) compactBtn.title = mt('compactTitle');
    document.querySelectorAll('#context-menu [data-action]').forEach(btn => {
        const action = btn.getAttribute('data-action');
        const text = mt(action);
        if (text !== action) btn.textContent = text;
    });
}

window.addEventListener('DOMContentLoaded', () => {
    applyMonitorCopy();
    setCompact(localStorage.getItem('desktop_token_monitor_compact') === 'true');
    document.getElementById('compact-btn').addEventListener('click', toggleCompact);
    document.addEventListener('contextmenu', showContextMenu);
    document.addEventListener('click', hideContextMenu);
    document.getElementById('context-menu').addEventListener('click', (event) => {
        const button = event.target.closest('button[data-action]');
        if (button) runMenuAction(button.dataset.action);
    });
    loadMonitorSettings().finally(() => refreshMonitor().catch(console.error));
    setInterval(() => refreshMonitor().catch(console.error), REFRESH_MS);
    window.addEventListener('resize', scheduleMonitorResize);
});
