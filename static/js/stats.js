/**
 * stats.js - Token 统计模块
 * 加载和渲染 Token Dashboard
 */

let dailyTrendChart = null;
let byModelChart = null;
let byProviderChart = null;
let hourlyChart = null;
let statsLoadInProgress = false;
let requestLogsLoadInProgress = false;

// Chart.js 全局配置
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = 'Inter, system-ui, sans-serif';

async function loadStats() {
    if (statsLoadInProgress) return;
    statsLoadInProgress = true;
    try {
        const [overview, byModel, byProvider, dailyTrend, topSessions, hourly] = await Promise.all([
            api('/api/stats/overview'),
            api('/api/stats/by-model'),
            api('/api/stats/by-provider'),
            api('/api/stats/daily-trend?days=30'),
            api('/api/stats/top-sessions?limit=10'),
            api('/api/stats/hourly'),
        ]);

        renderOverview(overview);
        renderByModel(byModel);
        renderByProvider(byProvider);
        renderDailyTrend(dailyTrend);
        renderTopSessions(topSessions);
        renderHourly(hourly);
        await loadRequestLogs();

        // Ensure range defaults are initialized
        if (typeof initRangeDefaults === 'function') initRangeDefaults();

        setStatus(`${t('statsLoaded')} - ${formatTokens(overview.total_tokens)}`);
    } catch (err) {
        showToast(t('failedLoadStats') + err.message, 'error');
    } finally {
        statsLoadInProgress = false;
    }
}

function animateValue(element, start, end, duration, formatter) {
    if (!element) return;
    const startTime = performance.now();
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const easeOut = 1 - Math.pow(1 - progress, 3);
        const current = start + (end - start) * easeOut;
        element.textContent = formatter ? formatter(current) : formatNumber(Math.round(current));
        if (progress < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
}

function renderOverview(data) {
    const duration = 800;
    animateValue(document.getElementById('stat-total-tokens'), 0, data.total_tokens || 0, duration, formatTokens);
    animateValue(document.getElementById('stat-total-sessions'), 0, data.total_sessions || 0, duration, formatCount);
    animateValue(document.getElementById('stat-today-tokens'), 0, data.today_tokens || 0, duration, formatTokens);
    document.getElementById('stat-today-sessions').textContent = formatCount(data.today_sessions) + ' ' + t('sessions');
    animateValue(document.getElementById('stat-week-tokens'), 0, data.week_tokens || 0, duration, formatTokens);
    document.getElementById('stat-week-sessions').textContent = formatCount(data.week_sessions) + ' ' + t('sessions');
}

function renderDailyTrend(data) {
    const ctx = document.getElementById('chart-daily-trend');
    if (!ctx) return;

    if (dailyTrendChart) dailyTrendChart.destroy();

    const labels = data.map(d => d.date || '');
    const tokensData = data.map(d => d.tokens || 0);
    const sessionsData = data.map(d => d.sessions || 0);

    dailyTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: t('tokens'),
                    data: tokensData,
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    yAxisID: 'y',
                },
                {
                    label: t('sessions'),
                    data: sessionsData,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    fill: false,
                    tension: 0.4,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    yAxisID: 'y1',
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'top', labels: { usePointStyle: true, padding: 15 } },
                tooltip: {
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        label: function(ctx) {
                            return ctx.dataset.label + ': ' + formatNumber(ctx.parsed.y);
                        }
                    }
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
                y: {
                    position: 'left',
                    grid: { color: '#1e293b' },
                    ticks: { callback: v => formatTokens(v), font: { size: 10 } },
                },
                y1: {
                    position: 'right',
                    grid: { display: false },
                    ticks: { font: { size: 10 } },
                },
            },
        },
    });
}

function renderByModel(data) {
    const ctx = document.getElementById('chart-by-model');
    if (!ctx) return;

    if (byModelChart) byModelChart.destroy();

    const colors = [
        '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
        '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
    ];

    byModelChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.model || 'unknown'),
            datasets: [{
                data: data.map(d => d.tokens || 0),
                backgroundColor: colors.slice(0, data.length),
                borderColor: '#1e293b',
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '60%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { usePointStyle: true, padding: 10, font: { size: 11 } },
                },
                tooltip: {
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return ctx.label + ': ' + formatTokens(ctx.parsed) + ' ' + t('tokensSuffix');
                        }
                    }
                },
            },
        },
    });
}

function renderByProvider(data) {
    const ctx = document.getElementById('chart-by-provider');
    if (!ctx) return;

    if (byProviderChart) byProviderChart.destroy();

    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

    byProviderChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.provider || 'unknown'),
            datasets: [{
                label: t('tokens'),
                data: data.map(d => d.tokens || 0),
                backgroundColor: colors.slice(0, data.length).map(c => c + '60'),
                borderColor: colors.slice(0, data.length),
                borderWidth: 1,
                borderRadius: 6,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return formatNumber(ctx.parsed.x) + ' ' + t('tokensSuffix');
                        }
                    }
                },
            },
            scales: {
                x: { grid: { color: '#1e293b' }, ticks: { callback: v => formatTokens(v) } },
                y: { grid: { display: false } },
            },
        },
    });
}

function renderHourly(data) {
    const ctx = document.getElementById('chart-hourly');
    if (!ctx) return;

    if (hourlyChart) hourlyChart.destroy();

    // Fill all 24 hours
    const hourMap = {};
    data.forEach(d => { hourMap[d.hour] = d; });
    const labels = [];
    const tokensData = [];
    for (let h = 0; h < 24; h++) {
        const key = String(h).padStart(2, '0');
        labels.push(key + ':00');
        tokensData.push(hourMap[key] ? (hourMap[key].tokens || 0) : 0);
    }

    // Color based on intensity
    const maxTokens = Math.max(...tokensData, 1);
    const bgColors = tokensData.map(v => {
        const intensity = v / maxTokens;
        const r = Math.round(59 * intensity + 30 * (1 - intensity));
        const g = Math.round(130 * intensity + 41 * (1 - intensity));
        const b = Math.round(246 * intensity + 59 * (1 - intensity));
        return `rgba(${r}, ${g}, ${b}, 0.6)`;
    });

    hourlyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: t('tokens'),
                data: tokensData,
                backgroundColor: bgColors,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return formatNumber(ctx.parsed.y) + ' ' + t('tokensSuffix');
                        }
                    }
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 9 }, maxRotation: 0 } },
                y: { grid: { color: '#1e293b' }, ticks: { callback: v => formatTokens(v), font: { size: 10 } } },
            },
        },
    });
}

function renderTopSessions(data) {
    const tbody = document.getElementById('top-sessions-body');
    if (!tbody) return;

    if (data.length === 0 || data.error) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-dark-400">' + t('noData') + '</td></tr>';
        return;
    }

    tbody.innerHTML = data.map((s, i) => `
        <tr class="cursor-pointer" onclick="openSessionFromStats('${s.id || ''}')">
            <td class="py-2 px-3 text-dark-400">${i + 1}</td>
            <td class="py-2 px-3 text-white max-w-xs truncate">${escapeHtml(s.title || '(No Title)')}</td>
            <td class="py-2 px-3"><span class="px-2 py-0.5 rounded text-xs bg-dark-700 text-dark-300">${escapeHtml(s.model || '-')}</span></td>
            <td class="py-2 px-3 text-right font-mono text-accent-400">${formatNumber(s.tokens)}</td>
        </tr>
    `).join('');
}

function openSessionFromStats(sessionId) {
    if (!sessionId) return;
    navigateTo('sessions');
    setTimeout(() => openSessionDetail(sessionId), 200);
}

async function loadRequestLogs() {
    if (requestLogsLoadInProgress) return;
    const root = document.getElementById('request-logs-root');
    if (!root) return;
    requestLogsLoadInProgress = true;
    const filters = getRequestLogFilters();
    renderRequestLogsLoading(root, filters);
    try {
        const query = new URLSearchParams();
        query.set('limit', String(filters.limit));
        if (filters.provider_id) query.set('provider_id', filters.provider_id);
        if (filters.endpoint) query.set('endpoint', filters.endpoint);
        if (filters.success) query.set('success', filters.success);
        const [summary, data] = await Promise.all([
            api('/api/request-logs/summary'),
            api('/api/request-logs?' + query.toString()),
        ]);
        renderRequestLogs(root, summary || {}, data || {}, filters);
    } catch (err) {
        renderRequestLogsError(root, err, filters);
    } finally {
        requestLogsLoadInProgress = false;
    }
}

function getRequestLogFilters() {
    const limit = Number(document.getElementById('request-log-limit')?.value || 50);
    return {
        provider_id: document.getElementById('request-log-provider')?.value.trim() || '',
        endpoint: document.getElementById('request-log-endpoint')?.value.trim() || '',
        success: document.getElementById('request-log-success')?.value || '',
        limit: Number.isFinite(limit) ? Math.max(Math.min(Math.round(limit), 500), 1) : 50,
    };
}

function renderRequestLogsLoading(root, filters) {
    root.innerHTML = renderRequestLogsShell({
        summary: null,
        entries: [],
        filters,
        body: '<div class="py-8 text-center text-dark-400">Loading local proxy request logs...</div>',
    });
}

function renderRequestLogsError(root, err, filters) {
    root.innerHTML = renderRequestLogsShell({
        summary: null,
        entries: [],
        filters,
        body: `<div class="py-8 text-center text-rose-300">Failed to load request logs: ${escapeHtml(err.message || String(err))}</div>`,
    });
}

function renderRequestLogs(root, summary, data, filters) {
    const entries = Array.isArray(data.entries) ? data.entries : [];
    root.innerHTML = renderRequestLogsShell({
        summary,
        entries,
        filters,
        body: renderRequestLogsTable(entries),
    });
}

function renderRequestLogsShell({ summary, entries, filters, body }) {
    const safeSummary = summary || {};
    const tokens = safeSummary.tokens || {};
    const entriesCount = entries ? entries.length : 0;
    return `
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h3 class="card-title">Local Proxy Request Logs</h3>
                <p class="text-sm text-dark-400 mt-1">Metadata-only logs for non-streaming proxy requests. Prompts, request bodies, raw headers, and raw upstream responses are not stored.</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="loadRequestLogs()" class="btn btn-secondary text-xs">Refresh Logs</button>
                <button onclick="applyRequestLogRetention()" class="btn btn-ghost text-xs">Apply Retention</button>
            </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3 mt-5">
            ${renderRequestLogMetric('Requests', formatNumber(safeSummary.count || 0), `${formatNumber(safeSummary.success_count || 0)} ok · ${formatNumber(safeSummary.error_count || 0)} errors`)}
            ${renderRequestLogMetric('Cache Read', formatTokens(tokens.cache_read_tokens || 0), 'tokens served from cache')}
            ${renderRequestLogMetric('Cache Write', formatTokens(tokens.cache_creation_tokens || 0), 'tokens written to cache')}
            ${renderRequestLogMetric('Output', formatTokens(tokens.output_tokens || 0), `input ${formatTokens(tokens.input_tokens || 0)}`)}
            ${renderRequestLogMetric('Cost', formatCurrencyMap(safeSummary.cost_display_by_currency || safeSummary.cost_native_by_currency || {}), 'local estimates')}
        </div>

        <div class="grid grid-cols-1 md:grid-cols-5 gap-3 mt-5">
            <div>
                <label class="text-xs text-dark-400">Provider ID</label>
                <input id="request-log-provider" class="input mt-1 w-full" value="${escapeHtml(filters.provider_id || '')}" placeholder="all">
            </div>
            <div>
                <label class="text-xs text-dark-400">Endpoint</label>
                <input id="request-log-endpoint" class="input mt-1 w-full" value="${escapeHtml(filters.endpoint || '')}" placeholder="all">
            </div>
            <div>
                <label class="text-xs text-dark-400">Status</label>
                <select id="request-log-success" class="input mt-1 w-full">
                    <option value="" ${!filters.success ? 'selected' : ''}>All</option>
                    <option value="true" ${filters.success === 'true' ? 'selected' : ''}>Success</option>
                    <option value="false" ${filters.success === 'false' ? 'selected' : ''}>Error</option>
                </select>
            </div>
            <div>
                <label class="text-xs text-dark-400">Limit</label>
                <input id="request-log-limit" type="number" min="1" max="500" step="10" class="input mt-1 w-full" value="${escapeHtml(String(filters.limit || 50))}">
            </div>
            <div class="flex items-end">
                <button onclick="loadRequestLogs()" class="btn btn-primary w-full">Apply Filters</button>
            </div>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-3 mt-4 text-xs text-dark-500">
            <span>Showing ${formatNumber(entriesCount)} local log entries. Streaming request logging is intentionally pending until lifecycle semantics are verified.</span>
            <span>Source: <code>logs/proxy_requests.jsonl</code></span>
        </div>

        ${body}
    `;
}

function renderRequestLogMetric(label, value, sub) {
    return `
        <div class="bg-dark-900 rounded-lg p-4 border border-dark-700/80">
            <div class="card-label">${escapeHtml(label)}</div>
            <div class="card-value text-base mt-1">${escapeHtml(value)}</div>
            <div class="card-sub mt-1">${escapeHtml(sub || '')}</div>
        </div>
    `;
}

function renderRequestLogsTable(entries) {
    if (!entries.length) {
        return '<div class="py-8 text-center text-dark-400">No local proxy request logs match the current filters.</div>';
    }
    return `
        <div class="overflow-x-auto mt-4">
            <table class="w-full text-xs">
                <thead>
                    <tr class="text-dark-400 border-b border-dark-700 bg-dark-800/40">
                        <th class="text-left py-3 px-3">Time</th>
                        <th class="text-left py-3 px-3">Status</th>
                        <th class="text-left py-3 px-3">Provider / Model</th>
                        <th class="text-left py-3 px-3">Endpoint</th>
                        <th class="text-right py-3 px-3">Tokens</th>
                        <th class="text-right py-3 px-3">Cache Read</th>
                        <th class="text-right py-3 px-3">Cache Write</th>
                        <th class="text-right py-3 px-3">Cost</th>
                        <th class="text-left py-3 px-3">FX</th>
                        <th class="text-right py-3 px-3">Duration</th>
                    </tr>
                </thead>
                <tbody>
                    ${entries.map(renderRequestLogRow).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderRequestLogRow(entry) {
    const usage = entry.usage || {};
    const statusClass = entry.success ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30' : 'text-rose-300 bg-rose-500/10 border-rose-500/30';
    const statusText = entry.success ? 'OK' : (entry.error_type || 'ERROR');
    const modelLine = [entry.provider_id || entry.provider_alias || '', entry.model || entry.upstream_model || ''].filter(Boolean).join(' / ');
    const tokensText = `${formatTokens(usage.total_tokens || 0)} total`;
    const errorTitle = entry.error_message ? ` title="${escapeHtml(entry.error_message)}"` : '';
    return `
        <tr class="border-b border-dark-800/80 hover:bg-dark-800/30"${errorTitle}>
            <td class="py-3 px-3 text-dark-300 whitespace-nowrap">${escapeHtml(formatLogTimestamp(entry.timestamp))}</td>
            <td class="py-3 px-3"><span class="px-2 py-1 rounded border ${statusClass}">${escapeHtml(statusText)}</span></td>
            <td class="py-3 px-3 min-w-[180px]">
                <div class="text-white font-medium">${escapeHtml(modelLine || '-')}</div>
                <div class="text-dark-500">${escapeHtml(entry.upstream_model && entry.upstream_model !== entry.model ? entry.upstream_model : entry.api_format || '')}</div>
            </td>
            <td class="py-3 px-3 text-dark-300">${escapeHtml(entry.endpoint || '-')}</td>
            <td class="py-3 px-3 text-right font-mono text-accent-300">${escapeHtml(tokensText)}</td>
            <td class="py-3 px-3 text-right font-mono text-cyan-300">${formatTokens(usage.cache_read_tokens || 0)}</td>
            <td class="py-3 px-3 text-right font-mono text-amber-300">${formatTokens(usage.cache_creation_tokens || 0)}</td>
            <td class="py-3 px-3 text-right font-mono text-emerald-300">${escapeHtml(formatLogCost(entry.cost_estimate || {}))}</td>
            <td class="py-3 px-3 text-dark-400 whitespace-nowrap">${escapeHtml(formatLogFx(entry.fx_snapshot || (entry.cost_estimate || {}).fx_snapshot || {}))}</td>
            <td class="py-3 px-3 text-right font-mono text-dark-300">${escapeHtml(formatDuration(entry.duration_ms))}</td>
        </tr>
    `;
}

async function applyRequestLogRetention() {
    try {
        const result = await api('/api/request-logs/retention/apply', { method: 'POST' });
        const removed = result?.result?.removed_entries || 0;
        showToast(`Request log retention applied. Removed ${formatNumber(removed)} entries.`, 'success');
        await loadRequestLogs();
    } catch (err) {
        showToast('Failed to apply request log retention: ' + err.message, 'error');
    }
}

function formatCurrencyMap(map) {
    const entries = Object.entries(map || {}).filter(([, value]) => Number(value || 0) !== 0);
    if (!entries.length) return '--';
    return entries.slice(0, 2).map(([currency, amount]) => `${currency} ${formatMoney(amount)}`).join(' · ');
}

function formatLogCost(cost) {
    if (!cost || typeof cost !== 'object') return '--';
    if (cost.total_display !== undefined && cost.total_display !== null) {
        return `${cost.display_currency || ''} ${formatMoney(cost.total_display)}`.trim();
    }
    if (cost.total_native !== undefined && cost.total_native !== null) {
        return `${cost.native_currency || ''} ${formatMoney(cost.total_native)}`.trim();
    }
    return '--';
}

function formatLogFx(fx) {
    if (!fx || typeof fx !== 'object') return '--';
    const from = fx.from_currency || fx.from || '';
    const to = fx.to_currency || fx.to || '';
    const rate = Number(fx.rate);
    if (from && to && Number.isFinite(rate)) {
        const source = fx.source ? ` · ${fx.source}` : '';
        return `${from}->${to} ${formatMoney(rate)}${source}`;
    }
    return fx.success === false ? 'unavailable' : '--';
}

function formatMoney(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return '0';
    return number.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: Math.abs(number) < 1 ? 8 : 4,
    });
}

function formatDuration(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number) || number <= 0) return '--';
    return number >= 1000 ? `${formatMoney(number / 1000)}s` : `${formatMoney(number)}ms`;
}

function formatLogTimestamp(value) {
    if (!value) return '-';
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleString();
    return String(value).slice(0, 19);
}

/** Escape HTML to prevent XSS */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
