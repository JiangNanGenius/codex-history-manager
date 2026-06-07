"""
request_capabilities.py - classify request needs before routing.

The classifier is intentionally structural and conservative. It detects the
capabilities already represented in provider/model schemas, plus route signals
that are useful for explanations. It does not infer vendor-specific payloads.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


ROUTE_CAPABILITIES = {
    "text",
    "vision",
    "tools",
    "custom_tools",
    "reasoning",
    "images",
    "videos",
}


def classify_request_capabilities(
    endpoint: str,
    request_json: Dict[str, Any] | None = None,
    *,
    compact: bool = False,
) -> Dict[str, Any]:
    """Return required capabilities and explanation for a proxy request."""
    body = request_json if isinstance(request_json, dict) else {}
    endpoint_name = str(endpoint or "").strip().lower()
    capabilities: Set[str] = set()
    signals: Dict[str, bool] = {
        "text": False,
        "vision": False,
        "tools": False,
        "custom_tools": False,
        "reasoning": False,
        "images": False,
        "videos": False,
        "compact": bool(compact),
    }
    explanation: List[str] = []

    if endpoint_name.startswith("images"):
        capabilities.add("images")
        signals["images"] = True
        explanation.append(f"Endpoint '{endpoint_name}' requires image generation/edit capability.")
    elif endpoint_name.startswith("videos"):
        capabilities.add("videos")
        signals["videos"] = True
        explanation.append(f"Endpoint '{endpoint_name}' requires video capability.")
    else:
        capabilities.add("text")
        signals["text"] = True
        explanation.append("Text capability is required by default.")

    if _contains_image_content(body):
        capabilities.add("vision")
        signals["vision"] = True
        explanation.append("Vision capability required by image input content.")

    tool_info = _tool_signals(body)
    if tool_info["tools"]:
        capabilities.add("tools")
        signals["tools"] = True
        explanation.append("Tool capability required by request tools/functions.")
    if tool_info["custom_tools"]:
        capabilities.add("custom_tools")
        signals["custom_tools"] = True
        explanation.append("Custom tool capability required by custom tool declaration.")

    if _contains_reasoning_request(body):
        capabilities.add("reasoning")
        signals["reasoning"] = True
        explanation.append("Reasoning capability required by reasoning/thinking fields.")

    if compact:
        explanation.append("Compact request signal detected.")

    return {
        "capabilities": sorted(capabilities),
        "signals": signals,
        "explanation": explanation,
    }


def _contains_image_content(value: Any) -> bool:
    if isinstance(value, dict):
        value_type = str(value.get("type") or "").strip().lower()
        if value_type in {"input_image", "image_url", "image"}:
            return True
        if "image_url" in value or "image" in value or "input_image" in value:
            return True
        return any(_contains_image_content(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_image_content(item) for item in value)
    return False


def _tool_signals(body: Dict[str, Any]) -> Dict[str, bool]:
    tools = body.get("tools")
    functions = body.get("functions")
    has_tools = bool(_non_empty_list(tools) or _non_empty_list(functions))
    has_custom = False
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and str(tool.get("type") or "").strip().lower() == "custom":
                has_custom = True
                break
    return {"tools": has_tools, "custom_tools": has_custom}


def _contains_reasoning_request(body: Dict[str, Any]) -> bool:
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("effort") or reasoning.get("summary") or reasoning.get("generate_summary"):
            return True
    elif reasoning not in (None, "", False):
        return True

    if body.get("reasoning_effort") not in (None, "", False):
        return True

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        return thinking.get("enabled", True) is not False
    return thinking not in (None, "", False)


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and any(True for _ in _iter_items(value))


def _iter_items(value: Iterable[Any]) -> Iterable[Any]:
    for item in value:
        yield item
