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
        const statsToggle = document.getElementById('stats-realtime-toggle');
        const rangeToggle = document.getElementById('range-realtime-toggle');
        const statsStatus = document.getElementById('stats-realtime-status');
        if (statsToggle) statsToggle.checked = false;
        if (rangeToggle) rangeToggle.checked = false;
        if (statsStatus) statsStatus.textContent = t('disabled');
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
        case 'stats': loadStats(); break;
        case 'sessions': loadSessions(); break;
        case 'sync': loadSyncStatus(); break;
        case 'backup': loadBackups(); break;
        case 'settings': loadSettings(); break;
    }
}

// ─────────────── Utility Functions ───────────────

/** Format large numbers with commas */
function formatNumber(n) {
    if (n === undefined || n === null) return '0';
    return Number(n).toLocaleString();
}

/** Format token count (e.g. 1.2M, 345K) */
function formatTokens(n) {
    if (n === undefined || n === null) return '0';
    n = Number(n);
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return n.toString();
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
                if (currentPage === 'sessions') loadSessions();
            }, 300);
        });
    }

    // Start with stats page
    navigateTo('stats');
});
