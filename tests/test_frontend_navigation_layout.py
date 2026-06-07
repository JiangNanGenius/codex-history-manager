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
    assert "https://github.com/JiangNanGenius/Codex-Enhance-Manager" in html
    assert "restoreAutoApprovalPromptDefault" in js
    assert "const SETTINGS_WIZARD_STEP_COUNT = 8;" in js
