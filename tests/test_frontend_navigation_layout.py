import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


BROKEN_HTML_CLOSER_RE = re.compile(r"\?/[A-Za-z][A-Za-z0-9-]*>")
MOJIBAKE_FLAG_MARKERS = ("馃", "рџ")


def test_static_html_has_no_mojibake_broken_closing_tags():
    for path in [
        ROOT / "static" / "index.html",
        ROOT / "static" / "monitor.html",
    ]:
        html = path.read_text(encoding="utf-8")
        assert not BROKEN_HTML_CLOSER_RE.search(html), path
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "</title>" in index.split("</head>", 1)[0]


def test_language_switcher_keeps_real_flag_emoji():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "🇨🇳" in index
    assert "🇬🇧" in index
    for marker in MOJIBAKE_FLAG_MARKERS:
        assert marker not in index


def test_flask_serves_frontend_assets_outside_project_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_ENHANCE_MANAGER_SMOKE_TEST", "1")

    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        index_response = client.get("/")
        assert index_response.status_code == 200
        index_html = index_response.get_data(as_text=True)
        assert "</title>" in index_html
        assert not BROKEN_HTML_CLOSER_RE.search(index_html)
        assert client.get("/js/app.js").status_code == 200
        assert client.get("/js/tailwindcss.js").status_code == 200
        assert client.get("/js/chart.js").status_code == 200
        assert client.get("/css/style.css").status_code == 200
        assert client.get("/fonts/inter.css").status_code == 200


def test_inactive_pages_do_not_keep_layout_space():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert ".page {" in css
    assert "display: none;" in css
    assert ".page.active {" in css
    assert "display: block;" in css


def test_navigation_resets_window_and_main_scroll():
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.scrollTo({ top: 0, left: 0, behavior: 'auto' });" in js
    assert "document.querySelector('main')?.scrollTo({ top: 0, left: 0, behavior: 'auto' });" in js


def test_settings_wizard_exposes_prompt_and_source_link():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "data-settings-step-button" in html
    assert html.count("data-settings-step-button=") == 8
    assert html.count("data-settings-step-panel=") == 8
    assert "setting-auto-approval-system-prompt" in html
    assert "settings-wizard-checklist" in html
    assert "settings-wizard-advisor" in html
    assert "settings-wizard-progress-bar" in html
    assert "settings-wizard-current-title" in html
    assert "settings-wizard-current-detail" in html
    assert "settings-provider-wizard-summary" in html
    assert "settings-routing-wizard-summary" in html
    assert "settings-alerts-wizard-summary" in html
    assert "settings-finish-wizard-summary" in html
    assert "fillSettingsWizardDefaults" in html
    assert "setting-close-button-action" in html
    assert "setting-desktop-monitor-enabled" in html
    assert "setting-desktop-monitor-opacity" in html
    assert "desktop_monitor_opacity" in js
    assert "syncMonitorOpacityLabel" in js
    assert "setting-update-check-enabled" in html
    assert "setting-update-include-prerelease" in html
    assert "setting-plugin-unlock-enabled" in html
    assert "setting-codex-sandbox-auto-repair-enabled" in html
    assert "setting-desktop-launch-action" in html
    assert "createDesktopShortcut('normal')" in html
    assert "createDesktopShortcut('start_codex')" in html
    assert 'href="/favicon.ico"' in html
    assert 'src="/app-icon.png"' in html
    assert "app-logo-shell" in html
    assert "startupElevationHelp" in html
    assert "checkForUpdates" in html
    assert "downloadLatestUpdate" in js
    assert "/api/updates/check" in js
    assert "/api/updates/download" in js
    assert "plugin_unlock_enabled" in js
    assert "codex_sandbox_auto_repair_enabled" in js
    assert "desktop_launch_action" in js
    assert "/api/desktop-shortcuts/create" in js
    assert "renderStartupPreviewResult" in js
    assert "https://github.com/JiangNanGenius/Codex-Enhance-Manager" in html
    assert "restoreAutoApprovalPromptDefault" in js
    assert "buildSettingsWizardState" in js
    assert "renderSettingsWizardAdvisor" in js
    assert "renderSettingsWizardStepSummaries" in js
    assert "updateSettingsWizardChecklist" in js
    assert "const SETTINGS_WIZARD_STEP_COUNT = 8;" in js
    assert "wizardAdvisorNextKicker" in (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")


def test_monitor_context_menu_exposes_desktop_actions():
    html = (ROOT / "static" / "monitor.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "monitor.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert 'id="sidebar-open-monitor"' in index
    assert 'id="sidebar-start-codex"' in index
    assert 'id="setting-desktop-monitor-enabled" type="checkbox" checked' in index
    assert "showFloatingMonitorNow" in index
    assert "startCodexFromQuickAction" in app_js
    assert "/api/codex/status" in app_js
    assert "confirmRestartRunningCodex" in app_js
    assert 'id="menu-btn"' in html
    assert 'data-action="start"' in html
    assert 'data-action="main"' in html
    assert 'data-action="settings"' in html
    assert 'data-action="hide"' in html
    assert 'data-action="exit"' in html
    assert 'id="provider-menu-items"' in html
    assert "showButtonMenu" in js
    assert "show_settings" in js
    assert "list_quick_providers" in js
    assert "switch_provider" in js
    assert "autoProvider" in js
    assert "/api/providers/focus" in js


def test_provider_request_preview_ui_is_wired():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "previewProviderRequest" in js
    assert "/request-preview-draft" in js
    assert "provider-request-preview" in js
    assert "requestHeadersRedacted" in js
    assert "renderProviderModelDetails" in js
    assert "readProviderModelsFromDetails" in js
    assert "providerModelDetailsTitle" in i18n
    assert "上下文窗口" in i18n


def test_provider_model_details_are_single_editing_surface():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "renderProviderModelBulkActions" in js
    assert "providerModelSingleSourceHint" in js
    assert "providerModelBulkActions" in i18n
    assert "modelDraftLabel" in i18n
    assert "renderTextarea('provider-models-text'" not in js
    assert "parseModelsText(document.getElementById('provider-models-text')" in js
    assert "请求头与高级策略" in i18n


def test_native_responses_and_codex_login_provider_locks_are_wired():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "NATIVE_LOCKED_CAPABILITIES" in js
    assert "renderResponsesModeSegment" in js
    assert "syncResponsesModeControls" in js
    assert "isCodexLoginProvider" in js
    assert "nativeCapabilitiesLocked" in js
    assert "codexLoginProviderReadOnly" in js
    assert "preserveLoginProxyModeDesc" in i18n


def test_media_route_guidance_ui_is_wired():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "guidance_keys" in js
    assert "action_keys" in js
    assert "mediaRouteGuidanceTitle" in js
    assert "guidance_key" in js
    assert "action_key" in js
    assert "mediaTextProviderNeedsFallback" in i18n
    assert "mediaNativeResponsesNeedsMediaProxy" in i18n
    assert "mediaConfigureMediaFallbackAction" in i18n


def test_user_facing_preview_copy_is_reframed_as_checks():
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    providers_js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    amr_js = (ROOT / "static" / "js" / "amr.js").read_text(encoding="utf-8")

    assert "历史用量来源" in i18n
    assert "请求路径检查" in i18n
    assert "图片生成能力检查" in i18n
    assert "审批规则测试" in i18n
    assert "将保存的 Codex 连接" in i18n
    assert "模型列表预览" not in i18n
    assert "图片/视频设置预览" not in i18n
    assert "审批预览" not in i18n
    assert "改动预览" not in i18n
    assert "renderCodexConnectionSummary" in providers_js
    assert "scheduleCodexConnectionCheck" in providers_js
    assert "renderAmrBoundaryCard" in amr_js
    assert "amrBoundaryProvider" in i18n
    assert "智能路由" in i18n
    assert "模型轮换" not in i18n


def test_official_login_start_keeps_safe_enhancement_copy_wired():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "startOfficialCodex" in js
    assert "startCodexWithSelectedMode" in js
    assert "renderCodexOfficialProviderViewer" in js
    assert "switchSelectedProvider" in js
    assert "renderCodexConnectionModeCard" in js
    assert "setCodexStartMode" in js
    assert "renderCodexEnhancementModeCard" in js
    assert "/api/codex/start" in app_js
    assert "ci-start-mode" in js
    assert "'preserve_login_proxy'" in js
    assert "start_mode: 'official_direct'" in js
    assert "official_mode: true" in js
    assert "startOfficialCodex" in i18n
    assert "startModePreserveLoginProxy" in i18n
    assert "startModeOfficialDirect" in i18n
    assert "检测到 Codex 登录" in i18n
    assert "智能路由会关闭" in i18n
    assert "modeOfficialDirectTitle" in i18n
    assert "切回官方并启动" in i18n
    assert "officialEnhancementModeDesc" in i18n
    assert "repairCodexConfigTemplate" in js
    assert "repairCodexSandboxPermissions" in js
    assert "/api/codex-integration/permissions-repair" in js
    assert "resetCodexForOfficialLogin" in js
    assert "codexStartStage" in i18n
    assert "officialAmrRiskNotice" in i18n
    assert "providerCoreCapabilitiesLocked" in i18n
    assert "cap-tools" not in js
    assert "cap-videos" not in js
    assert "media-default-video" not in js
    assert "route-sim-cap-tools" not in js
    assert "route-sim-cap-videos" not in js
    visible_api_formats = js.split("const VISIBLE_PROVIDER_API_FORMATS = [", 1)[1].split("];", 1)[0]
    assert "openai_videos" not in visible_api_formats
    assert "setting-codex-goals-enabled" in (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "features.goals" in i18n


def test_amr_frontend_context_window_keeps_unknown_zero_as_limiter():
    amr_js = (ROOT / "static" / "js" / "amr.js").read_text(encoding="utf-8")

    assert "Number.isFinite(value) && value >= 0" in amr_js
    assert "value > 0" not in amr_js


def test_amr_candidate_capabilities_are_inherited_from_providers():
    amr_js = (ROOT / "static" / "js" / "amr.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "getAmrCandidateCapabilities" in amr_js
    assert "data-amr-derived-capabilities" in amr_js
    assert "amrCandidateCapabilitiesInherited" in i18n
    assert "amrEditCapabilitiesInProviders" in i18n
    assert "data-amr-capability" not in amr_js
    assert "data-amr-route-capability" in amr_js


def test_monitor_uses_dense_sampling_and_official_usage_fallback():
    monitor_js = (ROOT / "static" / "js" / "monitor.js").read_text(encoding="utf-8")

    assert "const SPEED_SAMPLE_WINDOW_MS = 600000;" in monitor_js
    assert "const SPEED_SAMPLE_LIMIT = 120;" in monitor_js
    assert "const COST_SAMPLE_WINDOW_MS = 1800000;" in monitor_js
    assert "function isOfficialFocusProvider" in monitor_js
    assert "formatOfficialUsageLine(data)" in monitor_js
    assert "formatEstimatedCostLine(data)" in monitor_js
    assert "effective_cost_by_currency" in monitor_js
    assert "data.official_usage_default" in monitor_js


def test_readme_omits_external_project_body_references():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for content in (readme, readme_zh):
        assert "Code++" not in content
        assert "Codex++" not in content
        assert "CodexPlusPlus" not in content
        assert "cc-switch" not in content
        assert "真实修改测试必须由用户手动执行" not in content
        assert "Real mutation testing must be performed manually by the user" not in content


def test_readme_documents_codex_responses_wire_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for content in (readme, readme_zh):
        assert "POST /responses" in content
        assert 'wire_api = "responses"' in content
        assert "/images/generations" in content
        assert "openai/codex" in content


def test_health_endpoint_exposes_desktop_marker(monkeypatch):
    monkeypatch.setenv("CODEX_ENHANCE_MANAGER_DESKTOP", "1")
    monkeypatch.setenv("CODEX_ENHANCE_MANAGER_PORT", "51235")

    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    payload = flask_app.test_client().get("/api/health").get_json()

    assert payload["desktop_mode"] is True
    assert payload["desktop_port"] == "51235"
