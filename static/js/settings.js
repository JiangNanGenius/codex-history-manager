/**
 * settings.js - Settings, storage, cleanup, and theme controls.
 */

const THEME_PRESETS = {
    dark: { accent: '#3b82f6', deep: '#020617', background: '#0f172a', elevated: '#1e293b', surface: '#1e293b', border: '#334155', text_primary: '#f8fafc', text_secondary: '#cbd5e1', text_muted: '#94a3b8' },
    midnight: { accent: '#38bdf8', deep: '#010313', background: '#020617', elevated: '#0f172a', surface: '#111827', border: '#1f2a44', text_primary: '#f8fafc', text_secondary: '#cbd5e1', text_muted: '#94a3b8' },
    graphite: { accent: '#22c55e', deep: '#09090b', background: '#111827', elevated: '#1f2937', surface: '#27272a', border: '#3f3f46', text_primary: '#fafafa', text_secondary: '#d4d4d8', text_muted: '#a1a1aa' },
    cobalt: { accent: '#06b6d4', deep: '#07111f', background: '#0b1220', elevated: '#111c30', surface: '#172033', border: '#28415f', text_primary: '#f0f9ff', text_secondary: '#bae6fd', text_muted: '#7dd3fc' },
    ember: { accent: '#f97316', deep: '#0c0a09', background: '#18181b', elevated: '#1f1f23', surface: '#292524', border: '#44403c', text_primary: '#fff7ed', text_secondary: '#fed7aa', text_muted: '#fdba74' },
    jade: { accent: '#14b8a6', deep: '#04110f', background: '#08201d', elevated: '#102a27', surface: '#163733', border: '#24534e', text_primary: '#ecfdf5', text_secondary: '#ccfbf1', text_muted: '#99f6e4' },
    rose: { accent: '#f43f5e', deep: '#12070b', background: '#1f1117', elevated: '#2a1720', surface: '#351b27', border: '#5f2638', text_primary: '#fff1f2', text_secondary: '#fecdd3', text_muted: '#fda4af' },
    aurora: { accent: '#8b5cf6', deep: '#070818', background: '#111827', elevated: '#172033', surface: '#1f2937', border: '#3b4962', text_primary: '#f5f3ff', text_secondary: '#ddd6fe', text_muted: '#a5b4fc' },
    paper: { accent: '#2563eb', deep: '#e5e7eb', background: '#f8fafc', elevated: '#ffffff', surface: '#eef2ff', border: '#cbd5e1', text_primary: '#0f172a', text_secondary: '#334155', text_muted: '#64748b' },
    custom: { accent: '#3b82f6', deep: '#020617', background: '#0f172a', elevated: '#1e293b', surface: '#1e293b', border: '#334155', text_primary: '#f8fafc', text_secondary: '#cbd5e1', text_muted: '#94a3b8' },
};

const THEME_FIELDS = [
    ['accent', 'setting-theme-accent'],
    ['deep', 'setting-theme-deep'],
    ['background', 'setting-theme-background'],
    ['elevated', 'setting-theme-elevated'],
    ['surface', 'setting-theme-surface'],
    ['border', 'setting-theme-border'],
    ['text_primary', 'setting-theme-text-primary'],
    ['text_secondary', 'setting-theme-text-secondary'],
    ['text_muted', 'setting-theme-text-muted'],
];

async function loadSettings() {
    try {
        const data = await api('/api/settings');
        populateSettingsForm(data);
        applyThemeSettings(data);
        setStatus(t('settingsLoaded'));

        await Promise.allSettled([
            loadStorageInfo(),
            loadCleanupPreview(),
            loadUninstallPreview(),
            loadCurrencySettings(),
            loadStartupStatus(),
        ]);

        const hasDbPath = data.db_path && String(data.db_path).trim().length > 0;
        const hasSessionsDir = data.sessions_dir && String(data.sessions_dir).trim().length > 0;
        if (!hasDbPath || !hasSessionsDir) {
            await runAutoDetect();
        }
    } catch (err) {
        showToast(t('failedLoadSettings') + err.message, 'error');
    }
}

function populateSettingsForm(data) {
    const fields = {
        'setting-db-path': 'db_path',
        'setting-sessions-dir': 'sessions_dir',
        'setting-archived-dir': 'archived_dir',
        'setting-backup-dir': 'backup_dir',
        'setting-codex-cli': 'codex_cli_path',
        'setting-codex-pp': 'codex_plus_plus_path',
        'setting-cc-switch-db': 'cc_switch_db_path',
        'setting-provider-store-path': 'provider_store_path',
        'setting-temp-dir': 'temp_dir',
        'setting-diagnostics-dir': 'diagnostics_dir',
        'setting-exports-dir': 'exports_dir',
        'setting-request-log-path': 'request_log_path',
        'setting-request-log-retention-days': 'request_log_retention_days',
        'setting-request-log-max-mb': 'request_log_max_mb',
        'setting-proxy-upstream-timeout': 'proxy_upstream_timeout_seconds',
        'setting-proxy-retry-attempts': 'proxy_retry_attempts',
        'setting-proxy-retry-backoff-ms': 'proxy_retry_backoff_ms',
        'setting-startup-mode': 'startup_mode',
        'setting-startup-task-name': 'startup_task_name',
        'setting-startup-shortcut-name': 'startup_shortcut_name',
        'setting-startup-target-path': 'startup_target_path',
        'setting-startup-arguments': 'startup_arguments',
        'setting-theme-preset': 'theme_preset',
        'setting-page-size': 'page_size',
        'setting-backup-interval': 'backup_interval_hours',
        'setting-max-backups': 'max_backups',
        'setting-large-threshold': 'large_file_threshold_mb',
        'setting-max-lines': 'max_lines_large_file',
    };

    for (const [elId, key] of Object.entries(fields)) {
        const el = document.getElementById(elId);
        if (el && data[key] !== undefined) el.value = data[key];
    }

    const checkboxFields = {
        'setting-auto-backup': 'auto_backup',
        'setting-use-codex-pp': 'use_codex_plus_plus',
        'setting-dark-mode': 'dark_mode',
        'setting-startup-enabled': 'startup_enabled',
        'setting-startup-auto-elevate': 'startup_auto_elevate',
    };
    for (const [elId, key] of Object.entries(checkboxFields)) {
        const el = document.getElementById(elId);
        if (el && data[key] !== undefined) el.checked = Boolean(data[key]);
    }
    syncStartupControls();

    const themeCustom = normalizeThemeCustom(data.theme_custom || {});
    for (const [key, elId] of THEME_FIELDS) {
        const el = document.getElementById(elId);
        if (el) el.value = themeCustom[key];
    }

    const monitorFields = data.monitor_fields || {};
    const monitorMap = {
        'monitor-field-tokens': 'tokens',
        'monitor-field-progress': 'progress',
        'monitor-field-threshold': 'threshold',
        'monitor-field-cache': 'cache',
        'monitor-field-context': 'context_window',
        'monitor-field-updated': 'updated_at',
    };
    for (const [elId, key] of Object.entries(monitorMap)) {
        const el = document.getElementById(elId);
        if (el) el.checked = monitorFields[key] !== false;
    }
}

async function loadCurrencySettings() {
    const display = document.getElementById('currency-display');
    if (!display) return;
    try {
        const data = await api('/api/currency/settings');
        display.value = data.display_currency || 'USD';
        const source = document.getElementById('currency-source');
        if (source) source.value = data.exchange_rate_source || 'manual';
        const apiKey = document.getElementById('currency-api-key');
        if (apiKey) apiKey.value = data.exchange_rate_api_key || '';
        const ttl = document.getElementById('currency-ttl-hours');
        if (ttl) ttl.value = data.exchange_rate_ttl_hours || 24;
        const overrides = document.getElementById('currency-manual-overrides');
        if (overrides) overrides.value = JSON.stringify(data.exchange_rate_manual_overrides || {}, null, 2);
    } catch (err) {
        renderInlineError('currency-preview-result', err.message);
    }
}

async function saveCurrencySettings() {
    try {
        const overrides = JSON.parse(document.getElementById('currency-manual-overrides')?.value || '{}');
        const payload = {
            display_currency: document.getElementById('currency-display')?.value || 'USD',
            exchange_rate_source: document.getElementById('currency-source')?.value || 'manual',
            exchange_rate_api_key: document.getElementById('currency-api-key')?.value || '',
            exchange_rate_ttl_hours: parseInt(document.getElementById('currency-ttl-hours')?.value, 10) || 24,
            exchange_rate_manual_overrides: overrides,
        };
        const result = await api('/api/currency/settings', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        if (!result.success) throw new Error(result.error || 'Currency settings save failed');
        await loadCurrencySettings();
        showToast('Currency settings saved', 'success');
    } catch (err) {
        showToast('Currency save failed: ' + err.message, 'error');
    }
}

async function previewCurrencyRate() {
    const resultEl = document.getElementById('currency-preview-result');
    if (!resultEl) return;
    try {
        const data = await api('/api/currency/convert', {
            method: 'POST',
            body: JSON.stringify({
                from_currency: document.getElementById('currency-preview-from')?.value || 'USD',
                to_currency: document.getElementById('currency-preview-to')?.value || 'USD',
                amount: parseFloat(document.getElementById('currency-preview-amount')?.value || '1') || 0,
            }),
        });
        resultEl.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
        resultEl.textContent = 'Preview failed: ' + err.message;
    }
}

function readStartupSettingsFromForm() {
    let mode = document.getElementById('setting-startup-mode')?.value || 'disabled';
    let enabled = document.getElementById('setting-startup-enabled')?.checked || mode !== 'disabled';
    let autoElevate = document.getElementById('setting-startup-auto-elevate')?.checked || mode === 'scheduled_task_highest';
    if (!enabled) {
        mode = 'disabled';
        autoElevate = false;
    } else if (autoElevate) {
        mode = 'scheduled_task_highest';
    } else if (mode === 'disabled') {
        mode = 'startup_folder';
    }
    return {
        startup_enabled: enabled,
        startup_mode: mode,
        startup_auto_elevate: autoElevate,
        startup_task_name: document.getElementById('setting-startup-task-name')?.value || 'CodexEnhanceManager',
        startup_shortcut_name: document.getElementById('setting-startup-shortcut-name')?.value || 'CodexEnhanceManager.cmd',
        startup_target_path: document.getElementById('setting-startup-target-path')?.value || '',
        startup_arguments: document.getElementById('setting-startup-arguments')?.value || '',
    };
}

function syncStartupControls(source = '') {
    const modeEl = document.getElementById('setting-startup-mode');
    const enabledEl = document.getElementById('setting-startup-enabled');
    const autoEl = document.getElementById('setting-startup-auto-elevate');
    if (!modeEl || !enabledEl || !autoEl) return;
    if (source === 'setting-startup-enabled' && !enabledEl.checked) {
        modeEl.value = 'disabled';
        autoEl.checked = false;
    } else if (source === 'setting-startup-auto-elevate' && autoEl.checked) {
        enabledEl.checked = true;
        modeEl.value = 'scheduled_task_highest';
    } else if (source === 'setting-startup-auto-elevate' && !autoEl.checked && modeEl.value === 'scheduled_task_highest') {
        enabledEl.checked = true;
        modeEl.value = 'startup_folder';
    } else if (modeEl.value === 'scheduled_task_highest') {
        enabledEl.checked = true;
        autoEl.checked = true;
    } else if (modeEl.value === 'startup_folder') {
        enabledEl.checked = true;
        autoEl.checked = false;
    } else if (!enabledEl.checked) {
        modeEl.value = 'disabled';
        autoEl.checked = false;
    } else if (autoEl.checked) {
        modeEl.value = 'scheduled_task_highest';
    } else if (enabledEl.checked && modeEl.value === 'disabled') {
        modeEl.value = 'startup_folder';
    }
}

async function loadStartupStatus() {
    const strip = document.getElementById('startup-status-strip');
    if (!strip) return;
    try {
        const data = await api('/api/startup/status');
        renderStartupStatus(data);
    } catch (err) {
        strip.className = 'mt-4 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-200';
        strip.textContent = 'Startup status failed: ' + err.message;
    }
}

async function previewStartupSettings() {
    const resultEl = document.getElementById('startup-preview-result');
    if (!resultEl) return;
    try {
        syncStartupControls();
        const data = await api('/api/startup/preview', {
            method: 'POST',
            body: JSON.stringify(readStartupSettingsFromForm()),
        });
        resultEl.textContent = JSON.stringify(data, null, 2);
        renderStartupStatus({
            supported: data.supported,
            configured: {
                startup_mode: data.mode,
                startup_enabled: data.enabled,
                startup_auto_elevate: data.auto_elevate,
            },
            startup_entry_path: data.startup_entry_path,
            startup_entry_exists: false,
            scheduled_task_exists: false,
            scheduled_task_checked: false,
        });
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

async function applyStartupSettings() {
    const confirmation = prompt('Type MODIFY_WINDOWS_STARTUP to apply the startup change.');
    if (confirmation !== 'MODIFY_WINDOWS_STARTUP') {
        showToast('Startup change cancelled', 'warning');
        return;
    }
    try {
        syncStartupControls();
        const result = await api('/api/startup/apply', {
            method: 'POST',
            body: JSON.stringify({ ...readStartupSettingsFromForm(), confirmation }),
        });
        renderStartupMutationResult(result);
        await loadStartupStatus();
        showToast('Startup change applied', 'success');
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

async function removeStartupSettings() {
    const confirmation = prompt('Type MODIFY_WINDOWS_STARTUP to remove the startup entry.');
    if (confirmation !== 'MODIFY_WINDOWS_STARTUP') {
        showToast('Startup removal cancelled', 'warning');
        return;
    }
    try {
        const result = await api('/api/startup/remove', {
            method: 'POST',
            body: JSON.stringify({ ...readStartupSettingsFromForm(), confirmation }),
        });
        renderStartupMutationResult(result);
        await loadStartupStatus();
        await loadSettings();
        showToast('Startup entry removed', 'success');
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

function renderStartupStatus(data) {
    const strip = document.getElementById('startup-status-strip');
    if (!strip) return;
    const configured = data.configured || {};
    const mode = configured.startup_mode || data.mode || 'disabled';
    const supported = data.supported !== false;
    const active = mode !== 'disabled';
    const cls = !supported
        ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
        : active
            ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
            : 'border-dark-800 bg-dark-950/40 text-dark-300';
    const entry = data.startup_entry_exists ? 'startup file present' : 'startup file absent';
    const task = data.scheduled_task_exists ? 'scheduled task present' : 'scheduled task absent';
    const checked = data.scheduled_task_checked === false ? 'task not checked' : task;
    strip.className = `mt-4 rounded-md border ${cls} px-4 py-2 text-sm`;
    strip.textContent = `${supported ? 'Windows integration supported' : 'Unsupported platform'} · Mode: ${mode} · ${entry} · ${checked}`;
}

function renderStartupMutationResult(result) {
    const resultEl = document.getElementById('startup-preview-result');
    if (!resultEl) return;
    resultEl.textContent = JSON.stringify(result, null, 2);
}

function resolveTheme(data) {
    const presetName = (data && data.theme_preset) || 'dark';
    const preset = THEME_PRESETS[presetName] || THEME_PRESETS.dark;
    if (presetName !== 'custom') return preset;
    return normalizeThemeCustom({ ...preset, ...((data && data.theme_custom) || {}) });
}

function applyThemeSettings(data) {
    const palette = resolveTheme(data || {});
    document.documentElement.style.setProperty('--accent', palette.accent);
    document.documentElement.style.setProperty('--accent-glow', hexToRgba(palette.accent, 0.25));
    document.documentElement.style.setProperty('--bg-deep', palette.deep);
    document.documentElement.style.setProperty('--bg-base', palette.background);
    document.documentElement.style.setProperty('--bg-elevated', palette.elevated);
    document.documentElement.style.setProperty('--border-subtle', hexToRgba(palette.border, 0.55));
    document.documentElement.style.setProperty('--text-primary', palette.text_primary);
    document.documentElement.style.setProperty('--text-secondary', palette.text_secondary);
    document.documentElement.style.setProperty('--text-muted', palette.text_muted);
    document.documentElement.style.setProperty('--bg-surface', hexToRgba(palette.surface, 0.72));
    document.documentElement.style.setProperty('--border-hover', hexToRgba(palette.accent, 0.35));
}

function normalizeThemeCustom(value) {
    const raw = value && typeof value === 'object' ? value : {};
    const base = { ...THEME_PRESETS.custom };
    for (const key of Object.keys(base)) {
        base[key] = sanitizeColor(raw[key], base[key]);
    }
    return base;
}

function readThemeCustomFromForm() {
    const theme = {};
    for (const [key, elId] of THEME_FIELDS) {
        theme[key] = sanitizeColor(document.getElementById(elId)?.value, THEME_PRESETS.custom[key]);
    }
    return theme;
}

function hexToRgba(hex, alpha) {
    const cleaned = String(hex || '').replace('#', '');
    if (!/^[0-9a-fA-F]{6}$/.test(cleaned)) return `rgba(59, 130, 246, ${alpha})`;
    const value = parseInt(cleaned, 16);
    const r = (value >> 16) & 255;
    const g = (value >> 8) & 255;
    const b = value & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

async function saveSettings() {
    const data = {
        db_path: document.getElementById('setting-db-path')?.value || '',
        sessions_dir: document.getElementById('setting-sessions-dir')?.value || '',
        archived_dir: document.getElementById('setting-archived-dir')?.value || '',
        backup_dir: document.getElementById('setting-backup-dir')?.value || '',
        codex_cli_path: document.getElementById('setting-codex-cli')?.value || '',
        codex_plus_plus_path: document.getElementById('setting-codex-pp')?.value || '',
        cc_switch_db_path: document.getElementById('setting-cc-switch-db')?.value || '',
        provider_store_path: document.getElementById('setting-provider-store-path')?.value || '',
        temp_dir: document.getElementById('setting-temp-dir')?.value || '',
        diagnostics_dir: document.getElementById('setting-diagnostics-dir')?.value || '',
        exports_dir: document.getElementById('setting-exports-dir')?.value || '',
        request_log_path: document.getElementById('setting-request-log-path')?.value || '',
        request_log_retention_days: parseInt(document.getElementById('setting-request-log-retention-days')?.value, 10) || 30,
        request_log_max_mb: parseFloat(document.getElementById('setting-request-log-max-mb')?.value) || 50,
        proxy_upstream_timeout_seconds: parseInt(document.getElementById('setting-proxy-upstream-timeout')?.value, 10) || 120,
        proxy_retry_attempts: parseInt(document.getElementById('setting-proxy-retry-attempts')?.value, 10) || 0,
        proxy_retry_backoff_ms: parseInt(document.getElementById('setting-proxy-retry-backoff-ms')?.value, 10) || 250,
        ...readStartupSettingsFromForm(),
        theme_preset: document.getElementById('setting-theme-preset')?.value || 'dark',
        theme_custom: readThemeCustomFromForm(),
        monitor_fields: {
            tokens: document.getElementById('monitor-field-tokens')?.checked !== false,
            progress: document.getElementById('monitor-field-progress')?.checked !== false,
            threshold: document.getElementById('monitor-field-threshold')?.checked !== false,
            cache: document.getElementById('monitor-field-cache')?.checked !== false,
            context_window: document.getElementById('monitor-field-context')?.checked !== false,
            updated_at: document.getElementById('monitor-field-updated')?.checked !== false,
        },
        page_size: parseInt(document.getElementById('setting-page-size')?.value, 10) || 50,
        backup_interval_hours: parseInt(document.getElementById('setting-backup-interval')?.value, 10) || 6,
        max_backups: parseInt(document.getElementById('setting-max-backups')?.value, 10) || 20,
        large_file_threshold_mb: parseInt(document.getElementById('setting-large-threshold')?.value, 10) || 500,
        max_lines_large_file: parseInt(document.getElementById('setting-max-lines')?.value, 10) || 2000,
        auto_backup: document.getElementById('setting-auto-backup')?.checked || false,
        use_codex_plus_plus: document.getElementById('setting-use-codex-pp')?.checked || false,
        dark_mode: document.getElementById('setting-dark-mode')?.checked ?? true,
    };

    try {
        const result = await api('/api/settings', { method: 'POST', body: JSON.stringify(data) });
        if (result.success) {
            applyThemeSettings(data);
            await loadStorageInfo();
            showToast(t('settingsSaved') + (result.warning ? ' ' + t('warning') + ': ' + result.warning : ''), 'success');
        } else {
            showToast(t('saveFailed') + (result.error || t('unknownError')), 'error');
        }
    } catch (err) {
        showToast(t('saveFailed') + err.message, 'error');
    }
}

async function resetSettings() {
    if (!confirm(t('confirmResetSettings'))) return;
    try {
        const result = await api('/api/settings/reset', { method: 'POST' });
        if (result.success) {
            showToast(t('settingsReset'), 'success');
            loadSettings();
        } else {
            showToast(t('resetFailed') + (result.error || t('unknownError')), 'error');
        }
    } catch (err) {
        showToast(t('resetFailed') + err.message, 'error');
    }
}

async function exportSettings() {
    try {
        const data = await api('/api/settings/export');
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `codex-enhance-settings-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('Settings exported', 'success');
    } catch (err) {
        showToast('Export failed: ' + err.message, 'error');
    }
}

async function importSettingsFromFile(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
        const text = await file.text();
        const payload = JSON.parse(text);
        const result = await api('/api/settings/import', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        if (!result.success) throw new Error(result.error || 'Import failed');
        showToast('Settings imported', 'success');
        await loadSettings();
    } catch (err) {
        showToast('Import failed: ' + err.message, 'error');
    } finally {
        input.value = '';
    }
}

async function exportTheme() {
    try {
        const settings = await api('/api/settings');
        const payload = {
            schema: 'codex_enhance_manager.theme.v1',
            exported_at: new Date().toISOString(),
            theme_preset: settings.theme_preset || 'dark',
            theme_custom: settings.theme_custom || {},
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `codex-enhance-theme-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('Theme exported', 'success');
    } catch (err) {
        showToast('Theme export failed: ' + err.message, 'error');
    }
}

async function importThemeFromFile(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
        const payload = JSON.parse(await file.text());
        const themeCustom = payload.theme_custom || payload.custom || payload;
        const hasCustomColors = THEME_FIELDS.some(([key]) => Boolean(themeCustom[key]));
        const nextTheme = {
            theme_preset: hasCustomColors ? 'custom' : (payload.theme_preset || 'custom'),
            theme_custom: normalizeThemeCustom(themeCustom),
        };
        const result = await api('/api/settings', {
            method: 'POST',
            body: JSON.stringify(nextTheme),
        });
        if (!result.success) throw new Error(result.error || 'Theme import failed');
        populateSettingsForm(await api('/api/settings'));
        applyThemeSettings(nextTheme);
        showToast('Theme imported', 'success');
    } catch (err) {
        showToast('Theme import failed: ' + err.message, 'error');
    } finally {
        input.value = '';
    }
}

function sanitizeColor(value, fallback) {
    const color = String(value || '').trim();
    return /^#[0-9a-fA-F]{6}$/.test(color) ? color : fallback;
}

async function loadStorageInfo() {
    const container = document.getElementById('storage-locations');
    if (!container) return;
    try {
        const data = await api('/api/settings/storage');
        const rows = [
            ['App Data', data.app_data_dir],
            ['Config', data.config_file],
            ['Provider Registry', data.provider_store_path],
            ['Backups', data.backup_dir],
            ['Temp', data.temp_dir],
            ['Diagnostics', data.diagnostics_dir],
            ['Exports', data.exports_dir],
            ['Legacy Config', data.legacy_config_exists ? data.legacy_config_file : 'not present'],
        ];
        container.innerHTML = rows.map(([label, value]) => `
            <div class="flex gap-3 py-2 border-b border-dark-800 last:border-b-0">
                <div class="w-36 shrink-0 text-xs text-dark-400">${escapeHtml(label)}</div>
                <div class="min-w-0 flex-1 font-mono text-xs text-dark-200 break-all">${escapeHtml(value || '')}</div>
            </div>
        `).join('');
    } catch (err) {
        container.innerHTML = `<div class="text-sm text-red-400">${escapeHtml(err.message)}</div>`;
    }
}

async function loadCleanupPreview() {
    const container = document.getElementById('cleanup-targets');
    if (!container) return;
    try {
        const data = await api('/api/cleanup/preview');
        renderCleanupTargets(container, data.targets || [], { selectable: true, prefix: 'cleanup-target' });
    } catch (err) {
        container.innerHTML = `<div class="text-sm text-red-400">${escapeHtml(err.message)}</div>`;
    }
}

async function executeCleanup() {
    const selected = Array.from(document.querySelectorAll('[data-cleanup-target]:checked')).map(el => el.value);
    const confirmation = prompt('Type CLEAN_LOCAL_CACHE to clean selected local cache targets.');
    if (confirmation !== 'CLEAN_LOCAL_CACHE') {
        showToast('Cleanup cancelled', 'warning');
        return;
    }
    try {
        const result = await api('/api/cleanup/execute', {
            method: 'POST',
            body: JSON.stringify({ confirmation, targets: selected }),
        });
        renderCleanupResults('cleanup-result', result.results || []);
        await loadCleanupPreview();
        showToast('Cleanup completed', 'success');
    } catch (err) {
        renderInlineError('cleanup-result', err.message);
    }
}

async function loadUninstallPreview() {
    const container = document.getElementById('uninstall-cleanup-targets');
    if (!container) return;
    try {
        const data = await api('/api/uninstall-cleanup/preview');
        renderCleanupTargets(container, data.targets || [], { selectable: false, prefix: 'uninstall-target' });
        renderWriteLockState(data.write_locked, data.reason || '');
    } catch (err) {
        container.innerHTML = `<div class="text-sm text-red-400">${escapeHtml(err.message)}</div>`;
    }
}

async function executeUninstallCleanup() {
    const confirmation = prompt('Type UNINSTALL_CLEANUP to remove app-owned local data and lock writes until restart.');
    if (confirmation !== 'UNINSTALL_CLEANUP') {
        showToast('Uninstall cleanup cancelled', 'warning');
        return;
    }
    try {
        const result = await api('/api/uninstall-cleanup/execute', {
            method: 'POST',
            body: JSON.stringify({ confirmation }),
        });
        renderCleanupResults('uninstall-cleanup-result', result.results || []);
        renderWriteLockState(true, result.reason || '');
        showToast('Uninstall cleanup completed; writes locked until restart.', 'success', 5000);
    } catch (err) {
        renderInlineError('uninstall-cleanup-result', err.message);
    }
}

function renderCleanupTargets(container, targets, options = {}) {
    const selectable = options.selectable !== false;
    const prefix = options.prefix || 'target';
    if (!targets.length) {
        container.innerHTML = '<div class="text-sm text-dark-500">No targets.</div>';
        return;
    }
    container.innerHTML = targets.map(target => {
        const safeClass = target.safe ? 'text-emerald-400' : 'text-amber-400';
        const inputId = `${prefix}-${target.id}`;
        const labelFor = selectable && target.safe ? ` for="${escapeHtml(inputId)}"` : '';
        const checkbox = selectable && target.safe ? `
            <input id="${escapeHtml(inputId)}" data-cleanup-target type="checkbox" value="${escapeHtml(target.id)}" checked
                class="mt-1 w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
        ` : '<span class="mt-1 w-4 h-4"></span>';
        return `
            <label${labelFor} class="flex gap-3 py-3 border-b border-dark-800 last:border-b-0">
                ${checkbox}
                <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="text-sm font-medium text-dark-100">${escapeHtml(target.description || target.id)}</span>
                        <span class="text-xs ${safeClass}">${target.safe ? 'allowlisted' : 'manual'}</span>
                        <span class="text-xs text-dark-500">${target.exists ? formatBytes(target.size_bytes || 0) : 'not present'}</span>
                    </div>
                    <div class="mt-1 font-mono text-xs text-dark-400 break-all">${escapeHtml(target.path || '')}</div>
                    ${target.effect ? `<div class="mt-1 text-xs text-dark-500">${escapeHtml(target.effect)}</div>` : ''}
                </div>
            </label>
        `;
    }).join('');
}

function renderCleanupResults(containerId, results) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = results.map(result => {
        const status = result.success ? (result.skipped ? 'Skipped' : 'Removed') : 'Failed';
        const cls = result.success ? 'text-emerald-400' : 'text-red-400';
        return `
            <div class="flex gap-2 py-1 text-xs">
                <span class="${cls} w-16 shrink-0">${status}</span>
                <span class="font-mono text-dark-300 break-all">${escapeHtml(result.path || result.id || '')}</span>
                ${result.error ? `<span class="text-red-400">${escapeHtml(result.error)}</span>` : ''}
            </div>
        `;
    }).join('');
}

function renderInlineError(containerId, message) {
    const container = document.getElementById(containerId);
    if (container) container.innerHTML = `<div class="text-sm text-red-400">${escapeHtml(message)}</div>`;
}

function renderWriteLockState(locked, reason) {
    const strip = document.getElementById('uninstall-write-lock-state');
    if (!strip) return;
    strip.className = locked
        ? 'mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200'
        : 'mt-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200';
    strip.textContent = locked ? (reason || 'Writes are locked until restart.') : 'Writes are enabled.';
}

function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
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

async function runAutoDetect() {
    try {
        setStatus(t('detectingPaths'));
        const data = await api('/api/detect');
        renderDetectResults(data);
        showToast(t('autoDetectComplete'), 'success');

        if (data.db_path) document.getElementById('setting-db-path').value = data.db_path;
        if (data.sessions_dir) document.getElementById('setting-sessions-dir').value = data.sessions_dir;
        if (data.archived_dir) document.getElementById('setting-archived-dir').value = data.archived_dir;
        if (data.codex_plus_plus_path) document.getElementById('setting-codex-pp').value = data.codex_plus_plus_path;
        if (data.codex_config && data.codex_config.codex_cli_path) {
            document.getElementById('setting-codex-cli').value = data.codex_config.codex_cli_path;
        }
    } catch (err) {
        showToast(t('autoDetectFailed') + err.message, 'error');
    }
}

function renderDetectResults(data) {
    const container = document.getElementById('detect-results');
    if (!container) return;

    const items = [
        { label: t('dbPath'), value: data.db_path },
        { label: t('sessionsDir'), value: data.sessions_dir },
        { label: t('archivedDir'), value: data.archived_dir },
        { label: t('codexCliPath'), value: data.codex_cli_path },
        { label: t('codexPPPath'), value: data.codex_plus_plus_path },
    ];

    if (data.codex_config) {
        items.push(
            { label: 'Config Provider', value: data.codex_config.model_provider },
            { label: 'Config Model', value: data.codex_config.model },
        );
    }

    container.innerHTML = items.map(item => {
        const found = Boolean(item.value);
        const statusClass = found ? 'text-emerald-400' : 'text-dark-500';
        const statusText = found ? t('success') : t('error');
        return `
            <div class="flex items-center gap-3 py-2 px-3 rounded-lg bg-dark-900/50">
                <div class="flex-1 min-w-0">
                    <div class="text-xs text-dark-400">${escapeHtml(item.label)}</div>
                    <div class="text-sm font-mono text-dark-200 truncate">${escapeHtml(item.value || '(empty)')}</div>
                </div>
                <span class="text-xs ${statusClass}">${escapeHtml(statusText)}</span>
            </div>
        `;
    }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
    api('/api/settings')
        .then(applyThemeSettings)
        .catch(() => {});
    ['setting-theme-preset', ...THEME_FIELDS.map(([, elId]) => elId)]
        .forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('input', previewThemeFromForm);
            el.addEventListener('change', previewThemeFromForm);
        });
    ['setting-startup-mode', 'setting-startup-enabled', 'setting-startup-auto-elevate']
        .forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('change', () => syncStartupControls(id));
        });
});

function previewThemeFromForm() {
    applyThemeSettings({
        theme_preset: document.getElementById('setting-theme-preset')?.value || 'dark',
        theme_custom: readThemeCustomFromForm(),
    });
}
