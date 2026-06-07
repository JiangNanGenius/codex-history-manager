"""
guardrails.py - Shared safety checks for high-risk local operations.

The helpers here are intentionally framework-free so tests can run even when
Flask is not installed in the current Python environment.
"""
from __future__ import annotations

from typing import Any, Dict


CODEX_MUTATION_CONFIRMATION = "MODIFY_CODEX_FILES"


def has_codex_mutation_confirmation(body: Dict[str, Any]) -> bool:
    """Return True only when a request carries the typed Codex mutation phrase."""
    return (
        isinstance(body, dict)
        and body.get("manual_codex_mutation") is True
        and body.get("confirmation") == CODEX_MUTATION_CONFIRMATION
    )


def codex_mutation_error_payload(action: str) -> Dict[str, Any]:
    """Build the JSON-safe error payload for blocked Codex mutation endpoints."""
    return {
        "error": "Manual Codex mutation confirmation required.",
        "manual_confirmation_required": True,
        "required_confirmation": CODEX_MUTATION_CONFIRMATION,
        "action": action,
        "message": (
            "This endpoint changes Codex files or process state. "
            f"Send manual_codex_mutation=true and confirmation={CODEX_MUTATION_CONFIRMATION!r} "
            "after a human review."
        ),
    }
