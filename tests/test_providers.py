import json
import tempfile
import unittest
from pathlib import Path

from providers import (
    ProviderRegistry,
    REDACTED_VALUE,
    build_catalog_preview_from_providers,
    normalize_provider,
    is_secret_key,
    validate_provider,
)
from provider_routing import provider_allows_local_routing
from codex_official_provider import build_official_login_provider


class ProviderRegistryTest(unittest.TestCase):
    def test_create_provider_redacts_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            created = registry.create_provider({
                "display_name": "Test Provider",
                "short_alias": "test",
                "base_url": "https://example.test/v1",
                "api_key": "secret-value",
                "native_currency": "USD",
                "models": [{"id": "model-a", "selected": True}],
            })

            self.assertEqual(created["api_key"], REDACTED_VALUE)
            loaded = registry.get_provider(created["id"], include_secrets=True)
            self.assertEqual(loaded["api_key"], "secret-value")

    def test_import_domestic_responses_preset_marks_partial_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.import_preset("alibaba-bailian-text-media")

            profile = provider["responses_profile"]
            self.assertTrue(profile["domestic_responses"])
            self.assertTrue(profile["partial_compatibility"])
            self.assertTrue(profile["requires_adapter"])
            self.assertEqual(profile["profile_id"], "alibaba_bailian")
            self.assertIn("qwen-api-via-openai-responses", profile["verified_docs_url"])
            self.assertIn("input_image", profile["allowed_input_content_types"])
            self.assertIn("response.output_text.delta", profile["verified_event_types"])
            self.assertTrue(provider["media_profile"]["adapter_required"])
            self.assertFalse(provider["media_profile"]["openai_compatible_media"])
            self.assertEqual(provider["media_profile"]["adapter"], "alibaba_bailian")

    def test_volcengine_responses_preset_is_adapter_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.import_preset("volcengine-ark-text-media")

            profile = provider["responses_profile"]
            self.assertTrue(profile["domestic_responses"])
            self.assertTrue(profile["partial_compatibility"])
            self.assertTrue(profile["requires_adapter"])
            self.assertEqual(profile["profile_id"], "volcengine_ark")
            self.assertIn("1585128", profile["verified_docs_url"])
            self.assertIn("payload_until_verified", profile["unsupported_fields"])
            self.assertIn("image_process", profile["allowed_tool_types"])

    def test_volcengine_plan_presets_are_separate_and_include_user_contexts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            coding = registry.import_preset("volcengine-coding-plan")
            agent = registry.import_preset("volcengine-agent-plan")

            self.assertEqual(coding["base_url"], "https://ark.cn-beijing.volces.com/api/coding/v3")
            self.assertEqual(agent["base_url"], "https://ark.cn-beijing.volces.com/api/plan/v3")
            self.assertEqual(coding["codex_visible_alias"], "Ark Coding Plan")
            self.assertEqual(agent["codex_visible_alias"], "Ark Agent Plan")

            coding_models = {model["id"]: model for model in coding["models"]}
            agent_models = {model["id"]: model for model in agent["models"]}
            self.assertEqual(coding_models["ark-code-latest"]["context_window"], 256000)
            self.assertEqual(coding_models["ark-code-latest"]["max_output_tokens"], 32000)
            self.assertEqual(coding_models["deepseek-v4-pro"]["context_window"], 1024000)
            self.assertEqual(coding_models["minimax-m3"]["context_window"], 512000)
            self.assertTrue(coding_models["ark-code-latest"]["capabilities"]["vision"])
            self.assertFalse(coding_models["deepseek-v4-pro"]["capabilities"]["vision"])
            self.assertIn("doubao-seed-code", coding_models)
            self.assertIn("doubao-seed-2.0-mini", agent_models)
            self.assertNotIn("doubao-seed-code", agent_models)

    def test_catalog_preview_includes_selected_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Catalog Provider",
                "short_alias": "cat",
                "catalog_visibility": "selected_models",
                "models": [
                    {"id": "visible", "selected": True, "context_window": 1000},
                    {"id": "hidden", "selected": False, "context_window": 1000},
                ],
            })

            preview = registry.preview_catalog()
            model_ids = [entry["codex_model_id"] for entry in preview["entries"]]
            self.assertEqual(model_ids, ["Catalog Provider/visible"])

    def test_switch_only_official_provider_is_listed_but_not_catalog_routed(self):
        official = normalize_provider({
            "id": "codex_official",
            "display_name": "OpenAI Official Login",
            "short_alias": "codex",
            "enabled": True,
            "switch_only": True,
            "amr_excluded": True,
            "local_proxy_routing": False,
            "models": [{"id": "gpt-5.5", "enabled": True, "selected": True}],
        })

        self.assertFalse(provider_allows_local_routing(official))
        preview = build_catalog_preview_from_providers([official])
        self.assertEqual(preview["entry_count"], 0)

    def test_official_login_provider_is_switch_only_with_full_display_capabilities(self):
        official = build_official_login_provider(
            {"model": "gpt-5.5"},
            {"auth_mode": "chatgpt", "tokens": {"access_token": "eyJhbGciOiJ.demo"}},
        )

        self.assertIsNotNone(official)
        self.assertFalse(provider_allows_local_routing(official))
        self.assertTrue(official["switch_only"])
        self.assertTrue(official["amr_excluded"])
        self.assertFalse(official["local_proxy_routing"])
        for capability in ("text", "vision", "tools", "reasoning", "streaming", "compact", "models"):
            self.assertTrue(official["capabilities"][capability])
            self.assertTrue(official["models"][0]["capabilities"][capability])

    def test_registry_extra_official_provider_can_be_focused_without_catalog_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            official = normalize_provider({
                "id": "codex_official",
                "display_name": "OpenAI Official Login",
                "short_alias": "codex",
                "enabled": True,
                "switch_only": True,
                "amr_excluded": True,
                "local_proxy_routing": False,
                "models": [{"id": "gpt-5.5", "enabled": True, "selected": True}],
            })

            listed = registry.list_providers(extra_providers=[official])
            self.assertIn("codex_official", [p["id"] for p in listed["providers"]])
            focused = registry.set_focus_provider("codex_official", extra_providers=[official])
            self.assertEqual(focused["focus_provider_id"], "codex_official")
            preview = registry.preview_catalog(extra_providers=[official], focus_provider_id="codex_official")
            self.assertEqual(preview["entry_count"], 0)

    def test_update_provider_preserves_model_metadata_from_text_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Metadata Provider",
                "short_alias": "meta",
                "models": [
                    {
                        "id": "image-model",
                        "display_name": "Image Model",
                        "selected": False,
                        "context_window": 64000,
                        "capabilities": {"text": False, "images": True},
                        "pricing": {"input_per_million": 1.25},
                        "tags": ["media"],
                    },
                ],
            })

            updated = registry.update_provider(provider["id"], {
                "models": [
                    {
                        "id": "image-model",
                        "display_name": "Image Model Renamed",
                        "selected": True,
                        "context_window": 128000,
                        "enabled": True,
                    },
                ],
            })

            model = updated["models"][0]
            self.assertEqual(model["display_name"], "Image Model Renamed")
            self.assertTrue(model["selected"])
            self.assertEqual(model["context_window"], 128000)
            self.assertTrue(model["capabilities"]["images"])
            self.assertEqual(model["pricing"]["input_per_million"], 1.25)
            self.assertEqual(model["tags"], ["media"])

    def test_model_context_window_change_marks_codex_restart_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Context Provider",
                "short_alias": "ctx",
                "api_key": "saved-secret",
                "models": [{"id": "model-a", "selected": True, "context_window": 128000}],
            })

            updated = registry.update_provider(provider["id"], {
                "models": [{"id": "model-a", "selected": True, "context_window": 64000}],
            })

            self.assertTrue(updated["status"]["needs_restart"])
            self.assertIn("model catalog", updated["status"]["source_of_truth"])

    def test_secret_only_update_does_not_mark_codex_restart_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Secret Provider",
                "short_alias": "secret",
                "api_key": "old-secret",
                "models": [{"id": "model-a", "selected": True, "context_window": 128000}],
            })

            updated = registry.update_provider(provider["id"], {"api_key": "new-secret"})

            self.assertFalse(updated["status"]["needs_restart"])

    def test_provider_test_preserves_pending_codex_restart_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Restart Provider",
                "short_alias": "restart",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "models": [{"id": "model-a", "selected": True, "context_window": 128000}],
            })
            registry.update_provider(provider["id"], {
                "models": [{"id": "model-a", "selected": True, "context_window": 64000}],
            })

            result = registry.test_provider(provider_id=provider["id"])
            loaded = registry.get_provider(provider["id"], include_secrets=True)

            self.assertTrue(result["success"])
            self.assertTrue(loaded["status"]["needs_restart"])

    def test_test_provider_payload_validates_draft_without_writing_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            saved = registry.create_provider({
                "display_name": "Saved Provider",
                "short_alias": "saved",
                "base_url": "https://saved.example.test/v1",
                "api_key": "secret",
            })

            result = registry.test_provider(provider_data={
                "display_name": "Draft Provider",
                "short_alias": "draft",
                "base_url": "https://draft.example.test/v1",
                "api_key": "",
                "native_currency": "USD",
            })

            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "local_validation")
            self.assertIn("api_key is empty", "; ".join(result["warnings"]))
            loaded = registry.get_provider(saved["id"], include_secrets=True)
            self.assertEqual(loaded["status"]["last_tested"], "")

    def test_catalog_preview_resolves_duplicate_alias_collisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "providers.json"
            store_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "providers": [
                        {
                            "id": "provider-one",
                            "display_name": "Provider One",
                            "short_alias": "dup",
                            "enabled": True,
                            "catalog_visibility": "always_visible",
                            "models": [{"id": "shared", "enabled": True}],
                        },
                        {
                            "id": "provider-two",
                            "display_name": "Provider Two",
                            "short_alias": "dup",
                            "enabled": True,
                            "catalog_visibility": "always_visible",
                            "models": [{"id": "shared", "enabled": True}],
                        },
                    ],
                }),
                encoding="utf-8",
            )
            registry = ProviderRegistry(str(store_path))

            preview = registry.preview_catalog()
            model_ids = [entry["codex_model_id"] for entry in preview["entries"]]
            self.assertEqual(model_ids, ["Provider One/shared", "Provider Two/shared"])
            self.assertFalse(any(entry["catalog_collision"] for entry in preview["entries"]))
            self.assertFalse(any("Catalog ID collision" in item for item in preview["route_explanation"]))

    def test_focus_provider_includes_all_enabled_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Focused Provider",
                "short_alias": "focus",
                "catalog_visibility": "focused_only",
                "models": [
                    {"id": "a", "selected": False},
                    {"id": "b", "selected": False},
                ],
            })

            preview = registry.preview_catalog(focus_provider_id=provider["id"])
            self.assertEqual(preview["entry_count"], 2)

    def test_catalog_preview_with_provider_draft_uses_unsaved_models_without_saving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Draft Catalog Provider",
                "short_alias": "draft",
                "api_key": "saved-secret",
                "catalog_visibility": "focused_only",
                "capabilities": {"text": True, "images": False},
                "models": [{"id": "saved-only", "selected": False, "context_window": 1000}],
            })

            preview = registry.preview_catalog_with_provider_draft(provider["id"], {
                "api_key": REDACTED_VALUE,
                "catalog_visibility": "selected_models",
                "capabilities": {"text": True, "images": True},
                "models": [{
                    "id": "unsaved-image",
                    "display_name": "Unsaved Image",
                    "selected": True,
                    "context_window": 256000,
                    "capabilities": {"text": True, "images": True},
                    "pricing": {"input_per_million": 0.5, "per_image": 0.02},
                    "native_currency": "CNY",
                }],
            })

            self.assertTrue(preview["success"])
            self.assertTrue(preview["preview"])
            self.assertEqual(preview["focus_provider_id"], provider["id"])
            self.assertEqual(preview["entry_count"], 1)
            entry = preview["entries"][0]
            self.assertEqual(entry["codex_model_id"], "Draft Catalog Provider/Unsaved Image")
            self.assertEqual(entry["context_window"], 256000)
            self.assertTrue(entry["capabilities"]["images"])
            self.assertEqual(entry["native_currency"], "CNY")
            self.assertEqual(entry["pricing"]["per_image"], 0.02)

            saved = registry.get_provider(provider["id"], include_secrets=True)
            self.assertEqual(saved["api_key"], "saved-secret")
            self.assertEqual([model["id"] for model in saved["models"]], ["saved-only"])

    def test_anthropic_api_format_is_valid(self):
        provider = normalize_provider({
            "display_name": "Anthropic",
            "short_alias": "claude",
            "api_format": "anthropic",
            "models": [{"id": "claude-model"}],
        })

        self.assertEqual(provider["api_format"], "anthropic")

    def test_known_provider_question_corruption_is_repaired(self):
        bailian = normalize_provider({
            "id": "alibaba-bailian-cn",
            "short_alias": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "display_name": "?" * 10,
            "codex_visible_alias": "?" * 5,
            "models": [{"id": "qwen"}],
        })
        self.assertEqual(bailian["display_name"], "\u963f\u91cc\u4e91\u767e\u70bc")
        self.assertEqual(bailian["codex_visible_alias"], "\u963f\u91cc\u4e91\u767e\u70bc")

        ark = normalize_provider({
            "id": "volcengine-ark-cn",
            "short_alias": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "display_name": "?" * 9,
            "models": [{"id": "ark-code-latest"}],
        })
        self.assertEqual(ark["display_name"], "\u706b\u5c71\u5f15\u64ce")

    def test_compatible_responses_does_not_force_all_capabilities(self):
        provider = normalize_provider({
            "display_name": "Compatible Responses",
            "short_alias": "cr",
            "api_format": "openai_responses",
            "responses_profile": {"mode": "compatible"},
            "capabilities": {"text": True, "vision": False, "images": False},
            "models": [{"id": "auto"}],
        })

        self.assertEqual(provider["responses_profile"]["mode"], "compatible")
        self.assertFalse(provider["native_responses"])
        self.assertFalse(provider["native_capabilities_locked"])
        self.assertFalse(provider["capabilities"]["vision"])
        self.assertFalse(provider["capabilities"]["images"])

    def test_native_responses_forces_all_runtime_capabilities(self):
        provider = normalize_provider({
            "display_name": "Native Responses",
            "short_alias": "nr",
            "api_format": "openai_responses",
            "responses_profile": {"mode": "native"},
            "capabilities": {"vision": False, "images": False, "tools": False},
            "models": [{"id": "auto", "capabilities": {"vision": False, "images": False}}],
        })

        self.assertEqual(provider["responses_profile"]["mode"], "native")
        self.assertTrue(provider["native_responses"])
        self.assertTrue(provider["native_capabilities_locked"])
        self.assertTrue(provider["capabilities"]["vision"])
        self.assertTrue(provider["capabilities"]["images"])
        preview = build_catalog_preview_from_providers([provider], focus_provider_id=provider["id"])
        caps = preview["entries"][0]["capabilities"]
        self.assertTrue(caps["vision"])
        self.assertTrue(caps["images"])
        self.assertTrue(caps["native_approval"])

    def test_codex_login_provider_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Codex Login",
                "short_alias": "codex",
                "auth_mode": "official_oauth",
                "api_format": "openai_responses",
                "models": [{"id": "gpt-5"}],
            })

            loaded = registry.get_provider(provider["id"], include_secrets=True)
            self.assertTrue(loaded["read_only"])
            self.assertTrue(loaded["native_capabilities_locked"])
            with self.assertRaises(ValueError):
                registry.update_provider(provider["id"], {"display_name": "Changed"})
            with self.assertRaises(ValueError):
                registry.delete_provider(provider["id"])

    def test_model_api_format_and_native_approval_are_model_level_settings(self):
        provider = normalize_provider({
            "display_name": "Mixed Interface",
            "short_alias": "mixif",
            "api_format": "openai_responses",
            "catalog_visibility": "selected_models",
            "models": [
                {"id": "chat-model", "api_format": "openai_chat", "native_approval": False, "selected": True},
                {"id": "responses-model", "api_format": "openai_responses", "native_approval": True, "selected": True},
            ],
        })

        chat_model, responses_model = provider["models"]
        self.assertEqual(chat_model["api_format"], "openai_chat")
        self.assertFalse(chat_model["native_approval"])
        self.assertEqual(responses_model["api_format"], "openai_responses")
        self.assertTrue(responses_model["native_approval"])
        self.assertTrue(responses_model["capability_overrides"]["native_approval"])

        preview = build_catalog_preview_from_providers([provider])
        entries = {entry["upstream_model_id"]: entry for entry in preview["entries"]}
        self.assertEqual(entries["chat-model"]["api_format"], "openai_chat")
        self.assertEqual(entries["chat-model"]["api_format_source"], "model")
        self.assertEqual(entries["responses-model"]["api_format"], "openai_responses")
        self.assertTrue(entries["responses-model"]["capabilities"]["native_approval"])

    def test_model_alias_and_regex_rewrite_schema_is_normalized(self):
        provider = normalize_provider({
            "display_name": "Alias Provider",
            "short_alias": "alias",
            "aliases": [{"from": "coder-pro", "to": "qwen3-coder-plus"}],
            "alias_patterns": [
                {"pattern": "^fast-(.+)$", "replacement": "\\1-turbo"},
                {"pattern": "", "replacement": "ignored"},
            ],
            "models": [{"id": "qwen3-coder-plus", "aliases": ["coder", "coding"]}],
        })

        self.assertEqual(provider["aliases"], {"coder-pro": "qwen3-coder-plus"})
        self.assertEqual(provider["alias_patterns"], [{
            "pattern": "^fast-(.+)$",
            "replacement": "\\1-turbo",
            "enabled": True,
            "description": "",
        }])
        self.assertEqual(provider["models"][0]["aliases"], ["coder", "coding"])

    def test_media_profile_preserves_model_overrides(self):
        provider = normalize_provider({
            "display_name": "Media Overrides",
            "short_alias": "media",
            "api_format": "openai_images",
            "media_profile": {
                "image_model_overrides": {"cover-art": "gpt-image-1.5"},
                "video_model_overrides": {"storyboard": "sora-2"},
            },
            "models": [{"id": "gpt-image-1.5"}],
        })

        self.assertEqual(provider["media_profile"]["image_model_overrides"]["cover-art"], "gpt-image-1.5")
        self.assertEqual(provider["media_profile"]["video_model_overrides"]["storyboard"], "sora-2")
        self.assertTrue(provider["capabilities"]["images"])

    def test_media_profile_infers_catalog_capability(self):
        provider = normalize_provider({
            "display_name": "Native Media",
            "short_alias": "native",
            "api_format": "openai_responses",
            "capabilities": {"text": True},
            "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
            "models": [{"id": "auto", "selected": True}],
        })

        self.assertTrue(provider["capabilities"]["images"])
        self.assertEqual(provider["models"][0]["capability_overrides"], {})

    def test_validate_warns_when_media_mode_has_no_media_capability(self):
        provider = normalize_provider({
            "display_name": "Media Mode Only",
            "short_alias": "media-only",
            "api_format": "openai_responses",
            "capabilities": {"text": True, "images": False, "videos": False},
            "media_profile": {"openai_compatible_media": True},
            "models": [{"id": "auto"}],
        })

        _errors, warnings = validate_provider(provider)

        self.assertTrue(any("no image capability" in warning for warning in warnings))

    def test_proxy_profile_preserves_bypass_and_network_policy(self):
        provider = normalize_provider({
            "display_name": "Proxy Profile",
            "short_alias": "proxy",
            "proxy_profile": {
                "bypass_system_proxy": "false",
                "upstream_timeout_seconds": "45",
                "retry_attempts": "3",
                "retry_backoff_ms": "750",
            },
            "models": [{"id": "model-a"}],
        })

        profile = provider["proxy_profile"]
        self.assertFalse(profile["bypass_system_proxy"])
        self.assertEqual(profile["upstream_timeout_seconds"], 45)
        self.assertEqual(profile["retry_attempts"], 3)
        self.assertEqual(profile["retry_backoff_ms"], 750)

    def test_approval_profile_defaults_to_auto_approve(self):
        provider = normalize_provider({
            "display_name": "Approval Default",
            "short_alias": "approval",
            "models": [{"id": "model-a"}],
        })

        profile = provider["approval_profile"]
        self.assertEqual(profile["mode"], "proxy_auto_approve")
        self.assertEqual(profile["mode_source"], "default")
        self.assertFalse(profile["official_guardian"])
        self.assertTrue(profile["proxy_auto_approve"])
        self.assertEqual(profile["on_review_error"], "decline")

    def test_approval_profile_can_stay_manual_when_explicit(self):
        provider = normalize_provider({
            "display_name": "Approval Manual",
            "short_alias": "manual",
            "approval_profile": {"mode": "manual_only"},
            "models": [{"id": "model-a"}],
        })

        profile = provider["approval_profile"]
        self.assertEqual(profile["mode"], "manual_only")
        self.assertEqual(profile["mode_source"], "explicit")
        self.assertFalse(profile["proxy_auto_approve"])

    def test_approval_profile_supports_proxy_auto_approve(self):
        provider = normalize_provider({
            "display_name": "Approval Broker",
            "short_alias": "broker",
            "approval_profile": {
                "mode": "proxy_auto_approve",
                "reviewer_model": "qwen/qwen3-coder-plus",
                "allowed_actions": ["exec", "network"],
                "timeout_ms": "30000",
                "max_retries": "2",
            },
            "models": [{"id": "qwen3-coder-plus"}],
        })

        profile = provider["approval_profile"]
        self.assertEqual(profile["mode"], "proxy_auto_approve")
        self.assertEqual(profile["mode_source"], "explicit")
        self.assertTrue(profile["proxy_auto_approve"])
        self.assertEqual(profile["reviewer_model"], "qwen/qwen3-coder-plus")
        self.assertEqual(profile["allowed_actions"], ["exec", "network"])
        self.assertEqual(profile["timeout_ms"], 30000)
        self.assertEqual(profile["max_retries"], 2)

    def test_secret_key_word_boundary(self):
        # Should match true secrets
        self.assertTrue(is_secret_key("api_key"))
        self.assertTrue(is_secret_key("API_KEY"))
        self.assertTrue(is_secret_key("Authorization"))
        self.assertTrue(is_secret_key("x-api-key"))
        self.assertTrue(is_secret_key("my_token"))
        self.assertTrue(is_secret_key("secret"))
        self.assertTrue(is_secret_key("password"))
        # Should NOT match false positives
        self.assertFalse(is_secret_key("monkey"))
        self.assertFalse(is_secret_key("tokenize"))
        self.assertFalse(is_secret_key("mysecretstuff"))
        self.assertFalse(is_secret_key("apikeyname"))  # contains apikey but as part of a larger word

    # --- New preset tests ---

    def test_openrouter_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "openrouter"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("openrouter")
            self.assertEqual(provider["base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(provider["api_format"], "openai_chat")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            self.assertFalse(provider["responses_profile"]["partial_compatibility"])
            self.assertFalse(provider["responses_profile"]["requires_adapter"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("openai/gpt-4o", model_ids)
            self.assertIn("anthropic/claude-3.5-sonnet", model_ids)
            self.assertIn("google/gemini-pro", model_ids)
            self.assertIn("meta-llama/llama-3-70b", model_ids)
            self.assertIn("deepseek/deepseek-chat", model_ids)
            self.assertEqual(provider["native_currency"], "USD")
            self.assertEqual(provider["country_region"], "US")
            self.assertEqual(provider["caveat"], "OpenRouter 提供多供应商聚合，某些模型可能有速率限制或可用性波动。")

    def test_deepseek_official_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "deepseek-official"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("deepseek-official")
            self.assertEqual(provider["base_url"], "https://api.deepseek.com/v1")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["capabilities"]["vision"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            self.assertFalse(provider["responses_profile"]["partial_compatibility"])
            self.assertFalse(provider["responses_profile"]["requires_adapter"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("deepseek-chat", model_ids)
            self.assertIn("deepseek-coder", model_ids)
            self.assertIn("deepseek-reasoner", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "DeepSeek 官方 API 使用 Chat Completions 格式，不支持原生 Responses API。")

    def test_openai_compatible_images_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.import_preset("openai-compatible-images")

            self.assertEqual(provider["api_format"], "openai_images")
            self.assertTrue(provider["capabilities"]["images"])
            self.assertFalse(provider["capabilities"]["text"])
            self.assertTrue(provider["media_profile"]["default_image_provider"])
            self.assertTrue(provider["media_profile"]["openai_compatible_media"])
            self.assertFalse(provider["media_profile"]["adapter_required"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("gpt-image-1.5", model_ids)
            self.assertIn("dall-e-3", model_ids)

    def test_openai_compatible_videos_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            visible_preset_ids = [p["preset_id"] for p in registry.list_presets()["presets"]]
            self.assertNotIn("openai-compatible-videos", visible_preset_ids)
            provider = registry.import_preset("openai-compatible-videos")

            self.assertEqual(provider["api_format"], "openai_videos")
            self.assertTrue(provider["capabilities"]["videos"])
            self.assertFalse(provider["capabilities"]["text"])
            self.assertTrue(provider["media_profile"]["default_video_provider"])
            self.assertTrue(provider["media_profile"]["openai_compatible_media"])
            self.assertTrue(provider["media_profile"]["async_submit"])
            self.assertTrue(provider["media_profile"]["poll_required"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("sora-2", model_ids)
            self.assertIn("sora-2-pro", model_ids)

    def test_local_proxy_media_bridge_preset_enables_image_passthrough(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "codex-api-key-mixin"), None)
            self.assertIsNotNone(preset)
            visible_text = " ".join([
                preset.get("name", ""),
                preset.get("description", ""),
                preset.get("provider", {}).get("display_name", ""),
                preset.get("provider", {}).get("caveat", ""),
                preset.get("provider", {}).get("notes", ""),
            ])
            self.assertNotIn("Code++", visible_text)
            self.assertNotIn("Mix-in", visible_text)
            self.assertNotIn("mix-in", visible_text)
            self.assertNotIn("混入", visible_text)

            provider = registry.import_preset("codex-api-key-mixin")

            self.assertEqual(provider["api_format"], "openai_responses")
            self.assertTrue(provider["capabilities"]["images"])
            self.assertTrue(provider["media_profile"]["default_image_provider"])
            self.assertTrue(provider["media_profile"]["openai_compatible_media"])
            self.assertEqual(provider["models"][0]["capability_overrides"], {})

            preview = registry.preview_catalog()
            entry = next(item for item in preview["entries"] if item["provider_id"] == provider["id"])
            self.assertTrue(entry["capabilities"]["images"])

    def test_moonshot_kimi_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "moonshot-kimi"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("moonshot-kimi")
            self.assertEqual(provider["base_url"], "https://api.moonshot.cn/v1")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("kimi-k2", model_ids)
            self.assertIn("moonshot-v1-8k", model_ids)
            self.assertIn("moonshot-v1-32k", model_ids)
            self.assertIn("moonshot-v1-128k", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "Moonshot API 使用 OpenAI 兼容格式，支持 tool calling 和 vision input。")

    def test_zhipu_glm_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "zhipu-glm"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("zhipu-glm")
            self.assertEqual(provider["base_url"], "https://open.bigmodel.cn/api/paas/v4")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("glm-4", model_ids)
            self.assertIn("glm-4v", model_ids)
            self.assertIn("glm-4-flash", model_ids)
            self.assertIn("codegeex-4", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "智谱 GLM API 使用 OpenAI 兼容格式，glm-4v 支持 vision input。")

    def test_siliconflow_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "siliconflow"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("siliconflow")
            self.assertEqual(provider["base_url"], "https://api.siliconflow.cn/v1")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["capabilities"]["vision"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("Qwen/Qwen2.5-72B-Instruct", model_ids)
            self.assertIn("deepseek-ai/DeepSeek-V3", model_ids)
            self.assertIn("meta-llama/Llama-3.3-70B-Instruct", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "SiliconFlow 提供多种开源模型的统一 API 接入。")

    def test_minimax_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "minimax"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("minimax")
            self.assertEqual(provider["base_url"], "https://api.minimax.chat/v1")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("abab6.5s-chat", model_ids)
            self.assertIn("abab6-chat", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "MiniMax API 部分功能可能与 OpenAI 标准有差异。")

    def test_azure_openai_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "azure-openai"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("azure-openai")
            self.assertEqual(provider["base_url"], "https://{your-resource-name}.openai.azure.com/openai/deployments/{deployment-id}")
            self.assertEqual(provider["api_format"], "openai_chat")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("gpt-4o", model_ids)
            self.assertIn("gpt-4", model_ids)
            self.assertIn("gpt-35-turbo", model_ids)
            self.assertEqual(provider["native_currency"], "USD")
            self.assertEqual(provider["country_region"], "US")
            self.assertEqual(provider["caveat"], "Azure OpenAI 需要设置 api_version 查询参数（如 2024-10-21），base_url 中的 resource name 和 deployment id 必须替换为实际值。不支持原生 Responses API，使用 Chat Completions 格式。")

    def test_custom_responses_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "custom-responses"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("custom-responses")
            self.assertEqual(provider["base_url"], "https://your-custom-gateway.example.com/v1")
            self.assertEqual(provider["api_format"], "openai_responses")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertTrue(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("auto", model_ids)
            self.assertEqual(provider["caveat"], "自定义 Responses 兼容网关。需要确认上游是否完整支持 tools、custom tools、streaming terminal events、previous_response_id。")

    def test_modelscope_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "modelscope"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("modelscope")
            self.assertEqual(provider["base_url"], "https://www.modelscope.cn/api/v1/studio")
            self.assertEqual(provider["api_format"], "openai_chat")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertFalse(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("qwen2.5-72b-instruct", model_ids)
            self.assertIn("llama3.1-70b-instruct", model_ids)
            self.assertIn("glm-4-9b-chat", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "ModelScope 提供多种开源模型推理服务。具体 base URL 和模型可用性请以 ModelScope 官方文档为准。")

    def test_stepfun_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "stepfun"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("stepfun")
            self.assertEqual(provider["base_url"], "https://api.stepfun.com/v1")
            self.assertEqual(provider["api_format"], "openai_chat")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertTrue(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("step-1-8k", model_ids)
            self.assertIn("step-1-32k", model_ids)
            self.assertIn("step-1-128k", model_ids)
            self.assertIn("step-1-256k", model_ids)
            self.assertIn("step-1v-32k", model_ids)
            self.assertEqual(provider["native_currency"], "CNY")
            self.assertEqual(provider["country_region"], "CN")
            self.assertEqual(provider["caveat"], "阶跃星辰 StepFun API 使用 OpenAI 兼容格式。step-1v 系列支持 vision input。")

    def test_nvidia_build_preset_schema_and_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            preset = next((p for p in registry.list_presets()["presets"] if p["preset_id"] == "nvidia-build"), None)
            self.assertIsNotNone(preset)
            provider = registry.import_preset("nvidia-build")
            self.assertEqual(provider["base_url"], "https://integrate.api.nvidia.com/v1")
            self.assertEqual(provider["api_format"], "openai_chat")
            self.assertTrue(provider["capabilities"]["text"])
            self.assertFalse(provider["capabilities"]["vision"])
            self.assertTrue(provider["capabilities"]["tools"])
            self.assertFalse(provider["capabilities"]["reasoning"])
            self.assertTrue(provider["capabilities"]["streaming"])
            self.assertFalse(provider["responses_profile"]["domestic_responses"])
            model_ids = [m["id"] for m in provider["models"]]
            self.assertIn("meta/llama3-70b-instruct", model_ids)
            self.assertIn("meta/llama3-8b-instruct", model_ids)
            self.assertIn("nvidia/nemotron-4-340b-instruct", model_ids)
            self.assertEqual(provider["native_currency"], "USD")
            self.assertEqual(provider["country_region"], "US")
            self.assertEqual(provider["caveat"], "NVIDIA build endpoint 提供多种开源模型推理。需要 NVIDIA API 密钥。")

    def test_bulk_select_all_and_deselect_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Bulk Provider",
                "short_alias": "bulk",
                "models": [
                    {"id": "a", "selected": False},
                    {"id": "b", "selected": False},
                    {"id": "c", "enabled": False, "selected": False},
                ],
            })
            pid = provider["id"]

            result = registry.bulk_update_models(pid, "select_all")
            self.assertTrue(result["success"])
            self.assertEqual(result["changed"], 2)

            updated = registry.get_provider(pid, include_secrets=True)
            self.assertTrue(updated["models"][0]["selected"])
            self.assertTrue(updated["models"][1]["selected"])
            # disabled model 不应被修改
            self.assertFalse(updated["models"][2]["selected"])

            result = registry.bulk_update_models(pid, "deselect_all")
            self.assertEqual(result["changed"], 2)
            updated = registry.get_provider(pid, include_secrets=True)
            self.assertFalse(updated["models"][0]["selected"])
            self.assertFalse(updated["models"][1]["selected"])

    def test_bulk_select_vision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Vision Bulk",
                "short_alias": "vis",
                "capabilities": {"vision": True},
                "models": [
                    {"id": "vision-model", "capabilities": {"vision": True}, "selected": False},
                    {"id": "text-model", "capabilities": {"vision": False}, "selected": True},
                ],
            })
            pid = provider["id"]
            result = registry.bulk_update_models(pid, "select_vision")
            # vision-model: False -> True (changed)
            # text-model: True -> False (changed)
            self.assertEqual(result["changed"], 2)
            updated = registry.get_provider(pid, include_secrets=True)
            self.assertTrue(updated["models"][0]["selected"])
            self.assertFalse(updated["models"][1]["selected"])

    def test_bulk_select_high_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Context Bulk",
                "short_alias": "ctx",
                "models": [
                    {"id": "small", "context_window": 32000, "selected": False},
                    {"id": "large", "context_window": 256000, "selected": False},
                ],
            })
            pid = provider["id"]
            result = registry.bulk_update_models(pid, "select_high_context", {"context_threshold": 128000})
            self.assertEqual(result["changed"], 1)
            updated = registry.get_provider(pid, include_secrets=True)
            self.assertFalse(updated["models"][0]["selected"])
            self.assertTrue(updated["models"][1]["selected"])

    def test_set_provider_visibility(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Visibility Test",
                "short_alias": "vis",
            })
            pid = provider["id"]
            result = registry.set_provider_visibility(pid, "always_visible")
            self.assertTrue(result["success"])
            self.assertTrue(result["changed"])
            self.assertEqual(result["previous"], "focused_only")
            self.assertEqual(result["current"], "always_visible")

            updated = registry.get_provider(pid, include_secrets=True)
            self.assertEqual(updated["catalog_visibility"], "always_visible")

    def test_set_focus_provider_persists_and_catalog_uses_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({
                "display_name": "Focused Test",
                "short_alias": "focus",
                "catalog_visibility": "focused_only",
                "models": [{"id": "focus-model", "selected": False}],
            })

            result = registry.set_focus_provider(provider["id"])
            self.assertTrue(result["success"])
            self.assertTrue(result["changed"])

            listed = registry.list_providers()
            self.assertEqual(listed["focus_provider_id"], provider["id"])
            preview = registry.preview_catalog()
            self.assertEqual(preview["focus_provider_id"], provider["id"])
            self.assertEqual(preview["entry_count"], 1)

    def test_set_focus_provider_rejects_unknown_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            result = registry.set_focus_provider("missing")
            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "Provider not found")

    def test_invalid_bulk_action_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({"display_name": "X", "short_alias": "x"})
            with self.assertRaises(ValueError):
                registry.bulk_update_models(provider["id"], "invalid_action")

    def test_invalid_visibility_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ProviderRegistry(str(Path(tmpdir) / "providers.json"))
            provider = registry.create_provider({"display_name": "X", "short_alias": "x"})
            with self.assertRaises(ValueError):
                registry.set_provider_visibility(provider["id"], "invalid_visibility")


if __name__ == "__main__":
    unittest.main()
