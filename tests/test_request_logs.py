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

    def test_build_proxy_log_entry_estimates_media_pricing(self):
        provider = {
            "id": "media",
            "short_alias": "img",
            "native_currency": "USD",
            "pricing": {
                "per_image": 0.02,
                "per_video_job": 0.10,
                "per_video_second": 0.03,
            },
        }

        entry = build_proxy_log_entry(
            {
                "provider": provider,
                "endpoint": "images/generations",
                "model": "gpt-image-1",
                "media_kind": "image",
            },
            usage={"image_count": 2, "video_job_count": 1, "video_seconds": 3},
            currency_settings={"display_currency": "USD"},
        )

        self.assertEqual(entry["usage"]["image_count"], 2)
        self.assertEqual(entry["usage"]["video_job_count"], 1)
        self.assertEqual(entry["usage"]["video_seconds"], 3)
        self.assertAlmostEqual(entry["cost_estimate"]["components_native"]["images"], 0.04)
        self.assertAlmostEqual(entry["cost_estimate"]["components_native"]["video_jobs"], 0.10)
        self.assertAlmostEqual(entry["cost_estimate"]["components_native"]["video_seconds"], 0.09)
        self.assertAlmostEqual(entry["cost_estimate"]["total_native"], 0.23)

    def test_build_proxy_log_entry_extracts_provider_reported_cost(self):
        provider = {
            "id": "reported",
            "short_alias": "rp",
            "native_currency": "USD",
            "pricing": {"input_per_million": 1.0},
        }

        entry = build_proxy_log_entry(
            {
                "provider": provider,
                "endpoint": "responses",
                "model": "rp/gpt-5",
                "upstream_model": "gpt-5",
            },
            response_json={
                "usage": {
                    "input_tokens": 100_000,
                    "total_cost": 0.08,
                    "currency": "USD",
                },
                "debug": {"api_key": "test-secret-should-not-appear"},
            },
            currency_settings={"display_currency": "USD"},
        )

        self.assertAlmostEqual(entry["cost_estimate"]["total_native"], 0.1)
        self.assertEqual(entry["provider_reported_cost"], {
            "amount": 0.08,
            "currency": "USD",
            "source": "usage.total_cost",
            "currency_inferred": False,
        })
        self.assertEqual(entry["effective_cost"], {
            "amount": 0.08,
            "currency": "USD",
            "source": "usage.total_cost",
            "estimated": False,
        })
        self.assertNotIn("test-secret-should-not-appear", json.dumps(entry))

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
                "error_message": "Bearer test-secret-should-redact",
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
            self.assertNotIn("test-secret-should-redact", path.read_text(encoding="utf-8"))

    def test_read_entries_filters_media_kind_and_error_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append({
                "endpoint": "images/generations",
                "provider_id": "image",
                "status_code": 200,
                "success": True,
                "media_kind": "image",
            })
            store.append({
                "endpoint": "images/generations",
                "provider_id": "native-proxy",
                "status_code": 400,
                "success": False,
                "media_kind": "image",
                "error_type": "media_adapter_required",
            })
            store.append({
                "endpoint": "videos",
                "provider_id": "video",
                "status_code": 502,
                "success": False,
                "media_kind": "video",
                "error_type": "upstream_error",
            })

            image_entries = store.read_entries(media_kind="image")["entries"]
            adapter_errors = store.read_entries(media_kind="image", error_type="media_adapter_required")["entries"]
            video_errors = store.read_entries(media_kind="video", success=False)["entries"]

            self.assertEqual(len(image_entries), 2)
            self.assertEqual(len(adapter_errors), 1)
            self.assertEqual(adapter_errors[0]["provider_id"], "native-proxy")
            self.assertEqual(len(video_errors), 1)
            self.assertEqual(video_errors[0]["error_type"], "upstream_error")

    def test_summary_accumulates_media_usage_and_fx_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append({
                "endpoint": "images/generations",
                "provider_id": "image",
                "status_code": 200,
                "success": True,
                "usage": {"image_count": 2},
                "cost_estimate": {"total_native": 0.04, "native_currency": "USD"},
                "fx_snapshot": {"success": True, "source": "manual", "is_stale": True, "fallback_used": True},
            })
            store.append({
                "endpoint": "videos",
                "provider_id": "video",
                "status_code": 200,
                "success": True,
                "usage": {"video_job_count": 1, "video_seconds": 8},
                "cost_estimate": {"total_native": 0.12, "native_currency": "USD"},
                "fx_snapshot": {"success": False, "source": "apiforex"},
            })
            store.append({
                "timestamp": "2026-06-07T12:00:00Z",
                "endpoint": "images/generations",
                "provider_id": "native-proxy",
                "status_code": 400,
                "success": False,
                "media_kind": "image",
                "error_type": "media_adapter_required",
            })

            summary = store.summary()

            self.assertEqual(summary["tokens"]["image_count"], 2)
            self.assertEqual(summary["tokens"]["video_job_count"], 1)
            self.assertEqual(summary["tokens"]["video_seconds"], 8)
            self.assertEqual(summary["cost_native_by_currency"], {"USD": 0.16})
            self.assertEqual(summary["fx"]["snapshots"], 2)
            self.assertEqual(summary["fx"]["stale_count"], 1)
            self.assertEqual(summary["fx"]["fallback_count"], 1)
            self.assertEqual(summary["fx"]["unavailable_count"], 1)
            self.assertEqual(summary["fx"]["sources"], {"manual": 1, "apiforex": 1})
            self.assertEqual(summary["media"]["count"], 3)
            self.assertEqual(summary["media"]["success_count"], 2)
            self.assertEqual(summary["media"]["error_count"], 1)
            self.assertEqual(summary["media"]["by_kind"]["image"], {"count": 2, "success_count": 1, "error_count": 1})
            self.assertEqual(summary["media"]["by_kind"]["video"], {"count": 1, "success_count": 1, "error_count": 0})
            self.assertEqual(summary["media"]["providers"]["native-proxy"], 1)
            self.assertEqual(summary["media"]["endpoints"]["images/generations"], 2)
            self.assertEqual(summary["media"]["error_types"], {"media_adapter_required": 1})
            self.assertEqual(summary["media"]["latest_error"]["provider_id"], "native-proxy")
            self.assertEqual(summary["media"]["latest_error"]["media_kind"], "image")

    def test_summary_compares_provider_reported_and_estimated_cost(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append({
                "endpoint": "responses",
                "provider_id": "p1",
                "status_code": 200,
                "success": True,
                "cost_estimate": {"total_native": 0.1, "native_currency": "USD"},
                "provider_reported_cost": {"amount": 0.08, "currency": "USD", "source": "usage.total_cost"},
            })
            store.append({
                "endpoint": "responses",
                "provider_id": "p2",
                "status_code": 200,
                "success": True,
                "cost_estimate": {"total_native": 0.2, "native_currency": "USD"},
            })

            summary = store.summary()

            self.assertEqual(summary["provider_reported_cost_by_currency"], {"USD": 0.08})
            self.assertEqual(summary["effective_cost_by_currency"], {"USD": 0.28})
            self.assertEqual(summary["effective_cost_source_counts"], {
                "provider_reported": 1,
                "local_estimate": 1,
            })
            self.assertEqual(summary["cost_comparison"]["estimated_count"], 2)
            self.assertEqual(summary["cost_comparison"]["reported_count"], 1)
            self.assertEqual(summary["cost_comparison"]["estimated_only_count"], 1)
            self.assertEqual(summary["cost_comparison"]["matched_currency_count"], 1)
            self.assertAlmostEqual(
                summary["cost_comparison"]["estimated_minus_reported_by_currency"]["USD"],
                0.02,
            )

    def test_summary_filters_costs_by_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "proxy_requests.jsonl"
            store = RequestLogStore(path, retention_days=30, max_mb=1)
            store.append({
                "endpoint": "responses",
                "provider_id": "p1",
                "status_code": 200,
                "success": True,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "cost_estimate": {"total_native": 0.1, "native_currency": "USD"},
            })
            store.append({
                "endpoint": "responses",
                "provider_id": "p2",
                "status_code": 200,
                "success": True,
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "cost_estimate": {"total_native": 0.2, "native_currency": "USD"},
            })

            summary = store.summary(provider_id="p1")

            self.assertEqual(summary["filter"]["provider_id"], "p1")
            self.assertEqual(summary["count"], 1)
            self.assertEqual(summary["providers"], {"p1": 1})
            self.assertEqual(summary["tokens"]["input_tokens"], 10)
            self.assertEqual(summary["effective_cost_by_currency"], {"USD": 0.1})

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
