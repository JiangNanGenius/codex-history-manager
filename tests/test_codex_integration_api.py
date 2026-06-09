import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_approval_bridge import COMMAND_APPROVAL_METHOD
from codex_config import CodexConfigManager, load_config_toml, save_auth_json, save_config_toml
from local_proxy_auth import (
    REDACTED_LOCAL_PROXY_TOKEN,
    generate_local_proxy_bearer_token,
    local_proxy_token_fingerprint,
)


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
            patch("app.DesktopShortcutManager") as MockDesktopShortcutManager,
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
            self.proxy_server = MockLocalProxyServer.return_value
            MockDesktopShortcutManager.return_value.create_shortcuts.return_value = {
                "success": True,
                "shortcuts": [],
            }
            self.shortcut_manager = MockDesktopShortcutManager.return_value

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

    def test_sync_status_implies_openai_for_official_login_without_provider(self):
        app = self._app()
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {
            "auth_mode": "chatgpt",
            "tokens": {"access_token": "eyJhbGciOiJ.demo"},
        }
        with (
            patch("app.resolve_codex_home", return_value=Path("C:/Users/demo/.codex")),
            patch("app._load_config_toml", return_value={"model": "gpt-5.5"}),
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app.is_codex_running", return_value=(False, [])),
        ):
            response = app.test_client().get("/api/sync/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["current_provider"], "openai")
        self.assertEqual(data["current_provider_source"], "official_oauth")
        self.assertEqual(data["current_model"], "gpt-5.5")
        self.assertEqual(data["current_model_source"], "config")
        self.assertEqual(data["auth_mode"], "official_oauth")
        self.assertTrue(data["official_oauth_implied_provider"])

    def test_settings_redacts_local_proxy_bearer_token(self):
        app = self._app()
        token = generate_local_proxy_bearer_token()
        self.last_config.get_all.return_value = {"local_proxy_bearer_token": token}
        fake_mgr = MagicMock()
        fake_mgr.read_config.return_value = {"features": {"goals": True}}
        fake_mgr.config_path = Path("C:/Users/demo/.codex/config.toml")

        with patch("app.CodexConfigManager", return_value=fake_mgr):
            response = app.test_client().get("/api/settings")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        data = response.get_json()
        self.assertNotIn(token, body)
        self.assertEqual(data["local_proxy_bearer_token"], REDACTED_LOCAL_PROXY_TOKEN)
        self.assertTrue(data["local_proxy_bearer_token_configured"])
        self.assertTrue(data["local_proxy_bearer_token_strong"])
        self.assertEqual(data["local_proxy_bearer_token_fingerprint"], local_proxy_token_fingerprint(token))

    def test_save_settings_preserves_redacted_local_proxy_bearer_token(self):
        app = self._app()
        token = generate_local_proxy_bearer_token()
        self.last_config.get_all.return_value = {"local_proxy_bearer_token": token}

        response = app.test_client().post("/api/settings", json={
            "local_proxy_bearer_token": REDACTED_LOCAL_PROXY_TOKEN,
            "dark_mode": False,
        })

        self.assertEqual(response.status_code, 200)
        saved = self.last_config.update.call_args.args[0]
        self.assertEqual(saved["local_proxy_bearer_token"], token)
        self.assertFalse(saved["dark_mode"])

    def test_save_settings_defers_codex_goals_write_until_start(self):
        app = self._app()
        fake_mgr = MagicMock()

        with patch("app.CodexConfigManager", return_value=fake_mgr):
            response = app.test_client().post("/api/settings", json={
                "codex_goals_enabled": False,
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["codex_goals_sync"]["skipped"])
        self.assertEqual(data["codex_goals_sync"]["reason"], "deferred_until_codex_start")
        fake_mgr.write_goals_feature.assert_not_called()

    def test_codex_integration_apply_is_preview_only_until_start(self):
        app = self._app()
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {}
        fake_mgr.preview_write_provider.return_value = {
            "will_write_config": True,
            "will_write_auth": False,
            "config_diff": {"added": {"model_provider": "codex_enhance_manager"}},
            "auth_diff": {},
            "warnings": [],
        }

        with patch("app.CodexConfigManager", return_value=fake_mgr):
            response = app.test_client().post("/api/codex-integration/apply", json={
                "manual_codex_mutation": True,
                "confirmation": "MODIFY_CODEX_FILES",
                "proxy_base_url": "http://127.0.0.1:51236/v1",
                "proxy_model": "auto",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["applied"])
        self.assertTrue(data["deferred_until_codex_start"])
        fake_mgr.preview_write_provider.assert_called_once()
        fake_mgr.write_provider_config.assert_not_called()

    def test_proxy_start_does_not_sync_codex_config(self):
        app = self._app()
        self.proxy_server.start.return_value = True
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51236,
            "base_url": "http://127.0.0.1:51236/v1",
        }
        fake_mgr = MagicMock()

        with patch("app.CodexConfigManager", return_value=fake_mgr):
            response = app.test_client().post("/api/proxy/start", json={})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["provider_config"]["skipped"])
        self.assertEqual(data["provider_config"]["reason"], "deferred_until_codex_start")
        fake_mgr.write_provider_config.assert_not_called()

    def test_desktop_shortcut_endpoint_creates_selected_kind_only(self):
        app = self._app()

        response = app.test_client().post("/api/desktop-shortcuts/create", json={
            "kind": "start_codex",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.shortcut_manager.create_shortcuts.assert_called_once_with(
            normal=False,
            start_codex=True,
        )

    def test_start_codex_official_mode_skips_provider_sync_and_cpp(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": True}) as disable_proxy,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
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
        self.assertTrue(start.call_args.kwargs["enable_cdp_injection"])
        self.last_config.update.assert_any_call({
            "use_codex_plus_plus": False,
            "plugin_unlock_enabled": False,
        })

    def test_start_codex_official_mode_allows_safe_injection(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": True}),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
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
        self.assertTrue(data["codex_injection_active"])
        self.assertEqual(data["codex_cdp_port"], 51240)
        sync_with_backup.assert_not_called()
        self.assertTrue(start.call_args.kwargs["enable_cdp_injection"])
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
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
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

    def test_start_codex_restarts_running_codex_before_injection(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)),
            patch("app.is_codex_running", return_value=(True, [123, 456])) as running,
            patch("app.kill_codex", return_value=(True, "closed")) as kill,
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={
                "start_mode": "preserve_login_proxy",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["restart"]["skipped"])
        self.assertEqual(data["restart"]["pids"], [123, 456])
        running.assert_called_once()
        kill.assert_called_once_with(timeout=8)
        start.assert_called_once()

    def test_start_codex_backs_off_occupied_cdp_port(self):
        app = self._app()
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", side_effect=lambda port, host="127.0.0.1": int(port) != 61234),
            patch("app.start_codex", return_value=(True, "started")) as start,
        ):
            response = app.test_client().post("/api/codex/start", json={
                "start_mode": "preserve_login_proxy",
                "cdp_port": 61234,
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["codex_cdp_port"], 61235)
        self.assertEqual(start.call_args.kwargs["cdp_port"], 61235)
        self.last_config.update.assert_any_call({
            "codex_injection_enabled": True,
            "codex_cdp_port": 61235,
        })

    def test_start_codex_runs_optional_sandbox_repair_when_enabled(self):
        app = self._app()
        self.last_config.get.side_effect = lambda key, default=None: (
            True if key == "codex_sandbox_auto_repair_enabled" else default
        )
        with (
            patch("app.repair_codex_sandbox_permissions", return_value={
                "success": True,
                "changed": True,
                "restart_required": True,
            }) as repair,
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={
                "start_mode": "preserve_login_proxy",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["sandbox_repair"]["changed"])
        repair.assert_called_once()

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
        self.assertIn("hide_official_usage_alert", data)
        self.assertIn("plugin_marketplace_unlock", data)
        self.assertIn("force_plugin_install", data)
        self.assertIn("official_usage_visible", data)

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
                    "codex_enhance_manager": {"base_url": "http://127.0.0.1:51235/v1"},
                    "openai": {"name": "OpenAI"},
                },
                "model_providers": {
                    "codex_enhance_manager": {"base_url": "http://127.0.0.1:51235/v1"},
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

    def test_reset_for_official_login_requires_risk_confirmation(self):
        app = self._app()

        response = app.test_client().post("/api/codex-integration/reset-for-official-login", json={
            "manual_codex_mutation": True,
            "confirmation": "MODIFY_CODEX_FILES",
        })

        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertTrue(data["chat_history_risk"])
        self.assertEqual(data["required_risk_confirmation"], "CHAT_HISTORY_MAY_BE_LOST")

    def test_reset_for_official_login_backs_up_and_removes_config_and_auth(self):
        app = self._app()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=tmpdir)
            mgr.backup_dir = Path(tmpdir) / "backups"
            save_config_toml(str(mgr.config_path), {"model": "gpt-5.5"})
            save_auth_json(str(mgr.auth_path), {"auth_mode": "chatgpt", "tokens": {"access_token": "secret"}})

            with (
                patch("app.CodexConfigManager", return_value=mgr),
                patch("app.kill_codex", return_value=(True, "killed")),
            ):
                response = app.test_client().post("/api/codex-integration/reset-for-official-login", json={
                    "manual_codex_mutation": True,
                    "confirmation": "MODIFY_CODEX_FILES",
                    "risk_confirmation": "CHAT_HISTORY_MAY_BE_LOST",
                })

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["success"])
            self.assertTrue(data["chat_history_risk"])
            self.assertFalse(mgr.config_path.exists())
            self.assertFalse(mgr.auth_path.exists())
            self.assertIn("config_toml", data["backups"])
            self.assertIn("auth_json", data["backups"])

    def test_permissions_repair_requires_codex_mutation_confirmation(self):
        app = self._app()

        response = app.test_client().post("/api/codex-integration/permissions-repair", json={})

        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertEqual(data["required_confirmation"], "MODIFY_CODEX_FILES")

    def test_permissions_repair_normalizes_full_access_without_touching_auth(self):
        app = self._app()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=tmpdir)
            mgr.backup_dir = Path(tmpdir) / "backups"
            save_config_toml(str(mgr.config_path), {
                "model": "gpt-5.5",
                "model_provider": "openai",
                "approval_policy": "on-request",
                "sandbox_mode": "workspace-write",
                "default_permissions": "dev",
                "sandbox_workspace_write": {"network_access": True, "writable_roots": ["C:/old"]},
                "permissions": {"dev": {"extends": ":workspace"}},
                "windows": {"sandbox": "unelevated"},
            })
            save_auth_json(str(mgr.auth_path), {"auth_mode": "chatgpt", "tokens": {"access_token": "secret"}})

            with patch("app.CodexConfigManager", return_value=mgr):
                response = app.test_client().post("/api/codex-integration/permissions-repair", json={
                    "manual_codex_mutation": True,
                    "confirmation": "MODIFY_CODEX_FILES",
                })

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["success"])
            repaired = load_config_toml(str(mgr.config_path))
            self.assertEqual(repaired["model"], "gpt-5.5")
            self.assertEqual(repaired["model_provider"], "openai")
            self.assertEqual(repaired["approval_policy"], "never")
            self.assertEqual(repaired["sandbox_mode"], "danger-full-access")
            self.assertEqual(repaired["default_permissions"], ":danger-full-access")
            self.assertNotIn("sandbox_workspace_write", repaired)
            self.assertEqual(repaired["permissions"]["dev"]["extends"], ":workspace")
            if os.name == "nt":
                self.assertEqual(repaired["windows"]["sandbox"], "unelevated")
            self.assertTrue(mgr.auth_path.exists())
            self.assertTrue(data["backup_path"])

    def test_repair_config_template_removes_startup_risks_and_preserves_auth(self):
        app = self._app()
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=tmpdir)
            mgr.backup_dir = Path(tmpdir) / "backups"
            save_config_toml(str(mgr.config_path), {
                "model": "gpt-5.5",
                "model_provider": "bad-provider",
                "mcp_servers": {"bad": {"command": "/usr/bin/node", "required": True}},
                "hooks": {"SessionStart": []},
                "features": {"hooks": False},
            })
            save_auth_json(str(mgr.auth_path), {"auth_mode": "chatgpt", "tokens": {"access_token": "secret"}})

            with patch("app.CodexConfigManager", return_value=mgr):
                response = app.test_client().post("/api/codex-integration/repair-config-template", json={
                    "manual_codex_mutation": True,
                    "confirmation": "MODIFY_CODEX_FILES",
                })

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["success"])
            repaired = load_config_toml(str(mgr.config_path))
            self.assertEqual(repaired["model"], "gpt-5.5")
            self.assertTrue(repaired["features"]["goals"])
            self.assertNotIn("model_provider", repaired)
            self.assertNotIn("mcp_servers", repaired)
            self.assertNotIn("hooks", repaired)
            self.assertTrue(mgr.auth_path.exists())
            self.assertIn("mcp_servers", data["removed_keys"])


if __name__ == "__main__":
    unittest.main()
