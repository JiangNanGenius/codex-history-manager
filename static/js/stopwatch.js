/**
 * stopwatch.js - Token 秒表和时间段实时统计
 */

const STOPWATCH_TOKEN_REFRESH_MS = 4000;
const RANGE_REFRESH_MS = 20000;
const STATS_REFRESH_MS = 20000;

let stopwatchState = {
    running: false,
    startMs: 0,
    baseTotalTokens: 0,
    currentTotalTokens: 0,
};

let rangeTrendChart = null;

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

function updateStopwatchElapsed() {
    if (!stopwatchState.running) return;
    setTextById('stopwatch-elapsed', formatElapsed(Date.now() - stopwatchState.startMs));
}

async function refreshStopwatchTokens() {
    const data = await fetchCurrentTokenStats();
    const totalTokens = Number(data.total_tokens || 0);
    stopwatchState.currentTotalTokens = totalTokens;
    const tokenDiff = Math.max(totalTokens - stopwatchState.baseTotalTokens, 0);

    setTextById('stopwatch-current-total', formatNumber(totalTokens));
    setTextById('stopwatch-token-diff', formatNumber(tokenDiff));
    setTextById('stopwatch-cache-hits', data.cache_supported ? t('cacheAvailable') : t('cacheNotSupported'));
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
        setTextById('stopwatch-cache-hits', data.cache_supported ? t('cacheAvailable') : t('cacheNotSupported'));
        setTextById('stopwatch-note', data.realtime_note || t('recording'));

        realtimeController.start('stopwatch-elapsed', updateStopwatchElapsed, 1000, true);
        realtimeController.start('stopwatch-tokens', refreshStopwatchTokens, STOPWATCH_TOKEN_REFRESH_MS, false);
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
    setTextById('stopwatch-cache-hits', t('cacheNotSupported'));
    setTextById('stopwatch-note', t('noDataYet'));
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

function toggleRangeRealtime() {
    const enabled = Boolean(document.getElementById('range-realtime-toggle')?.checked);
    if (enabled) {
        realtimeController.start('range-stats', refreshRangeStats, RANGE_REFRESH_MS, true);
    } else {
        realtimeController.stop('range-stats');
    }
}

function restartRangeRealtimeIfEnabled() {
    const enabled = Boolean(document.getElementById('range-realtime-toggle')?.checked);
    if (enabled) {
        realtimeController.restart('range-stats', refreshRangeStats, RANGE_REFRESH_MS, true);
    } else {
        refreshRangeStats().catch(err => console.warn('Range refresh failed', err));
    }
}

function toggleStatsRealtime() {
    const enabled = Boolean(document.getElementById('stats-realtime-toggle')?.checked);
    const status = document.getElementById('stats-realtime-status');
    if (enabled) {
        if (status) status.textContent = t('enabled');
        realtimeController.start('stats-dashboard', loadStats, STATS_REFRESH_MS, true);
    } else {
        if (status) status.textContent = t('disabled');
        realtimeController.stop('stats-dashboard');
    }
}

function initRangeDefaults() {
    const endInput = document.getElementById('range-end');
    const startInput = document.getElementById('range-start');
    const rangeToggle = document.getElementById('range-realtime-toggle');
    if (!endInput || !startInput) return;

    const now = new Date();
    const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    const fmt = (d) => d.toISOString().split('T')[0];

    if (!endInput.value) endInput.value = fmt(now);
    if (!startInput.value) startInput.value = fmt(sevenDaysAgo);

    refreshRangeStats().catch(err => console.warn('Initial range refresh failed', err));

    // Auto-enable range realtime if toggle exists and not checked
    if (rangeToggle && !rangeToggle.checked) {
        rangeToggle.checked = true;
        toggleRangeRealtime();
    }
}

// Auto-init range defaults when stats page loads
window.addEventListener('DOMContentLoaded', () => {
    initRangeDefaults();
});
