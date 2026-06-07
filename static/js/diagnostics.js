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
        setStatus(t('diagLoaded'));
    } catch (err) {
        diagnosticsState.error = err.message;
        showToast(t('diagLoadFailed') + err.message, 'error');
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
                <h2 class="text-2xl font-semibold text-white">${diagEscapeHtml(t('diagTitle'))}</h2>
                <p class="text-sm text-dark-400 mt-1">${diagEscapeHtml(t('diagDesc'))}</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="loadDiagnosticsPage()" class="btn btn-secondary">${diagEscapeHtml(t('refresh'))}</button>
                <button onclick="exportSafeDiagnostics()" class="btn btn-primary">${diagEscapeHtml(t('exportSafeBundle'))}</button>
            </div>
        </div>
        ${diagnosticsState.error ? `<div class="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">${diagEscapeHtml(diagnosticsState.error)}</div>` : ''}
        ${diagnosticsState.loading ? `<div class="card"><div class="text-sm text-dark-300">${diagEscapeHtml(t('loadingDiagnostics'))}</div></div>` : renderDiagnosticsContent(data)}
    `;
}

function renderDiagnosticsContent(data) {
    if (!data || Object.keys(data).length === 0) {
        return `<div class="card"><div class="text-sm text-dark-400">${diagEscapeHtml(t('noDiagnosticsLoaded'))}</div></div>`;
    }
    return `
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            ${renderDiagnosticCard(t('codexSettings'), summarizeCodexConfig(data.codex_config), 'codex_config')}
            ${renderDiagnosticCard(t('loginStatus'), summarizeAuthMode(data.auth_mode), 'auth_mode')}
            ${renderDiagnosticCard(t('localConnection'), summarizeProxy(data.local_proxy), 'local_proxy')}
            ${renderDiagnosticCard(t('providersLabel'), summarizeProviders(data.providers), 'providers')}
            ${renderDiagnosticCard(t('amrTitle'), summarizeAmr(data.amr), 'amr')}
            ${renderDiagnosticCard(t('quotaLabel'), summarizeQuota(data.quota), 'quota')}
            ${renderDiagnosticCard(t('currencyLabel'), summarizeCurrency(data.currency), 'currency')}
            ${renderDiagnosticCard(t('safetyCheck'), summarizePermissions(data.codex_permissions), 'codex_permissions')}
            ${renderDiagnosticCard(t('approvalsLabel'), summarizeApprovalBridge(data.codex_approval_bridge), 'codex_approval_bridge')}
        </div>
        ${renderProviderMediaRoutes(data.providers)}
        <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div class="card xl:col-span-2">
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <h3 class="card-title">${diagEscapeHtml(t('safeDetailsPreview'))}</h3>
                    <span class="text-xs text-dark-500">${diagEscapeHtml(t('collectedAt', { value: data.collected_at || '-' }))}</span>
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
            <button onclick="scrollDiagnosticsPreview('${diagEscapeAttr(sectionKey)}')" class="btn btn-ghost text-xs mt-4">${diagEscapeHtml(t('viewDetails'))}</button>
        </div>
    `;
}

function diagnosticsCollectionCount(value) {
    if (Array.isArray(value)) return value.length;
    if (value && typeof value === 'object') return Object.keys(value).length;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
}

function summarizeCodexConfig(section = {}) {
    const exists = Boolean(section.exists || section.config_exists);
    return {
        tone: exists ? 'emerald' : 'amber',
        status: exists ? t('statusFound') : t('statusMissing'),
        primary: section.path || section.config_path || 'config.toml',
        detail: section.error || (exists ? t('settingsReadable') : t('settingsMissing')),
    };
}

function summarizeAuthMode(section = {}) {
    const rawMode = section.mode || section.auth_mode || 'unknown';
    const mode = rawMode === 'unknown' ? t('statusUnknown') : rawMode;
    const hasError = Boolean(section.error);
    return {
        tone: hasError ? 'red' : (rawMode === 'unknown' ? 'amber' : 'emerald'),
        status: mode,
        primary: mode,
        detail: section.error || section.note || t('authRedacted'),
    };
}

function summarizeProxy(section = {}) {
    const running = Boolean(section.running || section.is_running);
    const actualPort = section.actual_port || section.port || '';
    return {
        tone: running ? 'emerald' : 'dark',
        status: running ? t('statusRunning') : t('statusStopped'),
        primary: actualPort ? t('portValue', { port: actualPort }) : t('notBound'),
        detail: section.backoff_used ? t('localConnectionMovedPort') : (section.last_error || t('noLocalConnectionProblem')),
    };
}

function summarizeProviders(section = {}) {
    const count = Number(section.count ?? diagnosticsCollectionCount(section.providers));
    const mediaReady = Number(section.media_route_ready_count || 0);
    const mediaBlocked = Number(section.media_route_blocked_count || 0);
    const mediaDetail = mediaReady || mediaBlocked
        ? t('imageVideoChecksSummary', { ready: mediaReady, blocked: mediaBlocked })
        : '';
    return {
        tone: count > 0 ? 'emerald' : 'amber',
        status: t('providerCountConfigured', { count }),
        primary: t('providerCountPrimary', { count }),
        detail: [section.store_path ? t('providerListAvailable') : t('providerListMissing'), mediaDetail].filter(Boolean).join(' '),
    };
}

function renderProviderMediaRoutes(section = {}) {
    const providers = Array.isArray(section.providers) ? section.providers : [];
    return `
        <div class="card">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 class="card-title">${diagEscapeHtml(t('imageVideoSupport'))}</h3>
                    <p class="text-xs text-dark-400 mt-1">${diagEscapeHtml(t('imageVideoSupportDesc'))}</p>
                </div>
                <button onclick="scrollDiagnosticsPreview('providers')" class="btn btn-ghost text-xs">${diagEscapeHtml(t('viewDetails'))}</button>
            </div>
            <div class="mt-4 grid grid-cols-1 xl:grid-cols-2 gap-3">
                ${providers.length ? providers.map(renderProviderMediaRouteCard).join('') : `<div class="text-sm text-dark-400">${diagEscapeHtml(t('noEnabledProvidersInDiagnostics'))}</div>`}
            </div>
        </div>
    `;
}

function renderProviderMediaRouteCard(provider = {}) {
    const route = provider.media_route || {};
    const checks = Array.isArray(route.checks) ? route.checks : [];
    const ready = Number(route.ready_count || 0);
    const blocked = Number(route.blocked_count || 0);
    const providerName = provider.display_name || provider.id || t('providerFallbackName');
    const alias = provider.short_alias ? t('aliasLabel', { alias: provider.short_alias }) : '';
    const format = provider.api_format || t('apiFormatUnset');
    const tone = route.error ? 'red' : (ready > 0 ? 'emerald' : (blocked > 0 ? 'amber' : 'dark'));
    const liveLabel = route.live_forwarding_enabled ? t('readyShort') : t('needsSetup');
    const providerDetails = [provider.id, alias, format].filter(Boolean).join(' | ');
    return `
        <div class="rounded-lg border border-dark-800 bg-dark-950/40 p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div class="text-sm font-semibold text-white">${diagEscapeHtml(providerName)}</div>
                    ${providerDetails ? `
                        <details class="mt-1">
                            <summary class="text-xs text-dark-500 cursor-pointer">${diagEscapeHtml(t('viewDetails'))}</summary>
                            <div class="text-xs text-dark-500 mt-1 break-all">${diagEscapeHtml(providerDetails)}</div>
                        </details>
                    ` : ''}
                </div>
                <span class="status-pill status-pill-${diagEscapeAttr(tone)}">${diagEscapeHtml(liveLabel)}</span>
            </div>
            <div class="enhance-status-strip mt-3">
                <span class="status-pill status-pill-emerald">${diagEscapeHtml(t('readyCount', { count: ready }))}</span>
                <span class="status-pill status-pill-amber">${diagEscapeHtml(t('needsSetupCount', { count: blocked }))}</span>
            </div>
            ${route.error ? `<div class="text-xs text-red-200 mt-3 break-words">${diagEscapeHtml(route.error)}</div>` : ''}
            <div class="mt-3 space-y-2">
                ${checks.length ? checks.map(renderProviderMediaRouteCheck).join('') : `<div class="text-xs text-dark-400">${diagEscapeHtml(t('noImageVideoChecks'))}</div>`}
            </div>
        </div>
    `;
}

function renderProviderMediaRouteCheck(check = {}) {
    const canForward = Boolean(check.can_forward);
    const tone = providerMediaRouteCheckTone(check);
    const kind = check.media_kind === 'video' ? t('videoRequests') : t('imageRequests');
    const status = providerMediaRouteStatusLabel(check);
    const mode = providerMediaRouteModeLabel(check.route_mode || '');
    const message = providerMediaRouteDisplayMessage(check);
    const proxyPaths = Array.isArray(check.proxy_paths) ? check.proxy_paths.join(', ') : '';
    const details = [
        check.canonical_path ? t('routeDetail', { value: check.canonical_path }) : '',
        check.upstream_url ? t('providerUrlDetail', { value: check.upstream_url }) : '',
        proxyPaths ? t('appPathsDetail', { value: proxyPaths }) : '',
        check.error_type ? t('reasonCodeDetail', { value: check.error_type }) : '',
    ].filter(Boolean);
    return `
        <div class="rounded-md border border-dark-800 bg-dark-900/40 px-3 py-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="text-sm text-dark-100">${diagEscapeHtml(kind)}</div>
                <span class="status-pill status-pill-${diagEscapeAttr(tone)}">${diagEscapeHtml(status)}</span>
            </div>
            <div class="text-xs text-dark-400 mt-1">${diagEscapeHtml(mode)}</div>
            <div class="text-xs ${canForward ? 'text-emerald-200' : 'text-amber-200'} mt-2 break-words">${diagEscapeHtml(message)}</div>
            ${details.length ? `
                <details class="mt-2">
                    <summary class="text-xs text-dark-500 cursor-pointer">${diagEscapeHtml(t('viewDetails'))}</summary>
                    <div class="text-xs text-dark-500 mt-1 break-all">${diagEscapeHtml(details.join(' | '))}</div>
                </details>
            ` : ''}
        </div>
    `;
}

function providerMediaRouteCheckTone(check = {}) {
    if (check.can_forward) return 'emerald';
    if (check.error_type === 'media_base_url_missing') return 'red';
    if (check.error_type === 'media_capability_unsupported') return 'dark';
    return 'amber';
}

function providerMediaRouteStatusLabel(check = {}) {
    if (check.can_forward) return t('readyShort');
    if (check.error_type === 'media_base_url_missing') return t('missingUrl');
    if (check.error_type === 'media_capability_unsupported') return t('notEnabled');
    if (check.error_type === 'media_adapter_required') return t('needsSetup');
    return t('needsAttention');
}

function providerMediaRouteModeLabel(mode) {
    if (mode === 'openai_compatible_pass_through') return t('imageVideoReadyMode');
    if (mode === 'adapter_required') return t('imageVideoAdapterMode');
    if (mode === 'disabled') return t('imageVideoOffMode');
    return mode ? t('modeValue', { mode }) : t('modeNotReported');
}

function providerMediaRouteDisplayMessage(check = {}) {
    const fallback = providerMediaRouteFallbackMessage(check);
    if (fallback) return fallback;
    return check.message || t('mediaPathNeedsAttention');
}

function providerMediaRouteFallbackMessage(check = {}) {
    if (check.can_forward) return t('imageVideoCanUseProvider');
    if (check.error_type === 'media_capability_unsupported') {
        return t('turnOnImageVideoOption');
    }
    if (check.error_type === 'media_adapter_required') {
        return t('providerSpecificSetupNeeded');
    }
    if (check.error_type === 'media_base_url_missing') {
        return t('addProviderUrlForMedia');
    }
    return '';
}

function summarizeAmr(section = {}) {
    const groupPayload = section.groups && !Array.isArray(section.groups) && Array.isArray(section.groups.groups)
        ? section.groups.groups
        : section.groups;
    const groups = Number(section.groups_count ?? section.group_count ?? diagnosticsCollectionCount(groupPayload));
    return {
        tone: groups > 0 ? 'emerald' : 'dark',
        status: t('groupCount', { count: groups }),
        primary: t('amrGroupPrimary', { count: groups }),
        detail: section.store_path || section.note || t('amrDiagnosticsAvailable'),
    };
}

function summarizeQuota(section = {}) {
    const snapshots = Number(section.snapshot_count ?? section.cached_count ?? diagnosticsCollectionCount(section.snapshots));
    return {
        tone: snapshots > 0 ? 'emerald' : 'dark',
        status: t('cachedCount', { count: snapshots }),
        primary: t('quotaSnapshotPrimary', { count: snapshots }),
        detail: section.error || section.note || t('quotaDiagnosticsCachedOnly'),
    };
}

function summarizeCurrency(section = {}) {
    const display = section.display_currency || 'USD';
    const source = section.exchange_rate_source || section.source || 'manual';
    return {
        tone: section.error ? 'red' : 'emerald',
        status: display,
        primary: t('displayCurrencyPrimary', { currency: display }),
        detail: section.error || t('pricesShownInCurrency', { currency: display, source }),
    };
}

function summarizePermissions(section = {}) {
    const issues = Array.isArray(section.issues) ? section.issues.length : Number(section.issue_count || 0);
    return {
        tone: issues > 0 ? 'amber' : 'emerald',
        status: issues > 0 ? t('issueCount', { count: issues }) : t('cleanStatus'),
        primary: issues > 0 ? t('auditFindings', { count: issues }) : t('noAuditFindings'),
        detail: section.note || t('guardrailReadOnly'),
    };
}

function summarizeApprovalBridge(section = {}) {
    const available = Boolean(section.available);
    const previewOnly = section.preview_only !== false;
    const live = Boolean(section.live_transport_connected);
    const supported = Array.isArray(section.supported_methods) ? section.supported_methods.length : 0;
    const sample = section.sample || {};
    if (section.error) {
        return {
            tone: 'red',
            status: t('errorLabel'),
            primary: t('bridgePreviewFailed'),
            detail: section.error,
        };
    }
    return {
        tone: available ? (live ? 'emerald' : 'amber') : 'dark',
        status: live ? t('liveStatus') : (previewOnly ? t('previewStatus') : t('offlineStatus')),
        primary: available ? t('actionsSupported', { count: supported }) : t('notAvailable'),
        detail: available
            ? t('approvalPreviewUsable', { state: live ? t('connectedState') : t('notConnectedYet') })
            : t('approvalPreviewUnavailable'),
    };
}

function renderDiagnosticsErrors(errors = {}) {
    const items = Array.isArray(errors)
        ? errors
        : (Array.isArray(errors.recent) ? errors.recent : (Array.isArray(errors.items) ? errors.items : []));
    return `
        <div class="card">
            <h3 class="card-title">${diagEscapeHtml(t('recentErrors'))}</h3>
            <div class="mt-3 space-y-2">
                ${items.length ? items.slice(0, 8).map(item => `
                    <div class="rounded-md border border-dark-800 bg-dark-950/40 px-3 py-2">
                        <div class="text-xs text-dark-500">${diagEscapeHtml(item.at || item.timestamp || '')}</div>
                        <div class="text-sm text-dark-200">${diagEscapeHtml(item.context || item.source || 'error')}</div>
                        <div class="text-xs text-amber-200 mt-1 break-words">${diagEscapeHtml(item.error || item.message || JSON.stringify(item))}</div>
                    </div>
                `).join('') : `<div class="text-sm text-dark-400">${diagEscapeHtml(t('noRecentErrors'))}</div>`}
            </div>
        </div>
    `;
}

function renderDiagnosticsRequestLogs(section = {}) {
    const summary = section.summary || section;
    const media = summary.media || {};
    const latestMediaError = media.latest_error || {};
    const mediaDetail = Number(media.count || 0)
        ? t('imageVideoRequestsSummary', { count: media.count || 0, errors: media.error_count || 0 })
        : t('noImageVideoRequestsSaved');
    const latestErrorDetail = latestMediaError.error_type
        ? ` ${t('latestIssueOnProvider', { issue: diagnosticsMediaErrorLabel(latestMediaError.error_type), provider: latestMediaError.provider_id || t('statusUnknown') })}`
        : '';
    return `
        <div class="card">
            <h3 class="card-title">${diagEscapeHtml(t('recentRequests'))}</h3>
            <div class="enhance-status-strip mt-3">
                <span class="status-pill status-pill-dark">${diagEscapeHtml(t('savedCount', { count: summary.total_records || summary.count || 0 }))}</span>
                <span class="status-pill status-pill-dark">${diagEscapeHtml(t('okCount', { count: summary.successful_requests || summary.success_count || 0 }))}</span>
                <span class="status-pill status-pill-${Number(media.error_count || 0) ? 'amber' : 'dark'}">${diagEscapeHtml(t('imageVideoIssuesCount', { count: media.error_count || 0 }))}</span>
            </div>
            <div class="text-xs text-dark-400 mt-2">${diagEscapeHtml(mediaDetail + latestErrorDetail)}</div>
            <details class="mt-3">
                <summary class="text-xs text-dark-500 cursor-pointer">${diagEscapeHtml(t('storageDetails'))}</summary>
                <div class="text-xs text-dark-500 mt-1 break-all">${diagEscapeHtml(summary.path || section.path || t('safeRequestSummary'))}</div>
            </details>
        </div>
    `;
}

function diagnosticsMediaErrorLabel(errorType) {
    if (errorType === 'media_base_url_missing') return t('mediaBaseUrlMissing');
    if (errorType === 'media_capability_unsupported') return t('mediaCapabilityUnsupported');
    if (errorType === 'media_adapter_required') return t('mediaAdapterRequired');
    return errorType || t('requestIssue');
}

function renderDiagnosticsSystem(section = {}) {
    return `
        <div class="card">
            <h3 class="card-title">${diagEscapeHtml(t('systemLabel'))}</h3>
            <div class="text-xs text-dark-400 mt-3 space-y-1">
                <div>${diagEscapeHtml(t('platformLabel'))}: ${diagEscapeHtml(section.platform || '-')}</div>
                <div>${diagEscapeHtml(t('pythonLabel'))}: ${diagEscapeHtml(section.python_version || '-')}</div>
                <div>${diagEscapeHtml(t('appDataLabel'))}: ${diagEscapeHtml(section.app_data_dir || section.cwd || '-')}</div>
                <div>${diagEscapeHtml(t('configFileLabel'))}: ${diagEscapeHtml(section.config_file_path || '-')}</div>
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
        showToast(t('diagnosticsExported'), 'success');
    } catch (err) {
        showToast(t('diagnosticsExportFailed') + err.message, 'error');
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
