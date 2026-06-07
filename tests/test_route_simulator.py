import unittest

from app import (
    _normalize_route_capabilities,
    _route_candidate_matches_model,
    _route_candidate_status,
    _selected_provider_models_to_amr_candidates,
)


class RouteSimulatorHelpersTest(unittest.TestCase):
    def test_normalize_route_capabilities_accepts_list_and_ignores_unknowns(self):
        result = _normalize_route_capabilities({
            "capabilities": ["text", "vision", "unknown", "tools", "custom_tools"],
        })

        self.assertEqual(result, {"text", "vision", "tools", "custom_tools"})

    def test_normalize_route_capabilities_defaults_to_text(self):
        result = _normalize_route_capabilities({"capabilities": ["unknown"]})

        self.assertEqual(result, {"text"})

    def test_model_filter_matches_codex_model_id_and_candidate_id(self):
        candidate = {
            "id": "provider-1/qwen-plus",
            "provider_id": "provider-1",
            "short_alias": "qwen",
            "model_id": "qwen-plus",
            "codex_model_id": "qwen/qwen-plus",
            "display_name": "Qwen Plus",
        }

        self.assertTrue(_route_candidate_matches_model(candidate, "qwen/qwen-plus"))
        self.assertTrue(_route_candidate_matches_model(candidate, "provider-1/qwen-plus"))
        self.assertTrue(_route_candidate_matches_model(candidate, "Qwen Plus"))
        self.assertFalse(_route_candidate_matches_model(candidate, "openai/gpt-5"))

    def test_candidate_status_reports_capability_context_and_model_filters(self):
        candidates = [
            {
                "id": "p/text",
                "provider_id": "p",
                "model_id": "text",
                "codex_model_id": "p/text",
                "priority": 1,
                "context_window": 64000,
                "capabilities": {"text": True, "vision": False},
            },
            {
                "id": "p/vision",
                "provider_id": "p",
                "model_id": "vision",
                "codex_model_id": "p/vision",
                "priority": 2,
                "context_window": 128000,
                "capabilities": {"text": True, "vision": True},
            },
        ]

        rows = _route_candidate_status(candidates, {"text", "vision"}, 100000, "p/vision")

        self.assertFalse(rows[0]["available"])
        self.assertEqual(rows[0]["missing_capabilities"], ["vision"])
        self.assertFalse(rows[0]["context_match"])
        self.assertFalse(rows[0]["model_match"])
        self.assertTrue(rows[1]["available"])
        self.assertTrue(rows[1]["capability_match"])
        self.assertTrue(rows[1]["context_match"])
        self.assertTrue(rows[1]["model_match"])

    def test_selected_provider_models_to_amr_candidates_filters_and_merges_caps(self):
        provider = {
            "id": "provider-1",
            "enabled": True,
            "capabilities": {"text": True, "vision": False, "tools": True},
            "models": [
                {
                    "id": "text",
                    "selected": True,
                    "enabled": True,
                    "context_window": "128000",
                    "capabilities": {"vision": True},
                },
                {"id": "unselected", "selected": False, "enabled": True},
                {"id": "disabled", "selected": True, "enabled": False},
            ],
        }

        candidates = _selected_provider_models_to_amr_candidates(provider, 2)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "provider-1/text")
        self.assertEqual(candidates[0]["priority"], 2)
        self.assertEqual(candidates[0]["context_window"], 128000)
        self.assertTrue(candidates[0]["capabilities"]["text"])
        self.assertTrue(candidates[0]["capabilities"]["vision"])
        self.assertTrue(candidates[0]["capabilities"]["tools"])

    def test_selected_provider_models_to_amr_candidates_skips_disabled_provider(self):
        provider = {
            "id": "provider-1",
            "enabled": False,
            "models": [{"id": "text", "selected": True, "enabled": True}],
        }

        candidates = _selected_provider_models_to_amr_candidates(provider, 2)

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
