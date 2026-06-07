"""
tests/test_diagnostics.py - DiagnosticsCollector 单元测试。

设计意图：
  - 不依赖真实 Codex 配置、网络或 Flask 环境：所有外部依赖均用 unittest.mock 替换。
  - 覆盖 collect_all、collect_redacted、export_safe_bundle、check_provider_connectivity
    四个核心方法。
  - 对 urllib 的 mock 遵循「最小权限探测」场景：分别模拟可达、不可达、HTTP 错误、
    SSL 错误等边界条件。
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from diagnostics import DiagnosticsCollector
from providers import REDACTED_VALUE


class MockConfig:
    """极简 Config mock，满足 DiagnosticsCollector 依赖。"""

    def get(self, key, default=None):
        return self.get_all().get(key, default)

    def get_all(self):
        return {
            "db_path": ":memory:",
            "proxy_port": 8080,
            "request_log_path": "__missing_request_logs__.jsonl",
            "request_log_retention_days": 30,
            "request_log_max_mb": 50,
            "display_currency": "CNY",
            "exchange_rate_source": "manual",
            "exchange_rate_api_key": "secret-fx-key",
            "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
            "exchange_rate_cache": {
                "EUR:CNY": {
                    "rate": 7.8,
                    "source": "manual",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2026-01-02T00:00:00Z",
                    "is_manual": False,
                }
            },
            "exchange_rate_ttl_hours": 24,
        }


class MockProxyServer:
    """极简 LocalProxyServer mock。"""

    def status(self):
        return {"running": False, "port": 8080, "base_url": ""}


class MockAMRRegistry:
    """极简 AMR registry mock。"""

    def list_groups(self):
        return [{"id": "g1", "display_name": "Group 1", "candidate_count": 2}]


class MockQuotaManager:
    def list_cached(self):
        return {"snapshots": {"p1": {"success": True, "values": {"balance": 3.5}}}}


class TestDiagnosticsCollector(unittest.TestCase):
    def setUp(self):
        """每个测试前重建 mock 依赖，避免状态泄漏。"""
        self.config = MockConfig()
        self.proxy = MockProxyServer()
        self.registry = MagicMock()
        self.registry.list_providers.return_value = {
            "providers": [],
            "focus_provider_id": "",
            "store_path": "/tmp/providers.json",
        }
        self.registry.preview_catalog.return_value = {
            "entry_count": 0,
            "focus_provider_id": "",
            "generated_at": "2024-01-01T00:00:00+00:00",
        }
        self.amr = MockAMRRegistry()
        self.collector = DiagnosticsCollector(
            config=self.config,
            provider_registry=self.registry,
            proxy_server=self.proxy,
            amr_registry=self.amr,
        )

    def _mock_codex_manager(self, MockMgr, config_data=None, auth_data=None):
        """辅助方法：统一构造 CodexConfigManager mock。"""
        mock_mgr = MagicMock()
        mock_mgr.config_path.exists.return_value = True
        mock_mgr.auth_path.exists.return_value = True
        mock_mgr.read_config.return_value = config_data or {}
        mock_mgr.read_auth.return_value = auth_data or {}
        mock_mgr.inspect_permissions.return_value = {
            "approval_policy": "",
            "sandbox_mode": "",
            "issue_count": 0,
            "issues": [],
            "warnings": [],
        }
        MockMgr.return_value = mock_mgr
        return mock_mgr

    @patch("diagnostics.CodexConfigManager")
    def test_collect_all_returns_required_sections(self, MockMgr):
        """
        collect_all() 必须返回所有必需 section。

        边界条件：
          - 即使 provider store 为空，各 section 也应存在且为安全默认值。
        """
        self._mock_codex_manager(
            MockMgr,
            config_data={"model_provider": "openai", "model": "gpt-4"},
            auth_data={"access_token": "jwt-token"},
        )

        result = self.collector.collect_all()

        required_keys = {
            "codex_config",
            "codex_permissions",
            "auth_mode",
            "local_proxy",
            "providers",
            "model_catalog",
            "amr",
            "quota",
            "request_logs",
            "currency",
            "system",
            "errors",
            "collected_at",
        }
        self.assertTrue(required_keys.issubset(result.keys()))

        # 验证 codex_config 子字段
        cc = result["codex_config"]
        self.assertTrue(cc["exists"])
        self.assertEqual(cc["model_provider"], "openai")
        self.assertEqual(cc["model"], "gpt-4")
        self.assertEqual(result["codex_permissions"]["issue_count"], 0)

        # 验证 auth_mode 子字段
        auth = result["auth_mode"]
        self.assertEqual(auth["mode"], "official_oauth")
        self.assertTrue(auth["preserve_official_login"])

        # 验证 amr 子字段（使用了 MockAMRRegistry）
        self.assertIn("groups", result["amr"])
        self.assertEqual(len(result["amr"]["groups"]), 1)
        self.assertEqual(result["quota"], {"snapshots": {}})
        self.assertEqual(result["request_logs"]["count"], 0)
        self.assertEqual(result["currency"]["display_currency"], "CNY")
        self.assertEqual(result["currency"]["manual_override_count"], 1)
        self.assertTrue(result["currency"]["api_key_configured"])
        self.assertNotIn("secret-fx-key", json.dumps(result["currency"], ensure_ascii=False))

    @patch("diagnostics.CodexConfigManager")
    def test_collect_all_includes_quota_cache_when_available(self, MockMgr):
        self._mock_codex_manager(MockMgr)
        collector = DiagnosticsCollector(
            config=self.config,
            provider_registry=self.registry,
            proxy_server=self.proxy,
            amr_registry=self.amr,
            quota_manager=MockQuotaManager(),
        )

        result = collector.collect_all()

        self.assertIn("p1", result["quota"]["snapshots"])
        self.assertEqual(result["quota"]["snapshots"]["p1"]["values"]["balance"], 3.5)

    @patch("diagnostics.CodexConfigManager")
    def test_collect_redacted_redacts_api_key(self, MockMgr):
        """
        collect_redacted() 必须将 api_key 替换为 ********。

        工程权衡：
          - 测试直接检查 providers_summary 中的 api_key 字段，
            而非依赖递归 redact_secrets 的内部实现细节。
        """
        self._mock_codex_manager(MockMgr)
        self.registry.list_providers.return_value = {
            "providers": [
                {
                    "id": "test-provider",
                    "enabled": True,
                    "display_name": "Test",
                    "short_alias": "test",
                    "base_url": "https://example.com/v1",
                    "api_format": "openai_chat",
                    "api_key": "sk-real-secret",
                    "headers": {"Authorization": "Bearer token"},
                    "models": [{"id": "m1", "enabled": True}],
                    "country_region": "US",
                    "native_currency": "USD",
                    "catalog_visibility": "always_visible",
                }
            ],
            "focus_provider_id": "",
            "store_path": "/tmp/providers.json",
        }

        result = self.collector.collect_redacted()
        provider = result["providers"]["providers"][0]

        self.assertEqual(provider["api_key"], REDACTED_VALUE)
        self.assertEqual(provider["headers"]["Authorization"], REDACTED_VALUE)

    @patch("diagnostics.CodexConfigManager")
    def test_export_safe_bundle_outputs_valid_json(self, MockMgr):
        """
        export_safe_bundle() 必须输出合法 JSON，且包含 export_meta。
        """
        self._mock_codex_manager(MockMgr)
        bundle_str = self.collector.export_safe_bundle()

        # 必须是合法 JSON
        bundle = json.loads(bundle_str)
        self.assertIn("export_meta", bundle)
        self.assertTrue(bundle["export_meta"]["redacted"])
        self.assertEqual(bundle["export_meta"]["version"], "1.0")

    @patch("urllib.request.build_opener")
    def test_check_provider_connectivity_reachable(self, mock_build_opener):
        """
        对可达服务器应返回 success=True, reachable=True。

        mock 策略：
          - mock urllib.request.build_opener，使其返回的 opener.open 产生 200 响应。
        """
        self.registry.get_provider.return_value = {
            "id": "test",
            "base_url": "https://example.com/v1",
            "enabled": True,
        }

        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = self.collector.check_provider_connectivity("test")

        self.assertTrue(result["success"])
        self.assertTrue(result["reachable"])
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["provider_id"], "test")

    @patch("urllib.request.build_opener")
    def test_check_provider_connectivity_uses_user_agent_without_auth_headers(self, mock_build_opener):
        self.registry.get_provider.return_value = {
            "id": "test",
            "base_url": "https://example.com/v1",
            "enabled": True,
            "user_agent": "CustomHealthUA/1.0",
            "headers": {
                "Authorization": "Bearer secret",
                "x-api-key": "test-secret-header",
                "X-Trace-Id": "trace-123",
                "User-Agent": "HeaderUA/1.0",
            },
        }

        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = self.collector.check_provider_connectivity("test")

        self.assertTrue(result["success"])
        req = mock_opener.open.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"), "CustomHealthUA/1.0")
        self.assertEqual(req.get_header("X-trace-id"), "trace-123")
        self.assertIsNone(req.get_header("Authorization"))
        self.assertIsNone(req.get_header("X-api-key"))

    @patch("urllib.request.build_opener")
    def test_check_provider_connectivity_auth_error_considered_reachable(self, mock_build_opener):
        """
        401/403 等认证错误应视为「网络可达」，只是权限问题。

        边界条件：
          - 某些 provider（如 OpenAI）对 HEAD /models 返回 401，
            但网络路径是通的，不应标记为 unreachable。
        """
        self.registry.get_provider.return_value = {
            "id": "test",
            "base_url": "https://example.com/v1",
            "enabled": True,
        }

        # 构造 HTTPError：需要符合 urllib.error.HTTPError 签名
        from urllib.error import HTTPError
        from io import BytesIO

        def side_effect(*args, **kwargs):
            raise HTTPError(
                url="https://example.com/v1/models",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=BytesIO(b"{}"),
            )

        mock_opener = MagicMock()
        mock_opener.open.side_effect = side_effect
        mock_build_opener.return_value = mock_opener

        result = self.collector.check_provider_connectivity("test")

        self.assertTrue(result["success"])
        self.assertTrue(result["reachable"])
        self.assertEqual(result["status_code"], 401)
        self.assertIn("network path is open", result.get("note", ""))

    @patch("urllib.request.build_opener")
    def test_check_provider_connectivity_unreachable(self, mock_build_opener):
        """
        对完全不可达的服务器应返回 success=False, reachable=False。

        mock 策略：
          - 模拟 DNS 失败或连接拒绝（URLError）。
        """
        self.registry.get_provider.return_value = {
            "id": "test",
            "base_url": "https://invalid-domain-12345.test/v1",
            "enabled": True,
        }

        from urllib.error import URLError

        def side_effect(*args, **kwargs):
            raise URLError("Name or service not known")

        mock_opener = MagicMock()
        mock_opener.open.side_effect = side_effect
        mock_build_opener.return_value = mock_opener

        result = self.collector.check_provider_connectivity("test")

        self.assertFalse(result["success"])
        self.assertFalse(result["reachable"])
        self.assertIn("Could not connect", result["error"])

    def test_check_provider_connectivity_provider_not_found(self):
        """
        对不存在的 provider_id 应返回明确错误，不触发网络请求。
        """
        self.registry.get_provider.return_value = None
        result = self.collector.check_provider_connectivity("missing")

        self.assertFalse(result["success"])
        self.assertFalse(result["reachable"])
        self.assertIn("not found", result["error"].lower())

    def test_check_provider_connectivity_no_base_url(self):
        """
        base_url 为空时应直接返回错误，不触发网络请求。
        """
        self.registry.get_provider.return_value = {
            "id": "test",
            "base_url": "",
            "enabled": True,
        }
        result = self.collector.check_provider_connectivity("test")

        self.assertFalse(result["success"])
        self.assertFalse(result["reachable"])
        self.assertIn("no base_url", result["error"].lower())

    @patch("diagnostics.CodexConfigManager")
    def test_record_error_appears_in_diagnostics(self, MockMgr):
        """
        通过 record_error 记录的错误应出现在 collect_all 的 errors section 中。
        """
        self._mock_codex_manager(MockMgr)
        self.collector.record_error("test.module", "Something went wrong")
        result = self.collector.collect_all()

        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["source"], "test.module")
        self.assertEqual(result["errors"][0]["message"], "Something went wrong")

    @patch("diagnostics.CodexConfigManager")
    def test_errors_ring_buffer_drops_older_than_50(self, MockMgr):
        """
        错误环型缓冲区应只保留最近 50 条。
        """
        self._mock_codex_manager(MockMgr)
        for i in range(55):
            self.collector.record_error("test", f"error-{i}")

        result = self.collector.collect_all()
        # 只保留最近 10 条在 errors section 中展示
        self.assertEqual(len(result["errors"]), 10)
        # 但内部缓冲区应保留 50 条
        self.assertEqual(len(self.collector._recent_errors), 50)


if __name__ == "__main__":
    unittest.main()
