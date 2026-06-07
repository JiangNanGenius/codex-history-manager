/**
 * app.js - 主应用逻辑
 * SPA 路由、通用工具函数、Toast 通知、动画辅助函数
 *
 * 设计意图：
 *   - 作为前端 SPA 的「基础设施层」：提供路由、API 封装、数字格式化、
 *     Toast、动画等通用能力，供 providers.js、stats.js、sessions.js 等页面模块调用。
 *   - 与页面逻辑解耦：app.js 不感知具体业务（如 provider registry），
 *     只提供底层工具，便于复用和独立测试。
 *
 * 工程权衡：
 *   - 不使用前端框架（React/Vue）：项目为单文件桌面应用，引入框架会显著
 *     增加打包体积和启动时间。原生 JS + innerHTML 在当前字段数量下完全够用。
 *   - navigateTo 使用 CSS class 切换而非真正的浏览器 history API：
 *     因为应用运行在 PyWebView 内嵌窗口中，没有地址栏和前进/后退按钮，
 *     history API 意义不大；class 切换更简单可靠。
 */

// ─────────────── SPA Navigation ───────────────

let currentPage = 'overview';

/**
 * SPA 路由切换。
 *
 * 动画时序说明：
 *   1. 先移除所有 .page 的 .active，旧页面通过 CSS transition 淡出。
 *   2. 给目标页添加 .active，新页面通过 CSS transition 从 translateY(14px)
 *      scale(0.985) 滑入。
 *   3. 50ms 后调用 triggerStaggerAnimations(target) 触发内部 stagger-item
 *      与 .card 的交错进入。该延迟覆盖大多数 innerHTML 重绘场景；
 *      若异步加载较慢，各 render 函数末尾会再次调用，确保不遗漏。
 *   4. attachRippleToButtons 同步绑定，防止 innerHTML 刷新后 ripple 丢失。
 *
 * @param {string} page - 目标页面标识，对应 HTML 中 id="page-{page}"
 */
function navigateTo(page) {
    /**
     * SPA 路由切换。
     *
     * 动画时序说明：
     *   1. 先移除所有 .page 的 .active，旧页面通过 CSS transition 淡出。
     *   2. 给目标页添加 .active，新页面通过 CSS transition 从 translateY(14px)
     *      scale(0.985) 滑入。
     *   3. 50ms 后调用 triggerStaggerAnimations(target) 触发内部 stagger-item
     *      与 .card 的交错进入。该延迟覆盖大多数 innerHTML 重绘场景；
     *      若异步加载较慢，各 render 函数末尾会再次调用，确保不遗漏。
     *   4. attachRippleToButtons 同步绑定，防止 innerHTML 刷新后 ripple 丢失。
     *
     * 边界条件：
     *   - 离开 stats 页面时停止实时轮询：避免后台继续请求 /api/token/current
     *     浪费资源。
     *   - 若 target DOM 不存在（如页面模块未加载），静默返回，不抛异常。
     *
     * @param {string} page - 目标页面标识，对应 HTML 中 id="page-{page}"
     */
    currentPage = page;
    if (page !== 'stats' && typeof realtimeController !== 'undefined') {
        realtimeController.stop('stats-dashboard');
        realtimeController.stop('range-stats');
    }
    // 1) 先隐藏所有页面，旧页面通过 CSS transition 淡出
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    // 2) 显示目标页，新页面通过 CSS transition 滑入
    const target = document.getElementById('page-' + page);
    if (target) {
        target.classList.add('active');
        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        document.querySelector('main')?.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        // 3) 延迟触发内部交错动画与 ripple 绑定
        setTimeout(() => {
            triggerStaggerAnimations(target);
            attachRippleToButtons(target);
        }, 50);
    }
    // Update nav buttons
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.nav === page);
    });
    // Load page data
    switch (page) {
        case 'overview': loadEnhanceOverview(); break;
        case 'quick-setup': loadQuickSetup(); break;
        case 'providers': loadProvidersPage(); break;
        case 'amr': loadAmrPage(); break;
        case 'codex-integration': loadCodexIntegrationPage(); break;
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
        case 'diagnostics': loadDiagnosticsPage(); break;
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

/** Counts stay exact so labels like total sessions never become 2.38K/千. */
function formatCount(n) {
    return formatNumber(n, { compact: false });
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

/**
 * 显示 Toast 通知。
 *
 * 动画机制：
 *   - 显示时添加 .show 类，触发 CSS keyframe toast-in（translateY + scale
 *     bounce 弹入），并应用 toast-{type} 渐变背景。
 *   - 隐藏时替换为 .hide 类，触发 toast-out 平滑下坠消失。
 *   - 连续调用会自动清除旧定时器，防止多个 toast 堆叠或过早消失。
 *
 * @param {string} message - 通知文本
 * @param {string} type    - 类型：info | success | error | warning
 * @param {number} duration- 停留时长（ms），默认 3000
 */
function showToast(message, type = 'info', duration = 3000) {
    /**
     * 显示 Toast 通知。
     *
     * 动画机制：
     *   - 显示时添加 .show 类，触发 CSS keyframe toast-in（translateY + scale
     *     bounce 弹入），并应用 toast-{type} 渐变背景。
     *   - 隐藏时替换为 .hide 类，触发 toast-out 平滑下坠消失。
     *   - 连续调用会自动清除旧定时器，防止多个 toast 堆叠或过早消失。
     *
     * 工程权衡：
     *   - 单例 Toast：全局只有一个 toast 容器，新通知覆盖旧通知，避免屏幕
     *     被大量 toast 占据。在桌面内嵌窗口中，单例是更简洁的选择。
     *   - innerText 而非 innerHTML：防止 message 中含 HTML 特殊字符导致 XSS，
     *     虽然当前为本地应用无真实攻击面，但防御式编程。
     *
     * @param {string} message - 通知文本
     * @param {string} type    - 类型：info | success | error | warning
     * @param {number} duration- 停留时长（ms），默认 3000
     */
    const toast = document.getElementById('toast');
    const inner = document.getElementById('toast-inner');
    if (!toast || !inner) return;

    // 清除旧定时器，防止连续调用时 toast 过早消失或堆叠冲突
    if (toastTimer) clearTimeout(toastTimer);

    inner.textContent = message;
    inner.className = `px-4 py-3 rounded-lg shadow-lg text-sm font-medium toast-${type}`;

    // 通过 CSS keyframe 触发弹入动画
    toast.classList.remove('hide');
    toast.classList.add('show');

    // 自动隐藏：先替换为 toast-out 关键帧，再等待动画结束后清理
    toastTimer = setTimeout(() => {
        toast.classList.remove('show');
        toast.classList.add('hide');
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

    // Start with the operational overview page.
    navigateTo('overview');

    // Trigger entrance animations for initial page elements
    setTimeout(() => triggerStaggerAnimations(), 100);
});

// ─────────────── Animation Helpers ───────────────

/**
 * 触发容器内所有 .stagger-item 与 .card 的交错进入动画。
 *
 * 设计说明：
 *  - .stagger-item 用于 providers.js 动态渲染的列表项，通过 CSS
 *    transition + .visible 类实现，支持中途取消（如快速切页）。
 *  - .card:not(.stagger-item) 作为遗留页面（stats/sessions 等）
 *    的兜底，通过内联 style 设置初始态，再用 rAF 触发 transition。
 *  - 两者 delay 串联计算，避免两组元素同时动画造成视觉拥挤。
 *
 * @param {Element|Document} root - 搜索范围，默认 document
 */
function triggerStaggerAnimations(root = document) {
    /**
     * 触发容器内所有 .stagger-item 与 .card 的交错进入动画。
     *
     * 设计说明：
     *  - .stagger-item 用于 providers.js 动态渲染的列表项，通过 CSS
     *    transition + .visible 类实现，支持中途取消（如快速切页）。
     *  - .card:not(.stagger-item) 作为遗留页面（stats/sessions 等）
     *    的兜底，通过内联 style 设置初始态，再用 rAF 触发 transition。
     *  - 两者 delay 串联计算，避免两组元素同时动画造成视觉拥挤。
     *
     * 性能注意：
     *  - requestAnimationFrame 确保在浏览器重排前添加 .visible，
     *    使 transition 正确触发动画；若直接添加可能因样式已计算而跳过。
     *
     * @param {Element|Document} root - 搜索范围，默认 document
     */
    const items = root.querySelectorAll('.stagger-item');
    items.forEach((item, index) => {
        // 45ms 等差延迟，约等于 22fps 的感知阈值，既有序又不拖沓
        item.style.transitionDelay = `${index * 45}ms`;
        requestAnimationFrame(() => {
            item.classList.add('visible');
        });
    });
    // Fallback：遗留页面中的 .card 没有 .stagger-item，手动赋予相同进入效果
    const cards = root.querySelectorAll('.card:not(.stagger-item)');
    cards.forEach((card, index) => {
        const delay = (items.length + index) * 45;
        card.style.opacity = '0';
        card.style.transform = 'translateY(12px) scale(0.98)';
        card.style.transition = `opacity var(--transition-slow), transform var(--transition-slow)`;
        card.style.transitionDelay = `${delay}ms`;
        requestAnimationFrame(() => {
            card.style.opacity = '';
            card.style.transform = '';
        });
    });
}

/**
 * 为按钮点击创建 Material Design 风格的水波纹。
 *
 * 实现要点：
 *  - 波纹直径取按钮宽高最大值，确保能覆盖整个按钮。
 *  - 通过 getBoundingClientRect 将鼠标坐标转换为按钮局部坐标。
 *  - 旧波纹（如果存在）立即移除，防止快速连击时 DOM 膨胀。
 *  - 波纹元素 550ms 后自动清理，与 CSS animation 时长对齐。
 *
 * @param {MouseEvent} event - click 事件对象
 */
function addRippleEffect(event) {
    /**
     * 为按钮点击创建 Material Design 风格的水波纹。
     *
     * 实现要点：
     *  - 波纹直径取按钮宽高最大值，确保能覆盖整个按钮。
     *  - 通过 getBoundingClientRect 将鼠标坐标转换为按钮局部坐标。
     *  - 旧波纹（如果存在）立即移除，防止快速连击时 DOM 膨胀。
     *  - 波纹元素 550ms 后自动清理，与 CSS animation 时长对齐。
     *
     * 边界条件：
     *  - 若按钮在点击后迅速被移除（如快速切换页面），setTimeout 中的
     *    circle.remove() 可能操作已不在 DOM 中的元素，但 remove() 对
     *    游离元素无异常，安全。
     *
     * @param {MouseEvent} event - click 事件对象
     */
    const btn = event.currentTarget;
    const circle = document.createElement('span');
    const diameter = Math.max(btn.clientWidth, btn.clientHeight);
    const radius = diameter / 2;
    const rect = btn.getBoundingClientRect();
    circle.style.width = circle.style.height = `${diameter}px`;
    circle.style.left = `${event.clientX - rect.left - radius}px`;
    circle.style.top = `${event.clientY - rect.top - radius}px`;
    circle.classList.add('ripple');
    const existing = btn.getElementsByClassName('ripple')[0];
    if (existing) existing.remove();
    btn.appendChild(circle);
    setTimeout(() => circle.remove(), 550);
}

/**
 * 为 root 范围内所有 .btn 绑定 ripple 点击效果，并防重复。
 *
 * @param {Element|Document} root - 搜索范围，默认 document
 */
function attachRippleToButtons(root = document) {
    /**
     * 为 root 范围内所有 .btn 绑定 ripple 点击效果，并防重复。
     *
     * 设计意图：
     *   - innerHTML 重绘后，旧的事件监听器会随 DOM 元素一起被销毁，
     *     因此每次渲染后需重新绑定。dataset.rippleAttached 标记防止
     *     同一按钮被多次绑定（如多次调用 attachRippleToButtons）。
     *
     * @param {Element|Document} root - 搜索范围，默认 document
     */
    root.querySelectorAll('.btn').forEach(btn => {
        if (!btn.dataset.rippleAttached) {
            btn.addEventListener('click', addRippleEffect);
            btn.dataset.rippleAttached = '1';
        }
    });
}
