"""
Media proxy routing helpers.

The first implementation only supports OpenAI-compatible pass-through. Vendor
adapters for providers such as Bailian or Ark must be added after their media
payloads and async task semantics are verified from official docs.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Dict, List, Optional

from approval_broker import (
    failure_decision,
    is_auto_approval_enabled,
    normalize_approval_action,
    parse_approval_decision,
)
from media_adapters import build_media_adapter_preview, summarize_media_adapter_preview
from capabilities import effective_provider_capabilities, merge_provider_model_capabilities
from providers import normalize_approval_profile


MEDIA_KIND_IMAGE = "image"
MEDIA_KIND_VIDEO = "video"
MEDIA_OPERATION_SUBMIT = "submit"
MEDIA_OPERATION_POLL = "poll"
MEDIA_OPERATION_CANCEL = "cancel"
MEDIA_OPERATION_UNKNOWN = "unknown"

MediaApprovalReviewer = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]


def canonical_media_path(path: str) -> str:
    """Normalize proxy path variants to an OpenAI-style endpoint path."""
    normalized = (path or "").split("?", 1)[0]
    for prefix in ("/codex/v1", "/v1/v1", "/v1"):
        if normalized == prefix:
            return "/"
        if normalized.startswith(prefix + "/"):
            return normalized[len(prefix):]
    return normalized


def media_kind_for_path(path: str) -> str:
    canonical = canonical_media_path(path)
    if canonical in {"/images/generations", "/images/edits", "/images/variations"}:
        return MEDIA_KIND_IMAGE
    if canonical == "/videos" or canonical.startswith("/videos/"):
        return MEDIA_KIND_VIDEO
    return ""


def media_operation_for_request(method: str, path: str) -> str:
    """Return submit/poll/cancel for known OpenAI-compatible media endpoints."""
    canonical = canonical_media_path(path)
    method = str(method or "").upper()
    if method == "POST" and canonical in {"/images/generations", "/images/edits", "/images/variations", "/videos"}:
        return MEDIA_OPERATION_SUBMIT
    if method == "GET" and canonical.startswith("/videos/"):
        return MEDIA_OPERATION_POLL
    if method == "DELETE" and canonical.startswith("/videos/"):
        return MEDIA_OPERATION_CANCEL
    return MEDIA_OPERATION_UNKNOWN


def is_media_proxy_path(path: str) -> bool:
    return bool(media_kind_for_path(path))


def media_endpoint_url(base_url: str, canonical_path: str) -> str:
    """Build an upstream OpenAI-compatible media URL."""
    base = str(base_url or "").strip().rstrip("/")
    path = canonical_media_path(canonical_path)
    if not base:
        return ""
    if base.lower().endswith(path.lower()):
        return base
    origin_only = "://" not in base or (base.split("://", 1)[1].count("/") == 0)
    if _has_version_suffix(base) or not origin_only:
        url = f"{base}{path}"
    else:
        url = f"{base}/v1{path}"
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def resolve_media_provider(
    providers: List[Dict[str, Any]],
    media_kind: str,
    model_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Resolve an enabled provider for an image/video request."""
    route = resolve_media_route(providers, media_kind, model_id=model_id)
    provider = route.get("provider")
    return provider if isinstance(provider, dict) else None


def resolve_media_route(
    providers: List[Dict[str, Any]],
    media_kind: str,
    model_id: str = "",
) -> Dict[str, Any]:
    """Resolve a provider and upstream model rewrite for a media request."""
    enabled = [p for p in providers if isinstance(p, dict) and p.get("enabled", True)]
    if not enabled:
        return {"provider": None, "upstream_model_id": "", "route_explanation": ["No enabled providers."]}

    prefix = ""
    upstream_model = str(model_id or "").strip()
    if "/" in upstream_model:
        prefix, upstream_model = upstream_model.split("/", 1)
        prefix = prefix.lower().strip()
        for provider in enabled:
            if str(provider.get("short_alias") or "").lower() == prefix or str(provider.get("id") or "").lower() == prefix:
                return {
                    "provider": provider,
                    "upstream_model_id": upstream_model,
                    "route_explanation": [f"Hard-routed by provider prefix '{prefix}'."],
                }
        return {"provider": None, "upstream_model_id": "", "route_explanation": [f"No provider matched prefix '{prefix}'."]}

    if upstream_model:
        override_match = _provider_for_model_override(enabled, upstream_model, media_kind)
        if override_match:
            provider, rewritten_model = override_match
            return {
                "provider": provider,
                "upstream_model_id": rewritten_model,
                "route_explanation": [
                    f"Matched {media_kind} model override '{upstream_model}' -> '{rewritten_model}' on provider '{provider.get('id')}'."
                ],
            }
        model_match = _provider_for_model(enabled, upstream_model, media_kind)
        if model_match:
            return {
                "provider": model_match,
                "upstream_model_id": upstream_model,
                "route_explanation": [f"Matched enabled provider model '{upstream_model}'."],
            }

    focused = next((p for p in enabled if p.get("focused") and provider_supports_media(p, media_kind)), None)
    if focused:
        return {
            "provider": focused,
            "upstream_model_id": upstream_model,
            "route_explanation": [f"Using focused {media_kind} provider '{focused.get('id')}'."],
        }

    default_match = _default_media_provider(enabled, media_kind)
    if default_match:
        return {
            "provider": default_match,
            "upstream_model_id": upstream_model,
            "route_explanation": [f"Using default {media_kind} provider '{default_match.get('id')}'."],
        }

    for provider in enabled:
        if provider_supports_media(provider, media_kind):
            return {
                "provider": provider,
                "upstream_model_id": upstream_model,
                "route_explanation": [f"Using first enabled provider with {media_kind} capability."],
            }
    return {
        "provider": None,
        "upstream_model_id": "",
        "route_explanation": [f"No provider supports {media_kind} media."],
    }


def provider_supports_media(provider: Dict[str, Any], media_kind: str) -> bool:
    capability = "images" if media_kind == MEDIA_KIND_IMAGE else "videos"
    capabilities = effective_provider_capabilities(provider)
    if capabilities.get(capability):
        return True
    api_format = str(provider.get("api_format") or "")
    if media_kind == MEDIA_KIND_IMAGE and api_format == "openai_images":
        return True
    if media_kind == MEDIA_KIND_VIDEO and api_format == "openai_videos":
        return True
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    if media_kind == MEDIA_KIND_IMAGE and media_profile.get("default_image_provider"):
        return True
    if media_kind == MEDIA_KIND_VIDEO and media_profile.get("default_video_provider"):
        return True
    return False


def media_forwarding_status(provider: Dict[str, Any], media_kind: str) -> Dict[str, Any]:
    """Return whether a media request can be forwarded without an adapter."""
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    if media_profile.get("adapter_required") and not media_profile.get("openai_compatible_media"):
        preview = build_media_adapter_preview(provider, media_kind)
        return {
            "can_forward": False,
            "error_type": "media_adapter_required",
            "message": summarize_media_adapter_preview(preview),
            "adapter_preview": preview,
            **_media_guidance(provider, media_kind, "media_adapter_required"),
        }
    if not provider_supports_media(provider, media_kind):
        return {
            "can_forward": False,
            "error_type": "media_capability_unsupported",
            "message": f"Provider '{provider.get('id')}' is not configured for {media_kind} media requests.",
            **_media_guidance(provider, media_kind, "media_capability_unsupported"),
        }
    if not media_profile.get("openai_compatible_media") and str(provider.get("api_format") or "") not in {"openai_images", "openai_videos"}:
        return {
            "can_forward": False,
            "error_type": "media_adapter_required",
            "message": (
                f"Provider '{provider.get('id')}' has media capability enabled, but OpenAI-compatible "
                "media pass-through is not enabled."
            ),
            **_media_guidance(provider, media_kind, "media_adapter_required"),
        }
    return {"can_forward": True, "error_type": "", "message": ""}


def build_media_route_readiness(provider: Dict[str, Any], model_id: str = "") -> Dict[str, Any]:
    """Preview whether OpenAI-compatible media proxy routes can be forwarded."""
    provider = provider if isinstance(provider, dict) else {}
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    capabilities = effective_provider_capabilities(provider)
    checks = [
        _media_route_readiness_check(provider, MEDIA_KIND_IMAGE, model_id),
        _media_route_readiness_check(provider, MEDIA_KIND_VIDEO, model_id),
    ]
    ready_checks = [item for item in checks if item.get("can_forward")]
    blocked_checks = [item for item in checks if not item.get("can_forward")]
    guidance_keys = _unique_truthy(item.get("guidance_key") for item in blocked_checks)
    action_keys = _unique_truthy(item.get("action_key") for item in blocked_checks)
    return {
        "success": True,
        "preview": True,
        "provider_id": str(provider.get("id") or ""),
        "api_format": str(provider.get("api_format") or ""),
        "base_url": str(provider.get("base_url") or ""),
        "capabilities": {
            "images": bool(capabilities.get("images")),
            "videos": bool(capabilities.get("videos")),
        },
        "media_profile": {
            "default_image_provider": bool(media_profile.get("default_image_provider")),
            "default_video_provider": bool(media_profile.get("default_video_provider")),
            "openai_compatible_media": bool(media_profile.get("openai_compatible_media")),
            "adapter_required": bool(media_profile.get("adapter_required")),
        },
        "live_forwarding_enabled": bool(ready_checks),
        "ready_count": len(ready_checks),
        "blocked_count": len(blocked_checks),
        "guidance_keys": guidance_keys,
        "action_keys": action_keys,
        "checks": checks,
    }


def build_media_approval_action(
    provider: Dict[str, Any],
    media_kind: str,
    operation: str,
    canonical_path: str,
    model_id: str = "",
    upstream_model_id: str = "",
    route_explanation: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a metadata-only Auto Approval action for media operations."""
    kind = "image_generation" if media_kind == MEDIA_KIND_IMAGE else "video_generation"
    provider_id = str(provider.get("id") or "")
    model = upstream_model_id or model_id
    return normalize_approval_action({
        "kind": kind,
        "summary": f"{media_kind or 'media'} {operation or MEDIA_OPERATION_UNKNOWN} via provider '{provider_id}'",
        "operation": operation or MEDIA_OPERATION_UNKNOWN,
        "provider_id": provider_id,
        "model": model,
        "upstream_model": upstream_model_id or model_id,
        "media_kind": media_kind,
        "endpoint": canonical_media_path(canonical_path),
        "route_explanation": "; ".join(route_explanation or []),
    })


def evaluate_media_approval(
    provider: Dict[str, Any],
    media_kind: str,
    operation: str,
    canonical_path: str,
    model_id: str = "",
    upstream_model_id: str = "",
    route_explanation: Optional[List[str]] = None,
    reviewer: Optional[MediaApprovalReviewer] = None,
) -> Dict[str, Any]:
    """
    Evaluate the provider Auto Approval policy for a media operation.

    The reviewer is injected so the proxy can later call the configured model
    without coupling media routing to a network client. No prompt/body content is
    included in the approval action.
    """
    profile = normalize_approval_profile(provider.get("approval_profile"))
    action = build_media_approval_action(
        provider,
        media_kind,
        operation,
        canonical_path,
        model_id=model_id,
        upstream_model_id=upstream_model_id,
        route_explanation=route_explanation,
    )
    if not is_auto_approval_enabled(profile):
        return {
            "required": False,
            "approved": True,
            "action": action,
            "decision": {
                "decision": "accept",
                "risk_level": "low",
                "reason": "Auto Approval is not enabled for this provider.",
                "scope": "request",
                "confidence": 1.0,
            },
            "error_type": "",
            "message": "",
        }

    try:
        if reviewer is None:
            if profile.get("mode_source") == "default":
                decision = {
                    "decision": "accept",
                    "risk_level": "low",
                    "reason": "Default Auto Approval is pending a connected reviewer; media request continues.",
                    "scope": "request",
                    "confidence": 1.0,
                    "policy_overrides": ["implicit_default_no_reviewer"],
                }
            else:
                decision = failure_decision("Auto Approval reviewer is not connected.", profile)
        else:
            decision = parse_approval_decision(reviewer(action, profile, provider), profile)
    except Exception as exc:
        decision = failure_decision(str(exc), profile)

    approved = decision.get("decision") == "accept"
    return {
        "required": True,
        "approved": approved,
        "action": action,
        "decision": decision,
        "error_type": "" if approved else "media_auto_approval_declined",
        "message": "" if approved else f"Auto Approval did not approve this media request: {decision.get('reason') or 'declined'}",
    }


def prepare_media_body(
    body: bytes,
    content_type: str,
    provider: Dict[str, Any],
    upstream_model_id: str = "",
) -> bytes:
    """Strip provider aliases from JSON media request model IDs."""
    if "application/json" not in str(content_type or "").lower():
        return body
    try:
        payload = json.loads(body.decode("utf-8", errors="replace")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    payload = copy.deepcopy(payload)
    if upstream_model_id:
        payload["model"] = upstream_model_id
    else:
        model = str(payload.get("model") or "")
        alias = str(provider.get("short_alias") or "").strip()
        if alias and model.startswith(alias + "/"):
            payload["model"] = model[len(alias) + 1:]
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def extract_json_model(body: bytes, content_type: str) -> str:
    if "application/json" not in str(content_type or "").lower():
        return ""
    try:
        payload = json.loads(body.decode("utf-8", errors="replace")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("model") or "")


def _media_route_readiness_check(provider: Dict[str, Any], media_kind: str, model_id: str = "") -> Dict[str, Any]:
    canonical_path = "/images/generations" if media_kind == MEDIA_KIND_IMAGE else "/videos"
    proxy_paths = (
        ["/v1/images/generations", "/v1/images/edits", "/v1/images/variations"]
        if media_kind == MEDIA_KIND_IMAGE
        else ["/v1/videos", "/v1/videos/{video_id}"]
    )
    status = media_forwarding_status(provider, media_kind)
    upstream_url = media_endpoint_url(str(provider.get("base_url") or ""), canonical_path)
    can_forward = bool(status.get("can_forward")) and bool(upstream_url)
    error_type = str(status.get("error_type") or "")
    message = str(status.get("message") or "")
    if status.get("can_forward") and not upstream_url:
        error_type = "media_base_url_missing"
        message = "Provider has media pass-through enabled, but base_url is empty."
        status = {
            **status,
            "guidance_key": "mediaBaseUrlNeededGuidance",
            "action_key": "mediaAddProviderUrlAction",
        }
    elif can_forward:
        message = f"OpenAI-compatible {media_kind} media pass-through is ready."

    result = {
        "media_kind": media_kind,
        "operation": MEDIA_OPERATION_SUBMIT,
        "can_forward": can_forward,
        "error_type": "" if can_forward else error_type,
        "message": message,
        "guidance_key": str(status.get("guidance_key") or ""),
        "action_key": str(status.get("action_key") or ""),
        "proxy_paths": proxy_paths,
        "canonical_path": canonical_path,
        "upstream_url": upstream_url,
        "model": str(model_id or ""),
        "route_mode": _media_route_mode(provider),
    }
    if status.get("adapter_preview"):
        result["adapter_preview"] = status["adapter_preview"]
    return result


def _media_guidance(provider: Dict[str, Any], media_kind: str, error_type: str) -> Dict[str, str]:
    capabilities = effective_provider_capabilities(provider)
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    api_format = str(provider.get("api_format") or "")
    has_any_media_capability = bool(
        capabilities.get("images")
        or capabilities.get("videos")
        or media_profile.get("default_image_provider")
        or media_profile.get("default_video_provider")
    )
    text_only = bool(capabilities.get("text")) and not has_any_media_capability

    if error_type == "media_capability_unsupported":
        if text_only:
            return {
                "guidance_key": "mediaTextProviderNeedsFallback",
                "action_key": "mediaConfigureMediaFallbackAction",
            }
        return {
            "guidance_key": "mediaCapabilityNeedsEnableOrFallback",
            "action_key": "mediaConfigureMediaFallbackAction",
        }

    if error_type == "media_adapter_required":
        if api_format == "openai_responses":
            return {
                "guidance_key": "mediaNativeResponsesNeedsMediaProxy",
                "action_key": "mediaConfirmNativeMediaProxyAction",
            }
        return {
            "guidance_key": "mediaAdapterNeedsSetup",
            "action_key": "mediaUseAdapterOrFallbackAction",
        }

    return {"guidance_key": "", "action_key": ""}


def _unique_truthy(values) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        item = str(value or "")
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result


def _media_route_mode(provider: Dict[str, Any]) -> str:
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    api_format = str(provider.get("api_format") or "")
    if media_profile.get("openai_compatible_media") or api_format in {"openai_images", "openai_videos"}:
        return "openai_compatible_pass_through"
    if media_profile.get("adapter_required"):
        return "adapter_required"
    return "disabled"


def _provider_for_model(providers: List[Dict[str, Any]], model_id: str, media_kind: str) -> Optional[Dict[str, Any]]:
    model_id_lower = model_id.lower().strip()
    capability = "images" if media_kind == MEDIA_KIND_IMAGE else "videos"
    for provider in providers:
        for model in provider.get("models", []):
            if not isinstance(model, dict) or not model.get("enabled", True):
                continue
            if str(model.get("id") or "").lower().strip() != model_id_lower:
                continue
            model_caps = merge_provider_model_capabilities(provider, model)
            if model_caps.get(capability):
                return provider
    return None


def _provider_for_model_override(
    providers: List[Dict[str, Any]],
    model_id: str,
    media_kind: str,
) -> Optional[tuple[Dict[str, Any], str]]:
    override_key = "image_model_overrides" if media_kind == MEDIA_KIND_IMAGE else "video_model_overrides"
    model_id_lower = model_id.lower().strip()
    for provider in providers:
        media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
        overrides = media_profile.get(override_key) if isinstance(media_profile.get(override_key), dict) else {}
        for public_model, upstream_model in overrides.items():
            if str(public_model).lower().strip() != model_id_lower:
                continue
            rewritten = str(upstream_model or "").strip()
            alias = str(provider.get("short_alias") or "").strip()
            if alias and rewritten.startswith(alias + "/"):
                rewritten = rewritten[len(alias) + 1:]
            return provider, rewritten or model_id
    return None


def _default_media_provider(providers: List[Dict[str, Any]], media_kind: str) -> Optional[Dict[str, Any]]:
    flag = "default_image_provider" if media_kind == MEDIA_KIND_IMAGE else "default_video_provider"
    for provider in providers:
        media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
        if media_profile.get(flag):
            return provider
    return None


def _has_version_suffix(base_url: str) -> bool:
    segment = base_url.split("/")[-1]
    return segment.startswith("v") and len(segment) > 1 and segment[1].isdigit()
