"""
Auto Approval broker primitives.

This module is intentionally provider-agnostic and side-effect free. It builds
the structured review prompt used by a configured model, parses the strict JSON
decision it returns, and creates metadata-only audit records. It does not send
network requests and does not write Codex config/auth/model-catalog files.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from providers import REDACTED_VALUE, is_secret_key, normalize_approval_profile


BROKER_SCHEMA_VERSION = 1

DEFAULT_AUTO_APPROVAL_SYSTEM_PROMPT = (
    "You are the Auto Approval Broker for Codex actions. The user enabled "
    "this mode in local software settings. Review only the provided "
    "redacted action metadata and return strict JSON only. Prefer an "
    "explicit accept or decline decision. Use ask_user only when the policy "
    "requires manual escalation. Decline actions that expose credentials, "
    "damage files outside the intended workspace, disable security controls, "
    "or exfiltrate private data."
)

ACTION_KINDS = {
    "command",
    "file_change",
    "permissions",
    "mcp_tool",
    "network",
    "image_generation",
    "video_generation",
    "unknown",
}

DECISIONS = {"accept", "decline", "ask_user"}
RISK_LEVELS = {"low", "medium", "high", "critical", "unknown"}

DECISION_ALIASES = {
    "accept": "accept",
    "accepted": "accept",
    "approve": "accept",
    "approved": "accept",
    "allow": "accept",
    "allowed": "accept",
    "yes": "accept",
    "decline": "decline",
    "declined": "decline",
    "deny": "decline",
    "denied": "decline",
    "reject": "decline",
    "rejected": "decline",
    "no": "decline",
    "ask_user": "ask_user",
    "ask-user": "ask_user",
    "manual": "ask_user",
    "escalate": "ask_user",
    "needs_user": "ask_user",
}

KIND_ALIASES = {
    "exec": "command",
    "command_execution": "command",
    "command_execution_request_approval": "command",
    "shell_command": "command",
    "apply_patch": "file_change",
    "patch": "file_change",
    "file_change_request_approval": "file_change",
    "file_change_approval": "file_change",
    "request_permissions": "permissions",
    "permissions_request_approval": "permissions",
    "permission": "permissions",
    "mcp": "mcp_tool",
    "mcp_tool_approval": "mcp_tool",
    "tool_approval": "mcp_tool",
    "network_request": "network",
    "http": "network",
    "image": "image_generation",
    "images": "image_generation",
    "image_generation_call": "image_generation",
    "images/generations": "image_generation",
    "video": "video_generation",
    "videos": "video_generation",
    "video_generation_call": "video_generation",
}

SOURCE_NOTES = [
    "openai/codex codex-rs/app-server/src/bespoke_event_handling.rs routes approval events as JSON-RPC requests.",
    "Known approval request types include CommandExecutionRequestApproval, FileChangeRequestApproval, PermissionsRequestApproval, and MCP approval/elicitation flows.",
    "This module does not implement app-server response payloads yet; those must remain source-verified before live wiring.",
]

_OMITTED_KEYS = {
    "messages",
    "input",
    "prompt",
    "body",
    "request_body",
    "response_body",
    "raw",
    "raw_request",
    "raw_response",
    "headers",
    "env",
    "environment",
}


class ApprovalDecisionError(ValueError):
    """Raised when a model decision is not strict, valid JSON."""


def is_auto_approval_enabled(provider_or_profile: Any) -> bool:
    """Return whether the provider/profile explicitly enables proxy approval."""
    profile = _profile_from(provider_or_profile)
    return profile.get("mode") == "proxy_auto_approve"


def normalize_approval_action(raw_action: Any) -> Dict[str, Any]:
    """Normalize an incoming approval request into a redacted action summary."""
    raw = raw_action if isinstance(raw_action, dict) else {}
    kind = _normalize_kind(raw)
    action = {
        "schema_version": BROKER_SCHEMA_VERSION,
        "action_id": _safe_short(raw.get("action_id") or raw.get("request_id") or str(uuid.uuid4()), 120),
        "kind": kind,
        "summary": _safe_short(
            raw.get("summary")
            or raw.get("title")
            or raw.get("reason")
            or _default_summary(kind),
            500,
        ),
        "command": "",
        "cwd": _safe_short(raw.get("cwd") or raw.get("working_directory"), 500),
        "target_paths": _normalize_paths(raw),
        "permissions": _normalize_permissions(raw),
        "network": _normalize_network(raw),
        "media": _normalize_media(raw, kind),
        "tool": _normalize_tool(raw),
        "metadata": _normalize_metadata(raw),
        "risk_hints": [],
        "source_notes": SOURCE_NOTES,
    }
    if kind == "command":
        action["command"] = _normalize_command(raw)
    elif kind == "file_change":
        action["file_change"] = _normalize_file_change(raw)
    action["risk_hints"] = _risk_hints(action)
    return action


def build_auto_approval_prompt(
    raw_action: Any,
    provider_or_profile: Any,
    context: Optional[Dict[str, Any]] = None,
    system_prompt: str = "",
) -> Dict[str, Any]:
    """Build the strict-JSON model prompt for the Auto Approval broker."""
    profile = _profile_from(provider_or_profile)
    action = normalize_approval_action(raw_action)
    context = _sanitize_json(context or {})
    payload = {
        "broker_schema_version": BROKER_SCHEMA_VERSION,
        "mode": "proxy_auto_approve",
        "user_enabled": profile.get("mode") == "proxy_auto_approve",
        "risk_policy": profile.get("risk_policy"),
        "allowed_actions": profile.get("allowed_actions", []),
        "auto_accept_low_risk": bool(profile.get("auto_accept_low_risk", True)),
        "auto_decline_high_risk": bool(profile.get("auto_decline_high_risk", True)),
        "on_review_error": profile.get("on_review_error"),
        "action": action,
        "context": context,
        "expected_response_schema": expected_decision_schema(),
    }
    system_prompt = normalize_auto_approval_system_prompt(system_prompt)
    return {
        "schema_version": BROKER_SCHEMA_VERSION,
        "prompt_template_id": profile.get("prompt_template_id") or "codex_guardian_compatible",
        "reviewer_model": profile.get("reviewer_model") or "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ],
        "response_format": {"type": "json_object"},
        "expected_schema": expected_decision_schema(),
        "timeout_ms": profile.get("timeout_ms"),
        "max_retries": profile.get("max_retries"),
        "source_notes": SOURCE_NOTES,
    }


def normalize_auto_approval_system_prompt(value: Any) -> str:
    """Return a safe reviewer system prompt, falling back to the default."""
    prompt = str(value or "").strip()
    if not prompt:
        return DEFAULT_AUTO_APPROVAL_SYSTEM_PROMPT
    return _safe_short(prompt, 8000)


def expected_decision_schema() -> Dict[str, Any]:
    """Return the JSON decision shape expected from the reviewer model."""
    return {
        "type": "object",
        "required": ["decision", "risk_level", "reason"],
        "properties": {
            "decision": {"enum": sorted(DECISIONS)},
            "risk_level": {"enum": sorted(RISK_LEVELS - {"unknown"})},
            "reason": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "scope": {"enum": ["request", "turn", "session"]},
            "reviewed_action_id": {"type": "string"},
        },
        "additionalProperties": True,
    }


def parse_approval_decision(model_output: Any, provider_or_profile: Any = None) -> Dict[str, Any]:
    """Parse and policy-normalize a strict JSON approval decision."""
    payload = _load_strict_json(model_output)
    decision = _normalize_decision(payload.get("decision"))
    if not decision:
        raise ApprovalDecisionError("approval decision is missing or invalid")
    risk_level = _normalize_risk(payload.get("risk_level"))
    reason = _safe_short(payload.get("reason"), 1000)
    if not reason:
        raise ApprovalDecisionError("approval reason is required")
    normalized = {
        "schema_version": BROKER_SCHEMA_VERSION,
        "decision": decision,
        "risk_level": risk_level,
        "reason": reason,
        "confidence": _clamp_float(payload.get("confidence"), 0.0, 1.0),
        "scope": _normalize_scope(payload.get("scope")),
        "reviewed_action_id": _safe_short(payload.get("reviewed_action_id"), 120),
        "policy_overrides": [],
    }
    profile = _profile_from(provider_or_profile)
    if (
        normalized["decision"] == "accept"
        and normalized["risk_level"] in {"high", "critical"}
        and profile.get("auto_decline_high_risk", True)
    ):
        normalized["decision"] = "decline"
        normalized["policy_overrides"].append("auto_decline_high_risk")
        normalized["reason"] = _safe_short(
            "Policy declined a high-risk approval: " + normalized["reason"],
            1000,
        )
    return normalized


def failure_decision(error: Any, provider_or_profile: Any = None) -> Dict[str, Any]:
    """Return the configured decision when model review fails."""
    profile = _profile_from(provider_or_profile)
    policy = profile.get("on_review_error") or "decline"
    decision = "decline"
    if policy == "allow":
        decision = "accept"
    elif policy == "ask_user":
        decision = "ask_user"
    return {
        "schema_version": BROKER_SCHEMA_VERSION,
        "decision": decision,
        "risk_level": "unknown",
        "reason": _safe_short(f"Auto Approval review failed: {error}", 1000),
        "confidence": 0.0,
        "scope": "request",
        "reviewed_action_id": "",
        "policy_overrides": ["on_review_error"],
    }


def build_decision_record(
    raw_action: Any,
    decision: Dict[str, Any],
    provider_or_profile: Any = None,
    request_id: str = "",
) -> Dict[str, Any]:
    """Create a metadata-only record suitable for later audit logging."""
    profile = _profile_from(provider_or_profile)
    action = normalize_approval_action(raw_action)
    normalized_decision = parse_approval_decision(decision, profile)
    return {
        "schema_version": BROKER_SCHEMA_VERSION,
        "record_id": request_id or str(uuid.uuid4()),
        "timestamp": _utc_now_iso(),
        "broker": "proxy_auto_approval",
        "action_id": action.get("action_id"),
        "action_kind": action.get("kind"),
        "action_summary": action.get("summary"),
        "provider_mode": profile.get("mode"),
        "reviewer_model": profile.get("reviewer_model") or "",
        "decision": normalized_decision["decision"],
        "risk_level": normalized_decision["risk_level"],
        "confidence": normalized_decision["confidence"],
        "reason": normalized_decision["reason"],
        "policy_overrides": normalized_decision.get("policy_overrides", []),
    }


def _profile_from(provider_or_profile: Any) -> Dict[str, Any]:
    if isinstance(provider_or_profile, dict) and isinstance(provider_or_profile.get("approval_profile"), dict):
        return normalize_approval_profile(provider_or_profile.get("approval_profile"))
    return normalize_approval_profile(provider_or_profile if isinstance(provider_or_profile, dict) else {})


def _normalize_kind(raw: Dict[str, Any]) -> str:
    candidates = [
        raw.get("kind"),
        raw.get("type"),
        raw.get("request_type"),
        raw.get("approval_type"),
        raw.get("endpoint"),
        raw.get("media_kind"),
    ]
    for candidate in candidates:
        text = _alias_key(candidate)
        if not text:
            continue
        if text in ACTION_KINDS:
            return text
        if text in KIND_ALIASES:
            return KIND_ALIASES[text]
    if any(key in raw for key in ("command", "argv", "cmd", "shell_command")):
        return "command"
    if any(key in raw for key in ("patch", "diff", "files", "changes")):
        return "file_change"
    if "permissions" in raw or "permission" in raw:
        return "permissions"
    if any(key in raw for key in ("url", "host", "domain")):
        return "network"
    return "unknown"


def _normalize_command(raw: Dict[str, Any]) -> str:
    command = raw.get("command") or raw.get("cmd") or raw.get("shell_command") or raw.get("argv")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return _safe_short(command, 2000)


def _normalize_file_change(raw: Dict[str, Any]) -> Dict[str, Any]:
    patch = raw.get("patch") or raw.get("diff")
    patch_text = _safe_short(patch, 4000) if isinstance(patch, str) else ""
    return {
        "operation": _safe_short(raw.get("operation") or raw.get("change_type"), 80),
        "target_paths": _normalize_paths(raw),
        "patch_excerpt": patch_text,
        "patch_line_count": len(patch_text.splitlines()) if patch_text else 0,
    }


def _normalize_paths(raw: Dict[str, Any]) -> List[str]:
    values: List[Any] = []
    for key in ("path", "file", "target_path", "target_paths", "files", "changed_files"):
        value = raw.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    paths: List[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("path") or value.get("file") or value.get("name")
        text = _safe_short(value, 500)
        if text and text not in paths:
            paths.append(text)
    return paths[:50]


def _normalize_permissions(raw: Dict[str, Any]) -> Dict[str, Any]:
    permissions = raw.get("permissions") or raw.get("permission") or {}
    if not isinstance(permissions, dict):
        permissions = {"requested": permissions}
    return _sanitize_json(permissions, max_string=500)


def _normalize_network(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "url": _safe_short(raw.get("url"), 500),
        "host": _safe_short(raw.get("host") or raw.get("domain"), 200),
        "method": _safe_short(raw.get("method"), 20).upper(),
    }


def _normalize_media(raw: Dict[str, Any], kind: str) -> Dict[str, Any]:
    media_kind = raw.get("media_kind")
    if not media_kind and kind in {"image_generation", "video_generation"}:
        media_kind = "image" if kind == "image_generation" else "video"
    return {
        "media_kind": _safe_short(media_kind, 40),
        "provider_id": _safe_short(raw.get("provider_id"), 120),
        "model": _safe_short(raw.get("model") or raw.get("upstream_model"), 200),
        "operation": _safe_short(raw.get("operation"), 80),
    }


def _normalize_tool(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "server": _safe_short(raw.get("server") or raw.get("mcp_server"), 120),
        "tool_name": _safe_short(raw.get("tool_name") or raw.get("name"), 160),
    }


def _normalize_metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    allowed = (
        "request_id",
        "provider_id",
        "provider_alias",
        "model",
        "upstream_model",
        "endpoint",
        "api_format",
        "route_explanation",
    )
    return {
        key: _safe_short(raw.get(key), 500)
        for key in allowed
        if raw.get(key) is not None
    }


def _risk_hints(action: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    command = str(action.get("command") or "").lower()
    if command:
        if re.search(r"\b(remove-item|rm|del|erase|rmdir|format|shutdown)\b", command):
            hints.append("destructive_command")
        if re.search(r"\b(invoke-webrequest|curl|wget|scp|ftp|ssh)\b", command):
            hints.append("network_or_remote_command")
        if re.search(r"\b(set-executionpolicy|reg\s+add|schtasks|runas)\b", command):
            hints.append("security_or_privilege_change")
        if REDACTED_VALUE in str(action):
            hints.append("contains_redacted_secret")
    permissions = action.get("permissions") if isinstance(action.get("permissions"), dict) else {}
    permissions_text = json.dumps(permissions, ensure_ascii=False).lower()
    if "danger-full-access" in permissions_text or "full" in permissions_text:
        hints.append("full_access_permission")
    if action.get("network", {}).get("url") or action.get("network", {}).get("host"):
        hints.append("network_access")
    if action.get("kind") in {"image_generation", "video_generation"}:
        hints.append("media_generation")
    return sorted(set(hints))


def _load_strict_json(model_output: Any) -> Dict[str, Any]:
    if isinstance(model_output, dict):
        return _sanitize_json(model_output)
    if not isinstance(model_output, str):
        raise ApprovalDecisionError("approval decision must be a JSON object")
    text = model_output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise ApprovalDecisionError("approval decision must be strict JSON")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApprovalDecisionError(f"approval decision JSON is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApprovalDecisionError("approval decision must be a JSON object")
    return _sanitize_json(parsed)


def _normalize_decision(value: Any) -> str:
    return DECISION_ALIASES.get(_alias_key(value), "")


def _normalize_risk(value: Any) -> str:
    risk = _alias_key(value)
    return risk if risk in RISK_LEVELS else "unknown"


def _normalize_scope(value: Any) -> str:
    scope = _alias_key(value)
    return scope if scope in {"request", "turn", "session"} else "request"


def _alias_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(".", "_").replace("/", "/")


def _default_summary(kind: str) -> str:
    defaults = {
        "command": "Command execution approval",
        "file_change": "File change approval",
        "permissions": "Permissions approval",
        "mcp_tool": "MCP tool approval",
        "network": "Network access approval",
        "image_generation": "Image generation approval",
        "video_generation": "Video generation approval",
    }
    return defaults.get(kind, "Codex action approval")


def _sanitize_json(value: Any, *, max_string: int = 1000) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _OMITTED_KEYS:
                result[key_text] = "[omitted]"
            elif is_secret_key(key_text):
                result[key_text] = REDACTED_VALUE if item else ""
            else:
                result[key_text] = _sanitize_json(item, max_string=max_string)
        return result
    if isinstance(value, list):
        return [_sanitize_json(item, max_string=max_string) for item in value[:100]]
    if isinstance(value, str):
        return _safe_short(value, max_string)
    return value


def _safe_short(value: Any, limit: int) -> str:
    text = _redact_text(str(value or ""))
    if len(text) > limit:
        return text[: max(limit - 3, 0)] + "..."
    return text


def _redact_text(text: str) -> str:
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer " + REDACTED_VALUE, text)
    redacted = re.sub(r"sk-[A-Za-z0-9._\-]+", "sk-" + REDACTED_VALUE, redacted)
    redacted = re.sub(r"ek_[A-Za-z0-9._\-]+", "ek_" + REDACTED_VALUE, redacted)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]+", r"\1" + REDACTED_VALUE, redacted)
    return redacted


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return round(min(max(parsed, minimum), maximum), 6)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
