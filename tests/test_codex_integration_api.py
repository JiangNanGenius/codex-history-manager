import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_approval_bridge import COMMAND_APPROVAL_METHOD


class CodexIntegrationApiTest(unittest.TestCase):
    def _app(self):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB") as MockDB,
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
            self.last_config = config
            MockDB.return_value.get_provider_distribution.return_value = []

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

    def test_sync_status_uses_available_config_loader(self):
        app = self._app()
        with (
            patch("app.resolve_codex_home", return_value=Path("C:/Users/demo/.codex")),
            patch("app._load_config_toml", return_value={"model_provider": "openai", "model": "gpt-5"}),
            patch("app.is_codex_running", return_value=(False, [])),
        ):
            response = app.test_client().get("/api/sync/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["current_provider"], "openai")
        self.assertEqual(data["current_model"], "gpt-5")

    def test_start_codex_official_mode_skips_provider_sync_and_cpp(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": True}) as disable_proxy,
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={"official_mode": True})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["official_mode"])
        self.assertTrue(data["sync"]["skipped"])
        sync_with_backup.assert_not_called()
        disable_proxy.assert_called_once()
        start.assert_called_once()
        self.assertFalse(start.call_args.kwargs["use_codex_plus_plus"])
        self.last_config.update.assert_any_call({
            "use_codex_plus_plus": False,
            "plugin_unlock_enabled": False,
        })

    def test_disable_codex_enhance_provider_config_removes_only_local_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("model_provider = \"codex_enhance_manager\"\n", encoding="utf-8")
            mgr = MagicMock()
            mgr.config_path = config_path
            mgr.backup_dir = Path(tmp) / "backups"
            mgr.read_config.return_value = {
                "model_provider": "codex_enhance_manager",
                "provider": "codex_enhance_manager",
                "model": "gpt-5",
                "defaults": {"model_provider": "codex_enhance_manager", "model": "gpt-5"},
                "providers": {
                    "codex_enhance_manager": {"base_url": "http://127.0.0.1:8080/v1"},
                    "openai": {"name": "OpenAI"},
                },
            }

            with patch("app.backup_file", return_value=str(Path(tmp) / "backup.toml")) as backup, \
                    patch("app.save_config_toml") as save:
                from app import disable_codex_enhance_provider_config

                result = disable_codex_enhance_provider_config(mgr)

        self.assertTrue(result["success"])
        self.assertTrue(result["changed"])
        backup.assert_called_once()
        save.assert_called_once()
        saved = save.call_args.args[1]
        self.assertEqual(saved["model_provider"], "")
        self.assertEqual(saved["provider"], "")
        self.assertEqual(saved["model"], "gpt-5")
        self.assertEqual(saved["defaults"]["model_provider"], "")
        self.assertNotIn("codex_enhance_manager", saved["providers"])
        self.assertIn("openai", saved["providers"])


if __name__ == "__main__":
    unittest.main()
