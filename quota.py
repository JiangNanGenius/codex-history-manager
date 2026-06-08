"""
Generic provider balance/quota probes.

This module implements provider-configured probes only. Provider-specific
official quota endpoints can be layered on top later after their auth and
response shapes are verified.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from providers import is_secret_key


DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_TTL_SECONDS = 300
REDACTED_VALUE = "********"
SUPPORTED_QUOTA_PROBE_TYPES = {"http_json", "script", "manual", "unsupported"}
SCRIPT_LANGUAGES = {"javascript", "js"}


JS_QUOTA_RUNNER = r"""
const fs = require("fs");
const vm = require("vm");

function fail(message) {
  process.stderr.write(String(message || "quota script failed"));
  process.exit(2);
}

let input;
try {
  input = JSON.parse(fs.readFileSync(0, "utf8"));
} catch (err) {
  fail("invalid runner input");
}

const timeout = Math.max(1, Number(input.timeoutMs || 1000));
const context = vm.createContext(Object.create(null));
try {
  context.__response = input.response === undefined ? null : input.response;
  const source = `
    const __factory = (${input.code || ""});
    const __probe = typeof __factory === "function" ? __factory() : __factory;
    if (!__probe || typeof __probe !== "object") {
      throw new Error("quota script must return an object");
    }
    if (${JSON.stringify(input.phase)} === "request") {
      JSON.stringify(__probe.request || __probe);
    } else {
      const __extractor = __probe.extractor;
      const __value = typeof __extractor === "function"
        ? __extractor(__response)
        : (__extractor || {});
      JSON.stringify(__value === undefined ? {} : __value);
    }
  `;
  const output = new vm.Script(source).runInContext(context, { timeout });
  process.stdout.write(String(output || "{}"));
} catch (err) {
  fail(err && err.message ? err.message : "quota script execution failed");
}
"""


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
    probe_type = _normalize_probe_type(raw)
    method = str(raw.get("method") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        method = "GET"
    timeout = _int(raw.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS)
    auto_interval = _int(raw.get("auto_query_interval_minutes"), 0)
    ttl_default = auto_interval * 60 if auto_interval > 0 else DEFAULT_TTL_SECONDS
    ttl = _int(raw.get("ttl_seconds"), ttl_default)
    script = normalize_quota_script(raw)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "type": probe_type,
        "probe_type": probe_type,
        "method": method,
        "url": str(raw.get("url") or raw.get("endpoint") or "").strip(),
        "headers": raw.get("headers") if isinstance(raw.get("headers"), dict) else {},
        "body": raw.get("body") if isinstance(raw.get("body"), dict) else None,
        "json_paths": normalize_json_paths(raw.get("json_paths")),
        "timeout_seconds": max(timeout, 1),
        "ttl_seconds": max(ttl, 1),
        "auto_query_interval_minutes": max(auto_interval, 0),
        "script": script,
        "note": str(raw.get("note") or ""),
    }


def normalize_quota_script(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    script = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    language = str(script.get("language") or raw.get("language") or "javascript").strip().lower()
    if language not in SCRIPT_LANGUAGES:
        language = "javascript"
    code = str(script.get("code") or raw.get("code") or "").strip()
    return {
        "language": language,
        "code": code,
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
    probe_type = str(quota_check.get("type") or quota_check.get("probe_type") or "http_json")
    if probe_type == "script":
        return run_script_quota_probe(provider, quota_check)
    if probe_type in {"manual", "unsupported"}:
        provider_id = str(provider.get("id") or "")
        return _failure_snapshot(
            provider_id,
            f"Quota probe type '{probe_type}' is not executable automatically.",
            quota_check,
        )
    return run_http_json_quota_probe(provider, quota_check)


def run_http_json_quota_probe(provider: Dict[str, Any], quota_check: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(provider.get("id") or "")
    url = render_quota_templates(quota_check.get("url") or "", provider)
    if not url:
        return _failure_snapshot(provider_id, "Quota check URL is empty.", quota_check)

    headers = build_quota_headers(provider, quota_check)
    try:
        status, payload, raw_body = perform_quota_http_request(
            url=url,
            method=str(quota_check.get("method") or "GET"),
            headers=headers,
            body=render_quota_templates(quota_check.get("body"), provider),
            timeout_seconds=quota_check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
    except urllib.error.HTTPError as exc:
        raw_body = exc.read() if exc.fp else b""
        return _failure_snapshot(provider_id, f"HTTP {exc.code}", quota_check, status_code=exc.code, raw_body=raw_body)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return _failure_snapshot(provider_id, f"Quota request failed: {exc}", quota_check)
    except json.JSONDecodeError:
        return _failure_snapshot(
            provider_id,
            "Quota response is not valid JSON.",
            quota_check,
            status_code=status if "status" in locals() else 0,
            raw_body=raw_body if "raw_body" in locals() else b"",
        )

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


def run_script_quota_probe(provider: Dict[str, Any], quota_check: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(provider.get("id") or "")
    script = quota_check.get("script") if isinstance(quota_check.get("script"), dict) else {}
    language = str(script.get("language") or "javascript").strip().lower()
    code = str(script.get("code") or "").strip()
    if language not in SCRIPT_LANGUAGES:
        return _failure_snapshot(provider_id, f"Unsupported quota script language: {language}", quota_check)
    if not code:
        return _failure_snapshot(provider_id, "Quota script is empty.", quota_check)

    try:
        request_config = run_js_quota_script_phase(
            code,
            phase="request",
            response=None,
            timeout_seconds=quota_check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
    except QuotaError as exc:
        return _failure_snapshot(provider_id, str(exc), quota_check)

    if not isinstance(request_config, dict):
        return _failure_snapshot(provider_id, "Quota script request must be an object.", quota_check)
    url = render_quota_templates(
        request_config.get("url") or request_config.get("endpoint") or quota_check.get("url") or "",
        provider,
    )
    if not url:
        return _failure_snapshot(provider_id, "Quota script request URL is empty.", quota_check)
    method = str(request_config.get("method") or quota_check.get("method") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        method = "GET"

    script_headers = request_config.get("headers") if isinstance(request_config.get("headers"), dict) else {}
    headers = build_quota_headers(provider, {**quota_check, "headers": script_headers})
    body = request_config.get("body") if "body" in request_config else quota_check.get("body")
    body = render_quota_templates(body, provider)

    try:
        status, payload, raw_body = perform_quota_http_request(
            url=url,
            method=method,
            headers=headers,
            body=body,
            timeout_seconds=quota_check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
    except urllib.error.HTTPError as exc:
        raw_body = exc.read() if exc.fp else b""
        return _failure_snapshot(provider_id, f"HTTP {exc.code}", quota_check, status_code=exc.code, raw_body=raw_body)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return _failure_snapshot(provider_id, f"Quota request failed: {exc}", quota_check)
    except json.JSONDecodeError:
        return _failure_snapshot(
            provider_id,
            "Quota response is not valid JSON.",
            quota_check,
            status_code=status if "status" in locals() else 0,
            raw_body=raw_body if "raw_body" in locals() else b"",
        )

    try:
        extracted = run_js_quota_script_phase(
            code,
            phase="extract",
            response=payload,
            timeout_seconds=quota_check.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        )
    except QuotaError as exc:
        return _failure_snapshot(provider_id, str(exc), quota_check, status_code=status, raw_body=raw_body)
    if not isinstance(extracted, dict):
        extracted = {"value": extracted}

    return {
        "success": True,
        "provider_id": provider_id,
        "enabled": True,
        "type": "script",
        "status_code": status,
        "fetched_at": _now_iso(),
        "ttl_seconds": quota_check.get("ttl_seconds", DEFAULT_TTL_SECONDS),
        "values": redact_quota_result(extracted),
        "request_redacted": redact_quota_result({"method": method, "url": url, "headers": headers}),
        "raw_redacted": redact_quota_result(payload),
        "note": quota_check.get("note") or "",
    }


def perform_quota_http_request(
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Any,
    timeout_seconds: int,
) -> Tuple[int, Any, bytes]:
    rendered_headers = {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if value is not None
    }
    body_bytes = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            body_bytes = bytes(body)
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            rendered_headers["Content-Type"] = rendered_headers.get("Content-Type") or "application/json"
    request = urllib.request.Request(
        url,
        data=body_bytes,
        headers=rendered_headers,
        method=str(method or "GET").upper(),
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as resp:
        raw_body = resp.read()
        status = int(resp.getcode() or 200)
    payload = json.loads(raw_body.decode("utf-8", errors="replace")) if raw_body else {}
    return status, payload, raw_body


def run_js_quota_script_phase(
    code: str,
    phase: str,
    response: Any,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        raise QuotaError("JavaScript quota scripts require Node.js on PATH.")
    timeout_ms = max(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), 1) * 1000
    payload = {
        "code": code,
        "phase": phase,
        "response": response,
        "timeoutMs": timeout_ms,
    }
    try:
        completed = subprocess.run(
            [node, "-e", JS_QUOTA_RUNNER],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=max(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), 1) + 1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QuotaError(f"JavaScript quota script failed during {phase}: {exc}") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or "execution failed"
        raise QuotaError(f"JavaScript quota script failed during {phase}: {_safe_error_text(message)}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise QuotaError(f"JavaScript quota script returned invalid JSON during {phase}") from exc


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
            headers[str(key)] = render_quota_templates(value, provider)
    override = quota_check.get("headers") if isinstance(quota_check.get("headers"), dict) else {}
    for key, value in override.items():
        if isinstance(value, str):
            headers[str(key)] = render_quota_templates(value, provider)
    user_agent = str(provider.get("user_agent") or configured.get("User-Agent") or "Codex-Enhance-Manager-Quota/1.0")
    headers["User-Agent"] = headers.get("User-Agent") or user_agent

    api_key = str(provider.get("secondary_usage_key") or provider.get("api_key") or "")
    if api_key and not _has_header(headers, "Authorization") and not _has_header(headers, "x-api-key"):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def render_quota_templates(value: Any, provider: Dict[str, Any]) -> Any:
    mapping = {
        "providerId": str(provider.get("id") or ""),
        "providerName": str(provider.get("display_name") or provider.get("id") or ""),
        "baseUrl": str(provider.get("base_url") or "").rstrip("/"),
        "apiKey": str(provider.get("secondary_usage_key") or provider.get("api_key") or ""),
        "secondaryUsageKey": str(provider.get("secondary_usage_key") or ""),
    }
    if isinstance(value, str):
        text = value
        for key, item in mapping.items():
            text = text.replace("{{" + key + "}}", item)
        return text
    if isinstance(value, dict):
        return {key: render_quota_templates(item, provider) for key, item in value.items()}
    if isinstance(value, list):
        return [render_quota_templates(item, provider) for item in value]
    return value


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
        "type": quota_check.get("type") or quota_check.get("probe_type") or "http_json",
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


def _normalize_probe_type(raw: Dict[str, Any]) -> str:
    probe_type = str(raw.get("type") or raw.get("probe_type") or raw.get("kind") or "http_json").strip().lower()
    if raw.get("code") or raw.get("script") or probe_type in {"js", "javascript"}:
        probe_type = "script"
    if probe_type in {"http", "json", "http-json"}:
        probe_type = "http_json"
    if probe_type not in SUPPORTED_QUOTA_PROBE_TYPES:
        probe_type = "http_json"
    return probe_type


def _has_header(headers: Dict[str, str], name: str) -> bool:
    return any(str(key).lower() == name.lower() for key in headers)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_error_text(value: Any) -> str:
    text = str(value or "")
    if len(text) > 300:
        text = text[:297] + "..."
    return text.replace("\r", " ").replace("\n", " ")
