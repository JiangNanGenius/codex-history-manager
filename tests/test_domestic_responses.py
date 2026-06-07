import json
import unittest

from domestic_responses import (
    assess_domestic_responses_request,
    build_domestic_responses_probe_preview,
    domestic_responses_url,
    resolve_domestic_responses_profile,
)


class DomesticResponsesProfileTest(unittest.TestCase):
    def test_bailian_probe_preview_redacts_secrets_and_keeps_user_agent(self):
        provider = {
            "id": "alibaba-bailian",
            "kind": "alibaba_bailian",
            "short_alias": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-secret",
            "headers": {
                "Authorization": "Bearer sk-secret",
                "X-Api-Key": "sk-secret",
                "User-Agent": "CustomUA/1.0",
            },
            "responses_profile": {
                "domestic_responses": True,
                "profile_id": "alibaba_bailian",
                "partial_compatibility": True,
                "requires_adapter": True,
            },
            "models": [{"id": "qwen3-coder-plus", "enabled": True}],
        }

        preview = build_domestic_responses_probe_preview(provider)
        serialized = json.dumps(preview, ensure_ascii=False)

        self.assertTrue(preview["available"])
        self.assertEqual(preview["endpoint_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1/responses")
        self.assertEqual(preview["headers_preview"]["User-Agent"], "CustomUA/1.0")
        self.assertNotIn("sk-secret", serialized)
        self.assertFalse(preview["network_request_performed"])
        self.assertTrue(preview["manual_only"])

    def test_bailian_allows_verified_input_image_but_blocks_custom_tools(self):
        provider = {
            "id": "bailian",
            "short_alias": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "responses_profile": {
                "domestic_responses": True,
                "profile_id": "alibaba_bailian",
                "partial_compatibility": True,
                "requires_adapter": True,
            },
        }

        image_request = {
            "model": "qwen/qwen-plus",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe"},
                        {"type": "input_image", "image_url": "https://example.test/a.png"},
                    ],
                }
            ],
        }
        image_report = assess_domestic_responses_request(provider, image_request)
        self.assertTrue(image_report["safe_to_forward"])

        custom_tool_report = assess_domestic_responses_request(
            provider,
            {"model": "qwen/qwen-plus", "input": "x", "tools": [{"type": "custom", "name": "shell"}]},
        )
        self.assertFalse(custom_tool_report["safe_to_forward"])
        self.assertIn("unsupported tool types: custom", custom_tool_report["blocking_issues"])

    def test_volcengine_profile_resolves_from_base_url_and_builds_endpoint(self):
        provider = {
            "id": "ark-main",
            "short_alias": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "responses_profile": {"domestic_responses": True},
        }

        profile = resolve_domestic_responses_profile(provider)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["profile_id"], "volcengine_ark")
        self.assertEqual(domestic_responses_url(provider), "https://ark.cn-beijing.volces.com/api/v3/responses")

    def test_non_domestic_probe_preview_is_unavailable(self):
        preview = build_domestic_responses_probe_preview({
            "id": "openai",
            "base_url": "https://api.openai.com/v1",
            "responses_profile": {"domestic_responses": False},
        })

        self.assertFalse(preview["available"])
        self.assertFalse(preview["network_request_performed"])


if __name__ == "__main__":
    unittest.main()
