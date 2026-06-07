import copy
import unittest

from config import Config, DEFAULT_CONFIG


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


if __name__ == "__main__":
    unittest.main()
