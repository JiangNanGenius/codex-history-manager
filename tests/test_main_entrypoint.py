import unittest
from unittest.mock import patch

import webview

import main


class MainEntrypointTest(unittest.TestCase):
    def test_webview_smoke_creation_uses_valid_window_options(self):
        original_windows = list(webview.windows)

        self.assertTrue(main._smoke_test_webview_window_creation())
        self.assertEqual(webview.windows, original_windows)

    def test_monitor_window_uses_stable_non_white_background(self):
        original_windows = list(webview.windows)
        try:
            _, monitor = main._create_desktop_windows(main.DesktopApi())

            self.assertFalse(monitor.transparent)
            self.assertEqual(monitor.background_color, main.WEBVIEW_MONITOR_BACKGROUND)
            self.assertEqual(main.WEBVIEW_MONITOR_BACKGROUND, "#111827")
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


if __name__ == "__main__":
    unittest.main()
