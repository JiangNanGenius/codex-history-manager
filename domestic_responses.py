"""
Domestic Responses compatibility profiles and dry-run probes.

This module is intentionally non-networking. It records vendor-specific
Responses API compatibility that has been verified from official docs and uses
that information to build local previews and proxy safety guards.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set


REDACTED_VALUE = "********"
DEFAULT_USER_AGENT = "Codex-Enhance-Manager/0.1"


DOMESTIC_RESPONSES_PROFILES: Dict[str, Dict[str, Any]] = {
    "alibaba_bailian": {
        "profile_id": "alibaba_bailian",
        "title": "Alibaba Bailian / DashScope Responses",
        "support_level": "partial_openai_compatible",
        "verified_docs_url": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
        "docs_verified_at": "2026-06-07",
        "endpoint_path": "/compatible-mode/v1/responses",
        "base_url_candidates": [
            "https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/responses",
        ],
        "verified_features": [
            "text_input_output",
            "streaming_response_output_text_delta",
            "response_completed_event",
            "previous_response_id",
            "input_image",
            "function_tools",
            "code_interpreter",
            "web_search",
            "mcp_tool",
            "json_mode",
            "structured_outputs",
            "prompt_templates",
            "response_crud_list",
        ],
        "partial_or_unsupported_features": [
            "background_mode",
            "remote_mcp_compatibility",
            "built_in_image_generation",
            "codex_custom_tools",
            "responses_compact",
            "media_generation_items",
        ],
        "allowed_tool_types": ["function", "web_search", "code_interpreter", "mcp"],
        "allowed_input_item_types": [
            "message",
            "function_call_output",
            "mcp_approval_response",
        ],
        "allowed_input_content_types": ["input_text", "output_text", "text", "input_image"],
        "blocking_tool_types": [
            "custom",
            "computer_use_preview",
            "file_search",
            "image_generation",
        ],
        "blocking_input_item_types": ["custom_tool_call", "custom_tool_call_output"],
        "blocking_input_content_types": ["input_audio", "input_file", "input_video"],
        "verified_event_types": ["response.output_text.delta", "response.completed"],
        "compatibility_notes": (
            "Official Bailian docs describe an OpenAI-compatible Responses API, "
            "but note that OpenAI-only unsupported parameters may be ignored. "
            "Codex custom tools, compact, and media-generation item routing still "
            "require adapter verification before real forwarding."
        ),
        "probe_notes": [
            "No network request is sent by the preview.",
            "Use the China or International compatible-mode endpoint according to the account region.",
        ],
    },
    "volcengine_ark": {
        "profile_id": "volcengine_ark",
        "title": "Volcengine Ark Responses",
        "support_level": "partial_official_responses",
        "verified_docs_url": "https://www.volcengine.com/docs/82379/1585128?lang=zh",
        "docs_verified_at": "2026-06-07",
        "endpoint_path": "/api/v3/responses",
        "base_url_candidates": ["https://ark.cn-beijing.volces.com/api/v3/responses"],
        "verified_features": [
            "text_input_output",
            "streaming_response_events",
            "previous_response_id",
            "response_retrieve_delete",
            "input_image",
            "function_tools",
            "web_search",
            "image_process_tool",
            "knowledge_search_tool",
            "structured_outputs",
        ],
        "partial_or_unsupported_features": [
            "codex_custom_tools",
            "responses_compact",
            "media_generation_items",
            "mcp_payload_details",
            "code_interpreter_payload_details",
            "file_input_payload_details",
        ],
        "allowed_tool_types": [
            "function",
            "web_search",
            "image_process",
            "knowledge_search",
        ],
        "allowed_input_item_types": ["message", "function_call_output"],
        "allowed_input_content_types": ["input_text", "output_text", "text", "input_image"],
        "blocking_tool_types": [
            "custom",
            "computer_use_preview",
            "file_search",
            "image_generation",
        ],
        "blocking_input_item_types": ["custom_tool_call", "custom_tool_call_output"],
        "blocking_input_content_types": ["input_audio", "input_file", "input_video"],
        "verified_event_types": [
            "response.created",
            "response.reasoning_summary_part.added",
            "response.reasoning_summary_text_delta",
        ],
        "compatibility_notes": (
            "Official Ark docs expose /api/v3/responses and examples for text, "
            "stateful conversation, streaming, function/web-search tools, image "
            "process, and knowledge search. Keep this profile partial because "
            "the full page is JS-heavy and exact Codex item compatibility still "
            "needs adapter probes."
        ),
        "probe_notes": [
            "No network request is sent by the preview.",
            "Some Ark tools require provider-specific beta headers configured in provider headers.",
        ],
    },
}


_PROFILE_ALIASES = {
    "alibaba": "alibaba_bailian",
    "alibaba_bailian": "alibaba_bailian",
    "aliyun": "alibaba_bailian",
    "bailian": "alibaba_bailian",
    "dashscope": "alibaba_bailian",
    "qwen": "alibaba_bailian",
    "ark": "volcengine_ark",
    "doubao": "volcengine_ark",
    "volcengine": "volcengine_ark",
    "volcengine_ark": "volcengine_ark",
    "volces": "volcengine_ark",
}


def resolve_domestic_responses_profile(provider: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve a domestic Responses profile from provider metadata."""
    provider = provider or {}
    response_profile = provider.get("responses_profile")
    response_profile = response_profile if isinstance(response_profile, dict) else {}

    profile_id = str(response_profile.get("profile_id") or "").strip().lower()
    profile_id = _PROFILE_ALIASES.get(profile_id, profile_id)
    if profile_id in DOMESTIC_RESPONSES_PROFILES:
        return _merge_profile_overrides(DOMESTIC_RESPONSES_PROFILES[profile_id], response_profile)

    haystack = " ".join(
        str(provider.get(key) or "")
        for key in ("id", "kind", "display_name", "short_alias", "base_url")
    ).lower()
    haystack += " " + str(response_profile.get("verified_docs_url") or "").lower()
    for marker, resolved_id in _PROFILE_ALIASES.items():
        if marker in haystack and resolved_id in DOMESTIC_RESPONSES_PROFILES:
            return _merge_profile_overrides(DOMESTIC_RESPONSES_PROFILES[resolved_id], response_profile)

    if response_profile.get("domestic_responses"):
        generic = {
            "profile_id": profile_id or "custom_domestic",
            "title": "Custom Domestic Responses",
            "support_level": "partial_custom",
            "verified_docs_url": str(response_profile.get("verified_docs_url") or ""),
            "docs_verified_at": "",
            "endpoint_path": "/v1/responses",
            "base_url_candidates": [],
            "verified_features": [],
            "partial_or_unsupported_features": list(response_profile.get("unsupported_fields") or []),
            "allowed_tool_types": ["function"],
            "allowed_input_item_types": ["message", "function_call_output"],
            "allowed_input_content_types": ["input_text", "output_text", "text"],
            "blocking_tool_types": ["custom", "computer_use_preview", "image_generation"],
            "blocking_input_item_types": ["custom_tool_call", "custom_tool_call_output"],
            "blocking_input_content_types": ["input_audio", "input_file", "input_image", "input_video"],
            "verified_event_types": [],
            "compatibility_notes": str(response_profile.get("compatibility_notes") or ""),
            "probe_notes": ["No network request is sent by the preview."],
        }
        return _merge_profile_overrides(generic, response_profile)

    return None


def assess_domestic_responses_request(
    provider: Dict[str, Any],
    request_json: Optional[Dict[str, Any]] = None,
    compact: bool = False,
) -> Dict[str, Any]:
    """Assess whether a domestic Responses request is safe to forward as-is."""
    profile = resolve_domestic_responses_profile(provider)
    if not profile:
        return {
            "domestic_responses": False,
            "safe_to_forward": True,
            "blocking_issues": [],
            "warnings": [],
        }

    request_json = request_json if isinstance(request_json, dict) else {}
    response_profile = provider.get("responses_profile")
    response_profile = response_profile if isinstance(response_profile, dict) else {}

    blocking_issues: List[str] = []
    warnings: List[str] = []
    if compact and response_profile.get("requires_adapter", True):
        blocking_issues.append("/responses/compact")

    if request_json.get("background") is True:
        blocking_issues.append("background mode")

    unsupported_tools = _unsupported_tool_types(request_json.get("tools"), profile)
    if unsupported_tools:
        blocking_issues.append(f"unsupported tool types: {', '.join(sorted(unsupported_tools))}")

    unsupported_items = _unsupported_input_items(request_json.get("input"), profile)
    if unsupported_items:
        blocking_issues.append(
            f"unsupported input item/content types: {', '.join(sorted(unsupported_items))}"
        )

    unverified_content = _unverified_input_content_types(request_json.get("input"), profile)
    if unverified_content:
        warnings.append(
            "unverified input content types: " + ", ".join(sorted(unverified_content))
        )

    if response_profile.get("partial_compatibility", True):
        warnings.append("provider is marked partial compatibility; route explanation/probe is required")

    return {
        "domestic_responses": True,
        "profile_id": profile.get("profile_id"),
        "title": profile.get("title"),
        "support_level": profile.get("support_level"),
        "verified_docs_url": profile.get("verified_docs_url"),
        "safe_to_forward": not blocking_issues,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "verified_features": list(profile.get("verified_features") or []),
        "partial_or_unsupported_features": list(profile.get("partial_or_unsupported_features") or []),
        "verified_event_types": list(profile.get("verified_event_types") or []),
    }


def format_domestic_unsupported_reason(report: Dict[str, Any]) -> str:
    """Format a proxy-facing error message from an assessment report."""
    issues = report.get("blocking_issues") or []
    docs_url = str(report.get("verified_docs_url") or "")
    return (
        "Domestic Responses compatibility is partial for this provider. "
        f"Blocked until adapter/probe verification: {'; '.join(str(item) for item in issues)}. "
        f"Docs: {docs_url or 'not configured'}"
    )


def build_domestic_responses_probe_preview(
    provider: Dict[str, Any],
    request_json: Optional[Dict[str, Any]] = None,
    compact: bool = False,
) -> Dict[str, Any]:
    """Build a dry-run preview of a domestic Responses request."""
    profile = resolve_domestic_responses_profile(provider)
    assessment = assess_domestic_responses_request(provider, request_json, compact=compact)
    if not profile:
        return {
            "available": False,
            "manual_only": True,
            "network_request_performed": False,
            "codex_mutation": False,
            "assessment": assessment,
            "message": "Provider is not marked as a domestic Responses provider.",
        }

    payload = _build_probe_payload(provider, request_json)
    return {
        "available": True,
        "manual_only": True,
        "network_request_performed": False,
        "codex_mutation": False,
        "method": "POST",
        "endpoint_url": domestic_responses_url(provider, profile),
        "headers_preview": _headers_preview(provider),
        "payload_preview": payload,
        "assessment": assessment,
        "profile": {
            "profile_id": profile.get("profile_id"),
            "title": profile.get("title"),
            "support_level": profile.get("support_level"),
            "verified_docs_url": profile.get("verified_docs_url"),
            "docs_verified_at": profile.get("docs_verified_at"),
            "verified_features": list(profile.get("verified_features") or []),
            "partial_or_unsupported_features": list(profile.get("partial_or_unsupported_features") or []),
            "verified_event_types": list(profile.get("verified_event_types") or []),
            "compatibility_notes": profile.get("compatibility_notes") or "",
            "probe_notes": list(profile.get("probe_notes") or []),
        },
    }


def domestic_responses_url(provider: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> str:
    """Build the native Responses endpoint URL for a domestic profile."""
    profile = profile or resolve_domestic_responses_profile(provider) or {}
    base_url = str((provider or {}).get("base_url") or "").strip().rstrip("/")
    if not base_url:
        candidates = profile.get("base_url_candidates") or []
        return str(candidates[0]) if candidates else ""
    if base_url.lower().endswith("/responses"):
        return base_url
    return f"{base_url}/responses"


def _merge_profile_overrides(base_profile: Dict[str, Any], response_profile: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base_profile)
    if response_profile.get("verified_docs_url"):
        merged["verified_docs_url"] = str(response_profile.get("verified_docs_url") or "")
    if response_profile.get("compatibility_notes"):
        merged["compatibility_notes"] = str(response_profile.get("compatibility_notes") or "")
    for key in (
        "allowed_tool_types",
        "allowed_input_item_types",
        "allowed_input_content_types",
        "verified_features",
        "partial_or_unsupported_features",
        "verified_event_types",
    ):
        value = response_profile.get(key)
        if isinstance(value, list):
            merged[key] = [str(item) for item in value]
    unsupported = response_profile.get("unsupported_fields")
    if isinstance(unsupported, list) and unsupported:
        existing = list(merged.get("partial_or_unsupported_features") or [])
        merged["partial_or_unsupported_features"] = existing + [str(item) for item in unsupported]
    return merged


def _unsupported_tool_types(tools: Any, profile: Dict[str, Any]) -> List[str]:
    if not isinstance(tools, list):
        return []
    allowed = _string_set(profile.get("allowed_tool_types"))
    blocked = _string_set(profile.get("blocking_tool_types"))
    unsupported: List[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip()
        if not tool_type:
            continue
        if tool_type in blocked or (allowed and tool_type not in allowed):
            unsupported.append(tool_type)
    return unsupported


def _unsupported_input_items(input_value: Any, profile: Dict[str, Any]) -> List[str]:
    unsupported: List[str] = []
    if not isinstance(input_value, list):
        return unsupported
    blocked_items = _string_set(profile.get("blocking_input_item_types"))
    blocked_content = _string_set(profile.get("blocking_input_content_types"))
    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type in blocked_items:
            unsupported.append(item_type)
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip()
                if part_type in blocked_content:
                    unsupported.append(part_type)
    return unsupported


def _unverified_input_content_types(input_value: Any, profile: Dict[str, Any]) -> List[str]:
    unverified: List[str] = []
    if not isinstance(input_value, list):
        return unverified
    allowed = _string_set(profile.get("allowed_input_content_types"))
    blocked = _string_set(profile.get("blocking_input_content_types"))
    if not allowed:
        return unverified
    for item in input_value:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip()
            if part_type and part_type not in allowed and part_type not in blocked:
                unverified.append(part_type)
    return unverified


def _build_probe_payload(provider: Dict[str, Any], request_json: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(request_json, dict) and request_json:
        payload = copy.deepcopy(request_json)
        if "model" in payload:
            payload["model"] = _strip_provider_alias(str(payload["model"]), provider)
        return payload
    return {
        "model": _first_model_id(provider),
        "input": "ping from Codex Enhance Manager dry-run probe",
        "stream": False,
    }


def _headers_preview(provider: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    raw_headers = (provider or {}).get("headers") if isinstance((provider or {}).get("headers"), dict) else {}
    for key, value in raw_headers.items():
        key_str = str(key)
        headers[key_str] = REDACTED_VALUE if _is_secret_header(key_str) else str(value)
    user_agent = str((provider or {}).get("user_agent") or headers.get("User-Agent") or DEFAULT_USER_AGENT).strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    auth_mode = str((provider or {}).get("auth_mode") or "provider_api_key")
    if auth_mode != "no_auth" and not any(key.lower() == "authorization" for key in headers):
        headers["Authorization"] = f"Bearer {REDACTED_VALUE}"
    return headers


def _first_model_id(provider: Dict[str, Any]) -> str:
    models = (provider or {}).get("models")
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and model.get("enabled", True) and model.get("id"):
                return str(model.get("id"))
    return "model-id"


def _strip_provider_alias(model_id: str, provider: Dict[str, Any]) -> str:
    alias = str((provider or {}).get("short_alias") or "").strip()
    if alias and model_id.startswith(alias + "/"):
        return model_id[len(alias) + 1 :]
    return model_id


def _is_secret_header(key: str) -> bool:
    key_lower = key.lower()
    return any(marker in key_lower for marker in ("api-key", "api_key", "authorization", "bearer", "token", "secret"))


def _string_set(value: Any) -> Set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def sanitize_domestic_responses_request(
    provider: Dict[str, Any],
    request_json: Dict[str, Any],
) -> tuple[Dict[str, Any], List[str]]:
    """Remove unsupported tools/input items so the request can still be forwarded.

    Returns a tuple of (sanitized_request, warnings).  When the provider has no
    domestic Responses profile the request is returned unchanged.
    """
    profile = resolve_domestic_responses_profile(provider)
    if not profile:
        return dict(request_json), []

    sanitized = dict(request_json)
    warnings: List[str] = []

    # ---- tools ---------------------------------------------------------------
    tools = sanitized.get("tools")
    if isinstance(tools, list):
        allowed = _string_set(profile.get("allowed_tool_types"))
        blocked = _string_set(profile.get("blocking_tool_types"))
        kept_tools: List[Dict[str, Any]] = []
        removed_tool_types: List[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                kept_tools.append(tool)
                continue
            tool_type = str(tool.get("type") or "").strip()
            if not tool_type:
                kept_tools.append(tool)
                continue
            if tool_type in blocked or (allowed and tool_type not in allowed):
                removed_tool_types.append(tool_type)
                continue
            kept_tools.append(tool)
        if removed_tool_types:
            sanitized["tools"] = kept_tools
            warnings.append(
                "removed unsupported tools: " + ", ".join(sorted(set(removed_tool_types)))
            )

    # ---- input items ---------------------------------------------------------
    input_value = sanitized.get("input")
    if isinstance(input_value, list):
        blocked_items = _string_set(profile.get("blocking_input_item_types"))
        blocked_content = _string_set(profile.get("blocking_input_content_types"))
        allowed_content = _string_set(profile.get("allowed_input_content_types"))
        kept_input: List[Dict[str, Any]] = []
        removed_item_types: List[str] = []
        removed_content_types: List[str] = []
        for item in input_value:
            if not isinstance(item, dict):
                kept_input.append(item)
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type in blocked_items:
                removed_item_types.append(item_type)
                continue
            content = item.get("content")
            if isinstance(content, list):
                kept_content: List[Dict[str, Any]] = []
                for part in content:
                    if not isinstance(part, dict):
                        kept_content.append(part)
                        continue
                    part_type = str(part.get("type") or "").strip()
                    if part_type in blocked_content or (
                        allowed_content and part_type not in allowed_content
                    ):
                        removed_content_types.append(part_type)
                        continue
                    kept_content.append(part)
                if kept_content:
                    new_item = dict(item)
                    new_item["content"] = kept_content
                    kept_input.append(new_item)
                else:
                    # All content parts removed – drop the whole item.
                    removed_item_types.append(item_type or "message")
                    continue
            else:
                kept_input.append(item)
        if removed_item_types or removed_content_types:
            sanitized["input"] = kept_input
            if removed_item_types:
                warnings.append(
                    "removed unsupported input items: "
                    + ", ".join(sorted(set(removed_item_types)))
                )
            if removed_content_types:
                warnings.append(
                    "removed unsupported content types: "
                    + ", ".join(sorted(set(removed_content_types)))
                )

    return sanitized, warnings
