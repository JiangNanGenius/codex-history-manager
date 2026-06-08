import unittest
from unittest.mock import patch

import webview

import main


class MainEntrypointTest(unittest.TestCase):
    def test_webview_smoke_creation_uses_valid_window_options(self):
        original_windows = list(webview.windows)

        self.assertTrue(main._smoke_test_webview_window_creation())
        self.assertEqual(webview.windows, original_windows)

    def test_pyinstaller_parent_watchdog_skips_unfrozen_process(self):
        with patch.object(main.sys, "frozen", False, create=True):
            self.assertFalse(main._start_pyinstaller_parent_watchdog())

    def test_monitor_window_uses_stable_non_white_background_and_auto_shows(self):
        original_windows = list(webview.windows)
        try:
            with patch.object(main, "_monitor_auto_show_enabled", return_value=True):
                _, monitor = main._create_desktop_windows(main.DesktopApi())

            self.assertFalse(monitor.transparent)
            self.assertEqual(monitor.background_color, main.WEBVIEW_MONITOR_BACKGROUND)
            self.assertEqual(main.WEBVIEW_MONITOR_BACKGROUND, "#111827")
            self.assertFalse(monitor.hidden)
        finally:
            webview.windows[:] = original_windows

    def test_monitor_window_can_be_disabled_from_settings(self):
        original_windows = list(webview.windows)
        try:
            with patch.object(main, "_monitor_auto_show_enabled", return_value=False):
                _, monitor = main._create_desktop_windows(main.DesktopApi())

            self.assertTrue(monitor.hidden)
        finally:
            webview.windows[:] = original_windows

    def test_monitor_window_defaults_away_from_left_navigation(self):
        original_windows = list(webview.windows)
        try:
            with patch.object(main, "_default_monitor_position", return_value=(1440, 28)):
                _, monitor = main._create_desktop_windows(main.DesktopApi())

            self.assertGreaterEqual(monitor.initial_x, 1440)
            self.assertEqual(monitor.initial_y, 28)
        finally:
            webview.windows[:] = original_windows

    def test_show_monitor_reports_not_ready_instead_of_dynamic_create(self):
        original = main.monitor_window
        try:
            main.monitor_window = None
            result = main._show_monitor()

            self.assertFalse(result["success"])
            self.assertIn("not ready", result["error"])
        finally:
            main.monitor_window = original

    def test_show_monitor_returns_success_for_precreated_window(self):
        class FakeWindow:
            def __init__(self):
                self.shown = False
                self.restored = False
                self.size = None

            def show(self):
                self.shown = True

            def restore(self):
                self.restored = True

            def resize(self, width, height):
                self.size = (width, height)

        original = main.monitor_window
        fake = FakeWindow()
        try:
            main.monitor_window = fake
            result = main._show_monitor()

            self.assertTrue(result["success"])
            self.assertTrue(fake.shown)
            self.assertTrue(fake.restored)
            self.assertEqual(fake.size, (main.MONITOR_WINDOW_WIDTH, main.MONITOR_WINDOW_EXPANDED_HEIGHT))
        finally:
            main.monitor_window = original

    def test_show_monitor_recreates_missing_window_when_desktop_api_exists(self):
        class FakeWindow:
            def __init__(self):
                self.shown = False
                self.restored = False
                self.moved = None
                self.size = None
                self.on_top = False

            def show(self):
                self.shown = True

            def restore(self):
                self.restored = True

            def move(self, x, y):
                self.moved = (x, y)

            def resize(self, width, height):
                self.size = (width, height)

        original_window = main.monitor_window
        original_api = main.desktop_api
        fake = FakeWindow()
        try:
            main.monitor_window = None
            main.desktop_api = main.DesktopApi()
            with patch.object(main, "_create_monitor_window", return_value=fake):
                result = main._show_monitor()

            self.assertTrue(result["success"])
            self.assertIs(main.monitor_window, fake)
            self.assertTrue(fake.shown)
            self.assertTrue(fake.restored)
            self.assertEqual(fake.size, (main.MONITOR_WINDOW_WIDTH, main.MONITOR_WINDOW_EXPANDED_HEIGHT))
        finally:
            main.monitor_window = original_window
            main.desktop_api = original_api

    def test_show_settings_restores_main_window_and_navigates(self):
        class FakeWindow:
            def __init__(self):
                self.shown = False
                self.restored = False
                self.js = []

            def show(self):
                self.shown = True

            def restore(self):
                self.restored = True

            def evaluate_js(self, script):
                self.js.append(script)

        original = main.main_window
        fake = FakeWindow()
        try:
            main.main_window = fake
            result = main.DesktopApi().show_settings()

            self.assertTrue(result["success"])
            self.assertTrue(fake.shown)
            self.assertTrue(fake.restored)
            self.assertEqual(fake.js, ['navigateTo("settings")'])
        finally:
            main.main_window = original

    def test_configured_close_action_skips_prompt(self):
        with patch.object(main, "_configured_close_action", return_value="exit"):
            self.assertEqual(main._ask_close_action(None), "exit")
        with patch.object(main, "_configured_close_action", return_value="tray"):
            self.assertEqual(main._ask_close_action(None), "tray")

    def test_webview_started_schedules_default_monitor(self):
        calls = []

        class FakeTimer:
            def __init__(self, delay, callback):
                self.delay = delay
                self.callback = callback
                self.daemon = False

            def start(self):
                calls.append(("timer", self.delay, self.daemon))
                self.callback()

        with patch.object(main, "_monitor_auto_show_enabled", return_value=True), \
                patch.object(main, "_show_monitor", side_effect=lambda: calls.append("show")), \
                patch.object(main.threading, "Timer", FakeTimer):
            self.assertTrue(main._on_webview_started())

        self.assertEqual(calls, [("timer", 0.45, True), "show"])

    def test_webview_started_respects_disabled_monitor_setting(self):
        with patch.object(main, "_monitor_auto_show_enabled", return_value=False), \
                patch.object(main, "_show_monitor") as show_monitor:
            self.assertFalse(main._on_webview_started())
            show_monitor.assert_not_called()

    def test_tray_menu_texts_cover_desktop_actions(self):
        self.assertEqual(main.TRAY_MENU_TEXT["show_main"], "显示主窗口")
        self.assertEqual(main.TRAY_MENU_TEXT["show_settings"], "打开设置")
        self.assertEqual(main.TRAY_MENU_TEXT["show_monitor"], "显示悬浮窗")
        self.assertEqual(main.TRAY_MENU_TEXT["hide_monitor"], "隐藏悬浮窗")
        self.assertEqual(main.TRAY_MENU_TEXT["start_codex"], "启动 Codex")
        self.assertEqual(main.TRAY_MENU_TEXT["quick_switch_provider"], "快速切换供应商")
        self.assertEqual(main.TRAY_MENU_TEXT["auto_provider"], "自动选择供应商")
        self.assertEqual(main.TRAY_MENU_TEXT["exit"], "退出程序")


if __name__ == "__main__":
    unittest.main()
