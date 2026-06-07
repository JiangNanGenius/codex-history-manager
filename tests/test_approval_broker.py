import json
import unittest

from approval_broker import (
    ApprovalDecisionError,
    build_auto_approval_prompt,
    build_decision_record,
    failure_decision,
    is_auto_approval_enabled,
    normalize_approval_action,
    parse_approval_decision,
)


class ApprovalBrokerTest(unittest.TestCase):
    def test_auto_approval_enabled_uses_provider_profile(self):
        provider = {
            "approval_profile": {
                "mode": "proxy_auto_approve",
            }
        }

        self.assertTrue(is_auto_approval_enabled(provider))
        self.assertFalse(is_auto_approval_enabled({"mode": "manual_only"}))

    def test_normalize_command_redacts_secret_strings_and_secret_fields(self):
        bearer = "Bearer " + "local-secret-token"
        action = normalize_approval_action({
            "kind": "exec",
            "action_id": "cmd-1",
            "command": f"Invoke-WebRequest https://example.test -Headers @{{Authorization='{bearer}'}}",
            "env": {"API_KEY": "local-secret-value"},
            "headers": {"Authorization": bearer},
        })
        encoded = json.dumps(action)

        self.assertEqual(action["kind"], "command")
        self.assertIn("Bearer ********", action["command"])
        self.assertNotIn("local-secret-token", encoded)
        self.assertNotIn("local-secret-value", encoded)
        self.assertIn("contains_redacted_secret", action["risk_hints"])

    def test_build_prompt_is_strict_json_and_not_fallback_labeled(self):
        prompt = build_auto_approval_prompt(
            {"kind": "permissions", "permissions": {"filesystem": "workspace-write"}},
            {
                "mode": "proxy_auto_approve",
                "reviewer_model": "qwen/qwen3-coder-plus",
                "allowed_actions": ["permissions"],
            },
        )
        system_content = prompt["messages"][0]["content"]
        user_payload = json.loads(prompt["messages"][1]["content"])

        self.assertIn("Auto Approval Broker", system_content)
        self.assertNotIn("fallback", system_content.lower())
        self.assertEqual(prompt["response_format"], {"type": "json_object"})
        self.assertTrue(user_payload["user_enabled"])
        self.assertEqual(user_payload["action"]["kind"], "permissions")

    def test_parse_accept_alias_and_decline_high_risk_by_policy(self):
        decision = parse_approval_decision(
            {
                "decision": "approved",
                "risk_level": "high",
                "reason": "Would change execution policy.",
                "confidence": 0.9,
            },
            {
                "mode": "proxy_auto_approve",
                "auto_decline_high_risk": True,
            },
        )

        self.assertEqual(decision["decision"], "decline")
        self.assertEqual(decision["risk_level"], "high")
        self.assertIn("auto_decline_high_risk", decision["policy_overrides"])

    def test_parse_fenced_json(self):
        decision = parse_approval_decision(
            """```json
{"decision":"deny","risk_level":"medium","reason":"Unneeded network access.","confidence":2}
```"""
        )

        self.assertEqual(decision["decision"], "decline")
        self.assertEqual(decision["confidence"], 1.0)

    def test_parse_rejects_non_json_text(self):
        with self.assertRaises(ApprovalDecisionError):
            parse_approval_decision("I think this is fine: approve")

    def test_failure_decision_respects_review_error_policy(self):
        allow = failure_decision("timeout", {"on_review_error": "allow"})
        ask = failure_decision("timeout", {"on_review_error": "ask_user"})
        default = failure_decision("timeout", {})

        self.assertEqual(allow["decision"], "accept")
        self.assertEqual(ask["decision"], "ask_user")
        self.assertEqual(default["decision"], "decline")

    def test_build_decision_record_is_metadata_only(self):
        record = build_decision_record(
            {
                "kind": "image_generation",
                "action_id": "img-1",
                "prompt": "private image prompt",
                "provider_id": "bailian",
                "model": "wanx2.1-image",
            },
            {
                "decision": "accept",
                "risk_level": "low",
                "reason": "User enabled media approval for this provider.",
            },
            {"mode": "proxy_auto_approve", "reviewer_model": "qwen/qwen3-coder-plus"},
            request_id="record-1",
        )
        encoded = json.dumps(record)

        self.assertEqual(record["record_id"], "record-1")
        self.assertEqual(record["action_kind"], "image_generation")
        self.assertEqual(record["decision"], "accept")
        self.assertNotIn("private image prompt", encoded)


if __name__ == "__main__":
    unittest.main()
