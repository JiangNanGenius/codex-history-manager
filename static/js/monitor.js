const TOKEN_ALERT_DEFAULT = 100000;
const REFRESH_MS = 5000;
const PROVIDER_REFRESH_MS = 15000;
const QUOTA_REFRESH_MS = 30000;
const SPEED_SAMPLE_WINDOW_MS = 600000;
const SPEED_SAMPLE_LIMIT = 120;
const BALANCE_SAMPLE_WINDOW_MS = 1800000;
const BALANCE_SAMPLE_LIMIT = 120;
const COST_SAMPLE_WINDOW_MS = 1800000;
const COST_SAMPLE_LIMIT = 120;
const MONITOR_WINDOW_WIDTH = 300;
const MONITOR_COMPACT_MIN_HEIGHT = 92;
const MONITOR_EXPANDED_MIN_HEIGHT = 228;
const MONITOR_MAX_HEIGHT = 420;
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
        speed: '速度',
        avgSpeed: '平均',
        currentSpeed: '当前',
        tokensPerMin: 'tokens/min',
        speedUnavailable: '速度: --',
        sampleCount: '采样点',
        balance: '余额',
        balanceUnavailable: '余额: --',
        balanceDecrease: '下降',
        quota: '额度',
        quotaUnavailable: '额度: --',
        quotaResetIn: '重置',
        quotaAlert: '额度接近用尽',
        fiveHour: '5小时',
        sevenDay: '7天',
        weeklyLimit: '周',
        credits: '点数',
        estimatedCost: '预计扣费',
        estimatedCostUnavailable: '预计扣费: --',
        estimatedCostRate: '速率',
        moneyPerMin: '/min',
        source: '来源',
        sourceCodex: 'Codex 记录',
        sourceCodexDb: 'Codex DB',
        sourceLocalProxy: '本地代理',
        sourceLocal: '本地复用记录',
        sourceOverlap: '来源可能重叠',
        officialUsage: '官方用量',
        usageSource: '用量来源',
        usageSourceUnavailable: '用量来源: --',
        contextLength: '上下文长度',
        contextUsage: '上下文',
        contextUnavailable: '上下文长度: 暂未匹配模型列表',
        tokenReached: 'Token 已达到',
        menuTitle: '打开菜单',
        compactTitle: '折叠',
        compact: '折叠 / 展开',
        refresh: '立即刷新',
        hide: '隐藏悬浮窗',
        main: '显示主窗口',
        settings: '打开设置',
        start: '启动 Codex',
        exit: '退出程序',
        quickSwitchProvider: '快速切换供应商',
        autoProvider: '自动选择供应商',
        noProviders: '暂无可切换供应商',
        loadingProviders: '正在加载...',
        providerSwitched: '已切换供应商',
        providerSwitchFailed: '切换失败',
    },
    en: {
        title: 'Token Monitor',
        lastHourUsage: 'Last hour usage',
        trackingDelta: 'Current tracking change',
        reuseUnavailable: 'Reused: -- · new: -- · total: -- · not supported',
        reused: 'Reused',
        newReusable: 'New',
        totalSaved: 'Total',
        speed: 'Speed',
        avgSpeed: 'avg',
        currentSpeed: 'now',
        tokensPerMin: 'tokens/min',
        speedUnavailable: 'Speed: --',
        sampleCount: 'samples',
        balance: 'Balance',
        balanceUnavailable: 'Balance: --',
        balanceDecrease: 'burn',
        quota: 'Quota',
        quotaUnavailable: 'Quota: --',
        quotaResetIn: 'reset',
        quotaAlert: 'quota nearly exhausted',
        fiveHour: '5h',
        sevenDay: '7d',
        weeklyLimit: 'week',
        credits: 'credits',
        estimatedCost: 'Estimated cost',
        estimatedCostUnavailable: 'Estimated cost: --',
        estimatedCostRate: 'rate',
        moneyPerMin: '/min',
        source: 'Source',
        sourceCodex: 'Codex records',
        sourceCodexDb: 'Codex DB',
        sourceLocalProxy: 'Local proxy',
        sourceLocal: 'Local reuse history',
        sourceOverlap: 'sources may overlap',
        officialUsage: 'Official usage',
        usageSource: 'Usage source',
        usageSourceUnavailable: 'Usage source: --',
        contextLength: 'Context length',
        contextUsage: 'Context',
        contextUnavailable: 'Context length: model list not matched yet',
        tokenReached: 'Token reached',
        menuTitle: 'Open menu',
        compactTitle: 'Collapse',
        compact: 'Collapse / Expand',
        refresh: 'Refresh now',
        hide: 'Hide monitor',
        main: 'Show main window',
        settings: 'Open settings',
        start: 'Start Codex',
        exit: 'Exit app',
        quickSwitchProvider: 'Quick switch provider',
        autoProvider: 'Auto select provider',
        noProviders: 'No switchable providers',
        loadingProviders: 'Loading...',
        providerSwitched: 'Provider switched',
        providerSwitchFailed: 'Switch failed',
    },
};

let lastAlertBucket = 0;
let monitorSettings = null;
let providerFocus = { providers: [], focus_provider_id: '' };
let speedSamples = [];
let balanceSamples = [];
let costSamples = [];
let providerQuotaSnapshot = null;
let lastQuotaRefreshAt = 0;
let lastQuotaAlertBuckets = {};
let lastStatsData = {};

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
            { value: 10000, suffix: '万' },
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

async function api(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
}

async function loadMonitorSettings() {
    try {
        monitorSettings = await api('/api/settings');
    } catch {
        monitorSettings = {};
    }
}

async function loadQuickProviders() {
    if (window.pywebview?.api?.list_quick_providers) {
        providerFocus = await window.pywebview.api.list_quick_providers();
    } else {
        providerFocus = await api('/api/providers/focus');
    }
    renderProviderMenu();
}

function monitorFields() {
    return (monitorSettings && monitorSettings.monitor_fields) || {
        tokens: true,
        progress: true,
        threshold: true,
        speed: true,
        balance: true,
        cache: true,
        context_window: true,
        updated_at: true,
    };
}

function focusedProviderId() {
    const explicit = String(providerFocus.focus_provider_id || '');
    if (explicit) return explicit;
    const focused = (providerFocus.providers || []).find(provider => provider && provider.focused);
    return focused ? String(focused.id || '') : '';
}

function focusedProvider() {
    const id = focusedProviderId();
    return (providerFocus.providers || []).find(provider => String(provider.id || '') === id) || null;
}

function isOfficialFocusProvider(provider) {
    return Boolean(provider && (provider.switch_only || provider.codex_login || provider.id === 'codex_official'));
}

async function loadFocusedQuota(force = false) {
    const providerId = focusedProviderId();
    const provider = focusedProvider();
    const now = Date.now();
    if (isOfficialFocusProvider(provider) || lastStatsData?.official_usage_default) {
        if (!force && providerQuotaSnapshot && now - lastQuotaRefreshAt < QUOTA_REFRESH_MS) {
            return providerQuotaSnapshot;
        }
        const snapshot = await api('/api/official/quota/refresh', {
            method: 'POST',
            body: JSON.stringify({ force: Boolean(force) }),
        });
        lastQuotaRefreshAt = now;
        providerQuotaSnapshot = snapshot;
        return snapshot;
    }
    if (!providerId) {
        providerQuotaSnapshot = null;
        balanceSamples = [];
        return null;
    }
    if (isOfficialFocusProvider(provider)) {
        providerQuotaSnapshot = null;
        balanceSamples = [];
        costSamples = [];
        return null;
    }
    if (!force && providerQuotaSnapshot && now - lastQuotaRefreshAt < QUOTA_REFRESH_MS) {
        return providerQuotaSnapshot;
    }
    const snapshot = await api('/api/providers/' + encodeURIComponent(providerId) + '/quota/refresh', {
        method: 'POST',
        body: JSON.stringify({ force: false }),
    });
    lastQuotaRefreshAt = now;
    providerQuotaSnapshot = snapshot;
    return snapshot;
}

function firstNumber(values, keys) {
    for (const key of keys) {
        const raw = values && values[key];
        if (raw === undefined || raw === null || raw === '') continue;
        const parsed = Number(raw);
        if (Number.isFinite(parsed)) return parsed;
    }
    return null;
}

function firstText(values, keys) {
    for (const key of keys) {
        const raw = values && values[key];
        if (raw === undefined || raw === null || raw === '') continue;
        return String(raw);
    }
    return '';
}

function clampPercent(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    return Math.max(0, Math.min(parsed, 100));
}

function tierDisplayName(name) {
    const key = String(name || '').toLowerCase();
    if (key === 'five_hour' || key === '5_hour' || key === '5h') return mt('fiveHour');
    if (key === 'seven_day' || key === '7_day' || key === '7d') return mt('sevenDay');
    if (key === 'weekly_limit' || key === 'week' || key === 'weekly') return mt('weeklyLimit');
    if (key === 'credits' || key === 'credit') return mt('credits');
    if (!key || key === 'unknown') return mt('quota');
    return String(name).replace(/_/g, ' ');
}

function tierUtilization(tier = {}) {
    let utilization = clampPercent(tier.utilization ?? tier.used_percent ?? tier.usedPercent ?? tier.quota_percent ?? tier.quotaPercent);
    if (utilization === null) {
        const remaining = clampPercent(tier.remaining_percent ?? tier.remainingPercent);
        if (remaining !== null) utilization = 100 - remaining;
    }
    return utilization;
}

function quotaPercentSnapshot(snapshot) {
    if (!snapshot || snapshot.success === false) return null;
    const values = snapshot.values && typeof snapshot.values === 'object' ? snapshot.values : {};
    const rawTiers = Array.isArray(values.tiers)
        ? values.tiers
        : (Array.isArray(values.quota_tiers) ? values.quota_tiers : []);
    const tiers = rawTiers.map((tier, index) => {
        const utilization = tierUtilization(tier);
        if (utilization === null) return null;
        return {
            name: tierDisplayName(tier.name || tier.tier || tier.label || (index ? 'tier' : 'quota')),
            utilization,
            resetsAt: tier.resets_at || tier.resetsAt || tier.reset_at || tier.resetTime || '',
        };
    }).filter(Boolean);
    if (!tiers.length) {
        const flat = clampPercent(values.quota_percent ?? values.quotaPercent ?? values.utilization ?? values.used_percent ?? values.usedPercent);
        const remaining = clampPercent(values.remaining_percent ?? values.remainingPercent);
        const utilization = flat !== null ? flat : (remaining !== null ? 100 - remaining : null);
        if (utilization !== null) {
            tiers.push({
                name: mt('quota'),
                utilization,
                resetsAt: values.resets_at || values.resetsAt || values.reset_at || values.resetTime || '',
            });
        }
    }
    if (!tiers.length) return null;
    const maxUtilization = tiers.reduce((max, tier) => Math.max(max, tier.utilization), 0);
    return {
        tiers,
        maxUtilization,
        warning: maxUtilization >= 70,
        danger: maxUtilization >= 90,
        providerId: snapshot.provider_id || '',
        type: snapshot.type || snapshot.probe_type || '',
    };
}

function formatResetTime(value) {
    if (!value) return '';
    const ts = Date.parse(String(value));
    if (!Number.isFinite(ts)) return '';
    const deltaMinutes = Math.round((ts - Date.now()) / 60000);
    if (deltaMinutes <= 0) return '';
    if (deltaMinutes < 60) return `${deltaMinutes}m`;
    const hours = Math.round(deltaMinutes / 60);
    if (hours < 48) return `${hours}h`;
    return `${Math.round(hours / 24)}d`;
}

function formatQuotaLine(snapshot) {
    const parsed = quotaPercentSnapshot(snapshot);
    if (!parsed) return '';
    const parts = parsed.tiers.slice(0, 3).map(tier => {
        const reset = formatResetTime(tier.resetsAt);
        const resetText = reset ? ` ${mt('quotaResetIn')} ${reset}` : '';
        return `${tier.name} ${tier.utilization.toFixed(0)}%${resetText}`;
    });
    return `${mt('quota')}: ${parts.join(' · ')}`;
}

function quotaBalanceSnapshot(snapshot) {
    if (!snapshot || snapshot.success === false) return null;
    const values = snapshot.values && typeof snapshot.values === 'object' ? snapshot.values : {};
    const balance = firstNumber(values, [
        'balance',
        'remaining',
        'remaining_balance',
        'available_balance',
        'available',
        'quota_remaining',
        'credits_remaining',
    ]);
    const spent = firstNumber(values, [
        'spent',
        'used',
        'used_cost',
        'consumed',
        'total_cost',
        'cost',
        'charges',
    ]);
    const currency = firstText(values, ['currency', 'unit', 'native_currency']);
    if (balance === null && spent === null) return null;
    return { balance, spent, currency };
}

function rememberBalanceSample(snapshot) {
    const parsed = quotaBalanceSnapshot(snapshot);
    if (!parsed) return null;
    const now = Date.now();
    balanceSamples.push({ ts: now, ...parsed });
    balanceSamples = balanceSamples
        .filter(sample => now - sample.ts <= BALANCE_SAMPLE_WINDOW_MS)
        .slice(-BALANCE_SAMPLE_LIMIT);
    if (balanceSamples.length < 2) return { ...parsed, burn: null, samples: balanceSamples.length };
    const compatible = balanceSamples.filter(sample => sample.currency === parsed.currency);
    const usable = compatible.length >= 2 ? compatible : balanceSamples;
    const first = usable[0];
    const last = usable[usable.length - 1];
    const elapsedMinutes = Math.max((last.ts - first.ts) / 60000, 0);
    if (elapsedMinutes <= 0) return { ...parsed, burn: null, samples: usable.length };
    let burn = null;
    if (first.balance !== null && last.balance !== null) {
        burn = Math.max((first.balance - last.balance) / elapsedMinutes, 0);
    } else if (first.spent !== null && last.spent !== null) {
        burn = Math.max((last.spent - first.spent) / elapsedMinutes, 0);
    }
    return { ...parsed, burn, samples: usable.length };
}

function currencyPrefix(currency) {
    const code = String(currency || '').toUpperCase();
    if (code === 'CNY' || code === 'RMB' || code === '¥') return '¥';
    if (code === 'USD' || code === '$') return '$';
    return code ? code + ' ' : '';
}

function formatMoney(value, currency) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
    const amount = Number(value);
    const digits = Math.abs(amount) >= 100 ? 2 : 4;
    return currencyPrefix(currency) + amount.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: digits,
    });
}

function formatBalanceLine(snapshot) {
    const quotaLine = formatQuotaLine(snapshot);
    const sample = rememberBalanceSample(snapshot);
    if (!sample) return quotaLine || mt('balanceUnavailable');
    const primary = sample.balance !== null
        ? `${mt('balance')}: ${formatMoney(sample.balance, sample.currency)}`
        : `${mt('balanceDecrease')}: ${formatMoney(sample.spent, sample.currency)}`;
    const burn = sample.burn === null || sample.burn === undefined
        ? ''
        : ` · ${mt('balanceDecrease')} ${formatMoney(sample.burn, sample.currency)}${mt('moneyPerMin')}`;
    return `${quotaLine ? quotaLine + ' · ' : ''}${primary}${burn} · ${mt('sampleCount')}: ${sample.samples}`;
}

function firstCostSnapshot(data) {
    const summary = data && data.local_proxy_request_log && typeof data.local_proxy_request_log === 'object'
        ? data.local_proxy_request_log
        : {};
    const costs = summary.effective_cost_by_currency && typeof summary.effective_cost_by_currency === 'object'
        ? summary.effective_cost_by_currency
        : {};
    const preferred = ['CNY', 'RMB', 'USD'];
    const currencies = [
        ...preferred.filter(currency => Object.prototype.hasOwnProperty.call(costs, currency)),
        ...Object.keys(costs).filter(currency => !preferred.includes(String(currency).toUpperCase())),
    ];
    for (const currency of currencies) {
        const amount = Number(costs[currency]);
        if (Number.isFinite(amount) && amount > 0) {
            const counts = summary.effective_cost_source_counts && typeof summary.effective_cost_source_counts === 'object'
                ? summary.effective_cost_source_counts
                : {};
            return {
                amount,
                currency,
                providerReported: Number(counts.provider_reported || 0),
                localEstimate: Number(counts.local_estimate || 0),
            };
        }
    }
    return null;
}

function rememberCostSample(data) {
    const parsed = firstCostSnapshot(data);
    if (!parsed) return null;
    const now = Date.now();
    costSamples.push({ ts: now, ...parsed });
    costSamples = costSamples
        .filter(sample => now - sample.ts <= COST_SAMPLE_WINDOW_MS)
        .slice(-COST_SAMPLE_LIMIT);
    const compatible = costSamples.filter(sample => sample.currency === parsed.currency);
    const usable = compatible.length >= 2 ? compatible : costSamples;
    if (usable.length < 2) return { ...parsed, burn: null, samples: usable.length };
    const first = usable[0];
    const last = usable[usable.length - 1];
    const elapsedMinutes = Math.max((last.ts - first.ts) / 60000, 0);
    if (elapsedMinutes <= 0) return { ...parsed, burn: null, samples: usable.length };
    const burn = Math.max((last.amount - first.amount) / elapsedMinutes, 0);
    return { ...parsed, burn, samples: usable.length };
}

function formatEstimatedCostLine(data) {
    const sample = rememberCostSample(data);
    if (!sample) return mt('estimatedCostUnavailable');
    const rate = sample.burn === null || sample.burn === undefined
        ? ''
        : ` · ${mt('estimatedCostRate')} ${formatMoney(sample.burn, sample.currency)}${mt('moneyPerMin')}`;
    return `${mt('estimatedCost')}: ${formatMoney(sample.amount, sample.currency)}${rate} · ${mt('sampleCount')}: ${sample.samples}`;
}

function usageSourceLabel(source) {
    if (source === 'codex_rollout') return mt('sourceCodex');
    if (source === 'codex_db') return mt('sourceCodexDb');
    if (source === 'local_proxy_request_log') return mt('sourceLocalProxy');
    return source || '';
}

function formatOfficialUsageLine(data) {
    if (!data) return mt('usageSourceUnavailable');
    const total = Number(data.total_tokens || 0);
    const source = usageSourceLabel(data.data_source || '');
    const activeBadges = Array.isArray(data.usage_source_badges)
        ? data.usage_source_badges
            .filter(badge => badge && badge.active)
            .map(badge => usageSourceLabel(badge.id) || badge.label)
            .filter(Boolean)
        : [];
    const labels = Array.from(new Set([source, ...activeBadges].filter(Boolean)));
    if (!labels.length && total <= 0) return mt('usageSourceUnavailable');
    const sourceText = labels.length ? ` · ${mt('usageSource')}: ${labels.join(' + ')}` : '';
    return `${mt('officialUsage')}: ${formatCompact(total)}${sourceText}`;
}

function rememberSpeedSample(totalTokens) {
    const now = Date.now();
    const total = Number(totalTokens || 0);
    if (!Number.isFinite(total)) return null;
    speedSamples.push({ ts: now, total });
    speedSamples = speedSamples
        .filter(sample => now - sample.ts <= SPEED_SAMPLE_WINDOW_MS)
        .slice(-SPEED_SAMPLE_LIMIT);
    if (speedSamples.length < 2) return null;
    const first = speedSamples[0];
    const last = speedSamples[speedSamples.length - 1];
    const elapsedMinutes = Math.max((last.ts - first.ts) / 60000, 0);
    if (elapsedMinutes <= 0) return null;
    const avg = Math.max((last.total - first.total) / elapsedMinutes, 0);
    const previous = speedSamples[speedSamples.length - 2];
    const instantMinutes = Math.max((last.ts - previous.ts) / 60000, 0);
    const instant = instantMinutes > 0 ? Math.max((last.total - previous.total) / instantMinutes, 0) : avg;
    return { avg, instant, samples: speedSamples.length };
}

function formatSpeedLine(speed) {
    if (!speed) return mt('speedUnavailable');
    return `${mt('speed')}: ${mt('avgSpeed')} ${formatCompact(speed.avg)} / ${mt('currentSpeed')} ${formatCompact(speed.instant)} ${mt('tokensPerMin')}`;
}

function oneHourQuery() {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 60 * 1000);
    const params = new URLSearchParams({
        start: start.toISOString(),
        end: end.toISOString(),
        granularity: 'total',
        rollout_total_source: '1',
        rollout_scan_fallback: '1',
    });
    return `/api/token/current?${params.toString()}`;
}

function currentUsageQuery() {
    const params = new URLSearchParams({
        rollout_total_source: '1',
        rollout_scan_fallback: '1',
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
        data = await api(currentUsageQuery());
        value = Math.max(Number(data.total_tokens || 0) - Number(state.baseTotalTokens || 0), 0);
        mode = mt('trackingDelta');
    } else {
        data = await api(oneHourQuery());
        value = Number(data.total_tokens || 0);
    }
    lastStatsData = data || {};

    let quota = data.quota || providerQuotaSnapshot;
    if (data.quota) providerQuotaSnapshot = data.quota;
    try {
        quota = await loadFocusedQuota(false);
    } catch {
        quota = providerQuotaSnapshot;
    }

    render(value, threshold, mode, data, quota);
}

function render(value, threshold, mode, data, quota) {
    const card = document.getElementById('monitor-card');
    const valueEl = document.getElementById('monitor-value');
    const modeEl = document.getElementById('monitor-mode');
    const fillEl = document.getElementById('monitor-fill');
    const thresholdEl = document.getElementById('monitor-threshold');
    const updatedEl = document.getElementById('monitor-updated');
    const cacheEl = document.getElementById('monitor-cache');
    const contextEl = document.getElementById('monitor-context');
    const speedEl = document.getElementById('monitor-speed');
    const balanceEl = document.getElementById('monitor-balance');
    const quotaMeterEl = document.getElementById('monitor-quota-meter');
    const quotaFillEl = document.getElementById('monitor-quota-fill');
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

    const speed = rememberSpeedSample(data.total_tokens || value);
    speedEl.textContent = formatSpeedLine(speed);
    speedEl.title = speed
        ? `${mt('currentSpeed')}: ${formatCompact(speed.instant)} ${mt('tokensPerMin')} · ${mt('sampleCount')}: ${speed.samples}`
        : speedEl.textContent;
    cacheEl.textContent = formatCacheLine(data);
    cacheEl.title = cacheEl.textContent;
    const provider = focusedProvider();
    const showOfficialUsage = isOfficialFocusProvider(provider) || data.official_usage_default;
    const quotaInfo = quotaPercentSnapshot(quota);
    const quotaLine = quota && quota.success !== false ? formatBalanceLine(quota) : '';
    balanceEl.textContent = quotaLine || (showOfficialUsage ? formatOfficialUsageLine(data) : formatEstimatedCostLine(data));
    balanceEl.title = balanceEl.textContent;
    balanceEl.classList.toggle('quota-warning', Boolean(quotaInfo && quotaInfo.warning && !quotaInfo.danger));
    balanceEl.classList.toggle('quota-danger', Boolean(quotaInfo && quotaInfo.danger));
    if (quotaMeterEl && quotaFillEl) {
        const showMeter = Boolean(quotaInfo && fields.balance);
        quotaMeterEl.style.display = showMeter ? 'block' : 'none';
        quotaMeterEl.classList.toggle('warning', Boolean(quotaInfo && quotaInfo.warning && !quotaInfo.danger));
        quotaMeterEl.classList.toggle('danger', Boolean(quotaInfo && quotaInfo.danger));
        quotaFillEl.style.width = showMeter ? `${quotaInfo.maxUtilization.toFixed(1)}%` : '0%';
        quotaMeterEl.title = balanceEl.textContent;
    }
    const contextWindow = Number(data.current_context_window || 0);
    const contextUsed = Number(data.current_context_used_tokens || 0);
    if (contextWindow && contextUsed) {
        const contextPct = Math.min(Math.max((contextUsed / contextWindow) * 100, 0), 100);
        contextEl.textContent = `${mt('contextUsage')}: ${formatCompact(contextUsed)} / ${formatCompact(contextWindow)} (${contextPct.toFixed(1)}%)`;
    } else {
        contextEl.textContent = contextWindow
            ? `${mt('contextLength')}: ${formatCompact(contextWindow)} · ${data.current_model || '-'}`
            : mt('contextUnavailable');
    }

    document.querySelector('.value-row').style.display = fields.tokens ? 'flex' : 'none';
    progressEl.style.display = fields.progress ? 'block' : 'none';
    thresholdEl.style.display = fields.threshold ? 'inline' : 'none';
    updatedEl.style.display = fields.updated_at ? 'inline' : 'none';
    speedEl.style.display = fields.speed ? 'block' : 'none';
    balanceEl.style.display = fields.balance ? 'block' : 'none';
    cacheEl.style.display = fields.cache ? 'block' : 'none';
    contextEl.style.display = fields.context_window ? 'block' : 'none';

    maybeAlert(value, threshold);
    maybeQuotaAlert(quotaInfo, quota);
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

function maybeQuotaAlert(quotaInfo, snapshot) {
    if (!quotaInfo || !quotaInfo.danger) return;
    const providerId = snapshot?.provider_id || quotaInfo.providerId || 'quota';
    const dangerous = quotaInfo.tiers.filter(tier => tier.utilization >= 90);
    for (const tier of dangerous) {
        const bucket = `${providerId}:${tier.name}:90`;
        if (lastQuotaAlertBuckets[bucket]) continue;
        lastQuotaAlertBuckets[bucket] = Date.now();
        if (window.pywebview?.api?.notify_monitor_alert) {
            window.pywebview.api.notify_monitor_alert(`${mt('quotaAlert')}: ${tier.name} ${tier.utilization.toFixed(0)}%`);
        }
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

function openContextMenuAt(x, y) {
    loadQuickProviders().catch(() => renderProviderMenu(true));
    const menu = document.getElementById('context-menu');
    const rectWidth = Math.min(230, window.innerWidth - 12);
    menu.style.left = `${Math.max(6, Math.min(x, window.innerWidth - rectWidth - 6))}px`;
    menu.style.top = `${Math.max(6, Math.min(y, window.innerHeight - 260))}px`;
    menu.classList.add('open');
}

function showContextMenu(event) {
    event.preventDefault();
    openContextMenuAt(event.clientX, event.clientY);
}

function showButtonMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    openContextMenuAt(rect.right - 216, rect.bottom + 6);
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

function renderProviderMenu(failed = false) {
    const root = document.getElementById('provider-menu-items');
    if (!root) return;
    const providers = Array.isArray(providerFocus.providers) ? providerFocus.providers : [];
    if (failed) {
        root.innerHTML = `<button class="muted" disabled>${mt('providerSwitchFailed')}</button>`;
        return;
    }
    if (!providers.length) {
        root.innerHTML = `<button data-provider-id="" class="${providerFocus.focus_provider_id ? '' : 'active'}">${providerFocus.focus_provider_id ? '' : '✓ '}${mt('autoProvider')}</button>
            <button class="muted" disabled>${mt('noProviders')}</button>`;
        return;
    }
    const autoActive = !providerFocus.focus_provider_id;
    const autoButton = `<button data-provider-id="" class="${autoActive ? 'active' : ''}">
        <span>${autoActive ? '✓ ' : ''}${mt('autoProvider')}</span>
    </button>`;
    root.innerHTML = autoButton + providers.map(provider => {
        const id = String(provider.id || '');
        const label = provider.display_name || id;
        const alias = provider.short_alias ? ` (${provider.short_alias})` : '';
        const active = provider.focused || id === providerFocus.focus_provider_id;
        return `
            <button data-provider-id="${escapeAttr(id)}" class="${active ? 'active' : ''}">
                <span>${active ? '✓ ' : ''}${escapeHtml(label + alias)}</span>
            </button>
        `;
    }).join('');
}

async function switchProvider(providerId) {
    providerId = String(providerId || '');
    if (window.pywebview?.api?.switch_provider) {
        const result = await window.pywebview.api.switch_provider(providerId);
        if (!result || result.success === false) throw new Error((result && result.error) || mt('providerSwitchFailed'));
    } else {
        await api('/api/providers/focus', {
            method: 'POST',
            body: JSON.stringify({ provider_id: providerId }),
        });
    }
    providerQuotaSnapshot = null;
    balanceSamples = [];
    costSamples = [];
    lastQuotaRefreshAt = 0;
    await loadQuickProviders();
    await loadFocusedQuota(true).catch(() => {});
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
    if (action === 'settings' && window.pywebview?.api?.show_settings) {
        await window.pywebview.api.show_settings();
    }
    if (action === 'start' && window.pywebview?.api?.start_codex) {
        await window.pywebview.api.start_codex();
    }
    if (action === 'exit' && window.pywebview?.api?.exit_app) {
        await window.pywebview.api.exit_app();
    }
}

function applyMonitorCopy() {
    document.documentElement.lang = monitorLang === 'en' ? 'en' : 'zh-CN';
    document.title = mt('title');
    const kicker = document.querySelector('.monitor-kicker');
    if (kicker) kicker.textContent = mt('title');
    const compactBtn = document.getElementById('compact-btn');
    if (compactBtn) compactBtn.title = mt('compactTitle');
    const menuBtn = document.getElementById('menu-btn');
    if (menuBtn) menuBtn.title = mt('menuTitle');
    document.querySelectorAll('#context-menu [data-action]').forEach(btn => {
        const action = btn.getAttribute('data-action');
        const text = mt(action);
        if (text !== action) btn.textContent = text;
    });
    document.querySelectorAll('#context-menu [data-copy]').forEach(el => {
        el.textContent = mt(el.getAttribute('data-copy'));
    });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#096;');
}

window.addEventListener('DOMContentLoaded', () => {
    applyMonitorCopy();
    setCompact(localStorage.getItem('desktop_token_monitor_compact') === 'true');
    document.getElementById('menu-btn').addEventListener('click', showButtonMenu);
    document.getElementById('compact-btn').addEventListener('click', toggleCompact);
    document.addEventListener('contextmenu', showContextMenu);
    document.addEventListener('click', hideContextMenu);
    document.getElementById('context-menu').addEventListener('click', (event) => {
        const providerButton = event.target.closest('button[data-provider-id]');
        if (providerButton) {
            event.stopPropagation();
            switchProvider(providerButton.dataset.providerId).catch(console.error);
            return;
        }
        const button = event.target.closest('button[data-action]');
        if (button) runMenuAction(button.dataset.action).catch(console.error);
    });
    loadMonitorSettings().finally(() => refreshMonitor().catch(console.error));
    loadQuickProviders().catch(() => renderProviderMenu(true));
    setInterval(() => refreshMonitor().catch(console.error), REFRESH_MS);
    setInterval(() => loadQuickProviders().catch(() => {}), PROVIDER_REFRESH_MS);
    window.addEventListener('resize', scheduleMonitorResize);
});
