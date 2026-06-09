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

const SETTINGS_WIZARD_STEP_COUNT = 8;
let settingsWizardStep = 0;
let defaultAutoApprovalSystemPrompt = '';
let settingsDefaults = {};
let latestSettings = {};
let latestUpdateCheck = null;

function showSettingsWizardStep(step, options = {}) {
    const nextStep = Math.min(Math.max(Number(step) || 0, 0), SETTINGS_WIZARD_STEP_COUNT - 1);
    settingsWizardStep = nextStep;
    document.querySelectorAll('[data-settings-step-panel]').forEach(panel => {
        panel.classList.toggle('active', Number(panel.dataset.settingsStepPanel) === nextStep);
    });
    document.querySelectorAll('[data-settings-step-button]').forEach(button => {
        button.classList.toggle('active', Number(button.dataset.settingsStepButton) === nextStep);
    });

    const prev = document.getElementById('settings-wizard-prev');
    const next = document.getElementById('settings-wizard-next');
    if (prev) prev.disabled = nextStep === 0;
    if (next) next.textContent = nextStep === SETTINGS_WIZARD_STEP_COUNT - 1 ? t('settingsWizardFinish') : t('settingsWizardNext');
    updateSettingsWizardProgress();

    if (options.scroll !== false) {
        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        document.querySelector('main')?.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    }
}

function showNextSettingsWizardStep() {
    if (settingsWizardStep >= SETTINGS_WIZARD_STEP_COUNT - 1) {
        saveSettings();
        return;
    }
    showSettingsWizardStep(settingsWizardStep + 1);
}

function showPreviousSettingsWizardStep() {
    showSettingsWizardStep(settingsWizardStep - 1);
}

function updateSettingsWizardProgress() {
    const state = buildSettingsWizardState(readSettingsWizardDraft());
    const progress = document.getElementById('settings-wizard-progress');
    if (progress) {
        progress.textContent = t('settingsWizardProgress', {
            current: settingsWizardStep + 1,
            total: SETTINGS_WIZARD_STEP_COUNT,
        });
    }
    const currentStep = state.steps[settingsWizardStep] || state.steps[0] || {};
    const progressBar = document.getElementById('settings-wizard-progress-bar');
    if (progressBar) {
        progressBar.style.width = `${Math.round(((settingsWizardStep + 1) / SETTINGS_WIZARD_STEP_COUNT) * 100)}%`;
    }
    const currentTitle = document.getElementById('settings-wizard-current-title');
    if (currentTitle) currentTitle.textContent = currentStep.label || '';
    const currentDetail = document.getElementById('settings-wizard-current-detail');
    if (currentDetail) currentDetail.textContent = currentStep.detail || '';
    const currentBadge = document.getElementById('settings-wizard-current-badge');
    if (currentBadge) {
        const stateLabel = currentStep.state === 'ready'
            ? t('wizardReady')
            : currentStep.state === 'warn'
                ? t('wizardNeedsInput')
                : t('wizardOptional');
        currentBadge.textContent = `${settingsWizardStep + 1} / ${SETTINGS_WIZARD_STEP_COUNT} · ${stateLabel}`;
    }
    document.querySelectorAll('[data-settings-step-button]').forEach(button => {
        const index = Number(button.dataset.settingsStepButton);
        const step = state.steps[index] || {};
        button.classList.toggle('complete', Number.isFinite(index) && index < settingsWizardStep);
        button.classList.toggle('ready', step.state === 'ready');
        button.classList.toggle('warn', step.state === 'warn');
        button.classList.toggle('idle', step.state === 'idle');
        button.title = step.detail || '';
    });
    renderSettingsWizardAdvisor(state);
    renderSettingsWizardStepSummaries(state);
}

function readSettingsWizardDraft() {
    return {
        ...latestSettings,
        db_path: document.getElementById('setting-db-path')?.value || latestSettings.db_path || '',
        sessions_dir: document.getElementById('setting-sessions-dir')?.value || latestSettings.sessions_dir || '',
        backup_dir: document.getElementById('setting-backup-dir')?.value || latestSettings.backup_dir || '',
        provider_store_path: document.getElementById('setting-provider-store-path')?.value || latestSettings.provider_store_path || '',
        request_log_path: document.getElementById('setting-request-log-path')?.value || latestSettings.request_log_path || '',
        close_button_action: document.getElementById('setting-close-button-action')?.value || latestSettings.close_button_action || 'ask',
        desktop_launch_action: document.getElementById('setting-desktop-launch-action')?.value || latestSettings.desktop_launch_action || 'show_window',
        desktop_monitor_enabled: document.getElementById('setting-desktop-monitor-enabled')?.checked ?? latestSettings.desktop_monitor_enabled ?? true,
        desktop_monitor_opacity: parseInt(document.getElementById('setting-desktop-monitor-opacity')?.value, 10) || latestSettings.desktop_monitor_opacity || 88,
    };
}

function syncMonitorOpacityLabel() {
    const input = document.getElementById('setting-desktop-monitor-opacity');
    const label = document.getElementById('setting-desktop-monitor-opacity-label');
    if (!input || !label) return;
    const value = Math.min(Math.max(parseInt(input.value, 10) || 88, 35), 100);
    input.value = value;
    label.textContent = `${value}%`;
}

function settingsWizardProviderMetrics() {
    const providers = (typeof providerState !== 'undefined' && Array.isArray(providerState.providers))
        ? providerState.providers
        : [];
    const enabledProviders = providers.filter(provider => provider && provider.enabled !== false);
    const selectedModels = providers.reduce((sum, provider) => (
        sum + (provider.models || []).filter(model => model && model.selected && model.enabled !== false).length
    ), 0);
    const mediaFallbacks = providers.filter(provider => {
        const profile = provider.media_profile || {};
        return profile.default_image_provider;
    }).length;
    const nativeApprovalModels = providers.reduce((sum, provider) => (
        sum + (provider.models || []).filter(model => model && model.native_approval === true).length
    ), 0);
    const focusProviderId = (typeof providerState !== 'undefined' && providerState.focus_provider_id)
        ? String(providerState.focus_provider_id || '')
        : '';
    const focusedProvider = providers.find(provider => provider.id === focusProviderId) || null;
    return {
        providers,
        enabledProviders,
        selectedModels,
        mediaFallbacks,
        nativeApprovalModels,
        focusProviderId,
        focusedProvider,
    };
}

function buildSettingsWizardState(draft = readSettingsWizardDraft()) {
    const metrics = settingsWizardProviderMetrics();
    const pathReady = Boolean(draft.db_path && draft.sessions_dir);
    const providerReady = metrics.enabledProviders.length > 0 && metrics.selectedModels > 0;
    const storageReady = Boolean(draft.backup_dir && draft.provider_store_path && draft.request_log_path);
    const routeReady = providerReady && (metrics.focusProviderId || metrics.enabledProviders.length === 1);
    const alertsReady = Boolean(draft.request_log_path);
    const startupReady = Boolean(draft.close_button_action || 'ask');
    const cleanupReady = Boolean(draft.exports_dir || draft.backup_dir);
    const mustHaveReady = pathReady && providerReady && storageReady;
    const steps = [
        {
            index: 0,
            state: pathReady ? 'ready' : 'warn',
            label: t('settingsWizardDetect'),
            detail: pathReady ? t('wizardStepDetectReady') : t('wizardStepDetectMissing'),
        },
        {
            index: 1,
            state: storageReady ? 'ready' : 'warn',
            label: t('settingsWizardBasics'),
            detail: storageReady ? t('wizardStepBasicsReady') : t('wizardStepBasicsMissing'),
        },
        {
            index: 2,
            state: providerReady ? 'ready' : 'warn',
            label: t('settingsWizardProviders'),
            detail: providerReady
                ? t('wizardStepProvidersReady', { providers: metrics.enabledProviders.length, models: metrics.selectedModels })
                : t('wizardStepProvidersMissing'),
        },
        {
            index: 3,
            state: routeReady ? 'ready' : providerReady ? 'idle' : 'warn',
            label: t('settingsWizardRouting'),
            detail: routeReady
                ? t('wizardStepRoutingReady')
                : providerReady ? t('wizardStepRoutingOptional') : t('wizardStepRoutingMissing'),
        },
        {
            index: 4,
            state: alertsReady ? 'ready' : 'idle',
            label: t('settingsWizardCost'),
            detail: alertsReady ? t('wizardStepAlertsReady') : t('wizardStepAlertsOptional'),
        },
        {
            index: 5,
            state: startupReady ? 'ready' : 'idle',
            label: t('settingsWizardStartup'),
            detail: t('wizardStepStartupReady'),
        },
        {
            index: 6,
            state: cleanupReady ? 'ready' : 'idle',
            label: t('settingsWizardCleanup'),
            detail: t('wizardStepCleanupReady'),
        },
        {
            index: 7,
            state: mustHaveReady ? 'ready' : 'warn',
            label: t('settingsWizardFinishStep'),
            detail: mustHaveReady ? t('wizardStepFinishReady') : t('wizardStepFinishMissing'),
        },
    ];
    const recommended = steps.find(step => step.state === 'warn') || steps[settingsWizardStep] || steps[0];
    return {
        metrics,
        steps,
        recommended,
        pathReady,
        providerReady,
        storageReady,
        routeReady,
        alertsReady,
        startupReady,
        cleanupReady,
        mustHaveReady,
    };
}

function wizardCheckItem(state, label, detail) {
    const classes = {
        ready: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
        warn: 'border-amber-500/30 bg-amber-500/10 text-amber-100',
        idle: 'border-dark-800 bg-dark-950/45 text-dark-300',
    };
    const badge = state === 'ready' ? t('wizardReady') : state === 'warn' ? t('wizardNeedsInput') : t('wizardOptional');
    return `
        <div class="wizard-check-item ${classes[state] || classes.idle}">
            <div class="flex items-center justify-between gap-3">
                <span class="font-semibold">${escapeHtml(label)}</span>
                <span class="text-xs">${escapeHtml(badge)}</span>
            </div>
            <p class="mt-1 text-xs opacity-80">${escapeHtml(detail)}</p>
        </div>
    `;
}

function updateSettingsWizardChecklist(draft = readSettingsWizardDraft()) {
    const root = document.getElementById('settings-wizard-checklist');
    if (!root) return;
    const state = buildSettingsWizardState(draft);
    const metrics = state.metrics;
    const closeAction = draft.close_button_action || 'ask';

    root.innerHTML = [
        wizardCheckItem(
            state.pathReady ? 'ready' : 'warn',
            t('wizardCheckPaths'),
            state.pathReady ? t('wizardCheckPathsReady') : t('wizardCheckPathsMissing')
        ),
        wizardCheckItem(
            state.providerReady ? 'ready' : 'warn',
            t('wizardCheckProviders'),
            state.providerReady
                ? t('wizardCheckProvidersReady', { providers: metrics.enabledProviders.length, models: metrics.selectedModels })
                : t('wizardCheckProvidersMissing')
        ),
        wizardCheckItem(
            metrics.mediaFallbacks > 0 ? 'ready' : 'idle',
            t('wizardCheckMedia'),
            metrics.mediaFallbacks > 0 ? t('wizardCheckMediaReady', { count: metrics.mediaFallbacks }) : t('wizardCheckMediaOptional')
        ),
        wizardCheckItem(
            state.storageReady ? 'ready' : 'warn',
            t('wizardCheckDefaults'),
            state.storageReady ? t('wizardCheckDefaultsReady') : t('wizardCheckDefaultsMissing')
        ),
        wizardCheckItem(
            'ready',
            t('closeButtonAction'),
            t('closeButtonActionCurrent', { action: t(`closeAction_${closeAction}`) })
        ),
    ].join('');
    renderSettingsWizardAdvisor(state);
    renderSettingsWizardStepSummaries(state);
    updateSettingsWizardProgress();
}

function renderSettingsWizardAdvisor(state = buildSettingsWizardState(readSettingsWizardDraft())) {
    const root = document.getElementById('settings-wizard-advisor');
    if (!root) return;
    const target = state.recommended || state.steps[0];
    const allReady = state.mustHaveReady;
    root.innerHTML = `
        <div class="wizard-advisor ${allReady ? 'ready' : 'warn'}">
            <div>
                <p class="text-xs font-semibold uppercase tracking-[0.16em]">${escapeHtml(allReady ? t('wizardAdvisorReadyKicker') : t('wizardAdvisorNextKicker'))}</p>
                <h3 class="mt-1 text-lg font-bold">${escapeHtml(allReady ? t('wizardAdvisorReadyTitle') : target.label)}</h3>
                <p class="mt-1 text-sm opacity-80">${escapeHtml(allReady ? t('wizardAdvisorReadyDesc') : target.detail)}</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button type="button" onclick="showSettingsWizardStep(${target.index})" class="btn btn-secondary text-xs">${escapeHtml(allReady ? t('wizardReviewStep') : t('wizardGoToStep'))}</button>
                <button type="button" onclick="fillSettingsWizardDefaults()" class="btn btn-primary text-xs">${escapeHtml(t('wizardFillDefaults'))}</button>
            </div>
        </div>
    `;
}

function wizardSummaryCard(title, value, detail, tone = 'idle') {
    return `
        <div class="wizard-summary-card ${tone}">
            <div class="text-xs uppercase tracking-[0.14em] opacity-70">${escapeHtml(title)}</div>
            <div class="mt-2 text-2xl font-bold">${escapeHtml(String(value))}</div>
            <div class="mt-1 text-xs opacity-75">${escapeHtml(detail)}</div>
        </div>
    `;
}

function wizardGuideCard(title, detail, actionLabel = '', action = '') {
    return `
        <div class="wizard-guide-card">
            <div class="font-semibold text-dark-100">${escapeHtml(title)}</div>
            <p class="mt-1 text-sm text-dark-400">${escapeHtml(detail)}</p>
            ${actionLabel && action ? `<button type="button" onclick="${escapeHtml(action)}" class="btn btn-secondary text-xs mt-3">${escapeHtml(actionLabel)}</button>` : ''}
        </div>
    `;
}

function renderSettingsWizardStepSummaries(state = buildSettingsWizardState(readSettingsWizardDraft())) {
    const metrics = state.metrics;
    const providerRoot = document.getElementById('settings-provider-wizard-summary');
    if (providerRoot) {
        providerRoot.innerHTML = `
            <div class="wizard-summary-grid">
                ${wizardSummaryCard(t('wizardMetricProviders'), metrics.enabledProviders.length, t('wizardMetricProvidersDesc'), metrics.enabledProviders.length ? 'ready' : 'warn')}
                ${wizardSummaryCard(t('wizardMetricModels'), metrics.selectedModels, t('wizardMetricModelsDesc'), metrics.selectedModels ? 'ready' : 'warn')}
                ${wizardSummaryCard(t('wizardMetricNativeApproval'), metrics.nativeApprovalModels, t('wizardMetricNativeApprovalDesc'), metrics.nativeApprovalModels ? 'ready' : 'idle')}
            </div>
            <div class="wizard-guide-grid mt-4">
                ${wizardGuideCard(t('wizardProviderActionPreset'), t('wizardProviderActionPresetDesc'), t('wizardProviderGuidePrimary'), "navigateTo('quick-setup')")}
                ${wizardGuideCard(t('wizardProviderActionEdit'), t('wizardProviderActionEditDesc'), t('wizardProviderGuideSecondary'), "navigateTo('providers')")}
            </div>
        `;
    }

    const routingRoot = document.getElementById('settings-routing-wizard-summary');
    if (routingRoot) {
        const focusName = metrics.focusedProvider
            ? (metrics.focusedProvider.display_name || metrics.focusedProvider.id)
            : (metrics.enabledProviders.length === 1 ? (metrics.enabledProviders[0].display_name || metrics.enabledProviders[0].id) : t('notSelected'));
        routingRoot.innerHTML = `
            <div class="wizard-summary-grid">
                ${wizardSummaryCard(t('wizardMetricFocusedProvider'), focusName, t('wizardMetricFocusedProviderDesc'), state.routeReady ? 'ready' : 'idle')}
                ${wizardSummaryCard(t('wizardMetricMediaFallback'), metrics.mediaFallbacks, t('wizardMetricMediaFallbackDesc'), metrics.mediaFallbacks ? 'ready' : 'idle')}
                ${wizardSummaryCard(t('wizardMetricRouteReady'), state.routeReady ? t('yes') : t('no'), state.routeReady ? t('wizardRouteReadyDesc') : t('wizardRouteNeedsProviderDesc'), state.routeReady ? 'ready' : 'warn')}
            </div>
            <div class="wizard-guide-grid mt-4">
                ${wizardGuideCard(t('wizardRoutingActionFocus'), t('wizardRoutingActionFocusDesc'), t('wizardProviderGuideSecondary'), "navigateTo('providers')")}
                ${wizardGuideCard(t('wizardRoutingActionMedia'), t('wizardRoutingActionMediaDesc'), t('wizardProviderGuideSecondary'), "navigateTo('providers')")}
            </div>
        `;
    }

    const alertsRoot = document.getElementById('settings-alerts-wizard-summary');
    if (alertsRoot) {
        alertsRoot.innerHTML = `
            <div class="wizard-summary-grid">
                ${wizardSummaryCard(t('wizardMetricTokenUsage'), state.alertsReady ? t('enabled') : t('wizardOptional'), t('wizardMetricTokenUsageDesc'), state.alertsReady ? 'ready' : 'idle')}
                ${wizardSummaryCard(t('wizardMetricQuotaAlert'), t('wizardOptional'), t('wizardMetricQuotaAlertDesc'), 'idle')}
                ${wizardSummaryCard(t('wizardMetricBalanceAlert'), t('wizardOptional'), t('wizardMetricBalanceAlertDesc'), 'idle')}
            </div>
        `;
    }

    const finishRoot = document.getElementById('settings-finish-wizard-summary');
    if (finishRoot) {
        const readyCount = state.steps.filter(step => step.state === 'ready').length;
        finishRoot.innerHTML = `
            <div class="wizard-summary-grid">
                ${wizardSummaryCard(t('wizardMetricReadySteps'), `${readyCount}/${SETTINGS_WIZARD_STEP_COUNT}`, t('wizardMetricReadyStepsDesc'), state.mustHaveReady ? 'ready' : 'warn')}
                ${wizardSummaryCard(t('wizardMetricRequired'), state.mustHaveReady ? t('wizardReady') : t('wizardNeedsInput'), state.mustHaveReady ? t('wizardRequiredReadyDesc') : t('wizardRequiredMissingDesc'), state.mustHaveReady ? 'ready' : 'warn')}
                ${wizardSummaryCard(t('wizardMetricLaterEdits'), t('enabled'), t('wizardMetricLaterEditsDesc'), 'ready')}
            </div>
        `;
    }
}

function fillSettingsWizardDefaults() {
    const defaults = settingsDefaults || {};
    const mappings = {
        'setting-backup-dir': 'backup_dir',
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
        'setting-close-button-action': 'close_button_action',
    };
    let filled = 0;
    Object.entries(mappings).forEach(([elId, key]) => {
        const el = document.getElementById(elId);
        if (!el || el.value) return;
        if (defaults[key] === undefined || defaults[key] === null) return;
        el.value = defaults[key];
        filled += 1;
    });
    updateSettingsWizardChecklist(readSettingsWizardDraft());
    showToast(filled ? t('wizardDefaultsFilled', { count: filled }) : t('wizardDefaultsAlreadyFilled'), 'success');
}

async function loadSettings() {
    try {
        const data = await api('/api/settings');
        latestSettings = data || {};
        settingsDefaults = data.defaults || {};
        populateSettingsForm(data);
        applyThemeSettings(data);
        showSettingsWizardStep(settingsWizardStep, { scroll: false });
        setStatus(t('settingsLoaded'));

        const hasDbPath = data.db_path && String(data.db_path).trim().length > 0;
        const hasSessionsDir = data.sessions_dir && String(data.sessions_dir).trim().length > 0;
        scheduleSettingsBackgroundLoads({ needsAutoDetect: !hasDbPath || !hasSessionsDir });
    } catch (err) {
        showToast(t('failedLoadSettings') + err.message, 'error');
    }
}

function scheduleSettingsBackgroundLoads({ needsAutoDetect = false } = {}) {
    setTimeout(async () => {
        await Promise.allSettled([
            loadStorageInfo(),
            loadCleanupPreview(),
            loadUninstallPreview(),
            loadCurrencySettings(),
            loadStartupStatus(),
            typeof ensureProviderData === 'function' ? ensureProviderData() : Promise.resolve(),
        ]);
        if (currentPage === 'settings') {
            updateSettingsWizardChecklist(readSettingsWizardDraft());
        }
    }, 0);

    setTimeout(() => {
        if (currentPage === 'settings') loadUpdateStatus();
    }, 900);

    if (needsAutoDetect) {
        setTimeout(() => {
            if (currentPage === 'settings') runAutoDetect();
        }, 250);
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
        'setting-close-button-action': 'close_button_action',
        'setting-desktop-launch-action': 'desktop_launch_action',
        'setting-desktop-monitor-opacity': 'desktop_monitor_opacity',
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
    syncMonitorOpacityLabel();

    defaultAutoApprovalSystemPrompt = data.auto_approval_system_prompt_default || data.auto_approval_system_prompt || '';
    const approvalPrompt = document.getElementById('setting-auto-approval-system-prompt');
    if (approvalPrompt) {
        approvalPrompt.value = data.auto_approval_system_prompt || defaultAutoApprovalSystemPrompt;
    }
    renderSecretRevealPasswordStatus(data);

    const checkboxFields = {
        'setting-auto-backup': 'auto_backup',
        'setting-use-codex-pp': 'use_codex_plus_plus',
        'setting-dark-mode': 'dark_mode',
        'setting-startup-enabled': 'startup_enabled',
        'setting-startup-auto-elevate': 'startup_auto_elevate',
        'setting-desktop-monitor-enabled': 'desktop_monitor_enabled',
        'setting-update-check-enabled': 'update_check_enabled',
        'setting-update-include-prerelease': 'update_include_prerelease',
        'setting-plugin-unlock-enabled': 'plugin_unlock_enabled',
        'setting-codex-goals-enabled': 'codex_goals_enabled',
        'setting-codex-sandbox-auto-repair-enabled': 'codex_sandbox_auto_repair_enabled',
    };
    for (const [elId, key] of Object.entries(checkboxFields)) {
        const el = document.getElementById(elId);
        if (el && data[key] !== undefined) el.checked = Boolean(data[key]);
    }
    renderAppVersion(data);
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
        'monitor-field-speed': 'speed',
        'monitor-field-cache': 'cache',
        'monitor-field-context': 'context_window',
        'monitor-field-updated': 'updated_at',
    };
    for (const [elId, key] of Object.entries(monitorMap)) {
        const el = document.getElementById(elId);
        if (el) el.checked = monitorFields[key] !== false;
    }
}

function renderSecretRevealPasswordStatus(data = latestSettings) {
    const el = document.getElementById('setting-secret-reveal-status');
    if (!el) return;
    const configured = Boolean(data && data.secret_reveal_password_configured);
    el.textContent = configured ? t('secretRevealPasswordConfigured') : t('secretRevealPasswordNotConfigured');
    el.className = configured ? 'text-xs text-emerald-300 mt-1' : 'text-xs text-dark-500 mt-1';
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
        if (!result.success) throw new Error(result.error || t('currencySettingsSaveFailed'));
        await loadCurrencySettings();
        showToast(t('currencySettingsSaved'), 'success');
    } catch (err) {
        showToast(t('currencySaveFailed') + err.message, 'error');
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
        resultEl.textContent = t('previewFailed') + err.message;
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
        strip.textContent = t('startupStatusFailed') + err.message;
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
        renderStartupPreviewResult(data);
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
            target_diagnostics: data.target_diagnostics,
        });
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

function renderAppVersion(data = latestSettings) {
    const el = document.getElementById('app-version-label');
    if (!el) return;
    el.textContent = data.app_version ? t('currentVersionLabel', { version: data.app_version }) : '';
}

async function loadUpdateStatus() {
    const status = document.getElementById('update-status');
    if (!status) return;
    renderAppVersion(latestSettings);
    if (latestSettings.update_check_enabled === false) {
        renderUpdateStatus({ skipped: true });
        return;
    }
    await checkForUpdates(false);
}

async function checkForUpdates(showSuccessToast = true) {
    const include = document.getElementById('setting-update-include-prerelease')?.checked || false;
    const status = document.getElementById('update-status');
    if (status) status.textContent = t('updateChecking');
    try {
        latestUpdateCheck = await api('/api/updates/check?include_prerelease=' + encodeURIComponent(String(include)));
        renderUpdateStatus(latestUpdateCheck);
        if (showSuccessToast) {
            showToast(latestUpdateCheck.update_available ? t('updateAvailableToast') : t('updateNoUpdateToast'), 'success');
        }
    } catch (err) {
        latestUpdateCheck = null;
        renderInlineError('update-status', t('updateCheckFailed') + err.message);
    }
}

async function downloadLatestUpdate() {
    const include = document.getElementById('setting-update-include-prerelease')?.checked || false;
    const button = document.getElementById('download-update-btn');
    if (button) button.disabled = true;
    const status = document.getElementById('update-status');
    if (status) status.textContent = t('updateDownloading');
    try {
        const result = await api('/api/updates/download', {
            method: 'POST',
            body: JSON.stringify({ include_prerelease: include }),
        });
        if (!result.success) throw new Error(result.error || t('updateDownloadFailed'));
        renderUpdateStatus(result.check || latestUpdateCheck, result.downloaded_path);
        showToast(t('updateDownloadedToast'), 'success', 5000);
    } catch (err) {
        renderInlineError('update-status', t('updateDownloadFailed') + err.message);
        if (button) button.disabled = false;
    }
}

function renderUpdateStatus(result, downloadedPath = '') {
    const status = document.getElementById('update-status');
    const button = document.getElementById('download-update-btn');
    if (!status) return;
    if (result && result.skipped) {
        status.innerHTML = `<div class="text-dark-500">${escapeHtml(t('updateAutoCheckDisabled'))}</div>`;
        if (button) button.disabled = true;
        return;
    }
    const release = (result && result.release) || {};
    const asset = release.asset || {};
    const current = (result && result.current_version) || latestSettings.app_version || '-';
    const latest = (result && result.latest_version) || release.tag_name || '-';
    const updateAvailable = Boolean(result && result.update_available && asset.url);
    if (button) button.disabled = !updateAvailable;
    const assetLine = asset.name
        ? `${escapeHtml(asset.name)} · ${escapeHtml(formatBytes(asset.size || 0))}`
        : escapeHtml(t('updateNoExeAsset'));
    status.innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div class="rounded-lg border border-dark-800 bg-dark-950/45 p-3">
                <div class="text-xs text-dark-500">${escapeHtml(t('currentVersion'))}</div>
                <div class="text-sm font-semibold text-dark-100">${escapeHtml(current)}</div>
            </div>
            <div class="rounded-lg border border-dark-800 bg-dark-950/45 p-3">
                <div class="text-xs text-dark-500">${escapeHtml(t('latestVersion'))}</div>
                <div class="text-sm font-semibold ${updateAvailable ? 'text-emerald-300' : 'text-dark-100'}">${escapeHtml(latest)}</div>
            </div>
            <div class="rounded-lg border border-dark-800 bg-dark-950/45 p-3">
                <div class="text-xs text-dark-500">${escapeHtml(t('releaseAsset'))}</div>
                <div class="text-sm text-dark-200 break-all">${assetLine}</div>
            </div>
        </div>
        <div class="mt-3 text-xs ${updateAvailable ? 'text-emerald-300' : 'text-dark-500'}">
            ${escapeHtml(updateAvailable ? t('updateAvailable') : t('updateNoUpdate'))}
        </div>
        ${downloadedPath ? `<div class="mt-2 text-xs text-amber-200 break-all">${escapeHtml(t('updateDownloadedPath', { path: downloadedPath }))}</div>` : ''}
        ${release.url ? `<a class="inline-flex mt-2 text-xs font-semibold text-accent-300 hover:text-accent-200" href="${escapeHtml(release.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t('openReleasePage'))}</a>` : ''}
    `;
}

async function applyStartupSettings() {
    const confirmation = prompt(t('startupConfirmApply'));
    if (confirmation !== 'MODIFY_WINDOWS_STARTUP') {
        showToast(t('startupChangeCancelled'), 'warning');
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
        showToast(t('startupChangeApplied'), 'success');
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

async function removeStartupSettings() {
    const confirmation = prompt(t('startupConfirmRemove'));
    if (confirmation !== 'MODIFY_WINDOWS_STARTUP') {
        showToast(t('startupRemovalCancelled'), 'warning');
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
        showToast(t('startupEntryRemoved'), 'success');
    } catch (err) {
        renderInlineError('startup-preview-result', err.message);
    }
}

async function createDesktopShortcut(kind = '') {
    const resultEl = document.getElementById('startup-preview-result');
    try {
        const payload = kind ? { kind } : { normal: true, start_codex: true };
        const result = await api('/api/desktop-shortcuts/create', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        const shortcuts = (result.shortcuts || [])
            .map(item => `${item.success ? 'OK' : 'ERR'} ${item.kind}: ${item.path || ''}`)
            .join('\n');
        if (resultEl) resultEl.textContent = shortcuts || JSON.stringify(result, null, 2);
        showToast(t('desktopShortcutCreated'), 'success');
    } catch (err) {
        if (resultEl) resultEl.textContent = t('desktopShortcutFailed') + err.message;
        showToast(t('desktopShortcutFailed') + err.message, 'error');
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
    const entry = data.startup_entry_exists ? t('startupFilePresent') : t('startupFileAbsent');
    const task = data.scheduled_task_exists ? t('scheduledTaskPresent') : t('scheduledTaskAbsent');
    const checked = data.scheduled_task_checked === false ? t('scheduledTaskNotChecked') : task;
    const targetDiagnostics = data.target_diagnostics || {};
    const targetSummary = formatStartupTargetSummary(targetDiagnostics, active);
    const warnings = active && Array.isArray(targetDiagnostics.warnings) ? targetDiagnostics.warnings : [];
    strip.className = `mt-4 rounded-md border ${cls} px-4 py-2 text-sm`;
    strip.innerHTML = `
        <div>${escapeHtml(supported ? t('windowsIntegrationSupported') : t('unsupportedPlatform'))} | ${escapeHtml(t('modeStatus', { mode }))} | ${escapeHtml(entry)} | ${escapeHtml(checked)} | ${escapeHtml(targetSummary)}</div>
        ${warnings.length ? `<div class="mt-1 text-xs">${warnings.map(item => escapeHtml(item)).join('<br>')}</div>` : ''}
    `;
}

function renderStartupMutationResult(result) {
    const resultEl = document.getElementById('startup-preview-result');
    if (!resultEl) return;
    if (result && result.preview) {
        renderStartupPreviewResult(result.preview, result);
        return;
    }
    resultEl.textContent = JSON.stringify(result, null, 2);
}

function formatStartupTargetSummary(diagnostics = {}, active = false) {
    if (!active) return t('targetNotUsed');
    const name = diagnostics.target_name || diagnostics.target || 'target';
    const exists = diagnostics.target_exists ? t('targetExists') : t('targetMissing');
    if (diagnostics.release_startup_ready) {
        return t('targetReleaseReady', { name, exists });
    }
    if (diagnostics.target_is_exe) {
        return t('targetExeNeedsReview', { name, exists });
    }
    return t('targetNotPackagedExe', { name, exists });
}

function describeStartupAction(action = {}) {
    const kind = action.kind || '';
    const operation = action.action || '';
    if (kind === 'startup_entry' && operation === 'write_cmd') return t('startupActionWriteFile');
    if (kind === 'startup_entry' && operation === 'remove') return t('startupActionRemoveFile');
    if (kind === 'scheduled_task' && operation === 'create') return t('startupActionCreateTask');
    if (kind === 'scheduled_task' && operation === 'delete') return t('startupActionDeleteTask');
    return t('startupActionReview');
}

function renderStartupPreviewResult(data, mutation = null) {
    const resultEl = document.getElementById('startup-preview-result');
    if (!resultEl) return;
    const mode = data.mode || 'disabled';
    const target = data.target || t('targetNotUsed');
    const privilege = data.elevation_method === 'task_scheduler_highest'
        ? t('startupPrivilegeTask')
        : t('startupPrivilegeNormal');
    const actions = (data.actions || []).map(describeStartupAction);
    const notes = data.elevation_method === 'task_scheduler_highest'
        ? [t('startupNoteTaskScheduler'), t('startupNoteAdminConfirm')]
        : mode === 'startup_folder' ? [t('startupNoteFolderNoAdmin')] : [];
    const successLine = mutation && mutation.success !== undefined
        ? `<div class="${mutation.success ? 'text-emerald-300' : 'text-red-300'}">${escapeHtml(mutation.success ? t('startupMutationSuccess') : t('startupMutationFailed'))}</div>`
        : '';

    resultEl.innerHTML = `
        <div class="space-y-2 text-sm whitespace-normal">
            ${successLine}
            <div><span class="text-dark-500">${escapeHtml(t('startupPreviewMode'))}</span> ${escapeHtml(t(`startupMode_${mode}`) || mode)}</div>
            <div><span class="text-dark-500">${escapeHtml(t('startupPreviewPrivilege'))}</span> ${escapeHtml(privilege)}</div>
            <div><span class="text-dark-500">${escapeHtml(t('startupPreviewTarget'))}</span> <span class="font-mono">${escapeHtml(target)}</span></div>
            <div><span class="text-dark-500">${escapeHtml(t('startupPreviewActions'))}</span> ${escapeHtml(actions.length ? actions.join(' · ') : t('noChanges'))}</div>
            ${notes.length ? `<div class="pt-2 border-t border-dark-800 text-xs text-dark-400">${notes.map(note => escapeHtml(note)).join('<br>')}</div>` : ''}
        </div>
    `;
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
        close_button_action: document.getElementById('setting-close-button-action')?.value || 'ask',
        desktop_launch_action: document.getElementById('setting-desktop-launch-action')?.value || 'show_window',
        desktop_monitor_enabled: document.getElementById('setting-desktop-monitor-enabled')?.checked ?? true,
        desktop_monitor_opacity: parseInt(document.getElementById('setting-desktop-monitor-opacity')?.value, 10) || 88,
        update_check_enabled: document.getElementById('setting-update-check-enabled')?.checked ?? true,
        update_include_prerelease: document.getElementById('setting-update-include-prerelease')?.checked ?? false,
        plugin_unlock_enabled: document.getElementById('setting-plugin-unlock-enabled')?.checked || false,
        codex_goals_enabled: document.getElementById('setting-codex-goals-enabled')?.checked !== false,
        codex_sandbox_auto_repair_enabled: document.getElementById('setting-codex-sandbox-auto-repair-enabled')?.checked || false,
        proxy_upstream_timeout_seconds: parseInt(document.getElementById('setting-proxy-upstream-timeout')?.value, 10) || 120,
        proxy_retry_attempts: parseInt(document.getElementById('setting-proxy-retry-attempts')?.value, 10) || 0,
        proxy_retry_backoff_ms: parseInt(document.getElementById('setting-proxy-retry-backoff-ms')?.value, 10) || 250,
        auto_approval_system_prompt: document.getElementById('setting-auto-approval-system-prompt')?.value || defaultAutoApprovalSystemPrompt,
        ...readStartupSettingsFromForm(),
        theme_preset: document.getElementById('setting-theme-preset')?.value || 'dark',
        theme_custom: readThemeCustomFromForm(),
        monitor_fields: {
            tokens: document.getElementById('monitor-field-tokens')?.checked !== false,
            progress: document.getElementById('monitor-field-progress')?.checked !== false,
            threshold: document.getElementById('monitor-field-threshold')?.checked !== false,
            speed: document.getElementById('monitor-field-speed')?.checked !== false,
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
            latestSettings = { ...latestSettings, ...data };
            applyThemeSettings(data);
            await loadStorageInfo();
            updateSettingsWizardChecklist(readSettingsWizardDraft());
            showToast(t('settingsSaved') + (result.warning ? ' ' + t('warning') + ': ' + result.warning : ''), 'success');
        } else {
            showToast(t('saveFailed') + (result.error || t('unknownError')), 'error');
        }
    } catch (err) {
        showToast(t('saveFailed') + err.message, 'error');
    }
}

function restoreAutoApprovalPromptDefault() {
    const el = document.getElementById('setting-auto-approval-system-prompt');
    if (el) el.value = defaultAutoApprovalSystemPrompt;
    showToast(t('autoApprovalPromptRestored'), 'success');
}

async function saveSecretRevealPassword() {
    const passwordEl = document.getElementById('setting-secret-reveal-password');
    const confirmEl = document.getElementById('setting-secret-reveal-password-confirm');
    const password = passwordEl?.value || '';
    const confirm = confirmEl?.value || '';
    if (!password) {
        showToast(t('secretRevealPasswordEmpty'), 'warning');
        return;
    }
    if (password !== confirm) {
        showToast(t('secretRevealPasswordMismatch'), 'error');
        return;
    }

    let currentPassword = '';
    if (latestSettings.secret_reveal_password_configured) {
        const entered = window.prompt(t('secretRevealCurrentPasswordPrompt'));
        if (entered === null) return;
        currentPassword = entered;
    }

    try {
        const result = await api('/api/settings/secret-reveal-password', {
            method: 'POST',
            body: JSON.stringify({ password, current_password: currentPassword }),
        });
        latestSettings.secret_reveal_password_configured = Boolean(result.configured);
        if (passwordEl) passwordEl.value = '';
        if (confirmEl) confirmEl.value = '';
        renderSecretRevealPasswordStatus(latestSettings);
        showToast(t('secretRevealPasswordSaved'), 'success');
    } catch (err) {
        showToast(t('secretRevealPasswordSaveFailed') + err.message, 'error');
    }
}

async function clearSecretRevealPassword() {
    let currentPassword = '';
    if (latestSettings.secret_reveal_password_configured) {
        const entered = window.prompt(t('secretRevealCurrentPasswordPrompt'));
        if (entered === null) return;
        currentPassword = entered;
    }

    try {
        const result = await api('/api/settings/secret-reveal-password', {
            method: 'POST',
            body: JSON.stringify({ clear: true, current_password: currentPassword }),
        });
        latestSettings.secret_reveal_password_configured = Boolean(result.configured);
        renderSecretRevealPasswordStatus(latestSettings);
        showToast(t('secretRevealPasswordCleared'), 'success');
    } catch (err) {
        showToast(t('secretRevealPasswordSaveFailed') + err.message, 'error');
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
        showToast(t('settingsExported'), 'success');
    } catch (err) {
        showToast(t('exportFailed') + err.message, 'error');
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
        if (!result.success) throw new Error(result.error || t('settingsImportFailed'));
        showToast(t('settingsImported'), 'success');
        await loadSettings();
    } catch (err) {
        showToast(t('settingsImportFailed') + ': ' + err.message, 'error');
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
        showToast(t('themeExported'), 'success');
    } catch (err) {
        showToast(t('themeExportFailed') + err.message, 'error');
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
        if (!result.success) throw new Error(result.error || t('themeImportFailed'));
        populateSettingsForm(await api('/api/settings'));
        applyThemeSettings(nextTheme);
        showToast(t('themeImported'), 'success');
    } catch (err) {
        showToast(t('themeImportFailed') + err.message, 'error');
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
            [t('appDataStorage'), data.app_data_dir],
            [t('configStorage'), data.config_file],
            [t('providerRegistryStorage'), data.provider_store_path],
            [t('backupsStorage'), data.backup_dir],
            [t('tempDir'), data.temp_dir],
            [t('diagnosticsDir'), data.diagnostics_dir],
            [t('exportsDir'), data.exports_dir],
            [t('legacyConfig'), data.legacy_config_exists ? data.legacy_config_file : t('notPresent')],
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
    const confirmation = prompt(t('cleanupConfirm'));
    if (confirmation !== 'CLEAN_LOCAL_CACHE') {
        showToast(t('cleanupCancelled'), 'warning');
        return;
    }
    try {
        const result = await api('/api/cleanup/execute', {
            method: 'POST',
            body: JSON.stringify({ confirmation, targets: selected }),
        });
        renderCleanupResults('cleanup-result', result.results || []);
        await loadCleanupPreview();
        showToast(t('cleanupCompleted'), 'success');
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
    const confirmation = prompt(t('uninstallConfirm'));
    if (confirmation !== 'UNINSTALL_CLEANUP') {
        showToast(t('uninstallCancelled'), 'warning');
        return;
    }
    try {
        const result = await api('/api/uninstall-cleanup/execute', {
            method: 'POST',
            body: JSON.stringify({ confirmation }),
        });
        renderCleanupResults('uninstall-cleanup-result', result.results || []);
        renderWriteLockState(true, result.reason || '');
        showToast(t('uninstallCompleted'), 'success', 5000);
    } catch (err) {
        renderInlineError('uninstall-cleanup-result', err.message);
    }
}

function renderCleanupTargets(container, targets, options = {}) {
    const selectable = options.selectable !== false;
    const prefix = options.prefix || 'target';
    if (!targets.length) {
        container.innerHTML = `<div class="text-sm text-dark-500">${escapeHtml(t('noTargets'))}</div>`;
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
                        <span class="text-xs ${safeClass}">${escapeHtml(target.safe ? t('allowlisted') : t('manualLabel'))}</span>
                        <span class="text-xs text-dark-500">${escapeHtml(target.exists ? formatBytes(target.size_bytes || 0) : t('notPresent'))}</span>
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
        const status = result.success ? (result.skipped ? t('skippedStatus') : t('removedStatus')) : t('failedStatus');
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
    strip.textContent = locked ? (reason || t('writesLockedRestart')) : t('writesEnabled');
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
        updateSettingsWizardChecklist(readSettingsWizardDraft());
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
            { label: t('configProvider'), value: data.codex_config.model_provider },
            { label: t('configModel'), value: data.codex_config.model },
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
                    <div class="text-sm font-mono text-dark-200 truncate">${escapeHtml(item.value || t('emptyValue'))}</div>
                </div>
                <span class="text-xs ${statusClass}">${escapeHtml(statusText)}</span>
            </div>
        `;
    }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
    showSettingsWizardStep(0, { scroll: false });
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
    const settingsPage = document.getElementById('page-settings');
    if (settingsPage) {
        settingsPage.addEventListener('input', () => updateSettingsWizardChecklist(readSettingsWizardDraft()));
        settingsPage.addEventListener('change', () => updateSettingsWizardChecklist(readSettingsWizardDraft()));
    }
});

function previewThemeFromForm() {
    applyThemeSettings({
        theme_preset: document.getElementById('setting-theme-preset')?.value || 'dark',
        theme_custom: readThemeCustomFromForm(),
    });
}
