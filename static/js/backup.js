/**
 * backup.js - 备份管理模块
 * 备份列表、创建、还原
 */

async function loadBackups() {
    try {
        const data = await api('/api/backups');
        renderBackupTable(data);
        setStatus(`Backups: ${data.length} files`);
    } catch (err) {
        showToast('Failed to load backups: ' + err.message, 'error');
    }
}

function renderBackupTable(backups) {
    const tbody = document.getElementById('backups-table-body');
    if (!tbody) return;

    if (!backups || backups.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-8 text-dark-400">No backups found</td></tr>';
        document.getElementById('backup-info').textContent = 'No backups yet';
        return;
    }

    document.getElementById('backup-info').textContent = `${backups.length} backup(s) found`;

    tbody.innerHTML = backups.map(b => {
        const btype = (b.meta && b.meta.backup_type) || 'full';
        const typeBadge = btype === 'incremental'
            ? '<span class="px-2 py-0.5 rounded text-xs bg-amber-500/15 text-amber-400">Incremental</span>'
            : '<span class="px-2 py-0.5 rounded text-xs bg-emerald-500/15 text-emerald-400">Full</span>';

        return `
            <tr>
                <td class="py-3 px-4 text-sm font-mono text-dark-200 max-w-sm truncate">${escapeHtml(b.name)}</td>
                <td class="py-3 px-4 text-right text-sm font-mono">${b.size_mb || '0'}</td>
                <td class="py-3 px-4 text-sm text-dark-400">${b.mtime || '-'}</td>
                <td class="py-3 px-4">${typeBadge}</td>
                <td class="py-3 px-4 text-center">
                    ${btype === 'full' ? `<button onclick="restoreBackup('${escapeHtml(b.name)}')" class="btn btn-success text-xs">Restore</button>` : '<span class="text-dark-500 text-xs">N/A</span>'}
                </td>
            </tr>
        `;
    }).join('');
}

async function createFullBackup() {
    try {
        setStatus('Creating full backup...');
        const data = await api('/api/backups/create', { method: 'POST' });
        if (data.success) {
            showToast(`Backup created: ${data.size_mb} MB`, 'success');
            loadBackups();
        } else {
            showToast('Backup failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Backup failed: ' + err.message, 'error');
    }
}

async function createIncrementalBackup() {
    try {
        setStatus('Creating incremental backup...');
        const data = await api('/api/backups/incremental', { method: 'POST' });
        if (data.success) {
            showToast(`Incremental backup: ${data.changed_count || 0} changes`, 'success');
            loadBackups();
        } else {
            showToast('Backup failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Backup failed: ' + err.message, 'error');
    }
}

async function restoreBackup(backupName) {
    if (!confirm(`Restore from this backup?\n\nCurrent database will be backed up, then replaced.\n\n${backupName}`)) return;

    try {
        setStatus('Restoring backup...');
        const data = await api(`/api/backups/${encodeURIComponent(backupName)}/restore`, { method: 'POST' });
        if (data.success) {
            showToast('Restore successful!', 'success');
            loadBackups();
        } else {
            showToast('Restore failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Restore failed: ' + err.message, 'error');
    }
}
