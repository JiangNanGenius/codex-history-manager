"""
Local request cost estimation.

All calculations here are estimates unless a provider-reported invoice-grade
cost is supplied elsewhere. The estimator keeps native currency and display
currency separate and returns the exact FX snapshot used for conversion.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from currency import build_rate_snapshot, normalize_currency_code


TOKEN_UNIT = 1_000_000


PRICE_ALIASES = {
    "input_per_million": ("input_per_million", "input_tokens_per_million", "input", "prompt_per_million"),
    "output_per_million": ("output_per_million", "output_tokens_per_million", "output", "completion_per_million"),
    "cache_read_per_million": ("cache_read_per_million", "cached_input_per_million", "cache_read", "cache_hit_per_million"),
    "cache_write_per_million": ("cache_write_per_million", "cache_creation_per_million", "cache_write", "cache_creation"),
    "reasoning_per_million": ("reasoning_per_million", "reasoning_tokens_per_million", "reasoning"),
    "per_image": ("per_image", "image", "image_per_unit"),
    "per_video_job": ("per_video_job", "video_job", "video_per_job"),
    "per_video_second": ("per_video_second", "video_second", "video_per_second"),
    "request_minimum": ("request_minimum", "minimum", "minimum_charge"),
    "provider_cost_multiplier": ("provider_cost_multiplier", "multiplier", "cost_multiplier"),
    "tax_percent": ("tax_percent", "tax", "vat_percent", "gst_percent"),
    "discount_percent": ("discount_percent", "discount"),
}


def estimate_request_cost(
    usage: Dict[str, Any],
    pricing: Dict[str, Any],
    currency_settings: Dict[str, Any],
    native_currency: str = "",
    display_currency: str = "",
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Estimate a single request cost with token/cache/media breakdown."""
    usage = usage or {}
    pricing = pricing or {}
    native = normalize_currency_code(
        native_currency or pricing.get("native_currency") or usage.get("native_currency") or "USD"
    )
    display = normalize_currency_code(display_currency or currency_settings.get("display_currency") or native)

    input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
    cache_read_tokens = _int(usage.get("cache_read_tokens") or usage.get("cached_input_tokens"))
    cache_write_tokens = _int(usage.get("cache_creation_tokens") or usage.get("cache_write_tokens"))
    reasoning_tokens = _int(usage.get("reasoning_tokens"))

    input_includes_cache_read = bool(pricing.get("input_includes_cache_read", usage.get("input_includes_cache_read", True)))
    billable_input_tokens = input_tokens
    if input_includes_cache_read:
        billable_input_tokens = max(input_tokens - cache_read_tokens, 0)

    components = {
        "input": _token_cost(billable_input_tokens, _price(pricing, "input_per_million")),
        "output": _token_cost(output_tokens, _price(pricing, "output_per_million")),
        "cache_read": _token_cost(cache_read_tokens, _price(pricing, "cache_read_per_million")),
        "cache_write": _token_cost(cache_write_tokens, _price(pricing, "cache_write_per_million")),
        "reasoning": _token_cost(reasoning_tokens, _price(pricing, "reasoning_per_million")),
        "images": _int(usage.get("image_count") or usage.get("images")) * _price(pricing, "per_image"),
        "video_jobs": _int(usage.get("video_job_count") or usage.get("video_count") or usage.get("videos")) * _price(pricing, "per_video_job"),
        "video_seconds": _float(usage.get("video_seconds")) * _price(pricing, "per_video_second"),
    }
    components = {key: round(value, 12) for key, value in components.items() if value}
    subtotal_native = round(sum(components.values()), 12)

    request_minimum = _price(pricing, "request_minimum")
    minimum_adjustment = round(max(request_minimum - subtotal_native, 0.0), 12)
    after_minimum = subtotal_native + minimum_adjustment

    multiplier = _price(pricing, "provider_cost_multiplier") or 1.0
    after_multiplier = round(after_minimum * multiplier, 12)

    discount_percent = _price(pricing, "discount_percent")
    discount_amount = round(after_multiplier * max(discount_percent, 0.0) / 100.0, 12)
    after_discount = max(after_multiplier - discount_amount, 0.0)

    tax_percent = _price(pricing, "tax_percent")
    tax_amount = round(after_discount * max(tax_percent, 0.0) / 100.0, 12)
    total_native = round(after_discount + tax_amount, 12)

    fx_snapshot = build_rate_snapshot(currency_settings, native, display, now=now)
    total_display = None
    components_display: Dict[str, float] = {}
    if fx_snapshot.get("success"):
        rate = _float(fx_snapshot.get("rate"))
        total_display = round(total_native * rate, 12)
        components_display = {key: round(value * rate, 12) for key, value in components.items()}

    return {
        "estimate": True,
        "native_currency": native,
        "display_currency": display,
        "usage": {
            "input_tokens": input_tokens,
            "billable_input_tokens": billable_input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "reasoning_tokens": reasoning_tokens,
            "input_includes_cache_read": input_includes_cache_read,
        },
        "components_native": components,
        "subtotal_native": subtotal_native,
        "minimum_adjustment_native": minimum_adjustment,
        "multiplier": multiplier,
        "discount_amount_native": discount_amount,
        "tax_amount_native": tax_amount,
        "total_native": total_native,
        "fx_snapshot": fx_snapshot,
        "components_display": components_display,
        "total_display": total_display,
        "warnings": _warnings(pricing, fx_snapshot),
    }


def pricing_preview_payload(provider: Dict[str, Any], model_id: str = "") -> Dict[str, Any]:
    """Return provider/model pricing metadata for UI previews."""
    provider = provider or {}
    model = _find_model(provider, model_id)
    pricing: Dict[str, Any] = {}
    if isinstance(provider.get("pricing"), dict):
        pricing.update(provider["pricing"])
    if model and isinstance(model.get("pricing"), dict):
        pricing.update(model["pricing"])
    native_currency = normalize_currency_code(
        (model or {}).get("native_currency") or provider.get("native_currency") or pricing.get("native_currency") or "USD"
    )
    return {
        "provider_id": provider.get("id") or "",
        "model_id": (model or {}).get("id") or model_id,
        "native_currency": native_currency,
        "pricing": pricing,
        "has_model_pricing": bool(model and isinstance(model.get("pricing"), dict) and model.get("pricing")),
    }


def _find_model(provider: Dict[str, Any], model_id: str) -> Optional[Dict[str, Any]]:
    model_id = str(model_id or "").strip()
    if "/" in model_id:
        _, model_id = model_id.split("/", 1)
    if not model_id:
        return None
    for model in provider.get("models", []):
        if isinstance(model, dict) and str(model.get("id") or "") == model_id:
            return model
    return None


def _price(pricing: Dict[str, Any], canonical: str) -> float:
    for key in PRICE_ALIASES[canonical]:
        if key in pricing:
            return _float(pricing.get(key))
    return 0.0


def _token_cost(tokens: int, per_million: float) -> float:
    if tokens <= 0 or per_million <= 0:
        return 0.0
    return (tokens / TOKEN_UNIT) * per_million


def _warnings(pricing: Dict[str, Any], fx_snapshot: Dict[str, Any]) -> list[str]:
    warnings = ["Local cost is an estimate unless provider-reported invoice-grade cost is available."]
    if not pricing:
        warnings.append("No pricing table was supplied; total will be zero until pricing is configured.")
    if not fx_snapshot.get("success"):
        warnings.append(str(fx_snapshot.get("error") or "FX conversion failed."))
    warnings.extend(str(item) for item in fx_snapshot.get("warnings", []))
    return warnings


def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
