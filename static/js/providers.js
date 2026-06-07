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
    quotaPreview: null,
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
    await Promise.all([refreshCatalogPreview(), refreshProxyStatus()]);
    renderProvidersPage();
    setStatus('Providers loaded');
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
        providerState.presets = presetsData.presets || [];
    } catch (err) {
        providerState.draftError = err.message;
        showToast('加载 provider 数据失败：' + err.message, 'error');
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
        providerState.catalogPreview = { entries: [], route_explanation: ['Preview failed: ' + err.message] };
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
                <h2 class="text-2xl font-semibold text-white">Codex Enhance Manager</h2>
                <p class="text-sm text-dark-400 mt-1">本地增强控制中心：先建立 provider registry、预览和 dry-run 护栏，再接入 UMC、AMR、proxy、media、cost。</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('enabled', enabledCount + ' providers enabled', 'emerald')}
                ${renderStatusPill('dry-run', 'Codex writes are preview-only', 'amber')}
                ${renderStatusPill('manual', 'Codex mutation tests are manual', 'accent')}
            </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mt-6">
            ${renderMetricCard('Providers', providers.length, enabledCount + ' enabled')}
            ${renderMetricCard('Always Visible', alwaysVisible, 'UMC pinned providers')}
            ${renderMetricCard('Selected Models', selectedModels, 'visible model picks')}
            ${renderMetricCard('Media Profiles', mediaProviders, 'image/video defaults')}
        </div>

        <div class="grid grid-cols-1 xl:grid-cols-3 gap-4 mt-6">
            <div class="card xl:col-span-2">
                <div class="flex items-center justify-between gap-3">
                    <h3 class="card-title">Implementation Shell</h3>
                    <button onclick="navigateTo('quick-setup')" class="btn btn-secondary text-xs">Quick Setup</button>
                </div>
                <div class="enhance-step-list mt-3">
                    ${renderStepRow('Provider Registry', '本地 JSON registry、schema version、CRUD、preset import、secret redaction。', true)}
                    ${renderStepRow('Unified Model Catalog Preview', '常驻显示、选中模型、focus provider 的最终 catalog 预览。', true)}
                    ${renderStepRow('Codex Config Diff Preview', '预留入口；真实写入前必须有 diff、backup、rollback。', false)}
                    ${renderStepRow('Route Simulator', 'Read-only AMR capability/context simulation with route explanation.', true)}
                </div>
            </div>
            <div class="card">
                <h3 class="card-title">Guardrails</h3>
                <div class="space-y-3 mt-3 text-sm text-dark-300">
                    <div class="guardrail-line stagger-item">读取型 Codex 检查可以在本窗口执行。</div>
                    <div class="guardrail-line stagger-item">会修改 Codex 状态的测试由用户手动执行。</div>
                    <div class="guardrail-line stagger-item">协议转换不猜测，先查官方源码和供应商文档。</div>
                    <div class="guardrail-line stagger-item">API key 只进本地配置，UI 和诊断默认脱敏。</div>
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
     * 渲染 Quick Setup 页面。
     * Preset card 与 Next Steps tile 均带 .stagger-item，配合交错延迟
     * 让导入流程显得有节奏感，而非一次性轰击视觉。
     */
    const root = document.getElementById('quick-setup-root');
    if (!root) return;
    const presets = providerState.presets || [];
    const domestic = presets.filter(p => p.category === 'domestic');
    const generic = presets.filter(p => p.category !== 'domestic');

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">Quick Setup</h2>
                <p class="text-sm text-dark-400 mt-1">Preset-first flow：先导入预设，再在 Providers 页面补 base URL、key、模型和高级字段。</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('preview', 'No Codex files are written', 'amber')}
                ${renderStatusPill('test', 'Tests validate current section only', 'emerald')}
            </div>
        </div>

        <div class="grid grid-cols-1 xl:grid-cols-2 gap-4 mt-6">
            <div class="card">
                <h3 class="card-title">Generic Presets</h3>
                <div class="preset-list mt-3">${generic.map(renderPresetCard).join('') || renderEmptyState('No generic presets')}</div>
            </div>
            <div class="card">
                <h3 class="card-title">Domestic Media/Text Presets</h3>
                <div class="preset-list mt-3">${domestic.map(renderPresetCard).join('') || renderEmptyState('No domestic presets')}</div>
            </div>
        </div>

        <div class="card mt-4">
            <h3 class="card-title">Next Steps</h3>
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
                ${renderPreviewTile('1', 'Import a preset', 'Creates a local provider record with redacted secret handling.')}
                ${renderPreviewTile('2', 'Edit provider', 'Set alias, currency, visibility, User-Agent, models, media profile.')}
                ${renderPreviewTile('3', 'Preview catalog', 'See final UMC catalog before any future Codex config write.')}
            </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
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
                <h2 class="text-2xl font-semibold text-white">Providers</h2>
                <p class="text-sm text-dark-400 mt-1">管理本地 provider registry。这里的 Test 是本地校验，不触发真实 provider 请求，也不写 Codex 配置。</p>
            </div>
            <div class="flex flex-wrap gap-2">
                <button onclick="createBlankProvider()" class="btn btn-primary">New Provider</button>
                <button onclick="exportProviderBundle()" class="btn btn-secondary">Export Redacted</button>
            </div>
        </div>

        <div class="grid grid-cols-1 2xl:grid-cols-[320px_1fr_420px] gap-4 mt-6">
            <div class="space-y-4">
                <div class="card">
                    <h3 class="card-title">Provider List</h3>
                    <div class="space-y-2 mt-3">
                        ${providers.map(renderProviderListItem).join('') || renderEmptyState('No providers yet. Import a preset or create one.')}
                    </div>
                </div>
                <div class="card">
                    <h3 class="card-title">Preset Import</h3>
                    <div class="space-y-2 mt-3">
                        ${(providerState.presets || []).slice(0, 6).map(renderCompactPresetButton).join('')}
                    </div>
                    <button onclick="navigateTo('quick-setup')" class="btn btn-ghost text-xs mt-3">Open full wizard</button>
                </div>
                ${renderProxyControlCard()}
            </div>

            <div class="space-y-4">
                ${selected ? renderProviderEditor(selected) : renderEmptyProviderEditor()}
            </div>

            <div class="space-y-4">
                ${renderCatalogPreviewPanel()}
                ${renderProxyRouteTestCard()}
                ${renderRouteSimulatorShell()}
                ${renderCodexDiffPreviewShell()}
            </div>
        </div>
        </div>
    `;
    if (typeof triggerStaggerAnimations === 'function') triggerStaggerAnimations(root);
    if (typeof attachRippleToButtons === 'function') attachRippleToButtons(root);
    syncApprovalModeControls();
    syncMediaModeControls();
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
        { value: 'hidden', label: 'hidden' },
        { value: 'focused_only', label: 'focused' },
        { value: 'always_visible', label: 'always' },
        { value: 'selected_models', label: 'selected' },
    ];
    const visSelect = `
        <select
            onchange="event.stopPropagation(); setProviderVisibility('${escapeAttr(provider.id)}', this.value, this)"
            onclick="event.stopPropagation()"
            class="input text-xs py-0.5 px-1.5 w-auto min-w-[80px]"
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
    ].join('|')).join('\n');
    const approvalProfile = provider.approval_profile || {};
    const mediaProfile = provider.media_profile || {};
    const proxyProfile = provider.proxy_profile || {};
    const showMediaAsyncFields = shouldShowMediaAsyncFields(mediaProfile, provider.api_format);

    return `
        <div class="card">
            <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-3">
                <div>
                    <h3 class="card-title">Edit Provider</h3>
                    <div class="text-xs text-dark-500 font-mono">${escapeHtml(provider.id)}</div>
                </div>
                ${renderProviderStatusStrip(provider)}
            </div>

            <div id="provider-form-error" class="hidden mt-3 text-sm text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-3"></div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                ${renderInput('provider-display-name', 'Display Name', provider.display_name)}
                ${renderInput('provider-short-alias', 'Short Alias', provider.short_alias)}
                ${renderInput('provider-base-url', 'Base URL', provider.base_url)}
                ${renderSelect('provider-api-format', 'API Format', provider.api_format, [
                    'openai_responses',
                    'openai_chat',
                    'openai_images',
                    'openai_videos',
                    'openai_compatible',
                    'anthropic',
                    'custom',
                ], 'syncMediaModeControls(true)')}
                ${renderInput('provider-country-region', 'Country / Region', provider.country_region)}
                ${renderInput('provider-native-currency', 'Native Currency', provider.native_currency)}
                ${renderSelect('provider-visibility', 'Catalog Visibility', provider.catalog_visibility, [
                    'hidden',
                    'focused_only',
                    'always_visible',
                    'selected_models',
                ])}
                ${renderInput('provider-user-agent', 'User-Agent', provider.user_agent || (provider.headers || {})['User-Agent'] || '')}
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="provider-enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${provider.enabled ? 'checked' : ''}>
                    <span>Enabled</span>
                </label>
                <label class="flex items-center gap-2 text-sm cursor-pointer">
                    <input id="provider-fallback-enabled" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${provider.fallback_enabled ? 'checked' : ''}>
                    <span>Fallback Enabled</span>
                </label>
            </div>

            <details class="advanced-box mt-4">
                <summary>Advanced</summary>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderInput('provider-api-key', 'API Key', provider.api_key || '', 'password')}
                    ${renderSelect('provider-auth-mode', 'Auth Mode', provider.auth_mode, [
                        'provider_api_key',
                        'global_auth_json',
                        'official_oauth',
                        'no_auth',
                    ])}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('provider-headers-json', 'Headers JSON', JSON.stringify(provider.headers || {}, null, 2), 7)}
                    ${renderTextarea('provider-models-text', 'Models (id|display|context|selected)', modelsText, 7)}
                </div>
                <div class="mt-4">
                    <div class="text-xs text-dark-400 mb-2">Proxy Network Policy</div>
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        ${renderCapabilityToggle('proxy-bypass-system-proxy', 'Bypass System Proxy', proxyProfile.bypass_system_proxy !== false)}
                        ${renderInput('proxy-upstream-timeout', 'Upstream Timeout Seconds (0 = global)', proxyProfile.upstream_timeout_seconds || 0, 'number')}
                        ${renderInput('proxy-retry-attempts', 'Retry Attempts (0 = global)', proxyProfile.retry_attempts || 0, 'number')}
                        ${renderInput('proxy-retry-backoff-ms', 'Retry Backoff ms (0 = global)', proxyProfile.retry_backoff_ms || 0, 'number')}
                    </div>
                </div>
                <div class="flex flex-wrap gap-2 mt-3">
                    <button data-bulk-action="select_all" onclick="runBulkModelAction('select_all')" class="btn btn-secondary text-xs">全选</button>
                    <button data-bulk-action="deselect_all" onclick="runBulkModelAction('deselect_all')" class="btn btn-secondary text-xs">全不选</button>
                    <button data-bulk-action="select_vision" onclick="runBulkModelAction('select_vision')" class="btn btn-secondary text-xs">只选 Vision</button>
                    <button data-bulk-action="select_high_context" onclick="runBulkModelAction('select_high_context')" class="btn btn-secondary text-xs">只选高上下文</button>
                    <button data-bulk-action="select_low_cost" onclick="runBulkModelAction('select_low_cost')" class="btn btn-secondary text-xs">只选低成本</button>
                </div>
                <div id="bulk-action-error" class="hidden mt-2 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('cap-text', 'Text', provider.capabilities.text)}
                    ${renderCapabilityToggle('cap-vision', 'Vision Input', provider.capabilities.vision)}
                    ${renderCapabilityToggle('cap-tools', 'Tools', provider.capabilities.tools)}
                    ${renderCapabilityToggle('cap-reasoning', 'Reasoning', provider.capabilities.reasoning)}
                    ${renderCapabilityToggle('cap-images', 'Images', provider.capabilities.images)}
                    ${renderCapabilityToggle('cap-videos', 'Videos', provider.capabilities.videos)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('responses-domestic', 'Domestic Responses', provider.responses_profile && provider.responses_profile.domestic_responses)}
                    ${renderCapabilityToggle('responses-partial', 'Partial Responses Compatibility', provider.responses_profile && provider.responses_profile.partial_compatibility)}
                    ${renderCapabilityToggle('responses-requires-adapter', 'Responses Adapter Required', provider.responses_profile && provider.responses_profile.requires_adapter)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderInput('responses-profile-id', 'Responses Profile ID', provider.responses_profile && provider.responses_profile.profile_id)}
                    ${renderInput('responses-docs-url', 'Responses Docs URL', provider.responses_profile && provider.responses_profile.verified_docs_url)}
                    ${renderInput('responses-unsupported', 'Unsupported Fields', provider.responses_profile && (provider.responses_profile.unsupported_fields || []).join(', '))}
                </div>
                ${renderApprovalModeSegment(approvalProfile)}
                <div id="approval-auto-fields" class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4 ${approvalModeFromProfile(approvalProfile) === 'proxy_auto_approve' ? '' : 'hidden'}">
                    ${renderInput('approval-reviewer-model', 'Reviewer Model', approvalProfile.reviewer_model || '')}
                    ${renderInput('approval-allowed-actions', 'Allowed Actions', (approvalProfile.allowed_actions || []).join(', '))}
                    ${renderSelect('approval-error-policy', 'Review Error Policy', approvalProfile.on_review_error || 'decline', [
                        'decline',
                        'ask_user',
                        'allow',
                    ])}
                    ${renderInput('approval-timeout-ms', 'Timeout ms', approvalProfile.timeout_ms || 90000, 'number')}
                    ${renderInput('approval-max-retries', 'Max Retries', approvalProfile.max_retries || 1, 'number')}
                    ${renderCapabilityToggle('approval-audit-decisions', 'Audit Decisions', approvalProfile.audit_decisions !== false)}
                    ${renderCapabilityToggle('approval-auto-accept-low-risk', 'Auto Accept Low Risk', approvalProfile.auto_accept_low_risk !== false)}
                    ${renderCapabilityToggle('approval-auto-decline-high-risk', 'Auto Decline High Risk', approvalProfile.auto_decline_high_risk !== false)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-4">
                    ${renderCapabilityToggle('media-default-image', 'Default Image Provider', mediaProfile.default_image_provider)}
                    ${renderCapabilityToggle('media-default-video', 'Default Video Provider', mediaProfile.default_video_provider)}
                </div>
                ${renderMediaModeSegment(mediaProfile)}
                <div id="media-async-fields" class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4 ${showMediaAsyncFields ? '' : 'hidden'}">
                    ${renderCapabilityToggle('media-async-submit', 'Async Submit', mediaProfile.async_submit)}
                    ${renderCapabilityToggle('media-poll-required', 'Poll Required', mediaProfile.poll_required)}
                    ${renderCapabilityToggle('media-cancel-supported', 'Cancel Supported', mediaProfile.cancel_supported)}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                    ${renderTextarea('media-image-overrides-json', 'Image Model Overrides JSON', JSON.stringify(mediaProfile.image_model_overrides || {}, null, 2), 5)}
                    ${renderTextarea('media-video-overrides-json', 'Video Model Overrides JSON', JSON.stringify(mediaProfile.video_model_overrides || {}, null, 2), 5)}
                </div>
                <div class="mt-4">
                    ${renderTextarea('provider-quota-json', 'Quota Check JSON', JSON.stringify(provider.quota_check || {}, null, 2), 8)}
                </div>
            </details>

            <div class="flex flex-wrap gap-2 mt-5">
                <button onclick="saveSelectedProvider()" class="btn btn-primary">Save Local Provider</button>
                <button onclick="testSelectedProvider()" class="btn btn-secondary">Test This Section</button>
                <button onclick="previewResponsesProbe()" class="btn btn-secondary">Responses Probe Preview</button>
                <button onclick="refreshProviderQuota()" class="btn btn-secondary">Refresh Quota</button>
                <button onclick="previewProviderDraft()" class="btn btn-ghost">Preview Draft</button>
                <button onclick="deleteSelectedProvider()" class="btn btn-danger">Delete</button>
            </div>
        </div>

        <div id="provider-draft-preview" class="card hidden">
            <h3 class="card-title">Draft Preview</h3>
            <pre class="preview-code mt-3"></pre>
        </div>

        <div id="provider-responses-probe-preview" class="card ${providerState.responsesProbePreview ? '' : 'hidden'}">
            <h3 class="card-title">Responses Probe Preview</h3>
            <p class="text-xs text-dark-400 mt-1">Dry-run only: no network request, no Codex mutation.</p>
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.responsesProbePreview || {}, null, 2))}</pre>
        </div>

        <div id="provider-quota-preview" class="card ${providerState.quotaPreview ? '' : 'hidden'}">
            <h3 class="card-title">Quota Preview</h3>
            <p class="text-xs text-dark-400 mt-1">Manual provider-section test. Uses provider quota_check and returns redacted results.</p>
            <pre class="preview-code mt-3">${escapeHtml(JSON.stringify(providerState.quotaPreview || {}, null, 2))}</pre>
        </div>
    `;
}

function renderEmptyProviderEditor() {
    return `
        <div class="card">
            <h3 class="card-title">Edit Provider</h3>
            ${renderEmptyState('Create or import a provider to edit local registry settings.')}
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
            ${renderStatusPill(provider.enabled ? 'enabled' : 'disabled', provider.enabled ? 'enabled' : 'disabled', provider.enabled ? 'emerald' : 'dark')}
            ${renderStatusPill('tested', status.last_tested ? 'last tested ' + formatShortDate(status.last_tested) : 'not tested', status.last_tested ? 'accent' : 'dark')}
            ${renderStatusPill('restart', status.needs_restart ? 'needs restart' : 'no restart', status.needs_restart ? 'amber' : 'emerald')}
            ${status.last_error ? renderStatusPill('error', 'has error', 'red') : ''}
        </div>
    `;
}

/**
 * 渲染右侧 UMC Catalog 预览面板。
 * catalog-entry 携带 .stagger-item，列表加载时依次滑入。
 */
function renderCatalogPreviewPanel() {
    /**
     * 渲染右侧 UMC Catalog 预览面板。
     * catalog-entry 携带 .stagger-item，列表加载时依次滑入。
     */
    const preview = providerState.catalogPreview || { entries: [], route_explanation: [] };
    const entries = preview.entries || [];
    const filteredEntries = getFilteredCatalogEntries(entries);
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">Unified Model Catalog Preview</h3>
                <button onclick="previewWithSelectedFocus()" class="btn btn-secondary text-xs">Focus Selected</button>
            </div>
            <div class="text-xs text-dark-500 mt-1">Preview only. No Codex model catalog file is written.</div>
            ${renderCatalogFilters(entries, filteredEntries)}
            <div class="space-y-2 mt-3 max-h-[360px] overflow-y-auto">
                ${filteredEntries.map(renderCatalogEntry).join('') || renderEmptyState('No catalog entries match the filters.')}
            </div>
            <div class="mt-3">
                <div class="text-xs text-dark-400 mb-1">Route explanation</div>
                <pre class="preview-code">${escapeHtml((preview.route_explanation || []).join('\n') || 'No routes yet.')}</pre>
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
    const capabilityOptions = ['text', 'vision', 'tools', 'reasoning', 'images', 'videos']
        .filter(capability => entries.some(entry => catalogHasCapability(entry, capability)))
        .map(capability => ({ value: capability, label: capability }));
    const countLabel = `${filteredEntries.length}/${entries.length} visible`;

    return `
        <div class="mt-3 rounded-lg border border-dark-700/70 bg-dark-950/40 p-3">
            <div class="flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-dark-300">Filters</span>
                <span class="text-xs text-dark-500">${escapeHtml(countLabel)}</span>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                <select class="input text-xs py-2" onchange="updateCatalogFilter('provider', this.value)">
                    <option value="">All providers</option>
                    ${providerOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.provider === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <select class="input text-xs py-2" onchange="updateCatalogFilter('capability', this.value)">
                    <option value="">Any capability</option>
                    ${capabilityOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.capability === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <input class="input text-xs py-2" type="number" min="0" step="1000" value="${escapeAttr(filters.minContext)}"
                    onchange="updateCatalogFilter('minContext', this.value)" placeholder="Min context">
                <input class="input text-xs py-2" type="number" min="0" step="0.0001" value="${escapeAttr(filters.maxInputPrice)}"
                    onchange="updateCatalogFilter('maxInputPrice', this.value)" placeholder="Max input / 1M">
                <select class="input text-xs py-2" onchange="updateCatalogFilter('currency', this.value)">
                    <option value="">Any currency</option>
                    ${currencyOptions.map(option => `<option value="${escapeAttr(option.value)}" ${filters.currency === option.value ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}
                </select>
                <button onclick="resetCatalogFilters()" class="btn btn-secondary text-xs">Reset Filters</button>
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
    renderProvidersPage();
}

function resetCatalogFilters() {
    providerState.catalogFilters = {
        provider: '',
        capability: '',
        minContext: '',
        maxInputPrice: '',
        currency: '',
    };
    renderProvidersPage();
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

function catalogInputPrice(entry) {
    const pricing = entry && entry.pricing && typeof entry.pricing === 'object' ? entry.pricing : {};
    const aliases = [
        'input_per_million',
        'input_tokens_per_million',
        'prompt_per_million',
        'input_per_1m',
        'input_price_per_million',
        'prompt',
        'input',
    ];
    for (const key of aliases) {
        const value = parseCatalogNumber(pricing[key]);
        if (value !== null) return value;
    }
    return null;
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
        entry.focused ? renderMiniBadge('focused') : '',
        entry.catalog_visibility ? renderMiniBadge(entry.catalog_visibility) : '',
        entry.api_format ? renderMiniBadge(entry.api_format) : '',
        entry.responses_profile && entry.responses_profile.domestic_responses ? renderMiniBadge('domestic responses') : '',
        entry.context_window ? renderMiniBadge(formatNumber(entry.context_window, { compact: false }) + ' ctx') : '',
        inputPrice !== null ? renderMiniBadge('input ' + formatCatalogPrice(inputPrice) + '/1M') : '',
        ...capabilityBadges(entry.capabilities),
    ].filter(Boolean).join('');
    return `
        <div class="catalog-entry stagger-item">
            <div class="flex items-center justify-between gap-2">
                <div class="font-mono text-sm text-white truncate">${escapeHtml(entry.codex_model_id)}</div>
                <span class="badge">${escapeHtml(entry.native_currency || '-')}</span>
            </div>
            <div class="text-xs text-dark-400 truncate">${escapeHtml(providerLabel)} -> ${escapeHtml(entry.upstream_model_id)}</div>
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
                <h3 class="card-title">Route Simulator</h3>
                <span class="text-xs text-emerald-300">read-only</span>
            </div>
            <div class="text-xs text-dark-500 mt-1">Simulates AMR capability/context routing from the current provider registry. No upstream request or Codex write is performed.</div>
            <div class="grid grid-cols-2 gap-2 mt-3">
                ${renderRouteCapabilityToggle('route-sim-cap-text', 'text', true)}
                ${renderRouteCapabilityToggle('route-sim-cap-vision', 'vision', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-tools', 'tools', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-reasoning', 'reasoning', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-images', 'images', false)}
                ${renderRouteCapabilityToggle('route-sim-cap-videos', 'videos', false)}
            </div>
            <div class="grid grid-cols-1 gap-3 mt-3">
                <input id="route-sim-model" class="input" list="route-sim-model-options" placeholder="optional model id, provider/model, or catalog id">
                <datalist id="route-sim-model-options">
                    ${datalistEntries.map(value => `<option value="${escapeAttr(value)}"></option>`).join('')}
                </datalist>
                <input id="route-sim-context" type="number" min="0" step="1000" class="input" placeholder="required context tokens">
            </div>
            <button onclick="runRouteSimulatorShell()" class="btn btn-secondary text-xs mt-3">Simulate Route</button>
            <div id="route-sim-result" class="preview-code mt-3">No simulation yet.</div>
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
            <h3 class="card-title">Codex Diff Preview</h3>
            <div class="text-xs text-dark-500 mt-1">Future writes to auth/config/model catalog must pass through diff preview, backup, and rollback. This shell intentionally performs no write.</div>
            <pre class="preview-code mt-3">No pending Codex file changes.</pre>
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
            <button onclick="importPreset('${escapeAttr(preset.preset_id)}')" class="btn btn-secondary text-xs">Import</button>
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
        await ensureProviderData();
        await refreshCatalogPreview();
        showToast('Provider preset imported', 'success');
        if (currentPage === 'quick-setup') renderQuickSetup();
        if (currentPage === 'providers') renderProvidersPage();
    } catch (err) {
        showToast('导入 preset 失败：' + err.message, 'error');
    }
}

async function createBlankProvider() {
    try {
        const result = await api('/api/providers', {
            method: 'POST',
            body: JSON.stringify({
                display_name: 'New Provider',
                short_alias: 'new',
                base_url: '',
                api_format: 'openai_responses',
                native_currency: 'USD',
                catalog_visibility: 'focused_only',
                models: [{ id: 'model-id', display_name: 'Model', selected: true, context_window: 128000 }],
            }),
        });
        providerState.selectedProviderId = result.provider.id;
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast('Provider created', 'success');
    } catch (err) {
        showToast('创建 provider 失败：' + err.message, 'error');
    }
}

function selectProvider(providerId) {
    providerState.selectedProviderId = providerId;
    providerState.responsesProbePreview = null;
    providerState.quotaPreview = null;
    renderProvidersPage();
}

async function saveSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    const draft = readProviderForm(provider);
    if (!draft) return;
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id), {
            method: 'PUT',
            body: JSON.stringify(draft),
        });
        providerState.selectedProviderId = result.provider.id;
        providerState.quotaPreview = null;
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast('Provider saved locally', 'success');
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function testSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    try {
        const result = await api('/api/providers/' + encodeURIComponent(provider.id) + '/test', { method: 'POST' });
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        if (result.success) {
            showToast(result.message || 'Provider test passed', 'success');
        } else {
            showToast((result.errors || []).join('; ') || 'Provider test failed', 'error');
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
        renderProvidersPage();
        showToast('Responses probe preview generated', 'success');
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function refreshProviderQuota() {
    const provider = getSelectedProvider();
    if (!provider) return;
    try {
        providerState.quotaPreview = await api('/api/providers/' + encodeURIComponent(provider.id) + '/quota/refresh', {
            method: 'POST',
            body: JSON.stringify({ force: true }),
        });
        renderProvidersPage();
        if (providerState.quotaPreview && providerState.quotaPreview.success) {
            showToast('Quota refreshed', 'success');
        } else {
            showToast('Quota refresh returned an error', 'warning');
        }
    } catch (err) {
        showProviderFormError(err.message);
    }
}

async function deleteSelectedProvider() {
    const provider = getSelectedProvider();
    if (!provider) return;
    if (!confirm('Delete local provider "' + provider.display_name + '"?')) return;
    try {
        await api('/api/providers/' + encodeURIComponent(provider.id), { method: 'DELETE' });
        providerState.selectedProviderId = '';
        await ensureProviderData();
        await refreshCatalogPreview();
        renderProvidersPage();
        showToast('Provider deleted', 'success');
    } catch (err) {
        showToast('删除 provider 失败：' + err.message, 'error');
    }
}

async function previewWithSelectedFocus() {
    await refreshCatalogPreview(providerState.selectedProviderId || '');
    renderProvidersPage();
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
        result.textContent = 'Simulation failed: ' + err.message;
    }
}

function renderRouteSimulationResult(decision) {
    const statusClass = decision.success ? 'text-emerald-300' : 'text-red-300';
    const title = decision.success
        ? `${decision.provider_id || ''}/${decision.model_id || ''}`
        : (decision.error || 'No route available');
    const candidateRows = (decision.candidate_status || []).slice(0, 8).map(row => {
        const missingCapabilities = row.missing_capabilities || [];
        const flags = [
            row.capability_match ? 'caps ok' : `missing ${missingCapabilities.join(', ') || 'capability'}`,
            row.context_match ? 'ctx ok' : 'ctx too small',
            row.model_match ? 'model ok' : 'model filtered',
        ];
        return `
            <div class="grid grid-cols-[1fr_auto] gap-2 py-2 border-b border-dark-800 last:border-b-0">
                <div class="min-w-0">
                    <div class="font-mono text-xs text-dark-100 break-all">${escapeHtml(row.codex_model_id || row.candidate_id)}</div>
                    <div class="text-xs text-dark-500">p${escapeHtml(row.priority)} · ctx ${formatCount(row.context_window || 0)} · ${escapeHtml(flags.join(' · '))}</div>
                </div>
                <span class="text-xs ${row.available ? 'text-emerald-300' : 'text-amber-300'}">${row.available ? 'available' : 'blocked'}</span>
            </div>
        `;
    }).join('');
    return `
        <div class="space-y-3">
            <div>
                <div class="text-xs ${statusClass}">${decision.success ? 'Route selected' : 'Route unavailable'}</div>
                <div class="font-mono text-sm text-dark-100 break-all">${escapeHtml(title)}</div>
                <div class="text-xs text-dark-500 mt-1">
                    capabilities: ${escapeHtml((decision.required_capabilities || []).join(', ') || 'text')}
                    · context: ${escapeHtml(decision.required_context || 0)}
                    · candidates: ${escapeHtml(decision.candidate_count || 0)}
                </div>
            </div>
            <div class="rounded-md border border-dark-800 bg-dark-950/50 px-3 py-2">
                ${(decision.explanation || []).map(line => `<div class="text-xs text-dark-300 py-0.5">${escapeHtml(line)}</div>`).join('') || '<div class="text-xs text-dark-500">No explanation returned.</div>'}
            </div>
            <div class="rounded-md border border-dark-800 bg-dark-950/40 px-3 py-1">
                ${candidateRows || '<div class="text-xs text-dark-500 py-2">No candidate status returned.</div>'}
            </div>
        </div>
    `;
}

async function exportProviderBundle() {
    try {
        const bundle = await api('/api/providers/export');
        const preview = JSON.stringify(bundle, null, 2);
        await navigator.clipboard.writeText(preview);
        showToast('Redacted provider bundle copied', 'success');
    } catch (err) {
        showToast('导出失败：' + err.message, 'error');
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
        const models = parseModelsText(document.getElementById('provider-models-text')?.value || '');
        const approvalMode = getSelectedApprovalMode();
        const mediaMode = getSelectedMediaMode();
        const mediaAsyncVisible = !document.getElementById('media-async-fields')?.classList.contains('hidden');
        return {
            ...existing,
            display_name: document.getElementById('provider-display-name')?.value || '',
            short_alias: document.getElementById('provider-short-alias')?.value || '',
            base_url: document.getElementById('provider-base-url')?.value || '',
            api_format: document.getElementById('provider-api-format')?.value || 'openai_responses',
            country_region: document.getElementById('provider-country-region')?.value || '',
            native_currency: document.getElementById('provider-native-currency')?.value || 'USD',
            catalog_visibility: document.getElementById('provider-visibility')?.value || 'focused_only',
            user_agent: document.getElementById('provider-user-agent')?.value || '',
            enabled: document.getElementById('provider-enabled')?.checked || false,
            fallback_enabled: document.getElementById('provider-fallback-enabled')?.checked || false,
            api_key: document.getElementById('provider-api-key') ? document.getElementById('provider-api-key').value : (existing.api_key || ''),
            auth_mode: document.getElementById('provider-auth-mode')?.value || 'provider_api_key',
            headers,
            quota_check: quotaCheck,
            models,
            capabilities: {
                ...existing.capabilities,
                text: document.getElementById('cap-text')?.checked || false,
                vision: document.getElementById('cap-vision')?.checked || false,
                tools: document.getElementById('cap-tools')?.checked || false,
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
        showProviderFormError('Draft parse failed: ' + err.message);
        return null;
    }
}

function parseModelsText(text) {
    return String(text || '').split('\n')
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => {
            const parts = line.split('|').map(part => part.trim());
            return {
                id: parts[0] || 'model-id',
                display_name: parts[1] || parts[0] || 'Model',
                context_window: parseInt(parts[2], 10) || 0,
                selected: parts[3] === 'selected' || parts[3] === 'true',
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

function renderInput(id, label, value, type = 'text') {
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <input id="${escapeAttr(id)}" type="${escapeAttr(type)}" class="input mt-1 w-full" value="${escapeAttr(value || '')}">
        </div>
    `;
}

function renderTextarea(id, label, value, rows = 4) {
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <textarea id="${escapeAttr(id)}" class="input mt-1 w-full font-mono" rows="${rows}">${escapeHtml(value || '')}</textarea>
        </div>
    `;
}

function renderSelect(id, label, value, options, onchange = '') {
    const changeAttr = onchange ? ` onchange="${escapeAttr(onchange)}"` : '';
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <select id="${escapeAttr(id)}" class="input mt-1 w-full"${changeAttr}>
                ${options.map(option => `<option value="${escapeAttr(option)}" ${option === value ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
            </select>
        </div>
    `;
}

function renderCapabilityToggle(id, label, checked) {
    return `
        <label class="flex items-center gap-2 text-sm cursor-pointer bg-dark-900/60 border border-dark-700 rounded-lg px-3 py-2">
            <input id="${escapeAttr(id)}" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" ${checked ? 'checked' : ''}>
            <span>${escapeHtml(label)}</span>
        </label>
    `;
}

function approvalModeFromProfile(profile) {
    const mode = profile && profile.mode;
    if (mode === 'official_guardian' || mode === 'proxy_auto_approve' || mode === 'manual_only') return mode;
    if (profile && profile.proxy_auto_approve) return 'proxy_auto_approve';
    if (profile && profile.official_guardian) return 'official_guardian';
    return 'manual_only';
}

function renderApprovalModeSegment(profile) {
    const mode = approvalModeFromProfile(profile || {});
    const options = [
        ['manual_only', 'Manual', 'User decides'],
        ['official_guardian', 'Official Guardian', 'Codex native'],
        ['proxy_auto_approve', 'Auto Approval', 'Proxy broker'],
    ];
    return `
        <div class="approval-mode-field mt-4">
            <div class="text-xs text-dark-400 mb-2">Approval Mode</div>
            <div class="segmented-control" role="radiogroup" aria-label="Approval Mode">
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

function getSelectedApprovalMode() {
    return document.querySelector('input[name="approval-mode"]:checked')?.value || 'manual_only';
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

function renderMediaModeSegment(profile) {
    const mode = mediaModeFromProfile(profile || {});
    const options = [
        ['openai_compatible', 'OpenAI-compatible', 'Direct pass-through'],
        ['adapter_required', 'Adapter required', 'Vendor adapter'],
        ['disabled', 'Disabled', 'No media route'],
    ];
    return `
        <div class="media-mode-field mt-4">
            <div class="text-xs text-dark-400 mb-2">Media Mode</div>
            <div class="segmented-control" role="radiogroup" aria-label="Media Mode">
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
    if (!asyncFields) return;
    const apiFormat = document.getElementById('provider-api-format')?.value || '';
    const hasExistingAsync = ['media-async-submit', 'media-poll-required', 'media-cancel-supported']
        .some(id => document.getElementById(id)?.checked);
    const showAsync = selectedMode === 'adapter_required'
        || apiFormat === 'openai_videos'
        || (!clearHiddenAsync && hasExistingAsync);
    asyncFields.classList.toggle('hidden', !showAsync);
    if (!showAsync) {
        ['media-async-submit', 'media-poll-required', 'media-cancel-supported'].forEach(id => {
            const checkbox = document.getElementById(id);
            if (checkbox) checkbox.checked = false;
        });
    }
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
    return ['text', 'vision', 'tools', 'reasoning', 'images', 'videos']
        .filter(key => caps[key])
        .map(renderMiniBadge);
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
    const baseUrl = status.base_url || '未启动';
    const approvalBrokerConnected = Boolean(status.media_auto_approval_reviewer_connected);
    const backoffNotice = backoff.used
        ? `<div class="mt-3 text-xs text-amber-200 bg-amber-950/30 border border-amber-700/40 rounded-lg p-2">
                配置端口 ${escapeHtml(backoff.from)} 已占用，已自动退避到 ${escapeHtml(backoff.to)}。
           </div>`
        : '';
    return `
        <div class="card">
            <h3 class="card-title">Local Proxy</h3>
            <div class="flex items-center gap-2 mt-3">
                <span class="status-dot ${running ? 'bg-emerald-500' : 'bg-dark-500'}"></span>
                <span class="text-xs ${running ? 'text-emerald-400' : 'text-dark-400'}">${running ? '代理运行中' : '代理已停止'} ${portLabel}</span>
            </div>
            <div class="mt-2 text-xs text-dark-400 break-all">${escapeHtml(baseUrl)}</div>
            <div class="mt-2 text-xs ${approvalBrokerConnected ? 'text-emerald-300' : 'text-dark-500'}">
                Approval Broker ${approvalBrokerConnected ? 'connected' : 'idle'}
            </div>
            ${backoffNotice}
            ${status.last_start_error ? `<div class="mt-3 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2">${escapeHtml(status.last_start_error)}</div>` : ''}
            <div class="mt-2 text-xs text-dark-500">启动时会自动尝试配置端口之后的可用端口。</div>
            <div class="flex flex-wrap gap-2 mt-3">
                <button id="proxy-start-btn" onclick="startProxy()" class="btn btn-primary text-xs" ${running ? 'disabled' : ''}>启动代理</button>
                <button id="proxy-stop-btn" onclick="stopProxy()" class="btn btn-danger text-xs" ${running ? '' : 'disabled'}>停止代理</button>
            </div>
            <div id="proxy-action-error" class="hidden mt-2 text-xs text-red-300 bg-red-950/30 border border-red-700/50 rounded-lg p-2"></div>
        </div>
    `;
}

function renderProxyRouteTestCard() {
    return `
        <div class="card">
            <h3 class="card-title">Proxy Route Test</h3>
            <div class="text-xs text-dark-500 mt-1">输入 model ID，测试会路由到哪个 provider</div>
            <div class="flex gap-2 mt-3">
                <input id="proxy-test-model" class="input flex-1 text-sm" placeholder="qwen/qwen3-coder-plus">
                <button id="proxy-test-btn" onclick="testProxyRoute()" class="btn btn-secondary text-xs">测试路由</button>
            </div>
            <pre id="proxy-test-result" class="preview-code mt-3">输入 model ID 并点击测试</pre>
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
        showToast('批量操作成功', 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = '批量操作失败：' + (err.message || '网络错误，请检查连接');
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
        showToast('Visibility 已更新', 'success', 1500);
    } catch (err) {
        showToast('更新 visibility 失败：' + (err.message || '网络错误'), 'error');
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
        showToast('代理已启动', 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = '启动代理失败：' + (err.message || '网络错误');
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
        showToast('代理已停止', 'success', 1500);
    } catch (err) {
        if (errorEl) {
            errorEl.textContent = '停止代理失败：' + (err.message || '网络错误');
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
        if (result) result.textContent = '请输入 model ID';
        return;
    }
    if (btn) btn.disabled = true;
    if (result) result.textContent = '测试中...';
    try {
        const data = await api('/api/proxy/test-route', {
            method: 'POST',
            body: JSON.stringify({ model: modelId }),
        });
        if (result) {
            result.textContent = 'Provider: ' + (data.provider_id || '-') + '\n' +
                'Display: ' + (data.display_name || '-') + '\n' +
                'Base URL: ' + (data.base_url || '-') + '\n' +
                'Format: ' + (data.api_format || '-');
        }
        if (btn) {
            btn.classList.add('btn-success-flash');
            setTimeout(() => btn.classList.remove('btn-success-flash'), 1200);
        }
    } catch (err) {
        if (result) result.textContent = '测试失败：' + (err.message || '网络错误');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ─────────────── Codex Integration Page ───────────────

let codexIntegrationState = {
    status: null,
    preview: null,
    permissionsPreview: null,
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
    setStatus('Codex Integration loaded');
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

async function previewCodexIntegration() {
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || getCodexIntegrationProxyBaseUrl();
    const proxyModel = document.getElementById('ci-proxy-model')?.value || 'auto';
    try {
        codexIntegrationState.preview = await api('/api/codex-integration/preview', {
            method: 'POST',
            body: JSON.stringify({ proxy_base_url: proxyBaseUrl, proxy_model: proxyModel }),
        });
        renderCodexIntegrationPage();
        showToast('Diff preview generated', 'success');
    } catch (err) {
        showToast('Preview failed: ' + err.message, 'error');
    }
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
        showToast('Sandbox preview generated', 'success');
    } catch (err) {
        showToast('Sandbox preview failed: ' + err.message, 'error');
    }
}

async function applyCodexIntegration() {
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || getCodexIntegrationProxyBaseUrl();
    const proxyModel = document.getElementById('ci-proxy-model')?.value || 'auto';
    const preserveAuth = document.getElementById('ci-preserve-auth')?.checked !== false;
    const manual = requestCodexMutationConfirmation('write Codex config.toml');
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/apply', {
            method: 'POST',
            body: JSON.stringify({
                proxy_base_url: proxyBaseUrl,
                proxy_model: proxyModel,
                preserve_official_auth: preserveAuth,
                ...manual,
            }),
        });
        if (result.success) {
            showToast('Applied. Restart Codex to take effect.', 'success');
        } else {
            showToast('Apply failed: ' + (result.errors || []).join('; '), 'error');
        }
        await refreshCodexIntegrationStatus();
        await refreshCodexIntegrationBackups();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast('Apply failed: ' + err.message, 'error');
    }
}

async function restoreCodexConfig() {
    const manual = requestCodexMutationConfirmation('restore Codex config.toml');
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/restore-config', {
            method: 'POST',
            body: JSON.stringify(manual),
        });
        if (result.success) {
            showToast('Config restored. Restart Codex.', 'success');
        } else {
            showToast('Restore failed: ' + (result.error || ''), 'error');
        }
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast('Restore failed: ' + err.message, 'error');
    }
}

async function restoreCodexAuth() {
    const manual = requestCodexMutationConfirmation('restore Codex auth.json');
    if (!manual) return;
    try {
        const result = await api('/api/codex-integration/restore-auth', {
            method: 'POST',
            body: JSON.stringify(manual),
        });
        if (result.success) {
            showToast('Auth restored. Restart Codex.', 'success');
        } else {
            showToast('Restore failed: ' + (result.error || ''), 'error');
        }
        await refreshCodexIntegrationStatus();
        renderCodexIntegrationPage();
    } catch (err) {
        showToast('Restore failed: ' + err.message, 'error');
    }
}

function requestCodexMutationConfirmation(actionLabel) {
    const phrase = 'MODIFY_CODEX_FILES';
    const value = prompt(
        'This action will change Codex files or process state: ' + actionLabel + '\n\n' +
        'Codex mutation tests are manual-only in this workspace.\n' +
        'Type ' + phrase + ' to continue.'
    );
    if (value !== phrase) {
        showToast('Codex mutation cancelled', 'warning');
        return null;
    }
    return {
        manual_codex_mutation: true,
        confirmation: phrase,
    };
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
    const proxyBackoffNote = proxyBackoff.used
        ? `<div class="mt-2 text-xs text-amber-300">Configured port ${escapeHtml(proxyBackoff.from)} was occupied; using ${escapeHtml(proxyBackoff.to)}.</div>`
        : '';

    root.innerHTML = `
        <div class="animate-in">
        <div class="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
            <div>
                <h2 class="text-2xl font-semibold text-white">Codex Integration</h2>
                <p class="text-sm text-dark-400 mt-1">Safe config/auth management with diff preview, backup, and rollback.</p>
            </div>
            <div class="enhance-status-strip">
                ${renderStatusPill('auth-mode', 'Auth: ' + (status.auth_mode || 'unknown'), status.auth_mode === 'official_oauth' ? 'emerald' : 'amber')}
                ${renderStatusPill('restart', 'Restart required after writes', 'amber')}
            </div>
        </div>

        <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4 mt-6">
            <div class="space-y-4">
                <div class="card">
                    <h3 class="card-title">Current Codex Status</h3>
                    <div class="space-y-2 mt-3 text-sm text-dark-300">
                        <div class="flex justify-between"><span class="text-dark-500">Config path</span><span class="font-mono text-dark-200">${escapeHtml(status.config_path || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">Auth path</span><span class="font-mono text-dark-200">${escapeHtml(status.auth_path || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">Auth mode</span><span class="font-mono ${status.auth_mode === 'official_oauth' ? 'text-emerald-400' : 'text-amber-400'}">${escapeHtml(status.auth_mode || 'none')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">Model provider</span><span class="font-mono text-dark-200">${escapeHtml((status.config || {}).model_provider || '-')}</span></div>
                        <div class="flex justify-between"><span class="text-dark-500">Model</span><span class="font-mono text-dark-200">${escapeHtml((status.config || {}).model || '-')}</span></div>
                    </div>
                </div>

                <div class="card">
                    <h3 class="card-title">Local Proxy Settings</h3>
                    <div class="grid grid-cols-1 gap-4 mt-3">
                        ${renderInput('ci-proxy-base-url', 'Proxy Base URL', proxyBaseUrl)}
                        ${renderInput('ci-proxy-model', 'Proxy Model', 'auto')}
                        <label class="flex items-center gap-2 text-sm cursor-pointer">
                            <input id="ci-preserve-auth" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" checked>
                            <span>Preserve official OAuth (do not modify auth.json)</span>
                        </label>
                    </div>
                    ${proxyBackoffNote}
                    <div class="flex flex-wrap gap-2 mt-4">
                        <button onclick="previewCodexIntegration()" class="btn btn-secondary">Preview Diff</button>
                        <button onclick="applyCodexIntegration()" class="btn btn-warning">Manual Apply to Codex Config</button>
                    </div>
                </div>

                ${renderPermissionsAudit(status.permissions || {}, codexIntegrationState.permissionsPreview)}
            </div>

            <div class="space-y-4">
                ${preview ? renderDiffPreview(preview) : renderDiffPreviewShell()}

                <div class="card">
                    <h3 class="card-title">Backups & Rollback</h3>
                    <div class="space-y-2 mt-3">
                        ${backups.length ? backups.slice(0, 6).map(b => `
                            <div class="flex items-center justify-between text-sm">
                                <span class="font-mono text-dark-300">${escapeHtml(b.name)}</span>
                                <span class="text-dark-500">${escapeHtml(b.mtime ? b.mtime.slice(0, 19).replace('T', ' ') : '')}</span>
                            </div>
                        `).join('') : '<div class="text-sm text-dark-500">No backups yet.</div>'}
                    </div>
                    <div class="flex flex-wrap gap-2 mt-4">
                        <button onclick="restoreCodexConfig()" class="btn btn-warning text-xs">Manual Restore Config</button>
                        <button onclick="restoreCodexAuth()" class="btn btn-warning text-xs">Manual Restore Auth</button>
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
                <h3 class="card-title">Diff Preview</h3>
                ${hasChanges ? renderStatusPill('pending', 'changes pending', 'amber') : renderStatusPill('clean', 'no changes', 'emerald')}
            </div>
            ${(preview.warnings || []).length ? `<div class="mt-3 space-y-1">${(preview.warnings || []).map(w => `<div class="text-xs text-amber-300">${escapeHtml(w)}</div>`).join('')}</div>` : ''}
            <div class="mt-3 space-y-2 text-sm">
                ${Object.entries(diff.added || {}).map(([k, v]) => `<div class="diff-added">+ ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${Object.entries(diff.changed || {}).map(([k, v]) => `<div class="diff-changed">~ ${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v.old))} → ${escapeHtml(JSON.stringify(v.new))}</div>`).join('')}
                ${Object.entries(diff.removed || {}).map(([k, v]) => `<div class="diff-removed">- ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${!hasChanges ? '<div class="text-dark-500">No differences from current config.</div>' : ''}
            </div>
            <div class="mt-3 text-xs text-dark-500">
                preserve_official_oauth: ${preview.preserve_official_oauth ? 'true' : 'false'}
                &middot; restart_required: ${preview.restart_required ? 'true' : 'false'}
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
                <h3 class="card-title">Approval & Sandbox Audit</h3>
                ${renderStatusPill('sandbox', current.issue_count ? `${current.issue_count} issues` : 'clean', issueColor)}
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 text-sm">
                ${renderReadonlyKV('approval_policy', current.approval_policy || '(default)')}
                ${renderReadonlyKV('sandbox_mode', current.sandbox_mode || '(derived/default)')}
                ${renderReadonlyKV('default_permissions', current.default_permissions || '(none)')}
                ${renderReadonlyKV('windows.sandbox', current.windows_sandbox || '(default)')}
                ${renderReadonlyKV('network_access', String((current.sandbox_workspace_write || {}).network_access || false))}
                ${renderReadonlyKV('full_access_detected', String(Boolean(current.effective_full_access)))}
            </div>
            ${issues.length ? `<div class="mt-3 space-y-1">${issues.map(issue => `
                <div class="text-xs ${issue.severity === 'error' || issue.severity === 'high' ? 'text-red-300' : 'text-amber-300'}">
                    ${escapeHtml(issue.field || 'config')}: ${escapeHtml(issue.message || '')}
                </div>
            `).join('')}</div>` : '<div class="mt-3 text-xs text-emerald-300">No known approval/sandbox corruption detected.</div>'}
            ${warnings.length ? `<div class="mt-2 space-y-1">${warnings.map(w => `<div class="text-xs text-amber-300">${escapeHtml(w)}</div>`).join('')}</div>` : ''}
            ${recommendations.length ? `<div class="mt-2 space-y-1">${recommendations.map(r => `<div class="text-xs text-dark-400">${escapeHtml(r)}</div>`).join('')}</div>` : ''}

            <details class="advanced-box mt-4">
                <summary>Sandbox Preview</summary>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
                    <div>
                        <label class="text-xs text-dark-400">Approval Policy</label>
                        <select id="ci-approval-policy" class="input mt-1 w-full">
                            ${renderOption('', 'Keep current', true)}
                            ${renderOption('untrusted', 'untrusted', false)}
                            ${renderOption('on-request', 'on-request', false)}
                            ${renderOption('never', 'never', false)}
                            ${renderOption('on-failure', 'on-failure (deprecated)', false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">Sandbox Mode</label>
                        <select id="ci-sandbox-mode" class="input mt-1 w-full">
                            ${renderOption('', 'Keep current', true)}
                            ${renderOption('read-only', 'read-only', false)}
                            ${renderOption('workspace-write', 'workspace-write', false)}
                            ${renderOption('danger-full-access', 'danger-full-access', false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">Windows Sandbox</label>
                        <select id="ci-windows-sandbox" class="input mt-1 w-full">
                            ${renderOption('', 'Keep current', true)}
                            ${renderOption('disabled', 'disabled', false)}
                            ${renderOption('restricted-token', 'restricted-token', false)}
                            ${renderOption('elevated', 'elevated', false)}
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-dark-400">Default Permissions</label>
                        <input id="ci-default-permissions" class="input mt-1 w-full" placeholder="Keep current or :workspace">
                    </div>
                    <div class="md:col-span-2">
                        <label class="text-xs text-dark-400">Writable Roots</label>
                        <textarea id="ci-writable-roots" class="input mt-1 w-full min-h-[72px]" placeholder="One path per line or comma-separated"></textarea>
                    </div>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-network-access" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>workspace network_access</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-exclude-tmpdir-env" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>exclude_tmpdir_env_var</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm">
                        <input id="ci-exclude-slash-tmp" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500">
                        <span>exclude_slash_tmp</span>
                    </label>
                </div>
                <button onclick="previewCodexPermissions()" class="btn btn-secondary mt-3">Preview Sandbox Diff</button>
                ${preview ? renderPermissionsPreviewResult(preview, desired, previewHasChanges) : '<div class="text-xs text-dark-500 mt-3">Preview only. This does not write config.toml.</div>'}
            </details>
            <details class="advanced-box mt-3">
                <summary>Verified Source Notes</summary>
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
                <span class="text-sm font-medium text-dark-200">Sandbox Diff Preview</span>
                ${hasChanges ? renderStatusPill('pending', 'changes pending', 'amber') : renderStatusPill('clean', 'no changes', 'emerald')}
            </div>
            <div class="mt-2 space-y-1 text-xs">
                ${Object.entries(diff.added || {}).map(([k, v]) => `<div class="diff-added">+ ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${Object.entries(diff.changed || {}).map(([k, v]) => `<div class="diff-changed">~ ${escapeHtml(k)}: ${escapeHtml(JSON.stringify(v.old))} -> ${escapeHtml(JSON.stringify(v.new))}</div>`).join('')}
                ${Object.entries(diff.removed || {}).map(([k, v]) => `<div class="diff-removed">- ${escapeHtml(k)} = ${escapeHtml(JSON.stringify(v))}</div>`).join('')}
                ${!hasChanges ? '<div class="text-dark-500">No differences from current config.</div>' : ''}
            </div>
            ${desiredIssues.length ? `<div class="mt-2 space-y-1">${desiredIssues.map(issue => `<div class="text-xs text-amber-300">${escapeHtml(issue.field || 'config')}: ${escapeHtml(issue.message || '')}</div>`).join('')}</div>` : ''}
        </div>
    `;
}

function renderDiffPreviewShell() {
    return `
        <div class="card">
            <h3 class="card-title">Diff Preview</h3>
            <div class="text-sm text-dark-500 mt-3">Click "Preview Diff" to see what will change in config.toml before applying.</div>
        </div>
    `;
}
