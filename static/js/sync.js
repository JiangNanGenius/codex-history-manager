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

        setStatus('Sync status loaded');
    } catch (err) {
        showToast('Failed to load sync status: ' + err.message, 'error');
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

    if (running) {
        container.innerHTML = `
            <span class="status-dot bg-red-500 pulse-dot"></span>
            <span class="text-sm text-red-400">${t('running')} (PID: ${pids.join(', ')})</span>
        `;
    } else {
        container.innerHTML = `
            <span class="status-dot bg-emerald-500"></span>
            <span class="text-sm text-emerald-400">${t('notRunning')}</span>
        `;
    }
}

async function checkCodexStatus() {
    try {
        const data = await api('/api/codex/status');
        renderCodexStatus(data.running, data.pids);
        showToast(data.running ? 'Codex is running' : 'Codex is not running', 'info');
    } catch (err) {
        showToast('Failed to check: ' + err.message, 'error');
    }
}

async function killCodex() {
    if (!confirm('Kill Codex process?')) return;
    try {
        const data = await api('/api/codex/kill', { method: 'POST' });
        showToast(data.message, data.success ? 'success' : 'error');
        setTimeout(() => loadSyncStatus(), 1000);
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    }
}

async function syncPreview() {
    const targetProvider = document.getElementById('sync-target-provider')?.value || '';
    const targetModel = document.getElementById('sync-target-model')?.value || '';

    try {
        setStatus('Preview sync...');
        const data = await api('/api/sync/preview', {
            method: 'POST',
            body: JSON.stringify({ target_provider: targetProvider, target_model: targetModel }),
        });
        showSyncResult(data, true);
        showToast('Preview complete', 'info');
    } catch (err) {
        showToast('Preview failed: ' + err.message, 'error');
    }
}

async function syncExecute() {
    const targetProvider = document.getElementById('sync-target-provider')?.value || '';
    const targetModel = document.getElementById('sync-target-model')?.value || '';

    if (!confirm('Execute sync? This will modify the database and session files.')) return;

    try {
        setStatus('Executing sync...');
        const data = await api('/api/sync/execute', {
            method: 'POST',
            body: JSON.stringify({ target_provider: targetProvider, target_model: targetModel }),
        });
        showSyncResult(data, false);
        showToast('Sync complete!', 'success');
    } catch (err) {
        showToast('Sync failed: ' + err.message, 'error');
    }
}

async function oneClickSyncRestart() {
    if (!confirm('One-click sync + restart?\n\n1. Kill Codex\n2. Execute sync\n3. Restart Codex')) return;

    try {
        // Step 1: Kill
        setStatus('Step 1/3: Killing Codex...');
        await api('/api/codex/kill', { method: 'POST' });

        // Wait for process to exit
        await new Promise(r => setTimeout(r, 2000));

        // Step 2: Sync
        setStatus('Step 2/3: Executing sync...');
        const syncData = await api('/api/sync/execute', { method: 'POST' });

        // Step 3: Start
        setStatus('Step 3/3: Starting Codex...');
        const startData = await api('/api/codex/start', { method: 'POST' });

        showSyncResult(syncData, false);
        if (startData.success) {
            showToast('One-click sync restart complete!', 'success');
        } else {
            showToast('Sync done but restart failed: ' + startData.message, 'warning');
        }

        setTimeout(() => loadSyncStatus(), 1000);
    } catch (err) {
        showToast('One-click failed: ' + err.message, 'error');
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
    setStatus('Sync result displayed');
}
