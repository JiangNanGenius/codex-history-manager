import unittest
import hashlib
import json
import os
import tempfile
import time
from types import SimpleNamespace
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
            patch("app.ProviderRegistry") as MockProviderRegistry,
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer") as MockLocalProxyServer,
            patch("app.AMRRegistry") as MockAMRRegistry,
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
            self.db = MockDB.return_value
            self.db.get_provider_distribution.return_value = [{"provider": "openai", "count": 1}]
            MockLocalProxyServer.return_value.status.return_value = {}
            self.proxy_server = MockLocalProxyServer.return_value
            self.provider_registry = MockProviderRegistry.return_value
            self.provider_registry.list_providers.return_value = {
                "success": True,
                "focus_provider_id": "test-provider",
                "providers": [{
                    "id": "test-provider",
                    "short_alias": "test",
                    "display_name": "Test Provider",
                    "enabled": True,
                    "local_proxy_routing": True,
                    "base_url": "https://example.test/v1",
                    "models": [{
                        "id": "test-model",
                        "enabled": True,
                        "context_window": 128000,
                        "capabilities": {"text": True, "vision": False},
                    }],
                }],
            }
            def current_amr_groups():
                payload = self.provider_registry.list_providers.return_value
                providers = payload.get("providers", []) if isinstance(payload, dict) else []
                focus_provider_id = payload.get("focus_provider_id", "") if isinstance(payload, dict) else ""
                provider = next((item for item in providers if item.get("id") == focus_provider_id), None)
                if provider is None and providers:
                    provider = providers[0]
                models = provider.get("models", []) if isinstance(provider, dict) else []
                model = models[0] if models else {"id": "test-model", "context_window": 128000}
                provider_id = provider.get("id", "test-provider") if isinstance(provider, dict) else "test-provider"
                model_id = model.get("id", "test-model") if isinstance(model, dict) else "test-model"
                return {
                    "groups": [{
                        "id": "default",
                        "display_name": "Smart Routing",
                        "candidates": [{
                            "id": f"{provider_id}/{model_id}",
                            "provider_id": provider_id,
                            "model_id": model_id,
                            "enabled": True,
                            "context_window": model.get("context_window", 128000) if isinstance(model, dict) else 128000,
                            "capabilities": {"text": True, "vision": False},
                        }],
                    }],
                }

            MockAMRRegistry.return_value.list_groups.side_effect = current_amr_groups
            self.amr_registry = MockAMRRegistry.return_value
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

    def test_settings_redacts_secret_reveal_password_hash(self):
        app = self._app()
        secret_settings = {
            "secret_reveal_password_hash": "hash-value",
            "secret_reveal_password_salt": "salt-value",
            "secret_reveal_password_iterations": 210000,
        }
        self.last_config.get_all.return_value = dict(secret_settings)
        self.last_config.get.side_effect = lambda key, default=None: secret_settings.get(key, default)
        with patch("app.CodexConfigManager") as MockCodexConfigManager:
            MockCodexConfigManager.return_value.read_config.return_value = {}
            response = app.test_client().get("/api/settings")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["secret_reveal_password_configured"])
        self.assertNotIn("secret_reveal_password_hash", data)
        self.assertNotIn("secret_reveal_password_salt", data)
        self.assertNotIn("secret_reveal_password_iterations", data)
        self.assertNotIn("secret_reveal_password_hash", data["defaults"])

    def test_sync_preview_allows_running_codex_because_it_is_read_only(self):
        app = self._app()
        stats = SimpleNamespace(
            db_threads_seen=2,
            db_threads_updated=1,
            rollout_files_seen=0,
            rollout_files_updated=0,
            index_rows_seen=0,
            index_rows_updated=0,
            malformed_lines=0,
            errors=[],
            changed=True,
        )
        with (
            patch("app.full_sync", return_value=stats) as full_sync,
            patch("app.is_codex_running", return_value=(True, [123])) as running,
        ):
            response = app.test_client().post("/api/sync/preview", json={
                "target_provider": "codex_enhance_manager",
                "target_model": "amr/default",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["changed"])
        running.assert_not_called()
        full_sync.assert_called_once()

    def test_amr_group_create_hydrates_context_from_provider_model(self):
        app = self._app()
        self.amr_registry.create_group.side_effect = lambda payload: payload

        response = app.test_client().post("/api/amr/groups", json={
            "display_name": "Smart Routing",
            "candidates": [{
                "provider_id": "test-provider",
                "model_id": "test-model",
                "priority": 2,
                "enabled": True,
                "context_window": 1,
                "capabilities": {"text": True, "vision": True},
            }],
        })

        self.assertEqual(response.status_code, 200)
        candidate = response.get_json()["group"]["candidates"][0]
        self.assertEqual(candidate["context_window"], 128000)
        self.assertFalse(candidate["capabilities"]["vision"])
        self.amr_registry.create_group.assert_called_once()

    def test_provider_secret_reveal_is_direct_when_secondary_password_unset(self):
        app = self._app()
        self.provider_registry.get_provider.return_value = {"id": "p", "api_key": "local-secret"}

        response = app.test_client().post("/api/providers/p/secret", json={"field": "api_key"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["secret"], "local-secret")
        self.assertFalse(data["password_required"])

    def test_provider_secret_reveal_requires_secondary_password_when_set(self):
        app = self._app()
        from app import _hash_secret_reveal_password

        secret_settings = _hash_secret_reveal_password("second-pass")
        self.last_config.get.side_effect = lambda key, default=None: secret_settings.get(key, default)
        self.provider_registry.get_provider.return_value = {"id": "p", "api_key": "local-secret"}

        denied = app.test_client().post("/api/providers/p/secret", json={"field": "api_key", "password": "wrong"})
        allowed = app.test_client().post("/api/providers/p/secret", json={"field": "api_key", "password": "second-pass"})

        self.assertEqual(denied.status_code, 403)
        self.assertTrue(denied.get_json()["password_required"])
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()["secret"], "local-secret")

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

    def test_proxy_status_auto_starts_when_focused_provider_needs_proxy(self):
        app = self._app()
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [{
                "id": "volcengine-plan",
                "enabled": True,
                "local_proxy_routing": True,
                "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
                "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
            }],
        }
        self.proxy_server.start.return_value = True
        self.proxy_server.status.side_effect = [
            {"running": False, "port": 51235},
            {"running": False, "port": 51235},
            {"running": True, "port": 51236, "base_url": "http://127.0.0.1:51236/v1"},
        ]
        self.proxy_server.start.reset_mock()

        response = app.test_client().get("/api/proxy/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["running"])
        self.assertTrue(data["auto_start"]["success"])
        self.assertTrue(data["auto_start"]["started"])
        self.proxy_server.start.assert_called_once()
        self.last_config.set.assert_any_call("proxy_port", 51236)

    def test_proxy_status_does_not_auto_start_when_official_focused(self):
        app = self._app()
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "codex_official",
            "providers": [{
                "id": "codex_official",
                "switch_only": True,
                "codex_login": True,
                "local_proxy_routing": False,
            }],
        }
        self.proxy_server.start.reset_mock()
        self.proxy_server.status.return_value = {"running": False, "port": 51235}

        response = app.test_client().get("/api/proxy/status")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["running"])
        self.assertNotIn("auto_start", data)
        self.proxy_server.start.assert_not_called()

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
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {"auth_mode": "chatgpt", "tokens": {"access_token": "official"}}
        fake_mgr.read_config.return_value = {"model": "gpt-5.5"}
        with (
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)) as sync_with_backup,
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
        self.assertEqual(data["sync"]["target_provider"], "openai")
        self.assertEqual(data["sync"]["target_model"], "gpt-5.5")
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_same_provider_family")
        sync_with_backup.assert_not_called()
        disable_proxy.assert_called_once()
        start.assert_called_once()
        self.assertFalse(start.call_args.kwargs["use_codex_plus_plus"])
        self.assertTrue(start.call_args.kwargs["enable_cdp_injection"])
        self.last_config.update.assert_any_call({
            "use_codex_plus_plus": False,
            "plugin_unlock_enabled": False,
        })

    def test_start_codex_current_focus_official_uses_official_direct(self):
        app = self._app()
        official = {
            "id": "codex_official",
            "display_name": "OpenAI Official Login",
            "switch_only": True,
            "codex_login": True,
            "local_proxy_routing": False,
            "routing_mode": "official_direct",
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "codex_official",
            "providers": [official],
        }
        self.proxy_server.start.reset_mock()
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {"access_token": "official"}
        fake_mgr.read_config.return_value = {"model": "gpt-5.5"}
        with (
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)) as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": False}) as disable_proxy,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["start_mode"], "official_direct")
        self.assertTrue(data["official_mode"])
        self.assertEqual(data["sync"]["target_provider"], "openai")
        self.assertEqual(data["sync"]["target_model"], "gpt-5.5")
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_same_provider_family")
        sync_with_backup.assert_not_called()
        disable_proxy.assert_called_once()
        self.proxy_server.start.assert_not_called()

    def test_start_codex_current_focus_official_skips_history_when_signature_current(self):
        app = self._app()
        official = {
            "id": "codex_official",
            "display_name": "OpenAI Official Login",
            "switch_only": True,
            "codex_login": True,
            "local_proxy_routing": False,
            "routing_mode": "official_direct",
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "codex_official",
            "providers": [official],
        }
        codex_home = str(Path(os.path.expandvars(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))).expanduser())
        payload = {
            "version": 1,
            "start_mode": "official_direct",
            "target_provider": "openai",
            "target_model": "gpt-5.5",
            "codex_home": codex_home,
            "paths": {"db_path": "", "sessions_dir": "", "archived_dir": ""},
        }
        signature = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.last_config.get.side_effect = lambda key, default=None: signature if key == "history_sync_signature" else default
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {"access_token": "official"}
        fake_mgr.read_config.return_value = {"model": "gpt-5.5"}
        with (
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": False}),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["start_mode"], "official_direct")
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["target_provider"], "openai")
        self.assertEqual(data["sync"]["target_model"], "gpt-5.5")
        sync_with_backup.assert_not_called()

    def test_start_codex_official_mode_migrates_third_party_history_to_official(self):
        app = self._app()
        self.db.get_provider_distribution.return_value = [{"provider": "codex_enhance_manager", "count": 4}]
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {"auth_mode": "chatgpt", "tokens": {"access_token": "official"}}
        fake_mgr.read_config.return_value = {"model": "gpt-5.5"}
        with (
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": True}, 200)) as sync_with_backup,
            patch("app.disable_codex_enhance_provider_config", return_value={"success": True, "changed": True}),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"official_mode": True})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["official_mode"])
        self.assertEqual(data["sync"]["target_provider"], "openai")
        self.assertEqual(data["sync"]["target_model"], "gpt-5.5")
        sync_with_backup.assert_called_once()

    def test_start_codex_current_focus_third_party_uses_proxy_mode(self):
        app = self._app()
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)) as sync_with_backup,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["start_mode"], "preserve_login_proxy")
        self.assertFalse(data["official_mode"])
        sync_with_backup.assert_called_once()
        self.assertEqual(sync_with_backup.call_args.kwargs["target_provider"], "codex_enhance_manager")
        self.assertEqual(sync_with_backup.call_args.kwargs["target_model"], "amr/default")

    def test_start_codex_current_focus_third_party_skips_history_when_already_third_party(self):
        app = self._app()
        self.db.get_provider_distribution.return_value = [{"provider": "codex_enhance_manager", "count": 6}]
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["start_mode"], "preserve_login_proxy")
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_same_provider_family")
        self.assertEqual(data["sync"]["current_family"], "third_party")
        self.assertEqual(data["sync"]["target_family"], "third_party")
        sync_with_backup.assert_not_called()

    def test_start_codex_skips_third_party_to_third_party_even_if_db_looks_official(self):
        app = self._app()
        self.last_config.get.side_effect = lambda key, default=None: (
            "preserve_login_proxy" if key == "codex_last_start_mode" else default
        )
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_same_provider_family")
        self.assertEqual(data["sync"]["previous_start_mode"], "preserve_login_proxy")
        self.assertEqual(data["sync"]["target_start_mode"], "preserve_login_proxy")
        sync_with_backup.assert_not_called()

    def test_start_codex_runs_official_to_third_party_migration_even_if_db_looks_third_party(self):
        app = self._app()
        self.last_config.get.side_effect = lambda key, default=None: (
            "official_direct" if key == "codex_last_start_mode" else default
        )
        self.db.get_provider_distribution.return_value = [{"provider": "codex_enhance_manager", "count": 6}]
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }
        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": True}, 200)) as sync_with_backup,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        sync_with_backup.assert_called_once()
        self.assertEqual(sync_with_backup.call_args.kwargs["target_provider"], "codex_enhance_manager")

    def test_start_codex_history_sync_reports_heartbeat_while_running(self):
        app = self._app()
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }

        def slow_sync(*args, **kwargs):
            time.sleep(0.45)
            return {"success": True, "changed": False}, 200

        with (
            patch("app._run_sync_with_backup", side_effect=slow_sync),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            client = app.test_client()
            started = client.post("/api/codex/start", json={"start_mode": "current_focus", "async": True})
            self.assertEqual(started.status_code, 200)
            status_url = started.get_json()["status_url"]
            saw_heartbeat = False
            for _ in range(12):
                time.sleep(0.08)
                status = client.get(status_url).get_json()
                if status.get("stage") == "history_sync" and int(status.get("progress") or 0) > 20:
                    saw_heartbeat = True
                    break
            self.assertTrue(saw_heartbeat)
            for _ in range(12):
                status = client.get(status_url).get_json()
                if status.get("status") == "complete":
                    break
                time.sleep(0.08)
            self.assertEqual(status.get("status"), "complete")

    def test_start_codex_reports_launch_heartbeat_while_waiting(self):
        app = self._app()
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "switch_only": False,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }

        def slow_start(*args, **kwargs):
            time.sleep(2.25)
            return True, "started"

        with (
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)),
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", side_effect=slow_start),
        ):
            client = app.test_client()
            started = client.post("/api/codex/start", json={"start_mode": "current_focus", "async": True})
            self.assertEqual(started.status_code, 200)
            status_url = started.get_json()["status_url"]
            saw_launch_heartbeat = False
            for _ in range(35):
                time.sleep(0.1)
                status = client.get(status_url).get_json()
                if status.get("stage") == "launching" and int(status.get("progress") or 0) > 82:
                    saw_launch_heartbeat = True
                    break
            self.assertTrue(saw_launch_heartbeat)
            for _ in range(20):
                status = client.get(status_url).get_json()
                if status.get("status") == "complete":
                    break
                time.sleep(0.1)
            self.assertEqual(status.get("status"), "complete")

    def test_start_codex_skips_history_sync_when_signature_is_current(self):
        app = self._app()
        provider = {
            "id": "volcengine-plan",
            "display_name": "Ark Coding Plan",
            "enabled": True,
            "local_proxy_routing": True,
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "models": [{"id": "ark-code-latest", "enabled": True, "context_window": 256000}],
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "focus_provider_id": "volcengine-plan",
            "providers": [provider],
        }
        self.proxy_server.status.return_value = {
            "running": True,
            "port": 51235,
            "base_url": "http://127.0.0.1:51235/v1",
        }
        codex_home = str(Path(os.path.expandvars(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))).expanduser())
        payload = {
            "version": 1,
            "start_mode": "preserve_login_proxy",
            "target_provider": "codex_enhance_manager",
            "target_model": "amr/default",
            "codex_home": codex_home,
            "paths": {"db_path": "", "sessions_dir": "", "archived_dir": ""},
        }
        signature = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.last_config.get.side_effect = lambda key, default=None: signature if key == "history_sync_signature" else default
        with (
            patch("app._run_sync_with_backup") as sync_with_backup,
            patch("app.is_codex_running", return_value=(False, [])),
            patch("app._tcp_port_is_available", return_value=True),
            patch("app.start_codex", return_value=(True, "started")),
        ):
            response = app.test_client().post("/api/codex/start", json={"start_mode": "current_focus"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_signature_unchanged")
        sync_with_backup.assert_not_called()

    def test_provider_focus_official_persists_and_updates_local_start_mode(self):
        app = self._app()
        official = {
            "id": "codex_official",
            "display_name": "OpenAI Official Login",
            "switch_only": True,
            "codex_login": True,
            "local_proxy_routing": False,
            "routing_mode": "official_direct",
        }
        self.provider_registry.set_focus_provider.return_value = {
            "success": True,
            "focus_provider_id": "codex_official",
            "changed": True,
        }
        self.provider_registry.list_providers.return_value = {
            "success": True,
            "store_path": "C:/demo/providers.json",
            "focus_provider_id": "codex_official",
            "providers": [official],
        }
        with patch("app._official_provider_extra", return_value=[official]):
            response = app.test_client().post("/api/providers/focus", json={
                "provider_id": "codex_official",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["verified_focus_provider_id"], "codex_official")
        self.assertEqual(data["store_path"], "C:/demo/providers.json")
        self.assertTrue(data["switch_only"])
        self.last_config.set.assert_any_call("codex_last_start_mode", "official_direct")

    def test_start_codex_official_mode_allows_safe_injection(self):
        app = self._app()
        fake_mgr = MagicMock()
        fake_mgr.read_auth.return_value = {"access_token": "official"}
        fake_mgr.read_config.return_value = {"model": "gpt-5.5"}
        with (
            patch("app.CodexConfigManager", return_value=fake_mgr),
            patch("app._run_sync_with_backup", return_value=({"success": True, "changed": False}, 200)) as sync_with_backup,
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
        self.assertTrue(data["sync"]["skipped"])
        self.assertEqual(data["sync"]["reason"], "history_sync_same_provider_family")
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
        self.assertEqual(saved["model_provider"], "openai")
        self.assertEqual(saved["model"], "gpt-5")
        self.assertNotIn("provider", saved)
        self.assertNotIn("defaults", saved)
        self.assertNotIn("providers", saved)
        self.assertNotIn("model_providers", saved)

    def test_disable_codex_enhance_provider_config_removes_proxy_auto_model_for_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("model_provider = \"codex_enhance_manager\"\nmodel = \"auto\"\n", encoding="utf-8")
            mgr = MagicMock()
            mgr.config_path = config_path
            mgr.backup_dir = Path(tmp) / "backups"
            mgr.read_config.return_value = {
                "model_provider": "codex_enhance_manager",
                "model": "auto",
                "provider": "codex_enhance_manager",
                "defaults": {"model_provider": "codex_enhance_manager", "model": "auto"},
                "model_providers": {
                    "codex_enhance_manager": {"base_url": "http://127.0.0.1:51235/v1"},
                },
            }

            with patch("app.backup_file", return_value=str(Path(tmp) / "backup.toml")), \
                    patch("app.save_config_toml") as save:
                from app import disable_codex_enhance_provider_config

                result = disable_codex_enhance_provider_config(mgr)

        self.assertTrue(result["success"])
        self.assertTrue(result["changed"])
        saved = save.call_args.args[1]
        self.assertEqual(saved["model_provider"], "openai")
        self.assertNotIn("model", saved)
        self.assertNotIn("provider", saved)
        self.assertNotIn("defaults", saved)
        self.assertNotIn("model_providers", saved)

    def test_disable_codex_enhance_provider_config_skips_backup_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("model = \"gpt-5\"\n", encoding="utf-8")
            mgr = MagicMock()
            mgr.config_path = config_path
            mgr.backup_dir = Path(tmp) / "backups"
            mgr.read_config.return_value = {
                "model": "gpt-5",
                "features": {"goals": True},
            }

            with patch("app.backup_file") as backup, patch("app.save_config_toml") as save:
                from app import disable_codex_enhance_provider_config

                result = disable_codex_enhance_provider_config(mgr, goals_enabled=True)

        self.assertTrue(result["success"])
        self.assertFalse(result["changed"])
        backup.assert_not_called()
        save.assert_not_called()

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
