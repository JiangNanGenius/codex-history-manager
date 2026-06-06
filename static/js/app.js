/**
 * app.js - 主应用逻辑
 * SPA 路由、通用工具函数、Toast 通知
 */

// ─────────────── SPA Navigation ───────────────

let currentPage = 'stats';

function navigateTo(page) {
    currentPage = page;
    if (page !== 'stats' && typeof realtimeController !== 'undefined') {
        realtimeController.stop('stats-dashboard');
        realtimeController.stop('range-stats');
    }
    // Hide all pages
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    // Show target page
    const target = document.getElementById('page-' + page);
    if (target) target.classList.add('active');
    // Update nav buttons
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.nav === page);
    });
    // Load page data
    switch (page) {
        case 'stats':
            loadStats();
            // Stats refreshes continuously while the page is active.
            setTimeout(() => {
                if (typeof startStatsRealtime === 'function') startStatsRealtime();
                if (typeof startRangeRealtime === 'function') startRangeRealtime();
            }, 100);
            break;
        case 'sessions': loadSessions(); break;
        case 'sync': loadSyncStatus(); break;
        case 'backup': loadBackups(); break;
        case 'settings': loadSettings(); break;
    }
}

// ─────────────── Utility Functions ───────────────

/** Format large numbers with commas */
function formatNumber(n, options = {}) {
    if (n === undefined || n === null) return '0';
    const value = Number(n);
    if (!Number.isFinite(value)) return '0';
    if (options.compact === false) {
        return value.toLocaleString();
    }
    return formatCompactNumber(value);
}

/** Format large numbers into readable K/M/B or 千/百万/亿 units. */
function formatCompactNumber(value) {
    const abs = Math.abs(value);
    const zh = typeof currentLang !== 'undefined' && currentLang === 'zh';
    const units = zh
        ? [
            { value: 100000000, suffix: '亿' },
            { value: 1000000, suffix: '百万' },
            { value: 1000, suffix: '千' },
        ]
        : [
            { value: 1000000000, suffix: 'B' },
            { value: 1000000, suffix: 'M' },
            { value: 1000, suffix: 'K' },
        ];

    for (const unit of units) {
        if (abs >= unit.value) {
            const scaled = value / unit.value;
            const digits = Math.abs(scaled) < 10 ? 2 : 1;
            return trimNumber(scaled, digits) + unit.suffix;
        }
    }

    return Math.round(value).toLocaleString();
}

function trimNumber(value, maxDigits = 2) {
    return Number(value.toFixed(maxDigits)).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: maxDigits,
    });
}

/** Format token count (e.g. 1.2M, 345K) */
function formatTokens(n) {
    return formatNumber(n);
}

/** Format unix timestamp to date string */
function formatDate(ts) {
    if (!ts) return '-';
    const d = new Date(Number(ts) * 1000);
    if (isNaN(d.getTime())) {
        // Try as milliseconds
        const d2 = new Date(Number(ts));
        if (isNaN(d2.getTime())) return String(ts).slice(0, 10);
        return d2.toLocaleDateString('zh-CN');
    }
    return d.toLocaleDateString('zh-CN');
}

/** Format unix timestamp to datetime string */
function formatDateTime(ts) {
    if (!ts) return '-';
    const d = new Date(Number(ts) * 1000);
    if (isNaN(d.getTime())) {
        const d2 = new Date(Number(ts));
        if (isNaN(d2.getTime())) return String(ts).slice(0, 19);
        return d2.toLocaleString('zh-CN');
    }
    return d.toLocaleString('zh-CN');
}

/** Fetch API wrapper with error handling */
async function api(url, options = {}) {
    try {
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.error || `HTTP ${resp.status}`);
        }
        return data;
    } catch (err) {
        console.error(`API Error: ${url}`, err);
        throw err;
    }
}

/** Set status bar text */
function setStatus(text) {
    const el = document.getElementById('status-text');
    if (el) el.textContent = text;
}

// ─────────────── Toast Notification ───────────────

let toastTimer = null;

function showToast(message, type = 'info', duration = 3000) {
    const toast = document.getElementById('toast');
    const inner = document.getElementById('toast-inner');
    if (!toast || !inner) return;

    // Clear existing timer
    if (toastTimer) clearTimeout(toastTimer);

    inner.textContent = message;
    inner.className = `px-4 py-3 rounded-lg shadow-lg text-sm font-medium toast-${type}`;

    // Show
    toast.classList.remove('translate-y-20', 'opacity-0');
    toast.classList.add('translate-y-0', 'opacity-100');

    // Auto-hide
    toastTimer = setTimeout(() => {
        toast.classList.add('translate-y-20', 'opacity-0');
        toast.classList.remove('translate-y-0', 'opacity-100');
    }, duration);
}

// ─────────────── Init ───────────────

document.addEventListener('DOMContentLoaded', () => {
    // Search debounce
    const searchInput = document.getElementById('session-search');
    if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                if (currentPage === 'sessions') resetSessionsAndLoad();
            }, 300);
        });
    }

    // Start with stats page
    navigateTo('stats');
});
