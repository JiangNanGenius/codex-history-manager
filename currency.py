"""
Currency settings, manual FX overrides, and rate snapshots.

Online exchange-rate providers are deliberately gated until their official
endpoint and response shape are verified. Manual overrides are first-class and
can be used immediately for deterministic historical cost snapshots.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


REDACTED_VALUE = "********"
SUPPORTED_RATE_SOURCES = {"manual", "apiforex", "disabled"}


def normalize_currency_code(value: Any, default: str = "USD") -> str:
    code = str(value or default).strip().upper()
    if re.match(r"^[A-Z]{3}$", code):
        return code
    return default


def normalize_rate_source(value: Any) -> str:
    source = str(value or "manual").strip().lower()
    return source if source in SUPPORTED_RATE_SOURCES else "manual"


def normalize_manual_overrides(value: Any) -> Dict[str, float]:
    """Normalize manual rate overrides to {'FROM:TO': rate}."""
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, float] = {}
    for raw_key, raw_rate in value.items():
        pair = normalize_pair_key(raw_key)
        if not pair:
            continue
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            normalized[pair] = rate
    return normalized


def normalize_rate_cache(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_entry in value.items():
        pair = normalize_pair_key(raw_key)
        if not pair or not isinstance(raw_entry, dict):
            continue
        try:
            rate = float(raw_entry.get("rate"))
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        normalized[pair] = {
            "from_currency": pair.split(":", 1)[0],
            "to_currency": pair.split(":", 1)[1],
            "rate": rate,
            "source": str(raw_entry.get("source") or "cache"),
            "updated_at": str(raw_entry.get("updated_at") or ""),
            "expires_at": str(raw_entry.get("expires_at") or ""),
            "is_manual": bool(raw_entry.get("is_manual", False)),
        }
    return normalized


def normalize_pair_key(value: Any) -> str:
    raw = str(value or "").strip().upper().replace("/", ":").replace("-", ":")
    parts = [part.strip() for part in raw.split(":") if part.strip()]
    if len(parts) != 2:
        return ""
    from_currency = normalize_currency_code(parts[0], default="")
    to_currency = normalize_currency_code(parts[1], default="")
    if not from_currency or not to_currency:
        return ""
    return f"{from_currency}:{to_currency}"


def redact_currency_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    redacted = copy.deepcopy(settings or {})
    if redacted.get("exchange_rate_api_key"):
        redacted["exchange_rate_api_key"] = REDACTED_VALUE
    return redacted


def preserve_redacted_currency_secret(incoming: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(incoming or {})
    if merged.get("exchange_rate_api_key") == REDACTED_VALUE:
        merged["exchange_rate_api_key"] = str((current or {}).get("exchange_rate_api_key") or "")
    return merged


def normalize_currency_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    raw = settings or {}
    ttl_hours = raw.get("exchange_rate_ttl_hours", 24)
    try:
        ttl_hours = int(ttl_hours)
    except (TypeError, ValueError):
        ttl_hours = 24
    return {
        "display_currency": normalize_currency_code(raw.get("display_currency") or "USD"),
        "exchange_rate_source": normalize_rate_source(raw.get("exchange_rate_source")),
        "exchange_rate_api_key": str(raw.get("exchange_rate_api_key") or "").strip(),
        "exchange_rate_manual_overrides": normalize_manual_overrides(raw.get("exchange_rate_manual_overrides")),
        "exchange_rate_cache": normalize_rate_cache(raw.get("exchange_rate_cache")),
        "exchange_rate_ttl_hours": max(ttl_hours, 1),
        "exchange_rate_docs": {
            "apiforex": {
                "url": "https://apiforex.cn/docs.html",
                "verified": False,
                "status": "unavailable_502_on_2026-06-07",
                "note": "Online apiforex fetching is disabled until the official endpoint and response shape are verified.",
            }
        },
    }


def build_rate_snapshot(
    settings: Dict[str, Any],
    from_currency: str,
    to_currency: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Resolve a rate and return a snapshot suitable for historical request logs."""
    fx = normalize_currency_settings(settings)
    now = now or datetime.now(timezone.utc)
    source_currency = normalize_currency_code(from_currency)
    target_currency = normalize_currency_code(to_currency)
    if source_currency == target_currency:
        return _snapshot(source_currency, target_currency, 1.0, "identity", now, is_manual=True)

    manual = _manual_rate(fx["exchange_rate_manual_overrides"], source_currency, target_currency)
    if manual:
        rate, source = manual
        return _snapshot(source_currency, target_currency, rate, source, now, is_manual=True)

    cached = _cache_rate(fx["exchange_rate_cache"], source_currency, target_currency, now)
    if cached:
        return cached

    if fx["exchange_rate_source"] == "apiforex":
        return {
            "success": False,
            "from_currency": source_currency,
            "to_currency": target_currency,
            "error": "apiforex.cn docs are not verified; online FX fetch is disabled.",
            "docs_url": "https://apiforex.cn/docs.html",
            "warnings": ["Add a manual FX override or verify apiforex endpoint/response shape before enabling online fetch."],
        }

    return {
        "success": False,
        "from_currency": source_currency,
        "to_currency": target_currency,
        "error": "No manual or cached exchange rate is configured.",
        "warnings": ["Add a manual FX override to make cost conversion deterministic."],
    }


def convert_amount(
    settings: Dict[str, Any],
    amount: float,
    from_currency: str,
    to_currency: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        amount_value = 0.0
    rate_result = build_rate_snapshot(settings, from_currency, to_currency, now=now)
    if not rate_result.get("success"):
        return {"success": False, "amount": amount_value, "rate_snapshot": rate_result, "error": rate_result.get("error")}
    converted = amount_value * float(rate_result["rate"])
    return {
        "success": True,
        "amount": amount_value,
        "from_currency": rate_result["from_currency"],
        "to_currency": rate_result["to_currency"],
        "converted_amount": converted,
        "rate_snapshot": rate_result,
    }


def exchange_rate_status_summary(settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return a redaction-safe exchange-rate status summary for diagnostics."""
    fx = normalize_currency_settings(settings)
    now = now or datetime.now(timezone.utc)
    manual_pairs = sorted(fx["exchange_rate_manual_overrides"].keys())
    cache_entries = fx["exchange_rate_cache"]
    stale_cache_pairs = sorted(
        pair for pair, entry in cache_entries.items()
        if _is_stale(entry.get("expires_at"), now)
    )
    active_cache_pairs = sorted(pair for pair in cache_entries.keys() if pair not in set(stale_cache_pairs))

    online_fetch_verified = any(
        bool(doc.get("verified"))
        for doc in fx.get("exchange_rate_docs", {}).values()
        if isinstance(doc, dict)
    )
    can_convert_non_identity = bool(manual_pairs or active_cache_pairs)
    warnings = []

    if fx["exchange_rate_source"] == "apiforex":
        warnings.append("apiforex online fetching is disabled until the official endpoint and response shape are verified.")
    if fx["exchange_rate_source"] == "manual" and not manual_pairs:
        warnings.append("Manual FX source is selected but no manual overrides are configured.")
    if stale_cache_pairs:
        warnings.append(f"{len(stale_cache_pairs)} cached exchange-rate pair(s) are stale.")
    if not can_convert_non_identity and fx["exchange_rate_source"] != "apiforex":
        warnings.append("Only identity-rate conversions are currently guaranteed.")

    if can_convert_non_identity:
        status = "ready"
    elif fx["exchange_rate_source"] == "apiforex":
        status = "blocked_until_verified"
    else:
        status = "needs_manual_rate"

    return {
        "display_currency": fx["display_currency"],
        "exchange_rate_source": fx["exchange_rate_source"],
        "status": status,
        "api_key_configured": bool(fx["exchange_rate_api_key"]),
        "online_fetch_verified": online_fetch_verified,
        "online_fetch_enabled": fx["exchange_rate_source"] == "apiforex" and online_fetch_verified,
        "manual_override_count": len(manual_pairs),
        "manual_pairs": manual_pairs,
        "cache_count": len(cache_entries),
        "active_cache_count": len(active_cache_pairs),
        "stale_cache_count": len(stale_cache_pairs),
        "active_cache_pairs": active_cache_pairs,
        "stale_cache_pairs": stale_cache_pairs,
        "ttl_hours": fx["exchange_rate_ttl_hours"],
        "docs": fx.get("exchange_rate_docs", {}),
        "warnings": warnings,
    }


def update_currency_config(current: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a sanitized partial config update for currency fields."""
    payload = preserve_redacted_currency_secret(payload or {}, current or {})
    update: Dict[str, Any] = {}
    if "display_currency" in payload:
        update["display_currency"] = normalize_currency_code(payload.get("display_currency"))
    if "exchange_rate_source" in payload:
        update["exchange_rate_source"] = normalize_rate_source(payload.get("exchange_rate_source"))
    if "exchange_rate_api_key" in payload:
        update["exchange_rate_api_key"] = str(payload.get("exchange_rate_api_key") or "").strip()
    if "exchange_rate_manual_overrides" in payload:
        update["exchange_rate_manual_overrides"] = normalize_manual_overrides(payload.get("exchange_rate_manual_overrides"))
    if "exchange_rate_cache" in payload:
        update["exchange_rate_cache"] = normalize_rate_cache(payload.get("exchange_rate_cache"))
    if "exchange_rate_ttl_hours" in payload:
        try:
            update["exchange_rate_ttl_hours"] = max(int(payload.get("exchange_rate_ttl_hours")), 1)
        except (TypeError, ValueError):
            update["exchange_rate_ttl_hours"] = 24
    return update


def _manual_rate(overrides: Dict[str, float], from_currency: str, to_currency: str) -> Optional[tuple[float, str]]:
    direct_key = f"{from_currency}:{to_currency}"
    if direct_key in overrides:
        return float(overrides[direct_key]), "manual"
    inverse_key = f"{to_currency}:{from_currency}"
    if inverse_key in overrides and float(overrides[inverse_key]) > 0:
        return 1.0 / float(overrides[inverse_key]), "manual_inverse"
    return None


def _cache_rate(cache: Dict[str, Dict[str, Any]], from_currency: str, to_currency: str, now: datetime) -> Optional[Dict[str, Any]]:
    direct_key = f"{from_currency}:{to_currency}"
    entry = cache.get(direct_key)
    inverse = False
    if not entry:
        inverse_key = f"{to_currency}:{from_currency}"
        entry = cache.get(inverse_key)
        inverse = bool(entry)
    if not entry:
        return None
    rate = float(entry["rate"])
    if inverse:
        rate = 1.0 / rate
    snapshot = _snapshot(
        from_currency,
        to_currency,
        rate,
        str(entry.get("source") or "cache"),
        now,
        is_manual=bool(entry.get("is_manual", False)),
    )
    snapshot["updated_at"] = entry.get("updated_at") or snapshot["updated_at"]
    snapshot["expires_at"] = entry.get("expires_at") or ""
    snapshot["is_stale"] = _is_stale(entry.get("expires_at"), now)
    if snapshot["is_stale"]:
        snapshot["fallback_used"] = True
        snapshot["fallback_reason"] = "stale_cache"
        snapshot["warnings"].append("Cached exchange rate is stale.")
    return snapshot


def _snapshot(
    from_currency: str,
    to_currency: str,
    rate: float,
    source: str,
    now: datetime,
    is_manual: bool = False,
) -> Dict[str, Any]:
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    expires = (now + timedelta(days=3650) if is_manual else now).astimezone(timezone.utc)
    return {
        "success": True,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "rate": float(rate),
        "source": source,
        "updated_at": now_iso,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z") if is_manual else "",
        "is_manual": bool(is_manual),
        "is_stale": False,
        "fallback_used": False,
        "fallback_reason": "",
        "warnings": [],
    }


def _is_stale(expires_at: Any, now: datetime) -> bool:
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now.astimezone(timezone.utc)
