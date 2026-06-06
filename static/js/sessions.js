/**
 * sessions.js - 会话浏览模块
 * 列表、搜索、分页、详情查看
 */

let sessionPage = 0;
let sessionTotal = 0;
let sessionPageSize = 50;

async function loadSessions() {
    const search = document.getElementById('session-search')?.value || '';
    const filter = document.getElementById('filter-status')?.value || 'all';
    const source = document.getElementById('filter-source')?.value || 'all';
    const model = document.getElementById('filter-model')?.value || '';
    const provider = document.getElementById('filter-provider')?.value || '';
    const sortBy = document.getElementById('sort-by')?.value || 'created_at_ms';

    try {
        const data = await api(
            `/api/sessions?page=${sessionPage}&page_size=${sessionPageSize}` +
            `&search=${encodeURIComponent(search)}&filter=${filter}&source=${source}` +
            `&model=${encodeURIComponent(model)}&provider=${encodeURIComponent(provider)}` +
            `&sort_by=${sortBy}&sort_order=desc`
        );

        sessionTotal = data.total;
        sessionPageSize = data.page_size;
        renderSessionTable(data.sessions);
        renderSessionPagination();
        loadFilterOptions();

        setStatus(`Sessions: ${data.sessions.length} / ${data.total}`);
    } catch (err) {
        showToast('Failed to load sessions: ' + err.message, 'error');
    }
}

function renderSessionTable(sessions) {
    const tbody = document.getElementById('sessions-table-body');
    if (!tbody) return;

    if (!sessions || sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-dark-400">' + t('noSessions') + '</td></tr>';
        return;
    }

    tbody.innerHTML = sessions.map(s => {
        const archived = s.archived ? '<span class="px-1.5 py-0.5 rounded text-xs bg-dark-600 text-dark-300">' + t('filterArchived') + '</span>' : '<span class="px-1.5 py-0.5 rounded text-xs bg-emerald-500/20 text-emerald-400">' + t('filterActive') + '</span>';
        const title = escapeHtml(s.title || '(No Title)');
        const modelTag = s.model ? `<span class="px-1.5 py-0.5 rounded text-xs bg-dark-700 text-dark-300">${escapeHtml(s.model)}</span>` : '-';
        const providerTag = s.model_provider ? `<span class="px-1.5 py-0.5 rounded text-xs bg-accent-500/15 text-accent-400">${escapeHtml(s.model_provider)}</span>` : '-';
        const tokens = s.tokens_used ? formatNumber(s.tokens_used) : '-';
        const date = s.created_at ? formatDate(s.created_at) : '-';

        return `
            <tr class="cursor-pointer group" onclick="openSessionDetail('${s.id}')">
                <td class="py-3 px-4">
                    <div class="text-sm text-white group-hover:text-accent-400 transition truncate max-w-xs">${title}</div>
                </td>
                <td class="py-3 px-4">${modelTag}</td>
                <td class="py-3 px-4">${providerTag}</td>
                <td class="py-3 px-4 text-right font-mono text-sm">${tokens}</td>
                <td class="py-3 px-4 text-sm text-dark-400">${date}</td>
                <td class="py-3 px-4 text-center">${archived}</td>
                <td class="py-3 px-4 text-center">
                    <div class="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition">
                        <button onclick="event.stopPropagation(); toggleArchive('${s.id}', ${s.archived ? 0 : 1})" class="p-1 rounded hover:bg-dark-600 text-dark-400 hover:text-white" title="${s.archived ? 'Unarchive' : 'Archive'}">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"/></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function renderSessionPagination() {
    const totalPages = Math.max(1, Math.ceil(sessionTotal / sessionPageSize));
    const info = document.getElementById('sessions-info');
    const prevBtn = document.getElementById('btn-prev-page');
    const nextBtn = document.getElementById('btn-next-page');

    if (info) info.textContent = t('page') + ' ' + (sessionPage + 1) + ' ' + t('pageOf') + ' ' + totalPages + '  —  ' + t('totalSessions') + ': ' + sessionTotal;
    if (prevBtn) prevBtn.disabled = sessionPage <= 0;
    if (nextBtn) nextBtn.disabled = sessionPage >= totalPages - 1;
}

function prevPage() {
    if (sessionPage > 0) {
        sessionPage--;
        loadSessions();
    }
}

function nextPage() {
    const totalPages = Math.ceil(sessionTotal / sessionPageSize);
    if (sessionPage < totalPages - 1) {
        sessionPage++;
        loadSessions();
    }
}

async function loadFilterOptions() {
    try {
        const data = await api('/api/filters');
        const modelSelect = document.getElementById('filter-model');
        const providerSelect = document.getElementById('filter-provider');

        if (modelSelect && data.models) {
            const current = modelSelect.value;
            modelSelect.innerHTML = '<option value="">All Models</option>' +
                data.models.map(m => `<option value="${escapeHtml(m)}" ${m === current ? 'selected' : ''}>${escapeHtml(m)}</option>`).join('');
        }

        if (providerSelect && data.providers) {
            const current = providerSelect.value;
            providerSelect.innerHTML = '<option value="">All Providers</option>' +
                data.providers.map(p => `<option value="${escapeHtml(p)}" ${p === current ? 'selected' : ''}>${escapeHtml(p)}</option>`).join('');
        }
    } catch (err) {
        // Silently fail - filters are not critical
    }
}

async function openSessionDetail(sessionId) {
    try {
        const data = await api('/api/sessions/' + sessionId);
        renderSessionDetail(data);
        document.getElementById('session-detail-modal').classList.remove('hidden');
    } catch (err) {
        showToast('Failed to load session: ' + err.message, 'error');
    }
}

function renderSessionDetail(data) {
    // Title
    document.getElementById('detail-title').textContent = data.title || '(No Title)';

    // Meta
    const meta = document.getElementById('detail-meta');
    const metaItems = [];
    if (data.model) metaItems.push(`<span class="px-2 py-0.5 rounded bg-dark-700 text-dark-300">Model: ${escapeHtml(data.model)}</span>`);
    if (data.model_provider) metaItems.push(`<span class="px-2 py-0.5 rounded bg-accent-500/15 text-accent-400">Provider: ${escapeHtml(data.model_provider)}</span>`);
    if (data.tokens_used) metaItems.push(`<span class="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Tokens: ${formatNumber(data.tokens_used)}</span>`);
    if (data.created_at) metaItems.push(`<span class="text-dark-400">Created: ${formatDate(data.created_at)}</span>`);
    if (data.file_size_mb) metaItems.push(`<span class="text-dark-400">File: ${data.file_size_mb.toFixed(1)} MB</span>`);
    if (data.archived) metaItems.push('<span class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400">Archived</span>');
    if (data.is_large_file) metaItems.push('<span class="px-2 py-0.5 rounded bg-red-500/15 text-red-400">Large File</span>');
    if (data.truncated) metaItems.push('<span class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400">Truncated</span>');
    meta.innerHTML = metaItems.join('');

    // Messages
    const messagesDiv = document.getElementById('detail-messages');
    const messages = data.messages || [];

    if (data.file_not_found) {
        messagesDiv.innerHTML = '<div class="text-center py-8 text-dark-400">JSONL file not found</div>';
        return;
    }

    if (messages.length === 0) {
        messagesDiv.innerHTML = '<div class="text-center py-8 text-dark-400">No messages</div>';
        return;
    }

    messagesDiv.innerHTML = messages.map(msg => {
        const role = msg.role || 'unknown';
        const roleClass = `msg-${role}`;
        const roleLabel = { user: 'User', assistant: 'Assistant', system: 'System', tool: 'Tool', developer: 'Developer' }[role] || role;
        const roleColor = { user: 'text-accent-400', assistant: 'text-emerald-400', system: 'text-purple-400', tool: 'text-amber-400', developer: 'text-cyan-400' }[role] || 'text-dark-400';
        const content = formatMessageContent(msg.content || '');
        const ts = msg.timestamp ? `<span class="text-xs text-dark-500 ml-2">${msg.timestamp.slice(0, 19)}</span>` : '';

        return `
            <div class="rounded-lg border ${roleClass} p-3">
                <div class="flex items-center gap-1 mb-1">
                    <span class="text-xs font-semibold ${roleColor}">${roleLabel}</span>
                    ${ts}
                </div>
                <div class="msg-content text-sm text-dark-200 whitespace-pre-wrap break-words">${content}</div>
            </div>
        `;
    }).join('');
}

function formatMessageContent(content) {
    if (!content) return '';
    // Basic code block detection
    let html = escapeHtml(content);
    // Highlight code blocks (```...```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    return html;
}

function closeSessionDetail() {
    document.getElementById('session-detail-modal').classList.add('hidden');
}

async function toggleArchive(sessionId, archived) {
    try {
        const endpoint = archived ? `/api/sessions/${sessionId}/archive` : `/api/sessions/${sessionId}/unarchive`;
        await api(endpoint, { method: 'POST' });
        showToast(archived ? 'Session archived' : 'Session unarchived', 'success');
        loadSessions();
    } catch (err) {
        showToast('Failed: ' + err.message, 'error');
    }
}

// Close modal on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeSessionDetail();
    }
});

// Close modal on overlay click
document.addEventListener('click', (e) => {
    const modal = document.getElementById('session-detail-modal');
    if (e.target === modal) {
        closeSessionDetail();
    }
});
