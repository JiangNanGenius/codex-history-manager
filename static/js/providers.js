/**
 * providers.js - Codex Enhance Manager provider registry UI.
 * Provider Registry 前端渲染与交互逻辑。
 *
 * 设计意图：
 *   - 本文件只操作本地 provider registry，绝不直接写入 Codex auth.json、
 *     config.toml 或 model catalog 文件。所有写操作通过 /api/providers* 接口
 *     由后端代理，保证安全护栏。
 *   - 状态集中管理：providerState 对象持有所有 UI 状态，render 函数为纯函数
 *    （输入 state -> 输出 HTML），便于调试和测试。
 *
 * 动画约定：
 *   - 所有独立渲染项（card、step、tile、preset、list-item 等）均携带
 *     .stagger-item 类，由 render 函数末尾统一调用 triggerStaggerAnimations()
 *     触发交错进入动画。延迟步长 45ms，在 app.js 中集中管理。
 *   - 每个页面根容器额外包裹 .animate-in，利用 CSS 的 .page.active .animate-in
 *     关键帧实现整页内容的 fadeInUp 进入。
 *   - 按钮 ripple 由 attachRippleToButtons() 统一绑定，避免在 innerHTML
 *     重绘后事件丢失。
 *
 * 工程权衡：
 *   - 使用模板字符串（template literal）拼接 HTML：Provider 管理页字段极多，
 *     使用框架（React/Vue）会增加打包体积和复杂度；innerHTML 拼接在字段数量
 *     <100 时性能可接受，且无需虚拟 DOM diff。
 *   - 每次操作后重新渲染整页：逻辑简单，但大列表时可能有闪烁；当前 provider
 *     数量通常 <20，完全在可接受范围内。
 */

let providerState = {
    providers: [],
    presets: [],
    selectedProviderId: '',
    catalogPreview: null,
    catalogFilters: {
        provider: '',
        capability: '',
        minContext: '',
        maxInputPrice: '',
        currency: '',
    },
    draftError: '',
    proxyStatus: null,
    responsesProbePreview: null,
    mediaRoutePreview: null,
    mediaAdapterPreview: null,
    quotaPreview: null,
    healthPreview: null,
    requestPreview: null,
    modelFetchPreview: null,
    focus_provider_id: '',
    overviewRuntimeStatus: null,
    overviewProxyStatus: null,
};

const QUICK_SETUP_STEP_COUNT = 5;
let quickSetupStep = 0;

const PROVIDER_API_FORMATS = new Set([
    'openai_responses',
    'openai_chat',
    'openai_images',
    'openai_videos',
    'openai_compatible',
    'anthropic',
    'custom',
]);

const VISIBLE_PROVIDER_API_FORMATS = [
    'openai_responses',
    'openai_chat',
    'openai_images',
    'openai_compatible',
    'anthropic',
    'custom',
];

const MODEL_DETAIL_CAPABILITIES = [
    ['text', 'textCapability'],
    ['vision', 'visionInputCapability'],
    ['custom_tools', 'customToolsCapability'],
    ['reasoning', 'reasoningCapability'],
    ['images', 'imagesCapability'],
];

const NATIVE_LOCKED_CAPABILITIES = {
    text: true,
    vision: true,
    tools: true,
    custom_tools: true,
    reasoning: true,
    streaming: true,
    compact: true,
    images: true,
    videos: false,
    embeddings: true,
    models: true,
    balance: false,
    quota: false,
    native_approval: true,
};

/**
 * 加载 Overview 数据并渲染。
 * 注意：renderEnhanceOverview 内部已包含 triggerStaggerAnimations
 * 与 attachRippleToButtons 调用，无需在此处额外触发。
 */
async function loadEnhanceOverview() {
    renderEnhanceOverview();
    setStatus(t('loading') || 'Loading...');
    setTimeout(async () => {
        await ensureProviderData();
        await refreshOverviewRuntimeStatus();
        if (currentPage !== 'overview') return;
        renderEnhanceOverview();
        setStatus('Overview loaded');
    }, 0);
}

/**
 * 加载 Quick Setup 数据并渲染。
 */
async function loadQuickSetup() {
    await ensureProviderData();
    renderQuickSetup();
    setStatus('Quick setup loaded');
}

/**
 * 加载 Providers 管理页。
 * 若未选中任何 provider，默认选中第一个以便编辑器立即有内容。
 * Catalog Preview 异步刷新，渲染时可能使用旧缓存，随后自动更新。
 */
async function loadProvidersPage() {
    await ensureProviderData();
    if (!providerState.selectedProviderId && providerState.providers.length) {
        providerState.selectedProviderId = providerState.providers[0].id;
    }
    await Promise.all([refreshCatalogPreview(), refreshProxyStatus(), refreshSelectedQuotaCache()]);
    renderProvidersPage();
    setStatus(t('providersLoaded'));
}

async function ensureProviderData() {
    /**
     * 确保 providerState 已加载 providers 和 presets 数据。
     * 使用 Promise.all 并行请求，减少等待时间。
     * 失败时通过 showToast 提示用户，并将错误暂存到 draftError 供调试。
     */
    try {
        const [providersData, presetsData] = await Promise.all([
            api('/api/providers'),
            api('/api/provider-presets'),
        ]);
        providerState.providers = providersData.providers || [];
        providerState.focus_provider_id = providersData.focus_provider_id || '';
        providerState.presets = presetsData.presets || [];
    } catch (err) {
        providerState.draftError = err.message;
        showToast(t('failed') + err.message, 'error');
    }
}

async function refreshOverviewRuntimeStatus() {
    const [syncResult, proxyResult] = await Promise.allSettled([
        api('/api/sync/status'),
        api('/api/proxy/status'),
    ]);
    providerState.overviewRuntimeStatus = syncResult.status === 'fulfilled'
        ? syncResult.value
        : { error: syncResult.reason && syncResult.reason.message ? syncResult.reason.message : t('unknownError') };
    providerState.overviewProxyStatus = proxyResult.status === 'fulfilled'
        ? proxyResult.value
        : { running: false, error: proxyResult.reason && proxyResult.reason.message ? proxyResult.reason.message : t('unknownError') };
}

async function refreshCatalogPreview(focusProviderId = '') {
    /**
     * 异步刷新 UMC Catalog 预览数据。
     *
     * 设计意图：
     *   - Catalog Preview 与 provider 列表独立加载：即使预览接口失败，
     *     provider 编辑器仍可正常使用。
     *   - focusProviderId 支持「Focus Selected」功能：临时以选中 provider
     *     为焦点预览 Catalog，不修改 registry 状态。
     *
     * 边界条件：
     *   - 请求失败时不抛出异常，而是将 catalogPreview 设为安全默认值，
     *     避免 render 函数因访问 undefined 而崩溃。
     */
    const query = focusProviderId ? '?focus_provider_id=' + encodeURIComponent(focusProviderId) : '';
    try {
        providerState.catalogPreview = await api('/api/model-catalog/preview' + query);
    } catch (err) {
        providerState.catalogPreview = { entries: [], route_explanation: [t('previewFailedWithError', { error: err.message })] };
    }
}

async function refreshCatalogPreviewDraft(provider) {
    const draft = readProviderForm(provider);
    if (!draft) return false;
    try {
        providerState.catalogPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/model-catalog/preview-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        return true;
    } catch (err) {
        providerState.catalogPreview = { entries: [], route_explanation: [t('draftPreviewFailedWithError', { error: err.message })] };
        showProviderFormError(err.message);
        return false;
    }
}

async function refreshSelectedQuotaCache() {
    const provider = getSelectedProvider();
    if (!provider) {
        providerState.quotaPreview = null;
        return;
    }
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id) + '/quota');
        if (provider.id !== providerState.selectedProviderId) return;
        providerState.quotaPreview = result && result.cache_hit ? result : null;
    } catch (err) {
        if (provider.id === providerState.selectedProviderId) providerState.quotaPreview = null;
    }
}

/**
 * 渲染 Enhance Overview 页面。
 * 所有 .card.stagger-item、.enhance-step-row.stagger-item 与
 * .guardrail-line.stagger-item 会在 innerHTML 写入后由页面尾部统一触发
 * triggerStaggerAnimations(root)，产生依次滑入的 stagger 效果。
 */
function renderEnhanceOverview() {
    /**
     * 渲染 Enhance Overview 页面。
     * 所有 .card.stagger-item、.enhance-step-row.stagger-item 与
     * .guardrail-line.stagger-item 会在 innerHTML 写入后由页面尾部统一触发
     * triggerStaggerAnimations(root)，产生依次滑入的 stagger 效果。
     */
    const root = document.getElementById('enhance-overview-root');
    if (!root) return;
    const providers = providerState.providers || [];
    const enabledCount = providers.filter(p => p.enabled).length;
    const alwaysVisible = providers.filter(p => p.catalog_visibility === 'always_visible').length;
    const selectedModels = providers.reduce((sum, p) => sum + (p.models || []).filter(m => m.selected).length, 0);
    const mediaProviders = providers.filter(p => p.media_profile && p.media_profile.default_image_provider).length;
    const focusProvider = providers.find(p => p.id === providerState.focus_provider_id) || null;

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">${escapeHtml(t('providerOverviewTitle'))}</h2>
                <p class="text-sm text-dark-400 mt-1">${escapeHtml(t('providerOverviewDesc'))}</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('enabled', t('providersEnabled', { count: enabledCount }), 'emerald')}
                ${renderStatusPill('preview', t('codexWritesPreviewOnly'), 'amber')}
                ${renderStatusPill('manual', t('codexMutationManual'), 'accent')}
            </div>
        </div>

        <div class="mt-6">
            ${renderCurrentProviderOverviewCard(focusProvider, providerState.overviewRuntimeStatus, providerState.overviewProxyStatus)}
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mt-6">
            ${renderMetricCard(t('providersLabel'), providers.length, t('enabledCountSub', { count: enabledCount }))}
            ${renderMetricCard(t('alwaysVisibleMetric'), alwaysVisible, t('umcPinnedProviders'))}
            ${renderMetricCard(t('selectedModelsMetric'), selectedModels, t('visibleModelPicks'))}
            ${renderMetricCard(t('mediaProfilesMetric'), mediaProviders, t('imageVideoDefaults'))}
        </div>

        <div class="grid grid-cols-1 gap-4 mt-6">
            <div class="card">
                <div class="flex items-center justify-between gap-3">
                    <h3 class="card-title">${escapeHtml(t('implementationShell'))}</h3>
                    <button onclick="navigateTo('quick-setup')" class="btn btn-secondary text-xs">${escapeHtml(t('quickSetup'))}</button>
                </div>
                <div class="enhance-step-list mt-3">
                    ${renderStepRow(t('providerRegistry'), t('providerRegistryDesc'), true)}
                    ${renderStepRow(t('catalogPreviewTitle'), t('catalogPreviewDesc'), true)}
                    ${renderStepRow(t('codexConfigPreviewTitle'), t('codexConfigPreviewDesc'), false)}
                    ${renderStepRow(t('routeSimulator'), t('routeSimulatorDesc'), true)}
                </div>
            </div>
        </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

function renderCurrentProviderOverviewCard(focusProvider, runtimeStatus, proxyStatus) {
    const runtime = runtimeStatus || {};
    const proxy = proxyStatus || {};
    const focusName = focusProvider
        ? (focusProvider.display_name || focusProvider.provider_visible_alias || focusProvider.short_alias || focusProvider.id)
        : t('notConfigured');
    const focusId = focusProvider ? (focusProvider.id || '') : '';
    const focusMode = focusProvider
        ? (isCodexLoginProvider(focusProvider) || focusProvider.switch_only || focusProvider.local_proxy_routing === false
            ? t('overviewOfficialSwitchOnly')
            : t('overviewLocalProxyRoute'))
        : t('overviewNoFocusProvider');
    const codexProvider = runtime.current_provider || runtime.raw_current_provider || t('statusUnknown');
    const codexModel = runtime.current_model || runtime.raw_current_model || t('statusUnknown');
    const authMode = runtime.auth_mode ? providerOptionLabel(runtime.auth_mode) : t('statusUnknown');
    const codexRunning = runtime.codex_running ? t('statusRunning') : t('statusStopped');
    const proxyBaseUrl = proxy.base_url || (proxy.port ? `http://127.0.0.1:${proxy.port}/v1` : '');
    const proxyLine = proxy.running
        ? (proxyBaseUrl || t('statusRunning'))
        : (proxy.error ? `${t('statusStopped')} · ${proxy.error}` : t('statusStopped'));
    const proxyTone = proxy.running ? 'emerald' : 'dark';
    return `
        <div class="card stagger-item">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
                <div>
                    <div class="card-label">${escapeHtml(t('overviewCurrentProvider'))}</div>
                    <h3 class="text-xl font-semibold text-white mt-1">${escapeHtml(focusName)}</h3>
                    <p class="text-xs text-dark-400 mt-1">${escapeHtml(focusId || t('emptyValue'))} · ${escapeHtml(focusMode)}</p>
                </div>
                <div class="enhance-status-strip">
                    ${renderStatusPill('codex', `${t('codexLabel')}: ${codexRunning}`, runtime.codex_running ? 'emerald' : 'dark')}
                    ${renderStatusPill('proxy', `${t('localProxyTitle')}: ${proxy.running ? t('statusRunning') : t('statusStopped')}`, proxyTone)}
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mt-4 text-sm">
                ${renderReadonlyKV(t('overviewFocusedProvider'), focusName)}
                ${renderReadonlyKV(t('overviewCodexProvider'), codexProvider)}
                ${renderReadonlyKV(t('modelLabel'), codexModel)}
                ${renderReadonlyKV(t('authMode'), authMode)}
            </div>
            <div class="mt-3 rounded-md border border-dark-800 bg-dark-900/50 px-3 py-2 text-xs text-dark-400 break-all">
                ${escapeHtml(t('overviewProxyStatus'))}: ${escapeHtml(proxyLine)}
            </div>
        </div>
    `;
}

/**
 * 渲染 Quick Setup 页面。
 * Preset card 与 Next Steps tile 均带 .stagger-item，配合交错延迟
 * 让导入流程显得有节奏感，而非一次性轰击视觉。
 */
function renderQuickSetup() {
    /**
     * Render Quick Setup as a guided flow. The page intentionally stays inside
     * the Quick Setup tab so first-time users do not bounce between pages.
     */
    const root = document.getElementById('quick-setup-root');
    if (!root) return;
    const summary = quickSetupSummary();

    root.innerHTML = `
        <div class="animate-in">
            <div class="card quick-setup-wizard-shell">
                <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
                    <div>
                        <p class="text-xs font-semibold uppercase tracking-[0.18em] text-accent-300">${escapeHtml(t('quickSetupKicker'))}</p>
                        <h2 class="text-2xl font-semibold text-white mt-1">${escapeHtml(t('quickSetup'))}</h2>
                        <p class="text-sm text-dark-400 mt-2 max-w-3xl">${escapeHtml(t('quickSetupDesc'))}</p>
                    </div>
                    <div class="enhance-status-strip">
                        ${renderStatusPill('providers', t('quickSetupProviderCount', { count: summary.enabledProviders }), summary.enabledProviders ? 'emerald' : 'amber')}
                        ${renderStatusPill('models', t('quickSetupModelCount', { count: summary.selectedModels }), summary.selectedModels ? 'emerald' : 'amber')}
                        ${renderStatusPill('step', t('quickSetupProgress', { current: quickSetupStep + 1, total: QUICK_SETUP_STEP_COUNT }), 'accent')}
                    </div>
                </div>
                <div class="quick-setup-steps mt-5">
                    ${renderQuickSetupStepButton(0, t('quickSetupStepPreset'))}
                    ${renderQuickSetupStepButton(1, t('quickSetupStepConnection'))}
                    ${renderQuickSetupStepButton(2, t('quickSetupStepModels'))}
                    ${renderQuickSetupStepButton(3, t('quickSetupStepRouting'))}
                    ${renderQuickSetupStepButton(4, t('quickSetupStepFinish'))}
                </div>
                <div class="quick-setup-checklist mt-5">
                    ${renderQuickSetupCheck(summary.providerReady, t('quickSetupCheckProvider'), summary.providerReady ? t('quickSetupCheckProviderReady') : t('quickSetupCheckProviderMissing'))}
                    ${renderQuickSetupCheck(summary.connectionReady, t('quickSetupCheckConnection'), summary.connectionReady ? t('quickSetupCheckConnectionReady') : t('quickSetupCheckConnectionMissing'))}
                    ${renderQuickSetupCheck(summary.modelsReady, t('quickSetupCheckModels'), summary.modelsReady ? t('quickSetupCheckModelsReady', { count: summary.selectedModels }) : t('quickSetupCheckModelsMissing'))}
                    ${renderQuickSetupCheck(summary.mediaReady, t('quickSetupCheckMedia'), summary.mediaReady ? t('quickSetupCheckMediaReady', { count: summary.mediaFallbacks }) : t('quickSetupCheckMediaOptional'), true)}
                </div>
            </div>

            <div class="card quick-setup-panel mt-4">
                ${renderQuickSetupPanel(summary)}
            </div>

            <div class="card quick-setup-actions mt-4">
                <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                    <p class="text-sm text-dark-400">${escapeHtml(t('quickSetupActionHint'))}</p>
                    <div class="flex flex-wrap gap-2">
                        <button onclick="showPreviousQuickSetupStep()" class="btn btn-secondary" ${quickSetupStep === 0 ? 'disabled' : ''}>${escapeHtml(t('settingsWizardPrevious'))}</button>
                        <button onclick="showNextQuickSetupStep()" class="btn btn-primary">${escapeHtml(quickSetupStep === QUICK_SETUP_STEP_COUNT - 1 ? t('quickSetupFinishAction') : t('settingsWizardNext'))}</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

function quickSetupSummary() {
    const providers = providerState.providers || [];
    const selected = providers.find(provider => provider.id === providerState.selectedProviderId) || providers[0] || null;
    const enabledProviders = providers.filter(provider => provider && provider.enabled !== false);
    const selectedModels = providers.reduce((sum, provider) => (
        sum + (provider.models || []).filter(model => model && model.selected && model.enabled !== false).length
    ), 0);
    const mediaFallbacks = providers.filter(provider => {
        const profile = provider.media_profile || {};
        return profile.default_image_provider;
    }).length;
    const connectionReady = Boolean(selected && selected.base_url && ((selected.auth_mode === 'no_auth') || selected.api_key || Object.keys(selected.headers || {}).length));
    return {
        providers,
        selected,
        enabledProviders: enabledProviders.length,
        selectedModels,
        mediaFallbacks,
        providerReady: enabledProviders.length > 0,
        connectionReady,
        modelsReady: selectedModels > 0,
        mediaReady: mediaFallbacks > 0,
    };
}

function renderQuickSetupStepButton(index, label) {
    const active = index === quickSetupStep;
    const complete = index < quickSetupStep;
    return `
        <button type="button" class="quick-setup-step ${active ? 'active' : ''} ${complete ? 'complete' : ''}" onclick="showQuickSetupStep(${index})">
            <span class="quick-setup-step-index">${index + 1}</span>
            <span>${escapeHtml(label)}</span>
        </button>
    `;
}

function renderQuickSetupCheck(ready, label, detail, optional = false) {
    const state = ready ? 'ready' : optional ? 'optional' : 'warn';
    const badge = ready ? t('wizardReady') : optional ? t('wizardOptional') : t('wizardNeedsInput');
    return `
        <div class="quick-setup-check ${state}">
            <div class="flex items-center justify-between gap-3">
                <span class="font-semibold">${escapeHtml(label)}</span>
                <span class="text-xs">${escapeHtml(badge)}</span>
            </div>
            <p class="mt-1 text-xs opacity-80">${escapeHtml(detail)}</p>
        </div>
    `;
}

function renderQuickSetupPanel(summary) {
    if (quickSetupStep === 0) return renderQuickSetupPresetStep();
    if (quickSetupStep === 1) return renderQuickSetupConnectionStep(summary);
    if (quickSetupStep === 2) return renderQuickSetupModelsStep(summary);
    if (quickSetupStep === 3) return renderQuickSetupRoutingStep(summary);
    return renderQuickSetupFinishStep(summary);
}

function renderQuickSetupPresetStep() {
    const presets = providerState.presets || [];
    const domestic = presets.filter(p => p.category === 'domestic');
    const generic = presets.filter(p => p.category !== 'domestic');
    return `
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
                <h3 class="card-title">${escapeHtml(t('quickSetupPresetTitle'))}</h3>
                <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('quickSetupPresetDesc'))}</p>
            </div>
            <button onclick="createBlankProvider()" class="btn btn-secondary text-xs">${escapeHtml(t('newProvider'))}</button>
        </div>
        <div class="grid grid-cols-1 xl:grid-cols-2 gap-4 mt-5">
            <div>
                <h4 class="text-sm font-semibold text-dark-200">${escapeHtml(t('genericPresets'))}</h4>
                <div class="preset-list mt-3">${generic.map(renderPresetCard).join('') || renderEmptyState(t('noGenericPresets'))}</div>
            </div>
            <div>
                <h4 class="text-sm font-semibold text-dark-200">${escapeHtml(t('domesticPresets'))}</h4>
                <div class="preset-list mt-3">${domestic.map(renderPresetCard).join('') || renderEmptyState(t('noDomesticPresets'))}</div>
            </div>
        </div>
    `;
}

function renderQuickSetupConnectionStep(summary) {
    const selected = summary.selected;
    return `
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
                <h3 class="card-title">${escapeHtml(t('quickSetupConnectionTitle'))}</h3>
                <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('quickSetupConnectionDesc'))}</p>
            </div>
            <button onclick="navigateTo('providers')" class="btn btn-primary text-xs">${escapeHtml(t('wizardProviderGuideSecondary'))}</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-5">
            ${renderQuickSetupInfoTile(t('displayName'), selected ? selected.display_name : t('emptyValue'))}
            ${renderQuickSetupInfoTile(t('baseUrl'), selected && selected.base_url ? selected.base_url : t('quickSetupMissingBaseUrl'))}
            ${renderQuickSetupInfoTile(t('authMode'), selected ? providerOptionLabel(selected.auth_mode || 'provider_api_key') : t('emptyValue'))}
        </div>
        <div class="mt-4 rounded-xl border border-dark-800 bg-dark-950/45 p-4 text-sm text-dark-300">
            ${escapeHtml(summary.connectionReady ? t('quickSetupConnectionReady') : t('quickSetupConnectionTodo'))}
        </div>
    `;
}

function renderQuickSetupModelsStep(summary) {
    const selected = summary.selected;
    const models = selected ? (selected.models || []) : [];
    const selectedRows = models.filter(model => model && model.selected && model.enabled !== false);
    return `
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
                <h3 class="card-title">${escapeHtml(t('quickSetupModelsTitle'))}</h3>
                <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('quickSetupModelsDesc'))}</p>
            </div>
            <button onclick="navigateTo('providers')" class="btn btn-primary text-xs">${escapeHtml(t('wizardProviderGuideSecondary'))}</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-5">
            ${renderQuickSetupInfoTile(t('selectedModelsMetric'), String(summary.selectedModels))}
            ${renderQuickSetupInfoTile(t('apiFormat'), selected ? providerOptionLabel(selected.api_format || '') : t('emptyValue'))}
            ${renderQuickSetupInfoTile(t('catalogVisibility'), selected ? providerOptionLabel(selected.catalog_visibility || '') : t('emptyValue'))}
        </div>
        <div class="mt-4 space-y-2">
            ${selectedRows.slice(0, 6).map(model => `
                <div class="rounded-lg border border-dark-800 bg-dark-950/45 px-3 py-2 text-sm text-dark-200">
                    <span class="font-medium">${escapeHtml(model.display_name || model.id)}</span>
                    <span class="text-xs text-dark-500 ml-2">${escapeHtml(model.id || '')}</span>
                </div>
            `).join('') || renderEmptyState(t('quickSetupModelsMissing'))}
        </div>
    `;
}

function renderQuickSetupRoutingStep(summary) {
    return `
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
                <h3 class="card-title">${escapeHtml(t('quickSetupRoutingTitle'))}</h3>
                <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('quickSetupRoutingDesc'))}</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="navigateTo('providers')" class="btn btn-primary text-xs">${escapeHtml(t('wizardProviderGuideSecondary'))}</button>
                <button onclick="navigateTo('diagnostics')" class="btn btn-secondary text-xs">${escapeHtml(t('navDiagnostics'))}</button>
            </div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-5">
            ${renderPreviewTile('1', t('quickSetupRouteOrder'), t('quickSetupRouteOrderDesc'))}
            ${renderPreviewTile('2', t('quickSetupRouteMedia'), summary.mediaReady ? t('quickSetupCheckMediaReady', { count: summary.mediaFallbacks }) : t('quickSetupCheckMediaOptional'))}
            ${renderPreviewTile('3', t('quickSetupRouteTest'), t('routeSimulatorDesc'))}
        </div>
    `;
}

function renderQuickSetupFinishStep(summary) {
    const ready = summary.providerReady && summary.connectionReady && summary.modelsReady;
    return `
        <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
                <h3 class="card-title">${escapeHtml(t('quickSetupFinishTitle'))}</h3>
                <p class="text-sm text-dark-400 mt-2">${escapeHtml(ready ? t('quickSetupFinishReadyDesc') : t('quickSetupFinishTodoDesc'))}</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="navigateTo('providers')" class="btn btn-primary text-xs">${escapeHtml(t('wizardProviderGuideSecondary'))}</button>
                <button onclick="navigateTo('codex-integration')" class="btn btn-secondary text-xs">${escapeHtml(t('navCodexIntegration'))}</button>
            </div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mt-5">
            ${renderQuickSetupCheck(summary.providerReady, t('quickSetupCheckProvider'), summary.providerReady ? t('quickSetupCheckProviderReady') : t('quickSetupCheckProviderMissing'))}
            ${renderQuickSetupCheck(summary.connectionReady, t('quickSetupCheckConnection'), summary.connectionReady ? t('quickSetupCheckConnectionReady') : t('quickSetupCheckConnectionMissing'))}
            ${renderQuickSetupCheck(summary.modelsReady, t('quickSetupCheckModels'), summary.modelsReady ? t('quickSetupCheckModelsReady', { count: summary.selectedModels }) : t('quickSetupCheckModelsMissing'))}
        </div>
    `;
}

function renderQuickSetupInfoTile(label, value) {
    return `
        <div class="rounded-xl border border-dark-800 bg-dark-950/45 p-4">
            <div class="text-xs text-dark-500">${escapeHtml(label)}</div>
            <div class="text-sm text-dark-100 mt-1 break-all">${escapeHtml(value || '-')}</div>
        </div>
    `;
}

function showQuickSetupStep(step) {
    quickSetupStep = Math.min(Math.max(Number(step) || 0, 0), QUICK_SETUP_STEP_COUNT - 1);
    renderQuickSetup();
}

function showNextQuickSetupStep() {
    if (quickSetupStep >= QUICK_SETUP_STEP_COUNT - 1) {
        navigateTo('providers');
        return;
    }
    showQuickSetupStep(quickSetupStep + 1);
}

function showPreviousQuickSetupStep() {
    showQuickSetupStep(quickSetupStep - 1);
}

/**
 * 渲染 Providers 管理页（三栏布局：列表 / 编辑器 / Catalog）。
 * Provider list item、preset button、catalog entry 均为 stagger-item，
 * 切换选中 provider 时整栏重新渲染并重新触发进入动画，给用户
 * 明确的“内容已刷新”反馈。
 */
function renderProvidersPage() {
    /**
     * 渲染 Providers 管理页（三栏布局：列表 / 编辑器 / Catalog）。
     * Provider list item、preset button、catalog entry 均为 stagger-item，
     * 切换选中 provider 时整栏重新渲染并重新触发进入动画，给用户
     * 明确的“内容已刷新”反馈。
     */
    const root = document.getElementById('providers-root');
    if (!root) return;
    const providers = providerState.providers || [];
    const selected = providers.find(p => p.id === providerState.selectedProviderId) || providers[0] || null;
    if (selected) providerState.selectedProviderId = selected.id;

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">${escapeHtml(t('providersPageTitle'))}</h2>
                <p class="text-sm text-dark-400 mt-1">${escapeHtml(t('providersPageDesc'))}</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="showCreateProviderModal()" class="btn btn-primary">${escapeHtml(t('newProvider'))}</button>
                <button onclick="exportProviderBundle()" class="btn btn-secondary">${escapeHtml(t('exportRedacted'))}</button>
            </div>
        </div>

        ${renderProviderResponsibilityStrip()}

        <div class="grid grid-cols-1 2xl:grid-cols-[320px_1fr_420px] gap-4 mt-6">
            <div class="space-y-4">
                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('providerList'))}</h3>
                    <div class="space-y-2 mt-3">
                        ${providers.map(renderProviderListItem).join('') || renderEmptyState(t('noProvidersYet'))}
                    </div>
                </div>
                ${renderProxyControlCard()}
            </div>

            <div class="space-y-4">
                ${selected ? renderProviderEditor(selected) : renderEmptyProviderEditor()}
            </div>

            <div id="provider-side-panel-root" class="space-y-4">
                ${renderProviderSidePanel()}
            </div>
        </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
    syncApprovalModeControls();
    syncResponsesModeControls();
    syncMediaModeControls();
}

function renderProviderSidePanel() {
    return `
        ${renderProviderScopeCard()}
        ${renderCatalogPreviewPanel()}
    `;
}

function refreshProviderSidePanel() {
    const root = document.getElementById('provider-side-panel-root');
    if (!root) {
        renderProvidersPage();
        return;
    }
    root.innerHTML = renderProviderSidePanel();
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

function renderProviderResponsibilityStrip() {
    return `
        <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mt-5">
            ${renderProviderResponsibilityTile(t('providerScopeConnection'), t('providerScopeConnectionDesc'))}
                ${renderProviderResponsibilityTile(t('providerScopeModels'), t('providerScopeModelsDesc'))}
                ${renderProviderResponsibilityTile(t('providerScopeMediaQuota'), t('providerScopeMediaQuotaDesc'))}
                <div class="rounded-lg border border-amber-700/40 bg-amber-950/20 p-3">
                <div class="text-xs font-semibold text-amber-200">${escapeHtml(t('providerScopeRouting'))}</div>
                <div class="text-xs text-amber-100/80 mt-1">${escapeHtml(t('providerScopeRoutingDesc'))}</div>
                <button onclick="navigateTo('amr')" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('openSmartRouting'))}</button>
            </div>
        </div>
    `;
}

function renderProviderResponsibilityTile(title, detail) {
    return `
        <div class="rounded-lg border border-dark-800 bg-dark-950/45 p-3">
            <div class="text-xs font-semibold text-dark-200">${escapeHtml(title)}</div>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(detail)}</div>
        </div>
    `;
}

function renderProviderScopeCard() {
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('providerScopeTitle'))}</h3>
            <div class="space-y-2 mt-3 text-sm text-dark-300">
                <div>${escapeHtml(t('providerScopeKeepHere'))}</div>
                <div>${escapeHtml(t('providerScopeMoveToAmr'))}</div>
                <div>${escapeHtml(t('providerScopeCodexApply'))}</div>
            </div>
            <button onclick="navigateTo('amr')" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('openSmartRouting'))}</button>
        </div>
    `;
}

function renderProviderListItem(provider) {
    /**
     * 渲染 Provider 列表项（左侧栏）。
     *
     * 设计细节：
     *   - 左侧竖条指示器在 active 时通过 ::after scaleY(0→1) 从顶部向下生长。
     *   - hover 时 translateX(3px) 制造轻微右移反馈。
     *   - 若 provider.status.last_error 存在，在列表项底部显示红色错误摘要，
     *     帮助用户一眼定位问题 provider。
     *   - visibility 下拉选择支持快捷切换，点击时阻止事件冒泡避免触发选中。
    */
    const active = provider.id === providerState.selectedProviderId;
    const focused = provider.id === providerState.focus_provider_id;
    const error = provider.status && provider.status.last_error;
    const visOptions = [
        { value: 'hidden', label: t('catalogVisibilityHidden') },
        { value: 'focused_only', label: t('catalogVisibilityFocused') },
        { value: 'always_visible', label: t('catalogVisibilityAlways') },
        { value: 'selected_models', label: t('catalogVisibilitySelected') },
    ];
    const visSelect = `
        <select
            onchange="event.stopPropagation(); setProviderVisibility('${escapeAttr(provider.id)}', this.value, this)"
            onclick="event.stopPropagation()"
            class="input text-xs py-0.5 px-1.5 w-auto min-w-[80px]"
            ${isCodexLoginProvider(provider) ? 'disabled' : ''}
        >
            ${visOptions.map(o => `<option value="${escapeAttr(o.value)}" ${provider.catalog_visibility === o.value ? 'selected' : ''}>${escapeHtml(o.label)}</option>`).join('')}
        </select>
    `;
    return `
        <div class="provider-list-item stagger-item ${active ? 'active' : ''}">
            <div onclick="selectProvider('${escapeAttr(provider.id)}')" class="cursor-pointer">
                <div class="flex items-center justify-between gap-2">
                    <div class="min-w-0">
                        <div class="font-medium text-sm text-white truncate">${escapeHtml(provider.display_name)}</div>
                        <div class="text-xs text-dark-400 truncate">${escapeHtml(provider.short_alias)} · ${escapeHtml(provider.native_currency || '-')}</div>
                    </div>
                    <span class="status-dot ${provider.enabled ? 'bg-emerald-500' : 'bg-dark-500'}"></span>
                </div>
                ${focused ? `<div class="mt-2">${renderMiniBadge(t('currentSwitchProvider'))}</div>` : ''}
            </div>
            <div class="flex items-center justify-between gap-2 mt-2">
                ${error ? `<div class="text-xs text-red-300 truncate flex-1">${escapeHtml(error)}</div>` : '<div></div>'}
                ${visSelect}
            </div>
        </div>
    `;
}

/**
 * 渲染 Provider 编辑器（三栏中间栏）。
 * 表单字段较多，未使用 stagger-item（避免过多项同时动画导致眩晕），
 * 但外层 .card 仍参与兜底动画。
 */
function renderProviderEditor(provider) {
    const approvalProfile = provider.approval_profile || {};
    const mediaProfile = provider.media_profile || {};
    const proxyProfile = provider.proxy_profile || {};
    const showMediaAsyncFields = shouldShowMediaAsyncFields(mediaProfile, provider.api_format);
    const providerReadOnly = isCodexLoginProvider(provider);
    const capabilityLocked = isNativeCapabilityLocked(provider);
    const nativeBehaviorLocked = isNativeResponsesProvider(provider);
    const capabilitySource = capabilityLocked ? nativeLockedCapabilities(provider.capabilities || {}) : (provider.capabilities || {});

    if (providerReadOnly) {
        return renderCodexOfficialProviderViewer(provider);
    }

    return `
        <div class="card">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-3">
                <div>
                    <h3 class="card-title">${escapeHtml(t('editProvider'))}</h3>
                    <div class="text-xs text-dark-500 font-mono">${escapeHtml(provider.id)}</div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button onclick="switchSelectedProvider('${escapeAttr(provider.id)}')" class="btn btn-primary text-xs">${escapeHtml(t('switchToThisProvider'))}</button>
                    ${renderProviderStatusStrip(provider)}
                </div>
            </div>

            <div id="provider-form-error" class="hidden mt-3 text-sm text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-3"></div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                ${renderInput('provider-display-name', t('displayName'), provider.display_name, 'text', providerReadOnly)}
                ${renderInput('provider-codex-visible-alias', t('providerVisibleAlias'), provider.codex_visible_alias || provider.short_alias || provider.id, 'text', providerReadOnly)}
                ${renderInput('provider-short-alias', t('internalShortAlias'), provider.short_alias, 'text', true)}
                ${renderInput('provider-base-url', t('baseUrl'), provider.base_url, 'text', providerReadOnly)}
                ${renderSecretInput('provider-api-key', t('apiKey'), provider.api_key || '', 'api_key', providerReadOnly)}
                ${renderSelect('provider-api-format', t('apiFormat'), provider.api_format, visibleApiFormatOptions(provider.api_format), 'syncResponsesModeControls(true); syncMediaModeControls(true)', providerReadOnly || nativeBehaviorLocked)}
                ${renderSelect('provider-auth-mode', t('authMode'), provider.auth_mode, [
                    'provider_api_key',
                    'global_auth_json',
                    'official_oauth',
                    'no_auth',
                ], '', providerReadOnly || nativeBehaviorLocked)}
                ${renderInput('provider-country-region', t('countryRegion'), provider.country_region, 'text', providerReadOnly)}
                ${renderInput('provider-native-currency', t('nativeCurrency'), provider.native_currency, 'text', providerReadOnly)}
                ${renderSelect('provider-visibility', t('catalogVisibility'), provider.catalog_visibility, [
                    'hidden',
                    'focused_only',
                    'always_visible',
                    'selected_models',
                ], '', providerReadOnly)}
                ${renderInput('provider-user-agent', 'User-Agent', provider.user_agent || (provider.headers || {})['User-Agent'] || '', 'text', providerReadOnly)}
            </div>

            ${renderResponsesModeSegment(provider, providerReadOnly)}
            ${nativeBehaviorLocked ? `<div id="native-behavior-lock-note" class="mt-3 text-xs text-emerald-200 bg-emerald-950/20 border border-emerald-700/40 rounded-lg p-3">${escapeHtml(t('nativeProxyBehaviorLocked'))}</div>` : ''}

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="provider-enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${provider.enabled ? 'checked' : ''}>
                    <span>${escapeHtml(t('enabledLabel'))}</span>
                </label>
                <div class="text-xs text-dark-500">${escapeHtml(t('providerFallbackMoved'))}</div>
            </div>

            ${renderProviderModelDetails(provider, providerReadOnly, capabilityLocked)}

            <details class="advanced-box mt-4">
                <summary>${escapeHtml(t('providerDetailedProfile'))}</summary>
                ${renderProviderSectionHeading(t('providerHeadersModels'), t('providerHeadersModelsDesc'))}
                <div class="grid grid-cols-1 gap-4 mt-4">
                    ${renderTextarea('provider-headers-json', t('headersJson'), JSON.stringify(provider.headers || {}, null, 2), 7, '', providerReadOnly)}
                </div>
                ${renderProviderSectionHeading(t('providerAliasesPolicy'), t('providerAliasesPolicyDesc'))}
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('provider-aliases-json', t('modelAliasesJson'), JSON.stringify(provider.aliases || {}, null, 2), 6, '', providerReadOnly)}
                    ${renderTextarea('provider-alias-patterns-json', t('regexRewritePatternsJson'), JSON.stringify(provider.alias_patterns || [], null, 2), 6, '', providerReadOnly)}
                </div>
                <div class="mt-4">
                    <div class="text-xs text-dark-400 mb-2">${escapeHtml(t('proxyNetworkPolicy'))}</div>
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        ${renderCapabilityToggle('proxy-bypass-system-proxy', t('bypassSystemProxy'), proxyProfile.bypass_system_proxy !== false)}
                        ${renderInput('proxy-upstream-timeout', t('upstreamTimeoutSeconds'), proxyProfile.upstream_timeout_seconds || 0, 'number')}
                        ${renderInput('proxy-retry-attempts', t('retryAttempts'), proxyProfile.retry_attempts || 0, 'number')}
                        ${renderInput('proxy-retry-backoff-ms', t('retryBackoffMs'), proxyProfile.retry_backoff_ms || 0, 'number')}
                    </div>
                </div>
                ${renderProviderSectionHeading(t('providerCapabilities'), t('providerCapabilitiesDesc'))}
                <div class="mt-3 text-xs text-emerald-200 bg-emerald-950/20 border border-emerald-700/40 rounded-lg p-3">${escapeHtml(t('providerCoreCapabilitiesLocked'))}</div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('cap-text', t('textCapability'), capabilitySource.text, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-vision', t('visionInputCapability'), capabilitySource.vision, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-custom-tools', t('customToolsCapability'), capabilitySource.custom_tools, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-reasoning', t('reasoningCapability'), capabilitySource.reasoning, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-images', t('imagesCapability'), capabilitySource.images, 'syncMediaModeControls(true)', capabilityLocked || providerReadOnly)}
                </div>
                ${capabilityLocked ? `<div id="native-capability-lock-note" class="mt-3 text-xs text-emerald-200 bg-emerald-950/20 border border-emerald-700/40 rounded-lg p-3">${escapeHtml(t('nativeCapabilitiesLocked'))}</div>` : ''}
                ${renderProviderSectionHeading(t('providerResponsesProfile'), t('providerResponsesProfileDesc'))}
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('responses-domestic', t('domesticResponses'), provider.responses_profile && provider.responses_profile.domestic_responses, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('responses-partial', t('partialResponsesCompatibility'), provider.responses_profile && provider.responses_profile.partial_compatibility, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('responses-requires-adapter', t('responsesAdapterRequired'), provider.responses_profile && provider.responses_profile.requires_adapter, '', providerReadOnly || nativeBehaviorLocked)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderInput('responses-profile-id', t('responsesProfileId'), provider.responses_profile && provider.responses_profile.profile_id, 'text', providerReadOnly || nativeBehaviorLocked)}
                    ${renderInput('responses-docs-url', t('responsesDocsUrl'), provider.responses_profile && provider.responses_profile.verified_docs_url, 'text', providerReadOnly || nativeBehaviorLocked)}
                    ${renderInput('responses-unsupported', t('unsupportedFields'), provider.responses_profile && (provider.responses_profile.unsupported_fields || []).join(', '), 'text', providerReadOnly || nativeBehaviorLocked)}
                </div>
                ${renderApprovalModeSegment(approvalProfile, providerReadOnly || nativeBehaviorLocked)}
                ${renderProviderSectionHeading(t('providerApprovalProfile'), t('providerApprovalProfileDesc'))}
                <div id="approval-auto-fields" class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4 ${approvalModeFromProfile(approvalProfile) === 'proxy_auto_approve' ? '' : 'hidden'}">
                    ${renderInput('approval-reviewer-model', t('reviewerModel'), approvalProfile.reviewer_model || '', 'text', providerReadOnly || nativeBehaviorLocked)}
                    ${renderInput('approval-allowed-actions', t('allowedActions'), (approvalProfile.allowed_actions || []).join(', '), 'text', providerReadOnly || nativeBehaviorLocked)}
                    ${renderSelect('approval-error-policy', t('reviewErrorPolicy'), approvalProfile.on_review_error || 'decline', [
                        'decline',
                        'ask_user',
                        'allow',
                    ], '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderInput('approval-timeout-ms', t('timeoutMs'), approvalProfile.timeout_ms || 90000, 'number', providerReadOnly || nativeBehaviorLocked)}
                    ${renderInput('approval-max-retries', t('maxRetries'), approvalProfile.max_retries || 1, 'number', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('approval-audit-decisions', t('auditDecisions'), approvalProfile.audit_decisions !== false, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('approval-auto-accept-low-risk', t('autoAcceptLowRisk'), approvalProfile.auto_accept_low_risk !== false, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('approval-auto-decline-high-risk', t('autoDeclineHighRisk'), approvalProfile.auto_decline_high_risk !== false, '', providerReadOnly || nativeBehaviorLocked)}
                </div>
                ${renderProviderSectionHeading(t('providerMediaQuota'), t('providerMediaQuotaDesc'))}
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-4">
                    ${renderCapabilityToggle('media-default-image', t('defaultImageProvider'), mediaProfile.default_image_provider, 'syncMediaModeControls(true)', providerReadOnly || nativeBehaviorLocked)}
                </div>
                ${renderMediaModeSegment(mediaProfile, providerReadOnly || nativeBehaviorLocked)}
                ${renderMediaRoutingHint(provider)}
                <div id="media-async-fields" class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4 ${showMediaAsyncFields ? '' : 'hidden'}">
                    ${renderCapabilityToggle('media-async-submit', t('asyncSubmit'), mediaProfile.async_submit, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('media-poll-required', t('pollRequired'), mediaProfile.poll_required, '', providerReadOnly || nativeBehaviorLocked)}
                    ${renderCapabilityToggle('media-cancel-supported', t('cancelSupported'), mediaProfile.cancel_supported, '', providerReadOnly || nativeBehaviorLocked)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('media-image-overrides-json', t('imageOverridesJson'), JSON.stringify(mediaProfile.image_model_overrides || {}, null, 2), 5, '', providerReadOnly || nativeBehaviorLocked)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('provider-quota-json', t('quotaCheckJson'), JSON.stringify(provider.quota_check || {}, null, 2), 8, t('quotaCheckJsonDesc'), providerReadOnly)}
                    ${renderTextarea('provider-quota-script-js', t('quotaScriptJs'), providerQuotaScriptCode(provider), 8, t('quotaScriptJsDesc'), providerReadOnly)}
                </div>
            </details>

            <div class="flex flex-wrap gap-2 mt-5">
                ${providerReadOnly ? '' : `<button onclick="saveSelectedProvider()" class="btn btn-primary">${escapeHtml(t('saveLocalProvider'))}</button>`}
                <button onclick="testSelectedProvider()" class="btn btn-secondary">${escapeHtml(t('testThisSection'))}</button>
                <button onclick="checkProviderHealth()" class="btn btn-secondary">${escapeHtml(t('checkNetwork'))}</button>
                <button onclick="previewProviderRequest()" class="btn btn-secondary">${escapeHtml(t('requestPreview'))}</button>
                <button onclick="previewResponsesProbe()" class="btn btn-secondary">${escapeHtml(t('responsesProbePreview'))}</button>
                <button onclick="previewMediaRoutes()" class="btn btn-secondary">${escapeHtml(t('mediaRouteCheck'))}</button>
                <button onclick="previewMediaAdapter()" class="btn btn-secondary">${escapeHtml(t('mediaAdapterPreview'))}</button>
                <button onclick="refreshProviderQuota()" class="btn btn-secondary">${escapeHtml(t('refreshQuota'))}</button>
                <button onclick="previewProviderDraft()" class="btn btn-ghost">${escapeHtml(t('previewDraft'))}</button>
                ${providerReadOnly ? '' : `<button onclick="deleteSelectedProvider()" class="btn btn-danger">${escapeHtml(t('deleteAction'))}</button>`}
            </div>
        </div>

        <div id="provider-preview-panels-root" class="space-y-4">
            ${renderProviderPreviewPanels()}
        </div>
    `;
}

function renderCodexOfficialProviderViewer(provider) {
    const focused = providerState.focus_provider_id === provider.id;
    const loginStatus = provider.official_oauth_detected || provider.auth_mode === 'official_oauth'
        ? t('officialLoginDetected')
        : t('officialLoginStatusUnknown');
    return `
        <div class="card">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-3">
                <div>
                    <h3 class="card-title">${escapeHtml(t('codexOfficialProviderTitle'))}</h3>
                    <div class="text-xs text-dark-500 font-mono">${escapeHtml(provider.id || 'codex_official')}</div>
                    <p class="text-sm text-dark-400 mt-2 max-w-2xl">${escapeHtml(t('codexOfficialProviderDesc'))}</p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    ${focused ? `<span class="badge bg-emerald-900/40 text-emerald-200 border-emerald-700/50">${escapeHtml(t('currentSwitchProvider'))}</span>` : ''}
                    <button onclick="switchSelectedProvider('${escapeAttr(provider.id)}')" class="btn btn-primary text-xs">${escapeHtml(t('switchToThisProvider'))}</button>
                    <button onclick="startOfficialCodex()" class="btn btn-secondary text-xs">${escapeHtml(t('startOfficialCodex'))}</button>
                </div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 mt-5">
                ${renderReadonlyKV(t('authMode'), providerOptionLabel(provider.auth_mode || 'official_oauth'))}
                ${renderReadonlyKV(t('loginStatus'), loginStatus)}
                ${renderReadonlyKV(t('routingModeLabel'), t('officialProviderSwitchOnly'))}
                ${renderReadonlyKV(t('modelCapabilities'), t('officialManagedCapabilities'))}
            </div>
            <div class="mt-4 text-xs text-amber-200 bg-amber-950/20 border border-amber-700/40 rounded-lg p-3">
                ${escapeHtml(t('officialAmrRiskNotice'))}
            </div>
        </div>
    `;
}

function renderProviderModelDetails(provider, disabled = false, capabilityLocked = false) {
    const models = Array.isArray(provider.models) && provider.models.length
        ? provider.models
        : [{ id: '', display_name: '', context_window: 0, selected: true, enabled: true, capabilities: {} }];
    return `
        <div class="provider-model-details mt-5 rounded-xl border border-dark-800 bg-dark-950/45 p-4">
            <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
                <div>
                    <h4 class="text-sm font-semibold text-white">${escapeHtml(t('providerModelDetailsTitle'))}</h4>
                    <p class="text-xs text-dark-500 mt-1">${escapeHtml(t('providerModelDetailsDesc'))}</p>
                </div>
                ${disabled ? '' : `
                    <div class="flex flex-wrap gap-2">
                        <button type="button" onclick="fetchProviderModels()" class="btn btn-secondary text-xs">${escapeHtml(t('fetchModelList'))}</button>
                        <button type="button" onclick="addProviderModelDetail()" class="btn btn-secondary text-xs">${escapeHtml(t('addModel'))}</button>
                    </div>
                `}
            </div>
            <div class="mt-3 text-xs text-sky-200 bg-sky-950/20 border border-sky-700/40 rounded-lg p-3">
                ${escapeHtml(t('providerModelSingleSourceHint'))}
            </div>
            ${renderProviderModelBulkActions(disabled)}
            <div id="provider-model-detail-list" class="space-y-3 mt-4">
                ${models.map((model, index) => renderProviderModelDetailRow(provider, model, index, disabled, capabilityLocked)).join('')}
            </div>
        </div>
    `;
}

function renderProviderModelBulkActions(disabled = false) {
    if (disabled) return '';
    return `
        <div class="mt-4">
            <div class="text-xs font-semibold text-dark-300 mb-2">${escapeHtml(t('providerModelBulkActions'))}</div>
            <div class="flex flex-wrap gap-2">
                <button data-bulk-action="select_all" onclick="runBulkModelAction('select_all')" class="btn btn-secondary text-xs">${escapeHtml(t('selectAll'))}</button>
                <button data-bulk-action="deselect_all" onclick="runBulkModelAction('deselect_all')" class="btn btn-secondary text-xs">${escapeHtml(t('deselectAll'))}</button>
                <button data-bulk-action="select_vision" onclick="runBulkModelAction('select_vision')" class="btn btn-secondary text-xs">${escapeHtml(t('selectVision'))}</button>
                <button data-bulk-action="select_high_context" onclick="runBulkModelAction('select_high_context')" class="btn btn-secondary text-xs">${escapeHtml(t('selectHighContext'))}</button>
                <button data-bulk-action="select_low_cost" onclick="runBulkModelAction('select_low_cost')" class="btn btn-secondary text-xs">${escapeHtml(t('selectLowCost'))}</button>
                <button data-bulk-action="add_selected_to_amr" onclick="addSelectedModelsToAmr()" class="btn btn-secondary text-xs">${escapeHtml(t('addToAmr'))}</button>
            </div>
            <div id="bulk-action-error" class="hidden mt-2 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
        </div>
    `;
}

function renderProviderModelDetailRow(provider, model, index, disabled = false, capabilityLocked = false) {
    const effectiveCaps = {
        ...(provider && provider.capabilities ? provider.capabilities : {}),
        ...(model && model.capabilities ? model.capabilities : {}),
        ...(model && model.capability_overrides ? model.capability_overrides : {}),
    };
    const disabledAttr = disabled ? ' disabled' : '';
    const capabilityDisabledAttr = (disabled || capabilityLocked) ? ' disabled' : '';
    const apiFormat = model && model.api_format ? model.api_format : '';
    const reasoningProfile = model && model.reasoning_effort_profile && typeof model.reasoning_effort_profile === 'object'
        ? model.reasoning_effort_profile
        : {};
    const hidden = Boolean(model && model.catalog_hidden);
    const selected = model ? (!hidden && model.selected !== false) : true;
    const enabled = !model || model.enabled !== false;
    const rowTitle = model && (model.display_name || model.name || model.id)
        ? (model.display_name || model.name || model.id)
        : t('modelDraftLabel');
    const rowMeta = model && model.id ? model.id : t('modelDraftLabel');
    const rowBadges = [
        enabled ? `<span class="badge bg-emerald-900/35 text-emerald-200 border-emerald-700/40">${escapeHtml(t('enabledLabel'))}</span>` : `<span class="badge bg-dark-800 text-dark-300 border-dark-700">${escapeHtml(t('disabledLabel'))}</span>`,
        model && model.primary ? `<span class="badge bg-sky-900/35 text-sky-200 border-sky-700/40">${escapeHtml(t('primaryModel'))}</span>` : '',
        hidden ? `<span class="badge bg-amber-900/35 text-amber-200 border-amber-700/40">${escapeHtml(t('hideFromCodex'))}</span>` : '',
        selected && !hidden ? `<span class="badge bg-accent-900/35 text-accent-200 border-accent-700/40">${escapeHtml(t('selectedModel'))}</span>` : '',
    ].filter(Boolean).join('');
    return `
        <div class="provider-model-row rounded-lg border border-dark-800 bg-dark-900/55 p-3" data-provider-model-row="${index}" data-original-model-id="${escapeAttr(model.id || '')}">
            <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-3 mb-3">
                <div class="min-w-0">
                    <div class="text-sm font-semibold text-dark-100 truncate">${escapeHtml(rowTitle)}</div>
                    <div class="text-xs text-dark-500 font-mono truncate">${escapeHtml(rowMeta)}</div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    ${rowBadges}
                    ${disabled ? '' : `<button type="button" onclick="removeProviderModelDetail(this)" class="btn btn-ghost text-xs text-red-200 hover:text-red-100">${escapeHtml(t('removeModel'))}</button>`}
                </div>
            </div>
            <div class="grid grid-cols-1 xl:grid-cols-[minmax(160px,1fr)_minmax(200px,2fr)_120px_150px] gap-3">
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('modelId'))}</label>
                    <input data-model-field="id" class="input mt-1 w-full font-mono" value="${escapeAttr(model.id || '')}" placeholder="gpt-5.5"${disabledAttr}>
                </div>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('modelDisplayName'))}</label>
                    <input data-model-field="display_name" class="input mt-1 w-full" value="${escapeAttr(model.display_name || model.id || '')}" placeholder="${escapeAttr(t('displayName'))}"${disabledAttr}>
                </div>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('contextWindow'))}</label>
                    <input data-model-field="context_window" type="number" min="0" step="1000" class="input mt-1 w-full" value="${escapeAttr(model.context_window || 0)}"${disabledAttr}>
                </div>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('modelInterface'))}</label>
                    <select data-model-field="api_format" class="input mt-1 w-full"${capabilityDisabledAttr}>
                        <option value="" ${apiFormat ? '' : 'selected'}>${escapeHtml(t('inheritProvider'))}</option>
                        ${visibleApiFormatOptions(apiFormat).map(format => `
                            <option value="${escapeAttr(format)}" ${apiFormat === format ? 'selected' : ''}>${escapeHtml(providerOptionLabel(format))}</option>
                        `).join('')}
                    </select>
                </div>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3 ${!effectiveCaps.text && effectiveCaps.images ? 'hidden' : ''}" data-llm-only-section>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('reasoningEffortParameter'))}</label>
                    <select data-model-field="reasoning_effort_parameter" class="input mt-1 w-full"${capabilityDisabledAttr}>
                        ${['auto', 'disabled', 'reasoning.effort', 'reasoning_effort', 'output_config.effort', 'thinking'].map(value => `
                            <option value="${escapeAttr(value)}" ${String(reasoningProfile.parameter || 'auto') === value ? 'selected' : ''}>${escapeHtml(reasoningEffortParameterLabel(value))}</option>
                        `).join('')}
                    </select>
                </div>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('reasoningEfforts'))}</label>
                    <input data-model-field="reasoning_efforts" class="input mt-1 w-full" value="${escapeAttr((reasoningProfile.supported_efforts || model.reasoning_efforts || []).join(', '))}" placeholder="low, medium, high"${capabilityDisabledAttr}>
                </div>
                <div>
                    <label class="text-xs text-dark-400">${escapeHtml(t('reasoningEffortDefault'))}</label>
                    <input data-model-field="reasoning_effort_default" class="input mt-1 w-full" value="${escapeAttr(reasoningProfile.default_effort || model.reasoning_effort_default || '')}" placeholder="medium"${capabilityDisabledAttr}>
                </div>
            </div>
            <div class="mt-3">
                <div class="text-xs text-dark-500 mb-2">${escapeHtml(t('modelPricing'))} <span class="text-dark-600">— ${escapeHtml(t('modelPricingDesc'))}</span></div>
                <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('inputPrice'))}</label>
                        <input data-model-pricing="input_per_million" type="number" min="0" step="0.01" class="input mt-1 w-full" value="${escapeAttr((model.pricing && model.pricing.input_per_million) || '')}" placeholder="/M"${disabledAttr}>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('outputPrice'))}</label>
                        <input data-model-pricing="output_per_million" type="number" min="0" step="0.01" class="input mt-1 w-full" value="${escapeAttr((model.pricing && model.pricing.output_per_million) || '')}" placeholder="/M"${disabledAttr}>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('cacheReadPrice'))}</label>
                        <input data-model-pricing="cache_read_per_million" type="number" min="0" step="0.01" class="input mt-1 w-full" value="${escapeAttr((model.pricing && model.pricing.cache_read_per_million) || '')}" placeholder="/M"${disabledAttr}>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('cacheWritePrice'))}</label>
                        <input data-model-pricing="cache_write_per_million" type="number" min="0" step="0.01" class="input mt-1 w-full" value="${escapeAttr((model.pricing && model.pricing.cache_write_per_million) || '')}" placeholder="/M"${disabledAttr}>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('reasoningPrice'))}</label>
                        <input data-model-pricing="reasoning_per_million" type="number" min="0" step="0.01" class="input mt-1 w-full" value="${escapeAttr((model.pricing && model.pricing.reasoning_per_million) || '')}" placeholder="/M"${disabledAttr}>
                    </div>
                </div>
            </div>
            <div class="flex flex-wrap gap-3 mt-3">
                <label class="flex items-center gap-2 text-xs text-dark-300">
                    <input data-model-field="enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${enabled ? 'checked' : ''}${disabledAttr}>
                    <span>${escapeHtml(t('enabledLabel'))}</span>
                </label>
                <span class="flex flex-wrap gap-3 ${!effectiveCaps.text && effectiveCaps.images ? 'hidden' : ''}" data-llm-only-checkboxes>
                <label class="flex items-center gap-2 text-xs text-dark-300">
                    <input data-model-field="selected" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${selected ? 'checked' : ''}${disabledAttr}>
                    <span>${escapeHtml(t('selectedModel'))}</span>
                </label>
                <label class="flex items-center gap-2 text-xs text-dark-300">
                    <input data-model-field="catalog_hidden" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${hidden ? 'checked' : ''}${disabledAttr}>
                    <span>${escapeHtml(t('hideFromCodex'))}</span>
                </label>
                <label class="flex items-center gap-2 text-xs text-dark-300">
                    <input data-model-field="primary" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${model.primary ? 'checked' : ''}${disabledAttr}>
                    <span>${escapeHtml(t('primaryModel'))}</span>
                </label>
                <label class="flex items-center gap-2 text-xs text-dark-300">
                    <input data-model-field="native_approval" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${capabilityLocked || model.native_approval ? 'checked' : ''}${capabilityDisabledAttr}>
                    <span>${escapeHtml(t('nativeApprovalCapability'))}</span>
                </label>
                </span>
            </div>
            <div class="mt-3">
                <div class="text-xs text-dark-500 mb-2">${escapeHtml(t('modelCapabilities'))}</div>
                <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2">
                    ${MODEL_DETAIL_CAPABILITIES.map(([capability, labelKey]) => `
                        <label class="flex items-center gap-2 text-xs rounded-md border border-dark-800 bg-dark-950/45 px-2 py-1.5 ${(disabled || capabilityLocked) ? 'opacity-70' : ''} ${capability !== 'images' ? 'non-image-cap' : ''}">
                            <input data-model-capability="${escapeAttr(capability)}" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${effectiveCaps[capability] ? 'checked' : ''}${capabilityDisabledAttr} onchange="onModelCapabilityChange(this, '${escapeAttr(capability)}')">
                            <span>${escapeHtml(t(labelKey))}</span>
                        </label>
                    `).join('')}
                </div>
            </div>
            <div class="mt-3 ${!effectiveCaps.text && effectiveCaps.images ? '' : 'hidden'}" data-image-advanced>
                <div class="text-xs text-dark-500 mb-2">${escapeHtml(t('imageModelAdvanced'))} <span class="text-dark-600">— ${escapeHtml(t('imageModelAdvancedDesc'))}</span></div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('modelBaseUrl'))}</label>
                        <input data-model-field="model_base_url" class="input mt-1 w-full font-mono" value="${escapeAttr(model.model_base_url || '')}" placeholder="https://api.example.com/v1"${disabledAttr}>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('modelBasePath'))}</label>
                        <input data-model-field="model_base_path" class="input mt-1 w-full font-mono" value="${escapeAttr(model.model_base_path || '')}" placeholder="/images/generations"${disabledAttr}>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function onModelCapabilityChange(checkbox, capability) {
    if (capability !== 'images') return;
    const row = checkbox.closest('[data-provider-model-row]');
    if (!row) return;
    const isImage = checkbox.checked;
    // Update other capability checkboxes: image mode clears others; LLM mode restores text+vision defaults
    row.querySelectorAll('[data-model-capability]').forEach(input => {
        const cap = input.getAttribute('data-model-capability');
        if (cap === 'images') return;
        input.checked = !isImage && (cap === 'text' || cap === 'vision');
    });
    // Toggle non-image capability labels
    row.querySelectorAll('.non-image-cap').forEach(el => {
        el.classList.toggle('hidden', isImage);
    });
    // Toggle LLM-only sections
    const llmSection = row.querySelector('[data-llm-only-section]');
    if (llmSection) llmSection.classList.toggle('hidden', isImage);
    const llmCheckboxes = row.querySelector('[data-llm-only-checkboxes]');
    if (llmCheckboxes) llmCheckboxes.classList.toggle('hidden', isImage);
    // Toggle image-only advanced settings
    const imageAdvanced = row.querySelector('[data-image-advanced]');
    if (imageAdvanced) imageAdvanced.classList.toggle('hidden', !isImage);
}

function renderProviderSectionHeading(title, detail) {
    return `
        <div class="mt-5 border-t border-dark-800 pt-4">
            <div class="text-xs font-semibold text-dark-200">${escapeHtml(title)}</div>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(detail)}</div>
        </div>
    `;
}

function renderProviderPreviewPanels() {
    return `
        <div id="provider-draft-preview" class="card hidden">
            <h3 class="card-title">${escapeHtml(t('draftPreview'))}</h3>
            <pre class="preview-code mt-3"></pre>
        </div>

        <div id="provider-responses-probe-preview" class="card ${providerState.responsesProbePreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('responsesProbePreview'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('noNetworkNoCodexMutation'))}</p>
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.responsesProbePreview || {}, null, 2))}</pre>
        </div>

        <div id="provider-media-adapter-preview" class="card ${providerState.mediaAdapterPreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('mediaAdapterPreview'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('adapterPreviewDesc'))}</p>
            ${renderProviderMediaAdapterResult(providerState.mediaAdapterPreview)}
        </div>

        <div id="provider-media-route-preview" class="card ${providerState.mediaRoutePreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('mediaRouteCheck'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('mediaRouteCheckDesc'))}</p>
            ${renderProviderMediaRouteResult(providerState.mediaRoutePreview)}
        </div>

        <div id="provider-quota-preview" class="card ${providerState.quotaPreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('quotaPreview'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('quotaPreviewDesc'))}</p>
            ${renderProviderQuotaResult(providerState.quotaPreview)}
        </div>

        <div id="provider-health-preview" class="card ${providerState.healthPreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('providerHealthCheck'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('providerHealthDesc'))}</p>
            ${renderProviderHealthResult(providerState.healthPreview)}
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.healthPreview || {}, null, 2))}</pre>
        </div>

        <div id="provider-request-preview" class="card ${providerState.requestPreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('requestPreview'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('requestPreviewDesc'))}</p>
            ${renderProviderRequestPreviewResult(providerState.requestPreview)}
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.requestPreview || {}, null, 2))}</pre>
        </div>

        <div id="provider-model-fetch-preview" class="card ${providerState.modelFetchPreview ? '' : 'hidden'}">
            <h3 class="card-title">${escapeHtml(t('modelFetchPreview'))}</h3>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('modelFetchPreviewDesc'))}</p>
            ${renderProviderModelFetchResult(providerState.modelFetchPreview)}
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.modelFetchPreview || {}, null, 2))}</pre>
        </div>
    `;
}

function refreshProviderPreviewPanels() {
    const root = document.getElementById('provider-preview-panels-root');
    if (!root) return;
    root.innerHTML = renderProviderPreviewPanels();
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

function renderEmptyProviderEditor() {
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('editProvider'))}</h3>
            ${renderEmptyState(t('providerEmptyEdit'))}
        </div>
    `;
}

/**
 * Provider 状态条：enabled / tested / restart / error 四枚 pill。
 * 使用 enhance-status-strip 布局，支持自动换行。
 */
function renderProviderStatusStrip(provider) {
    const status = provider.status || {};
    return `
        <div class="enhance-status-strip">
            ${renderStatusPill(provider.enabled ? 'enabled' : 'disabled', provider.enabled ? t('enabledLabel') : t('disabled'), provider.enabled ? 'emerald' : 'dark')}
            ${renderStatusPill('tested', status.last_tested ? t('lastTested', { value: formatShortDate(status.last_tested) }) : t('notTested'), status.last_tested ? 'accent' : 'dark')}
            ${renderStatusPill('restart', status.needs_restart ? t('needsRestart') : t('noRestart'), status.needs_restart ? 'amber' : 'emerald')}
            ${status.last_error ? renderStatusPill('error', t('hasError'), 'red') : ''}
        </div>
    `;
}

function renderProviderHealthResult(result) {
    if (!result) return '';
    const reachable = Boolean(result.reachable);
    const success = Boolean(result.success);
    const tone = success ? 'emerald' : (reachable ? 'amber' : 'red');
    const label = success ? t('reachable') : (reachable ? t('reachableWithError') : t('unreachable'));
    const status = result.status_code ? `HTTP ${result.status_code}` : t('noStatus');
    const target = result.url || (Array.isArray(result.urls_tested) ? result.urls_tested.join(', ') : '');
    const message = result.note || result.error || t('noDetailsReturned');
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('network', label, tone)}
            ${renderStatusPill('status', status, result.status_code ? 'dark' : 'amber')}
            ${result.method ? renderStatusPill('method', result.method, 'dark') : ''}
        </div>
        <div class="text-xs text-dark-400 mt-2 break-all">${escapeHtml(target)}</div>
        <div class="text-xs ${success ? 'text-emerald-300' : 'text-amber-200'} mt-2">${escapeHtml(message)}</div>
    `;
}

function renderProviderRequestPreviewResult(result) {
    if (!result) return '';
    if (result.success === false) {
        return `<div class="text-xs text-red-300 mt-3">${escapeHtml(result.error || t('unknownError'))}</div>`;
    }
    const requestedModel = result.requested_model || t('notSelected');
    const upstreamModel = result.upstream_model || t('notSelected');
    const headers = result.headers && typeof result.headers === 'object' ? result.headers : {};
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('requested', requestedModel, result.requested_model ? 'accent' : 'amber')}
            ${renderStatusPill('upstream', upstreamModel, result.upstream_model ? 'emerald' : 'amber')}
            ${renderStatusPill('format', result.api_format || t('formatUnknown'), 'dark')}
            ${renderStatusPill('mode', result.network_request ? t('sent') : t('previewOnly'), result.network_request ? 'amber' : 'emerald')}
        </div>
        <div class="text-xs text-dark-400 mt-3 break-all">${escapeHtml(t('upstreamPathLabel'))}: ${escapeHtml(result.base_url || '-')}</div>
        <div class="mt-3">
            <div class="text-xs font-semibold text-dark-300 mb-2">${escapeHtml(t('requestHeadersRedacted'))}</div>
            <pre class="preview-code">${escapeHtml(JSON.stringify(headers, null, 2))}</pre>
        </div>
        ${renderProviderRequestRouteExplanation(result)}
    `;
}

function renderProviderModelFetchResult(result) {
    if (!result) return '';
    if (result.success === false) {
        return `
            <div class="enhance-status-strip mt-3">
                ${renderStatusPill('status', result.status_code ? `HTTP ${result.status_code}` : t('failed'), 'red')}
                ${renderStatusPill('mode', t('previewOnly'), 'emerald')}
            </div>
            <div class="text-xs text-red-300 mt-2 break-all">${escapeHtml(result.error || t('unknownError'))}</div>
            ${result.url ? `<div class="text-xs text-dark-500 mt-2 break-all">${escapeHtml(result.url)}</div>` : ''}
        `;
    }
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('fetched', String(result.fetched_count || 0), 'accent')}
            ${renderStatusPill('added', String(result.added_count || 0), result.added_count ? 'emerald' : 'dark')}
            ${renderStatusPill('updated', String(result.updated_count || 0), result.updated_count ? 'amber' : 'dark')}
            ${renderStatusPill('default', `${result.default_context_window || 200000}`, 'dark')}
            ${renderStatusPill('mode', t('previewOnly'), 'emerald')}
        </div>
        <div class="text-xs text-dark-400 mt-2 break-all">${escapeHtml(result.url || '')}</div>
        <div class="text-xs text-amber-200 mt-2">${escapeHtml(t('modelFetchUnsavedNotice'))}</div>
    `;
}

function renderProviderRequestRouteExplanation(result) {
    const lines = Array.isArray(result.route_explanation) ? result.route_explanation : [];
    if (!lines.length) return '';
    const requestedModel = result.requested_model || '';
    const upstreamModel = result.upstream_model || '';
    const strippedModel = requestedModel.includes('/') ? requestedModel.split('/').slice(1).join('/') : requestedModel;
    const translated = lines.map(line => {
        const text = String(line || '');
        if (text.startsWith('Provider prefix removed')) {
            return t('requestRoutePrefixRemoved', { from: requestedModel, to: strippedModel });
        }
        if (text.startsWith('Exact model alias') || text.startsWith('Case-insensitive model alias') || text.startsWith('Model mapping applied')) {
            return t('requestRouteAliasApplied', { from: strippedModel || requestedModel, to: upstreamModel });
        }
        if (text.startsWith('Regex model mapping')) {
            return t('requestRouteRegexApplied', { from: strippedModel || requestedModel, to: upstreamModel });
        }
        if (text.includes('forwarded unchanged')) return t('requestRouteUnchanged');
        if (text.startsWith('No model is selected')) return t('requestRouteNoModel');
        if (text.startsWith('Preview only')) return t('requestRoutePreviewOnly');
        return text;
    });
    return `
        <div class="mt-3 space-y-1">
            ${translated.map(line => `<div class="text-xs text-dark-400">${escapeHtml(line)}</div>`).join('')}
        </div>
    `;
}

function renderProviderMediaAdapterResult(result) {
    if (!result) return '';
    const previews = Array.isArray(result.previews) ? result.previews : [];
    const modeLabel = result.openai_compatible_media ? t('adapterModeReady') : (result.adapter_required ? t('adapterModeRequired') : t('adapterModePreview'));
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('adapter', result.adapter_id || t('adapterUnknown'), result.adapter_id ? 'accent' : 'amber')}
            ${renderStatusPill('mode', modeLabel, result.adapter_required ? 'amber' : 'dark')}
            ${renderStatusPill('live', result.live_forwarding_enabled ? t('liveEnabled') : t('previewOnly'), result.live_forwarding_enabled ? 'emerald' : 'amber')}
        </div>
        <div class="space-y-2 mt-3">
            ${previews.map(renderMediaAdapterPreviewItem).join('') || renderEmptyState(t('noAdapterPreview'))}
        </div>
        <details class="mt-3">
            <summary class="text-xs text-dark-400 cursor-pointer">${escapeHtml(t('rawAdapterPreview'))}</summary>
            <pre class="preview-code mt-2">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
        </details>
    `;
}

function renderProviderMediaRouteResult(result) {
    if (!result) return '';
    const checks = Array.isArray(result.checks)
        ? result.checks.filter(item => String(item && item.media_kind || '') !== 'video')
        : [];
    const guidanceKeys = Array.isArray(result.guidance_keys) ? result.guidance_keys.filter(Boolean) : [];
    const actionKeys = Array.isArray(result.action_keys) ? result.action_keys.filter(Boolean) : [];
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('forwarding', result.live_forwarding_enabled ? t('forwardingReady') : t('forwardingBlocked'), result.live_forwarding_enabled ? 'emerald' : 'amber')}
            ${renderStatusPill('ready', String(result.ready_count || 0), result.ready_count ? 'emerald' : 'dark')}
            ${renderStatusPill('blocked', String(result.blocked_count || 0), result.blocked_count ? 'amber' : 'dark')}
            ${renderStatusPill('format', result.api_format || t('formatUnknown'), 'dark')}
        </div>
        ${guidanceKeys.length ? `
            <div class="mt-3 rounded-lg border border-amber-800/60 bg-amber-950/10 p-3">
                <div class="text-xs font-semibold text-amber-200">${escapeHtml(t('mediaRouteGuidanceTitle'))}</div>
                <div class="mt-1 space-y-1">
                    ${guidanceKeys.map(key => `<div class="text-xs text-amber-100">${escapeHtml(t(key))}</div>`).join('')}
                    ${actionKeys.map(key => `<div class="text-xs text-dark-300">${escapeHtml(t(key))}</div>`).join('')}
                </div>
            </div>
        ` : ''}
        <div class="space-y-2 mt-3">
            ${checks.map(renderMediaRouteCheckItem).join('') || renderEmptyState(t('noMediaRouteChecks'))}
        </div>
        <details class="mt-3">
            <summary class="text-xs text-dark-400 cursor-pointer">${escapeHtml(t('rawMediaReadiness'))}</summary>
            <pre class="preview-code mt-2">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
        </details>
    `;
}

function renderMediaRouteCheckItem(item) {
    const ready = Boolean(item && item.can_forward);
    const proxyPaths = Array.isArray(item.proxy_paths) ? item.proxy_paths : [];
    const title = `${providerMediaKindLabel(item.media_kind)} ${item.operation || t('routeFilter')}`;
    const guidance = item.guidance_key ? t(item.guidance_key) : '';
    const action = item.action_key ? t(item.action_key) : '';
    const message = guidance || (ready ? t('routeCanForward') : (item.message || t('routeBlocked')));
    return `
        <div class="rounded-lg border ${ready ? 'border-emerald-800/60 bg-emerald-950/10' : 'border-amber-800/60 bg-amber-950/10'} p-3">
            <div class="flex items-center justify-between gap-2">
                <div class="text-sm font-medium text-dark-100">${escapeHtml(title)}</div>
                <span class="text-xs ${ready ? 'text-emerald-300' : 'text-amber-300'}">${ready ? escapeHtml(t('routeCanForward')) : escapeHtml(item.error_type ? providerProblemLabel(item.error_type) : t('routeBlocked'))}</span>
            </div>
            <div class="font-mono text-xs text-dark-300 mt-2 break-all">${escapeHtml(t('proxyPathLabel'))}: ${escapeHtml(proxyPaths.join(' | ') || item.canonical_path || '-')}</div>
            <div class="font-mono text-xs text-dark-300 mt-1 break-all">${escapeHtml(t('upstreamPathLabel'))}: ${escapeHtml(item.upstream_url || '-')}</div>
            <div class="text-xs ${ready ? 'text-emerald-300' : 'text-amber-200'} mt-2">${escapeHtml(message)}</div>
            ${action ? `<div class="text-xs text-dark-300 mt-1">${escapeHtml(action)}</div>` : ''}
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(t('modeValue', { mode: item.route_mode || t('statusUnknown') }))}</div>
        </div>
    `;
}

function renderMediaAdapterPreviewItem(item) {
    const endpoint = item && item.endpoint && typeof item.endpoint === 'object' ? item.endpoint : {};
    const blockers = Array.isArray(item.blockers) ? item.blockers : [];
    const docs = Array.isArray(item.docs_urls) ? item.docs_urls : [];
    const endpointText = endpoint.method && endpoint.path ? `${endpoint.method} ${endpoint.path}` : t('noStatus');
    const title = `${providerMediaKindLabel(item.media_kind)} ${item.operation || t('previewDraft')}`;
    return `
        <div class="rounded-lg border border-dark-800 bg-dark-950/50 p-3">
            <div class="flex items-center justify-between gap-2">
                <div class="text-sm font-medium text-dark-100">${escapeHtml(title)}</div>
                <span class="text-xs ${item.supported ? 'text-emerald-300' : 'text-amber-300'}">${escapeHtml(item.supported ? t('supportedShape') : t('blockedLabel'))}</span>
            </div>
            <div class="font-mono text-xs text-dark-300 mt-1 break-all">${escapeHtml(endpointText)}</div>
            <div class="text-xs text-dark-400 mt-2">${escapeHtml(item.summary || '')}</div>
            ${blockers.length ? `<div class="mt-2 space-y-1">${blockers.map(blocker => `<div class="text-xs text-amber-200">${escapeHtml(t('blockedReason', { value: blocker }))}</div>`).join('')}</div>` : ''}
            ${docs.length ? `<div class="mt-2 text-xs text-dark-500 break-all">${escapeHtml(t('docsLabel'))}: ${docs.map(url => `<span>${escapeHtml(url)}</span>`).join(' | ')}</div>` : ''}
        </div>
    `;
}

function providerMediaKindLabel(kind) {
    if (kind === 'image') return t('imageLabel');
    if (kind === 'video') return t('videoLabel');
    return t('mediaFilter');
}

function providerProblemLabel(errorType) {
    if (errorType === 'media_base_url_missing') return t('mediaBaseUrlMissing');
    if (errorType === 'media_capability_unsupported') return t('mediaCapabilityUnsupported');
    if (errorType === 'media_adapter_required') return t('mediaAdapterRequired');
    if (errorType === 'upstream_error') return t('upstreamError');
    if (errorType === 'network_error') return t('networkError');
    return errorType || t('requestIssue');
}

/**
 * 渲染右侧 UMC Catalog 预览面板。
 * catalog-entry 携带 .stagger-item，列表加载时依次滑入。
 */
// Render a readable quota snapshot while keeping the raw redacted payload nearby.
function renderProviderQuotaResult(result) {
    if (!result) return '';
    const success = Boolean(result.success);
    const enabled = result.enabled !== false;
    const tone = success ? 'emerald' : (enabled ? 'amber' : 'dark');
    const label = success ? t('quotaSnapshotOk') : (enabled ? t('quotaProbeIssue') : t('disabled'));
    const cacheTone = result.cache_expired ? 'amber' : (result.cache_hit ? 'accent' : 'emerald');
    const cacheLabel = result.cache_hit ? t('cacheHit') : t('freshProbe');
    const status = result.status_code ? `HTTP ${result.status_code}` : t('noStatus');
    const fetchedAt = result.fetched_at || result.cache_created_at;
    const values = quotaValues(result);
    const message = result.error || result.note || (success ? t('quotaSnapshotOk') : t('noDetailsReturned'));
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('quota', label, tone)}
            ${renderStatusPill('status', status, result.status_code ? 'dark' : tone)}
            ${renderStatusPill('cache', result.cache_expired ? t('cacheExpired') : cacheLabel, cacheTone)}
            ${Number.isFinite(Number(result.cache_ttl_remaining_seconds)) ? renderStatusPill('ttl', formatDuration(result.cache_ttl_remaining_seconds), result.cache_expired ? 'amber' : 'dark') : ''}
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
            ${renderQuotaMetaTile(t('fetchedLabel'), formatQuotaDate(fetchedAt))}
            ${renderQuotaMetaTile(t('expiresLabel'), formatQuotaDate(result.cache_expires_at))}
        </div>
        <div class="text-xs ${success ? 'text-emerald-300' : 'text-amber-200'} mt-3">${escapeHtml(message)}</div>
        ${values.length ? renderQuotaValueTable(values) : renderEmptyState(t('noQuotaValues'))}
        <details class="mt-3">
            <summary class="text-xs text-dark-400 cursor-pointer">${escapeHtml(t('rawRedactedJson'))}</summary>
            <pre class="preview-code mt-2">${escapeHtml(JSON.stringify(result || {}, null, 2))}</pre>
        </details>
    `;
}

function renderQuotaMetaTile(label, value) {
    return `
        <div class="rounded-lg border border-dark-800 bg-dark-950/45 px-3 py-2">
            <div class="text-[11px] uppercase tracking-wide text-dark-500">${escapeHtml(label)}</div>
            <div class="text-xs text-dark-200 mt-1">${escapeHtml(value || '-')}</div>
        </div>
    `;
}

function renderQuotaValueTable(values) {
    return `
        <div class="mt-3 rounded-lg border border-dark-800 bg-dark-950/45 overflow-hidden">
            ${values.map(([key, value]) => `
                <div class="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-3 px-3 py-2 border-b border-dark-800 last:border-b-0">
                    <div class="text-xs text-dark-400 break-all">${escapeHtml(key)}</div>
                    <div class="text-xs font-mono text-dark-100 break-all">${escapeHtml(formatQuotaValue(value))}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function quotaValues(result) {
    const raw = result && result.values && typeof result.values === 'object' && !Array.isArray(result.values)
        ? result.values
        : {};
    return Object.entries(raw).filter(([, value]) => value !== undefined && value !== null && value !== '');
}

function renderCatalogPreviewPanel() {
    /**
     * 渲染右侧 UMC Catalog 预览面板。
     * catalog-entry 携带 .stagger-item，列表加载时依次滑入。
     */
    const preview = providerState.catalogPreview || { entries: [], route_explanation: [] };
    const entries = preview.entries || [];
    const filteredEntries = getFilteredCatalogEntries(entries);
    const previewMode = preview.preview ? t('draftFormPreview') : '';
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('unifiedCatalogPreview'))}</h3>
                <button onclick="previewWithSelectedFocus()" class="btn btn-secondary text-xs">${escapeHtml(t('focusSelected'))}</button>
            </div>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(previewMode)}${escapeHtml(t('previewOnlyNoCatalogWritten'))}</div>
            ${renderCatalogFilters(entries, filteredEntries)}
            <div class="space-y-2 mt-3 max-h-[360px] overflow-y-auto">
                ${filteredEntries.map(renderCatalogEntry).join('') || renderEmptyState(t('noCatalogEntries'))}
            </div>
            <div class="mt-3">
                <div class="text-xs text-dark-400 mb-1">${escapeHtml(t('routeExplanation'))}</div>
                <pre class="preview-code">${escapeHtml((preview.route_explanation || []).join('\n') || t('noRoutesYet'))}</pre>
            </div>
        </div>
    `;
}

/**
 * Render UMC preview filters. Filters affect only the local preview panel.
 */
function renderCatalogFilters(entries, filteredEntries) {
    const filters = getCatalogFilters();
    const providerOptions = uniqueCatalogOptions(entries, entry => entry.provider_id, entry => {
        const alias = entry.provider_alias || entry.provider_id || '';
        const name = entry.provider_display_name || entry.provider_id || alias;
        return alias && name && alias !== name ? `${alias} / ${name}` : (name || alias);
    });
    const currencyOptions = uniqueCatalogOptions(entries, entry => entry.native_currency, entry => entry.native_currency);
    const capabilityOptions = ['text', 'vision', 'custom_tools', 'reasoning', 'images']
        .filter(capability => entries.some(entry => catalogHasCapability(entry, capability)))
        .map(capability => ({ value: capability, label: capability }));
    const countLabel = t('visibleCount', { shown: filteredEntries.length, total: entries.length });

    return `
        <div class="mt-3 rounded-lg border border-dark-700/70 bg-dark-950/40 p-3">
            <div class="flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-dark-300">${escapeHtml(t('filtersLabel'))}</span>
                <span class="text-xs text-dark-500">${escapeHtml(countLabel)}</span>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                <select class="input text-xs py-2" onchange="updateCatalogFilter('provider', this.value)">
                    <option value="">${escapeHtml(t('allProviders'))}</option>
                    ${providerOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.provider === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <select class="input text-xs py-2" onchange="updateCatalogFilter('capability', this.value)">
                    <option value="">${escapeHtml(t('anyCapability'))}</option>
                    ${capabilityOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.capability === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <input class="input text-xs py-2" type="number" min="0" step="1000" value="${escapeAttr(filters.minContext)}"
                    onchange="updateCatalogFilter('minContext', this.value)" placeholder="${escapeAttr(t('minContext'))}">
                <input class="input text-xs py-2" type="number" min="0" step="0.0001" value="${escapeAttr(filters.maxInputPrice)}"
                    onchange="updateCatalogFilter('maxInputPrice', this.value)" placeholder="${escapeAttr(t('maxInputPrice'))}">
                <select class="input text-xs py-2" onchange="updateCatalogFilter('currency', this.value)">
                    <option value="">${escapeHtml(t('anyCurrency'))}</option>
                    ${currencyOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.currency === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <button onclick="resetCatalogFilters()" class="btn btn-secondary text-xs">${escapeHtml(t('resetFilters'))}</button>
            </div>
        </div>
    `;
}

function getCatalogFilters() {
    return {
        provider: '',
        capability: '',
        minContext: '',
        maxInputPrice: '',
        currency: '',
        ...(providerState.catalogFilters || {}),
    };
}

function getFilteredCatalogEntries(entries) {
    const filters = getCatalogFilters();
    const minContext = parseCatalogNumber(filters.minContext);
    const maxInputPrice = parseCatalogNumber(filters.maxInputPrice);
    const currency = String(filters.currency || '').trim().toUpperCase();
    return (entries || []).filter(entry => {
        if (filters.provider && entry.provider_id !== filters.provider) return false;
        if (filters.capability && !catalogHasCapability(entry, filters.capability)) return false;
        if (minContext !== null && Number(entry.context_window || 0) < minContext) return false;
        if (currency && String(entry.native_currency || '').toUpperCase() !== currency) return false;
        if (maxInputPrice !== null) {
            const inputPrice = catalogInputPrice(entry);
            if (inputPrice === null || inputPrice > maxInputPrice) return false;
        }
        return true;
    });
}

function updateCatalogFilter(key, value) {
    providerState.catalogFilters = {
        ...getCatalogFilters(),
        [key]: value,
    };
    refreshProviderSidePanel();
}

function resetCatalogFilters() {
    providerState.catalogFilters = {
        provider: '',
        capability: '',
        minContext: '',
        maxInputPrice: '',
        currency: '',
    };
    refreshProviderSidePanel();
}

function uniqueCatalogOptions(entries, valueFn, labelFn) {
    const seen = new Set();
    return (entries || []).reduce((options, entry) => {
        const value = String(valueFn(entry) || '').trim();
        if (!value || seen.has(value)) return options;
        seen.add(value);
        options.push({ value, label: String(labelFn(entry) || value) });
        return options;
    }, []);
}

function catalogHasCapability(entry, capability) {
    const capabilities = entry && entry.capabilities ? entry.capabilities : {};
    return Boolean(capabilities[capability]);
}

function catalogPriceValue(pricing, aliases) {
    const source = pricing && typeof pricing === 'object' ? pricing : {};
    for (const key of aliases) {
        const value = parseCatalogNumber(source[key]);
        if (value !== null) return value;
    }
    return null;
}

function catalogInputPrice(entry) {
    const pricing = entry && entry.pricing && typeof entry.pricing === 'object' ? entry.pricing : {};
    return catalogPriceValue(pricing, [
        'input_per_million',
        'input_tokens_per_million',
        'prompt_per_million',
        'input_per_1m',
        'input_price_per_million',
        'prompt',
        'input',
    ]);
}

function catalogMediaPriceBadges(entry) {
    const pricing = entry && entry.pricing && typeof entry.pricing === 'object' ? entry.pricing : {};
    const mediaPrices = [
        [t('mediaPriceImage'), ['per_image', 'image', 'image_per_unit', 'image_per_generation']],
        [t('mediaPriceMinimum'), ['request_minimum', 'minimum', 'min_charge']],
    ];
    return mediaPrices
        .map(([label, aliases]) => {
            const value = catalogPriceValue(pricing, aliases);
            return value !== null ? renderMiniBadge(`${label} ${formatCatalogPrice(value)}`) : '';
        })
        .filter(Boolean);
}

function parseCatalogNumber(value) {
    if (value === undefined || value === null || value === '') return null;
    const parsed = Number(String(value).replace(/,/g, '').trim());
    return Number.isFinite(parsed) ? parsed : null;
}

function formatCatalogPrice(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '-';
    return Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

/**
 * Render one UMC catalog entry with model metadata badges.
 */
function renderCatalogEntry(entry) {
    /**
     * UMC Catalog 条目。
     * hover 时 translateY(-2px) scale(1.01)，在密集列表中制造轻微“浮起”
     * 以便用户聚焦当前行。
     */
    const inputPrice = catalogInputPrice(entry);
    const providerLabel = entry.provider_display_name || entry.provider_id || 'provider';
    const badges = [
        renderMiniBadge(entry.provider_alias || entry.provider_id),
        entry.focused ? renderMiniBadge(t('focusedBadge')) : '',
        entry.catalog_visibility ? renderMiniBadge(entry.catalog_visibility) : '',
        entry.catalog_collision ? renderMiniBadge(t('collisionResolved')) : '',
        entry.api_format ? renderMiniBadge(providerOptionLabel(entry.api_format)) : '',
        entry.api_format_source === 'model' ? renderMiniBadge(t('modelInterfaceOverride')) : '',
        entry.responses_profile && entry.responses_profile.domestic_responses ? renderMiniBadge(t('domesticResponsesBadge')) : '',
        entry.context_window ? renderMiniBadge(t('contextShort', { value: formatNumber(entry.context_window, { compact: false }) })) : '',
        inputPrice !== null ? renderMiniBadge(t('inputPriceBadge', { value: formatCatalogPrice(inputPrice) })) : '',
        ...catalogMediaPriceBadges(entry),
        ...capabilityBadges(entry.capabilities),
    ].filter(Boolean).join('');
    return `
        <div class="catalog-entry stagger-item">
            <div class="flex items-center justify-between gap-2">
                <div class="font-mono text-sm text-white truncate">${escapeHtml(entry.codex_model_id)}</div>
                <span class="badge">${escapeHtml(entry.native_currency || '-')}</span>
            </div>
            <div class="text-xs text-dark-400 truncate">${escapeHtml(providerLabel)} -> ${escapeHtml(entry.upstream_model_id)}${entry.original_codex_model_id ? ' · from ' + escapeHtml(entry.original_codex_model_id) : ''}</div>
            <div class="flex flex-wrap gap-1 mt-2">
                ${badges}
            </div>
        </div>
    `;
}

function renderRouteSimulatorShell() {
    /**
     * Render the real read-only route simulator backed by /api/model-rotation/simulate.
     */
    const preview = providerState.catalogPreview || { entries: [] };
    const datalistEntries = Array.from(new Set((preview.entries || []).flatMap(entry => [
        entry.codex_model_id,
        entry.upstream_model_id,
    ]).filter(Boolean))).slice(0, 80);
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('routeSimulator'))}</h3>
                <span class="text-xs text-emerald-300">${escapeHtml(t('readOnlyLabel'))}</span>
            </div>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(t('routeSimulatorDescFull'))}</div>
            <div class="grid grid-cols-2 gap-2 mt-3">
                ${renderRouteCapabilityToggle('route-sim-cap-text', 'text', true)}
                ${renderRouteCapabilityToggle('route-sim-cap-vision', 'vision', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-custom-tools', 'custom_tools', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-reasoning', 'reasoning', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-images', 'images', false)}
            </div>
            <div class="grid grid-cols-1 gap-3 mt-3">
                <input id="route-sim-model" class="input" list="route-sim-model-options" placeholder="${escapeAttr(t('optionalModelPlaceholder'))}">
                <datalist id="route-sim-model-options">
                    ${datalistEntries.map(value => `<option value="${escapeAttr(value)}"></option>`).join('')}
                </datalist>
                <input id="route-sim-context" type="number" min="0" step="1000" class="input" placeholder="${escapeAttr(t('requiredContextPlaceholder'))}">
            </div>
            <button onclick="runRouteSimulatorShell()" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('simulateRoute'))}</button>
            <div id="route-sim-result" class="preview-code mt-3">${escapeHtml(t('noSimulationYet'))}</div>
        </div>
    `;
}

function renderRouteCapabilityToggle(id, capability, checked) {
    return `
        <label class="flex items-center gap-2 text-xs cursor-pointer bg-dark-900/60 border border-dark-700 rounded-md px-2 py-2">
            <input id="${escapeAttr(id)}" data-route-capability="${escapeAttr(capability)}" type="checkbox"
                class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${checked ? 'checked' : ''}>
            <span>${escapeHtml(capability)}</span>
        </label>
    `;
}

function renderCodexDiffPreviewShell() {
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('codexDiffPreview'))}</h3>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(t('codexDiffShellDesc'))}</div>
            <pre class="preview-code mt-3">${escapeHtml(t('noPendingCodexChanges'))}</pre>
        </div>
    `;
}

/**
 * Preset 卡片（Quick Setup 用）。hover 时 translateY(-3px) + glow，
 * 与 card 组件保持一致的视觉语言。
 */
function renderPresetCard(preset) {
    return `
        <div class="preset-card stagger-item">
            <div>
                <div class="font-medium text-white">${escapeHtml(preset.name)}</div>
                <div class="text-xs text-dark-400 mt-1">${escapeHtml(preset.description || '')}</div>
            </div>
            <button onclick="importPreset('${escapeAttr(preset.preset_id)}')" class="btn btn-secondary text-xs">${escapeHtml(t('importAction'))}</button>
        </div>
    `;
}

/**
 * Providers 页右侧的紧凑型 preset 按钮。
 * hover 时向右微移 + 边框高亮，节省纵向空间的同时保留交互反馈。
 */
function renderCompactPresetButton(preset) {
    return `
        <button onclick="importPreset('${escapeAttr(preset.preset_id)}')" class="compact-preset-btn stagger-item">
            <span>${escapeHtml(preset.name)}</span>
            <span class="text-dark-500">${escapeHtml(preset.category || '')}</span>
        </button>
    `;
}

async function importPreset(presetId) {
    try {
        const result = await api('/api/providers/import-preset', {
            method: 'POST',
            body: JSON.stringify({ preset_id: presetId }),
        });
        providerState.selectedProviderId = result.provider.id;
        providerState.mediaRoutePreview = null;
        providerState.healthPreview = null;
        providerState.modelFetchPreview = null;
        await ensureProviderData();
        await refreshCatalogPreview();
        showToast(t('providerPresetImported'), 'success');
        if (currentPage === 'quick-setup') {
            quickSetupStep = Math.max(quickSetupStep, 1);
            renderQuickSetup();
        }
        if (currentPage === 'providers') renderProvidersPage();
    } catch (err) {
        showToast(t('providerPresetImportFailed') + err.message, 'error');
    }
}

function showCreateProviderModal() {
    const modal = document.getElementById('create-provider-modal');
    if (!modal) return;
    const presets = (providerState.presets || []).filter(p => !p.hidden);
    const generic = presets.filter(p => p.category !== 'domestic');
    const domestic = presets.filter(p => p.category === 'domestic');
    document.getElementById('create-provider-modal-presets').innerHTML = `
        <div class="space-y-4">
            ${generic.length ? `
                <div>
                    <h4 class="text-sm font-semibold text-dark-200 mb-2">${escapeHtml(t('genericPresets'))}</h4>
                    <div class="grid grid-cols-1 gap-2">${generic.map(renderPresetCardCompact).join('')}</div>
                </div>
            ` : ''}
            ${domestic.length ? `
                <div>
                    <h4 class="text-sm font-semibold text-dark-200 mb-2">${escapeHtml(t('domesticPresets'))}</h4>
                    <div class="grid grid-cols-1 gap-2">${domestic.map(renderPresetCardCompact).join('')}</div>
                </div>
            ` : ''}
        </div>
    `;
    modal.classList.remove('hidden');
}

function hideCreateProviderModal() {
    const modal = document.getElementById('create-provider-modal');
    if (modal) modal.classList.add('hidden');
}

function renderPresetCardCompact(preset) {
    return `
        <button onclick="importPreset('${escapeAttr(preset.preset_id)}'); hideCreateProviderModal();" class="preset-card text-left w-full">
            <div class="font-medium text-white">${escapeHtml(preset.name)}</div>
            <div class="text-xs text-dark-400 mt-1">${escapeHtml(preset.description || '')}</div>
        </button>
    `;
}

async function createBlankProvider() {
    try {
        const result = await api('/api/providers', {
            method: 'POST',
            body: JSON.stringify({
                display_name: t('newProviderDefaultName'),
                short_alias: 'new',
                base_url: '',
                api_format: 'openai_responses',
                native_currency: 'USD',
                catalog_visibility: 'focused_only',
                models: [{ id: 'model-id', display_name: 'Model', selected: true, context_window: 128000 }],
            }),
        });
        providerState.selectedProviderId = result.provider.id;
        providerState.mediaRoutePreview = null;
        providerState.mediaAdapterPreview = null;
        providerState.healthPreview = null;
        providerState.modelFetchPreview = null;
        await ensureProviderData();
        await refreshCatalogPreview();
        if (currentPage === 'quick-setup') {
            quickSetupStep = Math.max(quickSetupStep, 1);
            renderQuickSetup();
        } else {
            renderProvidersPage();
        }
        showToast(t('providerCreated'), 'success');
    } catch (err) {
        showToast(t('providerCreateFailed') + err.message, 'error');
    }
}

async function selectProvider(providerId) {
    providerState.selectedProviderId = providerId;
    providerState.responsesProbePreview = null;
    providerState.mediaRoutePreview = null;
    providerState.mediaAdapterPreview = null;
    providerState.quotaPreview = null;
    providerState.healthPreview = null;
    providerState.requestPreview = null;
    providerState.modelFetchPreview = null;
    renderProvidersPage();
    await refreshSelectedQuotaCache();
    renderProvidersPage();
}

function addProviderModelDetail() {
    const provider = getSelectedProvider();
    if (!provider || isCodexLoginProvider(provider)) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    draft.models = Array.isArray(draft.models) ? draft.models : [];
    draft.models.push({
        id: 'model-id',
        display_name: 'Model',
        codex_visible_id: '',
        selected: true,
        catalog_hidden: false,
        primary: false,
        enabled: true,
        context_window: 128000,
        api_format: '',
        capabilities: { text: true },
        capability_overrides: { text: true },
    });
    providerState.providers = (providerState.providers || []).map(item => (
        item.id === provider.id ? { ...provider, ...draft } : item
    ));
    renderProvidersPage();
}

function removeProviderModelDetail(button) {
    const row = button && button.closest ? button.closest('[data-provider-model-row]') : null;
    if (!row) return;
    row.remove();
}

async function saveSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    if (isCodexLoginProvider(provider)) {
        showProviderFormError(t('codexLoginProviderReadOnly'));
        return;
    }
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id), {
            method: 'PUT',
            body: JSON.stringify(draft),
        });
        providerState.selectedProviderId = result.provider.id;
        providerState.mediaRoutePreview = null;
        providerState.mediaAdapterPreview = null;
        providerState.quotaPreview = null;
        providerState.healthPreview = null;
        providerState.requestPreview = null;
        providerState.modelFetchPreview = null;
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast(t('providerSaved'), 'success');
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function testSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        const result = await api('/api/providers/test', {
            method: 'POST',
            body: JSON.stringify(draft),
        });
        if (result.success) {
            showToast(result.message || t('providerTestPassed'), 'success');
        } else {
            showToast((result.errors || []).join('; ') || t('providerTestFailed'), 'error');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function checkProviderHealth() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.healthPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/health-check-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        if (providerState.healthPreview && providerState.healthPreview.success) {
            showToast(t('providerHealthReachable'), 'success');
        } else {
            showToast(t('providerHealthIssue'), 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function previewProviderRequest() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.requestPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/request-preview-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        if (providerState.requestPreview && providerState.requestPreview.success) {
            showToast(t('requestPreviewGenerated'), 'success');
        } else {
            showToast(t('requestPreviewIssue'), 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function fetchProviderModels() {
    const provider = getSelectedProvider();
    if (!provider || isCodexLoginProvider(provider)) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    const buttons = Array.from(document.querySelectorAll('button')).filter(button => button.getAttribute('onclick') === 'fetchProviderModels()');
    buttons.forEach(button => { button.disabled = true; });
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id) + '/models/fetch-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        providerState.modelFetchPreview = result;
        if (result && result.success && Array.isArray(result.merged_models)) {
            providerState.providers = (providerState.providers || []).map(item => (
                item.id === provider.id ? { ...provider, ...draft, models: result.merged_models } : item
            ));
        }
        renderProvidersPage();
        if (result && result.success) {
            showToast(t('modelFetchMerged'), 'success');
        } else {
            showToast(t('modelFetchFailed'), 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
        buttons.forEach(button => { button.disabled = false; });
    }
}

function previewProviderDraft() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    const panel = document.getElementById('provider-draft-preview');
    if (!panel) return;
    const pre = panel.querySelector('pre');
    panel.classList.remove('hidden');
    pre.textContent = JSON.stringify(redactDraft(draft), null, 2);
}

async function previewResponsesProbe() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.responsesProbePreview = await api('/api/providers/responses-profile/probe-preview', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        showToast(t('responsesProbeGenerated'), 'success');
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function previewMediaRoutes() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.mediaRoutePreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/media-route/preview-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        if (providerState.mediaRoutePreview && providerState.mediaRoutePreview.live_forwarding_enabled) {
            showToast(t('mediaRouteReadyToast'), 'success');
        } else {
            showToast(t('mediaRouteBlockersToast'), 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function previewMediaAdapter() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.mediaAdapterPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/media-adapter/preview-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        showToast(t('mediaAdapterGenerated'), 'success');
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function refreshProviderQuota() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        providerState.quotaPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/quota/refresh-draft', {
            method: 'POST',
            body: JSON.stringify({ provider: draft }),
        });
        refreshProviderPreviewPanels();
        if (providerState.quotaPreview && providerState.quotaPreview.success) {
            showToast(t('quotaRefreshed'), 'success');
        } else {
            showToast(t('quotaRefreshIssue'), 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function deleteSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    if (isCodexLoginProvider(provider)) {
        showToast(t('codexLoginProviderReadOnly'), 'warning');
        return;
    }
    if (!confirm(t('deleteProviderConfirm', { name: provider.display_name }))) return;
    try {
        await api('/api/providers/' + encodeURIComponent(provider.id), { method: 'DELETE' });
        providerState.selectedProviderId = '';
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast(t('providerDeleted'), 'success');
    } catch (err) {
        showToast(t('providerDeleteFailed') + err.message, 'error');
    }
}

async function switchSelectedProvider(providerId = '') {
    const provider = providerId
        ? (providerState.providers || []).find(item => item.id === providerId)
        : getSelectedProvider();
    const targetProviderId = provider ? provider.id : String(providerId || '').trim();
    if (!targetProviderId) return;
    try {
        const result = await api('/api/providers/focus', {
            method: 'POST',
            body: JSON.stringify({ provider_id: targetProviderId }),
        });
        const focusedProviderId = result.focus_provider_id || targetProviderId;
        providerState.focus_provider_id = focusedProviderId;
        await ensureProviderData();
        await refreshCatalogPreview(providerState.focus_provider_id || focusedProviderId);
        await refreshProxyStatus();
        renderProvidersPage();
        const proxy = result.proxy || {};
        if (proxy.success && proxy.status && proxy.status.base_url) {
            showToast(t('providerSwitchUpdatedWithProxy', { url: proxy.status.base_url }), 'success');
        } else if (result.switch_only) {
            showToast(t('officialProviderSwitchOnlyToast'), 'success');
        } else {
            showToast(t('providerSwitchUpdated'), 'success');
        }
    } catch (err) {
        showToast(t('providerSwitchFailed') + err.message, 'error');
    }
}

async function previewWithSelectedFocus() {
    const provider = getSelectedProvider();
    if (provider) {
        await refreshCatalogPreviewDraft(provider);
    } else {
        await refreshCatalogPreview(providerState.selectedProviderId || '');
    }
    refreshProviderSidePanel();
}

async function runRouteSimulatorShell() {
    /**
     * 执行路由模拟请求，将结果显示在 route-sim-result 面板。
     *
     * 设计意图：
     *   - 纯只读操作：只调用模拟接口，不修改任何 registry 或 Codex 配置。
     *   - 错误降级：若后端返回错误，将错误消息直接显示在结果面板，
     *     而非弹 Toast，避免打断用户操作流。
     */
    let capabilities = Array.from(document.querySelectorAll('[data-route-capability]:checked'))
        .map(el => el.getAttribute('data-route-capability'))
        .filter(Boolean);
    if (!capabilities.length) capabilities = ['text'];
    const model = document.getElementById('route-sim-model')?.value || '';
    const requiredContext = parseInt(document.getElementById('route-sim-context')?.value, 10) || 0;
    const result = document.getElementById('route-sim-result');
    if (!result) return;
    try {
        const decision = await api('/api/model-rotation/simulate', {
            method: 'POST',
            body: JSON.stringify({ capabilities, model, required_context: requiredContext }),
        });
        result.innerHTML = renderRouteSimulationResult(decision);
    } catch (err) {
        result.textContent = t('simulationFailed') + err.message;
    }
}

function renderRouteSimulationResult(decision) {
    const statusClass = decision.success ? 'text-emerald-300' : 'text-red-300';
    const title = decision.success
        ? `${decision.provider_id || ''}/${decision.model_id || ''}`
        : (decision.error || t('noRouteAvailable'));
    const candidateRows = (decision.candidate_status || []).slice(0, 8).map(row => {
        const missingCapabilities = row.missing_capabilities || [];
        const flags = [
            row.capability_match ? t('capsOk') : t('missingCapability', { value: missingCapabilities.join(', ') || t('capabilitiesLabel') }),
            row.context_match ? t('contextOk') : t('contextTooSmall'),
            row.model_match ? t('modelOk') : t('modelFiltered'),
        ];
        return `
            <div class="grid grid-cols-[1fr_auto] gap-2 py-2 border-b border-dark-800 last:border-b-0">
                <div class="min-w-0">
                    <div class="font-mono text-xs text-dark-100 break-all">${escapeHtml(row.codex_model_id || row.candidate_id)}</div>
                    <div class="text-xs text-dark-500">p${escapeHtml(row.priority)} · ctx ${formatCount(row.context_window || 0)} · ${escapeHtml(flags.join(' · '))}</div>
                </div>
                <span class="text-xs ${row.available ? 'text-emerald-300' : 'text-amber-300'}">${escapeHtml(row.available ? t('availableLabel') : t('blockedLabel'))}</span>
            </div>
        `;
    }).join('');
    return `
        <div class="space-y-3">
            <div>
                <div class="text-xs ${statusClass}">${escapeHtml(decision.success ? t('routeSelected') : t('routeUnavailable'))}</div>
                <div class="font-mono text-sm text-dark-100 break-all">${escapeHtml(title)}</div>
                <div class="text-xs text-dark-500 mt-1">
                    ${escapeHtml(t('capabilitiesLabel'))}: ${escapeHtml((decision.required_capabilities || []).join(', ') || 'text')}
                    · ${escapeHtml(t('contextLabel'))}: ${escapeHtml(decision.required_context || 0)}
                    · ${escapeHtml(t('candidatesLabel'))}: ${escapeHtml(decision.candidate_count || 0)}
                </div>
            </div>
            <div class="rounded-md border border-dark-800 bg-dark-950/50 px-3 py-2">
                ${(decision.explanation || []).map(line => `<div class="text-xs text-dark-300 py-0.5">${escapeHtml(line)}</div>`).join('') || `<div class="text-xs text-dark-500">${escapeHtml(t('noExplanationReturned'))}</div>`}
            </div>
            <div class="rounded-md border border-dark-800 bg-dark-950/40 px-3 py-1">
                ${candidateRows || `<div class="text-xs text-dark-500 py-2">${escapeHtml(t('noCandidateStatusReturned'))}</div>`}
            </div>
        </div>
    `;
}

async function exportProviderBundle() {
    try {
        const bundle = await api('/api/providers/export');
        const preview = JSON.stringify(bundle, null, 2);
        await navigator.clipboard.writeText(preview);
        showToast(t('redactedProviderBundleCopied'), 'success');
    } catch (err) {
        showToast(t('exportFailed') + err.message, 'error');
    }
}

function getSelectedProvider() {
    return (providerState.providers || []).find(p => p.id === providerState.selectedProviderId) || null;
}

function readProviderModelsFromDetails(existingModels = [], provider = {}) {
    const rows = Array.from(document.querySelectorAll('[data-provider-model-row]'));
    if (!rows.length) return null;
    const modelCapabilityLocked = isNativeCapabilityLocked(provider);
    const existingByOriginalId = new Map();
    (existingModels || []).forEach(model => {
        if (!model || !model.id) return;
        existingByOriginalId.set(String(model.id), model);
    });
    return rows.map((row, index) => {
        const modelId = String(row.querySelector('[data-model-field="id"]')?.value || '').trim();
        if (!modelId) return null;
        const originalId = String(row.getAttribute('data-original-model-id') || '').trim();
        const existing = existingByOriginalId.get(originalId) || existingByOriginalId.get(modelId) || {};
        const capabilities = {};
        row.querySelectorAll('[data-model-capability]').forEach(input => {
            capabilities[input.getAttribute('data-model-capability')] = Boolean(input.checked);
        });
        capabilities.tools = true;
        capabilities.streaming = true;
        const nativeApproval = modelCapabilityLocked ? true : Boolean(row.querySelector('[data-model-field="native_approval"]')?.checked);
        const apiFormat = modelCapabilityLocked
            ? String(existing.api_format || '').trim()
            : String(row.querySelector('[data-model-field="api_format"]')?.value || '').trim();
        const displayName = String(row.querySelector('[data-model-field="display_name"]')?.value || '').trim();
        const codexDisplayName = displayName || modelId;
        const codexVisibleId = modelId;
        const hidden = Boolean(row.querySelector('[data-model-field="catalog_hidden"]')?.checked);
        const primary = !hidden && Boolean(row.querySelector('[data-model-field="primary"]')?.checked);
        const selected = !hidden && (Boolean(row.querySelector('[data-model-field="selected"]')?.checked) || primary);
        const reasoningParameter = String(row.querySelector('[data-model-field="reasoning_effort_parameter"]')?.value || 'auto').trim();
        const reasoningEfforts = String(row.querySelector('[data-model-field="reasoning_efforts"]')?.value || '')
            .split(',')
            .map(item => item.trim())
            .filter(Boolean);
        const reasoningDefault = String(row.querySelector('[data-model-field="reasoning_effort_default"]')?.value || '').trim();
        const pricing = {};
        row.querySelectorAll('[data-model-pricing]').forEach(input => {
            const key = input.getAttribute('data-model-pricing');
            const value = parseFloat(input.value);
            if (!isNaN(value) && value >= 0) {
                pricing[key] = value;
            }
        });
        const lockedCapabilities = nativeLockedCapabilities(existing.capabilities || capabilities);
        const capabilityOverrides = modelCapabilityLocked ? { ...(existing.capability_overrides || {}) } : {
            ...(existing.capability_overrides || {}),
            ...capabilities,
            native_approval: nativeApproval,
        };
        delete capabilityOverrides.tools;
        delete capabilityOverrides.streaming;
        return {
            ...existing,
            id: modelId,
            display_name: displayName || modelId,
            codex_display_name: codexDisplayName,
            codex_visible_id: codexVisibleId,
            context_window: parseInt(row.querySelector('[data-model-field="context_window"]')?.value || '0', 10) || 0,
            selected,
            catalog_hidden: hidden,
            primary,
            enabled: Boolean(row.querySelector('[data-model-field="enabled"]')?.checked),
            api_format: PROVIDER_API_FORMATS.has(apiFormat) ? apiFormat : '',
            native_approval: nativeApproval,
            capabilities: modelCapabilityLocked ? lockedCapabilities : capabilities,
            capability_overrides: capabilityOverrides,
            reasoning_effort_profile: modelCapabilityLocked ? (existing.reasoning_effort_profile || {}) : {
                ...(existing.reasoning_effort_profile || {}),
                parameter: reasoningParameter,
                supported_efforts: reasoningEfforts,
                default_effort: reasoningDefault,
            },
            pricing: Object.keys(pricing).length ? pricing : (existing.pricing || {}),
            model_base_url: String(row.querySelector('[data-model-field="model_base_url"]')?.value || '').trim() || (existing.model_base_url || ''),
            model_base_path: String(row.querySelector('[data-model-field="model_base_path"]')?.value || '').trim() || (existing.model_base_path || ''),
            display_order: Number.isFinite(Number(existing.display_order)) ? existing.display_order : index,
        };
    }).filter(Boolean);
}

function readProviderForm(existing) {
    try {
        const headers = JSON.parse(document.getElementById('provider-headers-json')?.value || '{}');
        const imageModelOverrides = JSON.parse(document.getElementById('media-image-overrides-json')?.value || '{}');
        const quotaCheck = JSON.parse(document.getElementById('provider-quota-json')?.value || '{}');
        const quotaScriptCode = String(document.getElementById('provider-quota-script-js')?.value || '').trim();
        if (quotaScriptCode) {
            quotaCheck.enabled = quotaCheck.enabled !== false;
            quotaCheck.type = 'script';
            quotaCheck.script = {
                ...(quotaCheck.script && typeof quotaCheck.script === 'object' ? quotaCheck.script : {}),
                language: 'javascript',
                code: quotaScriptCode,
            };
        }
        const aliases = JSON.parse(document.getElementById('provider-aliases-json')?.value || '{}');
        const aliasPatterns = JSON.parse(document.getElementById('provider-alias-patterns-json')?.value || '[]');
        const models = readProviderModelsFromDetails(existing.models || [], existing)
            || parseModelsText(document.getElementById('provider-models-text')?.value || '', existing.models || []);
        const approvalMode = getSelectedApprovalMode();
        const mediaMode = getSelectedMediaMode();
        const mediaAsyncVisible = !document.getElementById('media-async-fields')?.classList.contains('hidden');
        const responsesMode = getSelectedResponsesMode();
        const apiFormat = document.getElementById('provider-api-format')?.value || 'openai_responses';
        const lockedCapabilities = apiFormat === 'openai_responses' && responsesMode === 'native';
        const nativeBehaviorLocked = isNativeResponsesProvider({ ...existing, api_format: apiFormat, responses_profile: { ...(existing.responses_profile || {}), mode: responsesMode, native_responses: responsesMode === 'native' } });
        const lockedResponsesProfile = {
            ...(existing.responses_profile || {}),
            mode: 'native',
            native_responses: true,
        };
        return {
            ...existing,
            display_name: document.getElementById('provider-display-name')?.value || '',
            codex_visible_alias: document.getElementById('provider-codex-visible-alias')?.value || '',
            short_alias: document.getElementById('provider-short-alias')?.value || '',
            base_url: document.getElementById('provider-base-url')?.value || '',
            api_format: nativeBehaviorLocked ? (existing.api_format || 'openai_responses') : apiFormat,
            country_region: document.getElementById('provider-country-region')?.value || '',
            native_currency: document.getElementById('provider-native-currency')?.value || 'USD',
            catalog_visibility: document.getElementById('provider-visibility')?.value || 'focused_only',
            user_agent: document.getElementById('provider-user-agent')?.value || '',
            enabled: document.getElementById('provider-enabled')?.checked || false,
            fallback_enabled: document.getElementById('provider-fallback-enabled')
                ? document.getElementById('provider-fallback-enabled').checked
                : Boolean(existing.fallback_enabled),
            api_key: document.getElementById('provider-api-key') ? document.getElementById('provider-api-key').value : (existing.api_key || ''),
            auth_mode: nativeBehaviorLocked ? (existing.auth_mode || 'provider_api_key') : (document.getElementById('provider-auth-mode')?.value || 'provider_api_key'),
            headers,
            aliases,
            alias_patterns: aliasPatterns,
            quota_check: quotaCheck,
            models,
            capabilities: lockedCapabilities ? nativeLockedCapabilities(existing.capabilities || {}) : {
                ...existing.capabilities,
                text: document.getElementById('cap-text')?.checked || false,
                vision: document.getElementById('cap-vision')?.checked || false,
                tools: true,
                streaming: true,
                custom_tools: document.getElementById('cap-custom-tools')?.checked || false,
                reasoning: document.getElementById('cap-reasoning')?.checked || false,
                images: document.getElementById('cap-images')?.checked || false,
                videos: false,
            },
            approval_profile: nativeBehaviorLocked ? (existing.approval_profile || {}) : {
                ...(existing.approval_profile || {}),
                mode: approvalMode,
                official_guardian: approvalMode === 'official_guardian',
                proxy_auto_approve: approvalMode === 'proxy_auto_approve',
                reviewer_model: document.getElementById('approval-reviewer-model')?.value || '',
                allowed_actions: String(document.getElementById('approval-allowed-actions')?.value || '')
                    .split(',')
                    .map(item => item.trim())
                    .filter(Boolean),
                on_review_error: document.getElementById('approval-error-policy')?.value || 'decline',
                timeout_ms: parseInt(document.getElementById('approval-timeout-ms')?.value || '90000', 10) || 90000,
                max_retries: parseInt(document.getElementById('approval-max-retries')?.value || '1', 10) || 1,
                audit_decisions: document.getElementById('approval-audit-decisions')?.checked !== false,
                auto_accept_low_risk: document.getElementById('approval-auto-accept-low-risk')?.checked !== false,
                auto_decline_high_risk: document.getElementById('approval-auto-decline-high-risk')?.checked !== false,
            },
            proxy_profile: {
                ...(existing.proxy_profile || {}),
                bypass_system_proxy: document.getElementById('proxy-bypass-system-proxy')?.checked !== false,
                upstream_timeout_seconds: parseInt(document.getElementById('proxy-upstream-timeout')?.value || '0', 10) || 0,
                retry_attempts: parseInt(document.getElementById('proxy-retry-attempts')?.value || '0', 10) || 0,
                retry_backoff_ms: parseInt(document.getElementById('proxy-retry-backoff-ms')?.value || '0', 10) || 0,
            },
            media_profile: nativeBehaviorLocked ? (existing.media_profile || {}) : {
                ...existing.media_profile,
                default_image_provider: document.getElementById('media-default-image')?.checked || false,
                default_video_provider: false,
                openai_compatible_media: mediaMode === 'openai_compatible',
                adapter_required: mediaMode === 'adapter_required',
                async_submit: mediaAsyncVisible && (document.getElementById('media-async-submit')?.checked || false),
                poll_required: mediaAsyncVisible && (document.getElementById('media-poll-required')?.checked || false),
                cancel_supported: mediaAsyncVisible && (document.getElementById('media-cancel-supported')?.checked || false),
                image_model_overrides: imageModelOverrides,
                video_model_overrides: {},
            },
            responses_profile: nativeBehaviorLocked ? lockedResponsesProfile : {
                ...(existing.responses_profile || {}),
                mode: responsesMode,
                native_responses: responsesMode === 'native',
                domestic_responses: document.getElementById('responses-domestic')?.checked || false,
                partial_compatibility: document.getElementById('responses-partial')?.checked || false,
                requires_adapter: document.getElementById('responses-requires-adapter')?.checked || false,
                profile_id: document.getElementById('responses-profile-id')?.value || '',
                verified_docs_url: document.getElementById('responses-docs-url')?.value || '',
                unsupported_fields: String(document.getElementById('responses-unsupported')?.value || '')
                    .split(',')
                    .map(item => item.trim())
                    .filter(Boolean),
            },
        };
    } catch (err) {
        showProviderFormError(t('draftParseFailed') + err.message);
        return null;
    }
}

async function revealProviderSecret(field = 'api_key', inputId = 'provider-api-key') {
    const provider = getSelectedProvider();
    const input = document.getElementById(inputId);
    const button = document.getElementById(inputId + '-reveal-btn');
    if (!provider || !input) return;

    if (input.type === 'text') {
        input.type = 'password';
        if (button) button.textContent = t('revealSecret');
        return;
    }

    async function requestSecret(password = '') {
        const response = await fetch('/api/providers/' + encodeURIComponent(provider.id) + '/secret', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ field, password }),
        });
        const data = await response.json();
        if (!response.ok) {
            const err = new Error(data.error || ('HTTP ' + response.status));
            err.payload = data;
            throw err;
        }
        return data;
    }

    try {
        let data;
        try {
            data = await requestSecret('');
        } catch (err) {
            if (!err.payload || !err.payload.password_required) throw err;
            const password = window.prompt(t('secretRevealPasswordPrompt'));
            if (password === null) return;
            data = await requestSecret(password);
        }
        input.value = data.secret || '';
        input.type = 'text';
        if (button) button.textContent = t('hideSecret');
        showToast(t('secretRevealSuccess'), 'success');
    } catch (err) {
        showToast(t('secretRevealFailed') + err.message, 'error');
    }
}

function parseModelsText(text, existingModels = []) {
    const existingById = new Map();
    (existingModels || []).forEach(model => {
        if (!model || !model.id) return;
        const key = String(model.id).trim();
        if (!key) return;
        const list = existingById.get(key) || [];
        list.push(model);
        existingById.set(key, list);
    });
    return String(text || '').split('\n')
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => {
            const parts = line.split('|').map(part => part.trim());
            const modelId = parts[0] || 'model-id';
            const existingList = existingById.get(modelId) || [];
            const existing = existingList.shift() || {};
            const apiFormat = PROVIDER_API_FORMATS.has(parts[4]) ? parts[4] : (existing.api_format || '');
            const nativeApprovalToken = String(parts[5] || '').trim().toLowerCase();
            const hasNativeApprovalToken = Boolean(nativeApprovalToken);
            const nativeApproval = hasNativeApprovalToken
                ? ['1', 'true', 'yes', 'on', 'native_approval', 'supports_native_approval'].includes(nativeApprovalToken)
                : Boolean(existing.native_approval);
            const selected = parts[3] === 'selected' || parts[3] === 'true';
            const catalogHidden = selected ? false : Boolean(existing.catalog_hidden);
            const capabilityOverrides = {
                ...(existing.capability_overrides || {}),
                ...(hasNativeApprovalToken ? { native_approval: nativeApproval } : {}),
            };
            return {
                ...existing,
                id: parts[0] || 'model-id',
                display_name: parts[1] || parts[0] || 'Model',
                codex_visible_id: existing.codex_visible_id || '',
                context_window: parseInt(parts[2], 10) || 0,
                selected,
                catalog_hidden: catalogHidden,
                primary: Boolean(existing.primary) && !catalogHidden,
                api_format: apiFormat,
                native_approval: nativeApproval,
                capability_overrides: capabilityOverrides,
                enabled: true,
            };
        });
}

function showProviderFormError(message) {
    const el = document.getElementById('provider-form-error');
    if (!el) {
        showToast(message, 'error');
        return;
    }
    el.textContent = message;
    el.classList.remove('hidden');
}

function redactDraft(draft) {
    const clone = JSON.parse(JSON.stringify(draft));
    if (clone.api_key) clone.api_key = '********';
    if (clone.secondary_usage_key) clone.secondary_usage_key = '********';
    if (clone.headers) {
        for (const key of Object.keys(clone.headers)) {
            if (/\b(api[_\-]?key|auth(orization)?|bearer|token|secret|password|x[_\-]?api[_\-]?key)\b/i.test(key)) {
                clone.headers[key] = '********';
            }
        }
    }
    return clone;
}

function providerResponsesMode(provider) {
    const profile = provider && provider.responses_profile ? provider.responses_profile : {};
    if (profile.mode === 'native' || profile.native_responses || provider.native_responses) return 'native';
    return 'compatible';
}

function isNativeResponsesProvider(providerOrApiFormat, profile = null) {
    if (typeof providerOrApiFormat === 'string') {
        return providerOrApiFormat === 'openai_responses' && providerResponsesMode({ responses_profile: profile || {} }) === 'native';
    }
    const provider = providerOrApiFormat || {};
    return provider.api_format === 'openai_responses' && providerResponsesMode(provider) === 'native';
}

function isCodexLoginProvider(provider) {
    return Boolean(provider && (provider.read_only || provider.auth_mode === 'official_oauth' || provider.codex_login));
}

function isNativeCapabilityLocked(provider) {
    return Boolean(provider && (provider.native_capabilities_locked || isNativeResponsesProvider(provider) || isCodexLoginProvider(provider)));
}

function nativeLockedCapabilities(existing = {}) {
    // 原生 provider 的能力按实际值显示，仅强制 native_approval 为 true
    return { ...(existing || {}), native_approval: true };
}

function providerQuotaScriptCode(provider = {}) {
    const quota = provider && provider.quota_check && typeof provider.quota_check === 'object'
        ? provider.quota_check
        : {};
    if (quota.script && typeof quota.script === 'object' && quota.script.code) {
        return String(quota.script.code || '');
    }
    if (quota.code) return String(quota.code || '');
    return '';
}

function renderInput(id, label, value, type = 'text', disabled = false, onchange = '') {
    const disabledAttr = disabled ? ' disabled' : '';
    const changeAttr = onchange && !disabled ? ` onchange="${escapeAttr(onchange)}" oninput="${escapeAttr(onchange)}"` : '';
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <input id="${escapeAttr(id)}" type="${escapeAttr(type)}" class="input mt-1 w-full" value="${escapeAttr(value || '')}"${changeAttr}${disabledAttr}>
        </div>
    `;
}

function renderSecretInput(id, label, value, field = 'api_key', disabled = false) {
    const disabledAttr = disabled ? ' disabled' : '';
    return `
        <div>
            <div class="flex items-center justify-between gap-2">
                <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
                ${disabled ? '' : `<button id="${escapeAttr(id)}-reveal-btn" type="button" onclick="revealProviderSecret('${escapeAttr(field)}', '${escapeAttr(id)}')" class="btn btn-ghost text-xs px-2 py-1">${escapeHtml(t('revealSecret'))}</button>`}
            </div>
            <input id="${escapeAttr(id)}" type="password" class="input mt-1 w-full font-mono" value="${escapeAttr(value || '')}" autocomplete="off"${disabledAttr}>
            <p class="text-xs text-dark-500 mt-1">${escapeHtml(t('secretRevealLocalOnlyHint'))}</p>
        </div>
    `;
}

function renderTextarea(id, label, value, rows = 4, hint = '', disabled = false) {
    const disabledAttr = disabled ? ' disabled' : '';
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <textarea id="${escapeAttr(id)}" class="input mt-1 w-full font-mono" rows="${rows}"${disabledAttr}>${escapeHtml(value || '')}</textarea>
            ${hint ? `<p class="text-xs text-dark-500 mt-1">${escapeHtml(hint)}</p>` : ''}
        </div>
    `;
}

function renderSelect(id, label, value, options, onchange = '', disabled = false) {
    const changeAttr = onchange ? ` onchange="${escapeAttr(onchange)}"` : '';
    const disabledAttr = disabled ? ' disabled' : '';
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <select id="${escapeAttr(id)}" class="input mt-1 w-full"${changeAttr}${disabledAttr}>
                ${options.map(option => {
                    const optionValue = typeof option === 'object' ? option.value : option;
                    const optionLabel = typeof option === 'object' ? option.label : providerOptionLabel(optionValue);
                    return `<option value="${escapeAttr(optionValue)}" ${optionValue === value ? 'selected' : ''}>${escapeHtml(optionLabel)}</option>`;
                }).join('')}
            </select>
        </div>
    `;
}

function providerOptionLabel(value) {
    const labels = {
        hidden: t('catalogVisibilityHidden'),
        focused_only: t('catalogVisibilityFocused'),
        always_visible: t('catalogVisibilityAlways'),
        selected_models: t('catalogVisibilitySelected'),
        openai_responses: t('apiFormatOpenaiResponses'),
        openai_chat: t('apiFormatOpenaiChat'),
        openai_images: t('apiFormatOpenaiImages'),
        openai_videos: t('apiFormatOpenaiVideos'),
        openai_compatible: t('apiFormatOpenaiCompatible'),
        anthropic: t('apiFormatAnthropic'),
        custom: t('apiFormatCustom'),
        provider_api_key: t('authProviderApiKey'),
        global_auth_json: t('authGlobalAuthJson'),
        official_oauth: t('authOfficialOAuth'),
        no_auth: t('authNoAuth'),
        decline: t('reviewDecline'),
        ask_user: t('reviewAskUser'),
        allow: t('reviewAllow'),
        preserve_login_proxy: t('startModePreserveLoginProxy'),
        official_direct: t('startModeOfficialDirect'),
        proxy_injection: t('startModeProxyInjection'),
    };
    return labels[value] || value;
}

function visibleApiFormatOptions(current = '') {
    const options = VISIBLE_PROVIDER_API_FORMATS.slice();
    if (current && PROVIDER_API_FORMATS.has(current) && !options.includes(current)) {
        options.push(current);
    }
    return options;
}

function reasoningEffortParameterLabel(value) {
    const labels = {
        auto: t('reasoningEffortAuto'),
        disabled: t('reasoningEffortDisabled'),
        'reasoning.effort': t('reasoningEffortResponses'),
        reasoning_effort: t('reasoningEffortChat'),
        'output_config.effort': t('reasoningEffortAnthropic'),
        thinking: t('reasoningEffortThinking'),
    };
    return labels[value] || value;
}

function renderCapabilityToggle(id, label, checked, onchange = '', disabled = false) {
    const changeAttr = onchange && !disabled ? ` onchange="${escapeAttr(onchange)}"` : '';
    const disabledAttr = disabled ? ' disabled' : '';
    return `
        <label class="flex items-center gap-2 text-sm ${disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'} bg-dark-900/60 border border-dark-700 rounded-lg px-3 py-2">
            <input id="${escapeAttr(id)}" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${checked ? 'checked' : ''}${changeAttr}${disabledAttr}>
            <span>${escapeHtml(label)}</span>
        </label>
    `;
}

function approvalModeFromProfile(profile) {
    const mode = profile && profile.mode;
    if (mode === 'official_guardian' || mode === 'proxy_auto_approve' || mode === 'manual_only') return mode;
    if (profile && profile.proxy_auto_approve) return 'proxy_auto_approve';
    if (profile && profile.official_guardian) return 'official_guardian';
    return 'proxy_auto_approve';
}

function renderApprovalModeSegment(profile, disabled = false) {
    const mode = approvalModeFromProfile(profile || {});
    const disabledAttr = disabled ? ' disabled' : '';
    const options = [
        ['manual_only', t('manualOnly'), t('userDecides')],
        ['official_guardian', t('officialGuardian'), t('codexNative')],
        ['proxy_auto_approve', t('autoApproval'), t('proxyBroker')],
    ];
    return `
        <div class="approval-mode-field mt-4">
            <div class="text-xs text-dark-400 mb-2">${escapeHtml(t('approvalMode'))}</div>
            <div class="segmented-control" role="radiogroup" aria-label="${escapeAttr(t('approvalMode'))}">
                ${options.map(([value, label, hint]) => `
                    <label class="segmented-option ${mode === value ? 'active' : ''} ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
                        <input
                            type="radio"
                            name="approval-mode"
                            value="${escapeAttr(value)}"
                            onchange="syncApprovalModeControls()"
                            ${mode === value ? 'checked' : ''}
                            ${disabledAttr}
                        >
                        <span class="segmented-label">${escapeHtml(label)}</span>
                        <span class="segmented-hint">${escapeHtml(hint)}</span>
                    </label>
                `).join('')}
            </div>
        </div>
    `;
}

function renderResponsesModeSegment(provider, disabled = false) {
    const mode = providerResponsesMode(provider || {});
    const disabledAttr = disabled ? ' disabled' : '';
    const options = [
        ['compatible', t('responsesCompatibleMode'), t('responsesCompatibleModeDesc')],
        ['native', t('responsesNativeMode'), t('responsesNativeModeDesc')],
    ];
    return `
        <div class="responses-mode-field mt-4">
            <div class="text-xs text-dark-400 mb-2">${escapeHtml(t('responsesMode'))}</div>
            <div class="segmented-control" role="radiogroup" aria-label="${escapeAttr(t('responsesMode'))}">
                ${options.map(([value, label, hint]) => `
                    <label class="segmented-option ${mode === value ? 'active' : ''} ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
                        <input
                            type="radio"
                            name="responses-mode"
                            value="${escapeAttr(value)}"
                            onchange="syncResponsesModeControls(true)"
                            ${mode === value ? 'checked' : ''}
                            ${disabledAttr}
                        >
                        <span class="segmented-label">${escapeHtml(label)}</span>
                        <span class="segmented-hint">${escapeHtml(hint)}</span>
                    </label>
                `).join('')}
            </div>
        </div>
    `;
}

function getSelectedResponsesMode() {
    return document.querySelector('input[name="responses-mode"]:checked')?.value || 'compatible';
}

function syncResponsesModeControls(rerender = false) {
    const selectedMode = getSelectedResponsesMode();
    document.querySelectorAll('.responses-mode-field .segmented-option').forEach(option => {
        const input = option.querySelector('input[name="responses-mode"]');
        option.classList.toggle('active', Boolean(input && input.value === selectedMode));
    });
    if (rerender) {
        const provider = getSelectedProvider();
        if (!provider) return;
        provider.responses_profile = { ...(provider.responses_profile || {}), mode: selectedMode, native_responses: selectedMode === 'native' };
        provider.native_responses = selectedMode === 'native';
        provider.native_capabilities_locked = isNativeCapabilityLocked(provider);
        if (provider.native_capabilities_locked) provider.capabilities = nativeLockedCapabilities(provider.capabilities || {});
        renderProvidersPage();
    }
}

function getSelectedApprovalMode() {
    return document.querySelector('input[name="approval-mode"]:checked')?.value || 'proxy_auto_approve';
}

function syncApprovalModeControls() {
    const selectedMode = getSelectedApprovalMode();
    document.querySelectorAll('.approval-mode-field .segmented-option').forEach(option => {
        const input = option.querySelector('input[name="approval-mode"]');
        option.classList.toggle('active', Boolean(input && input.value === selectedMode));
    });
    const autoFields = document.getElementById('approval-auto-fields');
    if (autoFields) autoFields.classList.toggle('hidden', selectedMode !== 'proxy_auto_approve');
}

function mediaModeFromProfile(profile) {
    if (profile && profile.adapter_required) return 'adapter_required';
    if (profile && profile.openai_compatible_media) return 'openai_compatible';
    return 'disabled';
}

function shouldShowMediaAsyncFields(profile, apiFormat) {
    const mode = mediaModeFromProfile(profile || {});
    return mode === 'adapter_required'
        || Boolean(profile && (profile.async_submit || profile.poll_required || profile.cancel_supported));
}

function providerMediaRoutingHint(provider) {
    const profile = provider && provider.media_profile ? provider.media_profile : {};
    const caps = provider && provider.capabilities ? provider.capabilities : {};
    const apiFormat = String(provider && provider.api_format ? provider.api_format : '');
    const wantsMedia = Boolean(caps.images || profile.default_image_provider);
    const hasMediaRoute = Boolean(
        profile.openai_compatible_media
        || profile.adapter_required
        || apiFormat === 'openai_images'
    );
    if (wantsMedia && !hasMediaRoute) {
        return t('mediaCapabilityHint');
    }
    if (hasMediaRoute && !wantsMedia && apiFormat !== 'openai_images') {
        return t('mediaModeEnabledWithoutCapability');
    }
    return '';
}

function renderMediaRoutingHint(provider) {
    const hint = providerMediaRoutingHint(provider);
    const hidden = hint ? '' : 'hidden';
    return `
        <div id="media-routing-hint" class="${hidden} mt-3 text-xs text-amber-200 bg-amber-950/25 border border-amber-700/50 rounded-lg p-3">
            ${escapeHtml(hint)}
        </div>
    `;
}

function renderMediaModeSegment(profile, disabled = false) {
    const mode = mediaModeFromProfile(profile || {});
    const disabledAttr = disabled ? ' disabled' : '';
    const options = [
        ['openai_compatible', t('openaiCompatibleMode'), t('directPassthrough')],
        ['adapter_required', t('adapterRequiredMode'), t('vendorSpecificMedia')],
        ['disabled', t('disabledMode'), t('noMediaForwarding')],
    ];
    return `
        <div class="media-mode-field mt-4">
            <div class="text-xs text-dark-400 mb-2">${escapeHtml(t('mediaProfile'))}</div>
            <div class="segmented-control" role="radiogroup" aria-label="${escapeAttr(t('mediaProfile'))}">
                ${options.map(([value, label, hint]) => `
                    <label class="segmented-option ${mode === value ? 'active' : ''} ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
                        <input
                            type="radio"
                            name="media-mode"
                            value="${escapeAttr(value)}"
                            onchange="syncMediaModeControls(true)"
                            ${mode === value ? 'checked' : ''}
                            ${disabledAttr}
                        >
                        <span class="segmented-label">${escapeHtml(label)}</span>
                        <span class="segmented-hint">${escapeHtml(hint)}</span>
                    </label>
                `).join('')}
            </div>
        </div>
    `;
}

function getSelectedMediaMode() {
    return document.querySelector('input[name="media-mode"]:checked')?.value || 'disabled';
}

function syncMediaModeControls(clearHiddenAsync = false) {
    const selectedMode = getSelectedMediaMode();
    document.querySelectorAll('.media-mode-field .segmented-option').forEach(option => {
        const input = option.querySelector('input[name="media-mode"]');
        option.classList.toggle('active', Boolean(input && input.value === selectedMode));
    });
    const asyncFields = document.getElementById('media-async-fields');
    const apiFormat = document.getElementById('provider-api-format')?.value || '';
    const hasExistingAsync = ['media-async-submit', 'media-poll-required', 'media-cancel-supported']
        .some(id => document.getElementById(id)?.checked);
    const showAsync = selectedMode === 'adapter_required'
        || (!clearHiddenAsync && hasExistingAsync);
    if (asyncFields) asyncFields.classList.toggle('hidden', !showAsync);
    if (asyncFields && !showAsync) {
        ['media-async-submit', 'media-poll-required', 'media-cancel-supported'].forEach(id => {
            const checkbox = document.getElementById(id);
            if (checkbox) checkbox.checked = false;
        });
    }
    syncMediaRoutingHint();
}

function syncMediaRoutingHint() {
    const hint = document.getElementById('media-routing-hint');
    if (!hint) return;
    const apiFormat = document.getElementById('provider-api-format')?.value || '';
    const selectedMode = getSelectedMediaMode();
    const wantsMedia = Boolean(
        document.getElementById('cap-images')?.checked
        || document.getElementById('media-default-image')?.checked
    );
    const hasMediaRoute = selectedMode !== 'disabled' || apiFormat === 'openai_images';
    let message = '';
    if (wantsMedia && !hasMediaRoute) {
        message = t('mediaCapabilityHint');
    } else if (hasMediaRoute && !wantsMedia && apiFormat !== 'openai_images') {
        message = t('mediaModeEnabledWithoutCapability');
    }
    hint.textContent = message;
    hint.classList.toggle('hidden', !message);
}

/**
 * 指标卡片。携带 .stagger-item，进入时参与交错动画。
 * .card-value.bump 可由外部 JS 在数值更新时临时附加，触发 CSS
 * count-bump 关键帧弹性放大。
 */
function renderMetricCard(label, value, sub) {
    return `
        <div class="card stagger-item">
            <div class="card-label">${escapeHtml(label)}</div>
            <div class="card-value">${escapeHtml(String(value))}</div>
            <div class="card-sub">${escapeHtml(sub || '')}</div>
        </div>
    `;
}

/**
 * 实施步骤行。左侧圆点使用 status-dot，完成项带 emerald
 * pulse 动画；整行 hover 时 translateX(4px) 向右微移。
 */
function renderStepRow(title, desc, done) {
    return `
        <div class="enhance-step-row stagger-item">
            <span class="status-dot ${done ? 'bg-emerald-500' : 'bg-dark-500'}"></span>
            <div>
                <div class="font-medium text-white">${escapeHtml(title)}</div>
                <div class="text-xs text-dark-400">${escapeHtml(desc)}</div>
            </div>
        </div>
    `;
}

/**
 * Quick Setup 的 Next Steps 预览块。
 * .preview-number 在父级 hover 时通过 CSS 选择器联动放大，
 * 无需 JS 事件监听，减少重排。
 */
function renderPreviewTile(number, title, desc) {
    return `
        <div class="preview-tile stagger-item">
            <div class="preview-number">${escapeHtml(number)}</div>
            <div class="font-medium text-white">${escapeHtml(title)}</div>
            <div class="text-xs text-dark-400 mt-1">${escapeHtml(desc)}</div>
        </div>
    `;
}

function renderStatusPill(key, label, tone = 'dark') {
    return `<span class="status-pill status-pill-${tone}" title="${escapeAttr(key)}">${escapeHtml(label)}</span>`;
}

function renderMiniBadge(label) {
    return `<span class="mini-badge">${escapeHtml(label || '')}</span>`;
}

function capabilityBadges(capabilities) {
    const caps = capabilities || {};
    return ['text', 'vision', 'custom_tools', 'reasoning', 'images', 'native_approval']
        .filter(key => caps[key])
        .map(key => renderMiniBadge(providerCapabilityLabel(key)));
}

function providerCapabilityLabel(capability) {
    const labels = {
        text: t('textCapability'),
        vision: t('visionInputCapability'),
        tools: t('toolsCapability'),
        custom_tools: t('customToolsCapability'),
        reasoning: t('reasoningCapability'),
        images: t('imagesCapability'),
        videos: t('videosCapability'),
        native_approval: t('nativeApprovalCapability'),
    };
    return labels[capability] || capability;
}

function renderEmptyState(text) {
    return `<div class="empty-state py-6">${escapeHtml(text)}</div>`;
}

function formatShortDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (isNaN(date.getTime())) return String(value).slice(0, 16);
    return date.toLocaleString();
}

function formatQuotaDate(value) {
    if (!value) return '-';
    const numeric = Number(value);
    const date = Number.isFinite(numeric) && numeric > 100000
        ? new Date(numeric * 1000)
        : new Date(value);
    if (isNaN(date.getTime())) return String(value).slice(0, 32);
    return date.toLocaleString();
}

function formatDuration(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    if (value < 60) return `${value}s`;
    const minutes = Math.floor(value / 60);
    const remainingSeconds = value % 60;
    if (minutes < 60) return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

function formatQuotaValue(value) {
    if (typeof value === 'number') {
        return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 6 });
    }
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (value && typeof value === 'object') return JSON.stringify(value);
    return String(value ?? '');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttr(value) {
    return escapeHtml(value);
}

// ─────────────── Batch Operations & Proxy Controls ───────────────

function renderProxyControlCard() {
    const status = providerState.proxyStatus || {};
    const running = status.running || false;
    const backoff = status.port_backoff || {};
    const portLabel = status.port ? `:${status.port}` : '--';
    const baseUrl = status.base_url || t('notStarted');
    const approvalBrokerConnected = Boolean(status.media_auto_approval_reviewer_connected);
    const authRequired = Boolean(status.local_proxy_auth_required);
    const tokenFingerprint = status.local_proxy_token_fingerprint || '';
    const backoffNotice = backoff.used
        ? `<div class="mt-3 text-xs text-amber-200 bg-amber-950/30 border border-amber-700/40 rounded-lg p-2">
                ${escapeHtml(t('proxyBackoffNotice', { from: backoff.from, to: backoff.to }))}
           </div>`
        : '';
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('localProxyTitle'))}</h3>
            <div class="flex items-center gap-2 mt-3">
                <span class="status-dot ${running ? 'bg-emerald-500' : 'bg-dark-500'}"></span>
                <span class="text-xs ${running ? 'text-emerald-400' : 'text-dark-400'}">${escapeHtml(running ? t('proxyRunning') : t('proxyStopped'))} ${escapeHtml(portLabel)}</span>
            </div>
            <div class="mt-2 text-xs text-dark-400 break-all">${escapeHtml(baseUrl)}</div>
            <div class="mt-2 text-xs ${approvalBrokerConnected ? 'text-emerald-300' : 'text-dark-500'}">
                ${escapeHtml(t('approvalBrokerLabel'))} ${escapeHtml(approvalBrokerConnected ? t('connectedLabel') : t('idleLabel'))}
            </div>
            <div class="mt-2 text-xs ${authRequired ? 'text-emerald-300' : 'text-amber-300'}">
                ${escapeHtml(authRequired ? t('localProxyAuthEnabled') : t('localProxyAuthDisabled'))}${tokenFingerprint ? ` · ${escapeHtml(t('tokenFingerprint', { value: tokenFingerprint }))}` : ''}
            </div>
            ${backoffNotice}
            ${status.last_start_error ? `<div class="mt-3 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2">${escapeHtml(status.last_start_error)}</div>` : ''}
            <div class="mt-2 text-xs text-dark-500">${escapeHtml(t('proxyPortHint'))}</div>
            <div class="flex flex-wrap gap-2 mt-3">
                <button id="proxy-start-btn" onclick="startProxy()" class="btn btn-primary text-xs" ${running ? 'disabled' : ''}>${escapeHtml(t('startProxy'))}</button>
                <button id="proxy-stop-btn" onclick="stopProxy()" class="btn btn-danger text-xs" ${running ? '' : 'disabled'}>${escapeHtml(t('stopProxy'))}</button>
            </div>
            <div id="proxy-action-error" class="hidden mt-2 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
        </div>
    `;
}

function renderProxyRouteTestCard() {
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('proxyRouteTest'))}</h3>
            <div class="text-xs text-dark-500 mt-1">${escapeHtml(t('proxyRouteTestDesc'))}</div>
            <div class="flex gap-2 mt-3">
                <input id="proxy-test-model" class="input flex-1 text-sm" placeholder="qwen/qwen3-coder-plus">
                <button id="proxy-test-btn" onclick="testProxyRoute()" class="btn btn-secondary text-xs">${escapeHtml(t('testRoute'))}</button>
            </div>
            <pre id="proxy-test-result" class="preview-code mt-3">${escapeHtml(t('routeTestPrompt'))}</pre>
        </div>
    `;
}

async function runBulkModelAction(action) {
    /**
     * 批量更新当前 provider 的模型选择状态。
     * 操作期间禁用批量按钮，失败时在 #bulk-action-error 显示错误（不 toast），
     * 成功后短暂高亮对应按钮。
     */
    const provider = getSelectedProvider();
    if (!provider) return;
    const errorEl = document.getElementById('bulk-action-error');
    if (errorEl) errorEl.classList.add('hidden');
    if (isCodexLoginProvider(provider)) {
        if (errorEl) {
            errorEl.textContent = t('codexLoginProviderReadOnly');
            errorEl.classList.remove('hidden');
        }
        return;
    }

    document.querySelectorAll('[data-bulk-action]').forEach(btn => { btn.disabled = true; });

    try {
        await api('/api/providers/' + encodeURIComponent(provider.id) + '/bulk-models', {
            method: 'POST',
            body: JSON.stringify({ action }),
        });
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        setTimeout(() => {
            const btn = document.querySelector('[data-bulk-action="' + action + '"]');
            if (btn) {
                btn.classList.add('btn-success-flash');
                setTimeout(() => btn.classList.remove('btn-success-flash'), 1200);
            }
        }, 60);
        showToast(t('bulkModelsUpdated'), 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = t('bulkModelsFailed') + (err.message || t('networkErrorGeneric'));
            errorEl.classList.remove('hidden');
        }
        document.querySelectorAll('[data-bulk-action]').forEach(btn => { btn.disabled = false; });
    }
}

async function addSelectedModelsToAmr() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const errorEl = document.getElementById('bulk-action-error');
    if (errorEl) errorEl.classList.add('hidden');
    if (isCodexLoginProvider(provider) || provider.switch_only || provider.amr_excluded || provider.local_proxy_routing === false) {
        if (errorEl) {
            errorEl.textContent = t('officialProviderSwitchOnly');
            errorEl.classList.remove('hidden');
        } else {
            showToast(t('officialProviderSwitchOnly'), 'warning');
        }
        return;
    }

    if (!provider.enabled) {
        if (errorEl) {
            errorEl.textContent = t('amrProviderDisabled');
            errorEl.classList.remove('hidden');
        }
        return;
    }
    const selectedCount = (provider.models || [])
        .filter(model => model && model.selected && model.enabled !== false)
        .length;
    if (!selectedCount) {
        if (errorEl) {
            errorEl.textContent = t('amrNoSelectedModels');
            errorEl.classList.remove('hidden');
        }
        return;
    }

    document.querySelectorAll('[data-bulk-action]').forEach(btn => { btn.disabled = true; });
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id) + '/amr/add-selected', {
            method: 'POST',
            body: JSON.stringify({ group_id: 'default' }),
        });
        await refreshCatalogPreview();
        renderProvidersPage();
        setTimeout(() => {
            const btn = document.querySelector('[data-bulk-action="add_selected_to_amr"]');
            if (btn) {
                btn.classList.add('btn-success-flash');
                setTimeout(() => btn.classList.remove('btn-success-flash'), 1200);
            }
        }, 60);
        showToast(t('amrModelsAdded', { count: result.added_count || selectedCount }), 'success', 1800);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = t('amrAddFailed') + (err.message || t('networkErrorGeneric'));
            errorEl.classList.remove('hidden');
        }
        document.querySelectorAll('[data-bulk-action]').forEach(btn => { btn.disabled = false; });
    }
}

async function setProviderVisibility(providerId, visibility, selectEl) {
    /**
     * 快捷设置 provider catalog_visibility。
     * 切换时禁用 select，成功刷新 catalog preview，失败恢复选项并显示提示。
     */
    if (selectEl) selectEl.disabled = true;
    try {
        await api('/api/providers/' + encodeURIComponent(providerId) + '/visibility', {
            method: 'POST',
            body: JSON.stringify({ visibility }),
        });
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast(t('visibilityUpdated'), 'success', 1500);
    } catch (err) {
        showToast(t('visibilityUpdateFailed') + (err.message || t('networkErrorGeneric')), 'error');
        if (selectEl) {
            const provider = (providerState.providers || []).find(p => p.id === providerId);
            if (provider) selectEl.value = provider.catalog_visibility;
            selectEl.disabled = false;
        }
    }
}

async function refreshProxyStatus() {
    try {
        providerState.proxyStatus = await api('/api/proxy/status');
    } catch (err) {
        providerState.proxyStatus = { running: false, error: err.message };
    }
}

async function startProxy() {
    const btn = document.getElementById('proxy-start-btn');
    const errorEl = document.getElementById('proxy-action-error');
    if (errorEl) errorEl.classList.add('hidden');
    if (btn) btn.disabled = true;
    try {
        await api('/api/proxy/start', { method: 'POST', body: '{}' });
        await refreshProxyStatus();
        renderProvidersPage();
        setTimeout(() => {
            const newBtn = document.getElementById('proxy-start-btn');
            if (newBtn) {
                newBtn.classList.add('btn-success-flash');
                setTimeout(() => newBtn.classList.remove('btn-success-flash'), 1200);
            }
        }, 60);
        showToast(t('proxyStartedToast'), 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = t('proxyStartFailed') + (err.message || t('networkErrorGeneric'));
            errorEl.classList.remove('hidden');
        }
        if (btn) btn.disabled = false;
    }
}

async function stopProxy() {
    const btn = document.getElementById('proxy-stop-btn');
    const errorEl = document.getElementById('proxy-action-error');
    if (errorEl) errorEl.classList.add('hidden');
    if (btn) btn.disabled = true;
    try {
        await api('/api/proxy/stop', { method: 'POST', body: '{}' });
        await refreshProxyStatus();
        renderProvidersPage();
        showToast(t('proxyStoppedToast'), 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = t('proxyStopFailed') + (err.message || t('networkErrorGeneric'));
            errorEl.classList.remove('hidden');
        }
        if (btn) btn.disabled = false;
    }
}

async function testProxyRoute() {
    const input = document.getElementById('proxy-test-model');
    const result = document.getElementById('proxy-test-result');
    const btn = document.getElementById('proxy-test-btn');
    const modelId = (input?.value || '').trim();
    if (!modelId) {
        if (result) result.textContent = t('enterModelId');
        return;
    }
    if (btn) btn.disabled = true;
    if (result) result.textContent = t('testing');
    try {
        const data = await api('/api/proxy/test-route', {
            method: 'POST',
            body: JSON.stringify({ model: modelId }),
        });
        if (result) {
            result.textContent = t('routeTestProvider') + ': ' + (data.provider_id || '-') + '\n' +
                t('routeTestDisplay') + ': ' + (data.display_name || '-') + '\n' +
                t('routeTestBaseUrl') + ': ' + (data.base_url || '-') + '\n' +
                t('routeTestFormat') + ': ' + (data.api_format || '-');
        }
        if (btn) {
            btn.classList.add('btn-success-flash');
            setTimeout(() => btn.classList.remove('btn-success-flash'), 1200);
        }
    } catch (err) {
        if (result) result.textContent = t('routeTestFailed') + (err.message || t('networkErrorGeneric'));
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ─────────────── Codex Integration Page ───────────────

let codexIntegrationState = {
    status: null,
    preview: null,
    connectionDraft: null,
    previewChecking: false,
    previewError: '',
    previewTimer: null,
    permissionsPreview: null,
    approvalBridgePreview: null,
    approvalBridgeMessage: '',
    approvalBridgeDecision: {
        decision: 'ask_user',
        risk_level: 'unknown',
        reason: '',
    },
    backups: [],
    injectionApplying: false,
    startJob: null,
    loading: false,
};

function getCodexIntegrationProxyBaseUrl() {
    const status = codexIntegrationState.status || {};
    const proxyStatus = status.proxy_status || {};
    if (status.default_proxy_base_url) return status.default_proxy_base_url;
    if (proxyStatus.base_url) return proxyStatus.base_url;
    if (proxyStatus.port) return `http://127.0.0.1:${proxyStatus.port}/v1`;
    return 'http://127.0.0.1:51235/v1';
}

/**
 * 加载 Codex Integration 页。
 * 该页卡片为静态结构，动画依赖 app.js 中 triggerStaggerAnimations
 * 对 .card:not(.stagger-item) 的兜底逻辑。
 */
async function loadCodexIntegrationPage() {
    await refreshCodexIntegrationStatus();
    await refreshCodexIntegrationBackups();
    renderCodexIntegrationPage();
    await refreshCodexConnectionCheck({ silent: true });
    setStatus(t('codexIntegrationTitle'));
}

async function refreshCodexIntegrationStatus() {
    try {
        codexIntegrationState.status = await api('/api/codex-integration/status');
    } catch (err) {
        codexIntegrationState.status = { error: err.message };
    }
}

async function refreshCodexIntegrationBackups() {
    try {
        const data = await api('/api/codex-integration/backups');
        codexIntegrationState.backups = data.backups || [];
    } catch (err) {
        codexIntegrationState.backups = [];
    }
}

function readCodexConnectionForm() {
    const status = codexIntegrationState.status || {};
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || getCodexIntegrationProxyBaseUrl();
    const proxyModel = document.getElementById('ci-proxy-model')?.value || 'auto';
    const preserveAuth = document.getElementById('ci-preserve-auth')?.checked !== false;
    const startMode = document.getElementById('ci-start-mode')?.value || 'preserve_login_proxy';
    const injectionEnabled = document.getElementById('ci-enable-cdp-injection')
        ? document.getElementById('ci-enable-cdp-injection').checked
        : status.codex_injection_enabled !== false;
    const cdpPort = parseInt(document.getElementById('ci-cdp-port')?.value || status.codex_cdp_port || '51236', 10) || 51236;
    const draft = {
        proxy_base_url: proxyBaseUrl,
        proxy_model: proxyModel,
        preserve_official_auth: preserveAuth,
        start_mode: startMode,
        enable_cdp_injection: injectionEnabled,
        cdp_port: cdpPort,
    };
    codexIntegrationState.connectionDraft = draft;
    return draft;
}

function scheduleCodexConnectionCheck() {
    if (codexIntegrationState.previewTimer) {
        clearTimeout(codexIntegrationState.previewTimer);
    }
    codexIntegrationState.previewTimer = setTimeout(() => {
        codexIntegrationState.previewTimer = null;
        refreshCodexConnectionCheck({ silent: true });
    }, 450);
}

async function refreshCodexConnectionCheck(options = {}) {
    const silent = options.silent !== false;
    const payload = readCodexConnectionForm();
    codexIntegrationState.previewChecking = true;
    codexIntegrationState.previewError = '';
    renderCodexIntegrationPage();
    try {
        codexIntegrationState.preview = await api('/api/codex-integration/preview', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        codexIntegrationState.previewError = '';
        return codexIntegrationState.preview;
    } catch (err) {
        codexIntegrationState.previewError = err.message || t('unknownError');
        if (!silent) showToast(t('connectionCheckFailedWithError', { error: codexIntegrationState.previewError }), 'error');
        return null;
    } finally {
        codexIntegrationState.previewChecking = false;
        renderCodexIntegrationPage();
    }
}

async function previewCodexIntegration() {
    const result = await refreshCodexConnectionCheck({ silent: false });
    if (result) showToast(t('connectionCheckUpdated'), 'success');
}

async function previewCodexPermissions() {
    try {
        codexIntegrationState.permissionsPreview = await api('/api/codex-integration/permissions-preview', {
            method: 'POST',
            body: JSON.stringify({
                approval_policy: document.getElementById('ci-approval-policy')?.value || '',
                sandbox_mode: document.getElementById('ci-sandbox-mode')?.value || '',
                windows_sandbox: document.getElementById('ci-windows-sandbox')?.value || '',
                default_permissions: document.getElementById('ci-default-permissions')?.value || '',
                writable_roots: document.getElementById('ci-writable-roots')?.value || '',
                network_access: document.getElementById('ci-network-access')?.checked || false,
                exclude_tmpdir_env_var: document.getElementById('ci-exclude-tmpdir-env')?.checked || false,
                exclude_slash_tmp: document.getElementById('ci-exclude-slash-tmp')?.checked || false,
            }),
        });
        renderCodexIntegrationPage();
        showToast(t('sandboxPreviewGenerated'), 'success');
    } catch (err) {
        showToast(t('previewFailedWithError', { error: err.message }), 'error');
    }
}

async function repairCodexSandboxPermissions() {
    const manual = requestCodexMutationConfirmation(t('repairCodexSandboxPermissionsAction'));
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/permissions-repair', {
            method: 'POST',
            body: JSON.stringify(manual),
        });
        showToast(result.message || t('repairCodexSandboxPermissionsCompleted'), result.success ? 'success' : 'error');
        await refreshCodexIntegrationStatus();
        await refreshCodexIntegrationBackups();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('repairCodexSandboxPermissionsFailed') + err.message, 'error');
    }
}

function defaultCodexApprovalBridgeMessage() {
    return {
        jsonrpc: '2.0',
        id: 101,
        method: 'item/commandExecution/requestApproval',
        params: {
            threadId: 'thread_dry_run',
            turnId: 'turn_dry_run',
            itemId: 'cmd_dry_run',
            approvalId: 'approval_dry_run',
            command: 'python -m pytest',
            cwd: 'C:/repo',
            reason: 'Run local tests',
            availableDecisions: ['accept', 'acceptForSession', 'decline'],
        },
    };
}

function codexApprovalBridgeMessageText() {
    if (codexIntegrationState.approvalBridgeMessage) return codexIntegrationState.approvalBridgeMessage;
    return JSON.stringify(defaultCodexApprovalBridgeMessage(), null, 2);
}

async function previewCodexApprovalBridge() {
    const messageText = document.getElementById('ci-approval-bridge-json')?.value || codexApprovalBridgeMessageText();
    const decision = {
        decision: document.getElementById('ci-approval-bridge-decision')?.value || 'ask_user',
        risk_level: document.getElementById('ci-approval-bridge-risk')?.value || 'unknown',
        reason: document.getElementById('ci-approval-bridge-reason')?.value || t('previewOnly'),
    };
    try {
        const message = JSON.parse(messageText);
        codexIntegrationState.approvalBridgeMessage = JSON.stringify(message, null, 2);
        codexIntegrationState.approvalBridgeDecision = decision;
        codexIntegrationState.approvalBridgePreview = await api('/api/codex-integration/approval-bridge-preview', {
            method: 'POST',
            body: JSON.stringify({ message, decision }),
        });
        renderCodexIntegrationPage();
        showToast(t('approvalBridgePreviewGenerated'), 'success');
    } catch (err) {
        showToast(t('previewFailedWithError', { error: err.message }), 'error');
    }
}

async function applyCodexIntegration() {
    const payload = readCodexConnectionForm();
    await refreshCodexConnectionCheck({ silent: true });
    const manual = requestCodexMutationConfirmation(t('writeCodexConfigAction'));
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/apply', {
            method: 'POST',
            body: JSON.stringify({
                ...payload,
                ...manual,
            }),
        });
        if (result.success) {
            showToast(result.deferred_until_codex_start ? t('applyDeferredUntilStart') : t('applySuccessRestart'), 'success');
        } else {
            showToast(t('applyFailed') + (result.errors || []).join('; '), 'error');
        }
        await refreshCodexIntegrationStatus();
        await refreshCodexIntegrationBackups();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('applyFailed') + err.message, 'error');
    }
}

async function startCodexWithSelectedMode() {
    try {
        const draft = readCodexConnectionForm();
        setStatus(t('codexStartRequested'));
        const result = await startCodexWithProgress({
                start_mode: draft.start_mode,
                preserve_official_auth: draft.preserve_official_auth,
                enable_cdp_injection: draft.enable_cdp_injection,
                cdp_port: draft.cdp_port,
        }, {
            onProgress: (job) => {
                codexIntegrationState.startJob = job;
                renderCodexIntegrationPage();
            },
        });
        showToast(result.message || t('codexStartRequested'), result.success ? 'success' : 'error');
        codexIntegrationState.startJob = null;
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        codexIntegrationState.startJob = null;
        showToast(t('codexStartFailed') + err.message, 'error');
        renderCodexIntegrationPage();
    }
}

async function retryCodexInjection() {
    const draft = readCodexConnectionForm();
    codexIntegrationState.injectionApplying = true;
    renderCodexIntegrationPage();
    try {
        const result = await api('/api/codex-injection/apply', {
            method: 'POST',
            body: JSON.stringify({
                enable_cdp_injection: draft.enable_cdp_injection,
                cdp_port: draft.cdp_port,
                backend_url: (codexIntegrationState.status || {}).backend_url || '',
            }),
        });
        showToast(result.message || t('codexInjectionApplied'), result.success ? 'success' : 'warning');
    } catch (err) {
        showToast(t('codexInjectionFailed') + err.message, 'error');
    } finally {
        codexIntegrationState.injectionApplying = false;
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    }
}

async function startOfficialCodex() {
    if (!confirm(t('confirmStartOfficialCodex'))) return;
    try {
        setStatus(t('codexOfficialStartRequested'));
        const result = await startCodexWithProgress({
            start_mode: 'official_direct',
            official_mode: true,
        }, {
            onProgress: (job) => {
                codexIntegrationState.startJob = job;
                renderCodexIntegrationPage();
            },
        });
        showToast(result.message || t('codexOfficialStartRequested'), result.success ? 'success' : 'error');
        codexIntegrationState.startJob = null;
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        codexIntegrationState.startJob = null;
        showToast(t('codexOfficialStartFailed') + err.message, 'error');
        renderCodexIntegrationPage();
    }
}

async function restoreCodexConfig() {
    const manual = requestCodexMutationConfirmation(t('restoreCodexConfigAction'));
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/restore-config', {
            method: 'POST',
            body: JSON.stringify(manual),
        });
        if (result.success) {
            showToast(t('configRestoredRestart'), 'success');
        } else {
            showToast(t('restoreFailed') + (result.error || ''), 'error');
        }
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('restoreFailed') + err.message, 'error');
    }
}

async function restoreCodexAuth() {
    const manual = requestCodexMutationConfirmation(t('restoreCodexAuthAction'));
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/restore-auth', {
            method: 'POST',
            body: JSON.stringify(manual),
        });
        if (result.success) {
            showToast(t('authRestoredRestart'), 'success');
        } else {
            showToast(t('restoreFailed') + (result.error || ''), 'error');
        }
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('restoreFailed') + err.message, 'error');
    }
}

async function repairCodexConfigTemplate() {
    const manual = requestCodexMutationConfirmation(t('repairCodexConfigAction'));
    if (!manual) return;
    const restartWindows = confirm(t('repairConfigRestartConfirm'));
    try {
        const result = await api('/api/codex-integration/repair-config-template', {
            method: 'POST',
            body: JSON.stringify({
                ...manual,
                restart_windows: restartWindows,
            }),
        });
        showToast(result.message || t('repairConfigCompleted'), result.success ? 'success' : 'error');
        await refreshCodexIntegrationStatus();
        await refreshCodexIntegrationBackups();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('repairConfigFailed') + err.message, 'error');
    }
}

async function resetCodexForOfficialLogin() {
    if (!confirm(t('resetOfficialLoginRisk'))) return;
    const riskPhrase = 'CHAT_HISTORY_MAY_BE_LOST';
    const riskValue = prompt(t('resetOfficialLoginRiskConfirm', { phrase: riskPhrase }));
    if (riskValue !== riskPhrase) {
        showToast(t('codexMutationCancelled'), 'warning');
        return;
    }
    const manual = requestCodexMutationConfirmation(t('resetCodexOfficialLoginAction'));
    if (!manual) return;
    const restartWindows = confirm(t('restartWindowsAfterOfficialResetConfirm'));
    try {
        const result = await api('/api/codex-integration/reset-for-official-login', {
            method: 'POST',
            body: JSON.stringify({
                ...manual,
                risk_confirmation: riskPhrase,
                restart_windows: restartWindows,
            }),
        });
        showToast(result.message || t('resetOfficialLoginCompleted'), result.success ? 'success' : 'error');
        await refreshCodexIntegrationStatus();
        await refreshCodexIntegrationBackups();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('resetOfficialLoginFailed') + err.message, 'error');
    }
}

function requestCodexMutationConfirmation(actionLabel) {
    const phrase = 'MODIFY_CODEX_FILES';
    const value = prompt(t('codexMutationConfirm', { action: actionLabel, phrase }));
    if (value !== phrase) {
        showToast(t('codexMutationCancelled'), 'warning');
        return null;
    }
    return {
        manual_codex_mutation: true,
        confirmation: phrase,
    };
}

function renderCodexEnhancementModeCard(status = {}) {
    const official = status.auth_mode === 'official_oauth';
    const pluginUnlock = Boolean(status.plugin_unlock_enabled);
    const tone = official ? 'emerald' : pluginUnlock ? 'accent' : 'amber';
    const badge = official
        ? t('officialEnhancementBadge')
        : pluginUnlock ? t('pluginUnlockOnBadge') : t('pluginUnlockOffBadge');
    const desc = official
        ? t('officialEnhancementModeDesc')
        : pluginUnlock ? t('nonOfficialPluginUnlockOnDesc') : t('nonOfficialPluginUnlockOffDesc');
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('enhancementModeTitle'))}</h3>
                ${renderStatusPill('enhancement-mode', badge, tone)}
            </div>
            <p class="text-sm text-dark-400 mt-2">${escapeHtml(desc)}</p>
            ${official ? `<p class="text-xs text-dark-400 mt-2">${escapeHtml(t('preserveLoginProxyModeDesc'))}</p>` : ''}
            <button onclick="navigateTo('settings'); showSettingsWizardStep(5);" class="btn btn-secondary text-xs mt-4">${escapeHtml(t('openStartupApprovalStep'))}</button>
        </div>
    `;
}

function renderCodexInjectionCard(status = {}) {
    const draft = codexIntegrationState.connectionDraft || {};
    const injectionEnabled = Object.prototype.hasOwnProperty.call(draft, 'enable_cdp_injection')
        ? draft.enable_cdp_injection !== false
        : status.codex_injection_enabled !== false;
    const cdpPort = Number(draft.cdp_port || status.codex_cdp_port || 51236);
    const backendUrl = status.backend_url || '';
    const applying = Boolean(codexIntegrationState.injectionApplying);
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('codexInjectionTitle'))}</h3>
                ${renderStatusPill('cdp', injectionEnabled ? t('codexInjectionOn') : t('codexInjectionOff'), injectionEnabled ? 'emerald' : 'dark')}
            </div>
            <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('codexInjectionDesc'))}</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
                <label class="flex items-center gap-2 text-sm cursor-pointer bg-dark-900/60 border border-dark-700 rounded-lg px-3 py-2">
                    <input id="ci-enable-cdp-injection" type="checkbox" onchange="scheduleCodexConnectionCheck()" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${injectionEnabled ? 'checked' : ''}>
                    <span>${escapeHtml(t('enableCodexInjection'))}</span>
                </label>
                ${renderInput('ci-cdp-port', t('cdpPort'), cdpPort, 'number', false, 'scheduleCodexConnectionCheck()')}
            </div>
            <div class="grid grid-cols-1 gap-3 mt-3">
                ${renderReadonlyKV(t('backendUrl'), backendUrl || '-')}
                ${renderReadonlyKV(t('cdpEndpoint'), `127.0.0.1:${cdpPort}`)}
            </div>
            <div class="flex flex-wrap gap-2 mt-4">
                <button onclick="retryCodexInjection()" class="btn btn-secondary text-xs" ${applying || !injectionEnabled ? 'disabled' : ''}>${escapeHtml(applying ? t('applying') : t('retryCodexInjection'))}</button>
            </div>
        </div>
    `;
}

function renderCodexRepairCard(status = {}) {
    const risk = status.config_risk_assessment || {};
    const counts = risk.counts || {};
    const issues = Array.isArray(risk.issues) ? risk.issues : [];
    const issueSummary = `${counts.critical || 0} / ${counts.warning || 0} / ${counts.info || 0}`;
    const topIssues = issues.slice(0, 4).map(issue => `
        <div class="rounded-md border border-dark-800 bg-dark-950/45 p-2">
            <div class="text-xs font-semibold ${issue.severity === 'critical' ? 'text-red-300' : issue.severity === 'warning' ? 'text-amber-300' : 'text-dark-300'}">${escapeHtml(issue.path || issue.code || '')}</div>
            <div class="text-xs text-dark-400 mt-1">${escapeHtml(issue.message || '')}</div>
        </div>
    `).join('');
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('codexConfigRepairTitle'))}</h3>
                ${renderStatusPill('config-risk', t('configRiskCount', { count: issueSummary }), (counts.critical || 0) ? 'red' : (counts.warning || 0) ? 'amber' : 'emerald')}
            </div>
            <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('codexConfigRepairDesc'))}</p>
            <div class="mt-3 space-y-2">
                ${topIssues || `<div class="text-sm text-dark-500">${escapeHtml(t('noConfigRisksDetected'))}</div>`}
            </div>
            <div class="flex flex-wrap gap-2 mt-4">
                <button onclick="repairCodexConfigTemplate()" class="btn btn-warning text-xs">${escapeHtml(t('repairCodexConfigTemplate'))}</button>
            </div>
            <p class="text-xs text-dark-500 mt-3">${escapeHtml(t('repairCodexConfigTemplateDesc'))}</p>
        </div>
    `;
}

function renderOfficialLoginResetCard(status = {}) {
    const official = status.auth_mode === 'official_oauth';
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('officialLoginResetTitle'))}</h3>
                ${renderStatusPill('official-reset', official ? t('officialLoginDetected') : t('manualRiskAction'), official ? 'emerald' : 'amber')}
            </div>
            <p class="text-sm text-dark-400 mt-2">${escapeHtml(t('officialLoginResetDesc'))}</p>
            <div class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 mt-3 text-xs text-amber-100">
                ${escapeHtml(t('officialLoginResetRiskLine'))}
            </div>
            <div class="flex flex-wrap gap-2 mt-4">
                <button onclick="resetCodexForOfficialLogin()" class="btn btn-danger text-xs">${escapeHtml(t('resetCodexOfficialLogin'))}</button>
            </div>
        </div>
    `;
}

function renderCodexStartProgressCard() {
    const job = codexIntegrationState.startJob;
    if (!job) return '';
    const progress = Math.min(Math.max(Number(job.progress || 0), 0), 100);
    const message = job.message || t('codexStartRequested');
    const stage = job.stage || 'queued';
    return `
        <div class="rounded-lg border border-accent-500/35 bg-accent-500/10 p-3">
            <div class="flex items-center justify-between gap-3">
                <div class="text-sm font-semibold text-accent-100">${escapeHtml(message)}</div>
                <div class="text-xs font-mono text-accent-200">${escapeHtml(`${progress}%`)}</div>
            </div>
            <div class="mt-2 h-2 rounded-full bg-dark-900 overflow-hidden">
                <div class="h-full bg-accent-400 transition-all" style="width:${progress}%"></div>
            </div>
            <div class="mt-2 text-xs text-dark-400">${escapeHtml(t('codexStartStage', { stage }))}</div>
        </div>
    `;
}

function setCodexStartMode(mode) {
    const input = document.getElementById('ci-start-mode');
    if (input) input.value = mode;
    if (mode === 'official_direct') {
        const preserve = document.getElementById('ci-preserve-auth');
        if (preserve) preserve.checked = true;
    }
    document.querySelectorAll('[data-codex-start-mode-card]').forEach(card => {
        card.classList.toggle('ring-2', card.getAttribute('data-codex-start-mode-card') === mode);
        card.classList.toggle('ring-accent-500', card.getAttribute('data-codex-start-mode-card') === mode);
    });
    scheduleCodexConnectionCheck();
}

function renderCodexConnectionModeCard(formStartMode, formProxyBaseUrl, formProxyModel, formPreserveAuth, proxyBackoffNote) {
    const modes = [
        {
            value: 'official_direct',
            title: t('modeOfficialDirectTitle'),
            desc: t('modeOfficialDirectDesc'),
            action: t('modeOfficialDirectAction'),
            tone: 'emerald',
            onclick: "setCodexStartMode('official_direct'); startOfficialCodex();",
        },
        {
            value: 'preserve_login_proxy',
            title: t('modePreserveProxyTitle'),
            desc: t('modePreserveProxyDesc'),
            action: t('modePreserveProxyAction'),
            tone: 'accent',
            onclick: "setCodexStartMode('preserve_login_proxy'); startCodexWithSelectedMode();",
        },
        {
            value: 'proxy_injection',
            title: t('modeProxyInjectionTitle'),
            desc: t('modeProxyInjectionDesc'),
            action: t('modeProxyInjectionAction'),
            tone: 'amber',
            onclick: "setCodexStartMode('proxy_injection'); startCodexWithSelectedMode();",
        },
    ];
    return `
        <div class="card">
            <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
                <div>
                    <h3 class="card-title">${escapeHtml(t('codexConnectionModeTitle'))}</h3>
                    <p class="text-xs text-dark-500 mt-1">${escapeHtml(t('codexConnectionModeDesc'))}</p>
                </div>
                ${renderStatusPill('mode', providerOptionLabel(formStartMode), 'accent')}
            </div>
            <input id="ci-start-mode" type="hidden" value="${escapeAttr(formStartMode)}">
            <div class="grid grid-cols-1 xl:grid-cols-3 gap-3 mt-4">
                ${modes.map(mode => {
                    const active = formStartMode === mode.value;
                    const toneClass = mode.tone === 'emerald'
                        ? 'border-emerald-600/50 bg-emerald-950/15'
                        : mode.tone === 'amber'
                            ? 'border-amber-600/50 bg-amber-950/15'
                            : 'border-accent-600/50 bg-accent-950/15';
                    return `
                        <div
                            data-codex-start-mode-card="${escapeAttr(mode.value)}"
                            class="rounded-xl border ${active ? toneClass + ' ring-2 ring-accent-500' : 'border-dark-800 bg-dark-950/45'} p-4 transition-all"
                        >
                            <div class="text-sm font-semibold text-white">${escapeHtml(mode.title)}</div>
                            <p class="text-xs text-dark-400 mt-2 min-h-[48px]">${escapeHtml(mode.desc)}</p>
                            <button onclick="${escapeAttr(mode.onclick)}" class="btn ${active ? 'btn-primary' : 'btn-secondary'} text-xs mt-4 w-full">${escapeHtml(mode.action)}</button>
                        </div>
                    `;
                }).join('')}
            </div>
            <div class="grid grid-cols-1 gap-4 mt-4">
                ${renderInput('ci-proxy-base-url', t('proxyBaseUrl'), formProxyBaseUrl, 'text', false, 'scheduleCodexConnectionCheck()')}
                ${renderInput('ci-proxy-model', t('proxyModel'), formProxyModel, 'text', false, 'scheduleCodexConnectionCheck()')}
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="ci-preserve-auth" type="checkbox" onchange="scheduleCodexConnectionCheck()" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${formPreserveAuth ? 'checked' : ''}>
                    <span>${escapeHtml(t('preserveOfficialOAuth'))}</span>
                </label>
            </div>
            ${proxyBackoffNote}
            <div class="flex flex-wrap gap-2 mt-4">
                <button onclick="startCodexWithSelectedMode()" class="btn btn-warning">${escapeHtml(t('manualApplyCodexConfig'))}</button>
                <button onclick="previewCodexIntegration()" class="btn btn-secondary">${escapeHtml(t('refreshPreview'))}</button>
            </div>
            <div class="mt-3">${renderCodexStartProgressCard()}</div>
            <p class="text-xs text-dark-500 mt-3">${escapeHtml(t('officialCodexStartDesc'))}</p>
        </div>
    `;
}

/**
 * 渲染 Codex Integration 页面（状态查看 / Diff 预览 / 回滚）。
 * 该页卡片为静态 HTML 结构（无 .stagger-item），因此依赖 app.js 中
 * triggerStaggerAnimations 对 .card:not(.stagger-item) 的兜底动画。
 */
function renderCodexIntegrationPage() {
    const root = document.getElementById('codex-integration-root');
    if (!root) return;
    const status = codexIntegrationState.status || {};
    const preview = codexIntegrationState.preview;
    const backups = codexIntegrationState.backups;
    const proxyBaseUrl = getCodexIntegrationProxyBaseUrl();
    const proxyStatus = status.proxy_status || {};
    const proxyBackoff = proxyStatus.port_backoff || {};
    const preserveAuthDefault = status.default_preserve_official_auth === true;
    const defaultStartMode = status.default_start_mode || (preserveAuthDefault ? 'preserve_login_proxy' : 'proxy_injection');
    const connectionDraft = codexIntegrationState.connectionDraft || {};
    const formStartMode = connectionDraft.start_mode || defaultStartMode;
    const formProxyBaseUrl = connectionDraft.proxy_base_url || proxyBaseUrl;
    const formProxyModel = connectionDraft.proxy_model || 'auto';
    const formPreserveAuth = Object.prototype.hasOwnProperty.call(connectionDraft, 'preserve_official_auth')
        ? connectionDraft.preserve_official_auth !== false
        : preserveAuthDefault;
    const proxyBackoffNote = proxyBackoff.used
        ? `<div class="mt-2 text-xs text-amber-300">${escapeHtml(t('proxyBackoffNotice', { from: proxyBackoff.from, to: proxyBackoff.to }))}</div>`
        : '';
    const effectiveProvider = status.effective_model_provider || (status.config || {}).model_provider || '';
    const providerDisplay = status.effective_model_provider_source === 'official_oauth'
        ? t('officialOpenAIProvider')
        : (effectiveProvider || '-');
    const effectiveModel = status.effective_model || (status.config || {}).model || '-';

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">${escapeHtml(t('codexIntegrationTitle'))}</h2>
                <p class="text-sm text-dark-400 mt-1">${escapeHtml(t('codexIntegrationDesc'))}</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('auth-mode', t('authPill', { mode: status.auth_mode || t('statusUnknown') }), status.auth_mode === 'official_oauth' ? 'emerald' : 'amber')}
                ${renderStatusPill('restart', t('restartRequiredAfterWrites'), 'amber')}
            </div>
        </div>

        <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4 mt-6">
            <div class="space-y-4">
                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('currentCodexStatus'))}</h3>
                    <div class="space-y-2 mt-3 text-sm text-dark-300">
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('configPath'))}</span><span class="font-mono text-dark-200">${escapeHtml(status.config_path || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('authPath'))}</span><span class="font-mono text-dark-200">${escapeHtml(status.auth_path || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('authModeLabel'))}</span><span class="font-mono ${status.auth_mode === 'official_oauth' ? 'text-emerald-400' : 'text-amber-400'}">${escapeHtml(status.auth_mode || 'none')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('modelProviderLabel'))}</span><span class="font-mono text-dark-200">${escapeHtml(providerDisplay)}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('modelLabelShort'))}</span><span class="font-mono text-dark-200">${escapeHtml(effectiveModel)}</span></div>
                    </div>
                </div>

                ${renderCodexEnhancementModeCard(status)}
                ${renderCodexInjectionCard(status)}

                ${renderCodexConnectionModeCard(formStartMode, formProxyBaseUrl, formProxyModel, formPreserveAuth, proxyBackoffNote)}

                ${renderPermissionsAudit(status.permissions || {}, codexIntegrationState.permissionsPreview)}
            </div>

            <div class="space-y-4">
                ${renderCodexConnectionSummary(preview)}
                ${renderApprovalBridgePreviewCard(codexIntegrationState.approvalBridgePreview)}
                ${renderCodexRepairCard(status)}
                ${renderOfficialLoginResetCard(status)}

                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('backupsRollback'))}</h3>
                    <div class="space-y-2 mt-3">
                        ${backups.length ? backups.slice(0, 6).map(b => `
                            <div class="flex items-center justify-between text-sm">
                                <span class="font-mono text-dark-300">${escapeHtml(b.name)}</span>
                                <span class="text-dark-500">${escapeHtml(b.mtime ? b.mtime.slice(0, 19).replace('T', ' ') : '')}</span>
                            </div>
                        `).join('') : `<div class="text-sm text-dark-500">${escapeHtml(t('noBackupsYet'))}</div>`}
                    </div>
                    <div class="flex flex-wrap gap-2 mt-4">
                        <button onclick="restoreCodexConfig()" class="btn btn-warning text-xs">${escapeHtml(t('manualRestoreConfig'))}</button>
                        <button onclick="restoreCodexAuth()" class="btn btn-warning text-xs">${escapeHtml(t('manualRestoreAuth'))}</button>
                    </div>
                </div>
            </div>
        </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
}

/**
 * 渲染 Codex Config Diff 预览结果。
 * diff 行（added/changed/removed）hover 时向右微移并带光晕，
 * 帮助用户在密集文本中快速定位差异。
 */
function renderDiffPreview(preview) {
    const diff = preview.config_diff || {};
    const hasChanges = Object.keys(diff.added || {}).length || Object.keys(diff.changed || {}).length || Object.keys(diff.removed || {}).length;
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('diffPreview'))}</h3>
                ${hasChanges ? renderStatusPill('pending', t('changesPending'), 'amber') : renderStatusPill('clean', t('noChanges'), 'emerald')}
            </div>
            ${(preview.warnings || []).length ? `<div class="mt-3 space-y-1">${(preview.warnings || []).map(w => `<div class="text-xs text-amber-300">${escapeHtml(w)}</div>`).join('')}</div>` : ''}
            <div class="mt-3 space-y-2 text-sm">
                ${Object.entries(diff.added || {}).map(([k, v]) => `<div class="diff-added">+ ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${Object.entries(diff.changed || {}).map(([k, v]) => `<div class="diff-changed">~ ${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v.old))} → ${escapeHtml(JSON.stringify(v.new))}</div>`).join('')}
                ${Object.entries(diff.removed || {}).map(([k, v]) => `<div class="diff-removed">- ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${!hasChanges ? `<div class="text-dark-500">${escapeHtml(t('noDifferencesConfig'))}</div>` : ''}
            </div>
            <div class="mt-3 text-xs text-dark-500">
                ${escapeHtml(t('preserveOfficialOAuthStatus'))}: ${escapeHtml(preview.preserve_official_oauth ? t('yes') : t('no'))}
                &middot; ${escapeHtml(t('restartRequiredStatus'))}: ${escapeHtml(preview.restart_required ? t('yes') : t('no'))}
            </div>
        </div>
    `;
}

function renderCodexConnectionSummary(preview) {
    if (codexIntegrationState.previewChecking) {
        return `
            <div class="card">
                <div class="flex items-center justify-between gap-3">
                    <h3 class="card-title">${escapeHtml(t('codexConnectionSummary'))}</h3>
                    ${renderStatusPill('checking', t('checking'), 'accent')}
                </div>
                <div class="text-sm text-dark-500 mt-3">${escapeHtml(t('connectionCheckRunning'))}</div>
            </div>
        `;
    }
    if (codexIntegrationState.previewError) {
        return `
            <div class="card">
                <div class="flex items-center justify-between gap-3">
                    <h3 class="card-title">${escapeHtml(t('codexConnectionSummary'))}</h3>
                    ${renderStatusPill('error', t('hasError'), 'red')}
                </div>
                <div class="text-sm text-red-300 mt-3">${escapeHtml(codexIntegrationState.previewError)}</div>
                <button onclick="previewCodexIntegration()" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('recheckConnection'))}</button>
            </div>
        `;
    }
    if (!preview) {
        return `
            <div class="card">
                <div class="flex items-center justify-between gap-3">
                    <h3 class="card-title">${escapeHtml(t('codexConnectionSummary'))}</h3>
                    ${renderStatusPill('pending', t('notTested'), 'dark')}
                </div>
                <div class="text-sm text-dark-500 mt-3">${escapeHtml(t('connectionCheckPending'))}</div>
                <button onclick="previewCodexIntegration()" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('recheckConnection'))}</button>
            </div>
        `;
    }
    const diff = preview.config_diff || {};
    const hasChanges = Object.keys(diff.added || {}).length || Object.keys(diff.changed || {}).length || Object.keys(diff.removed || {}).length;
    const mode = preview.start_mode || 'preserve_login_proxy';
    const changedFields = [
        ...Object.keys(diff.added || {}),
        ...Object.keys(diff.changed || {}),
        ...Object.keys(diff.removed || {}),
    ];
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('codexConnectionSummary'))}</h3>
                ${hasChanges ? renderStatusPill('pending', t('changesPending'), 'amber') : renderStatusPill('clean', t('ready'), 'emerald')}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 text-sm">
                ${renderReadonlyKV(t('codexStartMode'), providerOptionLabel(mode))}
                ${renderReadonlyKV(t('preserveOfficialOAuth'), preview.preserve_official_oauth ? t('yes') : t('no'))}
                ${renderReadonlyKV(t('restartRequiredStatus'), preview.restart_required ? t('yes') : t('no'))}
                ${renderReadonlyKV(t('changesToApply'), hasChanges ? t('changedFieldCount', { count: changedFields.length }) : t('noChanges'))}
            </div>
            ${(preview.warnings || []).length ? `<div class="mt-3 space-y-1">${(preview.warnings || []).map(w => `<div class="text-xs text-amber-300">${escapeHtml(w)}</div>`).join('')}</div>` : ''}
            <details class="advanced-box mt-3">
                <summary>${escapeHtml(t('connectionDetails'))}</summary>
                <div class="mt-3 space-y-2 text-sm">
                    ${Object.entries(diff.added || {}).map(([k, v]) => `<div class="diff-added">+ ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                    ${Object.entries(diff.changed || {}).map(([k, v]) => `<div class="diff-changed">~ ${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v.old))} -> ${escapeHtml(JSON.stringify(v.new))}</div>`).join('')}
                    ${Object.entries(diff.removed || {}).map(([k, v]) => `<div class="diff-removed">- ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                    ${!hasChanges ? `<div class="text-dark-500">${escapeHtml(t('noDifferencesConfig'))}</div>` : ''}
                </div>
            </details>
            <button onclick="previewCodexIntegration()" class="btn btn-secondary text-xs mt-3">${escapeHtml(t('recheckConnection'))}</button>
        </div>
    `;
}

function renderApprovalBridgePreviewCard(preview) {
    const decision = codexIntegrationState.approvalBridgeDecision || {};
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('approvalBridgeDryRun'))}</h3>
                ${renderStatusPill('transport', t('previewOnly'), 'amber')}
            </div>
            <p class="text-xs text-dark-400 mt-1">${escapeHtml(t('approvalBridgeDesc'))}</p>
            <textarea id="ci-approval-bridge-json" class="input mt-3 w-full font-mono text-xs" rows="9">${escapeHtml(codexApprovalBridgeMessageText())}</textarea>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
                <select id="ci-approval-bridge-decision" class="input">
                    ${renderOption('ask_user', t('decisionAskUser'), (decision.decision || 'ask_user') === 'ask_user')}
                    ${renderOption('accept', t('decisionAccept'), decision.decision === 'accept')}
                    ${renderOption('decline', t('decisionDecline'), decision.decision === 'decline')}
                </select>
                <select id="ci-approval-bridge-risk" class="input">
                    ${renderOption('unknown', t('riskUnknown'), (decision.risk_level || 'unknown') === 'unknown')}
                    ${renderOption('low', t('riskLow'), decision.risk_level === 'low')}
                    ${renderOption('medium', t('riskMedium'), decision.risk_level === 'medium')}
                    ${renderOption('high', t('riskHigh'), decision.risk_level === 'high')}
                    ${renderOption('critical', t('riskCritical'), decision.risk_level === 'critical')}
                </select>
                <input id="ci-approval-bridge-reason" class="input" value="${escapeAttr(decision.reason || t('previewOnly'))}" placeholder="${escapeAttr(t('decisionReason'))}">
            </div>
            <button onclick="previewCodexApprovalBridge()" class="btn btn-secondary mt-3">${escapeHtml(t('previewApprovalBridge'))}</button>
            ${preview ? renderApprovalBridgePreviewResult(preview) : `<div class="text-xs text-dark-500 mt-3">${escapeHtml(t('noApprovalBridgePreviewYet'))}</div>`}
        </div>
    `;
}

function renderApprovalBridgePreviewResult(preview) {
    const action = preview.broker_action || {};
    const response = preview.jsonrpc_response || {};
    return `
        <div class="enhance-status-strip mt-3">
            ${renderStatusPill('method', preview.method || t('statusUnknown'), preview.success ? 'accent' : 'amber')}
            ${renderStatusPill('action', action.kind || t('statusUnknown'), action.kind ? 'emerald' : 'amber')}
            ${renderStatusPill('live', preview.live_transport_connected ? t('connectedLabel') : t('previewOnly'), preview.live_transport_connected ? 'emerald' : 'amber')}
        </div>
        <div class="grid grid-cols-1 gap-3 mt-3">
            <div>
                <div class="text-xs text-dark-400 mb-1">${escapeHtml(t('brokerAction'))}</div>
                <pre class="preview-code">${escapeHtml(JSON.stringify(action, null, 2))}</pre>
            </div>
            <div>
                <div class="text-xs text-dark-400 mb-1">${escapeHtml(t('simulatedJsonRpcResponse'))}</div>
                <pre class="preview-code">${escapeHtml(JSON.stringify(response, null, 2))}</pre>
            </div>
        </div>
    `;
}

function renderPermissionsAudit(current, preview) {
    const issues = current.issues || [];
    const warnings = current.warnings || [];
    const recommendations = current.recommendations || [];
    const desired = preview && preview.desired ? preview.desired : null;
    const previewDiff = preview ? (preview.config_diff || {}) : {};
    const previewHasChanges = preview
        ? Object.keys(previewDiff.added || {}).length || Object.keys(previewDiff.changed || {}).length || Object.keys(previewDiff.removed || {}).length
        : false;
    const issueColor = current.issue_count ? 'amber' : 'emerald';
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">${escapeHtml(t('approvalSandboxAudit'))}</h3>
                ${renderStatusPill('sandbox', current.issue_count ? t('issueCount', { count: current.issue_count }) : t('cleanStatus'), issueColor)}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 text-sm">
                ${renderReadonlyKV(t('approvalPolicy'), current.approval_policy || t('defaultValueLabel'))}
                ${renderReadonlyKV(t('sandboxMode'), current.sandbox_mode || t('derivedDefaultValue'))}
                ${renderReadonlyKV(t('defaultPermissions'), current.default_permissions || t('noneValueLabel'))}
                ${renderReadonlyKV(t('windowsSandbox'), current.windows_sandbox || t('defaultValueLabel'))}
                ${renderReadonlyKV(t('networkAccess'), (current.sandbox_workspace_write || {}).network_access ? t('yes') : t('no'))}
                ${renderReadonlyKV(t('fullAccessDetected'), current.effective_full_access ? t('yes') : t('no'))}
            </div>
            ${issues.length ? `<div class="mt-3 space-y-1">${issues.map(issue => `
                <div class="text-xs ${issue.severity === 'error' || issue.severity === 'high' ? 'text-red-300' : 'text-amber-300'}">
                    ${escapeHtml(issue.field || t('configLabel'))}: ${escapeHtml(issue.message || '')}
                </div>
            `).join('')}</div>` : `<div class="mt-3 text-xs text-emerald-300">${escapeHtml(t('noSandboxCorruption'))}</div>`}
            ${warnings.length ? `<div class="mt-2 space-y-1">${warnings.map(w => `<div class="text-xs text-amber-300">${escapeHtml(w)}</div>`).join('')}</div>` : ''}
            ${recommendations.length ? `<div class="mt-2 space-y-1">${recommendations.map(r => `<div class="text-xs text-dark-400">${escapeHtml(r)}</div>`).join('')}</div>` : ''}

            <details class="advanced-box mt-4">
                <summary>${escapeHtml(t('sandboxPreview'))}</summary>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('approvalPolicy'))}</label>
                        <select id="ci-approval-policy" class="input mt-1 w-full">
                            ${renderOption('', t('keepCurrent'), true)}
                            ${renderOption('untrusted', t('approvalPolicyUntrusted'), false)}
                            ${renderOption('on-request', t('approvalPolicyOnRequest'), false)}
                            ${renderOption('never', t('approvalPolicyNever'), false)}
                            ${renderOption('on-failure', t('approvalPolicyOnFailure'), false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('sandboxMode'))}</label>
                        <select id="ci-sandbox-mode" class="input mt-1 w-full">
                            ${renderOption('', t('keepCurrent'), true)}
                            ${renderOption('read-only', t('sandboxReadOnly'), false)}
                            ${renderOption('workspace-write', t('sandboxWorkspaceWrite'), false)}
                            ${renderOption('danger-full-access', t('sandboxDangerFullAccess'), false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('windowsSandbox'))}</label>
                        <select id="ci-windows-sandbox" class="input mt-1 w-full">
                            ${renderOption('', t('keepCurrent'), true)}
                            ${renderOption('disabled', t('windowsSandboxDisabled'), false)}
                            ${renderOption('restricted-token', t('windowsSandboxRestricted'), false)}
                            ${renderOption('elevated', t('windowsSandboxElevated'), false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">${escapeHtml(t('defaultPermissions'))}</label>
                        <input id="ci-default-permissions" class="input mt-1 w-full" placeholder="${escapeAttr(t('keepCurrentWorkspace'))}">
                    </div>
                    <div class="md:col-span-2">
                        <label class="text-xs text-dark-400">${escapeHtml(t('writableRoots'))}</label>
                        <textarea id="ci-writable-roots" class="input mt-1 w-full min-h-[72px]" placeholder="${escapeAttr(t('writableRootsPlaceholder'))}"></textarea>
                    </div>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-network-access" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>${escapeHtml(t('workspaceNetworkAccess'))}</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-exclude-tmpdir-env" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>${escapeHtml(t('excludeTmpdirEnvVar'))}</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-exclude-slash-tmp" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>${escapeHtml(t('excludeSlashTmp'))}</span>
                    </label>
                </div>
                <div class="flex flex-wrap gap-2 mt-3">
                    <button onclick="previewCodexPermissions()" class="btn btn-secondary">${escapeHtml(t('previewSandboxDiff'))}</button>
                    <button onclick="repairCodexSandboxPermissions()" class="btn btn-warning">${escapeHtml(t('repairCodexSandboxPermissions'))}</button>
                </div>
                ${preview ? renderPermissionsPreviewResult(preview, desired, previewHasChanges) : `<div class="text-xs text-dark-500 mt-3">${escapeHtml(t('previewOnlyNoConfigWrite'))}</div>`}
            </details>
            <details class="advanced-box mt-3">
                <summary>${escapeHtml(t('verifiedSourceNotes'))}</summary>
                <div class="mt-2 space-y-1">
                    ${(current.source_notes || []).map(note => `<div class="text-xs text-dark-400">${escapeHtml(note)}</div>`).join('')}
                </div>
            </details>
        </div>
    `;
}

function renderReadonlyKV(label, value) {
    return `
        <div class="rounded-md border border-dark-800 bg-dark-900/50 px-3 py-2">
            <div class="text-xs text-dark-500">${escapeHtml(label)}</div>
            <div class="font-mono text-xs text-dark-200 break-all mt-1">${escapeHtml(value)}</div>
        </div>
    `;
}

function renderOption(value, label, selected) {
    return `<option value="${escapeHtml(value)}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
}

function renderPermissionsPreviewResult(preview, desired, hasChanges) {
    const diff = preview.config_diff || {};
    const desiredIssues = desired ? (desired.issues || []) : [];
    return `
        <div class="mt-3 rounded-md border border-dark-800 bg-dark-900/50 p-3">
            <div class="flex items-center justify-between gap-3">
                <span class="text-sm font-medium text-dark-200">${escapeHtml(t('sandboxDiffPreview'))}</span>
                ${hasChanges ? renderStatusPill('pending', t('changesPending'), 'amber') : renderStatusPill('clean', t('noChanges'), 'emerald')}
            </div>
            <div class="mt-2 space-y-1 text-xs">
                ${Object.entries(diff.added || {}).map(([k, v]) => `<div class="diff-added">+ ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${Object.entries(diff.changed || {}).map(([k, v]) => `<div class="diff-changed">~ ${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v.old))} -> ${escapeHtml(JSON.stringify(v.new))}</div>`).join('')}
                ${Object.entries(diff.removed || {}).map(([k, v]) => `<div class="diff-removed">- ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${!hasChanges ? `<div class="text-dark-500">${escapeHtml(t('noDifferencesConfig'))}</div>` : ''}
            </div>
            ${desiredIssues.length ? `<div class="mt-2 space-y-1">${desiredIssues.map(issue => `<div class="text-xs text-amber-300">${escapeHtml(issue.field || t('configLabel'))}: ${escapeHtml(issue.message || '')}</div>`).join('')}</div>` : ''}
        </div>
    `;
}

function renderDiffPreviewShell() {
    return `
        <div class="card">
            <h3 class="card-title">${escapeHtml(t('codexConnectionSummary'))}</h3>
            <div class="text-sm text-dark-500 mt-3">${escapeHtml(t('connectionCheckPending'))}</div>
        </div>
    `;
}

// Close create-provider modal on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        hideCreateProviderModal();
    }
});

// Close create-provider modal on overlay click
document.addEventListener('click', (e) => {
    const modal = document.getElementById('create-provider-modal');
    if (e.target === modal) {
        hideCreateProviderModal();
    }
});
