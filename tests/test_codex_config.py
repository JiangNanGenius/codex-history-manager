import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_config import (
    CodexConfigManager,
    LOCAL_PROXY_BEARER_TOKEN,
    load_config_toml,
    save_config_toml,
    load_auth_json,
    save_auth_json,
    merge_toml_dict,
    detect_auth_mode,
    backup_file,
    restore_file,
    redact_auth_for_preview,
    build_codex_enhance_provider_config,
    codex_goals_enabled_from_config,
    merge_codex_goals_feature,
    sanitize_codex_config_for_managed_write,
)


class ConfigTOMLTest(unittest.TestCase):
    def test_roundtrip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            original = {
                "model_provider": "openai",
                "model": "gpt-5",
                "unknown_custom_key": "preserve-me",
                "defaults": {
                    "model_provider": "openai",
                    "custom_setting": True,
                },
            }
            save_config_toml(str(path), original)
            loaded = load_config_toml(str(path))
            self.assertEqual(loaded.get("unknown_custom_key"), "preserve-me")
            self.assertEqual(loaded.get("defaults", {}).get("custom_setting"), True)

    def test_merge_toml_does_not_remove_keys(self):
        base = {
            "model_provider": "openai",
            "keep_this": "yes",
            "defaults": {"model": "gpt-5", "extra": 123},
        }
        updates = {"model_provider": "codex_enhance_manager"}
        merged = merge_toml_dict(base, updates)
        self.assertEqual(merged["model_provider"], "codex_enhance_manager")
        self.assertEqual(merged["keep_this"], "yes")
        self.assertEqual(merged["defaults"]["model"], "gpt-5")
        self.assertEqual(merged["defaults"]["extra"], 123)

    def test_roundtrip_nested_model_provider_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            original = {
                "model_provider": "codex_enhance_manager",
                "model_providers": {
                    "codex_enhance_manager": {
                        "name": "Codex Enhance Manager",
                        "base_url": "http://127.0.0.1:51234/v1",
                        "wire_api": "responses",
                        "requires_openai_auth": True,
                        "experimental_bearer_token": LOCAL_PROXY_BEARER_TOKEN,
                    }
                },
            }

            save_config_toml(str(path), original)
            loaded = load_config_toml(str(path))

            provider = loaded["model_providers"]["codex_enhance_manager"]
            self.assertEqual(provider["wire_api"], "responses")
            self.assertTrue(provider["requires_openai_auth"])
            self.assertEqual(provider["experimental_bearer_token"], LOCAL_PROXY_BEARER_TOKEN)


class AuthJsonTest(unittest.TestCase):
    def test_detect_official_oauth(self):
        self.assertEqual(
            detect_auth_mode({"access_token": "eyJhbGciOiJ..."}),
            "official_oauth",
        )

    def test_detect_official_oauth_from_nested_tokens(self):
        self.assertEqual(
            detect_auth_mode({"auth_mode": "chatgpt", "tokens": {"access_token": "eyJhbGciOiJ..."}}),
            "official_oauth",
        )
        self.assertEqual(
            detect_auth_mode({"tokens": {"refresh_token": "refresh-token"}}),
            "official_oauth",
        )

    def test_detect_legacy_api_key(self):
        self.assertEqual(
            detect_auth_mode({"access_token": "s" + "k-proj-abc123"}),
            "legacy_api_key",
        )
        self.assertEqual(
            detect_auth_mode({"api_key": "testkey-test-123"}),
            "legacy_api_key",
        )

    def test_detect_none(self):
        self.assertEqual(detect_auth_mode({}), "none")


class BackupRestoreTest(unittest.TestCase):
    def test_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "config.toml"
            original.write_text("model = 'gpt-5'\n", encoding="utf-8")
            backup_dir = Path(tmpdir) / "backups"
            backup_path = backup_file(str(original), backup_dir)
            self.assertTrue(backup_path)
            self.assertTrue(Path(backup_path).exists())

            original.write_text("model = 'changed'\n", encoding="utf-8")
            ok = restore_file(str(original), backup_path)
            self.assertTrue(ok)
            self.assertEqual(original.read_text(encoding="utf-8"), "model = 'gpt-5'\n")


class CodexConfigManagerTest(unittest.TestCase):
    def test_generated_codex_provider_uses_responses_wire_api_only(self):
        patch = build_codex_enhance_provider_config(
            proxy_base_url="http://127.0.0.1:51235/v1",
            proxy_model="codex-auto",
            goals_enabled=True,
            local_proxy_bearer_token="cem_lp_test_" + ("x" * 48),
            model_catalog_json="C:/demo/model_catalog.json",
        )

        provider = patch["model_providers"]["codex_enhance_manager"]
        self.assertEqual(provider["wire_api"], "responses")
        self.assertNotEqual(provider["wire_api"], "chat")
        self.assertEqual(provider["base_url"], "http://127.0.0.1:51235/v1")
        self.assertEqual(patch["model_catalog_json"], "C:/demo/model_catalog.json")
        self.assertNotIn("provider", patch)
        self.assertNotIn("defaults", patch)

    def test_managed_codex_write_sanitizes_legacy_switcher_fields(self):
        cleaned = sanitize_codex_config_for_managed_write({
            "model_provider": "openai",
            "model": "gpt-5.5",
            "provider": "openai",
            "providers": {"openai": {"name": "OpenAI"}},
            "defaults": {"model_provider": "openai", "model": "auto"},
            "model_providers": {"openai": {"name": "OpenAI"}},
            "features": {"goals": True},
        })

        self.assertEqual(cleaned["model_provider"], "openai")
        self.assertEqual(cleaned["model"], "gpt-5.5")
        self.assertEqual(cleaned["features"]["goals"], True)
        self.assertNotIn("provider", cleaned)
        self.assertNotIn("providers", cleaned)
        self.assertNotIn("defaults", cleaned)
        self.assertNotIn("model_providers", cleaned)

    def test_preview_shows_restart_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            preview = mgr.preview_write_provider()
            self.assertTrue(preview["restart_required"])
            self.assertEqual(preview["auth_mode"], "none")

    def test_inspect_permissions_reads_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            save_config_toml(str(mgr.config_path), {
                "approval_policy": "never",
                "sandbox_mode": "danger-full-access",
            })

            result = mgr.inspect_permissions()

            self.assertTrue(result["effective_full_access"])
            self.assertGreater(result["issue_count"], 0)

    def test_preview_permissions_update_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            save_config_toml(str(mgr.config_path), {
                "approval_policy": "on-request",
                "sandbox_mode": "read-only",
            })

            preview = mgr.preview_permissions_update(
                approval_policy="never",
                sandbox_mode="workspace-write",
                sandbox_workspace_write={"network_access": True},
            )
            current = load_config_toml(str(mgr.config_path))

            self.assertTrue(preview["will_write_config"])
            self.assertEqual(current["sandbox_mode"], "read-only")

    def test_write_provider_config_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            token = "cem_lp_test_" + ("x" * 48)
            catalog = {"models": [{
                "slug": "amr/default",
                "display_name": "Smart Routing",
                "description": "Routes requests",
                "supported_reasoning_levels": [],
                "shell_type": "shell_command",
                "visibility": "list",
                "supported_in_api": True,
                "priority": 0,
                "availability_nux": None,
                "upgrade": None,
                "base_instructions": "base",
                "supports_reasoning_summaries": False,
                "support_verbosity": False,
                "default_verbosity": None,
                "apply_patch_tool_type": "freeform",
                "truncation_policy": {"mode": "tokens", "limit": 10000},
                "supports_parallel_tool_calls": True,
                "context_window": 128000,
                "max_context_window": 128000,
                "experimental_supported_tools": [],
            }]}
            result = mgr.write_provider_config(
                preserve_official_auth=True,
                goals_enabled=True,
                local_proxy_bearer_token=token,
                model_catalog=catalog,
            )
            self.assertTrue(result["success"])
            self.assertTrue(mgr.config_path.exists())
            self.assertTrue(mgr.model_catalog_path.exists())
            config = load_config_toml(str(mgr.config_path))
            self.assertEqual(config.get("model_provider"), "codex_enhance_manager")
            self.assertEqual(config.get("model"), "amr/default")
            self.assertEqual(config.get("model_catalog_json"), str(mgr.model_catalog_path))
            self.assertNotIn("provider", config)
            self.assertNotIn("defaults", config)
            self.assertTrue(codex_goals_enabled_from_config(config))
            self.assertEqual(mgr.read_model_catalog()["models"][0]["slug"], "amr/default")
            provider = config.get("model_providers", {}).get("codex_enhance_manager", {})
            self.assertEqual(provider.get("base_url"), "http://127.0.0.1:51235/v1")
            self.assertEqual(provider.get("wire_api"), "responses")
            self.assertTrue(provider.get("requires_openai_auth"))
            self.assertEqual(provider.get("experimental_bearer_token"), token)
            self.assertNotEqual(provider.get("experimental_bearer_token"), "codex-enhance-manager-local")

    def test_goals_feature_merge_preserves_existing_features(self):
        merged = merge_codex_goals_feature({"features": {"hooks": False}}, True)
        self.assertTrue(merged["features"]["goals"])
        self.assertFalse(merged["features"]["hooks"])

    def test_preserve_official_oauth_does_not_touch_auth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            auth_data = {"access_token": "eyJhbGciOiJ...", "expires_at": 1234567890}
            save_auth_json(str(mgr.auth_path), auth_data)

            result = mgr.write_provider_config(preserve_official_auth=True)
            self.assertTrue(result["success"])
            loaded_auth = load_auth_json(str(mgr.auth_path))
            self.assertEqual(loaded_auth.get("access_token"), "eyJhbGciOiJ...")

    def test_restore_config_from_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            save_config_toml(str(mgr.config_path), {"model": "original"})
            result = mgr.write_provider_config(preserve_official_auth=True)
            self.assertTrue(result["success"])

            restore_result = mgr.restore_config()
            self.assertTrue(restore_result["success"])
            self.assertTrue(restore_result["restart_required"])
            restored = load_config_toml(str(mgr.config_path))
            self.assertEqual(restored.get("model"), "original")

    def test_redact_auth_hides_secrets(self):
        data = {
            "access_token": "secret-token",
            "api_key": "testkey-123",
            "refresh_token": "refresh-me",
            "id_token": "id-me",
            "tokens": {
                "access_token": "nested-access",
                "refresh_token": "nested-refresh",
            },
            "safe_field": "visible",
        }
        redacted = redact_auth_for_preview(data)
        self.assertEqual(redacted["access_token"], "********")
        self.assertEqual(redacted["api_key"], "********")
        self.assertEqual(redacted["refresh_token"], "********")
        self.assertEqual(redacted["id_token"], "********")
        self.assertEqual(redacted["tokens"]["access_token"], "********")
        self.assertEqual(redacted["tokens"]["refresh_token"], "********")
        self.assertEqual(redacted["safe_field"], "visible")


class RollbackTest(unittest.TestCase):
    @patch("codex_config.save_config_toml")
    def test_rollback_on_config_write_failure(self, mock_save):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            mgr.backup_dir = Path(tmpdir) / "backups"
            original = {"model": "original-model", "custom_key": 42}
            save_config_toml(str(mgr.config_path), original)
            original_text = mgr.config_path.read_text(encoding="utf-8")

            mock_save.side_effect = PermissionError("mock disk full")

            result = mgr.write_provider_config(preserve_official_auth=True)
            self.assertFalse(result["success"])
            self.assertTrue(result["errors"])
            self.assertTrue(any("config.toml write failed" in e for e in result["errors"]))
            self.assertIn("config_toml", result["backups"])

            # 验证 rollback 后 config.toml 内容与写入前一致
            restored_text = mgr.config_path.read_text(encoding="utf-8")
            self.assertEqual(restored_text, original_text)

    def test_no_rollback_when_write_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CodexConfigManager(codex_home=str(tmpdir))
            mgr.backup_dir = Path(tmpdir) / "backups"
            save_config_toml(str(mgr.config_path), {"model": "original"})

            result = mgr.write_provider_config(preserve_official_auth=True)
            self.assertTrue(result["success"])
            self.assertEqual(len(result["errors"]), 0)
            self.assertIn("config_toml", result["backups"])

            # 验证确实写入了新配置
            loaded = load_config_toml(str(mgr.config_path))
            self.assertEqual(loaded.get("model_provider"), "codex_enhance_manager")


class TOMLEscapingTest(unittest.TestCase):
    def test_roundtrip_string_with_quotes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            original = {
                "defaults": {
                    "system_prompt": 'You are a "helpful" assistant.',
                    "multiline": "Line one\nLine two",
                    "backslash": "C:\\Users\\test",
                },
            }
            save_config_toml(str(path), original)
            loaded = load_config_toml(str(path))
            self.assertEqual(loaded["defaults"]["system_prompt"], 'You are a "helpful" assistant.')
            self.assertEqual(loaded["defaults"]["multiline"], "Line one\nLine two")
            self.assertEqual(loaded["defaults"]["backslash"], "C:\\Users\\test")


if __name__ == "__main__":
    unittest.main()
