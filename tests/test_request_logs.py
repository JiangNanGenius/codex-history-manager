import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from request_logs import (
    RequestLogStore,
    build_proxy_log_entry,
    normalize_usage,
)


class RequestLogStoreTest(unittest.TestCase):
    def test_build_proxy_log_entry_normalizes_usage_and_cost_snapshot(self):
        provider = {
            "id": "p1",
            "short_alias": "openai",
            "api_key": "sk-secret",
            "native_currency": "USD",
            "pricing": {
                "input_per_million": 1.0,
                "output_per_million": 2.0,
                "cache_read_per_million": 0.1,
            },
        }
        entry = build_proxy_log_entry(
            {
                "provider": provider,
                "endpoint": "chat_completions",
                "model": "openai/gpt-5",
                "upstream_model": "gpt-5",
            },
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "prompt_tokens_details": {"cached_tokens": 200},
            },
            currency_settings={
                "display_currency": "CNY",
                "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
            },
        )

        self.assertEqual(entry["provider_id"], "p1")
        self.assertEqual(entry["usage"]["input_tokens"], 1000)
        self.assertEqual(entry["usage"]["cache_read_tokens"], 200)
        self.assertEqual(entry["cost_estimate"]["native_currency"], "USD")
        self.assertEqual(entry["cost_estimate"]["display_currency"], "CNY")
        self.assertTrue(entry["cost_estimate"]["fx_snapshot"]["success"])
        self.assertNotIn("sk-secret", json.dumps(entry))

    def test_log_entry_preserves_historical_fx_snapshot(self):
        provider = {
            "id": "p1",
            "short_alias": "openai",
            "native_currency": "USD",
            "pricing": {"input_per_million": 1.0},
        }
        first_entry = build_proxy_log_entry(
            {
                "provider": provider,
                "endpoint": "responses",
                "model": "openai/gpt-5",
                "upstream_model": "gpt-5",
            },
            usage={"input_tokens": 1_000_000},
            currency_settings={
                "display_currency": "CNY",
                "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
            },
        )
        later_entry = build_proxy_log_entry(
            {
                "provider": provider,
                "endpoint": "responses",
                "model": "openai/gpt-5",
                "upstream_model": "gpt-5",
            },
            usage={"input_tokens": 1_000_000},
            currency_settings={
                "display_currency": "CNY",
                "exchange_rate_manual_overrides": {"USD:CNY": 7.8},
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append(first_entry)
            store.append(later_entry)
            entries = store.read_entries(limit=10)["entries"]

        older = entries[1]
        newer = entries[0]
        self.assertEqual(older["fx_snapshot"]["rate"], 7.2)
        self.assertEqual(newer["fx_snapshot"]["rate"], 7.8)
        self.assertEqual(older["cost_estimate"]["total_display"], 7.2)
        self.assertEqual(newer["cost_estimate"]["total_display"], 7.8)

    def test_store_summary_and_filters_are_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append({
                "endpoint": "chat_completions",
                "provider_id": "p1",
                "model": "gpt-5",
                "status_code": 200,
                "success": True,
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 3},
                "error_message": "Bearer sk-should-redact",
            })
            store.append({
                "endpoint": "responses",
                "provider_id": "p2",
                "status_code": 502,
                "success": False,
                "error_type": "upstream_error",
            })

            summary = store.summary()
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["error_count"], 1)
            self.assertEqual(summary["tokens"]["cache_read_tokens"], 3)

            p1_entries = store.read_entries(provider_id="p1")["entries"]
            self.assertEqual(len(p1_entries), 1)
            self.assertNotIn("sk-should-redact", path.read_text(encoding="utf-8"))

    def test_enforce_retention_by_age_and_size(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            now = datetime(2026, 6, 7, tzinfo=timezone.utc)
            old_entry = {
                "timestamp": "2026-05-01T00:00:00Z",
                "endpoint": "chat_completions",
                "provider_id": "old",
            }
            new_entry = {
                "timestamp": "2026-06-07T00:00:00Z",
                "endpoint": "chat_completions",
                "provider_id": "new",
                "error_message": "x" * 400,
            }
            path.write_text(
                json.dumps(old_entry) + "\n" + json.dumps(new_entry) + "\n",
                encoding="utf-8",
            )
            store = RequestLogStore(path, retention_days=7, max_mb=1)
            result = store.enforce_retention(now=now)
            self.assertEqual(result["removed_entries"], 1)
            entries = store.read_entries(limit=10)["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["provider_id"], "new")

    def test_normalize_usage_handles_common_cache_aliases(self):
        usage = normalize_usage({
            "input_tokens": 12,
            "output_tokens": 3,
            "input_tokens_details": {"cached_tokens": 4},
            "cache_creation_input_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 1},
        })
        self.assertEqual(usage["cache_read_tokens"], 4)
        self.assertEqual(usage["cache_creation_tokens"], 2)
        self.assertEqual(usage["cache_total_tokens"], 6)
        self.assertEqual(usage["reasoning_tokens"], 1)
