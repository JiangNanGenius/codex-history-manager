"""
Generic provider balance/quota probes.

This module implements provider-configured probes only. Provider-specific
official quota endpoints can be layered on top later after their auth and
response shapes are verified.
"""
from __future__ import annotations

import copy
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from providers import is_secret_key


DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_TTL_SECONDS = 300
REDACTED_VALUE = "********"


class QuotaError(Exception):
    pass


class QuotaManager:
    """Small in-memory quota cache plus generic HTTP probe runner."""

    def __init__(self, provider_loader: Callable[[], list[Dict[str, Any]]]):
        self.provider_loader = provider_loader
        self._cache: Dict[str, Dict[str, Any]] = {}

    def refresh_provider_quota(self, provider_id: str, force: bool = False) -> Dict[str, Any]:
        provider = self._find_provider(provider_id)
        if not provider:
            return {"success": False, "provider_id": provider_id, "error": "Provider not found"}

        quota_check = normalize_quota_check(provider.get("quota_check"))
        if not quota_check.get("enabled"):
            result = {
                "success": False,
                "provider_id": provider_id,
                "enabled": False,
                "error": "Quota check is not enabled for this provider.",
                "snapshot": None,
            }
            entry = _cache_entry(result, quota_check.get("ttl_seconds"))
            self._cache[provider_id] = entry
            return redact_quota_result(_with_cache_metadata(result, entry, cache_hit=False))

        cached = self._cache.get(provider_id)
        if cached and not force and not _is_expired(cached):
            return redact_quota_result(_with_cache_metadata(cached["result"], cached, cache_hit=True))

        result = run_quota_probe(provider, quota_check)
        entry = _cache_entry(result, quota_check.get("ttl_seconds"))
        self._cache[provider_id] = entry
        return redact_quota_result(_with_cache_metadata(result, entry, cache_hit=False))

    def cached_provider_quota(self, provider_id: str) -> Dict[str, Any]:
        cached = self._cache.get(provider_id)
        if not cached:
            return {"success": False, "provider_id": provider_id, "cache_hit": False, "error": "No quota snapshot cached."}
        return redact_quota_result(_with_cache_metadata(cached["result"], cached, cache_hit=True))

    def list_cached(self) -> Dict[str, Any]:
        return {
            "snapshots": {
                provider_id: self.cached_provider_quota(provider_id)
                for provider_id in sorted(self._cache)
            }
        }

    def _find_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        for provider in self.provider_loader():
            if provider.get("id") == provider_id:
                return provider
        return None


def normalize_quota_check(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    method = str(raw.get("method") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        method = "GET"
    timeout = _int(raw.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS)
    ttl = _int(raw.get("ttl_seconds"), DEFAULT_TTL_SECONDS)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "method": method,
        "url": str(raw.get("url") or raw.get("endpoint") or "").strip(),
        "headers": raw.get("headers") if isinstance(raw.get("headers"), dict) else {},
        "body": raw.get("body") if isinstance(raw.get("body"), dict) else None,
        "json_paths": normalize_json_paths(raw.get("json_paths")),
        "timeout_seconds": max(timeout, 1),
        "ttl_seconds": max(ttl, 1),
        "note": str(raw.get("note") or ""),
    }


def normalize_json_paths(value: Any) -> Dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    result: Dict[str, str] = {}
    for key, path in raw.items():
        key_str = str(key).strip()
        path_str = str(path).strip()
        if key_str and path_str:
            result[key_str] = path_str
    return result


def run_quota_probe(provider: Dict[str, Any], quota_check: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(provider.get("id") or "")
    url = quota_check.get("url") or ""
    if not url:
        return _failure_snapshot(provider_id, "Quota check URL is empty.", quota_check)

    headers = build_quota_headers(provider, quota_check)
    body = None
    if quota_check.get("body") is not None:
        body = json.dumps(quota_check["body"], ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = headers.get("Content-Type") or "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=quota_check.get("method", "GET"))
    try:
        with urllib.request.urlopen(request, timeout=quota_check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)) as resp:
            raw_body = resp.read()
            status = int(resp.getcode() or 200)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read() if exc.fp else b""
        return _failure_snapshot(provider_id, f"HTTP {exc.code}", quota_check, status_code=exc.code, raw_body=raw_body)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return _failure_snapshot(provider_id, f"Quota request failed: {exc}", quota_check)

    try:
        payload = json.loads(raw_body.decode("utf-8", errors="replace")) if raw_body else {}
    except json.JSONDecodeError:
        return _failure_snapshot(provider_id, "Quota response is not valid JSON.", quota_check, status_code=status, raw_body=raw_body)

    extracted = extract_json_paths(payload, quota_check.get("json_paths") or {})
    return {
        "success": True,
        "provider_id": provider_id,
        "enabled": True,
        "status_code": status,
        "fetched_at": _now_iso(),
        "ttl_seconds": quota_check.get("ttl_seconds", DEFAULT_TTL_SECONDS),
        "values": extracted,
        "raw_redacted": redact_quota_result(payload),
        "note": quota_check.get("note") or "",
    }


def refresh_provider_quota_preview(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Run a one-off quota probe for an unsaved provider draft without caching it."""
    provider_id = str((provider or {}).get("id") or "")
    quota_check = normalize_quota_check((provider or {}).get("quota_check"))
    if not quota_check.get("enabled"):
        result = {
            "success": False,
            "provider_id": provider_id,
            "enabled": False,
            "error": "Quota check is not enabled for this provider.",
            "snapshot": None,
            "preview": True,
        }
    else:
        result = run_quota_probe(provider or {}, quota_check)
        result["preview"] = True
    entry = _cache_entry(result, quota_check.get("ttl_seconds"))
    return redact_quota_result(_with_cache_metadata(result, entry, cache_hit=False))


def build_quota_headers(provider: Dict[str, Any], quota_check: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    configured = provider.get("headers") if isinstance(provider.get("headers"), dict) else {}
    for key, value in configured.items():
        if isinstance(value, str) and key.lower() not in {"authorization", "x-api-key"}:
            headers[str(key)] = value
    override = quota_check.get("headers") if isinstance(quota_check.get("headers"), dict) else {}
    for key, value in override.items():
        if isinstance(value, str):
            headers[str(key)] = value
    user_agent = str(provider.get("user_agent") or configured.get("User-Agent") or "Codex-Enhance-Manager-Quota/1.0")
    headers["User-Agent"] = headers.get("User-Agent") or user_agent

    api_key = str(provider.get("secondary_usage_key") or provider.get("api_key") or "")
    if api_key and not _has_header(headers, "Authorization") and not _has_header(headers, "x-api-key"):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def extract_json_paths(payload: Any, paths: Dict[str, str]) -> Dict[str, Any]:
    return {name: extract_json_path(payload, path) for name, path in (paths or {}).items()}


def extract_json_path(payload: Any, path: str) -> Any:
    current = payload
    for segment in _split_path(path):
        if isinstance(current, dict):
            if segment not in current:
                return None
            current = current.get(segment)
            continue
        if isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return current


def redact_quota_result(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_quota_result(item) for item in value]
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(str(key)):
                redacted[key] = REDACTED_VALUE if item else ""
            else:
                redacted[key] = redact_quota_result(item)
        return redacted
    return value


def _failure_snapshot(provider_id: str, error: str, quota_check: Dict[str, Any], status_code: int = 0, raw_body: bytes = b"") -> Dict[str, Any]:
    snapshot = {
        "success": False,
        "provider_id": provider_id,
        "enabled": bool(quota_check.get("enabled", False)),
        "status_code": status_code,
        "fetched_at": _now_iso(),
        "ttl_seconds": quota_check.get("ttl_seconds", DEFAULT_TTL_SECONDS),
        "error": error,
        "raw_preview": raw_body.decode("utf-8", errors="replace")[:500] if raw_body else "",
    }
    return redact_quota_result(snapshot)


def _cache_entry(result: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    now = time.time()
    return {
        "result": copy.deepcopy(result),
        "created_at": now,
        "expires_at": now + max(int(ttl_seconds or DEFAULT_TTL_SECONDS), 1),
    }


def _with_cache_metadata(result: Dict[str, Any], entry: Dict[str, Any], cache_hit: bool) -> Dict[str, Any]:
    copy_result = copy.deepcopy(result)
    expires_at = float(entry.get("expires_at") or 0)
    created_at = float(entry.get("created_at") or 0)
    remaining = max(0, int(expires_at - time.time())) if expires_at else 0
    copy_result.update({
        "cache_hit": cache_hit,
        "cache_created_at": created_at,
        "cache_expires_at": expires_at,
        "cache_ttl_remaining_seconds": remaining,
        "cache_expired": _is_expired(entry),
    })
    return copy_result


def _is_expired(entry: Dict[str, Any]) -> bool:
    return float(entry.get("expires_at") or 0) < time.time()


def _split_path(path: str) -> list[str]:
    raw = str(path or "").strip()
    if raw.startswith("$."):
        raw = raw[2:]
    elif raw.startswith("$"):
        raw = raw[1:]
    return [part for part in raw.replace("[", ".").replace("]", "").split(".") if part]


def _has_header(headers: Dict[str, str], name: str) -> bool:
    return any(str(key).lower() == name.lower() for key in headers)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
