from datetime import datetime, timezone
import unittest

from costing import estimate_request_cost, pricing_preview_payload


class CostingTest(unittest.TestCase):
    def test_openai_style_cache_inclusive_input_cost(self):
        result = estimate_request_cost(
            usage={
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_tokens": 200,
                "cache_creation_tokens": 100,
            },
            pricing={
                "native_currency": "USD",
                "input_per_million": 2.0,
                "output_per_million": 8.0,
                "cache_read_per_million": 0.2,
                "cache_write_per_million": 3.0,
                "input_includes_cache_read": True,
            },
            currency_settings={"display_currency": "USD"},
            now=datetime(2026, 6, 7, tzinfo=timezone.utc),
        )

        self.assertTrue(result["estimate"])
        self.assertEqual(result["usage"]["billable_input_tokens"], 800)
        self.assertAlmostEqual(result["components_native"]["input"], 0.0016)
        self.assertAlmostEqual(result["components_native"]["output"], 0.004)
        self.assertAlmostEqual(result["components_native"]["cache_read"], 0.00004)
        self.assertAlmostEqual(result["components_native"]["cache_write"], 0.0003)
        self.assertAlmostEqual(result["total_native"], 0.00594)

    def test_anthropic_style_cache_exclusive_input_cost(self):
        result = estimate_request_cost(
            usage={
                "input_tokens": 1000,
                "cache_read_tokens": 200,
                "input_includes_cache_read": False,
            },
            pricing={
                "input_per_million": 3.0,
                "cache_read_per_million": 0.3,
                "input_includes_cache_read": False,
            },
            currency_settings={"display_currency": "USD"},
        )

        self.assertEqual(result["usage"]["billable_input_tokens"], 1000)
        self.assertAlmostEqual(result["components_native"]["input"], 0.003)
        self.assertAlmostEqual(result["components_native"]["cache_read"], 0.00006)

    def test_manual_fx_override_converts_total(self):
        result = estimate_request_cost(
            usage={"input_tokens": 1_000_000},
            pricing={"native_currency": "USD", "input_per_million": 1.0},
            currency_settings={
                "display_currency": "CNY",
                "exchange_rate_manual_overrides": {"USD:CNY": 7.2},
            },
        )

        self.assertEqual(result["native_currency"], "USD")
        self.assertEqual(result["display_currency"], "CNY")
        self.assertAlmostEqual(result["total_native"], 1.0)
        self.assertAlmostEqual(result["total_display"], 7.2)
        self.assertEqual(result["fx_snapshot"]["source"], "manual")

    def test_media_minimum_multiplier_discount_and_tax(self):
        result = estimate_request_cost(
            usage={"image_count": 1, "video_seconds": 4, "video_job_count": 1},
            pricing={
                "native_currency": "USD",
                "per_image": 0.01,
                "per_video_job": 0.20,
                "per_video_second": 0.05,
                "request_minimum": 1.0,
                "provider_cost_multiplier": 2.0,
                "discount_percent": 10.0,
                "tax_percent": 5.0,
            },
            currency_settings={"display_currency": "USD"},
        )

        self.assertAlmostEqual(result["subtotal_native"], 0.41)
        self.assertAlmostEqual(result["minimum_adjustment_native"], 0.59)
        self.assertAlmostEqual(result["discount_amount_native"], 0.2)
        self.assertAlmostEqual(result["tax_amount_native"], 0.09)
        self.assertAlmostEqual(result["total_native"], 1.89)

    def test_pricing_preview_merges_model_pricing_over_provider(self):
        provider = {
            "id": "p",
            "native_currency": "USD",
            "pricing": {"input_per_million": 1.0, "output_per_million": 2.0},
            "models": [
                {
                    "id": "m",
                    "native_currency": "CNY",
                    "pricing": {"input_per_million": 0.5},
                }
            ],
        }

        preview = pricing_preview_payload(provider, model_id="p/m")

        self.assertEqual(preview["native_currency"], "CNY")
        self.assertEqual(preview["pricing"]["input_per_million"], 0.5)
        self.assertEqual(preview["pricing"]["output_per_million"], 2.0)
        self.assertTrue(preview["has_model_pricing"])


if __name__ == "__main__":
    unittest.main()
