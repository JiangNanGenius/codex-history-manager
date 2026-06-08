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
NON_ESTIMATED_BILLING_MODES = {
    "token_plan",
    "token_plan_monthly",
    "monthly_plan",
    "subscription",
    "package",
    "credits_plan",
    "usage_unavailable",
    "unmetered",
    "manual",
}
DISABLED_COST_ESTIMATION_VALUES = {"disabled", "off", "none", "unavailable", "manual"}


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
    usage_summary = {
        "input_tokens": input_tokens,
        "billable_input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
        "input_includes_cache_read": bool(pricing.get("input_includes_cache_read", usage.get("input_includes_cache_read", True))),
    }

    non_estimated = _non_estimated_billing_mode(pricing)
    if non_estimated:
        return _non_estimated_result(
            native=native,
            display=display,
            usage_summary=usage_summary,
            pricing=pricing,
            billing_mode=non_estimated,
            currency_settings=currency_settings,
            now=now,
        )

    tier_info = select_pricing_tier(pricing, usage_summary)
    effective_pricing = tier_info.get("pricing") if isinstance(tier_info.get("pricing"), dict) else pricing

    input_includes_cache_read = bool(effective_pricing.get("input_includes_cache_read", usage.get("input_includes_cache_read", True)))
    billable_input_tokens = input_tokens
    if input_includes_cache_read:
        billable_input_tokens = max(input_tokens - cache_read_tokens, 0)
    usage_summary["billable_input_tokens"] = billable_input_tokens
    usage_summary["input_includes_cache_read"] = input_includes_cache_read

    components = {
        "input": _token_cost(billable_input_tokens, _price(effective_pricing, "input_per_million")),
        "output": _token_cost(output_tokens, _price(effective_pricing, "output_per_million")),
        "cache_read": _token_cost(cache_read_tokens, _price(effective_pricing, "cache_read_per_million")),
        "cache_write": _token_cost(cache_write_tokens, _price(effective_pricing, "cache_write_per_million")),
        "reasoning": _token_cost(reasoning_tokens, _price(effective_pricing, "reasoning_per_million")),
        "images": _int(usage.get("image_count") or usage.get("images")) * _price(effective_pricing, "per_image"),
        "video_jobs": _int(usage.get("video_job_count") or usage.get("video_count") or usage.get("videos")) * _price(effective_pricing, "per_video_job"),
        "video_seconds": _float(usage.get("video_seconds")) * _price(effective_pricing, "per_video_second"),
    }
    components = {key: round(value, 12) for key, value in components.items() if value}
    subtotal_native = round(sum(components.values()), 12)

    request_minimum = _price(effective_pricing, "request_minimum")
    minimum_adjustment = round(max(request_minimum - subtotal_native, 0.0), 12)
    after_minimum = subtotal_native + minimum_adjustment

    multiplier = _price(effective_pricing, "provider_cost_multiplier") or 1.0
    after_multiplier = round(after_minimum * multiplier, 12)

    discount_percent = _price(effective_pricing, "discount_percent")
    discount_amount = round(after_multiplier * max(discount_percent, 0.0) / 100.0, 12)
    after_discount = max(after_multiplier - discount_amount, 0.0)

    tax_percent = _price(effective_pricing, "tax_percent")
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
        "usage": usage_summary,
        "selected_pricing_tier": tier_info.get("selected_tier"),
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
        "warnings": _warnings(effective_pricing, fx_snapshot, tier_info),
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


def select_pricing_tier(pricing: Dict[str, Any], usage_summary: Dict[str, Any]) -> Dict[str, Any]:
    tiers = pricing.get("tiered_pricing") or pricing.get("pricing_tiers") or pricing.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        return {"pricing": pricing, "selected_tier": None}
    basis = str(pricing.get("tier_basis") or "input_tokens").strip() or "input_tokens"
    basis_value = _tier_basis_value(basis, usage_summary)
    normalized_tiers = [tier for tier in tiers if isinstance(tier, dict)]
    if not normalized_tiers:
        return {"pricing": pricing, "selected_tier": None}
    selected_index = len(normalized_tiers) - 1
    selected = normalized_tiers[-1]
    for idx, tier in enumerate(normalized_tiers):
        max_tokens = _tier_max_tokens(tier)
        min_tokens = _int(tier.get("min_input_tokens") or tier.get("min_tokens"))
        if min_tokens and basis_value < min_tokens:
            continue
        if max_tokens <= 0 or basis_value <= max_tokens:
            selected_index = idx
            selected = tier
            break

    merged = copy_pricing_without_tiers(pricing)
    for key, value in selected.items():
        if key not in {"min_input_tokens", "min_tokens", "max_input_tokens", "max_tokens", "up_to", "upto", "label", "name"}:
            merged[key] = value
    return {
        "pricing": merged,
        "selected_tier": {
            "index": selected_index,
            "label": selected.get("label") or selected.get("name") or f"tier-{selected_index + 1}",
            "basis": basis,
            "basis_value": basis_value,
            "min_tokens": _int(selected.get("min_input_tokens") or selected.get("min_tokens")),
            "max_tokens": _tier_max_tokens(selected),
            "applies_to": pricing.get("tier_applies_to") or "all_tokens",
        },
    }


def copy_pricing_without_tiers(pricing: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in (pricing or {}).items()
        if key not in {"tiered_pricing", "pricing_tiers", "tiers"}
    }


def _price(pricing: Dict[str, Any], canonical: str) -> float:
    for key in PRICE_ALIASES[canonical]:
        if key in pricing:
            return _float(pricing.get(key))
    return 0.0


def _token_cost(tokens: int, per_million: float) -> float:
    if tokens <= 0 or per_million <= 0:
        return 0.0
    return (tokens / TOKEN_UNIT) * per_million


def _warnings(pricing: Dict[str, Any], fx_snapshot: Dict[str, Any], tier_info: Optional[Dict[str, Any]] = None) -> list[str]:
    warnings = ["Local cost is an estimate unless provider-reported invoice-grade cost is available."]
    if not pricing:
        warnings.append("No pricing table was supplied; total will be zero until pricing is configured.")
    if tier_info and tier_info.get("selected_tier"):
        tier = tier_info["selected_tier"]
        warnings.append(
            "Tiered pricing selected by "
            f"{tier.get('basis')}={tier.get('basis_value')}; all request tokens use the selected tier."
        )
    if not fx_snapshot.get("success"):
        warnings.append(str(fx_snapshot.get("error") or "FX conversion failed."))
    warnings.extend(str(item) for item in fx_snapshot.get("warnings", []))
    return warnings


def _non_estimated_billing_mode(pricing: Dict[str, Any]) -> str:
    mode = str(pricing.get("billing_mode") or pricing.get("usage_metering") or "").strip().lower()
    cost_estimation = str(pricing.get("cost_estimation") or "").strip().lower()
    if pricing.get("estimate") is False:
        return mode or "manual"
    if cost_estimation in DISABLED_COST_ESTIMATION_VALUES:
        return mode or cost_estimation
    if mode in NON_ESTIMATED_BILLING_MODES:
        return mode
    return ""


def _non_estimated_result(
    native: str,
    display: str,
    usage_summary: Dict[str, Any],
    pricing: Dict[str, Any],
    billing_mode: str,
    currency_settings: Dict[str, Any],
    now: Optional[Any],
) -> Dict[str, Any]:
    fx_snapshot = build_rate_snapshot(currency_settings, native, display, now=now)
    return {
        "estimate": False,
        "billing_mode": billing_mode,
        "native_currency": native,
        "display_currency": display,
        "usage": usage_summary,
        "selected_pricing_tier": None,
        "components_native": {},
        "subtotal_native": None,
        "minimum_adjustment_native": None,
        "multiplier": None,
        "discount_amount_native": None,
        "tax_amount_native": None,
        "total_native": None,
        "fx_snapshot": fx_snapshot,
        "components_display": {},
        "total_display": None,
        "warnings": [
            f"Cost estimation is disabled for billing mode '{billing_mode}'.",
            "Use provider dashboard or quota script data for this plan.",
        ],
    }


def _tier_basis_value(basis: str, usage_summary: Dict[str, Any]) -> int:
    if basis in {"billable_input_tokens", "billable_input"}:
        return _int(usage_summary.get("billable_input_tokens"))
    if basis in {"input_plus_cache", "input_with_cache"}:
        return (
            _int(usage_summary.get("input_tokens"))
            + _int(usage_summary.get("cache_read_tokens"))
            + _int(usage_summary.get("cache_write_tokens"))
        )
    if basis in {"total_tokens", "all_tokens"}:
        return (
            _int(usage_summary.get("input_tokens"))
            + _int(usage_summary.get("output_tokens"))
            + _int(usage_summary.get("cache_read_tokens"))
            + _int(usage_summary.get("cache_write_tokens"))
            + _int(usage_summary.get("reasoning_tokens"))
        )
    return _int(usage_summary.get("input_tokens"))


def _tier_max_tokens(tier: Dict[str, Any]) -> int:
    return _int(tier.get("max_input_tokens") or tier.get("max_tokens") or tier.get("up_to") or tier.get("upto"))


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
