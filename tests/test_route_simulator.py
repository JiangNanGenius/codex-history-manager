import unittest

from app import (
    _normalize_route_capabilities,
    _route_candidate_matches_model,
    _route_candidate_status,
)


class RouteSimulatorHelpersTest(unittest.TestCase):
    def test_normalize_route_capabilities_accepts_list_and_ignores_unknowns(self):
        result = _normalize_route_capabilities({
            "capabilities": ["text", "vision", "unknown", "tools"],
        })

        self.assertEqual(result, {"text", "vision", "tools"})

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


if __name__ == "__main__":
    unittest.main()
