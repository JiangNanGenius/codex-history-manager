/**
 * backup.js - 备份管理模块
 * 备份列表、创建、还原
 */

async function loadBackups() {
    try {
        const data = await api('/api/backups');
        renderBackupTable(data);
        setStatus(`${t('backupsLoaded')}: ${data.length}`);
    } catch (err) {
        showToast(t('failedLoadBackups') + err.message, 'error');
    }
}

function renderBackupTable(backups) {
    const tbody = document.getElementById('backups-table-body');
    if (!tbody) return;

    if (!backups || backups.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-8 text-dark-400">' + t('noBackups') + '</td></tr>';
        document.getElementById('backup-info').textContent = t('noBackups');
        return;
    }

    document.getElementById('backup-info').textContent = `${backups.length} ${t('backupsFound')}`;

    tbody.innerHTML = backups.map(b => {
        const btype = (b.meta && b.meta.backup_type) || 'full';
        const typeBadge = btype === 'incremental'
            ? '<span class="px-2 py-0.5 rounded text-xs bg-amber-500/15 text-amber-400">' + t('incrementalBackupShort') + '</span>'
            : '<span class="px-2 py-0.5 rounded text-xs bg-emerald-500/15 text-emerald-400">' + t('fullBackup') + '</span>';

        return `
            <tr>
                <td class="py-3 px-4 text-sm font-mono text-dark-200 max-w-sm truncate">${escapeHtml(b.name)}</td>
                <td class="py-3 px-4 text-right text-sm font-mono">${b.size_mb || '0'}</td>
                <td class="py-3 px-4 text-sm text-dark-400">${b.mtime || '-'}</td>
                <td class="py-3 px-4">${typeBadge}</td>
                <td class="py-3 px-4 text-center">
                    ${btype === 'full' ? `<button onclick="restoreBackup('${escapeHtml(b.name)}')" class="btn btn-success text-xs">${t('restore')}</button>` : '<span class="text-dark-500 text-xs">N/A</span>'}
                </td>
            </tr>
        `;
    }).join('');
}

async function createFullBackup() {
    try {
        setStatus(t('creatingFullBackup'));
        const data = await api('/api/backups/create', { method: 'POST' });
        if (data.success) {
            showToast(`${t('backupCreatedSize')}: ${data.size_mb} MB`, 'success');
            loadBackups();
        } else {
            showToast(t('backupFailed') + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast(t('backupFailed') + err.message, 'error');
    }
}

async function createIncrementalBackup() {
    try {
        setStatus(t('creatingIncrementalBackup'));
        const data = await api('/api/backups/incremental', { method: 'POST' });
        if (data.success) {
            showToast(`${t('incrementalBackupChanges')}: ${data.changed_count || 0}`, 'success');
            loadBackups();
        } else {
            showToast(t('backupFailed') + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast(t('backupFailed') + err.message, 'error');
    }
}

async function restoreBackup(backupName) {
    if (!confirm(t('confirmRestore') + backupName)) return;

    try {
        setStatus(t('restoringBackup'));
        const data = await api(`/api/backups/${encodeURIComponent(backupName)}/restore`, { method: 'POST' });
        if (data.success) {
            showToast(t('restoreSuccessful'), 'success');
            loadBackups();
        } else {
            showToast(t('restoreFailed') + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast(t('restoreFailed') + err.message, 'error');
    }
}
