import tempfile
import unittest
from pathlib import Path

from providers import ProviderRegistry, REDACTED_VALUE, normalize_provider, is_secret_key


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
            self.assertEqual(model_ids, [f"{provider['short_alias']}/visible"])

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

    def test_anthropic_api_format_is_valid(self):
        provider = normalize_provider({
            "display_name": "Anthropic",
            "short_alias": "claude",
            "api_format": "anthropic",
            "models": [{"id": "claude-model"}],
        })

        self.assertEqual(provider["api_format"], "anthropic")

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

    def test_approval_profile_defaults_to_manual(self):
        provider = normalize_provider({
            "display_name": "Approval Default",
            "short_alias": "approval",
            "models": [{"id": "model-a"}],
        })

        profile = provider["approval_profile"]
        self.assertEqual(profile["mode"], "manual_only")
        self.assertFalse(profile["official_guardian"])
        self.assertFalse(profile["proxy_auto_approve"])
        self.assertEqual(profile["on_review_error"], "decline")

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
            self.assertFalse(provider["capabilities"]["tools"])
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
            self.assertFalse(provider["capabilities"]["tools"])
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
            self.assertFalse(provider["capabilities"]["tools"])
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
