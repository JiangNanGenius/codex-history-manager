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

    def test_configured_close_action_skips_prompt(self):
        with patch.object(main, "_configured_close_action", return_value="exit"):
            self.assertEqual(main._ask_close_action(None), "exit")
        with patch.object(main, "_configured_close_action", return_value="tray"):
            self.assertEqual(main._ask_close_action(None), "tray")


if __name__ == "__main__":
    unittest.main()
