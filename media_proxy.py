"""
Media proxy routing helpers.

The first implementation only supports OpenAI-compatible pass-through. Vendor
adapters for providers such as Bailian or Ark must be added after their media
payloads and async task semantics are verified from official docs.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional


MEDIA_KIND_IMAGE = "image"
MEDIA_KIND_VIDEO = "video"


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
    enabled = [p for p in providers if isinstance(p, dict) and p.get("enabled", True)]
    if not enabled:
        return None

    prefix = ""
    upstream_model = str(model_id or "").strip()
    if "/" in upstream_model:
        prefix, upstream_model = upstream_model.split("/", 1)
        prefix = prefix.lower().strip()
        for provider in enabled:
            if str(provider.get("short_alias") or "").lower() == prefix or str(provider.get("id") or "").lower() == prefix:
                return provider
        return None

    if upstream_model:
        model_match = _provider_for_model(enabled, upstream_model, media_kind)
        if model_match:
            return model_match

    default_match = _default_media_provider(enabled, media_kind)
    if default_match:
        return default_match

    for provider in enabled:
        if provider_supports_media(provider, media_kind):
            return provider
    return None


def provider_supports_media(provider: Dict[str, Any], media_kind: str) -> bool:
    capability = "images" if media_kind == MEDIA_KIND_IMAGE else "videos"
    capabilities = provider.get("capabilities") if isinstance(provider.get("capabilities"), dict) else {}
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
        return {
            "can_forward": False,
            "error_type": "media_adapter_required",
            "message": (
                f"Provider '{provider.get('id')}' is configured as adapter-required for "
                f"{media_kind} media. Vendor media payload conversion is not implemented yet."
            ),
        }
    if not provider_supports_media(provider, media_kind):
        return {
            "can_forward": False,
            "error_type": "media_capability_unsupported",
            "message": f"Provider '{provider.get('id')}' is not configured for {media_kind} media requests.",
        }
    if not media_profile.get("openai_compatible_media") and str(provider.get("api_format") or "") not in {"openai_images", "openai_videos"}:
        return {
            "can_forward": False,
            "error_type": "media_adapter_required",
            "message": (
                f"Provider '{provider.get('id')}' has media capability enabled, but OpenAI-compatible "
                "media pass-through is not enabled."
            ),
        }
    return {"can_forward": True, "error_type": "", "message": ""}


def prepare_media_body(body: bytes, content_type: str, provider: Dict[str, Any]) -> bytes:
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


def _provider_for_model(providers: List[Dict[str, Any]], model_id: str, media_kind: str) -> Optional[Dict[str, Any]]:
    model_id_lower = model_id.lower().strip()
    capability = "images" if media_kind == MEDIA_KIND_IMAGE else "videos"
    fallback: Optional[Dict[str, Any]] = None
    for provider in providers:
        for model in provider.get("models", []):
            if not isinstance(model, dict) or not model.get("enabled", True):
                continue
            if str(model.get("id") or "").lower().strip() != model_id_lower:
                continue
            model_caps = model.get("capabilities") if isinstance(model.get("capabilities"), dict) else {}
            if model_caps.get(capability):
                return provider
            if fallback is None:
                fallback = provider
    return fallback


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
