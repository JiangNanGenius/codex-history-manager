const AMR_CAPABILITIES = ['text', 'vision', 'tools', 'reasoning', 'images', 'videos'];

let amrState = {
    groups: [],
    selectedGroupId: '',
    routeResult: null,
    error: '',
    loading: false,
};

async function loadAmrPage() {
    amrState.loading = true;
    renderAmrPage();
    await Promise.all([
        refreshAmrGroups(),
        typeof ensureProviderData === 'function' ? ensureProviderData() : Promise.resolve(),
    ]);
    amrState.loading = false;
    renderAmrPage();
    setStatus('AMR loaded');
}

async function refreshAmrGroups() {
    try {
        const data = await api('/api/amr/groups');
        amrState.groups = data.groups || [];
        if (!amrState.selectedGroupId && amrState.groups.length) {
            amrState.selectedGroupId = amrState.groups[0].id;
        }
        if (amrState.selectedGroupId && !amrState.groups.some(group => group.id === amrState.selectedGroupId)) {
            amrState.selectedGroupId = amrState.groups[0]?.id || '';
        }
        amrState.error = '';
    } catch (err) {
        amrState.groups = [];
        amrState.error = err.message || 'Failed to load AMR groups';
    }
}

function renderAmrPage() {
    const root = document.getElementById('amr-root');
    if (!root) return;
    const groups = amrState.groups || [];
    const selected = getSelectedAmrGroup();
    const totalCandidates = groups.reduce((sum, group) => sum + (group.candidates || []).length, 0);
    const enabledCandidates = groups.reduce((sum, group) => {
        return sum + (group.candidates || []).filter(candidate => candidate.enabled !== false).length;
    }, 0);

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">Adaptive Model Rotation</h2>
                <p class="text-sm text-dark-400 mt-1">Configure local rotation groups and simulate capability/context routing before exposing them through Codex.</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('groups', `${groups.length} groups`, groups.length ? 'emerald' : 'dark')}
                ${renderStatusPill('candidates', `${enabledCandidates}/${totalCandidates} candidates`, enabledCandidates ? 'accent' : 'dark')}
                ${renderStatusPill('preview', 'no Codex writes', 'amber')}
            </div>
        </div>

        ${amrState.error ? `<div class="mt-4 text-sm text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-3">${escapeHtml(amrState.error)}</div>` : ''}

        <div class="grid grid-cols-1 2xl:grid-cols-3 gap-4 mt-6">
            <div class="space-y-4">
                ${renderAmrGroupList(groups)}
                ${renderAmrCandidatePalette()}
            </div>
            <div class="2xl:col-span-2 space-y-4">
                ${selected ? renderAmrGroupEditor(selected) : renderAmrEmptyEditor()}
                ${selected ? renderAmrRoutePreview(selected) : renderAmrRoutePreviewEmpty()}
            </div>
        </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

function renderAmrGroupList(groups) {
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">Rotation Groups</h3>
                <button onclick="createAmrGroup()" class="btn btn-secondary text-xs">New Group</button>
            </div>
            <div class="flex flex-wrap gap-2 mt-3">
                <button onclick="syncAmrFromProviders()" class="btn btn-primary text-xs">Sync From Providers</button>
                <button onclick="refreshAmrGroupsAndRender()" class="btn btn-secondary text-xs">Refresh</button>
            </div>
            <div class="space-y-2 mt-4">
                ${groups.map(renderAmrGroupListItem).join('') || renderEmptyState('No rotation groups yet.')}
            </div>
        </div>
    `;
}

function renderAmrGroupListItem(group) {
    const active = group.id === amrState.selectedGroupId;
    const summary = getAmrGroupSummary(group);
    return `
        <button onclick="selectAmrGroup('${escapeAttr(group.id)}')" class="provider-list-item stagger-item w-full text-left ${active ? 'active' : ''}">
            <div class="flex items-center justify-between gap-2">
                <div class="min-w-0">
                    <div class="font-medium text-sm text-white truncate">${escapeHtml(group.display_name || group.id)}</div>
                    <div class="text-xs text-dark-400 font-mono truncate">${escapeHtml(group.id)}</div>
                </div>
                <span class="status-dot ${summary.enabledCount ? 'bg-emerald-500' : 'bg-dark-500'}"></span>
            </div>
            <div class="flex flex-wrap gap-1 mt-2">
                ${renderMiniBadge(`${summary.enabledCount}/${summary.totalCount} enabled`)}
                ${summary.effectiveContext ? renderMiniBadge(formatNumber(summary.effectiveContext, { compact: false }) + ' ctx') : ''}
                ${summary.limitingCandidateId ? renderMiniBadge('limited by ' + summary.limitingCandidateId) : ''}
            </div>
        </button>
    `;
}

function renderAmrCandidatePalette() {
    const entries = getAmrProviderModelOptions();
    return `
        <div class="card">
            <h3 class="card-title">Provider Model Hints</h3>
            <div class="text-xs text-dark-500 mt-1">Use these IDs when adding candidates.</div>
            <div class="space-y-2 mt-3 max-h-[280px] overflow-y-auto">
                ${entries.slice(0, 80).map(entry => `
                    <div class="rounded-md border border-dark-800 bg-dark-950/40 px-3 py-2">
                        <div class="font-mono text-xs text-dark-200 truncate">${escapeHtml(entry.provider_id)} / ${escapeHtml(entry.model_id)}</div>
                        <div class="flex flex-wrap gap-1 mt-1">
                            ${renderMiniBadge(entry.alias || entry.provider_id)}
                            ${entry.context_window ? renderMiniBadge(formatNumber(entry.context_window, { compact: false }) + ' ctx') : ''}
                            ${capabilityBadges(entry.capabilities).join('')}
                        </div>
                    </div>
                `).join('') || renderEmptyState('Load providers to see model hints.')}
            </div>
        </div>
    `;
}

function renderAmrEmptyEditor() {
    return `
        <div class="card">
            <h3 class="card-title">Group Editor</h3>
            ${renderEmptyState('Create a group or sync candidates from providers.')}
        </div>
    `;
}

function renderAmrGroupEditor(group) {
    return `
        <div class="card">
            <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
                <div>
                    <h3 class="card-title">Group Editor</h3>
                    <div class="text-xs text-dark-500 font-mono">${escapeHtml(group.id || '')}</div>
                </div>
                <div class="flex flex-wrap gap-2">
                    <button onclick="addAmrCandidate()" class="btn btn-secondary text-xs">Add Candidate</button>
                    <button onclick="saveSelectedAmrGroup()" class="btn btn-primary text-xs">Save Group</button>
                    <button onclick="deleteSelectedAmrGroup()" class="btn btn-danger text-xs">Delete</button>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
                <div>
                    <label class="block text-xs font-medium text-dark-400 mb-1">Group ID</label>
                    <input id="amr-group-id" class="input w-full" value="${escapeAttr(group.id || '')}" readonly>
                </div>
                ${renderInput('amr-group-name', 'Display Name', group.display_name || group.id || '')}
            </div>
            <div class="mt-4">
                <div class="flex items-center justify-between gap-2">
                    <div class="text-xs text-dark-400">Candidates</div>
                    <div class="text-xs text-dark-500">Priority 1 is highest. Context is tokens.</div>
                </div>
                <datalist id="amr-provider-options">
                    ${getAmrProviderIds().map(value => `<option value="${escapeAttr(value)}"></option>`).join('')}
                </datalist>
                <datalist id="amr-model-options">
                    ${getAmrProviderModelOptions().map(entry => `<option value="${escapeAttr(entry.model_id)}"></option>`).join('')}
                </datalist>
                <div id="amr-candidates-list" class="space-y-3 mt-3">
                    ${(group.candidates || []).map(renderAmrCandidateEditor).join('') || renderEmptyState('No candidates in this group.')}
                </div>
            </div>
            <div id="amr-form-error" class="hidden mt-3 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
        </div>
    `;
}

function renderAmrCandidateEditor(candidate, index) {
    const caps = candidate.capabilities || {};
    const enabled = candidate.enabled !== false;
    return `
        <div class="rounded-lg border border-dark-700 bg-dark-950/40 p-3 stagger-item" data-amr-candidate-row="${index}" data-candidate-id="${escapeAttr(candidate.id || '')}">
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-2">
                <input class="input text-xs lg:col-span-3" data-amr-field="id" value="${escapeAttr(candidate.id || '')}" placeholder="candidate id">
                <input class="input text-xs lg:col-span-2" list="amr-provider-options" data-amr-field="provider_id" value="${escapeAttr(candidate.provider_id || '')}" placeholder="provider id">
                <input class="input text-xs lg:col-span-2" list="amr-model-options" data-amr-field="model_id" value="${escapeAttr(candidate.model_id || '')}" placeholder="model id">
                <input class="input text-xs lg:col-span-1" type="number" min="1" step="1" data-amr-field="priority" value="${escapeAttr(candidate.priority || 2)}" placeholder="priority">
                <input class="input text-xs lg:col-span-2" type="number" min="0" step="1000" data-amr-field="context_window" value="${escapeAttr(candidate.context_window || 0)}" placeholder="context">
                <label class="flex items-center gap-2 text-xs cursor-pointer bg-dark-900/60 border border-dark-700 rounded-md px-2 py-2 lg:col-span-1">
                    <input data-amr-field="enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${enabled ? 'checked' : ''}>
                    <span>on</span>
                </label>
                <button onclick="removeAmrCandidate(${index})" class="btn btn-danger text-xs lg:col-span-1">Remove</button>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2 mt-3">
                ${AMR_CAPABILITIES.map(capability => `
                    <label class="flex items-center gap-2 text-xs cursor-pointer bg-dark-900/60 border border-dark-700 rounded-md px-2 py-2">
                        <input data-amr-capability="${escapeAttr(capability)}" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${caps[capability] ? 'checked' : ''}>
                        <span>${escapeHtml(capability)}</span>
                    </label>
                `).join('')}
            </div>
        </div>
    `;
}

function renderAmrRoutePreview(group) {
    const result = amrState.routeResult;
    const resultText = result ? JSON.stringify(result, null, 2) : 'No route preview yet.';
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">Route Preview</h3>
                <span class="text-xs text-emerald-300">read-only</span>
            </div>
            <div class="text-xs text-dark-500 mt-1">Uses the saved group state. No provider request or Codex write is performed.</div>
            <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2 mt-3">
                ${AMR_CAPABILITIES.map(capability => renderAmrRouteCapabilityToggle(capability, capability === 'text')).join('')}
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                <input id="amr-route-context" class="input text-sm" type="number" min="0" step="1000" value="${escapeAttr(getAmrGroupSummary(group).effectiveContext || 0)}" placeholder="required context tokens">
                <button id="amr-route-btn" onclick="runAmrRoutePreview()" class="btn btn-secondary text-xs">Preview Route</button>
            </div>
            <pre id="amr-route-result" class="preview-code mt-3">${escapeHtml(resultText)}</pre>
        </div>
    `;
}

function renderAmrRoutePreviewEmpty() {
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">Route Preview</h3>
                <span class="text-xs text-emerald-300">read-only</span>
            </div>
            ${renderEmptyState('Create or select a saved group to preview routing.')}
        </div>
    `;
}

function renderAmrRouteCapabilityToggle(capability, checked) {
    return `
        <label class="flex items-center gap-2 text-xs cursor-pointer bg-dark-900/60 border border-dark-700 rounded-md px-2 py-2">
            <input data-amr-route-capability="${escapeAttr(capability)}" type="checkbox"
                class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${checked ? 'checked' : ''}>
            <span>${escapeHtml(capability)}</span>
        </label>
    `;
}

function getSelectedAmrGroup() {
    return (amrState.groups || []).find(group => group.id === amrState.selectedGroupId) || null;
}

function selectAmrGroup(groupId) {
    amrState.selectedGroupId = groupId;
    amrState.routeResult = null;
    renderAmrPage();
}

async function refreshAmrGroupsAndRender() {
    await refreshAmrGroups();
    renderAmrPage();
}

async function createAmrGroup() {
    try {
        const data = await api('/api/amr/groups', {
            method: 'POST',
            body: JSON.stringify({ display_name: 'New Rotation Group', candidates: [] }),
        });
        amrState.selectedGroupId = data.group.id;
        amrState.routeResult = null;
        await refreshAmrGroups();
        renderAmrPage();
        showToast('AMR group created', 'success');
    } catch (err) {
        showToast('Create failed: ' + err.message, 'error');
    }
}

async function syncAmrFromProviders() {
    try {
        const data = await api('/api/amr/sync-from-providers', {
            method: 'POST',
            body: '{}',
        });
        amrState.selectedGroupId = data.group.id;
        amrState.routeResult = null;
        await refreshAmrGroups();
        renderAmrPage();
        showToast('AMR candidates synced from providers', 'success');
    } catch (err) {
        showToast('Sync failed: ' + err.message, 'error');
    }
}

async function saveSelectedAmrGroup() {
    const existing = getSelectedAmrGroup();
    if (!existing) return;
    const draft = readAmrGroupForm(existing);
    if (!draft) return;
    try {
        const data = await api('/api/amr/groups/' + encodeURIComponent(existing.id), {
            method: 'PUT',
            body: JSON.stringify(draft),
        });
        amrState.selectedGroupId = data.group.id;
        amrState.routeResult = null;
        await refreshAmrGroups();
        renderAmrPage();
        showToast('AMR group saved', 'success');
    } catch (err) {
        showAmrFormError(err.message);
    }
}

async function deleteSelectedAmrGroup() {
    const group = getSelectedAmrGroup();
    if (!group) return;
    if (!confirm('Delete local AMR group "' + (group.display_name || group.id) + '"?')) return;
    try {
        await api('/api/amr/groups/' + encodeURIComponent(group.id), { method: 'DELETE' });
        amrState.selectedGroupId = '';
        amrState.routeResult = null;
        await refreshAmrGroups();
        renderAmrPage();
        showToast('AMR group deleted', 'success');
    } catch (err) {
        showToast('Delete failed: ' + err.message, 'error');
    }
}

function addAmrCandidate() {
    const group = readAmrGroupForm(getSelectedAmrGroup());
    if (!group) return;
    group.candidates.push({
        id: '',
        provider_id: '',
        model_id: '',
        priority: 2,
        enabled: true,
        context_window: 0,
        capabilities: { text: true },
    });
    amrState.groups = (amrState.groups || []).map(item => item.id === amrState.selectedGroupId ? { ...item, ...group } : item);
    renderAmrPage();
}

function removeAmrCandidate(index) {
    const group = readAmrGroupForm(getSelectedAmrGroup());
    if (!group) return;
    group.candidates.splice(index, 1);
    amrState.groups = (amrState.groups || []).map(item => item.id === amrState.selectedGroupId ? { ...item, ...group } : item);
    renderAmrPage();
}

function readAmrGroupForm(existing) {
    if (!existing) return null;
    const displayName = document.getElementById('amr-group-name')?.value || existing.display_name || existing.id;
    const candidates = Array.from(document.querySelectorAll('[data-amr-candidate-row]')).map(row => {
        const capabilities = {};
        AMR_CAPABILITIES.forEach(capability => {
            capabilities[capability] = Boolean(row.querySelector(`[data-amr-capability="${capability}"]`)?.checked);
        });
        return {
            id: row.querySelector('[data-amr-field="id"]')?.value || '',
            provider_id: row.querySelector('[data-amr-field="provider_id"]')?.value || '',
            model_id: row.querySelector('[data-amr-field="model_id"]')?.value || '',
            priority: parseInt(row.querySelector('[data-amr-field="priority"]')?.value || '2', 10) || 2,
            enabled: row.querySelector('[data-amr-field="enabled"]')?.checked !== false,
            context_window: parseInt(row.querySelector('[data-amr-field="context_window"]')?.value || '0', 10) || 0,
            capabilities,
        };
    });
    return {
        id: existing.id,
        display_name: displayName,
        candidates,
    };
}

async function runAmrRoutePreview() {
    const group = getSelectedAmrGroup();
    if (!group) return;
    const btn = document.getElementById('amr-route-btn');
    const resultEl = document.getElementById('amr-route-result');
    const capabilities = Array.from(document.querySelectorAll('[data-amr-route-capability]:checked'))
        .map(input => input.getAttribute('data-amr-route-capability'))
        .filter(Boolean);
    const context = parseInt(document.getElementById('amr-route-context')?.value || '0', 10) || 0;
    if (btn) btn.disabled = true;
    if (resultEl) resultEl.textContent = 'Previewing...';
    try {
        amrState.routeResult = await api('/api/amr/route', {
            method: 'POST',
            body: JSON.stringify({
                group_id: group.id,
                capabilities: capabilities.length ? capabilities : ['text'],
                context,
            }),
        });
        renderAmrPage();
    } catch (err) {
        amrState.routeResult = { success: false, error: err.message };
        renderAmrPage();
    } finally {
        if (btn) btn.disabled = false;
    }
}

function getAmrGroupSummary(group) {
    const candidates = group && Array.isArray(group.candidates) ? group.candidates : [];
    const enabled = candidates.filter(candidate => candidate.enabled !== false);
    const contexts = enabled.map(candidate => Number(candidate.context_window || 0)).filter(value => value > 0);
    const effectiveContext = contexts.length ? Math.min(...contexts) : 0;
    const limiting = effectiveContext
        ? enabled.find(candidate => Number(candidate.context_window || 0) === effectiveContext)
        : null;
    return {
        totalCount: candidates.length,
        enabledCount: enabled.length,
        effectiveContext,
        limitingCandidateId: limiting ? (limiting.id || `${limiting.provider_id}/${limiting.model_id}`) : '',
    };
}

function getAmrProviderIds() {
    return Array.from(new Set((providerState.providers || []).map(provider => provider.id).filter(Boolean))).sort();
}

function getAmrProviderModelOptions() {
    const providers = providerState.providers || [];
    const entries = [];
    providers.forEach(provider => {
        (provider.models || []).forEach(model => {
            entries.push({
                provider_id: provider.id || '',
                alias: provider.short_alias || '',
                model_id: model.id || '',
                context_window: model.context_window || 0,
                capabilities: model.capabilities || provider.capabilities || {},
            });
        });
    });
    return entries.filter(entry => entry.provider_id && entry.model_id);
}

function showAmrFormError(message) {
    const el = document.getElementById('amr-form-error');
    if (!el) {
        showToast(message, 'error');
        return;
    }
    el.textContent = message || 'AMR form error';
    el.classList.remove('hidden');
}
