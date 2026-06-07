"""
Codex approval and sandbox configuration inspection.

This module is intentionally read-only. It models only the documented/source-
verified fields needed for diagnostics and diff previews; real Codex mutation
still goes through the existing manual-confirmation config write path.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


VALID_APPROVAL_POLICIES = {
    "untrusted",
    "on-failure",
    "on-request",
    "granular",
    "never",
}

DEPRECATED_APPROVAL_POLICIES = {"on-failure"}

VALID_SANDBOX_MODES = {
    "read-only",
    "workspace-write",
    "danger-full-access",
}

VALID_WINDOWS_SANDBOX_LEVELS = {
    "disabled",
    "restricted-token",
    "elevated",
}

VALID_BUILT_IN_PERMISSION_PROFILES = {
    ":read-only",
    ":workspace",
    ":danger-full-access",
}

SOURCE_NOTES = [
    "openai/codex codex-rs/protocol/src/config_types.rs: SandboxMode = read-only, workspace-write, danger-full-access.",
    "openai/codex codex-rs/protocol/src/protocol.rs: AskForApproval = untrusted, on-failure, on-request, granular, never; on-failure is deprecated.",
    "openai/codex codex-rs/config/src/config_toml.rs: config.toml fields include approval_policy, sandbox_mode, sandbox_workspace_write, default_permissions, permissions.",
    "openai/codex codex-rs/config/src/types.rs: sandbox_workspace_write supports writable_roots, network_access, exclude_tmpdir_env_var, exclude_slash_tmp.",
    "openai/codex codex-rs/core/src/config/config_tests.rs: approval_policy=never with full access is rejected if requirements force read-only fallback.",
]


def inspect_codex_permissions(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect Codex approval/sandbox settings for known risky states."""
    config_data = config_data if isinstance(config_data, dict) else {}
    approval_policy = _string_or_empty(config_data.get("approval_policy"))
    sandbox_mode = _string_or_empty(config_data.get("sandbox_mode"))
    default_permissions = _string_or_empty(config_data.get("default_permissions"))
    sandbox_workspace_write = config_data.get("sandbox_workspace_write")
    if not isinstance(sandbox_workspace_write, dict):
        sandbox_workspace_write = {}
    permissions = config_data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    windows = config_data.get("windows")
    if not isinstance(windows, dict):
        windows = {}
    windows_sandbox = _string_or_empty(windows.get("sandbox"))

    issues: List[Dict[str, str]] = []
    warnings: List[str] = []
    recommendations: List[str] = []

    if approval_policy and approval_policy not in VALID_APPROVAL_POLICIES:
        issues.append({
            "severity": "error",
            "field": "approval_policy",
            "message": f"Unknown approval_policy '{approval_policy}'.",
        })
    if approval_policy in DEPRECATED_APPROVAL_POLICIES:
        issues.append({
            "severity": "warning",
            "field": "approval_policy",
            "message": "approval_policy 'on-failure' is deprecated upstream; prefer on-request or never.",
        })

    if sandbox_mode and sandbox_mode not in VALID_SANDBOX_MODES:
        issues.append({
            "severity": "error",
            "field": "sandbox_mode",
            "message": f"Unknown sandbox_mode '{sandbox_mode}'.",
        })

    if windows_sandbox and windows_sandbox not in VALID_WINDOWS_SANDBOX_LEVELS:
        issues.append({
            "severity": "error",
            "field": "windows.sandbox",
            "message": f"Unknown windows.sandbox '{windows_sandbox}'.",
        })

    workspace_fields = {
        "writable_roots",
        "network_access",
        "exclude_tmpdir_env_var",
        "exclude_slash_tmp",
    }
    unknown_workspace_fields = sorted(set(sandbox_workspace_write.keys()) - workspace_fields)
    if unknown_workspace_fields:
        issues.append({
            "severity": "warning",
            "field": "sandbox_workspace_write",
            "message": "Unknown sandbox_workspace_write fields: " + ", ".join(unknown_workspace_fields),
        })

    if sandbox_mode != "workspace-write" and sandbox_workspace_write:
        warnings.append("sandbox_workspace_write is configured but sandbox_mode is not workspace-write; Codex may ignore these fields.")

    if sandbox_mode == "danger-full-access" and approval_policy == "never":
        issues.append({
            "severity": "high",
            "field": "approval_policy+sandbox_mode",
            "message": "Full access plus never-ask approvals can become read-only-with-no-approval if managed requirements force read-only.",
        })

    if default_permissions:
        if default_permissions.startswith(":") and default_permissions not in VALID_BUILT_IN_PERMISSION_PROFILES:
            issues.append({
                "severity": "error",
                "field": "default_permissions",
                "message": f"Unknown built-in permission profile '{default_permissions}'.",
            })
        elif not default_permissions.startswith(":") and default_permissions not in permissions:
            issues.append({
                "severity": "error",
                "field": "default_permissions",
                "message": f"default_permissions references missing [permissions.{default_permissions}] profile.",
            })

    for profile_name, profile in permissions.items():
        if str(profile_name).startswith(":"):
            issues.append({
                "severity": "error",
                "field": f"permissions.{profile_name}",
                "message": "Permission profile names beginning with ':' are reserved for built-in profiles.",
            })
        if isinstance(profile, dict):
            extends = _string_or_empty(profile.get("extends"))
            if extends and extends.startswith(":") and extends == ":danger-full-access":
                issues.append({
                    "severity": "warning",
                    "field": f"permissions.{profile_name}.extends",
                    "message": "Extending :danger-full-access is not supported by current upstream tests.",
                })

    if not approval_policy:
        recommendations.append("Set approval_policy explicitly so provider/proxy changes do not inherit a surprising Codex default.")
    if not sandbox_mode and not default_permissions:
        recommendations.append("Set either sandbox_mode or default_permissions explicitly; current Codex may derive defaults from project trust and platform.")
    if approval_policy == "never" and not _effective_full_access(sandbox_mode, default_permissions, permissions):
        recommendations.append("approval_policy=never should be paired with a deliberately chosen permission profile; otherwise write attempts may fail without user escalation.")
    if sandbox_mode == "workspace-write" and not sandbox_workspace_write.get("writable_roots"):
        recommendations.append("workspace-write without extra writable_roots is usually safest; add roots only when a workflow truly needs them.")
    if sandbox_workspace_write.get("network_access") is True:
        warnings.append("sandbox_workspace_write.network_access=true allows network access inside the workspace sandbox.")

    return {
        "approval_policy": approval_policy,
        "sandbox_mode": sandbox_mode,
        "default_permissions": default_permissions,
        "windows_sandbox": windows_sandbox,
        "sandbox_workspace_write": {
            "writable_roots": list(sandbox_workspace_write.get("writable_roots") or []),
            "network_access": bool(sandbox_workspace_write.get("network_access", False)),
            "exclude_tmpdir_env_var": bool(sandbox_workspace_write.get("exclude_tmpdir_env_var", False)),
            "exclude_slash_tmp": bool(sandbox_workspace_write.get("exclude_slash_tmp", False)),
            "unknown_fields": unknown_workspace_fields,
        },
        "named_permission_profiles": sorted(str(name) for name in permissions.keys()),
        "effective_full_access": _effective_full_access(sandbox_mode, default_permissions, permissions),
        "issue_count": len(issues),
        "issues": issues,
        "warnings": warnings,
        "recommendations": recommendations,
        "source_notes": SOURCE_NOTES,
    }


def preview_codex_permissions_update(
    config_data: Dict[str, Any],
    *,
    approval_policy: Optional[str] = None,
    sandbox_mode: Optional[str] = None,
    sandbox_workspace_write: Optional[Dict[str, Any]] = None,
    default_permissions: Optional[str] = None,
    windows_sandbox: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a read-only diff preview for approval/sandbox settings."""
    current = config_data if isinstance(config_data, dict) else {}
    updates: Dict[str, Any] = {}
    if approval_policy is not None:
        updates["approval_policy"] = approval_policy
    if sandbox_mode is not None:
        updates["sandbox_mode"] = sandbox_mode
    if default_permissions is not None:
        updates["default_permissions"] = default_permissions
    if sandbox_workspace_write is not None:
        updates["sandbox_workspace_write"] = _normalize_workspace_write_update(sandbox_workspace_write)
    if windows_sandbox is not None:
        updates["windows"] = {"sandbox": windows_sandbox}

    desired = _merge_dict(current, updates)
    current_inspection = inspect_codex_permissions(current)
    desired_inspection = inspect_codex_permissions(desired)
    diff = _compute_diff(current, desired)
    return {
        "will_write_config": current != desired,
        "restart_required": current != desired,
        "current": current_inspection,
        "desired": desired_inspection,
        "config_diff": diff,
        "warnings": desired_inspection.get("warnings", []) + [
            issue["message"] for issue in desired_inspection.get("issues", [])
            if issue.get("severity") in {"high", "error"}
        ],
        "source_notes": SOURCE_NOTES,
    }


def _normalize_workspace_write_update(value: Dict[str, Any]) -> Dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    normalized: Dict[str, Any] = {}
    if "writable_roots" in value:
        roots = value.get("writable_roots") or []
        if isinstance(roots, str):
            roots = [roots]
        normalized["writable_roots"] = [str(root) for root in roots if str(root).strip()]
    for key in ("network_access", "exclude_tmpdir_env_var", "exclude_slash_tmp"):
        if key in value:
            normalized[key] = bool(value.get(key))
    return normalized


def _merge_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _compute_diff(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    diff: Dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        if key not in old:
            diff["added"][key] = new[key]
        elif key not in new:
            diff["removed"][key] = old[key]
        elif old[key] != new[key]:
            diff["changed"][key] = {"old": old[key], "new": new[key]}
    return diff


def _effective_full_access(
    sandbox_mode: str,
    default_permissions: str,
    permissions: Dict[str, Any],
) -> bool:
    if sandbox_mode == "danger-full-access" or default_permissions == ":danger-full-access":
        return True
    if default_permissions and default_permissions in permissions:
        profile = permissions.get(default_permissions)
        if isinstance(profile, dict):
            if _string_or_empty(profile.get("extends")) == ":danger-full-access":
                return True
            filesystem = profile.get("filesystem")
            if isinstance(filesystem, dict) and filesystem.get(":root") == "write":
                return True
    return False


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def redacted_permissions_preview(value: Dict[str, Any]) -> Dict[str, Any]:
    """Return a defensive copy for diagnostics export."""
    return copy.deepcopy(value if isinstance(value, dict) else {})
