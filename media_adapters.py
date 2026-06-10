"""
Source-backed media adapter previews and live image-generation backend.

Preview functions (build_media_adapter_preview*) remain metadata-only.
Live execution (execute_image_generation*) performs actual network calls
for the image-generation fallback pipeline.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


ADAPTER_ALIBABA_BAILIAN = "alibaba_bailian"
ADAPTER_VOLCENGINE_ARK = "volcengine_ark"

MEDIA_KIND_IMAGE = "image"
MEDIA_KIND_VIDEO = "video"

OPERATION_SUBMIT = "submit"
OPERATION_POLL = "poll"
OPERATION_CANCEL = "cancel"


def resolve_media_adapter_id(provider: Dict[str, Any]) -> str:
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    explicit = str(media_profile.get("adapter") or "").strip()
    if explicit:
        return explicit

    identity = " ".join(
        str(provider.get(key) or "").lower()
        for key in ("id", "kind", "display_name", "short_alias")
    )
    if "bailian" in identity or "dashscope" in identity or "alibaba" in identity:
        return ADAPTER_ALIBABA_BAILIAN
    if "volcengine" in identity or "ark" in identity or "volces" in identity:
        return ADAPTER_VOLCENGINE_ARK
    return ""


def build_media_adapter_preview(
    provider: Dict[str, Any],
    media_kind: str,
    operation: str = OPERATION_SUBMIT,
    model_id: str = "",
    upstream_model_id: str = "",
    request_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a metadata-only preview for an adapter-required media route."""
    adapter_id = resolve_media_adapter_id(provider)
    if adapter_id == ADAPTER_VOLCENGINE_ARK:
        preview = _volcengine_ark_preview(media_kind, operation)
    elif adapter_id == ADAPTER_ALIBABA_BAILIAN:
        preview = _alibaba_bailian_preview(media_kind, operation)
    else:
        preview = _unknown_adapter_preview(media_kind, operation)
        preview["adapter_id"] = adapter_id

    payload_fields = sorted(str(k) for k in (request_json or {}).keys())
    preview.update({
        "provider_id": str(provider.get("id") or ""),
        "adapter_id": adapter_id or preview.get("adapter_id", ""),
        "media_kind": media_kind,
        "operation": operation,
        "model": upstream_model_id or model_id,
        "request_fields": payload_fields,
        "live_forwarding_enabled": False,
    })
    if "prompt" in payload_fields:
        preview.setdefault("redacted_fields", []).append("prompt")
    return preview


def build_media_adapter_preview_bundle(
    provider: Dict[str, Any],
    request_json: Optional[Dict[str, Any]] = None,
    media_kind: str = "",
    model_id: str = "",
) -> Dict[str, Any]:
    """Return all metadata-only adapter previews useful for a provider draft."""
    normalized_kind = str(media_kind or "").strip().lower()
    media_kinds = [normalized_kind] if normalized_kind in {MEDIA_KIND_IMAGE, MEDIA_KIND_VIDEO} else [MEDIA_KIND_IMAGE, MEDIA_KIND_VIDEO]
    operations = {
        MEDIA_KIND_IMAGE: [OPERATION_SUBMIT],
        MEDIA_KIND_VIDEO: [OPERATION_SUBMIT, OPERATION_POLL, OPERATION_CANCEL],
    }
    request = request_json if isinstance(request_json, dict) else {}
    request_model = str(request.get("model") or model_id or "").strip()
    previews: List[Dict[str, Any]] = []
    for kind in media_kinds:
        for operation in operations.get(kind, [OPERATION_SUBMIT]):
            preview = build_media_adapter_preview(
                provider,
                kind,
                operation,
                model_id=request_model,
                upstream_model_id=request_model,
                request_json=request,
            )
            preview["summary"] = summarize_media_adapter_preview(preview)
            previews.append(preview)

    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    return {
        "success": True,
        "preview": True,
        "provider_id": str(provider.get("id") or ""),
        "adapter_id": resolve_media_adapter_id(provider),
        "adapter_required": bool(media_profile.get("adapter_required")),
        "openai_compatible_media": bool(media_profile.get("openai_compatible_media")),
        "live_forwarding_enabled": False,
        "previews": previews,
    }


def summarize_media_adapter_preview(preview: Dict[str, Any]) -> str:
    adapter_id = str(preview.get("adapter_id") or "unknown")
    endpoint = preview.get("endpoint") if isinstance(preview.get("endpoint"), dict) else {}
    endpoint_text = ""
    if endpoint.get("method") and endpoint.get("path"):
        endpoint_text = f" Verified endpoint preview: {endpoint.get('method')} {endpoint.get('path')}."
    blockers = preview.get("blockers") if isinstance(preview.get("blockers"), list) else []
    blocker_text = " ".join(str(item) for item in blockers[:2])
    if blocker_text:
        blocker_text = f" Blocked: {blocker_text}"
    return (
        f"Provider requires media adapter '{adapter_id}'."
        f"{endpoint_text} Live vendor media conversion is not enabled yet."
        f"{blocker_text}"
    ).strip()


def _volcengine_ark_preview(media_kind: str, operation: str) -> Dict[str, Any]:
    docs = [
        "https://www.volcengine.com/docs/82379/1541523?lang=zh",
        "https://www.volcengine.com/docs/82379/1520757?lang=zh",
        "https://www.volcengine.com/docs/82379/1521309?lang=zh",
        "https://www.volcengine.com/docs/82379/1521720?lang=zh",
    ]
    if media_kind == MEDIA_KIND_IMAGE:
        return {
            "adapter_id": ADAPTER_VOLCENGINE_ARK,
            "supported": operation == OPERATION_SUBMIT,
            "endpoint": {"method": "POST", "path": "/images/generations"},
            "async": False,
            "poll_required": False,
            "cancel_supported": False,
            "request_shape": {
                "required": ["model", "prompt"],
                "optional": ["image", "size", "output_format", "response_format", "watermark"],
            },
            "response_shape": {"openai_like_data": True, "normalization_required": True},
            "docs_urls": docs[:1],
            "source_status": "official_docs_partial_html_plus_search_snippet",
            "blockers": [
                "Seedream response normalization and error mapping still need live/mock adapter verification.",
                "Only image generation is previewed; edits and variations remain blocked until documented.",
            ],
        }
    if media_kind == MEDIA_KIND_VIDEO:
        endpoint = {"method": "POST", "path": "/contents/generations/tasks"}
        if operation == OPERATION_POLL:
            endpoint = {"method": "GET", "path": "/contents/generations/tasks/{task_id}"}
        elif operation == OPERATION_CANCEL:
            endpoint = {"method": "DELETE", "path": "/contents/generations/tasks/{task_id}"}
        return {
            "adapter_id": ADAPTER_VOLCENGINE_ARK,
            "supported": operation in {OPERATION_SUBMIT, OPERATION_POLL, OPERATION_CANCEL},
            "endpoint": endpoint,
            "async": True,
            "poll_required": True,
            "cancel_supported": True,
            "request_shape": {
                "submit_required": ["model", "content"],
                "content_item_types": ["text", "image_url", "video_url", "audio_url"],
            },
            "response_shape": {
                "submit_id_field": "id",
                "poll_result_video_url": "content.video_url",
                "task_id_retention": "7 days per official docs snippet",
            },
            "docs_urls": docs[1:],
            "source_status": "official_docs_index_plus_search_snippet",
            "blockers": [
                "OpenAI /v1/videos payload to Ark content-generation task conversion is not implemented.",
                "Task status and video_url normalization still need verified mock/live fixtures.",
            ],
        }
    return _unsupported_kind_preview(ADAPTER_VOLCENGINE_ARK, media_kind, operation, docs)


def _alibaba_bailian_preview(media_kind: str, operation: str) -> Dict[str, Any]:
    docs = [
        "https://help.aliyun.com/zh/model-studio/qwen-image-api",
        "https://help.aliyun.com/zh/model-studio/image-generation/",
    ]
    if media_kind == MEDIA_KIND_IMAGE:
        return {
            "adapter_id": ADAPTER_ALIBABA_BAILIAN,
            "supported": operation == OPERATION_SUBMIT,
            "endpoint": {
                "method": "POST",
                "path": "/api/v1/services/aigc/multimodal-generation/generation",
            },
            "async": False,
            "poll_required": False,
            "cancel_supported": False,
            "request_shape": {
                "required": ["model", "input.messages[0].role=user", "input.messages[0].content[].text"],
                "optional": ["parameters.negative_prompt", "parameters.prompt_extend", "parameters.watermark", "parameters.size", "parameters.n", "parameters.seed"],
            },
            "response_shape": {
                "image_url": "output.choices[].message.content[].image",
                "usage": ["usage.image_count", "usage.width", "usage.height"],
            },
            "docs_urls": docs,
            "source_status": "official_docs_http_shape",
            "blockers": [
                "OpenAI Images payload to DashScope multimodal-generation conversion is not implemented.",
                "DashScope response to OpenAI Images response normalization needs mock/live fixtures.",
            ],
        }
    return _unsupported_kind_preview(ADAPTER_ALIBABA_BAILIAN, media_kind, operation, docs)


def _unknown_adapter_preview(media_kind: str, operation: str) -> Dict[str, Any]:
    return {
        "adapter_id": "",
        "supported": False,
        "endpoint": {},
        "async": False,
        "poll_required": False,
        "cancel_supported": False,
        "request_shape": {},
        "response_shape": {},
        "docs_urls": [],
        "source_status": "unknown",
        "blockers": [f"No verified media adapter profile exists for {media_kind} {operation}."],
    }


def _unsupported_kind_preview(adapter_id: str, media_kind: str, operation: str, docs_urls: List[str]) -> Dict[str, Any]:
    return {
        "adapter_id": adapter_id,
        "supported": False,
        "endpoint": {},
        "async": False,
        "poll_required": False,
        "cancel_supported": False,
        "request_shape": {},
        "response_shape": {},
        "docs_urls": docs_urls,
        "source_status": "unsupported",
        "blockers": [f"{adapter_id} {media_kind} {operation} is not enabled until official media docs are verified."],
    }


# ─────────────── Live Image Generation Backend ───────────────


def execute_image_generation(
    provider: Dict[str, Any],
    prompt: str,
    size: str = "",
    quality: str = "",
    n: int = 1,
    upstream_model_id: str = "",
) -> Dict[str, Any]:
    """Unified image generation backend.

    Returns a dict with either:
        {"success": True, "data": [{"url": ..., "b64_json": ...}]}
    or:
        {"success": False, "error": "..."}
    """
    # 若指定了 upstream_model_id，临时覆盖 provider 的 model 字段
    if upstream_model_id:
        provider = dict(provider)
        provider["model"] = upstream_model_id
    adapter_id = resolve_media_adapter_id(provider)
    if adapter_id == ADAPTER_ALIBABA_BAILIAN:
        return _call_alibaba_bailian_image_generation(provider, prompt, size, n)
    if adapter_id == ADAPTER_VOLCENGINE_ARK:
        return _call_volcengine_ark_image_generation(provider, prompt, size, n)
    api_format = str(provider.get("api_format") or "")
    if api_format in ("openai_images", "openai_chat", "openai_responses", "custom"):
        return _call_openai_compatible_image_generation(provider, prompt, size, quality, n)
    return {"success": False, "error": f"No image generation adapter available for provider format '{api_format}'."}


def _provider_base_url(provider: Dict[str, Any]) -> str:
    return str(provider.get("base_url") or "").rstrip("/")


def _provider_api_key(provider: Dict[str, Any]) -> str:
    return str(provider.get("api_key") or "")


def _provider_headers(provider: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    raw_headers = provider.get("headers")
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            headers[str(key)] = str(value)
    api_key = _provider_api_key(provider)
    auth_mode = str(provider.get("auth_mode") or "provider_api_key")
    if auth_mode != "no_auth" and api_key and not any(
        k.lower() == "authorization" for k in headers
    ):
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _call_openai_compatible_image_generation(
    provider: Dict[str, Any],
    prompt: str,
    size: str,
    quality: str,
    n: int,
) -> Dict[str, Any]:
    base_url = _provider_base_url(provider)
    if not base_url:
        return {"success": False, "error": "Provider has no base_url configured."}
    url = f"{base_url}/images/generations"
    headers = _provider_headers(provider)
    payload: Dict[str, Any] = {"model": provider.get("model", ""), "prompt": prompt}
    if size:
        payload["size"] = size
    if quality:
        payload["quality"] = quality
    if n > 0:
        payload["n"] = n
    try:
        resp = _post_json(url, headers, payload)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"success": False, "error": f"Image generation request failed: {exc}"}
    data = resp.get("data") if isinstance(resp.get("data"), list) else []
    normalized: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry: Dict[str, str] = {}
        if item.get("url"):
            entry["url"] = str(item["url"])
        if item.get("b64_json"):
            entry["b64_json"] = str(item["b64_json"])
        normalized.append(entry)
    if not normalized:
        return {"success": False, "error": "Image generation returned no image data."}
    return {"success": True, "data": normalized}


def _call_alibaba_bailian_image_generation(
    provider: Dict[str, Any],
    prompt: str,
    size: str,
    n: int,
) -> Dict[str, Any]:
    base_url = _provider_base_url(provider)
    if not base_url:
        return {"success": False, "error": "Provider has no base_url configured."}
    url = f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"
    headers = _provider_headers(provider)
    payload: Dict[str, Any] = {
        "model": provider.get("model", "wanx-v1"),
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": {},
    }
    if size:
        payload["parameters"]["size"] = size
    if n > 0:
        payload["parameters"]["n"] = n
    try:
        resp = _post_json(url, headers, payload)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"success": False, "error": f"Alibaba image generation request failed: {exc}"}
    output = resp.get("output") if isinstance(resp.get("output"), dict) else {}
    choices = output.get("choices") if isinstance(output.get("choices"), list) else []
    normalized: List[Dict[str, str]] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        for part in content:
            if not isinstance(part, dict):
                continue
            image_b64 = part.get("image")
            if image_b64:
                normalized.append({"b64_json": str(image_b64)})
    if not normalized:
        return {"success": False, "error": "Alibaba image generation returned no image data."}
    return {"success": True, "data": normalized}


def _call_volcengine_ark_image_generation(
    provider: Dict[str, Any],
    prompt: str,
    size: str,
    n: int,
) -> Dict[str, Any]:
    base_url = _provider_base_url(provider)
    if not base_url:
        return {"success": False, "error": "Provider has no base_url configured."}
    url = f"{base_url}/images/generations"
    headers = _provider_headers(provider)
    payload: Dict[str, Any] = {"model": provider.get("model", ""), "prompt": prompt}
    if size:
        payload["size"] = size
    if n > 0:
        payload["n"] = n
    try:
        resp = _post_json(url, headers, payload)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"success": False, "error": f"Volcengine image generation request failed: {exc}"}
    data = resp.get("data") if isinstance(resp.get("data"), list) else []
    normalized: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry: Dict[str, str] = {}
        if item.get("url"):
            entry["url"] = str(item["url"])
        if item.get("b64_json"):
            entry["b64_json"] = str(item["b64_json"])
        normalized.append(entry)
    if not normalized:
        return {"success": False, "error": "Volcengine image generation returned no image data."}
    return {"success": True, "data": normalized}
