"""
Codex app-server approval bridge.

The bridge maps source-verified app-server JSON-RPC approval requests to the
local Auto Approval broker action format, then maps broker decisions back to the
response payloads expected by Codex app-server. It is pure and side-effect free:
callers are responsible for sending the JSON-RPC response.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from approval_broker import normalize_approval_action, parse_approval_decision


COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
FILE_CHANGE_APPROVAL_METHOD = "item/fileChange/requestApproval"
PERMISSIONS_APPROVAL_METHOD = "item/permissions/requestApproval"
MCP_ELICITATION_METHOD = "mcpServer/elicitation/request"

SUPPORTED_APPROVAL_METHODS = {
    COMMAND_APPROVAL_METHOD,
    FILE_CHANGE_APPROVAL_METHOD,
    PERMISSIONS_APPROVAL_METHOD,
    MCP_ELICITATION_METHOD,
}

SOURCE_NOTES = [
    "openai/codex codex-rs/app-server/README.md Approvals: client responses are method-specific JSON-RPC result payloads.",
    "openai/codex codex-rs/app-server-protocol/src/protocol/v2/item.rs: CommandExecutionRequestApprovalResponse { decision } and FileChangeRequestApprovalResponse { decision }.",
    "openai/codex codex-rs/app-server-protocol/src/protocol/v2/permissions.rs: PermissionsRequestApprovalResponse { permissions, scope, strict_auto_review }.",
    "openai/codex codex-rs/app-server-protocol/src/protocol/v2/mcp.rs: McpServerElicitationRequestResponse { action, content, _meta }.",
]


class CodexApprovalBridgeError(ValueError):
    """Raised when a JSON-RPC approval request/response cannot be mapped."""


def is_codex_approval_request(message: Any) -> bool:
    """Return whether a JSON-RPC message is a supported Codex approval request."""
    return isinstance(message, dict) and message.get("method") in SUPPORTED_APPROVAL_METHODS


def normalize_codex_server_request(message: Any) -> Dict[str, Any]:
    """Normalize a Codex app-server approval request into a compact shape."""
    if not isinstance(message, dict):
        raise CodexApprovalBridgeError("Codex server request must be a JSON object")
    method = str(message.get("method") or "")
    if method not in SUPPORTED_APPROVAL_METHODS:
        raise CodexApprovalBridgeError(f"Unsupported Codex approval method: {method}")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    return {
        "jsonrpc_id": message.get("id"),
        "method": method,
        "params": copy.deepcopy(params),
        "source_notes": SOURCE_NOTES,
    }


def codex_request_to_broker_action(message: Any) -> Dict[str, Any]:
    """Convert a Codex app-server approval request into broker action metadata."""
    request = normalize_codex_server_request(message)
    method = request["method"]
    params = request["params"]
    if method == COMMAND_APPROVAL_METHOD:
        raw_action = _command_action(params, request["jsonrpc_id"])
    elif method == FILE_CHANGE_APPROVAL_METHOD:
        raw_action = _file_change_action(params, request["jsonrpc_id"])
    elif method == PERMISSIONS_APPROVAL_METHOD:
        raw_action = _permissions_action(params, request["jsonrpc_id"])
    else:
        raw_action = _mcp_elicitation_action(params, request["jsonrpc_id"])
    action = normalize_approval_action(raw_action)
    action["codex_app_server"] = {
        "method": method,
        "jsonrpc_id": request["jsonrpc_id"],
        "thread_id": _camel(params, "threadId"),
        "turn_id": _camel(params, "turnId"),
        "item_id": _camel(params, "itemId"),
    }
    return action


def build_codex_approval_result(
    message: Any,
    broker_decision: Any,
    provider_or_profile: Any = None,
) -> Dict[str, Any]:
    """
    Build the JSON-RPC result payload expected by app-server for an approval.

    The returned dict is the `result` value, not the outer JSON-RPC envelope.
    """
    request = normalize_codex_server_request(message)
    decision = parse_approval_decision(broker_decision, provider_or_profile)
    params = request["params"]
    method = request["method"]
    if method == COMMAND_APPROVAL_METHOD:
        return _command_result(params, decision)
    if method == FILE_CHANGE_APPROVAL_METHOD:
        return _file_change_result(params, decision)
    if method == PERMISSIONS_APPROVAL_METHOD:
        return _permissions_result(params, decision)
    if method == MCP_ELICITATION_METHOD:
        return _mcp_elicitation_result(params, decision)
    raise CodexApprovalBridgeError(f"Unsupported Codex approval method: {method}")


def build_codex_jsonrpc_response(
    message: Any,
    broker_decision: Any,
    provider_or_profile: Any = None,
) -> Dict[str, Any]:
    """Build a complete JSON-RPC response envelope for a Codex approval request."""
    request = normalize_codex_server_request(message)
    return {
        "jsonrpc": "2.0",
        "id": request["jsonrpc_id"],
        "result": build_codex_approval_result(message, broker_decision, provider_or_profile),
    }


def _command_action(params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
    network_context = params.get("networkApprovalContext")
    kind = "network" if isinstance(network_context, dict) and not params.get("command") else "command"
    return {
        "kind": kind,
        "request_id": request_id,
        "action_id": _camel(params, "approvalId") or _camel(params, "itemId") or request_id,
        "summary": params.get("reason") or "Codex command execution approval",
        "command": params.get("command"),
        "cwd": params.get("cwd"),
        "permissions": params.get("additionalPermissions") or {},
        "network": network_context if isinstance(network_context, dict) else {},
        "available_decisions": params.get("availableDecisions") or [],
        "proposed_execpolicy_amendment": params.get("proposedExecpolicyAmendment"),
        "proposed_network_policy_amendments": params.get("proposedNetworkPolicyAmendments"),
    }


def _file_change_action(params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
    return {
        "kind": "file_change",
        "request_id": request_id,
        "action_id": _camel(params, "itemId") or request_id,
        "summary": params.get("reason") or "Codex file change approval",
        "target_path": params.get("grantRoot"),
        "operation": "apply_patch",
    }


def _permissions_action(params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
    return {
        "kind": "permissions",
        "request_id": request_id,
        "action_id": _camel(params, "itemId") or request_id,
        "summary": params.get("reason") or "Codex permissions approval",
        "cwd": params.get("cwd"),
        "permissions": params.get("permissions") or {},
    }


def _mcp_elicitation_action(params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
    request = params.get("request") if isinstance(params.get("request"), dict) else {}
    meta = params.get("_meta") or params.get("meta") or request.get("_meta") or request.get("meta") or {}
    is_tool_approval = isinstance(meta, dict) and meta.get("codex_approval_kind") == "mcp_tool_call"
    return {
        "kind": "mcp_tool" if is_tool_approval else "unknown",
        "request_id": request_id,
        "action_id": request_id,
        "summary": request.get("message") or params.get("message") or "Codex MCP elicitation approval",
        "server": params.get("serverName"),
        "tool_name": request.get("toolName") or request.get("tool") or params.get("tool"),
        "metadata": {"mcp_meta": meta} if isinstance(meta, dict) else {},
    }


def _command_result(params: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    if decision["decision"] == "accept":
        codex_decision: Any = _select_accept_command_decision(params, decision)
    elif decision["decision"] == "ask_user":
        codex_decision = "cancel"
    else:
        codex_decision = "decline"
    return {"decision": codex_decision}


def _file_change_result(params: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    if decision["decision"] == "accept":
        codex_decision = "acceptForSession" if _allows_available_decision(params, "acceptForSession") else "accept"
    elif decision["decision"] == "ask_user":
        codex_decision = "cancel"
    else:
        codex_decision = "decline"
    return {"decision": codex_decision}


def _permissions_result(params: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    if decision["decision"] != "accept":
        return {
            "permissions": {},
            "scope": _permission_scope(decision),
        }
    requested = params.get("permissions") if isinstance(params.get("permissions"), dict) else {}
    result: Dict[str, Any] = {
        "permissions": _granted_permissions_from_request(requested),
        "scope": _permission_scope(decision),
    }
    if decision.get("risk_level") in {"high", "critical"}:
        result["strictAutoReview"] = True
    return result


def _mcp_elicitation_result(params: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    if decision["decision"] == "accept":
        action = "accept"
        content: Optional[Dict[str, Any]] = {}
    elif decision["decision"] == "ask_user":
        action = "cancel"
        content = None
    else:
        action = "decline"
        content = None
    return {
        "action": action,
        "content": content,
    }


def _select_accept_command_decision(params: Dict[str, Any], decision: Dict[str, Any]) -> Any:
    if decision.get("scope") == "session" and _allows_available_decision(params, "acceptForSession"):
        return "acceptForSession"
    proposed_network = params.get("proposedNetworkPolicyAmendments")
    if (
        isinstance(proposed_network, list)
        and proposed_network
        and _allows_available_object_decision(params, "applyNetworkPolicyAmendment")
    ):
        amendment = _first_network_allow_amendment(proposed_network)
        if amendment:
            return {
                "applyNetworkPolicyAmendment": {
                    "network_policy_amendment": amendment,
                }
            }
    proposed_execpolicy = params.get("proposedExecpolicyAmendment")
    if proposed_execpolicy and _allows_available_object_decision(params, "acceptWithExecpolicyAmendment"):
        return {
            "acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": proposed_execpolicy,
            }
        }
    return "accept"


def _allows_available_decision(params: Dict[str, Any], decision: str) -> bool:
    available = params.get("availableDecisions")
    if not isinstance(available, list):
        return False
    return decision in available


def _allows_available_object_decision(params: Dict[str, Any], decision: str) -> bool:
    available = params.get("availableDecisions")
    if not isinstance(available, list):
        return False
    for item in available:
        if isinstance(item, dict) and decision in item:
            return True
    return False


def _first_network_allow_amendment(amendments: List[Any]) -> Optional[Dict[str, Any]]:
    for item in amendments:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").lower()
        host = str(item.get("host") or "").strip()
        if host and action == "allow":
            return {"host": host, "action": "allow"}
    return None


def _granted_permissions_from_request(requested: Dict[str, Any]) -> Dict[str, Any]:
    granted: Dict[str, Any] = {}
    file_system = requested.get("fileSystem")
    if isinstance(file_system, dict) and file_system:
        granted["fileSystem"] = _json_clone(file_system)
    network = requested.get("network")
    if isinstance(network, dict) and network:
        granted["network"] = _json_clone(network)
    return granted


def _permission_scope(decision: Dict[str, Any]) -> str:
    return "session" if decision.get("scope") == "session" else "turn"


def _camel(params: Dict[str, Any], key: str) -> Any:
    if key in params:
        return params.get(key)
    if not key:
        return None
    snake = key[0].lower() + "".join(
        "_" + char.lower() if char.isupper() else char
        for char in key[1:]
    )
    return params.get(snake)


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return copy.deepcopy(value)
