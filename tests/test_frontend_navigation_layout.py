from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    assert 'href="/favicon.ico"' in html
    assert 'src="/app-icon.png"' in html
    assert "app-logo-shell" in html
    assert "startupElevationHelp" in html
    assert "checkForUpdates" in html
    assert "downloadLatestUpdate" in js
    assert "/api/updates/check" in js
    assert "/api/updates/download" in js
    assert "plugin_unlock_enabled" in js
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

    assert "previewProviderRequest" in js
    assert "/request-preview-draft" in js
    assert "provider-request-preview" in js
    assert "requestHeadersRedacted" in js


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
    assert "图片/视频生成能力检查" in i18n
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


def test_official_login_start_keeps_safe_enhancement_copy_wired():
    js = (ROOT / "static" / "js" / "providers.js").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "js" / "i18n.js").read_text(encoding="utf-8")

    assert "startOfficialCodex" in js
    assert "startCodexWithSelectedMode" in js
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
    assert "AMR 和模型轮换会关闭" in i18n
    assert "officialEnhancementModeDesc" in i18n
    assert "repairCodexConfigTemplate" in js
    assert "resetCodexForOfficialLogin" in js
    assert "codexStartStage" in i18n
    assert "setting-codex-goals-enabled" in (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "features.goals" in i18n


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


def test_health_endpoint_exposes_desktop_marker(monkeypatch):
    monkeypatch.setenv("CODEX_ENHANCE_MANAGER_DESKTOP", "1")
    monkeypatch.setenv("CODEX_ENHANCE_MANAGER_PORT", "51235")

    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    payload = flask_app.test_client().get("/api/health").get_json()

    assert payload["desktop_mode"] is True
    assert payload["desktop_port"] == "51235"
