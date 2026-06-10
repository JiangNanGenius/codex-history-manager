import unittest

from reasoning_policy import (
    apply_reasoning_policy_to_chat_request,
    apply_reasoning_policy_to_responses_request,
    infer_reasoning_effort_policy,
    map_reasoning_effort,
    normalize_reasoning_effort_profile,
)


class ReasoningPolicyTest(unittest.TestCase):
    def test_xai_grok_43_accepts_documented_efforts_and_clamps(self):
        provider = {"id": "xai-grok", "base_url": "https://api.x.ai/v1", "api_format": "openai_responses"}
        model = {"id": "grok-4.3"}

        policy = infer_reasoning_effort_policy(provider, model, api_format="openai_responses")

        self.assertTrue(policy["supports_reasoning_effort"])
        self.assertEqual(policy["reasoning_efforts"], ["none", "low", "medium", "high"])
        self.assertEqual(policy["reasoning_effort_parameter"], "reasoning.effort")
        self.assertEqual(map_reasoning_effort("max", policy), "high")

        request = apply_reasoning_policy_to_responses_request(
            {"model": "grok-4.3", "reasoning": {"effort": "max", "summary": "auto"}},
            provider,
            model,
            api_format="openai_responses",
        )

        self.assertEqual(request["reasoning"], {"summary": "auto", "effort": "high"})

    def test_xai_build_model_drops_unsupported_effort(self):
        provider = {"id": "xai-grok", "base_url": "https://api.x.ai/v1"}
        model = {"id": "grok-build-0.1"}

        request = apply_reasoning_policy_to_responses_request(
            {"model": "grok-build-0.1", "reasoning": {"effort": "high"}},
            provider,
            model,
            api_format="openai_responses",
        )

        self.assertNotIn("reasoning", request)

    def test_xai_multi_agent_effort_uses_agent_count_semantics(self):
        provider = {"id": "xai-grok", "base_url": "https://api.x.ai/v1"}
        model = {"id": "grok-4.20-multi-agent-0309"}

        request = apply_reasoning_policy_to_responses_request(
            {"model": "grok-4.20-multi-agent-0309", "reasoning": {"effort": "xhigh"}},
            provider,
            model,
            api_format="openai_responses",
        )

        self.assertEqual(request["reasoning"], {"effort": "xhigh"})

        none_request = apply_reasoning_policy_to_responses_request(
            {"model": "grok-4.20-multi-agent-0309", "reasoning": {"effort": "none"}},
            provider,
            model,
            api_format="openai_responses",
        )
        self.assertNotIn("reasoning", none_request)

    def test_anthropic_effort_moves_to_output_config(self):
        provider = {"id": "anthropic-claude", "api_format": "anthropic"}
        model = {
            "id": "claude-sonnet-4-6",
            "reasoning_efforts": ["low", "medium", "high", "max"],
            "reasoning_effort_parameter": "output_config.effort",
        }

        request = apply_reasoning_policy_to_responses_request(
            {"model": "claude-sonnet-4-6", "reasoning": {"effort": "max"}, "output_config": {"foo": "bar"}},
            provider,
            model,
            api_format="anthropic",
        )

        self.assertNotIn("reasoning", request)
        self.assertEqual(request["output_config"], {"foo": "bar", "effort": "max"})

    def test_chat_request_uses_chat_reasoning_effort_when_model_supports_it(self):
        provider = {"id": "deepseek", "api_format": "openai_chat"}
        model = {
            "id": "deepseek-reasoner",
            "reasoning_efforts": ["low", "medium", "high"],
            "reasoning_effort_parameter": "auto",
        }

        request = apply_reasoning_policy_to_chat_request(
            {"model": "deepseek-reasoner", "messages": [], "reasoning_effort": "max"},
            {},
            provider,
            model,
            api_format="openai_chat",
        )

        self.assertEqual(request["reasoning_effort"], "high")

    def test_reasoning_profile_accepts_legacy_effort_aliases(self):
        profile = normalize_reasoning_effort_profile(
            {
                "supported_efforts": "low, medium, x-high",
                "default_effort": "very-high",
                "api_parameter": "reasoning.effort",
            }
        )

        self.assertTrue(profile["supports_reasoning_effort"])
        self.assertEqual(profile["reasoning_efforts"], ["low", "medium", "xhigh"])
        self.assertEqual(profile["reasoning_effort_default"], "xhigh")
        self.assertEqual(profile["reasoning_effort_parameter"], "reasoning.effort")


if __name__ == "__main__":
    unittest.main()
