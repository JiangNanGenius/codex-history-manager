import unittest

from codex_approval_bridge import (
    COMMAND_APPROVAL_METHOD,
    FILE_CHANGE_APPROVAL_METHOD,
    MCP_ELICITATION_METHOD,
    PERMISSIONS_APPROVAL_METHOD,
    build_codex_approval_result,
    build_codex_approval_bridge_preview,
    build_codex_jsonrpc_response,
    codex_request_to_broker_action,
    is_codex_approval_request,
)


class CodexApprovalBridgeTest(unittest.TestCase):
    def test_command_approval_accepts_with_source_verified_result_shape(self):
        request = {
            "jsonrpc": "2.0",
            "id": 41,
            "method": COMMAND_APPROVAL_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "call_1",
                "approvalId": "approval_1",
                "command": "python -m pytest -q",
                "cwd": "C:/repo",
                "reason": "Run tests",
            },
        }

        action = codex_request_to_broker_action(request)
        response = build_codex_jsonrpc_response(
            request,
            {"decision": "accept", "risk_level": "low", "reason": "Local test command."},
        )

        self.assertTrue(is_codex_approval_request(request))
        self.assertEqual(action["kind"], "command")
        self.assertEqual(action["codex_app_server"]["method"], COMMAND_APPROVAL_METHOD)
        self.assertEqual(response["id"], 41)
        self.assertEqual(response["result"], {"decision": "accept"})

    def test_command_approval_can_apply_verified_network_policy_amendment(self):
        request = {
            "id": 42,
            "method": COMMAND_APPROVAL_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "call_net",
                "networkApprovalContext": {"host": "example.com", "protocol": "https"},
                "proposedNetworkPolicyAmendments": [
                    {"host": "example.com", "action": "allow"},
                ],
                "availableDecisions": [
                    "accept",
                    {"applyNetworkPolicyAmendment": {"network_policy_amendment": {"host": "example.com", "action": "allow"}}},
                    "decline",
                ],
            },
        }

        response = build_codex_approval_result(
            request,
            {"decision": "accept", "risk_level": "low", "reason": "Allow listed host."},
        )

        self.assertEqual(response["decision"]["applyNetworkPolicyAmendment"]["network_policy_amendment"], {
            "host": "example.com",
            "action": "allow",
        })

    def test_file_change_accept_for_session_when_available(self):
        request = {
            "id": 43,
            "method": FILE_CHANGE_APPROVAL_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "patch_1",
                "reason": "Apply generated patch",
                "availableDecisions": ["accept", "acceptForSession", "decline"],
            },
        }

        response = build_codex_approval_result(
            request,
            {"decision": "accept", "risk_level": "low", "reason": "Workspace patch.", "scope": "session"},
        )

        self.assertEqual(response, {"decision": "acceptForSession"})

    def test_decline_maps_to_decline_for_command_and_file_change(self):
        command_request = {"id": 44, "method": COMMAND_APPROVAL_METHOD, "params": {}}
        file_request = {"id": 45, "method": FILE_CHANGE_APPROVAL_METHOD, "params": {}}
        decision = {"decision": "decline", "risk_level": "high", "reason": "Risky."}

        self.assertEqual(build_codex_approval_result(command_request, decision), {"decision": "decline"})
        self.assertEqual(build_codex_approval_result(file_request, decision), {"decision": "decline"})

    def test_permissions_accept_grants_requested_subset_shape(self):
        request = {
            "id": 46,
            "method": PERMISSIONS_APPROVAL_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "itemId": "perm_1",
                "cwd": "C:/repo",
                "permissions": {
                    "fileSystem": {
                        "entries": [
                            {"access": "write", "path": {"type": "path", "path": "C:/repo"}},
                        ],
                    },
                    "network": {"enabled": True},
                },
            },
        }

        response = build_codex_approval_result(
            request,
            {"decision": "accept", "risk_level": "medium", "reason": "Temporary workspace permission.", "scope": "turn"},
        )

        self.assertEqual(response["scope"], "turn")
        self.assertEqual(response["permissions"]["network"], {"enabled": True})
        self.assertIn("fileSystem", response["permissions"])
        self.assertNotIn("strictAutoReview", response)

    def test_permissions_decline_grants_empty_permissions(self):
        request = {
            "id": 47,
            "method": PERMISSIONS_APPROVAL_METHOD,
            "params": {
                "permissions": {"network": {"enabled": True}},
            },
        }

        response = build_codex_approval_result(
            request,
            {"decision": "decline", "risk_level": "high", "reason": "Network not needed."},
        )

        self.assertEqual(response, {"permissions": {}, "scope": "turn"})

    def test_mcp_tool_elicitation_accept_shape(self):
        request = {
            "id": 48,
            "method": MCP_ELICITATION_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "serverName": "demo",
                "request": {
                    "mode": "form",
                    "message": "Allow MCP tool call?",
                    "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": "session"},
                },
            },
        }

        action = codex_request_to_broker_action(request)
        response = build_codex_approval_result(
            request,
            {"decision": "accept", "risk_level": "low", "reason": "Allowed tool."},
        )

        self.assertEqual(action["kind"], "mcp_tool")
        self.assertEqual(response, {"action": "accept", "content": {}})

    def test_ask_user_maps_to_cancel_without_user_interaction(self):
        request = {"id": 49, "method": MCP_ELICITATION_METHOD, "params": {}}

        response = build_codex_jsonrpc_response(
            request,
            {"decision": "ask_user", "risk_level": "unknown", "reason": "Cannot decide."},
        )

        self.assertEqual(response["result"], {"action": "cancel", "content": None})

    def test_bridge_preview_is_side_effect_free_and_metadata_only(self):
        request = {
            "jsonrpc": "2.0",
            "id": 50,
            "method": COMMAND_APPROVAL_METHOD,
            "params": {
                "threadId": "thr_1",
                "turnId": "turn_1",
                "approvalId": "approval_50",
                "command": "python -m pytest",
                "cwd": "C:/repo",
                "reason": "Run tests",
            },
        }

        preview = build_codex_approval_bridge_preview(
            request,
            {"decision": "accept", "risk_level": "low", "reason": "Local command."},
        )

        self.assertTrue(preview["success"])
        self.assertTrue(preview["preview"])
        self.assertFalse(preview["live_transport_connected"])
        self.assertEqual(preview["method"], COMMAND_APPROVAL_METHOD)
        self.assertEqual(preview["broker_action"]["kind"], "command")
        self.assertEqual(preview["jsonrpc_response"]["id"], 50)
        self.assertEqual(preview["jsonrpc_response"]["result"], {"decision": "accept"})


if __name__ == "__main__":
    unittest.main()
