const TOKEN_ALERT_DEFAULT = 100000;
const REFRESH_MS = 5000;

let lastAlertBucket = 0;

function formatCompact(value) {
    const n = Number(value || 0);
    const abs = Math.abs(n);
    const units = [
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

async function refreshMonitor() {
    const state = getTrackerState();
    const threshold = getThreshold();
    let value = 0;
    let data = {};
    let mode = '近 1 小时用量';

    if (state.running && Number(state.baseTotalTokens || 0) >= 0) {
        data = await api('/api/token/current');
        value = Math.max(Number(data.total_tokens || 0) - Number(state.baseTotalTokens || 0), 0);
        mode = '当前追踪增量';
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

    if (data.cache_supported) {
        cacheEl.textContent = `缓存: ${formatCompact(data.cache_total_tokens || 0)}`;
    } else {
        cacheEl.textContent = '缓存: 未配置代理缓存数据库';
    }

    maybeAlert(value, threshold);
}

function maybeAlert(value, threshold) {
    if (!threshold || value < threshold) return;
    const bucket = Math.floor(value / threshold);
    if (bucket <= lastAlertBucket) return;
    lastAlertBucket = bucket;
    if (window.pywebview?.api?.notify_monitor_alert) {
        window.pywebview.api.notify_monitor_alert(`Token 已达到 ${formatCompact(value)}`);
    }
}

function setCompact(compact) {
    document.getElementById('monitor-card').classList.toggle('compact', compact);
    localStorage.setItem('desktop_token_monitor_compact', String(compact));
    document.getElementById('compact-glyph').textContent = compact ? '+' : '−';
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

window.addEventListener('DOMContentLoaded', () => {
    setCompact(localStorage.getItem('desktop_token_monitor_compact') === 'true');
    document.getElementById('compact-btn').addEventListener('click', toggleCompact);
    document.addEventListener('contextmenu', showContextMenu);
    document.addEventListener('click', hideContextMenu);
    document.getElementById('context-menu').addEventListener('click', (event) => {
        const button = event.target.closest('button[data-action]');
        if (button) runMenuAction(button.dataset.action);
    });
    refreshMonitor().catch(console.error);
    setInterval(() => refreshMonitor().catch(console.error), REFRESH_MS);
});
