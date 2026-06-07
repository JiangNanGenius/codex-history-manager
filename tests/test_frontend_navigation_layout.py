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
