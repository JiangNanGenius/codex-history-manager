/**
 * sync.js - 同步模块
 * 同步状态、预览、执行、一键同步重启
 */

async function loadSyncStatus() {
    try {
        const data = await api('/api/sync/status');

        // Current config
        document.getElementById('sync-current-provider').textContent = syncProviderDisplay(data);
        document.getElementById('sync-current-model').textContent = data.current_model || '-';

        // Provider distribution
        renderProviderDist(data.provider_distribution || []);

        // Codex process status
        renderCodexStatus(data.codex_running, data.codex_pids);

        // Pre-fill target fields
        const targetProvider = document.getElementById('sync-target-provider');
        const targetModel = document.getElementById('sync-target-model');
        if (targetProvider && !targetProvider.value) targetProvider.placeholder = data.current_provider || 'custom';
        if (targetModel && !targetModel.value) targetModel.placeholder = data.current_model || 'gpt-5';

        setStatus(t('syncCompleted'));
    } catch (err) {
        showToast(t('failedLoadSync') + err.message, 'error');
    }
}

function renderProviderDist(distribution) {
    const container = document.getElementById('sync-provider-dist');
    if (!container) return;

    const rows = (Array.isArray(distribution) ? distribution : [])
        .map(d => ({
            provider: d.provider || 'unknown',
            tokens: Number(d.count || 0),
        }))
        .filter(d => d.tokens > 0);
    const totalTokens = rows.reduce((sum, item) => sum + item.tokens, 0);

    if (rows.length === 0 || totalTokens <= 0) {
        container.innerHTML = '<div class="text-dark-400 text-sm">' + escapeHtml(t('noProviderUsageHistory')) + '</div>';
        return;
    }

    const maxTokens = Math.max(...rows.map(d => d.tokens), 1);

    container.innerHTML = `
        <div class="text-xs text-dark-500 mb-3">${escapeHtml(t('historyProviderUsageDesc'))}</div>
        ${rows.map(d => {
        const width = Math.max(4, Math.round((d.tokens / maxTokens) * 100));
        const share = Math.round((d.tokens / totalTokens) * 100);
        return `
            <div class="rounded-lg border border-dark-800 bg-dark-900/40 p-3">
                <div class="flex items-center justify-between gap-3">
                    <div class="text-sm font-medium text-dark-100 truncate">${escapeHtml(d.provider)}</div>
                    <div class="text-xs text-dark-400">${escapeHtml(t('providerUsageShare', { value: share }))}</div>
                </div>
                <div class="mt-2 bg-dark-800 rounded-full overflow-hidden">
                    <div class="provider-bar-fill rounded-full px-2 py-1 text-xs font-semibold text-white" style="width: ${width}%">
                        ${escapeHtml(t('providerUsageTokens', { value: formatNumber(d.tokens) }))}
                    </div>
                </div>
            </div>
        `;
    }).join('')}
    `;
}

function syncProviderDisplay(data) {
    if (data && data.current_provider_source === 'official_oauth') {
        return t('officialOpenAIProvider');
    }
    return (data && data.current_provider) || '-';
}

function renderCodexStatus(running, pids) {
    const container = document.getElementById('sync-codex-status');
    if (!container) return;

    const pidList = Array.isArray(pids) ? pids : [];

    if (running && pidList.length > 0) {
        container.innerHTML = `
            <span class="status-dot bg-emerald-500 pulse-dot"></span>
            <span class="text-sm text-emerald-400">${t('codexRunning')} (PID: ${pidList.join(', ')})</span>
        `;
    } else {
        container.innerHTML = `
            <span class="status-dot bg-dark-500"></span>
            <span class="text-sm text-dark-400">${t('codexNotRunning')}</span>
        `;
    }
}

async function checkCodexStatus() {
    try {
        const data = await api('/api/codex/status');
        renderCodexStatus(data.running, data.pids);
        showToast(data.running ? t('codexIsRunning') : t('codexIsNotRunning'), 'info');
    } catch (err) {
        showToast(t('failedCheck') + err.message, 'error');
    }
}

async function killCodex() {
    if (!confirm(t('killCodex') + '?')) return;
    try {
        const data = await api('/api/codex/kill', { method: 'POST' });
        showToast(data.message, data.success ? 'success' : 'error');
        setTimeout(() => loadSyncStatus(), 1000);
    } catch (err) {
        showToast(t('failedKillCodex') + err.message, 'error');
    }
}

async function syncPreview() {
    const targetProvider = document.getElementById('sync-target-provider')?.value || '';
    const targetModel = document.getElementById('sync-target-model')?.value || '';

    try {
        setStatus(t('previewSyncStatus'));
        const data = await api('/api/sync/preview', {
            method: 'POST',
            body: JSON.stringify({ target_provider: targetProvider, target_model: targetModel }),
        });
        showSyncResult(data, true);
        showToast(t('previewChanges'), 'info');
    } catch (err) {
        showToast(t('failedPreviewSync') + err.message, 'error');
    }
}

async function syncExecute() {
    const targetProvider = document.getElementById('sync-target-provider')?.value || '';
    const targetModel = document.getElementById('sync-target-model')?.value || '';
    const backupBeforeSync = document.getElementById('sync-backup-before')?.checked === true;

    if (!confirm(t('confirmExecuteSync'))) return;

    try {
        setStatus(t('executingSyncStatus'));
        const data = await api('/api/sync/execute', {
            method: 'POST',
            body: JSON.stringify({
                target_provider: targetProvider,
                target_model: targetModel,
                backup_before_sync: backupBeforeSync,
            }),
        });
        showSyncResult(data, false);
        showToast(t('syncCompleted'), 'success');
    } catch (err) {
        showToast(t('failedExecuteSync') + err.message, 'error');
    }
}

async function oneClickSyncRestart() {
    if (!confirm(t('confirmOneClickSyncRestart'))) return;

    try {
        // Step 1: Kill
        setStatus(t('oneClickStepKill'));
        await api('/api/codex/kill', { method: 'POST' });

        // Wait for process to exit
        await new Promise(r => setTimeout(r, 2000));

        const backupBeforeSync = document.getElementById('sync-backup-before')?.checked === true;

        // Step 2: Start endpoint auto-syncs current provider/model before launching.
        setStatus(t('oneClickStepStart'));
        const startData = await startCodexWithProgress({
            start_mode: 'preserve_login_proxy',
            backup_before_sync: backupBeforeSync,
        }, {
            onProgress: (job) => {
                const progress = Math.min(Math.max(Number(job.progress || 0), 0), 100);
                setStatus(`${job.message || t('codexStartRequested')} (${progress}%)`);
            },
        });
        const syncData = startData.sync || {};

        showSyncResult(syncData, false);
        if (startData.success) {
            showToast(t('oneClickSyncCompleted'), 'success');
        } else {
            showToast(t('syncCompleted') + ' ' + t('notRunning') + ': ' + startData.message, 'warning');
        }

        setTimeout(() => loadSyncStatus(), 1000);
    } catch (err) {
        showToast(t('failedOneClickSync') + err.message, 'error');
    }
}

function showSyncResult(data, isPreview) {
    const container = document.getElementById('sync-result');
    const textEl = document.getElementById('sync-result-text');
    if (!container || !textEl) return;

    container.classList.remove('hidden');

    const mode = isPreview ? 'DRY RUN (Preview)' : 'EXECUTED';
    let output = `═══ ${mode} ═══\n\n`;
    output += `Database (sqlite):\n`;
    output += `  Scanned: ${data.db_threads_seen} threads, Need update: ${data.db_threads_updated}\n\n`;
    output += `Rollout Files (jsonl):\n`;
    output += `  Scanned: ${data.rollout_files_seen}, Need update: ${data.rollout_files_updated}\n\n`;
    output += `Session Index:\n`;
    output += `  Scanned: ${data.index_rows_seen}, Updated: ${data.index_rows_updated}\n`;

    if (data.malformed_lines) {
        output += `\nMalformed lines skipped: ${data.malformed_lines}\n`;
    }
    if (data.backup_path) {
        output += `\nSafety backup:\n  ${data.backup_path}\n`;
    } else if (data.skipped_backup) {
        output += `\nSafety backup:\n  ${t('backupSkipped')}\n`;
    }
    if (data.errors && data.errors.length > 0) {
        output += `\nErrors:\n`;
        data.errors.forEach(e => { output += `  - ${e}\n`; });
    }
    if (!data.changed) {
        output += `\nNo changes needed - all records are up to date.\n`;
    } else if (!isPreview) {
        output += `\nSync complete! Restart Codex to see changes.\n`;
    }

    textEl.textContent = output;
    setStatus(t('syncResultDisplayed'));
}
