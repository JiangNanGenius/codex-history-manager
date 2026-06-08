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
    focus_provider_id: '',
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

const NATIVE_LOCKED_CAPABILITIES = {
    text: true,
    vision: true,
    tools: true,
    custom_tools: true,
    reasoning: true,
    streaming: true,
    compact: true,
    images: true,
    videos: true,
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
    await ensureProviderData();
    renderEnhanceOverview();
    setStatus('Overview loaded');
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
    const mediaProviders = providers.filter(p => p.media_profile && (p.media_profile.default_image_provider || p.media_profile.default_video_provider)).length;

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
        return profile.default_image_provider || profile.default_video_provider;
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
                <button onclick="createBlankProvider()" class="btn btn-primary">${escapeHtml(t('newProvider'))}</button>
                <button onclick="exportProviderBundle()" class="btn btn-secondary">${escapeHtml(t('exportRedacted'))}</button>
            </div>
        </div>

        <div class="grid grid-cols-1 2xl:grid-cols-[320px_1fr_420px] gap-4 mt-6">
            <div class="space-y-4">
                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('providerList'))}</h3>
                    <div class="space-y-2 mt-3">
                        ${providers.map(renderProviderListItem).join('') || renderEmptyState(t('noProvidersYet'))}
                    </div>
                </div>
                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('presetImport'))}</h3>
                    <div class="space-y-2 mt-3">
                        ${(providerState.presets || []).slice(0, 6).map(renderCompactPresetButton).join('')}
                    </div>
                    <button onclick="navigateTo('quick-setup')" class="btn btn-ghost text-xs mt-3">${escapeHtml(t('openFullWizard'))}</button>
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
        ${renderCatalogPreviewPanel()}
        ${renderProxyRouteTestCard()}
        ${renderRouteSimulatorShell()}
        ${renderCodexDiffPreviewShell()}
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
    /**
     * 渲染 Provider 编辑器（三栏中间栏）。
     *
     * 设计意图：
     *   - 表单字段较多，未使用 stagger-item（避免过多项同时动画导致眩晕），
     *     但外层 .card 仍参与兜底动画。
     *   - Models 使用文本区 + parseModelsText 解析：用户可批量粘贴模型列表，
     *     格式为 "id|display_name|context_window|selected"，比逐个表单字段
     *     更高效。
     *   - Advanced 折叠面板：默认折叠复杂字段（headers、capabilities、media profile），
     *     降低新手认知负担，高级用户可展开编辑。
     */
    const modelsText = (provider.models || []).map(m => [
        m.id || '',
        m.display_name || '',
        m.context_window || 0,
        m.selected ? 'selected' : '',
        m.api_format || '',
        m.native_approval ? 'native_approval' : '',
    ].join('|')).join('\n');
    const approvalProfile = provider.approval_profile || {};
    const mediaProfile = provider.media_profile || {};
    const proxyProfile = provider.proxy_profile || {};
    const showMediaAsyncFields = shouldShowMediaAsyncFields(mediaProfile, provider.api_format);
    const providerReadOnly = isCodexLoginProvider(provider);
    const capabilityLocked = isNativeCapabilityLocked(provider);
    const capabilitySource = capabilityLocked ? nativeLockedCapabilities(provider.capabilities || {}) : (provider.capabilities || {});

    return `
        <div class="card">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-3">
                <div>
                    <h3 class="card-title">${escapeHtml(t('editProvider'))}</h3>
                    <div class="text-xs text-dark-500 font-mono">${escapeHtml(provider.id)}</div>
                </div>
                ${renderProviderStatusStrip(provider)}
            </div>

            <div id="provider-form-error" class="hidden mt-3 text-sm text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-3"></div>
            ${providerReadOnly ? `<div class="mt-3 text-xs text-amber-200 bg-amber-950/25 border border-amber-700/50 rounded-lg p-3">${escapeHtml(t('codexLoginProviderReadOnly'))}</div>` : ''}

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                ${renderInput('provider-display-name', t('displayName'), provider.display_name, 'text', providerReadOnly)}
                ${renderInput('provider-short-alias', t('shortAlias'), provider.short_alias, 'text', providerReadOnly)}
                ${renderInput('provider-base-url', t('baseUrl'), provider.base_url, 'text', providerReadOnly)}
                ${renderSelect('provider-api-format', t('apiFormat'), provider.api_format, [
                    'openai_responses',
                    'openai_chat',
                    'openai_images',
                    'openai_videos',
                    'openai_compatible',
                    'anthropic',
                    'custom',
                ], 'syncResponsesModeControls(true); syncMediaModeControls(true)', providerReadOnly)}
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

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="provider-enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${provider.enabled ? 'checked' : ''}>
                    <span>${escapeHtml(t('enabledLabel'))}</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="provider-fallback-enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${provider.fallback_enabled ? 'checked' : ''}>
                    <span>${escapeHtml(t('fallbackEnabled'))}</span>
                </label>
            </div>

            <details class="advanced-box mt-4">
                <summary>${escapeHtml(t('advanced'))}</summary>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderInput('provider-api-key', t('apiKey'), provider.api_key || '', 'password', providerReadOnly)}
                    ${renderSelect('provider-auth-mode', t('authMode'), provider.auth_mode, [
                        'provider_api_key',
                        'global_auth_json',
                        'official_oauth',
                        'no_auth',
                    ], '', providerReadOnly)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('provider-headers-json', t('headersJson'), JSON.stringify(provider.headers || {}, null, 2), 7, '', providerReadOnly)}
                    ${renderTextarea('provider-models-text', t('modelsText'), modelsText, 7, t('modelsTextHint'), providerReadOnly)}
                </div>
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
                <div class="flex flex-wrap gap-2 mt-3 ${providerReadOnly ? 'hidden' : ''}">
                    <button data-bulk-action="select_all" onclick="runBulkModelAction('select_all')" class="btn btn-secondary text-xs">${escapeHtml(t('selectAll'))}</button>
                    <button data-bulk-action="deselect_all" onclick="runBulkModelAction('deselect_all')" class="btn btn-secondary text-xs">${escapeHtml(t('deselectAll'))}</button>
                    <button data-bulk-action="select_vision" onclick="runBulkModelAction('select_vision')" class="btn btn-secondary text-xs">${escapeHtml(t('selectVision'))}</button>
                    <button data-bulk-action="select_high_context" onclick="runBulkModelAction('select_high_context')" class="btn btn-secondary text-xs">${escapeHtml(t('selectHighContext'))}</button>
                    <button data-bulk-action="select_low_cost" onclick="runBulkModelAction('select_low_cost')" class="btn btn-secondary text-xs">${escapeHtml(t('selectLowCost'))}</button>
                    <button data-bulk-action="add_selected_to_amr" onclick="addSelectedModelsToAmr()" class="btn btn-secondary text-xs">${escapeHtml(t('addToAmr'))}</button>
                </div>
                <div id="bulk-action-error" class="hidden mt-2 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('cap-text', t('textCapability'), capabilitySource.text, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-vision', t('visionInputCapability'), capabilitySource.vision, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-tools', t('toolsCapability'), capabilitySource.tools, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-custom-tools', t('customToolsCapability'), capabilitySource.custom_tools, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-reasoning', t('reasoningCapability'), capabilitySource.reasoning, '', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-images', t('imagesCapability'), capabilitySource.images, 'syncMediaModeControls(true)', capabilityLocked || providerReadOnly)}
                    ${renderCapabilityToggle('cap-videos', t('videosCapability'), capabilitySource.videos, 'syncMediaModeControls(true)', capabilityLocked || providerReadOnly)}
                </div>
                ${capabilityLocked ? `<div id="native-capability-lock-note" class="mt-3 text-xs text-emerald-200 bg-emerald-950/20 border border-emerald-700/40 rounded-lg p-3">${escapeHtml(t('nativeCapabilitiesLocked'))}</div>` : ''}
                ${renderResponsesModeSegment(provider, providerReadOnly)}
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('responses-domestic', t('domesticResponses'), provider.responses_profile && provider.responses_profile.domestic_responses)}
                    ${renderCapabilityToggle('responses-partial', t('partialResponsesCompatibility'), provider.responses_profile && provider.responses_profile.partial_compatibility)}
                    ${renderCapabilityToggle('responses-requires-adapter', t('responsesAdapterRequired'), provider.responses_profile && provider.responses_profile.requires_adapter)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderInput('responses-profile-id', t('responsesProfileId'), provider.responses_profile && provider.responses_profile.profile_id)}
                    ${renderInput('responses-docs-url', t('responsesDocsUrl'), provider.responses_profile && provider.responses_profile.verified_docs_url)}
                    ${renderInput('responses-unsupported', t('unsupportedFields'), provider.responses_profile && (provider.responses_profile.unsupported_fields || []).join(', '))}
                </div>
                ${renderApprovalModeSegment(approvalProfile)}
                <div id="approval-auto-fields" class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4 ${approvalModeFromProfile(approvalProfile) === 'proxy_auto_approve' ? '' : 'hidden'}">
                    ${renderInput('approval-reviewer-model', t('reviewerModel'), approvalProfile.reviewer_model || '')}
                    ${renderInput('approval-allowed-actions', t('allowedActions'), (approvalProfile.allowed_actions || []).join(', '))}
                    ${renderSelect('approval-error-policy', t('reviewErrorPolicy'), approvalProfile.on_review_error || 'decline', [
                        'decline',
                        'ask_user',
                        'allow',
                    ])}
                    ${renderInput('approval-timeout-ms', t('timeoutMs'), approvalProfile.timeout_ms || 90000, 'number')}
                    ${renderInput('approval-max-retries', t('maxRetries'), approvalProfile.max_retries || 1, 'number')}
                    ${renderCapabilityToggle('approval-audit-decisions', t('auditDecisions'), approvalProfile.audit_decisions !== false)}
                    ${renderCapabilityToggle('approval-auto-accept-low-risk', t('autoAcceptLowRisk'), approvalProfile.auto_accept_low_risk !== false)}
                    ${renderCapabilityToggle('approval-auto-decline-high-risk', t('autoDeclineHighRisk'), approvalProfile.auto_decline_high_risk !== false)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-4">
                    ${renderCapabilityToggle('media-default-image', t('defaultImageProvider'), mediaProfile.default_image_provider, 'syncMediaModeControls(true)')}
                    ${renderCapabilityToggle('media-default-video', t('defaultVideoProvider'), mediaProfile.default_video_provider, 'syncMediaModeControls(true)')}
                </div>
                ${renderMediaModeSegment(mediaProfile)}
                ${renderMediaRoutingHint(provider)}
                <div id="media-async-fields" class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4 ${showMediaAsyncFields ? '' : 'hidden'}">
                    ${renderCapabilityToggle('media-async-submit', t('asyncSubmit'), mediaProfile.async_submit)}
                    ${renderCapabilityToggle('media-poll-required', t('pollRequired'), mediaProfile.poll_required)}
                    ${renderCapabilityToggle('media-cancel-supported', t('cancelSupported'), mediaProfile.cancel_supported)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('media-image-overrides-json', t('imageOverridesJson'), JSON.stringify(mediaProfile.image_model_overrides || {}, null, 2), 5)}
                    ${renderTextarea('media-video-overrides-json', t('videoOverridesJson'), JSON.stringify(mediaProfile.video_model_overrides || {}, null, 2), 5)}
                </div>
                <div class="mt-4">
                    ${renderTextarea('provider-quota-json', t('quotaCheckJson'), JSON.stringify(provider.quota_check || {}, null, 2), 8)}
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
    const checks = Array.isArray(result.checks) ? result.checks : [];
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
    const capabilityOptions = ['text', 'vision', 'tools', 'custom_tools', 'reasoning', 'images', 'videos']
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
        [t('mediaPriceVideoJob'), ['per_video_job', 'video_job', 'video_per_job', 'video_generation']],
        [t('mediaPriceVideoSecond'), ['per_video_second', 'video_second', 'video_per_second', 'video_sec']],
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
                ${renderRouteCapabilityToggle('route-sim-cap-tools', 'tools', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-custom-tools', 'custom_tools', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-reasoning', 'reasoning', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-images', 'images', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-videos', 'videos', false)}
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
    renderProvidersPage();
    await refreshSelectedQuotaCache();
    renderProvidersPage();
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

function readProviderForm(existing) {
    try {
        const headers = JSON.parse(document.getElementById('provider-headers-json')?.value || '{}');
        const imageModelOverrides = JSON.parse(document.getElementById('media-image-overrides-json')?.value || '{}');
        const videoModelOverrides = JSON.parse(document.getElementById('media-video-overrides-json')?.value || '{}');
        const quotaCheck = JSON.parse(document.getElementById('provider-quota-json')?.value || '{}');
        const aliases = JSON.parse(document.getElementById('provider-aliases-json')?.value || '{}');
        const aliasPatterns = JSON.parse(document.getElementById('provider-alias-patterns-json')?.value || '[]');
        const models = parseModelsText(document.getElementById('provider-models-text')?.value || '', existing.models || []);
        const approvalMode = getSelectedApprovalMode();
        const mediaMode = getSelectedMediaMode();
        const mediaAsyncVisible = !document.getElementById('media-async-fields')?.classList.contains('hidden');
        const responsesMode = getSelectedResponsesMode();
        const apiFormat = document.getElementById('provider-api-format')?.value || 'openai_responses';
        const lockedCapabilities = apiFormat === 'openai_responses' && responsesMode === 'native';
        return {
            ...existing,
            display_name: document.getElementById('provider-display-name')?.value || '',
            short_alias: document.getElementById('provider-short-alias')?.value || '',
            base_url: document.getElementById('provider-base-url')?.value || '',
            api_format: apiFormat,
            country_region: document.getElementById('provider-country-region')?.value || '',
            native_currency: document.getElementById('provider-native-currency')?.value || 'USD',
            catalog_visibility: document.getElementById('provider-visibility')?.value || 'focused_only',
            user_agent: document.getElementById('provider-user-agent')?.value || '',
            enabled: document.getElementById('provider-enabled')?.checked || false,
            fallback_enabled: document.getElementById('provider-fallback-enabled')?.checked || false,
            api_key: document.getElementById('provider-api-key') ? document.getElementById('provider-api-key').value : (existing.api_key || ''),
            auth_mode: document.getElementById('provider-auth-mode')?.value || 'provider_api_key',
            headers,
            aliases,
            alias_patterns: aliasPatterns,
            quota_check: quotaCheck,
            models,
            capabilities: lockedCapabilities ? nativeLockedCapabilities(existing.capabilities || {}) : {
                ...existing.capabilities,
                text: document.getElementById('cap-text')?.checked || false,
                vision: document.getElementById('cap-vision')?.checked || false,
                tools: document.getElementById('cap-tools')?.checked || false,
                custom_tools: document.getElementById('cap-custom-tools')?.checked || false,
                reasoning: document.getElementById('cap-reasoning')?.checked || false,
                images: document.getElementById('cap-images')?.checked || false,
                videos: document.getElementById('cap-videos')?.checked || false,
            },
            approval_profile: {
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
            media_profile: {
                ...existing.media_profile,
                default_image_provider: document.getElementById('media-default-image')?.checked || false,
                default_video_provider: document.getElementById('media-default-video')?.checked || false,
                openai_compatible_media: mediaMode === 'openai_compatible',
                adapter_required: mediaMode === 'adapter_required',
                async_submit: mediaAsyncVisible && (document.getElementById('media-async-submit')?.checked || false),
                poll_required: mediaAsyncVisible && (document.getElementById('media-poll-required')?.checked || false),
                cancel_supported: mediaAsyncVisible && (document.getElementById('media-cancel-supported')?.checked || false),
                image_model_overrides: imageModelOverrides,
                video_model_overrides: videoModelOverrides,
            },
            responses_profile: {
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
            const capabilityOverrides = {
                ...(existing.capability_overrides || {}),
                ...(hasNativeApprovalToken ? { native_approval: nativeApproval } : {}),
            };
            return {
                ...existing,
                id: parts[0] || 'model-id',
                display_name: parts[1] || parts[0] || 'Model',
                context_window: parseInt(parts[2], 10) || 0,
                selected: parts[3] === 'selected' || parts[3] === 'true',
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
    return { ...(existing || {}), ...NATIVE_LOCKED_CAPABILITIES };
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

function renderApprovalModeSegment(profile) {
    const mode = approvalModeFromProfile(profile || {});
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
                    <label class="segmented-option ${mode === value ? 'active' : ''}">
                        <input
                            type="radio"
                            name="approval-mode"
                            value="${escapeAttr(value)}"
                            onchange="syncApprovalModeControls()"
                            ${mode === value ? 'checked' : ''}
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
        || String(apiFormat || '') === 'openai_videos'
        || Boolean(profile && (profile.async_submit || profile.poll_required || profile.cancel_supported));
}

function providerMediaRoutingHint(provider) {
    const profile = provider && provider.media_profile ? provider.media_profile : {};
    const caps = provider && provider.capabilities ? provider.capabilities : {};
    const apiFormat = String(provider && provider.api_format ? provider.api_format : '');
    const wantsMedia = Boolean(caps.images || caps.videos || profile.default_image_provider || profile.default_video_provider);
    const hasMediaRoute = Boolean(
        profile.openai_compatible_media
        || profile.adapter_required
        || apiFormat === 'openai_images'
        || apiFormat === 'openai_videos'
    );
    if (wantsMedia && !hasMediaRoute) {
        return t('mediaCapabilityHint');
    }
    if (hasMediaRoute && !wantsMedia && apiFormat !== 'openai_images' && apiFormat !== 'openai_videos') {
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

function renderMediaModeSegment(profile) {
    const mode = mediaModeFromProfile(profile || {});
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
                    <label class="segmented-option ${mode === value ? 'active' : ''}">
                        <input
                            type="radio"
                            name="media-mode"
                            value="${escapeAttr(value)}"
                            onchange="syncMediaModeControls(true)"
                            ${mode === value ? 'checked' : ''}
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
        || apiFormat === 'openai_videos'
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
        || document.getElementById('cap-videos')?.checked
        || document.getElementById('media-default-image')?.checked
        || document.getElementById('media-default-video')?.checked
    );
    const hasMediaRoute = selectedMode !== 'disabled' || apiFormat === 'openai_images' || apiFormat === 'openai_videos';
    let message = '';
    if (wantsMedia && !hasMediaRoute) {
        message = t('mediaCapabilityHint');
    } else if (hasMediaRoute && !wantsMedia && apiFormat !== 'openai_images' && apiFormat !== 'openai_videos') {
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
    return ['text', 'vision', 'tools', 'custom_tools', 'reasoning', 'images', 'videos', 'native_approval']
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
    if (isCodexLoginProvider(provider)) {
        if (errorEl) {
            errorEl.textContent = t('codexLoginProviderReadOnly');
            errorEl.classList.remove('hidden');
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
    loading: false,
};

function getCodexIntegrationProxyBaseUrl() {
    const status = codexIntegrationState.status || {};
    const proxyStatus = status.proxy_status || {};
    if (status.default_proxy_base_url) return status.default_proxy_base_url;
    if (proxyStatus.base_url) return proxyStatus.base_url;
    if (proxyStatus.port) return `http://127.0.0.1:${proxyStatus.port}/v1`;
    return 'http://127.0.0.1:8080/v1';
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
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || getCodexIntegrationProxyBaseUrl();
    const proxyModel = document.getElementById('ci-proxy-model')?.value || 'auto';
    const preserveAuth = document.getElementById('ci-preserve-auth')?.checked !== false;
    const startMode = document.getElementById('ci-start-mode')?.value || 'preserve_login_proxy';
    const draft = {
        proxy_base_url: proxyBaseUrl,
        proxy_model: proxyModel,
        preserve_official_auth: preserveAuth,
        start_mode: startMode,
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
            showToast(t('applySuccessRestart'), 'success');
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
        const startMode = document.getElementById('ci-start-mode')?.value || 'preserve_login_proxy';
        const preserveAuth = document.getElementById('ci-preserve-auth')?.checked !== false;
        setStatus(t('codexStartRequested'));
        const result = await api('/api/codex/start', {
            method: 'POST',
            body: JSON.stringify({
                start_mode: startMode,
                preserve_official_auth: preserveAuth,
            }),
        });
        showToast(result.message || t('codexStartRequested'), result.success ? 'success' : 'error');
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('codexStartFailed') + err.message, 'error');
    }
}

async function startOfficialCodex() {
    if (!confirm(t('confirmStartOfficialCodex'))) return;
    try {
        setStatus(t('codexOfficialStartRequested'));
        const result = await api('/api/codex/start', {
            method: 'POST',
            body: JSON.stringify({ start_mode: 'official_direct', official_mode: true }),
        });
        showToast(result.message || t('codexOfficialStartRequested'), result.success ? 'success' : 'error');
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast(t('codexOfficialStartFailed') + err.message, 'error');
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
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('modelProviderLabel'))}</span><span class="font-mono text-dark-200">${escapeHtml((status.config || {}).model_provider || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">${escapeHtml(t('modelLabelShort'))}</span><span class="font-mono text-dark-200">${escapeHtml((status.config || {}).model || '-')}</span></div>
                    </div>
                </div>

                ${renderCodexEnhancementModeCard(status)}

                <div class="card">
                    <h3 class="card-title">${escapeHtml(t('localProxySettings'))}</h3>
                    <div class="grid grid-cols-1 gap-4 mt-3">
                        ${renderSelect('ci-start-mode', t('codexStartMode'), formStartMode, [
                            'preserve_login_proxy',
                            'official_direct',
                            'proxy_injection',
                        ], 'scheduleCodexConnectionCheck()')}
                        <p class="text-xs text-dark-500 -mt-2">${escapeHtml(t('codexStartModeDesc'))}</p>
                        ${renderInput('ci-proxy-base-url', t('proxyBaseUrl'), formProxyBaseUrl, 'text', false, 'scheduleCodexConnectionCheck()')}
                        ${renderInput('ci-proxy-model', t('proxyModel'), formProxyModel, 'text', false, 'scheduleCodexConnectionCheck()')}
                        <label class="flex items-center gap-2 text-sm cursor-pointer">
                            <input id="ci-preserve-auth" type="checkbox" onchange="scheduleCodexConnectionCheck()" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${formPreserveAuth ? 'checked' : ''}>
                            <span>${escapeHtml(t('preserveOfficialOAuth'))}</span>
                        </label>
                    </div>
                    ${proxyBackoffNote}
                    <div class="flex flex-wrap gap-2 mt-4">
                        <button onclick="applyCodexIntegration()" class="btn btn-warning">${escapeHtml(t('manualApplyCodexConfig'))}</button>
                        <button onclick="startCodexWithSelectedMode()" class="btn btn-primary">${escapeHtml(t('startCodexSelectedMode'))}</button>
                        <button onclick="startOfficialCodex()" class="btn btn-secondary">${escapeHtml(t('startOfficialCodex'))}</button>
                    </div>
                    <p class="text-xs text-dark-500 mt-3">${escapeHtml(t('officialCodexStartDesc'))}</p>
                </div>

                ${renderPermissionsAudit(status.permissions || {}, codexIntegrationState.permissionsPreview)}
            </div>

            <div class="space-y-4">
                ${renderCodexConnectionSummary(preview)}
                ${renderApprovalBridgePreviewCard(codexIntegrationState.approvalBridgePreview)}

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
                <button onclick="previewCodexPermissions()" class="btn btn-secondary mt-3">${escapeHtml(t('previewSandboxDiff'))}</button>
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
