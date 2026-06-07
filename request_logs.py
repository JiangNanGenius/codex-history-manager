"""
Local proxy request log storage.

The proxy log is intentionally metadata-only: it records routing, status,
usage, estimated cost, and the FX snapshot used for that estimate. It must not
store prompts, request bodies, upstream response bodies, headers, or secrets.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app_paths import app_data_path
from costing import estimate_request_cost, pricing_preview_payload
from currency import normalize_currency_code


DEFAULT_REQUEST_LOG_PATH = app_data_path("logs", "proxy_requests.jsonl")
LOG_SCHEMA_VERSION = 1
MAX_QUERY_LIMIT = 1000
REDACTED_VALUE = "********"
SECRET_KEY_FRAGMENTS = (
    "authorization",
    "api-key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "bearer",
)


class RequestLogStore:
    """Append-only JSONL request logs with simple retention controls."""

    def __init__(
        self,
        path: str | Path = "",
        retention_days: int = 30,
        max_mb: float = 50,
    ):
        self.path = Path(path).expanduser() if path else DEFAULT_REQUEST_LOG_PATH
        self.retention_days = _positive_int(retention_days, 30)
        self.max_bytes = int(max(_positive_float(max_mb, 50.0), 1.0) * 1024 * 1024)

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        safe_entry = safe_log_entry(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe_entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.enforce_retention()
        return safe_entry

    def read_entries(
        self,
        limit: int = 100,
        provider_id: str = "",
        endpoint: str = "",
        media_kind: str = "",
        error_type: str = "",
        since: str = "",
        success: Optional[bool] = None,
    ) -> Dict[str, Any]:
        limit = min(max(_positive_int(limit, 100), 1), MAX_QUERY_LIMIT)
        since_dt = _parse_datetime(since) if since else None
        media_kind = str(media_kind or "").strip().lower()
        error_type = str(error_type or "").strip()
        filtered: List[Dict[str, Any]] = []
        for entry in self._iter_entries():
            if provider_id and entry.get("provider_id") != provider_id:
                continue
            if endpoint and entry.get("endpoint") != endpoint:
                continue
            if media_kind and entry.get("media_kind") != media_kind:
                continue
            if error_type and entry.get("error_type") != error_type:
                continue
            if success is not None and bool(entry.get("success")) is not success:
                continue
            if since_dt:
                ts = _parse_datetime(entry.get("timestamp"))
                if not ts or ts < since_dt:
                    continue
            filtered.append(entry)
        return {
            "path": str(self.path),
            "limit": limit,
            "count": len(filtered),
            "entries": list(reversed(filtered[-limit:])),
        }

    def summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "path": str(self.path),
            "exists": self.path.exists(),
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "count": 0,
            "success_count": 0,
            "error_count": 0,
            "latest_timestamp": "",
            "tokens": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_total_tokens": 0,
                "reasoning_tokens": 0,
                "image_count": 0,
                "video_job_count": 0,
                "video_seconds": 0,
            },
            "cost_native_by_currency": {},
            "cost_display_by_currency": {},
            "provider_reported_cost_by_currency": {},
            "cost_comparison": {
                "estimated_count": 0,
                "reported_count": 0,
                "estimated_only_count": 0,
                "matched_currency_count": 0,
                "estimated_minus_reported_by_currency": {},
            },
            "fx": {
                "snapshots": 0,
                "unavailable_count": 0,
                "stale_count": 0,
                "fallback_count": 0,
                "sources": {},
            },
            "media": {
                "count": 0,
                "success_count": 0,
                "error_count": 0,
                "by_kind": {},
                "providers": {},
                "endpoints": {},
                "error_types": {},
                "latest_error": {},
            },
            "providers": {},
            "endpoints": {},
        }
        for entry in self._iter_entries():
            summary["count"] += 1
            if entry.get("success"):
                summary["success_count"] += 1
            else:
                summary["error_count"] += 1
            timestamp = str(entry.get("timestamp") or "")
            if timestamp > summary["latest_timestamp"]:
                summary["latest_timestamp"] = timestamp

            usage = normalize_usage(entry.get("usage"))
            for key in summary["tokens"]:
                summary["tokens"][key] += int(usage.get(key, 0))

            provider_id = str(entry.get("provider_id") or "unknown")
            endpoint = str(entry.get("endpoint") or "unknown")
            summary["providers"][provider_id] = summary["providers"].get(provider_id, 0) + 1
            summary["endpoints"][endpoint] = summary["endpoints"].get(endpoint, 0) + 1
            _add_media_log_summary(summary["media"], entry, usage, provider_id, endpoint, timestamp)

            cost = entry.get("cost_estimate") if isinstance(entry.get("cost_estimate"), dict) else {}
            native_currency = str(cost.get("native_currency") or "")
            display_currency = str(cost.get("display_currency") or "")
            estimated_total = cost.get("total_native")
            has_estimate = _safe_float(estimated_total) > 0
            if native_currency:
                _add_amount(summary["cost_native_by_currency"], native_currency, estimated_total)
            if display_currency:
                _add_amount(summary["cost_display_by_currency"], display_currency, cost.get("total_display"))
            if has_estimate:
                summary["cost_comparison"]["estimated_count"] += 1

            reported_cost = normalize_provider_reported_cost(entry.get("provider_reported_cost"))
            reported_amount = reported_cost.get("amount")
            reported_currency = str(reported_cost.get("currency") or "")
            if reported_currency and _safe_float(reported_amount) > 0:
                summary["cost_comparison"]["reported_count"] += 1
                _add_amount(summary["provider_reported_cost_by_currency"], reported_currency, reported_amount)
                if native_currency and native_currency == reported_currency and has_estimate:
                    summary["cost_comparison"]["matched_currency_count"] += 1
                    _add_amount(
                        summary["cost_comparison"]["estimated_minus_reported_by_currency"],
                        native_currency,
                        float(estimated_total or 0) - float(reported_amount or 0),
                    )
            elif has_estimate:
                summary["cost_comparison"]["estimated_only_count"] += 1

            fx_snapshot = entry.get("fx_snapshot") if isinstance(entry.get("fx_snapshot"), dict) else {}
            if not fx_snapshot and isinstance(cost.get("fx_snapshot"), dict):
                fx_snapshot = cost.get("fx_snapshot")
            _add_fx_snapshot(summary["fx"], fx_snapshot)
        return summary

    def enforce_retention(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        before_bytes = self.path.stat().st_size if self.path.exists() else 0
        entries = list(self._iter_entries())
        before_count = len(entries)
        if not entries:
            return {
                "path": str(self.path),
                "before_entries": before_count,
                "after_entries": before_count,
                "removed_entries": 0,
                "before_bytes": before_bytes,
                "after_bytes": before_bytes,
            }

        cutoff = now - timedelta(days=self.retention_days)
        kept = []
        for entry in entries:
            ts = _parse_datetime(entry.get("timestamp"))
            if ts is None or ts >= cutoff:
                kept.append(entry)

        lines = [_encode_line(entry) for entry in kept]
        total_bytes = sum(len(line) for line in lines)
        while lines and total_bytes > self.max_bytes:
            total_bytes -= len(lines.pop(0))
            kept.pop(0)

        changed = len(kept) != before_count or total_bytes != before_bytes
        if changed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "wb") as f:
                for line in lines:
                    f.write(line)
            tmp.replace(self.path)
        after_bytes = self.path.stat().st_size if self.path.exists() else 0
        return {
            "path": str(self.path),
            "before_entries": before_count,
            "after_entries": len(kept),
            "removed_entries": before_count - len(kept),
            "before_bytes": before_bytes,
            "after_bytes": after_bytes,
            "retention_days": self.retention_days,
            "max_bytes": self.max_bytes,
        }

    def _iter_entries(self) -> Iterable[Dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        entries.append(safe_log_entry(item))
        except OSError:
            return []
        return entries


def build_proxy_log_entry(
    context: Dict[str, Any],
    response_json: Any = None,
    usage: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
    duration_ms: Optional[float] = None,
    error_type: str = "",
    error_message: str = "",
    currency_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context = context or {}
    provider = context.get("provider") if isinstance(context.get("provider"), dict) else {}
    upstream_model = str(context.get("upstream_model") or context.get("model") or "")
    normalized_usage = normalize_usage(
        usage
        or extract_usage_from_response(response_json)
        or context.get("usage_hint")
        or {}
    )
    cost_estimate: Dict[str, Any] = {}
    pricing_payload: Dict[str, Any] = {}
    if provider:
        pricing_payload = pricing_preview_payload(provider, upstream_model)
        cost_estimate = estimate_request_cost(
            normalized_usage,
            pricing_payload.get("pricing") or {},
            currency_settings or {},
            native_currency=pricing_payload.get("native_currency") or provider.get("native_currency") or "",
            display_currency=str((currency_settings or {}).get("display_currency") or ""),
        )
    provider_reported_cost = extract_provider_reported_cost(
        response_json,
        usage or context.get("usage_hint") or {},
        native_currency=(
            cost_estimate.get("native_currency")
            or pricing_payload.get("native_currency")
            or provider.get("native_currency")
            or ""
        ),
    )

    return safe_log_entry({
        "schema_version": LOG_SCHEMA_VERSION,
        "request_id": context.get("request_id") or str(uuid.uuid4()),
        "timestamp": context.get("timestamp") or _utc_now_iso(),
        "endpoint": context.get("endpoint"),
        "method": context.get("method") or "POST",
        "provider_id": provider.get("id") or context.get("provider_id"),
        "provider_alias": provider.get("short_alias") or context.get("provider_alias"),
        "api_format": provider.get("api_format") or context.get("api_format"),
        "model": context.get("model") or upstream_model,
        "upstream_model": upstream_model,
        "media_kind": context.get("media_kind") or "",
        "stream": bool(context.get("stream", False)),
        "status_code": status_code,
        "success": 200 <= _positive_int(status_code, 0) < 400 and not error_type,
        "duration_ms": duration_ms if duration_ms is not None else context.get("duration_ms"),
        "route_explanation": context.get("route_explanation") or "",
        "usage": normalized_usage,
        "cost_estimate": cost_estimate,
        "provider_reported_cost": provider_reported_cost,
        "fx_snapshot": cost_estimate.get("fx_snapshot") if isinstance(cost_estimate, dict) else {},
        "error_type": error_type,
        "error_message": error_message,
    })


def safe_log_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    raw = entry or {}
    usage = normalize_usage(raw.get("usage"))
    cost = raw.get("cost_estimate") if isinstance(raw.get("cost_estimate"), dict) else {}
    provider_reported_cost = normalize_provider_reported_cost(raw.get("provider_reported_cost"))
    fx_snapshot = raw.get("fx_snapshot") if isinstance(raw.get("fx_snapshot"), dict) else {}
    if not fx_snapshot and isinstance(cost, dict):
        fx_snapshot = cost.get("fx_snapshot") if isinstance(cost.get("fx_snapshot"), dict) else {}
    status_code = _positive_int(raw.get("status_code"), 0)
    safe = {
        "schema_version": _positive_int(raw.get("schema_version"), LOG_SCHEMA_VERSION),
        "request_id": str(raw.get("request_id") or str(uuid.uuid4())),
        "timestamp": str(raw.get("timestamp") or _utc_now_iso()),
        "endpoint": _safe_short(raw.get("endpoint"), 80),
        "method": _safe_short(raw.get("method") or "POST", 12).upper(),
        "provider_id": _safe_short(raw.get("provider_id"), 120),
        "provider_alias": _safe_short(raw.get("provider_alias"), 80),
        "api_format": _safe_short(raw.get("api_format"), 80),
        "model": _safe_short(raw.get("model"), 200),
        "upstream_model": _safe_short(raw.get("upstream_model"), 200),
        "media_kind": _safe_short(raw.get("media_kind"), 40),
        "stream": bool(raw.get("stream", False)),
        "status_code": status_code,
        "success": bool(raw.get("success", 200 <= status_code < 400)),
        "duration_ms": round(_safe_float(raw.get("duration_ms")), 3),
        "route_explanation": _safe_short(raw.get("route_explanation"), 300),
        "usage": usage,
        "cost_estimate": redact_secrets(cost),
        "provider_reported_cost": provider_reported_cost,
        "fx_snapshot": redact_secrets(fx_snapshot),
        "error_type": _safe_short(raw.get("error_type"), 80),
        "error_message": _safe_short(redact_secrets(raw.get("error_message")), 500),
    }
    return safe


def normalize_usage(usage: Any) -> Dict[str, int]:
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _int_value(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int_value(usage.get("output_tokens") or usage.get("completion_tokens"))
    cache_read = _int_value(
        usage.get("cache_read_tokens")
        or usage.get("cache_read_input_tokens")
        or usage.get("cached_input_tokens")
        or _nested_int(usage, ("input_tokens_details", "cached_tokens"))
        or _nested_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or usage.get("cachedContentTokenCount")
    )
    cache_creation = _int_value(
        usage.get("cache_creation_tokens")
        or usage.get("cache_write_tokens")
        or usage.get("cache_creation_input_tokens")
    )
    reasoning = _int_value(
        usage.get("reasoning_tokens")
        or _nested_int(usage, ("output_tokens_details", "reasoning_tokens"))
        or _nested_int(usage, ("completion_tokens_details", "reasoning_tokens"))
    )
    total_tokens = _int_value(usage.get("total_tokens") or (input_tokens + output_tokens))
    image_count = _int_value(usage.get("image_count") or usage.get("images"))
    video_job_count = _int_value(usage.get("video_job_count") or usage.get("video_count") or usage.get("videos"))
    video_seconds = _int_value(usage.get("video_seconds"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "cache_total_tokens": cache_read + cache_creation,
        "reasoning_tokens": reasoning,
        "image_count": image_count,
        "video_job_count": video_job_count,
        "video_seconds": video_seconds,
    }


def extract_usage_from_response(response_json: Any) -> Dict[str, Any]:
    if not isinstance(response_json, dict):
        return {}
    usage = response_json.get("usage")
    return usage if isinstance(usage, dict) else {}


def extract_provider_reported_cost(
    response_json: Any,
    usage: Any = None,
    native_currency: str = "",
) -> Dict[str, Any]:
    """Extract invoice-like provider cost metadata without storing response bodies."""
    usage_obj = usage if isinstance(usage, dict) else {}
    response_obj = response_json if isinstance(response_json, dict) else {}
    response_usage = response_obj.get("usage") if isinstance(response_obj.get("usage"), dict) else {}
    currency_hint = _cost_currency_hint(usage_obj, response_usage, response_obj)
    candidates = [
        ("usage.total_cost", response_usage.get("total_cost"), response_usage),
        ("usage.cost", response_usage.get("cost"), response_usage),
        ("usage.cost_usd", response_usage.get("cost_usd"), {**response_usage, "currency": "USD"}),
        ("usage.billing_cost", response_usage.get("billing_cost"), response_usage),
        ("response.total_cost", response_obj.get("total_cost"), response_obj),
        ("response.cost", response_obj.get("cost"), response_obj),
        ("response.cost_usd", response_obj.get("cost_usd"), {**response_obj, "currency": "USD"}),
        ("billing.total_cost", _nested_value(response_obj, ("billing", "total_cost")), response_obj.get("billing")),
        ("billing.cost", _nested_value(response_obj, ("billing", "cost")), response_obj.get("billing")),
        ("usage_hint.total_cost", usage_obj.get("total_cost"), usage_obj),
        ("usage_hint.cost", usage_obj.get("cost"), usage_obj),
        ("usage_hint.cost_usd", usage_obj.get("cost_usd"), {**usage_obj, "currency": "USD"}),
    ]
    for source, value, container in candidates:
        result = _provider_cost_candidate(source, value, container, currency_hint, native_currency)
        if result:
            return result
    return {}


def normalize_provider_reported_cost(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    amount = _safe_float(value.get("amount"))
    currency = normalize_currency_code(value.get("currency") or "", default="")
    if amount <= 0 or not currency:
        return {}
    return {
        "amount": round(amount, 12),
        "currency": currency,
        "source": _safe_short(value.get("source"), 80),
        "currency_inferred": bool(value.get("currency_inferred", False)),
    }


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                redacted[key_text] = REDACTED_VALUE
            else:
                redacted[key_text] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer " + REDACTED_VALUE, value)
        text = re.sub(r"sk-[A-Za-z0-9._\-]+", "sk-" + REDACTED_VALUE, text)
        text = re.sub(r"ek_[A-Za-z0-9._\-]+", "ek_" + REDACTED_VALUE, text)
        return text
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS)


def _encode_line(entry: Dict[str, Any]) -> bytes:
    return (json.dumps(safe_log_entry(entry), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_short(value: Any, limit: int) -> str:
    text = str(value or "")
    text = str(redact_secrets(text))
    if len(text) > limit:
        return text[: max(limit - 3, 0)] + "..."
    return text


def _int_value(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _positive_int(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _positive_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _safe_float(value: Any) -> float:
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _provider_cost_candidate(
    source: str,
    value: Any,
    container: Any,
    currency_hint: str,
    native_currency: str,
) -> Dict[str, Any]:
    amount, explicit_currency = _provider_cost_amount_and_currency(value)
    if amount <= 0:
        return {}
    container_currency = _cost_currency_hint(container if isinstance(container, dict) else {})
    currency = normalize_currency_code(explicit_currency or container_currency or currency_hint or native_currency, default="")
    if not currency:
        return {}
    inferred = not bool(explicit_currency or container_currency or currency_hint)
    return normalize_provider_reported_cost({
        "amount": amount,
        "currency": currency,
        "source": source,
        "currency_inferred": inferred,
    })


def _provider_cost_amount_and_currency(value: Any) -> tuple[float, str]:
    if isinstance(value, dict):
        amount_keys = ("amount", "total", "total_cost", "cost", "value", "charged")
        amount = 0.0
        for key in amount_keys:
            amount = _safe_float(value.get(key))
            if amount > 0:
                break
        return amount, _cost_currency_hint(value)
    return _safe_float(value), ""


def _cost_currency_hint(*containers: Any) -> str:
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("currency", "cost_currency", "billing_currency", "native_currency"):
            currency = normalize_currency_code(container.get(key) or "", default="")
            if currency:
                return currency
    return ""


def _nested_value(data: Dict[str, Any], path: tuple[str, str]) -> Any:
    parent = data.get(path[0]) if isinstance(data, dict) else None
    if isinstance(parent, dict):
        return parent.get(path[1])
    return None


def _nested_int(data: Dict[str, Any], path: tuple[str, str]) -> int:
    parent = data.get(path[0])
    if isinstance(parent, dict):
        return _int_value(parent.get(path[1]))
    return 0


def _add_amount(target: Dict[str, float], currency: str, amount: Any) -> None:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return
    if value:
        target[currency] = round(float(target.get(currency, 0.0)) + value, 12)


def _add_fx_snapshot(target: Dict[str, Any], snapshot: Any) -> None:
    if not isinstance(snapshot, dict) or not snapshot:
        return
    target["snapshots"] = int(target.get("snapshots", 0)) + 1
    if snapshot.get("success") is False:
        target["unavailable_count"] = int(target.get("unavailable_count", 0)) + 1
    if snapshot.get("is_stale"):
        target["stale_count"] = int(target.get("stale_count", 0)) + 1
    if snapshot.get("fallback_used"):
        target["fallback_count"] = int(target.get("fallback_count", 0)) + 1
    source = str(snapshot.get("source") or "").strip()
    if source:
        sources = target.setdefault("sources", {})
        sources[source] = int(sources.get(source, 0)) + 1


def _add_media_log_summary(
    target: Dict[str, Any],
    entry: Dict[str, Any],
    usage: Dict[str, int],
    provider_id: str,
    endpoint: str,
    timestamp: str,
) -> None:
    media_kind = _media_kind_for_entry(entry, usage, endpoint)
    if not media_kind:
        return

    success = bool(entry.get("success"))
    target["count"] = int(target.get("count") or 0) + 1
    if success:
        target["success_count"] = int(target.get("success_count") or 0) + 1
    else:
        target["error_count"] = int(target.get("error_count") or 0) + 1

    by_kind = target.setdefault("by_kind", {})
    kind_summary = by_kind.setdefault(media_kind, {"count": 0, "success_count": 0, "error_count": 0})
    kind_summary["count"] = int(kind_summary.get("count") or 0) + 1
    if success:
        kind_summary["success_count"] = int(kind_summary.get("success_count") or 0) + 1
    else:
        kind_summary["error_count"] = int(kind_summary.get("error_count") or 0) + 1

    _increment_count(target.setdefault("providers", {}), provider_id or "unknown")
    _increment_count(target.setdefault("endpoints", {}), endpoint or "unknown")

    if not success:
        error_type = str(entry.get("error_type") or "unknown_error")
        _increment_count(target.setdefault("error_types", {}), error_type)
        latest = target.get("latest_error") if isinstance(target.get("latest_error"), dict) else {}
        if not latest or timestamp >= str(latest.get("timestamp") or ""):
            target["latest_error"] = {
                "timestamp": timestamp,
                "provider_id": provider_id or "unknown",
                "endpoint": endpoint or "unknown",
                "media_kind": media_kind,
                "error_type": error_type,
                "status_code": _positive_int(entry.get("status_code"), 0),
            }


def _media_kind_for_entry(entry: Dict[str, Any], usage: Dict[str, int], endpoint: str) -> str:
    explicit = str(entry.get("media_kind") or "").strip().lower()
    if explicit in {"image", "video"}:
        return explicit
    normalized_endpoint = str(endpoint or "").strip().lower().lstrip("/")
    if normalized_endpoint.startswith("v1/"):
        normalized_endpoint = normalized_endpoint[3:]
    if normalized_endpoint.startswith("images/"):
        return "image"
    if normalized_endpoint == "videos" or normalized_endpoint.startswith("videos/"):
        return "video"
    if int(usage.get("image_count") or 0) > 0:
        return "image"
    if int(usage.get("video_job_count") or 0) > 0 or int(usage.get("video_seconds") or 0) > 0:
        return "video"
    return ""


def _increment_count(target: Dict[str, int], key: str) -> None:
    target[key] = int(target.get(key) or 0) + 1
