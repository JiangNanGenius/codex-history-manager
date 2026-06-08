"""Shared provider routing guards."""
from __future__ import annotations

from typing import Any, Dict


def _flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def provider_allows_local_routing(provider: Dict[str, Any] | None) -> bool:
    """Return whether a provider may be used by local proxy/AMR routes."""
    if not isinstance(provider, dict):
        return False
    if not _flag(provider.get("enabled"), True):
        return False
    if _flag(provider.get("switch_only"), False) or _flag(provider.get("amr_excluded"), False):
        return False
    if not _flag(provider.get("local_proxy_routing"), True):
        return False
    return True
