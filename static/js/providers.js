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
    draftError: '',
    proxyStatus: null,
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
                    ${renderStepRow('Route Simulator', '预留 AMR/media route explanation 面板，后续接真实 router。', false)}
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
                ])}
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
                    ${renderInput('responses-docs-url', 'Responses Docs URL', provider.responses_profile && provider.responses_profile.verified_docs_url)}
                    ${renderInput('responses-unsupported', 'Unsupported Fields', provider.responses_profile && (provider.responses_profile.unsupported_fields || []).join(', '))}
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-4">
                    ${renderCapabilityToggle('media-default-image', 'Default Image Provider', provider.media_profile.default_image_provider)}
                    ${renderCapabilityToggle('media-default-video', 'Default Video Provider', provider.media_profile.default_video_provider)}
                    ${renderCapabilityToggle('media-adapter-required', 'Adapter Required', provider.media_profile.adapter_required)}
                </div>
            </details>

            <div class="flex flex-wrap gap-2 mt-5">
                <button onclick="saveSelectedProvider()" class="btn btn-primary">Save Local Provider</button>
                <button onclick="testSelectedProvider()" class="btn btn-secondary">Test This Section</button>
                <button onclick="previewProviderDraft()" class="btn btn-ghost">Preview Draft</button>
                <button onclick="deleteSelectedProvider()" class="btn btn-danger">Delete</button>
            </div>
        </div>

        <div id="provider-draft-preview" class="card hidden">
            <h3 class="card-title">Draft Preview</h3>
            <pre class="preview-code mt-3"></pre>
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
    return `
        <div class="card">
            <div class="flex items-center justify-between gap-3">
                <h3 class="card-title">Unified Model Catalog Preview</h3>
                <button onclick="previewWithSelectedFocus()" class="btn btn-secondary text-xs">Focus Selected</button>
            </div>
            <div class="text-xs text-dark-500 mt-1">Preview only. No Codex model catalog file is written.</div>
            <div class="space-y-2 mt-3 max-h-[360px] overflow-y-auto">
                ${entries.map(renderCatalogEntry).join('') || renderEmptyState('No catalog entries visible.')}
            </div>
            <div class="mt-3">
                <div class="text-xs text-dark-400 mb-1">Route explanation</div>
                <pre class="preview-code">${escapeHtml((preview.route_explanation || []).join('\n') || 'No routes yet.')}</pre>
            </div>
        </div>
    `;
}

/**
 * UMC Catalog 条目。hover 时 translateY(-2px) scale(1.01)，
 * 在密集列表中制造轻微“浮起”以便用户聚焦当前行。
 */
function renderCatalogEntry(entry) {
    /**
     * UMC Catalog 条目。
     * hover 时 translateY(-2px) scale(1.01)，在密集列表中制造轻微“浮起”
     * 以便用户聚焦当前行。
     */
    return `
        <div class="catalog-entry stagger-item">
            <div class="flex items-center justify-between gap-2">
                <div class="font-mono text-sm text-white truncate">${escapeHtml(entry.codex_model_id)}</div>
                <span class="badge">${escapeHtml(entry.native_currency || '-')}</span>
            </div>
            <div class="text-xs text-dark-400 truncate">${escapeHtml(entry.provider_display_name)} -> ${escapeHtml(entry.upstream_model_id)}</div>
            <div class="flex flex-wrap gap-1 mt-2">
                ${renderMiniBadge(entry.provider_alias)}
                ${entry.api_format === 'anthropic' ? renderMiniBadge('anthropic') : ''}
                ${entry.responses_profile && entry.responses_profile.domestic_responses ? renderMiniBadge('domestic responses') : ''}
                ${entry.context_window ? renderMiniBadge(formatNumber(entry.context_window, { compact: false }) + ' ctx') : ''}
                ${capabilityBadges(entry.capabilities).join('')}
            </div>
        </div>
    `;
}

function renderRouteSimulatorShell() {
    /**
     * 渲染 Route Simulator 外壳（右侧栏）。
     * 当前为占位 UI，真实 AMR 路由逻辑由后端 /api/model-rotation/simulate 提供。
     * capability 下拉框覆盖 text/vision/images/videos/tools 五种常见场景。
     */
    return `
        <div class="card">
            <h3 class="card-title">Route Simulator</h3>
            <div class="text-xs text-dark-500 mt-1">Shell for UMC/AMR/media route explanations. Real adapter logic will be implemented after source/doc verification.</div>
            <div class="grid grid-cols-2 gap-3 mt-3">
                <select id="route-sim-capability" class="input">
                    <option value="text">Text</option>
                    <option value="vision">Vision Input</option>
                    <option value="images">Image Generation</option>
                    <option value="videos">Video Generation</option>
                    <option value="tools">Tools</option>
                </select>
                <input id="route-sim-model" class="input" placeholder="model or group id">
            </div>
            <button onclick="runRouteSimulatorShell()" class="btn btn-secondary text-xs mt-3">Simulate</button>
            <pre id="route-sim-result" class="preview-code mt-3">No simulation yet.</pre>
        </div>
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
    const capability = document.getElementById('route-sim-capability')?.value || 'text';
    const model = document.getElementById('route-sim-model')?.value || '';
    const result = document.getElementById('route-sim-result');
    if (!result) return;
    try {
        const decision = await api('/api/model-rotation/simulate', {
            method: 'POST',
            body: JSON.stringify({ capability, model }),
        });
        result.textContent = JSON.stringify(decision, null, 2);
    } catch (err) {
        result.textContent = 'Simulation failed: ' + err.message;
    }
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
        const models = parseModelsText(document.getElementById('provider-models-text')?.value || '');
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
            media_profile: {
                ...existing.media_profile,
                default_image_provider: document.getElementById('media-default-image')?.checked || false,
                default_video_provider: document.getElementById('media-default-video')?.checked || false,
                adapter_required: document.getElementById('media-adapter-required')?.checked || false,
            },
            responses_profile: {
                ...(existing.responses_profile || {}),
                domestic_responses: document.getElementById('responses-domestic')?.checked || false,
                partial_compatibility: document.getElementById('responses-partial')?.checked || false,
                requires_adapter: document.getElementById('responses-requires-adapter')?.checked || false,
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

function renderSelect(id, label, value, options) {
    return `
        <div>
            <label class="text-xs text-dark-400">${escapeHtml(label)}</label>
            <select id="${escapeAttr(id)}" class="input mt-1 w-full">
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
    backups: [],
    loading: false,
};

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
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || 'http://localhost:8080/v1';
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

async function applyCodexIntegration() {
    const proxyBaseUrl = document.getElementById('ci-proxy-base-url')?.value || 'http://localhost:8080/v1';
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
                        ${renderInput('ci-proxy-base-url', 'Proxy Base URL', 'http://localhost:8080/v1')}
                        ${renderInput('ci-proxy-model', 'Proxy Model', 'auto')}
                        <label class="flex items-center gap-2 text-sm cursor-pointer">
                            <input id="ci-preserve-auth" type="checkbox" class="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-500 focus:ring-accent-500" checked>
                            <span>Preserve official OAuth (do not modify auth.json)</span>
                        </label>
                    </div>
                    <div class="flex flex-wrap gap-2 mt-4">
                        <button onclick="previewCodexIntegration()" class="btn btn-secondary">Preview Diff</button>
                        <button onclick="applyCodexIntegration()" class="btn btn-warning">Manual Apply to Codex Config</button>
                    </div>
                </div>
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

function renderDiffPreviewShell() {
    return `
        <div class="card">
            <h3 class="card-title">Diff Preview</h3>
            <div class="text-sm text-dark-500 mt-3">Click "Preview Diff" to see what will change in config.toml before applying.</div>
        </div>
    `;
}
