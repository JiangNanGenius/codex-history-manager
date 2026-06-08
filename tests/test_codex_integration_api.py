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
            patch("app.LocalProxyServer") as MockLocalProxyServer,
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
            MockLocalProxyServer.return_value.status.return_value = {}

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
        self.assertEqual(data["start_mode"], "official_direct")
        self.assertTrue(data["sync"]["skipped"])
        sync_with_backup.assert_not_called()
        disable_proxy.assert_called_once()
        start.assert_called_once()
        self.assertFalse(start.call_args.kwargs["use_codex_plus_plus"])
        self.assertFalse(start.call_args.kwargs["enable_cdp_injection"])
        self.last_config.update.assert_any_call({
            "use_codex_plus_plus": False,
            "plugin_unlock_enabled": False,
        })

    def test_start_codex_official_mode_ignores_requested_injection(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": True}),
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={
                "start_mode": "official_direct",
                "enable_cdp_injection": True,
                "cdp_port": 51240,
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["codex_injection_enabled"])
        self.assertFalse(data["codex_injection_active"])
        self.assertEqual(data["codex_cdp_port"], 51240)
        sync_with_backup.assert_not_called()
        self.assertFalse(start.call_args.kwargs["enable_cdp_injection"])
        self.assertEqual(start.call_args.kwargs["cdp_port"], 51240)
        self.last_config.update.assert_any_call({
            "codex_injection_enabled": True,
            "codex_cdp_port": 51240,
        })

    def test_start_codex_preserve_login_proxy_keeps_provider_sync(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)) as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config") as disable_proxy,
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "preserve_login_proxy"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["official_mode"])
        self.assertEqual(data["start_mode"], "preserve_login_proxy")
        self.assertTrue(data["preserve_official_auth"])
        sync_with_backup.assert_called_once()
        disable_proxy.assert_not_called()
        start.assert_called_once()
        self.assertTrue(start.call_args.kwargs["enable_cdp_injection"])
        self.assertEqual(start.call_args.kwargs["cdp_port"], 51236)

    def test_start_codex_persists_requested_injection_settings(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)),
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={
                "start_mode": "preserve_login_proxy",
                "enable_cdp_injection": False,
                "cdp_port": 61234,
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["codex_injection_enabled"])
        self.assertFalse(data["codex_injection_active"])
        self.assertEqual(data["codex_cdp_port"], 61234)
        self.assertFalse(start.call_args.kwargs["enable_cdp_injection"])
        self.assertEqual(start.call_args.kwargs["cdp_port"], 61234)
        self.last_config.update.assert_any_call({
            "codex_injection_enabled": False,
            "codex_cdp_port": 61234,
        })

    def test_codex_integration_status_defaults_to_preserve_login_when_official_oauth_detected(self):
        app = self._app()
        fake_mgr = MagicMock()
        fake_mgr.read_config.return_value = {}
        fake_mgr.read_auth.return_value = {"access_token": "eyJhbGciOiJ...", "expires_at": 123}
        fake_mgr.inspect_permissions.return_value = {}
        fake_mgr.get_auth_mode.return_value = "official_oauth"
        fake_mgr.codex_home = Path("C:/Users/demo/.codex")
        fake_mgr.config_path = Path("C:/Users/demo/.codex/config.toml")
        fake_mgr.auth_path = Path("C:/Users/demo/.codex/auth.json")
        with patch("app.CodexConfigManager", return_value=fake_mgr):
            response = app.test_client().get("/api/codex-integration/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["official_oauth_detected"])
        self.assertTrue(data["default_preserve_official_auth"])
        self.assertEqual(data["default_start_mode"], "preserve_login_proxy")
        self.assertIn("preserve_login_proxy", data["available_start_modes"])

    def test_codex_injection_apply_respects_disabled_setting(self):
        app = self._app()
        with patch("app.inject_codex_enhancements") as inject:
            response = app.test_client().post("/api/codex-injection/apply", json={
                "enable_cdp_injection": False,
                "cdp_port": 61234,
            })

        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertFalse(data["enabled"])
        self.assertEqual(data["cdp_port"], 61234)
        inject.assert_not_called()
        self.last_config.update.assert_any_call({
            "codex_injection_enabled": False,
            "codex_cdp_port": 61234,
        })

    def test_codex_injection_status_uses_current_request_host(self):
        app = self._app()

        response = app.test_client().get(
            "/api/codex-injection/status",
            base_url="http://127.0.0.1:59999",
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["backend_url"], "http://127.0.0.1:59999")

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
                "model_providers": {
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
        self.assertNotIn("codex_enhance_manager", saved["model_providers"])
        self.assertIn("openai", saved["model_providers"])


if __name__ == "__main__":
    unittest.main()
