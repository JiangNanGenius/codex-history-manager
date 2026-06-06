/**
 * stats.js - Token 统计模块
 * 加载和渲染 Token Dashboard
 */

let dailyTrendChart = null;
let byModelChart = null;
let byProviderChart = null;
let hourlyChart = null;

// Chart.js 全局配置
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';
Chart.defaults.font.family = 'Inter, system-ui, sans-serif';

async function loadStats() {
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

        setStatus(`Stats loaded - ${formatTokens(overview.total_tokens)} total tokens`);
    } catch (err) {
        showToast('Failed to load stats: ' + err.message, 'error');
    }
}

function renderOverview(data) {
    document.getElementById('stat-total-tokens').textContent = formatTokens(data.total_tokens);
    document.getElementById('stat-total-sessions').textContent = formatNumber(data.total_sessions);
    document.getElementById('stat-today-tokens').textContent = formatTokens(data.today_tokens);
    document.getElementById('stat-today-sessions').textContent = formatNumber(data.today_sessions) + ' sessions';
    document.getElementById('stat-week-tokens').textContent = formatTokens(data.week_tokens);
    document.getElementById('stat-week-sessions').textContent = formatNumber(data.week_sessions) + ' sessions';
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
                    label: 'Tokens',
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
                    label: 'Sessions',
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
                            return ctx.label + ': ' + formatTokens(ctx.parsed) + ' tokens';
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
                label: 'Tokens',
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
                            return formatNumber(ctx.parsed.x) + ' tokens';
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
                label: 'Tokens',
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
                            return formatNumber(ctx.parsed.y) + ' tokens';
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
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-dark-400">No data</td></tr>';
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

/** Escape HTML to prevent XSS */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
