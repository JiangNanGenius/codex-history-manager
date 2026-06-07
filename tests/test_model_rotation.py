import time
import unittest

from model_rotation import AdaptiveModelRotation


class AdaptiveModelRotationTest(unittest.TestCase):
    def test_route_priority_one_for_text(self):
        groups = [
            {
                "id": "coder-pro",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True, "vision": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("coder-pro", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["provider_id"], "openai")
        self.assertEqual(decision["model_id"], "gpt-5")

    def test_route_vision_to_capable_candidate(self):
        groups = [
            {
                "id": "vision-group",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen-vl", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True, "vision": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("vision-group", required_capabilities={"text", "vision"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["provider_id"], "qwen")
        self.assertEqual(decision["model_id"], "qwen-vl")

    def test_capability_error_when_no_candidate_supports(self):
        groups = [
            {
                "id": "text-only",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("text-only", required_capabilities={"text", "vision"})
        self.assertFalse(decision["success"])
        self.assertIn("No candidate supports", decision["error"])

    def test_context_window_rejection(self):
        groups = [
            {
                "id": "small-ctx",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 64000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("small-ctx", required_capabilities={"text"}, required_context=100000)
        self.assertFalse(decision["success"])
        self.assertIn("Context window too large", decision["error"])

    def test_cooldown_skips_failed_candidate(self):
        groups = [
            {
                "id": "failover",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        amr.report_failure("c1", cooldown_seconds=300)
        decision = amr.route("failover", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c2")

    def test_group_context_window_is_minimum(self):
        groups = [
            {
                "id": "mixed-ctx",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 64000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        self.assertEqual(amr.get_group_context_window("mixed-ctx"), 64000)

    def test_list_groups_shows_limiting_candidate(self):
        groups = [
            {
                "id": "mixed-ctx",
                "display_name": "Mixed Context",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 64000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        info = amr.list_groups()
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["effective_context_window"], 64000)
        self.assertEqual(info[0]["limiting_candidate_id"], "c2")

    def test_cooldown_expires_after_timeout(self):
        groups = [
            {
                "id": "failover",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        amr.report_failure("c1", cooldown_seconds=0.1)
        decision = amr.route("failover", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c2")
        time.sleep(0.2)
        decision = amr.route("failover", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c1")

    def test_equal_priority_tie_break_by_id(self):
        groups = [
            {
                "id": "tie-group",
                "candidates": [
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 1, "enabled": True, "context_window": 128000, "capabilities": {"text": True}},
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("tie-group", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c1")

    def test_disabled_candidate_excluded(self):
        groups = [
            {
                "id": "mixed",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": False, "context_window": 256000, "capabilities": {"text": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen3", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        decision = amr.route("mixed", required_capabilities={"text"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c2")

    def test_report_failure_does_not_affect_capability_routing(self):
        groups = [
            {
                "id": "vision-group",
                "candidates": [
                    {"id": "c1", "provider_id": "openai", "model_id": "gpt-5", "priority": 1, "enabled": True, "context_window": 256000, "capabilities": {"text": True, "vision": True}},
                    {"id": "c2", "provider_id": "qwen", "model_id": "qwen-vl", "priority": 2, "enabled": True, "context_window": 128000, "capabilities": {"text": True, "vision": True}},
                ],
            }
        ]
        amr = AdaptiveModelRotation(groups)
        amr.report_failure("c1", cooldown_seconds=300)
        decision = amr.route("vision-group", required_capabilities={"text", "vision"})
        self.assertTrue(decision["success"])
        self.assertEqual(decision["candidate_id"], "c2")


if __name__ == "__main__":
    unittest.main()
