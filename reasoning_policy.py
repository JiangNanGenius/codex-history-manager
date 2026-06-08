"""Model-level reasoning effort policy helpers.

Codex exposes a generic reasoning effort knob, but providers do not agree on
which models accept it, which values are valid, or even what the setting means.
Keep that compatibility logic in one place so proxy routing can drop invalid
settings instead of forwarding requests that upstreams reject.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
REASONING_EFFORT_PARAMETERS = {
    "auto",
    "disabled",
    "reasoning.effort",
    "reasoning_effort",
    "output_config.effort",
    "thinking",
}
CODEX_REASONING_ALIASES = {
    "off": "none",
    "disable": "none",
    "disabled": "none",
    "minimal": "none",
    "min": "none",
    "none": "none",
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "extra_high": "xhigh",
    "max": "max",
}


def normalize_reasoning_effort(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if not text:
        return ""
    return CODEX_REASONING_ALIASES.get(text, text if text in REASONING_EFFORTS else "")


def normalize_reasoning_efforts(value: Any) -> List[str]:
    if isinstance(value, str):
        items: Iterable[Any] = value.replace("|", ",").split(",")
    elif isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = value
    else:
        items = []
    result: List[str] = []
    for item in items:
        effort = normalize_reasoning_effort(item)
        if effort and effort not in result:
            result.append(effort)
    return result


def normalize_reasoning_effort_parameter(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "auto"}:
        return "auto"
    if text in {"none", "unsupported", "off"}:
        return "disabled"
    return text if text in REASONING_EFFORT_PARAMETERS else "auto"


def normalize_reasoning_effort_map(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, str] = {}
    for key, item in value.items():
        source = normalize_reasoning_effort(key)
        target = normalize_reasoning_effort(item)
        if source:
            result[source] = target
    return result


def normalize_reasoning_effort_profile(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    efforts = normalize_reasoning_efforts(
        raw.get("efforts")
        or raw.get("reasoning_efforts")
        or raw.get("supported_reasoning_efforts")
    )
    parameter = normalize_reasoning_effort_parameter(
        raw.get("parameter")
        or raw.get("reasoning_effort_parameter")
        or raw.get("api_parameter")
    )
    supports = raw.get("supports_reasoning_effort")
    if supports is False or parameter == "disabled":
        efforts = []
        parameter = "disabled"
    inferred_supports = None if supports is None and not efforts else (bool(efforts) if supports is None else bool(supports))
    return {
        "supports_reasoning_effort": inferred_supports,
        "reasoning_efforts": efforts,
        "reasoning_effort_parameter": parameter,
        "reasoning_effort_map": normalize_reasoning_effort_map(
            raw.get("effort_map") or raw.get("reasoning_effort_map")
        ),
        "reasoning_effort_default": normalize_reasoning_effort(
            raw.get("default") or raw.get("reasoning_effort_default")
        ),
        "semantics": str(raw.get("semantics") or raw.get("reasoning_effort_semantics") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
    }


def build_model_reasoning_effort_profile(model: Dict[str, Any]) -> Dict[str, Any]:
    raw_profile = normalize_reasoning_effort_profile(model.get("reasoning_effort_profile"))
    raw_efforts = normalize_reasoning_efforts(
        model.get("reasoning_efforts") or model.get("supported_reasoning_efforts")
    )
    raw_parameter = normalize_reasoning_effort_parameter(model.get("reasoning_effort_parameter"))
    raw_map = normalize_reasoning_effort_map(model.get("reasoning_effort_map"))
    default = normalize_reasoning_effort(model.get("reasoning_effort_default"))
    semantics = str(model.get("reasoning_effort_semantics") or "").strip()

    if raw_efforts:
        raw_profile["reasoning_efforts"] = raw_efforts
        raw_profile["supports_reasoning_effort"] = True
    if raw_parameter != "auto" or not raw_profile.get("reasoning_effort_parameter"):
        raw_profile["reasoning_effort_parameter"] = raw_parameter
    if raw_map:
        raw_profile["reasoning_effort_map"] = raw_map
    if default:
        raw_profile["reasoning_effort_default"] = default
    if semantics:
        raw_profile["semantics"] = semantics
    return raw_profile


def infer_reasoning_effort_policy(
    provider: Optional[Dict[str, Any]],
    model: Optional[Dict[str, Any]],
    api_format: str = "",
    target: str = "responses",
) -> Dict[str, Any]:
    provider = provider or {}
    model = model or {}
    explicit = build_model_reasoning_effort_profile(model)
    parameter = normalize_reasoning_effort_parameter(explicit.get("reasoning_effort_parameter"))
    efforts = normalize_reasoning_efforts(explicit.get("reasoning_efforts"))
    effort_map = normalize_reasoning_effort_map(explicit.get("reasoning_effort_map"))
    semantics = str(explicit.get("semantics") or "").strip()

    if parameter == "disabled" or explicit.get("supports_reasoning_effort") is False:
        return _policy(False, [], "disabled", effort_map, semantics, "model")
    if efforts:
        return _policy(
            True,
            efforts,
            _effective_parameter(parameter, provider, api_format, target),
            effort_map,
            semantics or "reasoning_depth",
            "model",
        )

    model_id = str(model.get("id") or "").strip()
    lowered = model_id.lower()
    if _looks_like_xai(provider, lowered):
        if _is_grok_43(lowered):
            return _policy(
                True,
                ["none", "low", "medium", "high"],
                _effective_parameter(parameter, provider, api_format, target),
                effort_map,
                "reasoning_depth",
                "xai_docs",
            )
        if _is_grok_420_multi_agent(lowered):
            return _policy(
                True,
                ["low", "medium", "high", "xhigh"],
                _effective_parameter(parameter, provider, api_format, target),
                {"none": "", **effort_map},
                "agent_count",
                "xai_docs",
            )
        if "grok" in lowered:
            return _policy(False, [], "disabled", effort_map, "unsupported", "xai_docs")

    return _policy(None, [], "auto", effort_map, "", "unknown")


def apply_reasoning_policy_to_responses_request(
    body: Dict[str, Any],
    provider: Dict[str, Any],
    model: Optional[Dict[str, Any]],
    api_format: str = "",
) -> Dict[str, Any]:
    request = dict(body or {})
    effort = _extract_responses_effort(request)
    if not effort:
        return request
    policy = infer_reasoning_effort_policy(provider, model, api_format=api_format, target="responses")
    if policy.get("supports_reasoning_effort") is None:
        return request
    if policy.get("supports_reasoning_effort") is False:
        _remove_responses_effort(request)
        return request

    mapped = map_reasoning_effort(effort, policy)
    _remove_responses_effort(request)
    if not mapped:
        return request

    parameter = str(policy.get("reasoning_effort_parameter") or "reasoning.effort")
    if parameter == "reasoning_effort":
        request["reasoning_effort"] = mapped
    elif parameter == "thinking":
        request["thinking"] = {"type": "enabled"}
    elif parameter == "output_config.effort":
        output_config = request.get("output_config") if isinstance(request.get("output_config"), dict) else {}
        request["output_config"] = {**output_config, "effort": mapped}
    else:
        reasoning = request.get("reasoning") if isinstance(request.get("reasoning"), dict) else {}
        request["reasoning"] = {**reasoning, "effort": mapped}
    return request


def apply_reasoning_policy_to_chat_request(
    chat_request: Dict[str, Any],
    original_request: Dict[str, Any],
    provider: Dict[str, Any],
    model: Optional[Dict[str, Any]],
    api_format: str = "",
) -> Dict[str, Any]:
    request = dict(chat_request or {})
    effort = _extract_responses_effort(original_request) or normalize_reasoning_effort(request.get("reasoning_effort"))
    if not effort:
        return request
    policy = infer_reasoning_effort_policy(provider, model, api_format=api_format, target="chat")
    if policy.get("supports_reasoning_effort") is None:
        return request
    if policy.get("supports_reasoning_effort") is False:
        request.pop("reasoning_effort", None)
        request.pop("reasoning", None)
        request.pop("thinking", None)
        return request

    mapped = map_reasoning_effort(effort, policy)
    request.pop("reasoning_effort", None)
    request.pop("reasoning", None)
    request.pop("thinking", None)
    if not mapped:
        return request

    parameter = str(policy.get("reasoning_effort_parameter") or "reasoning_effort")
    if parameter == "reasoning.effort":
        request["reasoning"] = {"effort": mapped}
    elif parameter == "thinking":
        request["thinking"] = {"type": "enabled"}
    else:
        request["reasoning_effort"] = mapped
    return request


def map_reasoning_effort(effort: Any, policy: Dict[str, Any]) -> str:
    normalized = normalize_reasoning_effort(effort)
    if not normalized:
        return ""
    effort_map = normalize_reasoning_effort_map(policy.get("reasoning_effort_map"))
    if normalized in effort_map:
        return effort_map[normalized]
    efforts = normalize_reasoning_efforts(policy.get("reasoning_efforts"))
    if normalized in efforts:
        return normalized
    if not efforts:
        return normalized
    if normalized == "none":
        return ""
    order = [item for item in REASONING_EFFORTS if item in efforts]
    if not order:
        return ""
    requested_index = REASONING_EFFORTS.index(normalized)
    lower_or_equal = [item for item in order if REASONING_EFFORTS.index(item) <= requested_index]
    return (lower_or_equal[-1] if lower_or_equal else order[0]) if order else ""


def _policy(
    supports: Optional[bool],
    efforts: List[str],
    parameter: str,
    effort_map: Dict[str, str],
    semantics: str,
    source: str,
) -> Dict[str, Any]:
    return {
        "supports_reasoning_effort": supports,
        "reasoning_efforts": efforts,
        "reasoning_effort_parameter": parameter,
        "reasoning_effort_map": effort_map,
        "semantics": semantics,
        "source": source,
    }


def _effective_parameter(parameter: str, provider: Dict[str, Any], api_format: str, target: str) -> str:
    if parameter and parameter != "auto":
        return parameter
    if target == "chat":
        return "reasoning_effort"
    fmt = str(api_format or provider.get("api_format") or "")
    if fmt == "openai_chat":
        return "reasoning_effort"
    return "reasoning.effort"


def _looks_like_xai(provider: Dict[str, Any], model_id: str) -> bool:
    haystack = " ".join([
        model_id,
        str(provider.get("id") or ""),
        str(provider.get("short_alias") or ""),
        str(provider.get("display_name") or ""),
        str(provider.get("base_url") or ""),
    ]).lower()
    return "x.ai" in haystack or "xai" in haystack or "grok" in haystack


def _is_grok_43(model_id: str) -> bool:
    normalized = model_id.replace("_", "-")
    return "grok-4.3" in normalized or "grok-4-3" in normalized


def _is_grok_420_multi_agent(model_id: str) -> bool:
    normalized = model_id.replace("_", "-")
    return (
        "grok-4.20-multi-agent" in normalized
        or "grok-420-multi-agent" in normalized
        or "grok-4-20-multi-agent" in normalized
    )


def _extract_responses_effort(body: Dict[str, Any]) -> str:
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = normalize_reasoning_effort(reasoning.get("effort"))
        if effort:
            return effort
    return normalize_reasoning_effort(body.get("reasoning_effort"))


def _remove_responses_effort(body: Dict[str, Any]) -> None:
    body.pop("reasoning_effort", None)
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        remaining = dict(reasoning)
        remaining.pop("effort", None)
        if remaining:
            body["reasoning"] = remaining
        else:
            body.pop("reasoning", None)
