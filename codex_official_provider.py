"""Derived Codex official-login provider helpers.

This module turns the real Codex config/auth state into a read-only provider
entry for UI switching and AMR candidates. It never writes or exposes OAuth
tokens, and it does not make the local proxy use the official login as an API
key.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from codex_config import detect_auth_mode
from providers import normalize_provider

DEFAULT_CODEX_PROVIDER = "openai"
DEFAULT_CODEX_MODEL = "gpt-5"
OFFICIAL_PROVIDER_ID = "codex_official"
OFFICIAL_PROVIDER_ALIAS = "codex"


def _first_config_value(config_data: Dict[str, Any], keys: tuple[str, ...]) -> str:
    if not isinstance(config_data, dict):
        return ""
    for key in keys:
        value = config_data.get(key)
        if value:
            return str(value)
    defaults = config_data.get("defaults")
    if isinstance(defaults, dict):
        for key in keys:
            value = defaults.get(key)
            if value:
                return str(value)
    return ""


def resolve_effective_codex_settings(config_data: Dict[str, Any], auth_mode: str = "") -> Dict[str, Any]:
    raw_provider = _first_config_value(config_data, ("model_provider", "modelProvider", "provider"))
    raw_model = _first_config_value(config_data, ("model",))
    official = str(auth_mode or "").strip() == "official_oauth"

    provider = raw_provider
    provider_source = "config" if provider else ""
    if not provider and official:
        provider = DEFAULT_CODEX_PROVIDER
        provider_source = "official_oauth"

    model = raw_model or DEFAULT_CODEX_MODEL
    model_source = "config" if raw_model else "default"

    return {
        "model_provider": provider,
        "model": model,
        "raw_model_provider": raw_provider,
        "raw_model": raw_model,
        "model_provider_source": provider_source or ("default" if provider else "missing"),
        "model_source": model_source,
        "official_oauth_implied_provider": official and not raw_provider,
    }


def build_official_login_provider(
    config_data: Dict[str, Any],
    auth_data: Dict[str, Any],
    allow_placeholder: bool = False,
) -> Optional[Dict[str, Any]]:
    auth_mode = detect_auth_mode(auth_data if isinstance(auth_data, dict) else {})
    official_oauth_detected = auth_mode == "official_oauth"
    if not official_oauth_detected and not allow_placeholder:
        return None

    settings = resolve_effective_codex_settings(config_data if isinstance(config_data, dict) else {}, auth_mode)
    model_id = settings.get("model") or DEFAULT_CODEX_MODEL
    provider = normalize_provider({
        "id": OFFICIAL_PROVIDER_ID,
        "display_name": "OpenAI Official Login",
        "kind": "codex_official_login",
        "short_alias": OFFICIAL_PROVIDER_ALIAS,
        "base_url": "",
        "api_format": "openai_responses",
        "auth_mode": "official_oauth",
        "codex_login": True,
        "switch_only": True,
        "amr_excluded": True,
        "local_proxy_routing": False,
        "routing_mode": "official_direct",
        "enabled": True,
        "fallback_enabled": True,
        "catalog_visibility": "hidden",
        "country_region": "US",
        "native_currency": "USD",
        "capability_policy": "official_managed",
        "capabilities": {
            "text": True,
            "vision": True,
            "tools": True,
            "custom_tools": True,
            "reasoning": True,
            "streaming": True,
            "compact": True,
            "models": True,
        },
        "responses_profile": {
            "mode": "native",
            "native_responses": True,
            "compatibility_notes": "Derived from the current Codex official login state.",
        },
        "models": [{
            "id": model_id,
            "display_name": model_id,
            "enabled": True,
            "selected": True,
            "context_window": 0,
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "custom_tools": True,
                "reasoning": True,
                "streaming": True,
                "compact": True,
                "models": True,
            },
        }],
        "status": {
            "last_success": official_oauth_detected,
            "last_error": "" if official_oauth_detected else "Official login was not detected in Codex auth.json.",
            "needs_restart": False,
        },
        "notes": "Read-only provider derived from Codex auth.json. OAuth tokens stay in Codex and are never copied here.",
        "caveat": "Use this entry to switch back to Codex official login. It is not a third-party API-key provider.",
    })
    provider["official_oauth_detected"] = official_oauth_detected
    provider["official_oauth_implied_provider"] = settings.get("official_oauth_implied_provider", False)
    provider["current_model_provider"] = settings.get("model_provider", "")
    provider["current_model_provider_source"] = settings.get("model_provider_source", "")
    return provider
