/**
 * diagnostics.js - Safe diagnostics and recovery summary page.
 *
 * This page intentionally uses the redacted diagnostics API only. It does not
 * request include_secrets, run provider network probes, or write Codex files.
 */

let diagnosticsState = {
    data: null,
    loading: false,
    error: '',
};

async function loadDiagnosticsPage() {
    const root = document.getElementById('diagnostics-root');
    if (!root) return;
    diagnosticsState.loading = true;
    diagnosticsState.error = '';
    renderDiagnosticsPage();
    try {
        diagnosticsState.data = await api('/api/diagnostics');
        setStatus('Diagnostics loaded');
    } catch (err) {
        diagnosticsState.error = err.message;
        showToast('Diagnostics failed: ' + err.message, 'error');
    } finally {
        diagnosticsState.loading = false;
        renderDiagnosticsPage();
        setTimeout(() => triggerStaggerAnimations(root), 30);
    }
}

function renderDiagnosticsPage() {
    const root = document.getElementById('diagnostics-root');
    if (!root) return;
    const data = diagnosticsState.data || {};
    root.innerHTML = `
        <div class="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">Diagnostics</h2>
                <p class="text-sm text-dark-400 mt-1">Redacted local status for config, auth, proxy, providers, AMR, quota, currency, request logs, and sandbox audit.</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="loadDiagnosticsPage()" class="btn btn-secondary">Refresh</button>
                <button onclick="exportSafeDiagnostics()" class="btn btn-primary">Export Safe Bundle</button>
            </div>
        </div>
        ${diagnosticsState.error ? `<div class="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">${diagEscapeHtml(diagnosticsState.error)}</div>` : ''}
        ${diagnosticsState.loading ? '<div class="card"><div class="text-sm text-dark-300">Loading diagnostics...</div></div>' : renderDiagnosticsContent(data)}
    `;
}

function renderDiagnosticsContent(data) {
    if (!data || Object.keys(data).length === 0) {
        return '<div class="card"><div class="text-sm text-dark-400">No diagnostics loaded.</div></div>';
    }
    return `
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            ${renderDiagnosticCard('Codex Config', summarizeCodexConfig(data.codex_config), 'codex_config')}
            ${renderDiagnosticCard('Auth Mode', summarizeAuthMode(data.auth_mode), 'auth_mode')}
            ${renderDiagnosticCard('Local Proxy', summarizeProxy(data.local_proxy), 'local_proxy')}
            ${renderDiagnosticCard('Providers', summarizeProviders(data.providers), 'providers')}
            ${renderDiagnosticCard('AMR', summarizeAmr(data.amr), 'amr')}
            ${renderDiagnosticCard('Quota', summarizeQuota(data.quota), 'quota')}
            ${renderDiagnosticCard('Currency', summarizeCurrency(data.currency), 'currency')}
            ${renderDiagnosticCard('Sandbox Audit', summarizePermissions(data.codex_permissions), 'codex_permissions')}
        </div>
        <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div class="card xl:col-span-2">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <h3 class="card-title">Redacted Bundle Preview</h3>
                    <span class="text-xs text-dark-500">Collected: ${diagEscapeHtml(data.collected_at || '-')}</span>
                </div>
                <pre class="preview-code mt-3">${diagEscapeHtml(JSON.stringify(data, null, 2))}</pre>
            </div>
            <div class="space-y-4">
                ${renderDiagnosticsErrors(data.errors)}
                ${renderDiagnosticsRequestLogs(data.request_logs)}
                ${renderDiagnosticsSystem(data.system)}
            </div>
        </div>
    `;
}

function renderDiagnosticCard(title, summary, sectionKey) {
    const tone = summary.tone || 'dark';
    return `
        <div class="card stagger-item">
            <div class="flex items-start justify-between gap-3">
                <div>
                    <div class="card-label">${diagEscapeHtml(title)}</div>
                    <div class="text-lg font-semibold text-white mt-1">${diagEscapeHtml(summary.primary || '-')}</div>
                </div>
                <span class="status-pill status-pill-${diagEscapeAttr(tone)}">${diagEscapeHtml(summary.status || 'status')}</span>
            </div>
            <div class="text-xs text-dark-400 mt-3 leading-relaxed">${diagEscapeHtml(summary.detail || '')}</div>
            <button onclick="scrollDiagnosticsPreview('${diagEscapeAttr(sectionKey)}')" class="btn btn-ghost text-xs mt-4">View JSON</button>
        </div>
    `;
}

function summarizeCodexConfig(section = {}) {
    const exists = Boolean(section.exists || section.config_exists);
    return {
        tone: exists ? 'emerald' : 'amber',
        status: exists ? 'found' : 'missing',
        primary: section.path || section.config_path || 'config.toml',
        detail: section.error || (exists ? 'Config file is readable in diagnostics.' : 'Config path was not found or not configured.'),
    };
}

function summarizeAuthMode(section = {}) {
    const mode = section.mode || section.auth_mode || 'unknown';
    const hasError = Boolean(section.error);
    return {
        tone: hasError ? 'red' : (mode === 'unknown' ? 'amber' : 'emerald'),
        status: mode,
        primary: mode,
        detail: section.error || section.note || 'Auth status is redacted; secrets are not shown.',
    };
}

function summarizeProxy(section = {}) {
    const running = Boolean(section.running || section.is_running);
    const actualPort = section.actual_port || section.port || '';
    return {
        tone: running ? 'emerald' : 'dark',
        status: running ? 'running' : 'stopped',
        primary: actualPort ? `port ${actualPort}` : 'not bound',
        detail: section.backoff_used ? 'Proxy used occupied-port backoff.' : (section.last_error || 'No active proxy error reported.'),
    };
}

function summarizeProviders(section = {}) {
    const count = Number(section.count || 0);
    return {
        tone: count > 0 ? 'emerald' : 'amber',
        status: `${count} configured`,
        primary: `${count} providers`,
        detail: section.store_path || 'Provider registry path is not configured.',
    };
}

function summarizeAmr(section = {}) {
    const groups = Number(section.groups_count || section.group_count || 0);
    return {
        tone: groups > 0 ? 'emerald' : 'dark',
        status: `${groups} groups`,
        primary: `${groups} AMR groups`,
        detail: section.store_path || section.note || 'AMR diagnostics are available when groups are configured.',
    };
}

function summarizeQuota(section = {}) {
    const snapshots = Number(section.snapshot_count || section.cached_count || 0);
    return {
        tone: snapshots > 0 ? 'emerald' : 'dark',
        status: `${snapshots} cached`,
        primary: `${snapshots} quota snapshots`,
        detail: section.error || section.note || 'Quota diagnostics read cached snapshots only.',
    };
}

function summarizeCurrency(section = {}) {
    const display = section.display_currency || 'USD';
    const source = section.exchange_rate_source || section.source || 'manual';
    return {
        tone: section.error ? 'red' : 'emerald',
        status: display,
        primary: `${display} display`,
        detail: section.error || `Exchange source: ${source}. API key presence is reported only as a boolean.`,
    };
}

function summarizePermissions(section = {}) {
    const issues = Array.isArray(section.issues) ? section.issues.length : Number(section.issue_count || 0);
    return {
        tone: issues > 0 ? 'amber' : 'emerald',
        status: issues > 0 ? `${issues} issues` : 'clean',
        primary: issues > 0 ? `${issues} audit findings` : 'No audit findings',
        detail: section.note || 'Sandbox and approval settings are audited read-only.',
    };
}

function renderDiagnosticsErrors(errors = {}) {
    const items = Array.isArray(errors.recent) ? errors.recent : (Array.isArray(errors.items) ? errors.items : []);
    return `
        <div class="card">
            <h3 class="card-title">Recent Errors</h3>
            <div class="mt-3 space-y-2">
                ${items.length ? items.slice(0, 8).map(item => `
                    <div class="rounded-md border border-dark-800 bg-dark-950/40 px-3 py-2">
                        <div class="text-xs text-dark-500">${diagEscapeHtml(item.at || item.timestamp || '')}</div>
                        <div class="text-sm text-dark-200">${diagEscapeHtml(item.context || item.source || 'error')}</div>
                        <div class="text-xs text-amber-200 mt-1 break-words">${diagEscapeHtml(item.error || item.message || JSON.stringify(item))}</div>
                    </div>
                `).join('') : '<div class="text-sm text-dark-400">No recent errors.</div>'}
            </div>
        </div>
    `;
}

function renderDiagnosticsRequestLogs(section = {}) {
    const summary = section.summary || section;
    return `
        <div class="card">
            <h3 class="card-title">Request Logs</h3>
            <div class="enhance-status-strip mt-3">
                <span class="status-pill status-pill-dark">records ${diagEscapeHtml(summary.total_records || summary.count || 0)}</span>
                <span class="status-pill status-pill-dark">success ${diagEscapeHtml(summary.successful_requests || summary.success_count || 0)}</span>
            </div>
            <div class="text-xs text-dark-400 mt-3">${diagEscapeHtml(summary.path || section.path || 'Metadata-only request log summary.')}</div>
        </div>
    `;
}

function renderDiagnosticsSystem(section = {}) {
    return `
        <div class="card">
            <h3 class="card-title">System</h3>
            <div class="text-xs text-dark-400 mt-3 space-y-1">
                <div>Platform: ${diagEscapeHtml(section.platform || '-')}</div>
                <div>Python: ${diagEscapeHtml(section.python_version || '-')}</div>
                <div>App data: ${diagEscapeHtml(section.app_data_dir || '-')}</div>
            </div>
        </div>
    `;
}

async function exportSafeDiagnostics() {
    try {
        const response = await fetch('/api/diagnostics/export', { method: 'POST' });
        if (!response.ok) {
            let message = `HTTP ${response.status}`;
            try {
                const payload = await response.json();
                message = payload.error || message;
            } catch (_err) {
                // Keep the HTTP status message.
            }
            throw new Error(message);
        }
        const text = await response.text();
        const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        const stamp = new Date().toISOString().replace(/[:.]/g, '-');
        link.href = url;
        link.download = `codex-enhance-diagnostics-${stamp}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast('Safe diagnostics exported', 'success');
    } catch (err) {
        showToast('Diagnostics export failed: ' + err.message, 'error');
    }
}

function scrollDiagnosticsPreview(sectionKey) {
    const data = diagnosticsState.data || {};
    const section = data[sectionKey] || {};
    const preview = document.querySelector('#diagnostics-root .preview-code');
    if (!preview) return;
    preview.textContent = JSON.stringify({ [sectionKey]: section }, null, 2);
    preview.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function diagEscapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function diagEscapeAttr(value) {
    return diagEscapeHtml(value).replace(/`/g, '&#96;');
}
