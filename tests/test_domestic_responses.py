import json
import unittest

from domestic_responses import (
    assess_domestic_responses_request,
    build_domestic_responses_probe_preview,
    domestic_responses_url,
    resolve_domestic_responses_profile,
    sanitize_domestic_responses_request,
)


class DomesticResponsesProfileTest(unittest.TestCase):
    def test_bailian_probe_preview_redacts_secrets_and_keeps_user_agent(self):
        provider = {
            "id": "alibaba-bailian",
            "kind": "alibaba_bailian",
            "short_alias": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "testkey-secret",
            "headers": {
                "Authorization": "Bearer testkey-secret",
                "X-Api-Key": "testkey-secret",
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
        self.assertNotIn("testkey-secret", serialized)
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

    def test_sanitize_removes_unsupported_tools_and_keeps_allowed_ones(self):
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
        request = {
            "model": "qwen/qwen-plus",
            "input": "test",
            "tools": [
                {"type": "function", "name": "get_weather"},
                {"type": "custom", "name": "shell"},
                {"type": "image_generation", "name": "dalle"},
                {"type": "web_search", "name": "search"},
            ],
        }
        sanitized, warnings = sanitize_domestic_responses_request(provider, request)
        # custom is removed; image_generation is replaced with generate_image function tool
        self.assertEqual(len(sanitized["tools"]), 3)
        self.assertEqual(sanitized["tools"][0]["type"], "function")
        self.assertEqual(sanitized["tools"][0]["name"], "get_weather")
        self.assertEqual(sanitized["tools"][1]["type"], "function")
        self.assertEqual(sanitized["tools"][1]["function"]["name"], "generate_image")
        self.assertEqual(sanitized["tools"][2]["type"], "web_search")
        self.assertTrue(any("custom" in w for w in warnings))
        self.assertTrue(any("image_generation" in w for w in warnings))
        self.assertTrue(sanitized.get("_cem_image_gen_fallback"))

    def test_sanitize_removes_unsupported_input_content_types(self):
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
        request = {
            "model": "qwen/qwen-plus",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "hello"},
                        {"type": "input_audio", "audio_url": "https://example.test/a.mp3"},
                        {"type": "input_image", "image_url": "https://example.test/a.png"},
                    ],
                }
            ],
        }
        sanitized, warnings = sanitize_domestic_responses_request(provider, request)
        self.assertEqual(len(sanitized["input"]), 1)
        self.assertEqual(len(sanitized["input"][0]["content"]), 2)
        self.assertEqual(sanitized["input"][0]["content"][0]["type"], "input_text")
        self.assertEqual(sanitized["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(any("input_audio" in w for w in warnings))

    def test_sanitize_returns_original_for_non_domestic_provider(self):
        provider = {"id": "openai", "base_url": "https://api.openai.com/v1"}
        request = {"model": "gpt-4", "tools": [{"type": "custom", "name": "x"}]}
        sanitized, warnings = sanitize_domestic_responses_request(provider, request)
        self.assertEqual(sanitized, request)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
