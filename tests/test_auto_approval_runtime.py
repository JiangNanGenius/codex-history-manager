import json
import unittest
from unittest.mock import patch

from auto_approval_runtime import AutoApprovalModelReviewer, AutoApprovalRuntimeError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def open(self, request, timeout=0):
        self.calls.append((request, timeout))
        return FakeResponse(self.payload)


class AutoApprovalRuntimeTest(unittest.TestCase):
    def _action(self):
        return {
            "kind": "image_generation",
            "summary": "image submit via provider 'image-main'",
            "media": {"kind": "image", "operation": "submit"},
        }

    def _profile(self, reviewer_model="qwen/qwen3-coder-plus"):
        return {
            "mode": "proxy_auto_approve",
            "reviewer_model": reviewer_model,
            "timeout_ms": 2500,
            "max_retries": 0,
            "require_structured_json": True,
        }

    def test_chat_reviewer_uses_prefixed_model_user_agent_and_json_mode(self):
        providers = [{
            "id": "qwen",
            "short_alias": "qwen",
            "enabled": True,
            "api_format": "openai_chat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "testkey-test",
            "user_agent": "CodexEnhance/Test",
            "capabilities": {"text": True},
            "models": [{"id": "qwen3-coder-plus", "enabled": True}],
        }]
        opener = FakeOpener({
            "choices": [{
                "message": {
                    "content": "{\"decision\":\"accept\",\"risk_level\":\"low\",\"reason\":\"Allowed.\"}"
                }
            }]
        })

        with patch("auto_approval_runtime.urllib.request.build_opener", return_value=opener):
            result = AutoApprovalModelReviewer(lambda: providers).review(
                self._action(),
                self._profile(),
                {"id": "image-main"},
            )

        request, timeout = opener.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(timeout, 3)
        self.assertEqual(request.full_url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer testkey-test")
        self.assertEqual(headers["user-agent"], "CodexEnhance/Test")
        self.assertEqual(body["model"], "qwen3-coder-plus")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("prompt", json.dumps(body).lower())
        self.assertIn("Allowed", result)

    def test_reviewer_uses_user_custom_system_prompt(self):
        providers = [{
            "id": "qwen",
            "short_alias": "qwen",
            "enabled": True,
            "api_format": "openai_chat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "testkey-test",
            "capabilities": {"text": True},
            "models": [{"id": "qwen3-coder-plus", "enabled": True}],
        }]
        opener = FakeOpener({
            "choices": [{
                "message": {
                    "content": "{\"decision\":\"accept\",\"risk_level\":\"low\",\"reason\":\"Allowed.\"}"
                }
            }]
        })

        with patch("auto_approval_runtime.urllib.request.build_opener", return_value=opener):
            AutoApprovalModelReviewer(lambda: providers, lambda: "Custom reviewer rules. Return JSON only.").review(
                self._action(),
                self._profile(),
                {"id": "image-main"},
            )

        request, _timeout = opener.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["messages"][0]["content"], "Custom reviewer rules. Return JSON only.")

    def test_responses_reviewer_uses_source_verified_message_items(self):
        providers = [{
            "id": "openai",
            "short_alias": "openai",
            "enabled": True,
            "api_format": "openai_responses",
            "base_url": "https://api.openai.com/v1",
            "api_key": "testkey-test",
            "capabilities": {"text": True},
            "models": [{"id": "gpt-5.1-codex", "enabled": True}],
        }]
        opener = FakeOpener({
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "{\"decision\":\"accept\",\"risk_level\":\"low\",\"reason\":\"OK\"}",
                }],
            }]
        })

        with patch("auto_approval_runtime.urllib.request.build_opener", return_value=opener):
            result = AutoApprovalModelReviewer(lambda: providers).review(
                self._action(),
                self._profile("gpt-5.1-codex"),
                providers[0],
            )

        request, _timeout = opener.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(body["model"], "gpt-5.1-codex")
        self.assertEqual(body["input"][0]["type"], "message")
        self.assertEqual(body["input"][0]["content"][0]["type"], "input_text")
        self.assertEqual(body["store"], False)
        self.assertIn("decision", result)

    def test_reviewer_can_use_system_proxy_from_provider_profile(self):
        providers = [{
            "id": "qwen",
            "short_alias": "qwen",
            "enabled": True,
            "api_format": "openai_chat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "capabilities": {"text": True},
            "proxy_profile": {"bypass_system_proxy": False},
            "models": [{"id": "qwen3-coder-plus", "enabled": True}],
        }]
        opener = FakeOpener({
            "choices": [{
                "message": {
                    "content": "{\"decision\":\"accept\",\"risk_level\":\"low\",\"reason\":\"Allowed.\"}"
                }
            }]
        })

        with patch("auto_approval_runtime.urllib.request.build_opener", return_value=opener) as build_opener:
            AutoApprovalModelReviewer(lambda: providers).review(
                self._action(),
                self._profile(),
                {"id": "image-main"},
            )

        build_opener.assert_called_once_with()

    def test_anthropic_reviewer_uses_messages_api_headers(self):
        providers = [{
            "id": "anthropic",
            "short_alias": "claude",
            "enabled": True,
            "api_format": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "testkey-ant",
            "capabilities": {"text": True},
            "models": [{"id": "claude-sonnet-4-5", "enabled": True}],
        }]
        opener = FakeOpener({
            "content": [{
                "type": "text",
                "text": "{\"decision\":\"decline\",\"risk_level\":\"medium\",\"reason\":\"Needs review.\"}",
            }]
        })

        with patch("auto_approval_runtime.urllib.request.build_opener", return_value=opener):
            result = AutoApprovalModelReviewer(lambda: providers).review(
                self._action(),
                self._profile("claude-sonnet-4-5"),
                providers[0],
            )

        request, _timeout = opener.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "testkey-ant")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(body["system"].startswith("You are the Auto Approval Broker"), True)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertIn("decline", result)

    def test_media_only_provider_without_reviewer_model_is_not_guessed(self):
        reviewer = AutoApprovalModelReviewer(lambda: [])
        with self.assertRaises(AutoApprovalRuntimeError):
            reviewer.review(
                self._action(),
                self._profile(""),
                {
                    "id": "image-main",
                    "enabled": True,
                    "api_format": "openai_images",
                    "base_url": "https://image.example.test/v1",
                    "capabilities": {"images": True, "text": False},
                    "models": [{"id": "gpt-image-1", "enabled": True}],
                },
            )


if __name__ == "__main__":
    unittest.main()
