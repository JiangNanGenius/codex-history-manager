import json
import unittest
from unittest.mock import MagicMock, patch


class FakeQuotaResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def getcode(self):
        return self.status


class ProviderHealthApiTest(unittest.TestCase):
    def _app_with_health_result(self, result):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB"),
            patch("app.BackupManager"),
            patch("app.TokenStats"),
            patch("app.ProviderRegistry") as MockProviderRegistry,
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer"),
            patch("app.AMRRegistry"),
            patch("app.QuotaManager"),
            patch("app.StartupManager"),
            patch("app.DiagnosticsCollector") as MockDiagnosticsCollector,
        ):
            config = MagicMock()
            config.get.side_effect = lambda key, default=None: default
            config.get_all.return_value = {}
            config.is_write_locked.return_value = False
            config.write_lock_reason.return_value = ""
            MockConfig.return_value = config

            registry = MagicMock()
            MockProviderRegistry.return_value = registry

            diagnostics = MagicMock()
            diagnostics.check_provider_connectivity.return_value = result
            MockDiagnosticsCollector.return_value = diagnostics

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            return flask_app, diagnostics

    def _app_for_provider_draft(self, existing_provider):
        with (
            patch("app.Config") as MockConfig,
            patch("app.CodexDB"),
            patch("app.BackupManager"),
            patch("app.TokenStats"),
            patch("app.ProviderRegistry") as MockProviderRegistry,
            patch("app.AutoApprovalModelReviewer"),
            patch("app.LocalProxyServer"),
            patch("app.AMRRegistry"),
            patch("app.QuotaManager"),
            patch("app.StartupManager"),
            patch("app.DiagnosticsCollector") as MockDiagnosticsCollector,
        ):
            config = MagicMock()
            config.get.side_effect = lambda key, default=None: default
            config.get_all.return_value = {}
            config.is_write_locked.return_value = False
            config.write_lock_reason.return_value = ""
            MockConfig.return_value = config

            registry = MagicMock()
            registry.get_provider.return_value = existing_provider
            MockProviderRegistry.return_value = registry
            diagnostics = MagicMock()
            MockDiagnosticsCollector.return_value = diagnostics

            from app import create_app

            flask_app = create_app()
            flask_app.config["TESTING"] = True
            return flask_app, registry, diagnostics

    def test_provider_health_check_returns_structured_failure_with_200(self):
        app, diagnostics = self._app_with_health_result({
            "success": False,
            "reachable": False,
            "provider_id": "p1",
            "error": "Could not connect to any tested endpoint.",
        })

        response = app.test_client().post("/api/providers/p1/health-check")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["success"])
        self.assertFalse(data["reachable"])
        self.assertEqual(data["provider_id"], "p1")
        diagnostics.check_provider_connectivity.assert_called_once_with("p1")

    def test_diagnostics_provider_test_preserves_error_status(self):
        app, diagnostics = self._app_with_health_result({
            "success": False,
            "reachable": False,
            "provider_id": "p1",
            "error": "Could not connect to any tested endpoint.",
        })

        response = app.test_client().post("/api/diagnostics/test-provider/p1")

        self.assertEqual(response.status_code, 503)
        data = response.get_json()
        self.assertFalse(data["success"])
        diagnostics.check_provider_connectivity.assert_called_once_with("p1")

    def test_quota_refresh_draft_preserves_saved_secret_and_uses_draft_quota_check(self):
        app, registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "base_url": "https://api.example.test/v1",
            "api_key": "saved-secret",
            "quota_check": {"enabled": True, "url": "https://old.example.test/quota"},
        })

        with patch("quota.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = FakeQuotaResponse({"balance": 42})
            response = app.test_client().post("/api/providers/p1/quota/refresh-draft", json={
                "provider": {
                    "api_key": "********",
                    "quota_check": {
                        "enabled": True,
                        "url": "https://new.example.test/quota",
                        "json_paths": {"balance": "$.balance"},
                    },
                }
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["preview"])
        self.assertEqual(data["values"], {"balance": 42})
        registry.get_provider.assert_called_once_with("p1", include_secrets=True)
        request_arg = mock_urlopen.call_args.args[0]
        self.assertEqual(request_arg.full_url, "https://new.example.test/quota")
        self.assertEqual(request_arg.headers["Authorization"], "Bearer saved-secret")

    def test_provider_health_draft_preserves_saved_secret_and_uses_draft_base_url(self):
        app, registry, diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "base_url": "https://old.example.test/v1",
            "api_key": "saved-secret",
            "headers": {"Authorization": "Bearer saved-header"},
        })
        diagnostics.check_provider_payload_connectivity.return_value = {
            "success": True,
            "reachable": True,
            "provider_id": "p1",
            "preview": True,
        }

        response = app.test_client().post("/api/providers/p1/health-check-draft", json={
            "provider": {
                "api_key": "********",
                "base_url": "https://new.example.test/v1",
                "headers": {"Authorization": "********", "X-Trace-Id": "draft"},
            }
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        registry.get_provider.assert_called_once_with("p1", include_secrets=True)
        provider = diagnostics.check_provider_payload_connectivity.call_args.args[0]
        self.assertEqual(provider["api_key"], "saved-secret")
        self.assertEqual(provider["base_url"], "https://new.example.test/v1")
        self.assertEqual(provider["headers"]["Authorization"], "Bearer saved-header")
        self.assertEqual(provider["headers"]["X-Trace-Id"], "draft")

    def test_provider_request_preview_draft_uses_real_proxy_headers_and_redacts(self):
        app, registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "base_url": "https://old.example.test/v1",
            "api_key": "saved-secret",
            "api_format": "openai_chat",
            "headers": {"Authorization": "Bearer saved-header", "X-Trace-Id": "old"},
            "aliases": {"codex-fast": "gpt-5-real"},
            "models": [
                {"id": "gpt-5-real", "enabled": True, "selected": True, "api_format": "openai_responses"},
            ],
        })

        response = app.test_client().post("/api/providers/p1/request-preview-draft", json={
            "model": "p1/codex-fast",
            "provider": {
                "api_key": "********",
                "base_url": "https://new.example.test/v1",
                "user_agent": "DraftUA/1.0",
                "headers": {"Authorization": "********", "X-Trace-Id": "draft"},
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["preview"])
        self.assertFalse(data["network_request"])
        self.assertTrue(data["uses_real_proxy_headers"])
        self.assertEqual(data["base_url"], "https://new.example.test/v1")
        self.assertEqual(data["requested_model"], "p1/codex-fast")
        self.assertEqual(data["upstream_model"], "gpt-5-real")
        self.assertEqual(data["api_format"], "openai_responses")
        self.assertEqual(data["headers"]["Content-Type"], "application/json")
        self.assertEqual(data["headers"]["User-Agent"], "DraftUA/1.0")
        self.assertEqual(data["headers"]["X-Trace-Id"], "draft")
        self.assertEqual(data["headers"]["Authorization"], "********")
        self.assertNotIn("saved-secret", json.dumps(data, ensure_ascii=False))
        self.assertTrue(any("Exact model alias" in line for line in data["route_explanation"]))
        registry.get_provider.assert_called_once_with("p1", include_secrets=True)

    def test_provider_request_preview_draft_shows_regex_model_rewrite(self):
        app, _registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "base_url": "https://api.example.test/v1",
            "api_format": "openai_chat",
            "alias_patterns": [{"pattern": "^fast-(.+)$", "replacement": "\\1-turbo"}],
            "models": [{"id": "qwen-turbo", "enabled": True, "selected": True}],
        })

        response = app.test_client().post("/api/providers/p1/request-preview-draft", json={
            "request": {"model": "fast-qwen"},
            "provider": {},
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["requested_model"], "fast-qwen")
        self.assertEqual(data["upstream_model"], "qwen-turbo")
        self.assertEqual(data["api_format"], "openai_chat")
        self.assertTrue(any("Regex model mapping" in line for line in data["route_explanation"]))

    def test_model_catalog_preview_draft_uses_registry_draft_preview(self):
        app, registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
        })
        registry.preview_catalog_with_provider_draft.return_value = {
            "success": True,
            "preview": True,
            "focus_provider_id": "p1",
            "entries": [{"codex_model_id": "p1/draft"}],
            "entry_count": 1,
            "route_explanation": [],
        }

        response = app.test_client().post("/api/providers/p1/model-catalog/preview-draft", json={
            "provider": {
                "models": [{"id": "draft", "selected": True}],
            }
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["preview"])
        self.assertEqual(data["entries"][0]["codex_model_id"], "p1/draft")
        registry.preview_catalog_with_provider_draft.assert_called_once_with(
            "p1",
            {"models": [{"id": "draft", "selected": True}]},
        )

    def test_media_adapter_preview_draft_uses_draft_without_leaking_prompt(self):
        app, registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "api_key": "saved-secret",
            "media_profile": {"adapter_required": False},
        })

        response = app.test_client().post("/api/providers/p1/media-adapter/preview-draft", json={
            "provider": {
                "api_key": "********",
                "media_profile": {
                    "adapter_required": True,
                    "adapter": "volcengine_ark",
                },
            },
            "request": {
                "model": "seedream",
                "prompt": "private prompt",
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["preview"])
        self.assertEqual(data["adapter_id"], "volcengine_ark")
        self.assertTrue(data["adapter_required"])
        self.assertEqual(len(data["previews"]), 4)
        self.assertNotIn("private prompt", json.dumps(data, ensure_ascii=False))
        registry.get_provider.assert_called_once_with("p1", include_secrets=True)

    def test_media_route_preview_draft_reports_openai_image_pass_through(self):
        app, registry, _diagnostics = self._app_for_provider_draft({
            "id": "p1",
            "display_name": "Provider",
            "short_alias": "p1",
            "api_key": "saved-secret",
            "base_url": "https://old.example.test/v1",
            "capabilities": {"text": True},
            "media_profile": {"openai_compatible_media": False},
        })

        response = app.test_client().post("/api/providers/p1/media-route/preview-draft", json={
            "provider": {
                "api_key": "********",
                "base_url": "https://new.example.test/v1",
                "capabilities": {"images": True},
                "media_profile": {
                    "default_image_provider": True,
                    "openai_compatible_media": True,
                },
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["preview"])
        self.assertTrue(data["live_forwarding_enabled"])
        image_check = next(item for item in data["checks"] if item["media_kind"] == "image")
        self.assertTrue(image_check["can_forward"])
        self.assertEqual(image_check["upstream_url"], "https://new.example.test/v1/images/generations")
        self.assertNotIn("saved-secret", json.dumps(data, ensure_ascii=False))
        registry.get_provider.assert_called_once_with("p1", include_secrets=True)


if __name__ == "__main__":
    unittest.main()
