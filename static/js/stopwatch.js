/**
 * stopwatch.js - Token 用量追踪和时间段实时统计
 */

const STOPWATCH_TOKEN_REFRESH_MS = 4000;
const RANGE_REFRESH_MS = 20000;
const STATS_REFRESH_MS = 20000;
const TOKEN_MONITOR_REFRESH_MS = 10000;
const TOKEN_ALERT_DEFAULT = 100000;

let stopwatchState = {
    running: false,
    startMs: 0,
    baseTotalTokens: 0,
    currentTotalTokens: 0,
};

let rangeTrendChart = null;
let rangeDefaultsInitialized = false;
let tokenMonitorState = {
    visible: localStorage.getItem('token_monitor_visible') !== 'false',
    compact: localStorage.getItem('token_monitor_compact') === 'true',
    threshold: Number(localStorage.getItem('token_alert_threshold') || TOKEN_ALERT_DEFAULT),
    lastAlertBucket: 0,
};

function formatElapsed(ms) {
    const totalSeconds = Math.max(Math.floor(ms / 1000), 0);
    const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, '0');
    const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, '0');
    const seconds = String(totalSeconds % 60).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
}

function setTextById(id, text) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = text;
    }
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[ch]));
}

async function fetchCurrentTokenStats(params = {}) {
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') {
            searchParams.set(key, value);
        }
    }
    const query = searchParams.toString();
    return api(query ? `/api/token/current?${query}` : '/api/token/current');
}

function getOneHourRangeQuery() {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1000);
    return {
        start: start.toISOString(),
        end: end.toISOString(),
        granularity: 'total',
    };
}

function formatCacheUsage(data) {
    if (!data || !data.cache_supported) {
        return `${t('cacheRead')}: -- · ${t('cacheWrite')}: -- · ${t('cacheTotal')}: -- · ${t('cacheNotSupported')}`;
    }
    const read = formatTokens(data.cache_read_tokens || 0);
    const write = formatTokens(data.cache_creation_tokens || 0);
    const total = formatTokens(data.cache_total_tokens || 0);
    const sources = Array.isArray(data.cache_sources) ? data.cache_sources : [];
    const sourceLabels = sources.map(source => {
        if (source === 'codex_rollout') return t('cacheSourceCodexRollout');
        if (source === 'cc_switch_db') return t('cacheSourceCcSwitch');
        return source;
    }).filter(Boolean);
    const sourceText = sourceLabels.length ? ` · ${t('cacheSource')}: ${sourceLabels.join(' + ')}` : '';
    const riskText = data.cache_overlap_risk ? ` · ${t('cacheOverlapRisk')}` : '';
    return `${t('cacheRead')}: ${read} · ${t('cacheWrite')}: ${write} · ${t('cacheTotal')}: ${total}${sourceText}${riskText}`;
}

function cacheUsageTitle(data) {
    if (!data) return t('cacheUsageUnavailable');
    const lines = [formatCacheUsage(data)];
    if (data.cache_note) lines.push(data.cache_note);
    if (Array.isArray(data.usage_sources)) {
        for (const source of data.usage_sources) {
            if (source && source.tooltip) {
                lines.push(`${source.label || source.id}: ${source.tooltip}`);
            }
        }
    }
    return lines.join('\n');
}

function setCacheUsageDisplay(data) {
    const element = document.getElementById('stopwatch-cache-hits');
    if (!element) return;
    element.textContent = formatCacheUsage(data);
    element.title = cacheUsageTitle(data);
    renderUsageSourceBadges(data);
}

function renderUsageSourceBadges(data) {
    const container = document.getElementById('stopwatch-source-badges');
    if (!container) return;
    const badges = Array.isArray(data?.usage_source_badges) ? data.usage_source_badges : [];
    container.innerHTML = badges.map(badge => {
        const active = badge.active ? ' active' : '';
        const warning = badge.status === 'configured' || badge.status === 'available' ? ' warning' : '';
        const label = escapeHtml(badge.label || badge.id || '');
        const status = badge.status ? ` · ${escapeHtml(badge.status)}` : '';
        const title = escapeHtml(badge.tooltip || '');
        return `<span class="usage-source-badge${active}${warning}" title="${title}">${label}${status}</span>`;
    }).join('');
}

function getTokenAlertThreshold() {
    const input = document.getElementById('token-alert-threshold');
    const raw = input ? Number(input.value) : tokenMonitorState.threshold;
    const threshold = Number.isFinite(raw) && raw > 0 ? raw : 0;
    tokenMonitorState.threshold = threshold;
    return threshold;
}

function saveTokenMonitorSettings() {
    const threshold = getTokenAlertThreshold();
    localStorage.setItem('token_alert_threshold', String(threshold || TOKEN_ALERT_DEFAULT));
    saveTrackerState();
    updateTokenMonitor().catch(err => console.warn('Token monitor refresh failed', err));
}

function saveTrackerState() {
    const payload = {
        running: Boolean(stopwatchState.running),
        startMs: Number(stopwatchState.startMs || 0),
        baseTotalTokens: Number(stopwatchState.baseTotalTokens || 0),
        currentTotalTokens: Number(stopwatchState.currentTotalTokens || 0),
        threshold: Number(tokenMonitorState.threshold || TOKEN_ALERT_DEFAULT),
        updatedAt: Date.now(),
    };
    localStorage.setItem('token_tracker_state', JSON.stringify(payload));
}

async function showDesktopTokenMonitor() {
    saveTokenMonitorSettings();
    if (window.pywebview && window.pywebview.api && window.pywebview.api.show_monitor) {
        await window.pywebview.api.show_monitor();
        return;
    }
    showToast(t('desktopMonitorOnly'), 'info');
}

function initTokenMonitorSettings() {
    const thresholdInput = document.getElementById('token-alert-threshold');
    if (thresholdInput) {
        thresholdInput.value = String(tokenMonitorState.threshold || TOKEN_ALERT_DEFAULT);
    }
    const monitor = document.getElementById('token-monitor');
    if (monitor) {
        monitor.classList.toggle('hidden-monitor', !tokenMonitorState.visible);
        monitor.classList.toggle('compact', tokenMonitorState.compact);
        const savedLeft = localStorage.getItem('token_monitor_left');
        const savedTop = localStorage.getItem('token_monitor_top');
        if (savedLeft && savedTop) {
            monitor.style.left = savedLeft;
            monitor.style.top = savedTop;
            monitor.style.right = 'auto';
            monitor.style.bottom = 'auto';
        }
    }
    setupTokenMonitorDrag();
    realtimeController.start('token-monitor', updateTokenMonitor, TOKEN_MONITOR_REFRESH_MS, true);
}

async function updateTokenMonitor() {
    const monitor = document.getElementById('token-monitor');
    if (!monitor || !tokenMonitorState.visible) return;

    let modeText = t('lastHourUsage');
    let currentValue = 0;
    let threshold = getTokenAlertThreshold();

    if (stopwatchState.running) {
        await refreshStopwatchTokens();
        currentValue = Math.max(stopwatchState.currentTotalTokens - stopwatchState.baseTotalTokens, 0);
        modeText = t('trackingDelta');
    } else {
        const data = await fetchCurrentTokenStats(getOneHourRangeQuery());
        currentValue = Number(data.total_tokens || 0);
    }

    renderTokenMonitor(currentValue, threshold, modeText);
}

function renderTokenMonitor(value, threshold, modeText) {
    const monitor = document.getElementById('token-monitor');
    const valueEl = document.getElementById('token-monitor-value');
    const modeEl = document.getElementById('token-monitor-mode');
    const fillEl = document.getElementById('token-monitor-progress-fill');
    const thresholdEl = document.getElementById('token-monitor-threshold-label');
    const updatedEl = document.getElementById('token-monitor-updated');
    if (!monitor || !valueEl || !modeEl || !fillEl) return;

    modeEl.textContent = modeText;
    valueEl.textContent = formatTokens(value);
    valueEl.classList.remove('updated');
    void valueEl.offsetWidth;
    valueEl.classList.add('updated');
    setTimeout(() => valueEl.classList.remove('updated'), 180);

    const pct = threshold > 0 ? Math.min((value / threshold) * 100, 100) : 0;
    fillEl.style.width = `${pct}%`;
    monitor.classList.toggle('alert', threshold > 0 && value >= threshold);
    if (thresholdEl) {
        thresholdEl.textContent = threshold > 0
            ? `${formatTokens(value)} / ${formatTokens(threshold)}`
            : `${formatTokens(value)} / ${t('disabled')}`;
    }
    if (updatedEl) {
        updatedEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    maybeAlertTokenThreshold(value, threshold);
}

function maybeAlertTokenThreshold(value, threshold) {
    if (!threshold || value < threshold) return;
    const bucket = Math.floor(value / threshold);
    if (bucket <= tokenMonitorState.lastAlertBucket) return;
    tokenMonitorState.lastAlertBucket = bucket;
    showToast(`${t('tokenAlertReached')}: ${formatTokens(value)}`, 'warning', 6000);
    if (navigator.vibrate) {
        navigator.vibrate([120, 60, 120]);
    }
}

function toggleTokenMonitor(forceVisible) {
    tokenMonitorState.visible = typeof forceVisible === 'boolean' ? forceVisible : !tokenMonitorState.visible;
    localStorage.setItem('token_monitor_visible', String(tokenMonitorState.visible));
    const monitor = document.getElementById('token-monitor');
    if (monitor) {
        monitor.classList.toggle('hidden-monitor', !tokenMonitorState.visible);
    }
    if (tokenMonitorState.visible) {
        updateTokenMonitor().catch(err => console.warn('Token monitor refresh failed', err));
    }
}

function toggleTokenMonitorCompact() {
    tokenMonitorState.compact = !tokenMonitorState.compact;
    localStorage.setItem('token_monitor_compact', String(tokenMonitorState.compact));
    const monitor = document.getElementById('token-monitor');
    if (monitor) monitor.classList.toggle('compact', tokenMonitorState.compact);
}

function setupTokenMonitorDrag() {
    const monitor = document.getElementById('token-monitor');
    const handle = document.getElementById('token-monitor-drag');
    if (!monitor || !handle || handle.dataset.dragReady === '1') return;
    handle.dataset.dragReady = '1';

    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    let dragging = false;

    const onMove = (event) => {
        if (!dragging) return;
        const clientX = event.touches ? event.touches[0].clientX : event.clientX;
        const clientY = event.touches ? event.touches[0].clientY : event.clientY;
        const nextLeft = Math.min(Math.max(startLeft + clientX - startX, 8), window.innerWidth - monitor.offsetWidth - 8);
        const nextTop = Math.min(Math.max(startTop + clientY - startY, 8), window.innerHeight - monitor.offsetHeight - 8);
        monitor.style.left = `${nextLeft}px`;
        monitor.style.top = `${nextTop}px`;
        monitor.style.right = 'auto';
        monitor.style.bottom = 'auto';
    };

    const onEnd = () => {
        if (!dragging) return;
        dragging = false;
        localStorage.setItem('token_monitor_left', monitor.style.left);
        localStorage.setItem('token_monitor_top', monitor.style.top);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onEnd);
        window.removeEventListener('touchmove', onMove);
        window.removeEventListener('touchend', onEnd);
    };

    const onStart = (event) => {
        if (event.target.closest('button')) return;
        const clientX = event.touches ? event.touches[0].clientX : event.clientX;
        const clientY = event.touches ? event.touches[0].clientY : event.clientY;
        const rect = monitor.getBoundingClientRect();
        dragging = true;
        startX = clientX;
        startY = clientY;
        startLeft = rect.left;
        startTop = rect.top;
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onEnd);
        window.addEventListener('touchmove', onMove, { passive: false });
        window.addEventListener('touchend', onEnd);
    };

    handle.addEventListener('mousedown', onStart);
    handle.addEventListener('touchstart', onStart, { passive: true });
}

function updateStopwatchElapsed() {
    if (!stopwatchState.running) return;
    setTextById('stopwatch-elapsed', formatElapsed(Date.now() - stopwatchState.startMs));
}

async function refreshStopwatchTokens() {
    const data = await fetchCurrentTokenStats();
    const totalTokens = Number(data.total_tokens || 0);
    stopwatchState.currentTotalTokens = totalTokens;
    const tokenDiff = Math.max(totalTokens - stopwatchState.baseTotalTokens, 0);
    saveTrackerState();

    setTextById('stopwatch-current-total', formatNumber(totalTokens));
    setTextById('stopwatch-token-diff', formatNumber(tokenDiff));
    setCacheUsageDisplay(data);
    setTextById('stopwatch-note', data.cache_note || data.realtime_note || t('noDataYet'));
}

async function startStopwatch() {
    if (stopwatchState.running) return;
    try {
        const data = await fetchCurrentTokenStats();
        const totalTokens = Number(data.total_tokens || 0);
        stopwatchState = {
            running: true,
            startMs: Date.now(),
            baseTotalTokens: totalTokens,
            currentTotalTokens: totalTokens,
        };

        const startBtn = document.getElementById('stopwatch-start-btn');
        const stopBtn = document.getElementById('stopwatch-stop-btn');
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;

        setTextById('stopwatch-elapsed', '00:00:00');
        setTextById('stopwatch-current-total', formatNumber(totalTokens));
        setTextById('stopwatch-token-diff', '0');
        setCacheUsageDisplay(data);
        setTextById('stopwatch-note', data.realtime_note || t('recording'));

        realtimeController.start('stopwatch-elapsed', updateStopwatchElapsed, 1000, true);
        realtimeController.start('stopwatch-tokens', refreshStopwatchTokens, STOPWATCH_TOKEN_REFRESH_MS, false);
        tokenMonitorState.lastAlertBucket = 0;
        saveTrackerState();
        updateTokenMonitor().catch(err => console.warn('Token monitor refresh failed', err));
        setStatus(t('stopwatchRecording'));
    } catch (err) {
        showToast(t('failed') + err.message, 'error');
    }
}

async function stopStopwatch() {
    if (!stopwatchState.running) return;
    const finalElapsedMs = Date.now() - stopwatchState.startMs;
    realtimeController.stop('stopwatch-elapsed');
    realtimeController.stop('stopwatch-tokens');
    stopwatchState.running = false;
    saveTrackerState();

    const startBtn = document.getElementById('stopwatch-start-btn');
    const stopBtn = document.getElementById('stopwatch-stop-btn');
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;

    setTextById('stopwatch-elapsed', formatElapsed(finalElapsedMs));
    try {
        await refreshStopwatchTokens();
    } catch (err) {
        console.warn('Final stopwatch token refresh failed', err);
    }
    setStatus(t('stopwatchStopped'));
}

function resetStopwatch() {
    realtimeController.stop('stopwatch-elapsed');
    realtimeController.stop('stopwatch-tokens');
    stopwatchState = {
        running: false,
        startMs: 0,
        baseTotalTokens: 0,
        currentTotalTokens: 0,
    };

    const startBtn = document.getElementById('stopwatch-start-btn');
    const stopBtn = document.getElementById('stopwatch-stop-btn');
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;

    setTextById('stopwatch-elapsed', '00:00:00');
    setTextById('stopwatch-token-diff', '0');
    setTextById('stopwatch-current-total', '0');
    setCacheUsageDisplay(null);
    setTextById('stopwatch-note', t('noDataYet'));
    tokenMonitorState.lastAlertBucket = 0;
    saveTrackerState();
    updateTokenMonitor().catch(err => console.warn('Token monitor refresh failed', err));
}

function getRangeQuery() {
    return {
        start: document.getElementById('range-start')?.value || '',
        end: document.getElementById('range-end')?.value || '',
        granularity: document.getElementById('range-granularity')?.value || 'day',
    };
}

async function refreshRangeStats() {
    const query = getRangeQuery();
    const data = await fetchCurrentTokenStats(query);
    setTextById('range-total-tokens', formatNumber(data.total_tokens || 0));
    setTextById('range-note', data.realtime_note || t('rangeNote'));
    renderRangeTrend(data.buckets || [], query.granularity);
    setStatus(`Range tokens: ${formatTokens(data.total_tokens || 0)}`);
}

function showRangePlaceholder() {
    const wrapper = document.getElementById('range-chart-wrapper');
    const canvas = document.getElementById('chart-range-trend');
    if (!wrapper) return;
    if (canvas) canvas.style.display = 'none';
    let placeholder = wrapper.querySelector('.range-placeholder');
    if (!placeholder) {
        placeholder = document.createElement('div');
        placeholder.className = 'range-placeholder flex flex-col items-center justify-center text-dark-400 py-8';
        placeholder.innerHTML = `
            <svg class="w-10 h-10 mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"/></svg>
            <span class="text-sm">${t('selectRangeToViewTrend')}</span>
        `;
        wrapper.appendChild(placeholder);
    }
    placeholder.style.display = 'flex';
}

function hideRangePlaceholder() {
    const wrapper = document.getElementById('range-chart-wrapper');
    const canvas = document.getElementById('chart-range-trend');
    if (!wrapper) return;
    const placeholder = wrapper.querySelector('.range-placeholder');
    if (placeholder) placeholder.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
}

function renderRangeTrend(buckets, granularity) {
    const ctx = document.getElementById('chart-range-trend');
    if (!ctx) return;

    if (rangeTrendChart) {
        rangeTrendChart.destroy();
        rangeTrendChart = null;
    }

    const labels = buckets.map(item => item.bucket || '');
    const tokens = buckets.map(item => Number(item.tokens || 0));
    if (granularity === 'total' || labels.length === 0) {
        showRangePlaceholder();
        return;
    }

    hideRangePlaceholder();

    rangeTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: t('tokens'),
                data: tokens,
                borderColor: '#22c55e',
                backgroundColor: 'rgba(34, 197, 94, 0.12)',
                fill: true,
                tension: 0.35,
                pointRadius: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true },
                tooltip: {
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => `${formatNumber(ctx.parsed.y)} ${t('tokensSuffix')}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
                y: { grid: { color: '#1e293b' }, ticks: { callback: value => formatTokens(value) } },
            },
        },
    });
}

function startRangeRealtime() {
    realtimeController.start('range-stats', refreshRangeStats, RANGE_REFRESH_MS, true);
}

function restartRangeRealtime() {
    realtimeController.restart('range-stats', refreshRangeStats, RANGE_REFRESH_MS, true);
}

function startStatsRealtime() {
    realtimeController.start('stats-dashboard', loadStats, STATS_REFRESH_MS, false);
}

function initRangeDefaults() {
    const endInput = document.getElementById('range-end');
    const startInput = document.getElementById('range-start');
    if (!endInput || !startInput) return;
    if (rangeDefaultsInitialized) {
        if (!realtimeController.isRunning('range-stats')) startRangeRealtime();
        return;
    }

    const now = new Date();
    const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    const fmt = (d) => d.toISOString().split('T')[0];

    if (!endInput.value) endInput.value = fmt(now);
    if (!startInput.value) startInput.value = fmt(sevenDaysAgo);

    startRangeRealtime();
    rangeDefaultsInitialized = true;
}

// Auto-init range defaults when stats page loads
window.addEventListener('DOMContentLoaded', () => {
    initRangeDefaults();
    initTokenMonitorSettings();
    refreshStopwatchTokens().catch(err => console.warn('Token cache source refresh failed', err));
    saveTrackerState();
});
