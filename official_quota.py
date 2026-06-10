"""Read-only Codex official subscription quota probe.

This module intentionally never refreshes or writes Codex auth/config files.
It only reads the existing Codex OAuth access token and queries the same
ChatGPT wham usage endpoint used by Codex/CC Switch.
"""
from __future__ import annotations

import copy
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


DEFAULT_OFFICIAL_QUOTA_TTL_SECONDS = 300
CODEX_WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


class OfficialCodexQuotaManager:
    def __init__(
        self,
        auth_loader: Callable[[], Dict[str, Any]],
        *,
        ttl_seconds: int = DEFAULT_OFFICIAL_QUOTA_TTL_SECONDS,
    ) -> None:
        self.auth_loader = auth_loader
        self.ttl_seconds = max(int(ttl_seconds or DEFAULT_OFFICIAL_QUOTA_TTL_SECONDS), 1)
        self._cache: Optional[Dict[str, Any]] = None

    def cached(self) -> Dict[str, Any]:
        if not self._cache:
            return {
                "success": False,
                "provider_id": "codex_official",
                "enabled": True,
                "type": "official_subscription",
                "cache_hit": False,
                "error": "No official quota snapshot cached.",
            }
        return _with_cache(copy.deepcopy(self._cache), cache_hit=True)

    def refresh(self, *, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if (
            not force
            and self._cache
            and now < float(self._cache.get("_expires_at") or 0)
        ):
            return _with_cache(copy.deepcopy(self._cache), cache_hit=True)
        result = self._query()
        result["_cached_at"] = now
        result["_expires_at"] = now + self.ttl_seconds
        self._cache = copy.deepcopy(result)
        return _with_cache(result, cache_hit=False)

    def _query(self) -> Dict[str, Any]:
        auth = self.auth_loader() or {}
        parsed = _read_codex_oauth(auth)
        if not parsed.get("access_token"):
            return _failure(parsed.get("error") or "Codex official OAuth token was not found.", parsed)
        if parsed.get("credential_status") == "expired":
            return _failure(parsed.get("error") or "Codex official OAuth token may be expired.", parsed)

        headers = {
            "Authorization": f"Bearer {parsed['access_token']}",
            "User-Agent": "codex-cli",
            "Accept": "application/json",
        }
        if parsed.get("account_id"):
            headers["ChatGPT-Account-Id"] = str(parsed["account_id"])

        request = urllib.request.Request(CODEX_WHAM_USAGE_URL, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = int(getattr(response, "status", 200) or 200)
                payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            return _failure(f"Official quota request failed: HTTP {exc.code}", parsed, status_code=exc.code, raw_preview=body)
        except Exception as exc:
            return _failure(f"Official quota request failed: {exc}", parsed)

        if status < 200 or status >= 300:
            return _failure(f"Official quota request failed: HTTP {status}", parsed, status_code=status)

        values = _extract_wham_quota_values(payload)
        return {
            "success": True,
            "provider_id": "codex_official",
            "enabled": True,
            "type": "official_subscription",
            "status_code": status,
            "fetched_at": _now_iso(),
            "ttl_seconds": self.ttl_seconds,
            "credential_status": parsed.get("credential_status", "valid"),
            "values": values,
            "raw_redacted": _redact_wham_payload(payload),
            "note": "Codex official OAuth quota from chatgpt.com/backend-api/wham/usage.",
        }


def _read_codex_oauth(auth: Dict[str, Any]) -> Dict[str, Any]:
    if str(auth.get("auth_mode") or "").strip() not in {"chatgpt", "official_oauth"}:
        return {
            "credential_status": "not_found",
            "error": "Codex is not using official OAuth mode.",
        }
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        return {
            "credential_status": "parse_error",
            "error": "Codex auth.json has no access_token.",
        }
    status = "valid"
    error = ""
    if _codex_token_is_stale(auth.get("last_refresh")):
        status = "expired"
        error = "Codex OAuth token may be stale (>8 days since last refresh)."
    return {
        "credential_status": status,
        "error": error,
        "access_token": access_token,
        "account_id": tokens.get("account_id") or auth.get("chatgpt_account_id") or "",
    }


def _codex_token_is_stale(last_refresh: Any) -> bool:
    text = str(last_refresh or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return (datetime.now(timezone.utc) - parsed).total_seconds() > 8 * 24 * 3600


def _extract_wham_quota_values(payload: Dict[str, Any]) -> Dict[str, Any]:
    rate_limit = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else {}
    tiers = []
    for window in (rate_limit.get("primary_window"), rate_limit.get("secondary_window")):
        if not isinstance(window, dict):
            continue
        used = _number_or_none(window.get("used_percent"))
        if used is None:
            continue
        tiers.append({
            "name": _window_seconds_to_tier_name(window.get("limit_window_seconds")),
            "utilization": max(0.0, min(float(used), 100.0)),
            "resets_at": _unix_seconds_to_iso(window.get("reset_at")),
        })
    max_utilization = max([float(tier.get("utilization") or 0) for tier in tiers] or [0.0])
    return {
        "tool": "codex_oauth",
        "tiers": tiers,
        "quota_percent": max_utilization,
        "remaining_percent": max(0.0, 100.0 - max_utilization),
    }


def _window_seconds_to_tier_name(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if seconds == 18000:
        return "five_hour"
    if seconds == 604800:
        return "seven_day"
    hours = seconds // 3600
    if hours >= 24:
        return f"{hours // 24}_day"
    return f"{hours}_hour"


def _unix_seconds_to_iso(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")


def _number_or_none(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _failure(message: str, parsed_auth: Dict[str, Any] | None = None, *, status_code: int = 0, raw_preview: str = "") -> Dict[str, Any]:
    return {
        "success": False,
        "provider_id": "codex_official",
        "enabled": True,
        "type": "official_subscription",
        "status_code": status_code,
        "fetched_at": _now_iso(),
        "ttl_seconds": DEFAULT_OFFICIAL_QUOTA_TTL_SECONDS,
        "credential_status": (parsed_auth or {}).get("credential_status") or "unknown",
        "error": message,
        "raw_preview": raw_preview,
    }


def _with_cache(result: Dict[str, Any], *, cache_hit: bool) -> Dict[str, Any]:
    cleaned = {key: value for key, value in result.items() if not key.startswith("_")}
    cached_at = result.get("_cached_at")
    expires_at = result.get("_expires_at")
    cleaned["cache_hit"] = cache_hit
    if cached_at:
        cleaned["cached_at"] = datetime.fromtimestamp(float(cached_at), tz=timezone.utc).isoformat(timespec="seconds")
    if expires_at:
        cleaned["expires_at"] = datetime.fromtimestamp(float(expires_at), tz=timezone.utc).isoformat(timespec="seconds")
    return cleaned


def _redact_wham_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {}
    if isinstance(payload.get("rate_limit"), dict):
        allowed["rate_limit"] = copy.deepcopy(payload["rate_limit"])
    return allowed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
