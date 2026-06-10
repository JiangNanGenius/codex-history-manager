from datetime import datetime, timezone
import unittest

from currency import (
    REDACTED_VALUE,
    build_rate_snapshot,
    convert_amount,
    exchange_rate_status_summary,
    preserve_redacted_currency_secret,
    redact_currency_settings,
    update_currency_config,
)


class CurrencyTest(unittest.TestCase):
    def test_manual_override_snapshot_and_conversion(self):
        settings = {
            "exchange_rate_source": "manual",
            "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
        }
        now = datetime(2026, 6, 7, tzinfo=timezone.utc)

        snapshot = build_rate_snapshot(settings, "usd", "cny", now=now)
        converted = convert_amount(settings, 2, "USD", "CNY", now=now)

        self.assertTrue(snapshot["success"])
        self.assertEqual(snapshot["from_currency"], "USD")
        self.assertEqual(snapshot["to_currency"], "CNY")
        self.assertEqual(snapshot["rate"], 7.2)
        self.assertEqual(snapshot["source"], "manual")
        self.assertTrue(snapshot["is_manual"])
        self.assertTrue(converted["success"])
        self.assertEqual(converted["converted_amount"], 14.4)
        self.assertEqual(converted["rate_snapshot"]["rate"], 7.2)

    def test_manual_inverse_rate(self):
        settings = {
            "exchange_rate_source": "manual",
            "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
        }

        snapshot = build_rate_snapshot(settings, "CNY", "USD", now=datetime(2026, 6, 7, tzinfo=timezone.utc))

        self.assertTrue(snapshot["success"])
        self.assertAlmostEqual(snapshot["rate"], 1 / 7.2)
        self.assertEqual(snapshot["source"], "manual_inverse")

    def test_currency_secret_redaction_and_preservation(self):
        settings = {
            "display_currency": "CNY",
            "exchange_rate_api_key": "secret-key",
        }

        redacted = redact_currency_settings(settings)
        preserved = preserve_redacted_currency_secret({"exchange_rate_api_key": REDACTED_VALUE}, settings)

        self.assertEqual(redacted["exchange_rate_api_key"], REDACTED_VALUE)
        self.assertEqual(preserved["exchange_rate_api_key"], "secret-key")

    def test_update_currency_config_sanitizes_overrides_and_key(self):
        current = {"exchange_rate_api_key": "secret-key"}

        update = update_currency_config(current, {
            "display_currency": "cny",
            "exchange_rate_source": "manual",
            "exchange_rate_api_key": REDACTED_VALUE,
            "exchange_rate_manual_overrides": {
                "usd/cny": "7.2",
                "bad": "9",
                "EUR:CNY": "-1",
            },
            "exchange_rate_ttl_hours": "12",
        })

        self.assertEqual(update["display_currency"], "CNY")
        self.assertEqual(update["exchange_rate_api_key"], "secret-key")
        self.assertEqual(update["exchange_rate_manual_overrides"], {"USD:CNY": 7.2})
        self.assertEqual(update["exchange_rate_ttl_hours"], 12)

    def test_apiforex_source_with_key_attempts_fetch(self):
        settings = {
            "exchange_rate_source": "apiforex",
            "exchange_rate_api_key": "invalid-key-for-unit-test",
            "exchange_rate_manual_overrides": {},
        }

        result = build_rate_snapshot(settings, "USD", "AUD")

        self.assertFalse(result["success"])
        # With an invalid key, apiforex fetch should fail gracefully
        self.assertIn("apiforex", result.get("error", "").lower())

    def test_stale_cache_rate_is_used_as_explicit_fallback(self):
        settings = {
            "display_currency": "CNY",
            "exchange_rate_source": "manual",
            "exchange_rate_cache": {
                "USD:CNY": {
                    "rate": 7.05,
                    "source": "apiforex",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2026-01-02T00:00:00Z",
                }
            },
        }

        snapshot = build_rate_snapshot(settings, "USD", "CNY", now=datetime(2026, 6, 7, tzinfo=timezone.utc))

        self.assertTrue(snapshot["success"])
        self.assertEqual(snapshot["rate"], 7.05)
        self.assertTrue(snapshot["is_stale"])
        self.assertTrue(snapshot["fallback_used"])
        self.assertEqual(snapshot["fallback_reason"], "stale_cache")
        self.assertIn("stale", " ".join(snapshot["warnings"]).lower())

    def test_exchange_rate_status_summary_is_redaction_safe(self):
        settings = {
            "display_currency": "aud",
            "exchange_rate_source": "apiforex",
            "exchange_rate_api_key": "secret-key",
            "exchange_rate_manual_overrides": {"usd/aud": "1.52"},
            "exchange_rate_cache": {
                "CNY:AUD": {
                    "rate": 0.21,
                    "source": "cache",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2026-01-02T00:00:00Z",
                }
            },
        }

        summary = exchange_rate_status_summary(settings, now=datetime(2026, 6, 7, tzinfo=timezone.utc))

        self.assertEqual(summary["display_currency"], "AUD")
        self.assertEqual(summary["status"], "ready")
        self.assertTrue(summary["api_key_configured"])
        self.assertTrue(summary["online_fetch_enabled"])
        self.assertEqual(summary["manual_pairs"], ["USD:AUD"])
        self.assertEqual(summary["stale_cache_pairs"], ["CNY:AUD"])
        self.assertNotIn("secret-key", str(summary))


if __name__ == "__main__":
    unittest.main()
