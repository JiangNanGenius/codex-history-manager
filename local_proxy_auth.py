"""Local proxy bearer-token helpers."""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Dict


LOCAL_PROXY_TOKEN_PREFIX = "cem_lp_"
MIN_LOCAL_PROXY_TOKEN_LENGTH = 40
REDACTED_LOCAL_PROXY_TOKEN = "********"


def generate_local_proxy_bearer_token() -> str:
    """Return a high-entropy local proxy bearer token."""
    return LOCAL_PROXY_TOKEN_PREFIX + secrets.token_urlsafe(48)


def local_proxy_token_is_strong(token: Any) -> bool:
    text = str(token or "").strip()
    return text.startswith(LOCAL_PROXY_TOKEN_PREFIX) and len(text) >= MIN_LOCAL_PROXY_TOKEN_LENGTH


def local_proxy_token_fingerprint(token: Any) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def redact_local_proxy_token(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return settings with the bearer token hidden but its status visible."""
    redacted = dict(settings or {})
    token = str(redacted.get("local_proxy_bearer_token") or "")
    redacted["local_proxy_bearer_token_configured"] = bool(token)
    redacted["local_proxy_bearer_token_strong"] = local_proxy_token_is_strong(token)
    redacted["local_proxy_bearer_token_fingerprint"] = local_proxy_token_fingerprint(token)
    if "local_proxy_bearer_token" in redacted:
        redacted["local_proxy_bearer_token"] = REDACTED_LOCAL_PROXY_TOKEN if token else ""
    return redacted


def preserve_redacted_local_proxy_token(incoming: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the existing token when the UI posts the redacted placeholder back."""
    data = dict(incoming or {})
    if data.get("local_proxy_bearer_token") == REDACTED_LOCAL_PROXY_TOKEN:
        data["local_proxy_bearer_token"] = str((current or {}).get("local_proxy_bearer_token") or "")
    return data
