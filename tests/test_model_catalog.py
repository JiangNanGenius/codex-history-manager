import tempfile
import unittest
from pathlib import Path

from capabilities import normalize_capabilities
from model_catalog import UnifiedModelCatalog
from providers import ProviderRegistry


class UnifiedModelCatalogTest(unittest.TestCase):
    def test_always_visible_includes_all_enabled_models(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [
                    {"id": "gpt-5", "enabled": True, "display_name": "GPT-5"},
                    {"id": "gpt-5-mini", "enabled": True, "display_name": "GPT-5 Mini"},
                ],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 2)
        self.assertEqual(catalog["entries"][0]["codex_model_id"], "openai/GPT-5")

    def test_selected_models_only_shows_selected(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "qwen",
                "enabled": True,
                "catalog_visibility": "selected_models",
                "models": [
                    {"id": "qwen3", "enabled": True, "selected": True},
                    {"id": "qwen-vl", "enabled": True, "selected": False},
                ],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 1)
        self.assertEqual(catalog["entries"][0]["codex_model_id"], "qwen/qwen3")

    def test_focus_provider_includes_all_enabled_models(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "qwen",
                "enabled": True,
                "catalog_visibility": "focused_only",
                "models": [
                    {"id": "qwen3", "enabled": True},
                    {"id": "qwen-vl", "enabled": True},
                ],
            }
        ]
        catalog = UnifiedModelCatalog(providers, focus_provider_id="p1").build_catalog()
        self.assertEqual(catalog["entry_count"], 2)

    def test_focus_provider_does_not_remove_always_visible(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "gpt-5", "enabled": True}],
            },
            {
                "id": "p2",
                "short_alias": "qwen",
                "enabled": True,
                "catalog_visibility": "focused_only",
                "models": [{"id": "qwen3", "enabled": True}],
            },
        ]
        catalog = UnifiedModelCatalog(providers, focus_provider_id="p2").build_catalog()
        ids = [e["codex_model_id"] for e in catalog["entries"]]
        self.assertIn("openai/gpt-5", ids)
        self.assertIn("qwen/qwen3", ids)
        self.assertEqual(len(ids), 2)

    def test_hidden_provider_not_shown(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "hidden",
                "enabled": True,
                "catalog_visibility": "hidden",
                "models": [{"id": "m1", "enabled": True}],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 0)

    def test_disabled_provider_not_shown(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "off",
                "enabled": False,
                "catalog_visibility": "always_visible",
                "models": [{"id": "m1", "enabled": True}],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 0)

    def test_injection_data_shape(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "gpt-5", "enabled": True, "display_name": "GPT-5"}],
            }
        ]
        injection = UnifiedModelCatalog(providers).build_injection_data()
        self.assertEqual(len(injection), 1)
        self.assertEqual(injection[0]["id"], "openai/GPT-5")
        self.assertEqual(injection[0]["name"], "GPT-5")

    def test_find_entry(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "gpt-5", "enabled": True}],
            }
        ]
        umc = UnifiedModelCatalog(providers)
        entry = umc.find_entry("openai/gpt-5")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["provider_id"], "p1")
        self.assertIsNone(umc.find_entry("nonexistent"))

    def test_alias_collision_uses_provider_prefix(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "gpt-4", "enabled": True}],
            },
            {
                "id": "p2",
                "short_alias": "qwen",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "gpt-4", "enabled": True}],
            },
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        ids = [e["codex_model_id"] for e in catalog["entries"]]
        self.assertIn("openai/gpt-4", ids)
        self.assertIn("qwen/gpt-4", ids)
        self.assertEqual(len(ids), 2)

    def test_duplicate_provider_alias_gets_visible_suffix_before_collision_fallback(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "dup",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "same-model", "enabled": True}],
            },
            {
                "id": "p2",
                "short_alias": "dup",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "same-model", "enabled": True}],
            },
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        ids = [e["codex_model_id"] for e in catalog["entries"]]
        self.assertEqual(ids, ["dup/same-model", "dup (p2)/same-model"])
        self.assertFalse(any(e["catalog_collision"] for e in catalog["entries"]))
        self.assertFalse(any("Catalog ID collision" in item for item in catalog["route_explanation"]))

    def test_duplicate_model_not_added_twice(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [
                    {"id": "gpt-4", "enabled": True},
                    {"id": "gpt-4", "enabled": True},
                ],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 1)
        self.assertEqual(catalog["entries"][0]["codex_model_id"], "openai/gpt-4")

    def test_invalid_visibility_skipped(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "bad",
                "enabled": True,
                "catalog_visibility": "invalid_value",
                "models": [{"id": "m1", "enabled": True}],
            }
        ]
        catalog = UnifiedModelCatalog(providers).build_catalog()
        self.assertEqual(catalog["entry_count"], 0)

    def test_entry_capabilities_fallback_to_provider(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "openai",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "capabilities": {"text": True, "vision": True},
                "models": [{"id": "gpt-4", "enabled": True}],
            }
        ]
        umc = UnifiedModelCatalog(providers)
        entry = umc.find_entry("openai/gpt-4")
        self.assertIsNotNone(entry)
        self.assertTrue(entry["capabilities"]["text"])
        self.assertTrue(entry["capabilities"]["vision"])

    def test_entry_legacy_normalized_model_caps_do_not_mask_provider_images(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "img",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "capabilities": {"text": False, "images": True},
                "models": [{"id": "auto", "enabled": True, "capabilities": normalize_capabilities(None)}],
            }
        ]
        umc = UnifiedModelCatalog(providers)
        entry = umc.find_entry("img/auto")
        self.assertIsNotNone(entry)
        self.assertFalse(entry["capabilities"]["text"])
        self.assertTrue(entry["capabilities"]["images"])

    def test_entry_infers_images_from_media_profile(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "native",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "api_format": "openai_responses",
                "capabilities": {"text": True},
                "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                "models": [{"id": "auto", "enabled": True, "capabilities": normalize_capabilities(None)}],
            }
        ]
        entry = UnifiedModelCatalog(providers).find_entry("native/auto")

        self.assertIsNotNone(entry)
        self.assertTrue(entry["capabilities"]["images"])

    def test_codex_api_key_mixin_preset_advertises_images_to_catalog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.import_preset("codex-api-key-mixin")

        catalog = UnifiedModelCatalog([provider]).build_catalog()
        entry = catalog["entries"][0] if catalog["entries"] else None

        self.assertIsNotNone(entry)
        self.assertEqual(entry["api_format"], "openai_responses")
        self.assertTrue(entry["capabilities"]["text"])
        self.assertTrue(entry["capabilities"]["vision"])
        self.assertTrue(entry["capabilities"]["images"])

    def test_entry_pricing_merges_model_over_provider(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "qwen",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "native_currency": "CNY",
                "pricing": {"input_per_million": 1.0, "output_per_million": 2.0},
                "models": [
                    {
                        "id": "qwen3",
                        "enabled": True,
                        "native_currency": "USD",
                        "pricing": {"input_per_million": 0.5},
                    }
                ],
            }
        ]
        entry = UnifiedModelCatalog(providers).find_entry("qwen/qwen3")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["native_currency"], "USD")
        self.assertEqual(entry["pricing"]["input_per_million"], 0.5)
        self.assertEqual(entry["pricing"]["output_per_million"], 2.0)
        self.assertTrue(entry["has_model_pricing"])

    def test_focused_provider_uses_visible_names_and_other_provider_primary_only(self):
        providers = [
            {
                "id": "ark-code-plan",
                "short_alias": "arkplan",
                "codex_visible_alias": "Ark Coding Plan",
                "enabled": True,
                "catalog_visibility": "hidden",
                "models": [
                    {
                        "id": "ark-code-latest",
                        "display_name": "Ark Code Latest",
                        "codex_visible_id": "Ark Code Latest",
                        "enabled": True,
                        "catalog_hidden": False,
                        "primary": True,
                    },
                    {
                        "id": "hidden-model",
                        "display_name": "Hidden Model",
                        "enabled": True,
                        "catalog_hidden": True,
                    },
                ],
            },
            {
                "id": "kimi-code",
                "short_alias": "kimi",
                "codex_visible_alias": "Kimi Code",
                "enabled": True,
                "catalog_visibility": "selected_models",
                "models": [
                    {
                        "id": "kimi-k2.6",
                        "display_name": "Kimi K2.6",
                        "enabled": True,
                        "catalog_hidden": False,
                        "primary": True,
                    },
                    {
                        "id": "kimi-extra",
                        "display_name": "Kimi Extra",
                        "enabled": True,
                        "catalog_hidden": False,
                        "selected": True,
                    },
                ],
            },
        ]

        catalog = UnifiedModelCatalog(providers, focus_provider_id="ark-code-plan").build_catalog()
        ids = [entry["codex_model_id"] for entry in catalog["entries"]]

        self.assertEqual(ids, ["Ark Coding Plan/Ark Code Latest", "Kimi Code/Kimi K2.6"])
        self.assertNotIn("Ark Coding Plan/Hidden Model", ids)
        self.assertTrue(catalog["entries"][0]["focused"])
        self.assertEqual(catalog["entries"][0]["upstream_model_id"], "ark-code-latest")

    def test_duplicate_visible_provider_alias_gets_stable_suffix(self):
        providers = [
            {
                "id": "p1",
                "short_alias": "a",
                "codex_visible_alias": "Shared Provider",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "m1", "display_name": "Model One", "enabled": True}],
            },
            {
                "id": "p2",
                "short_alias": "b",
                "codex_visible_alias": "Shared Provider",
                "enabled": True,
                "catalog_visibility": "always_visible",
                "models": [{"id": "m2", "display_name": "Model Two", "enabled": True}],
            },
        ]

        ids = [entry["codex_model_id"] for entry in UnifiedModelCatalog(providers).build_catalog()["entries"]]

        self.assertEqual(ids, ["Shared Provider/Model One", "Shared Provider (p2)/Model Two"])


if __name__ == "__main__":
    unittest.main()
