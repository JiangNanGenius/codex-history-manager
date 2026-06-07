import unittest
from unittest.mock import MagicMock, patch

from codex_approval_bridge import COMMAND_APPROVAL_METHOD


class CodexIntegrationApiTest(unittest.TestCase):
    def _app(self):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB"),
            patch("app.BackupManager"),
            patch("app.TokenStats"),
            patch("app.ProviderRegistry"),
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer"),
            patch("app.AMRRegistry"),
            patch("app.QuotaManager"),
            patch("app.StartupManager"),
            patch("app.DiagnosticsCollector"),
        ):
            config = MagicMock()
            config.get.side_effect = lambda key, default=None: default
            config.get_all.return_value = {}
            config.is_write_locked.return_value = False
            config.write_lock_reason.return_value = ""
            MockConfig.return_value = config

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            return flask_app

    def test_approval_bridge_preview_returns_broker_action_and_jsonrpc_response(self):
        app = self._app()
        response = app.test_client().post("/api/codex-integration/approval-bridge-preview", json={
            "message": {
                "jsonrpc": "2.0",
                "id": 71,
                "method": COMMAND_APPROVAL_METHOD,
                "params": {
                    "approvalId": "approval_71",
                    "command": "python -m pytest",
                    "cwd": "C:/repo",
                    "reason": "Run tests",
                },
            },
            "decision": {
                "decision": "accept",
                "risk_level": "low",
                "reason": "Tests are local.",
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["preview"])
        self.assertFalse(data["live_transport_connected"])
        self.assertEqual(data["broker_action"]["kind"], "command")
        self.assertEqual(data["jsonrpc_response"]["result"], {"decision": "accept"})

    def test_approval_bridge_preview_rejects_unsupported_messages(self):
        app = self._app()
        response = app.test_client().post("/api/codex-integration/approval-bridge-preview", json={
            "message": {"id": 1, "method": "unknown/method", "params": {}},
        })

        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Unsupported Codex approval method", data["error"])


if __name__ == "__main__":
    unittest.main()
