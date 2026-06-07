import io
import json
import unittest
from unittest.mock import patch

from quota import (
    QuotaManager,
    build_quota_headers,
    extract_json_path,
    normalize_quota_check,
    redact_quota_result,
    refresh_provider_quota_preview,
)


class FakeResponse:
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


class QuotaTest(unittest.TestCase):
    def test_extract_json_path_supports_dot_and_array_segments(self):
        payload = {"data": {"items": [{"balance": 12.5}]}}

        self.assertEqual(extract_json_path(payload, "$.data.items[0].balance"), 12.5)
        self.assertIsNone(extract_json_path(payload, "$.data.items[9].balance"))

    def test_normalize_quota_check_defaults_and_paths(self):
        normalized = normalize_quota_check({
            "enabled": True,
            "method": "PATCH",
            "endpoint": "https://example.test/quota",
            "json_paths": {"balance": "$.balance", "": "$.ignored"},
            "ttl_seconds": "60",
        })

        self.assertTrue(normalized["enabled"])
        self.assertEqual(normalized["method"], "GET")
        self.assertEqual(normalized["url"], "https://example.test/quota")
        self.assertEqual(normalized["json_paths"], {"balance": "$.balance"})
        self.assertEqual(normalized["ttl_seconds"], 60)

    def test_build_quota_headers_uses_user_agent_and_secondary_key(self):
        headers = build_quota_headers(
            {
                "api_key": "primary",
                "secondary_usage_key": "usage",
                "user_agent": "QuotaUA/1.0",
                "headers": {"X-Custom": "yes", "Authorization": "Bearer ignored"},
            },
            {"headers": {"X-Quota": "1"}},
        )

        self.assertEqual(headers["User-Agent"], "QuotaUA/1.0")
        self.assertEqual(headers["Authorization"], "Bearer usage")
        self.assertEqual(headers["X-Custom"], "yes")
        self.assertEqual(headers["X-Quota"], "1")

    @patch("quota.urllib.request.urlopen")
    def test_refresh_provider_quota_extracts_values_and_caches(self, mock_urlopen):
        provider = {
            "id": "p1",
            "api_key": "secret",
            "user_agent": "QuotaUA/1.0",
            "quota_check": {
                "enabled": True,
                "url": "https://example.test/quota",
                "json_paths": {
                    "balance": "$.data.balance",
                    "limit": "$.data.limit",
                },
                "ttl_seconds": 300,
            },
        }
        mock_urlopen.return_value = FakeResponse({"data": {"balance": 3.5, "limit": 10}, "api_key": "secret"})
        manager = QuotaManager(lambda: [provider])

        first = manager.refresh_provider_quota("p1")
        second = manager.refresh_provider_quota("p1", force=False)

        self.assertTrue(first["success"])
        self.assertFalse(first["cache_hit"])
        self.assertIn("cache_expires_at", first)
        self.assertIn("cache_ttl_remaining_seconds", first)
        self.assertEqual(first["values"], {"balance": 3.5, "limit": 10})
        self.assertEqual(first["raw_redacted"]["api_key"], "********")
        self.assertTrue(second["cache_hit"])
        self.assertFalse(second["cache_expired"])
        self.assertEqual(second["cache_expires_at"], first["cache_expires_at"])
        self.assertEqual(mock_urlopen.call_count, 1)

        cached = manager.cached_provider_quota("p1")
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["cache_expires_at"], first["cache_expires_at"])

    @patch("quota.urllib.request.urlopen")
    def test_failed_quota_probe_returns_snapshot(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("network down")
        manager = QuotaManager(lambda: [{
            "id": "p1",
            "quota_check": {
                "enabled": True,
                "url": "https://example.test/quota",
            },
        }])

        result = manager.refresh_provider_quota("p1")

        self.assertFalse(result["success"])
        self.assertIn("network down", result["error"])
        self.assertIn("fetched_at", result)

    @patch("quota.urllib.request.urlopen")
    def test_refresh_provider_quota_preview_uses_draft_without_cache_hit(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse({"balance": 12, "token": "secret"})

        result = refresh_provider_quota_preview({
            "id": "draft",
            "api_key": "secret",
            "quota_check": {
                "enabled": True,
                "url": "https://example.test/draft-quota",
                "json_paths": {"balance": "$.balance"},
            },
        })

        self.assertTrue(result["success"])
        self.assertTrue(result["preview"])
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["values"], {"balance": 12})
        self.assertEqual(result["raw_redacted"]["token"], "********")

    def test_redact_quota_result_redacts_nested_secrets(self):
        redacted = redact_quota_result({
            "data": [{"token": "secret", "balance": 1}],
            "normal": "value",
        })

        self.assertEqual(redacted["data"][0]["token"], "********")
        self.assertEqual(redacted["data"][0]["balance"], 1)


if __name__ == "__main__":
    unittest.main()
