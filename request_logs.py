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
        since: str = "",
        success: Optional[bool] = None,
    ) -> Dict[str, Any]:
        limit = min(max(_positive_int(limit, 100), 1), MAX_QUERY_LIMIT)
        since_dt = _parse_datetime(since) if since else None
        filtered: List[Dict[str, Any]] = []
        for entry in self._iter_entries():
            if provider_id and entry.get("provider_id") != provider_id:
                continue
            if endpoint and entry.get("endpoint") != endpoint:
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
            },
            "cost_native_by_currency": {},
            "cost_display_by_currency": {},
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

            cost = entry.get("cost_estimate") if isinstance(entry.get("cost_estimate"), dict) else {}
            native_currency = str(cost.get("native_currency") or "")
            display_currency = str(cost.get("display_currency") or "")
            if native_currency:
                _add_amount(summary["cost_native_by_currency"], native_currency, cost.get("total_native"))
            if display_currency:
                _add_amount(summary["cost_display_by_currency"], display_currency, cost.get("total_display"))
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
    if provider:
        pricing = pricing_preview_payload(provider, upstream_model)
        cost_estimate = estimate_request_cost(
            normalized_usage,
            pricing.get("pricing") or {},
            currency_settings or {},
            native_currency=pricing.get("native_currency") or provider.get("native_currency") or "",
            display_currency=str((currency_settings or {}).get("display_currency") or ""),
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
        "fx_snapshot": cost_estimate.get("fx_snapshot") if isinstance(cost_estimate, dict) else {},
        "error_type": error_type,
        "error_message": error_message,
    })


def safe_log_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    raw = entry or {}
    usage = normalize_usage(raw.get("usage"))
    cost = raw.get("cost_estimate") if isinstance(raw.get("cost_estimate"), dict) else {}
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
