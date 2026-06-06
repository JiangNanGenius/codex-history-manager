/**
 * sync.js - 同步模块
 * 同步状态、预览、执行、一键同步重启
 */

async function loadSyncStatus() {
    try {
        const data = await api('/api/sync/status');

        // Current config
        document.getElementById('sync-current-provider').textContent = data.current_provider || '-';
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

    if (distribution.length === 0) {
        container.innerHTML = '<div class="text-dark-400 text-sm">' + t('noData') + '</div>';
        return;
    }

    const maxCount = Math.max(...distribution.map(d => d.count), 1);

    container.innerHTML = distribution.map(d => {
        const pct = Math.round((d.count / maxCount) * 100);
        return `
            <div class="provider-bar">
                <div class="w-24 text-sm font-medium text-dark-200 truncate">${escapeHtml(d.provider || 'unknown')}</div>
                <div class="flex-1 bg-dark-700 rounded overflow-hidden">
                    <div class="provider-bar-fill" style="width: ${pct}%">${formatNumber(d.count)}</div>
                </div>
            </div>
        `;
    }).join('');
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

    if (!confirm(t('confirmExecuteSync'))) return;

    try {
        setStatus(t('executingSyncStatus'));
        const data = await api('/api/sync/execute', {
            method: 'POST',
            body: JSON.stringify({ target_provider: targetProvider, target_model: targetModel }),
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

        // Step 2: Start endpoint auto-syncs current provider/model before launching.
        setStatus(t('oneClickStepStart'));
        const startData = await api('/api/codex/start', { method: 'POST' });
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
