import copy
import unittest

from config import Config, DEFAULT_CONFIG
from local_proxy_auth import local_proxy_token_is_strong


class ConfigThemeTest(unittest.TestCase):
    def test_legacy_custom_theme_is_expanded_with_defaults(self):
        cfg = Config.__new__(Config)
        cfg._data = copy.deepcopy(DEFAULT_CONFIG)
        cfg._data["theme_custom"] = {"accent": "#111111"}

        cfg._normalize_storage_defaults()

        self.assertEqual(cfg._data["theme_custom"]["accent"], "#111111")
        self.assertEqual(cfg._data["theme_custom"]["background"], DEFAULT_CONFIG["theme_custom"]["background"])
        self.assertEqual(cfg._data["theme_custom"]["text_primary"], DEFAULT_CONFIG["theme_custom"]["text_primary"])
        self.assertEqual(cfg._data["theme_custom"]["text_muted"], DEFAULT_CONFIG["theme_custom"]["text_muted"])

    def test_startup_defaults_are_added_and_normalized(self):
        cfg = Config.__new__(Config)
        cfg._data = {"startup_enabled": True, "startup_mode": "unexpected"}

        cfg._normalize_storage_defaults()

        self.assertEqual(cfg._data["startup_mode"], "startup_folder")
        self.assertFalse(cfg._data["startup_auto_elevate"])
        self.assertEqual(cfg._data["startup_task_name"], DEFAULT_CONFIG["startup_task_name"])
        self.assertEqual(cfg._data["startup_shortcut_name"], DEFAULT_CONFIG["startup_shortcut_name"])

    def test_disabled_startup_clears_auto_elevate(self):
        cfg = Config.__new__(Config)
        cfg._data = {
            "startup_enabled": False,
            "startup_mode": "scheduled_task_highest",
            "startup_auto_elevate": True,
        }

        cfg._normalize_storage_defaults()

        self.assertEqual(cfg._data["startup_mode"], "disabled")
        self.assertFalse(cfg._data["startup_auto_elevate"])

    def test_auto_approval_prompt_default_is_restored_when_blank(self):
        cfg = Config.__new__(Config)
        cfg._data = {"auto_approval_system_prompt": ""}

        cfg._normalize_storage_defaults()

        self.assertEqual(
            cfg._data["auto_approval_system_prompt"],
            DEFAULT_CONFIG["auto_approval_system_prompt"],
        )

    def test_close_button_action_defaults_to_ask_when_invalid(self):
        cfg = Config.__new__(Config)
        cfg._data = {"close_button_action": "unexpected"}

        cfg._normalize_storage_defaults()

        self.assertEqual(cfg._data["close_button_action"], "ask")

    def test_desktop_launch_action_defaults_to_show_window_when_invalid(self):
        cfg = Config.__new__(Config)
        cfg._data = {"desktop_launch_action": "unexpected"}

        cfg._normalize_storage_defaults()

        self.assertEqual(cfg._data["desktop_launch_action"], "show_window")

    def test_desktop_monitor_enabled_defaults_and_string_normalization(self):
        cfg = Config.__new__(Config)
        cfg._data = {}
        cfg._normalize_storage_defaults()
        self.assertTrue(cfg._data["desktop_monitor_enabled"])
        self.assertEqual(cfg._data["desktop_monitor_opacity"], 88)

        cfg._data["desktop_monitor_enabled"] = "false"
        cfg._normalize_storage_defaults()
        self.assertFalse(cfg._data["desktop_monitor_enabled"])

    def test_desktop_monitor_opacity_accepts_percent_or_fraction(self):
        cfg = Config.__new__(Config)
        cfg._data = {"desktop_monitor_opacity": "0.72"}
        cfg._normalize_storage_defaults()
        self.assertEqual(cfg._data["desktop_monitor_opacity"], 72)

        cfg._data["desktop_monitor_opacity"] = 120
        cfg._normalize_storage_defaults()
        self.assertEqual(cfg._data["desktop_monitor_opacity"], 100)

        cfg._data["desktop_monitor_opacity"] = 10
        cfg._normalize_storage_defaults()
        self.assertEqual(cfg._data["desktop_monitor_opacity"], 35)

    def test_update_settings_default_and_string_normalization(self):
        cfg = Config.__new__(Config)
        cfg._data = {}
        cfg._normalize_storage_defaults()
        self.assertTrue(cfg._data["update_check_enabled"])
        self.assertFalse(cfg._data["update_include_prerelease"])
        self.assertFalse(cfg._data["plugin_unlock_enabled"])

        cfg._data["update_check_enabled"] = "no"
        cfg._data["update_include_prerelease"] = "yes"
        cfg._data["plugin_unlock_enabled"] = "yes"
        cfg._data["codex_sandbox_auto_repair_enabled"] = "yes"
        cfg._normalize_storage_defaults()
        self.assertFalse(cfg._data["update_check_enabled"])
        self.assertTrue(cfg._data["update_include_prerelease"])
        self.assertTrue(cfg._data["plugin_unlock_enabled"])
        self.assertTrue(cfg._data["codex_sandbox_auto_repair_enabled"])

    def test_local_proxy_token_is_generated_when_missing_or_weak(self):
        cfg = Config.__new__(Config)
        cfg._data = {"local_proxy_bearer_token": "codex-enhance-manager-local"}

        changed = cfg._ensure_local_proxy_token()

        self.assertTrue(changed)
        self.assertTrue(local_proxy_token_is_strong(cfg._data["local_proxy_bearer_token"]))


if __name__ == "__main__":
    unittest.main()
