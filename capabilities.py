from __future__ import annotations

from typing import Any, Dict


CAPABILITY_DEFAULTS: Dict[str, bool] = {
    "text": True,
    "vision": False,
    "tools": False,
    "custom_tools": False,
    "reasoning": False,
    "streaming": True,
    "compact": False,
    "images": False,
    "videos": False,
    "embeddings": False,
    "models": True,
    "balance": False,
    "quota": False,
    "native_approval": False,
}

NATIVE_RESPONSES_PROFILE_MODES = {"native", "native_responses", "native_proxy", "native_responses_proxy"}
NATIVE_FULL_CAPABILITY_EXCLUDED_KEYS = {"balance", "quota"}


def native_full_capabilities(data: Any = None) -> Dict[str, bool]:
    """Return the locked capability map for native Responses/Codex-login modes."""
    capabilities = normalize_capabilities(data)
    for key in CAPABILITY_DEFAULTS:
        capabilities[key] = key not in NATIVE_FULL_CAPABILITY_EXCLUDED_KEYS
    return normalize_capabilities(capabilities)


def responses_profile_mode(profile: Any) -> str:
    raw = profile if isinstance(profile, dict) else {}
    mode = str(
        raw.get("mode")
        or raw.get("responses_mode")
        or raw.get("connection_mode")
        or ""
    ).strip().lower()
    if mode in NATIVE_RESPONSES_PROFILE_MODES:
        return "native"
    if mode == "native":
        return "native"
    if bool(raw.get("native_responses") or raw.get("native_proxy") or raw.get("native_responses_proxy")):
        return "native"
    return "compatible"


def is_native_responses_provider(provider: Dict[str, Any]) -> bool:
    if not isinstance(provider, dict):
        return False
    return (
        str(provider.get("api_format") or "") == "openai_responses"
        and responses_profile_mode(provider.get("responses_profile")) == "native"
    )


def is_codex_login_provider(provider: Dict[str, Any]) -> bool:
    if not isinstance(provider, dict):
        return False
    return str(provider.get("auth_mode") or "") == "official_oauth" or bool(provider.get("codex_login"))


def has_locked_native_capabilities(provider: Dict[str, Any]) -> bool:
    return is_native_responses_provider(provider) or is_codex_login_provider(provider)


def normalize_capabilities(data: Any) -> Dict[str, bool]:
    """
    Normalize provider/model capabilities into a complete boolean map.

    Lists mean "these capabilities are enabled"; dicts preserve explicit True/False
    values. Unknown capability names are kept so third-party providers can expose
    custom flags without losing information.
    """
    defaults = dict(CAPABILITY_DEFAULTS)
    if isinstance(data, list):
        for item in data:
            key = str(item)
            defaults[key] = True
    elif isinstance(data, dict):
        for key, value in data.items():
            defaults[str(key)] = bool(value)
    return defaults


def normalize_capability_overrides(data: Any) -> Dict[str, bool]:
    """Return only explicitly supplied capability keys."""
    overrides: Dict[str, bool] = {}
    if isinstance(data, list):
        for item in data:
            overrides[str(item)] = True
    elif isinstance(data, dict):
        for key, value in data.items():
            overrides[str(key)] = bool(value)
    return overrides


def effective_provider_capabilities(provider: Dict[str, Any]) -> Dict[str, bool]:
    """
    Return provider capabilities plus capabilities implied by media routing.

    Media routes are configured in media_profile/api_format, while Catalog and
    AMR read capabilities. Keep those views aligned so a native image/video
    route is not displayed as unsupported just because the capability checkbox
    was not explicitly enabled.
    """
    if not isinstance(provider, dict):
        return normalize_capabilities(None)

    capabilities = normalize_capabilities(provider.get("capabilities"))
    api_format = str(provider.get("api_format") or "")
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}

    if has_locked_native_capabilities(provider):
        return native_full_capabilities(capabilities)

    if (
        api_format == "openai_images"
        or media_profile.get("default_image_provider")
        or bool(media_profile.get("image_model_overrides"))
    ):
        capabilities["images"] = True
    if (
        api_format == "openai_videos"
        or media_profile.get("default_video_provider")
        or bool(media_profile.get("video_model_overrides"))
    ):
        capabilities["videos"] = True

    return normalize_capabilities(capabilities)


def model_capability_overrides(model: Dict[str, Any]) -> Dict[str, bool]:
    """
    Extract model-level capability overrides.

    New normalized models store capability_overrides. For legacy stores that only
    contain the old fully-normalized capabilities map, keep only values that differ
    from defaults so default False/True values do not mask provider-level flags.
    Compact user-provided maps such as {"vision": False} still remain explicit.
    """
    if not isinstance(model, dict):
        return {}
    explicit = model.get("capability_overrides")
    if isinstance(explicit, (dict, list)):
        return normalize_capability_overrides(explicit)

    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return {}

    keys = {str(key) for key in capabilities.keys()}
    if set(CAPABILITY_DEFAULTS).issubset(keys):
        return {
            str(key): bool(value)
            for key, value in capabilities.items()
            if str(key) not in CAPABILITY_DEFAULTS or bool(value) != CAPABILITY_DEFAULTS[str(key)]
        }

    return normalize_capability_overrides(capabilities)


def merge_model_capabilities(provider_capabilities: Any, model: Dict[str, Any]) -> Dict[str, bool]:
    """Merge provider capabilities with explicit model-level overrides."""
    merged = normalize_capabilities(provider_capabilities)
    merged.update(model_capability_overrides(model))
    return normalize_capabilities(merged)


def merge_provider_model_capabilities(provider: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, bool]:
    """Merge effective provider capabilities with explicit model overrides."""
    if has_locked_native_capabilities(provider):
        return native_full_capabilities(provider.get("capabilities"))
    return merge_model_capabilities(effective_provider_capabilities(provider), model)
