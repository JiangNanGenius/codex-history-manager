/**
 * settings.js - 设置模块
 * 加载、保存、重置、自动检测
 */

async function loadSettings() {
    try {
        const data = await api('/api/settings');
        populateSettingsForm(data);
        setStatus(t('settingsLoaded'));

        // Auto-detect paths if key paths are missing
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
        'setting-page-size': 'page_size',
        'setting-backup-interval': 'backup_interval_hours',
        'setting-max-backups': 'max_backups',
        'setting-large-threshold': 'large_file_threshold_mb',
        'setting-max-lines': 'max_lines_large_file',
    };

    for (const [elId, key] of Object.entries(fields)) {
        const el = document.getElementById(elId);
        if (el && data[key] !== undefined) {
            el.value = data[key];
        }
    }

    // Checkboxes
    const checkboxFields = {
        'setting-auto-backup': 'auto_backup',
        'setting-use-codex-pp': 'use_codex_plus_plus',
        'setting-dark-mode': 'dark_mode',
    };

    for (const [elId, key] of Object.entries(checkboxFields)) {
        const el = document.getElementById(elId);
        if (el && data[key] !== undefined) {
            el.checked = Boolean(data[key]);
        }
    }
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
        page_size: parseInt(document.getElementById('setting-page-size')?.value) || 50,
        backup_interval_hours: parseInt(document.getElementById('setting-backup-interval')?.value) || 6,
        max_backups: parseInt(document.getElementById('setting-max-backups')?.value) || 20,
        large_file_threshold_mb: parseInt(document.getElementById('setting-large-threshold')?.value) || 500,
        max_lines_large_file: parseInt(document.getElementById('setting-max-lines')?.value) || 2000,
        auto_backup: document.getElementById('setting-auto-backup')?.checked || false,
        use_codex_plus_plus: document.getElementById('setting-use-codex-pp')?.checked || false,
        dark_mode: document.getElementById('setting-dark-mode')?.checked ?? true,
    };

    try {
        const result = await api('/api/settings', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        if (result.success) {
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

async function runAutoDetect() {
    try {
        setStatus(t('detectingPaths'));
        const data = await api('/api/detect');
        renderDetectResults(data);
        showToast(t('autoDetectComplete'), 'success');

        // Also update settings form with detected values
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
        { label: t('dbPath'), value: data.db_path, icon: '🗄️' },
        { label: t('sessionsDir'), value: data.sessions_dir, icon: '📂' },
        { label: t('archivedDir'), value: data.archived_dir, icon: '📁' },
        { label: t('codexCliPath'), value: data.codex_cli_path, icon: '⚡' },
        { label: t('codexPPPath'), value: data.codex_plus_plus_path, icon: '🚀' },
    ];

    if (data.codex_config) {
        items.push(
            { label: 'Config Provider', value: data.codex_config.model_provider, icon: '🔧' },
            { label: 'Config Model', value: data.codex_config.model, icon: '🤖' },
        );
    }

    container.innerHTML = items.map(item => {
        const found = item.value ? true : false;
        const statusClass = found ? 'text-emerald-400' : 'text-dark-500';
        const statusText = found ? '✓ ' + t('success') : '✗ ' + t('error');

        return `
            <div class="flex items-center gap-3 py-2 px-3 rounded-lg bg-dark-900/50">
                <span class="text-lg">${item.icon}</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs text-dark-400">${item.label}</div>
                    <div class="text-sm font-mono text-dark-200 truncate">${item.value || '(empty)'}</div>
                </div>
                <span class="text-xs ${statusClass}">${statusText}</span>
            </div>
        `;
    }).join('');
}
