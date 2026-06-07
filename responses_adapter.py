"""
responses_adapter.py - Responses <-> Chat Completions conversion and SSE normalization.
Responses API 与 Chat Completions API 之间的协议转换器 + SSE 流规范化。

设计说明 / Design notes：
- 本实现基于对 Codex++ protocol_proxy.rs（3649 行）及其测试套件（1241 行）的
  研究。Codex++ 是第三方参考实现，**并非** OpenAI 官方源码。所有转换均标注了
  置信度等级，便于后续与官方源码比对时快速定位。
- 在将任何结构视为权威之前，必须与 openai/codex 官方 responses.rs 源码对比。
  标记为 [UNVERIFIED] 的项在对比后可能发生变化。
- 标记为 [CONFIRMED-CODEX++] 的项来自 Codex++ 源码的直接阅读。
- 标记为 [PLACEHOLDER] 的项是结构性存根，等待文档/源码验证后填充。

转换范围 / Conversion scope：
- responses_to_chat_completions: Codex Responses 请求 -> OpenAI Chat 请求
- chat_completion_to_response: OpenAI Chat 非流式响应 -> Codex Response
- ChatSseToResponsesConverter: OpenAI Chat SSE 流 -> Codex Responses SSE 流
- responses_error_from_upstream: 将上游错误规范化为 Responses 错误结构

工程权衡：
  - 纯函数设计：所有转换函数无状态、无副作用，便于单元测试和并行调用。
  - 宽松解析：上游返回的字段可能超集或子集，使用 .get() 而非 []，
    避免字段缺失时抛出 KeyError 导致整个请求失败。
  - UTF-8 截断防护：ChatSseToResponsesConverter 处理字节流时保留不完整
    多字节字符的 remainder，防止中文/emoji 被截断为 �。
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ─────────────── Constants ───────────────

# [CONFIRMED-CODEX++] Fields that can pass through from Responses to Chat unchanged
EXTRA_CHAT_PASSTHROUGH_FIELDS = {
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "metadata",
    "n",
    "presence_penalty",
    "response_format",
    "seed",
    "service_tier",
    "stop",
    "stream_options",
    "top_logprobs",
    "user",
}

# [CONFIRMED-CODEX++] Paths that the proxy should intercept
RESPONSES_PROXY_PATHS = {
    "/responses",
    "/v1/responses",
    "/v1/v1/responses",
    "/codex/v1/responses",
    "/responses/compact",
    "/v1/responses/compact",
    "/v1/v1/responses/compact",
    "/codex/v1/responses/compact",
}

CHAT_COMPLETIONS_PROXY_PATHS = {
    "/chat/completions",
    "/v1/chat/completions",
    "/v1/v1/chat/completions",
    "/codex/v1/chat/completions",
}

MODELS_PROXY_PATHS = {
    "/models",
    "/v1/models",
    "/v1/v1/models",
    "/codex/v1/models",
}


# ─────────────── Responses -> Chat Completions ───────────────

def responses_to_chat_completions(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    [CONFIRMED-CODEX++] 将 Codex Responses API 请求转换为 OpenAI Chat Completions 请求。

    这是协议转换的核心函数，Codex CLI 发送的是 Responses 格式，而大多数国内/第三方
    Provider 只支持 Chat Completions 格式，因此必须经过此转换。

    Verified behaviors from Codex++ / CC Switch tests and openai/codex item shapes：
    - instructions -> system message at head
    - input array -> messages array with role normalization
    - max_output_tokens -> max_tokens (o-series 用 max_completion_tokens)
    - stream=true 强制 stream_options.include_usage=true
    - tools (function/custom/namespace/web_search) -> function tools
    - reasoning -> vendor-specific dialect mapping
    - developer role -> system role
    - latest_reminder role -> user role
    - Multiple system messages collapsed to single head message
    - input_image content -> Chat Completions image_url content block
    - function/custom tool output arrays/objects -> compact JSON string

    工程权衡：
      - 使用 .get() 而非直接索引：Codex 可能在不同版本发送不同字段子集，
        宽松解析防止因字段缺失导致转换崩溃。
      - max_output_tokens 优先于 max_tokens/max_completion_tokens：
        Responses API 的原生字段 wins，避免静默覆盖用户意图。

    Args:
        body: Codex Responses API 请求体。

    Returns:
        OpenAI Chat Completions 格式的请求体字典。
    """
    result: Dict[str, Any] = {}

    if "model" in body:
        result["model"] = body["model"]

    messages: List[Dict[str, Any]] = []

    # instructions -> system message
    if "instructions" in body:
        text = _instruction_text(body["instructions"])
        if text:
            messages.append({"role": "system", "content": text})

    # input -> messages
    if "input" in body:
        _append_responses_input(body["input"], messages)

    _normalize_chat_messages(messages)
    messages = _collapse_system_messages_to_head(messages)
    result["messages"] = messages

    model = str(body.get("model") or "")

    # Token limits: max_output_tokens (Responses API standard) takes precedence.
    # If the request also carries max_tokens / max_completion_tokens, the
    # Responses-native field wins to avoid silent overwrites.
    if "max_output_tokens" in body:
        if _is_openai_o_series(model):
            result["max_completion_tokens"] = body["max_output_tokens"]
        else:
            result["max_tokens"] = body["max_output_tokens"]
    else:
        if "max_tokens" in body:
            result["max_tokens"] = body["max_tokens"]
        if "max_completion_tokens" in body:
            result["max_completion_tokens"] = body["max_completion_tokens"]

    # Standard passthroughs
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            result[key] = body[key]

    # Stream options: force include_usage when streaming
    if body.get("stream"):
        stream_options = copy.deepcopy(body.get("stream_options") or {})
        stream_options["include_usage"] = True
        result["stream_options"] = stream_options

    # Reasoning options (vendor-specific)
    _apply_chat_reasoning_options(result, body, model)

    # Tools
    tool_context = _build_tool_context(body.get("tools"))
    chat_tools = _responses_tools_to_chat_tools(body.get("tools"), tool_context)
    if chat_tools:
        result["tools"] = chat_tools
        if "tool_choice" in body:
            chat_tool_choice = _responses_tool_choice_to_chat(body["tool_choice"], tool_context)
            if chat_tool_choice:
                result["tool_choice"] = chat_tool_choice
        if "parallel_tool_calls" in body:
            result["parallel_tool_calls"] = body["parallel_tool_calls"]

    # Extra passthrough fields
    for key in EXTRA_CHAT_PASSTHROUGH_FIELDS:
        if key == "stream_options" and "stream_options" in result:
            continue
        if key in body:
            result[key] = body[key]

    return result


def _instruction_text(instructions: Any) -> str:
    if isinstance(instructions, str):
        return instructions
    if isinstance(instructions, list):
        parts = []
        for item in instructions:
            if isinstance(item, dict) and item.get("type") == "input_text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(instructions) if instructions is not None else ""


def _append_responses_input(input_value: Any, messages: List[Dict[str, Any]]) -> None:
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
        return
    if not isinstance(input_value, list):
        return
    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        if item_type == "message":
            role = item.get("role", "user")
            if role == "developer":
                role = "system"
            if role == "latest_reminder":
                role = "user"
            content = _responses_content_to_chat_content(item.get("content"))
            messages.append({"role": role, "content": content})
        elif item_type == "reasoning":
            summary = item.get("summary", [])
            text = _extract_summary_text(summary)
            # Merge into previous assistant message if possible
            if messages and messages[-1].get("role") == "assistant":
                messages[-1]["reasoning_content"] = text
            else:
                messages.append({"role": "assistant", "content": "", "reasoning_content": text})
        elif item_type == "function_call":
            call_id = item.get("call_id", "")
            name = item.get("name", "")
            namespace = item.get("namespace", "")
            arguments = item.get("arguments", "{}")
            if namespace and name:
                name = f"{namespace}{name}"
            tc = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            if messages and messages[-1].get("role") == "assistant":
                messages[-1].setdefault("tool_calls", []).append(tc)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
        elif item_type == "function_call_output":
            call_id = item.get("call_id", "")
            output = item.get("output", "")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_output_to_chat_content(output)})
        elif item_type in ("custom_tool_call", "tool_call"):
            call_id = item.get("call_id", "") or item.get("id", "")
            name = item.get("name", "")
            input_data = item.get("input", "")
            if isinstance(input_data, dict):
                input_str = json.dumps(input_data, ensure_ascii=False)
            else:
                input_str = str(input_data)
            tc = {"id": call_id, "type": "function", "function": {"name": name, "arguments": input_str}}
            if messages and messages[-1].get("role") == "assistant":
                messages[-1].setdefault("tool_calls", []).append(tc)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
        elif item_type in ("custom_tool_call_output", "tool_result"):
            call_id = item.get("call_id", "")
            output = item.get("output", "")
            messages.append({"role": "tool", "tool_call_id": call_id, "content": _tool_output_to_chat_content(output)})


def _responses_content_to_chat_content(content: Any) -> Any:
    """
    Convert official Codex Responses content items into Chat message content.

    openai/codex models.rs defines content items as input_text, input_image and
    output_text. CodexPlusPlus and CC Switch both preserve input_image by
    emitting Chat Completions image_url blocks. When a message is text-only we
    keep the historic plain-string shape for broad Chat provider compatibility.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chat_parts: List[Dict[str, Any]] = []
        has_non_text_part = False
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("input_text", "output_text", "text"):
                    text = str(part.get("text", ""))
                    if text:
                        chat_parts.append({"type": "text", "text": text})
                elif part.get("type") == "input_image":
                    image_url = _chat_image_url_payload(part.get("image_url"))
                    if image_url:
                        chat_parts.append({"type": "image_url", "image_url": image_url})
                        has_non_text_part = True
            elif isinstance(part, str):
                if part:
                    chat_parts.append({"type": "text", "text": part})
        if has_non_text_part:
            return chat_parts
        return "\n".join(str(part.get("text", "")) for part in chat_parts)
    return str(content) if content is not None else ""


def _chat_image_url_payload(image_url: Any) -> Optional[Dict[str, Any]]:
    if isinstance(image_url, dict):
        return copy.deepcopy(image_url)
    if isinstance(image_url, str) and image_url:
        return {"url": image_url}
    return None


def _tool_output_to_chat_content(output: Any) -> str:
    if isinstance(output, str):
        return _canonicalize_json_string_if_parseable(output)
    if output is None:
        return ""
    return _canonical_json_string(output)


def _canonicalize_json_string_if_parseable(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
    return _canonical_json_string(parsed)


def _canonical_json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _extract_summary_text(summary: Any) -> str:
    if isinstance(summary, str):
        return summary
    if isinstance(summary, list):
        parts = []
        for part in summary:
            if isinstance(part, dict):
                if "text" in part:
                    parts.append(str(part["text"]))
                elif "summary" in part:
                    parts.append(str(part["summary"]))
            elif isinstance(part, str):
                parts.append(part)
        return "\n\n".join(parts)
    return str(summary) if summary is not None else ""


def _normalize_chat_messages(messages: List[Dict[str, Any]]) -> None:
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("content") is None:
            msg["content"] = ""
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list) and not msg.get("content"):
            msg["content"] = ""


def _collapse_system_messages_to_head(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    system_texts = []
    others = []
    for msg in messages:
        if msg.get("role") == "system":
            system_texts.append(str(msg.get("content", "")))
        else:
            others.append(msg)
    if not system_texts:
        return others
    collapsed = [{"role": "system", "content": "\n\n".join(system_texts)}]
    collapsed.extend(others)
    return collapsed


def _is_openai_o_series(model: str) -> bool:
    """
    检测 OpenAI o-series 模型（o1、o3、o4 及其变体）。

    设计意图：
      - o-series 使用 max_completion_tokens 而非 max_tokens，需要特殊处理。
      - 使用词边界 \\b 避免误匹配：如 "custom-o3-model" 会匹配，但 "foo3bar" 不会。
      - 正则覆盖 o1、o3、o4 及 -mini / -preview 后缀，与未来可能的新型号兼容。

    Args:
        model: 模型名字符串。

    Returns:
        是否为 o-series 模型。
    """
    import re
    return bool(re.search(r'\b(o[134](?:-mini|-preview)?)\b', model, re.IGNORECASE))


def _apply_chat_reasoning_options(result: Dict[str, Any], body: Dict[str, Any], model: str) -> None:
    reasoning = body.get("reasoning")
    if not isinstance(reasoning, dict):
        return
    effort = reasoning.get("effort", "")
    lower_model = model.lower()

    # DeepSeek dialect
    if "deepseek" in lower_model:
        mapping = {"none": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "max"}
        result["reasoning_effort"] = mapping.get(effort, effort)
        return

    # OpenRouter dialect
    if "openrouter" in lower_model:
        result["reasoning"] = {"effort": effort}
        return

    # Kimi dialect
    if "kimi" in lower_model:
        result["thinking"] = {"type": "enabled"}
        return

    # Default OpenAI o-series
    if _is_openai_o_series(model):
        result["reasoning_effort"] = effort


def _build_tool_context(tools: Any) -> Dict[str, Any]:
    """Build a simple tool context for name mapping. [PLACEHOLDER]"""
    return {"custom_tools": set(), "namespace_tools": {}}


def _responses_tools_to_chat_tools(tools: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    将 Responses API 的 tools 转换为 Chat Completions 的 tools。

    设计意图：
      - Responses API 支持多种 tool 类型（function、namespace、custom、
        web_search、built_in），而 Chat Completions 只认 function。
      - namespace 工具：Codex 支持将多个 function 打包到 namespace 下，
        转换时拼接为 "namespace.name" 格式，保持唯一性。
      - custom/web_search/built_in：信息不足时退化为无参 function，
        保证请求能发出去（上游可能忽略参数），而非因格式错误被拒。

    Args:
        tools: Responses API 的 tools 列表。
        context: 预留的工具上下文（当前未使用，供未来扩展）。

    Returns:
        Chat Completions 格式的 tools 列表。
    """
    if not isinstance(tools, list):
        return []
    result = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type", "")
        if tool_type == "function":
            # Chat Completions 要求 parameters 必须有 type、properties、required
            params = copy.deepcopy(tool.get("parameters") or {})
            if not params.get("type"):
                params["type"] = "object"
            if "properties" not in params:
                params["properties"] = {}
            if "required" not in params:
                params["required"] = []
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": params,
                },
            })
        elif tool_type == "namespace":
            ns = tool.get("name", "")
            for sub in tool.get("tools", []):
                if isinstance(sub, dict) and sub.get("type") == "function":
                    name = f"{ns}.{sub.get('name', '')}"
                    result.append({
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": sub.get("description", ""),
                            "parameters": sub.get("parameters", {"type": "object"}),
                        },
                    })
        elif tool_type in ("custom", "web_search", "built_in"):
            # 退化为无参 function：信息不足时不猜测参数结构，避免上游校验失败
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            })
    return result


def _responses_tool_choice_to_chat(tool_choice: Any, context: Dict[str, Any]) -> Optional[Any]:
    if isinstance(tool_choice, str):
        if tool_choice in ("auto", "none", "required"):
            return tool_choice
        return None
    if not isinstance(tool_choice, dict):
        return None
    tc_type = tool_choice.get("type", "")
    if tc_type == "function":
        return {
            "type": "function",
            "function": {"name": tool_choice.get("name", "")},
        }
    if tc_type in ("auto", "none", "required"):
        return tc_type
    if tc_type == "custom":
        return {"type": "function", "function": {"name": tool_choice.get("name", "")}}
    return None


# ─────────────── Chat Completions -> Responses ───────────────

def chat_completion_to_response(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    [CONFIRMED-CODEX++] 将 OpenAI Chat Completions 响应转换为 Codex Response。

    这是非流式响应的转换入口。代理服务器从上游 Provider 收到 Chat Completions
    格式后，必须转回 Responses 格式，Codex CLI 才能正确解析。

    Verified behaviors from Codex++ tests：
    - choices[0].message -> output items (reasoning, message, function_call)
    - usage prompt_tokens/completion_tokens -> input_tokens/output_tokens
    - reasoning_content -> reasoning output item
    - tool_calls -> function_call output items
    - finish_reason: length -> incomplete_details

    边界条件：
      - 上游返回空 choices 时抛出 ValueError：这是严重异常，代理层应捕获并
        转为 responses_error_from_upstream 格式的错误返回。
      - reasoning_content 和 reasoning_details 双源读取：不同 Provider
        （DeepSeek、OpenRouter）的 reasoning 字段命名不一致，优先取
        reasoning_content，缺失时尝试 reasoning_details。

    Args:
        body: Chat Completions 响应体。

    Returns:
        Codex Response 格式字典。

    Raises:
        ValueError: choices 为空时抛出。
    """
    choices = body.get("choices", [])
    if not choices:
        raise ValueError("chat response missing choices")
    choice = choices[0]
    message = choice.get("message", {})

    response_id = _response_id_from_chat_id(body.get("id"))
    output: List[Dict[str, Any]] = []

    # Reasoning
    reasoning_content = message.get("reasoning_content", "")
    if not reasoning_content and "reasoning_details" in message:
        reasoning_content = _extract_reasoning_details(message["reasoning_details"])
    if reasoning_content:
        output.append({
            "id": f"rs_{response_id}",
            "type": "reasoning",
            "status": "completed",
            "reasoning_content": reasoning_content,
            "summary": [{"type": "summary_text", "text": reasoning_content}],
        })

    # Message content
    content = message.get("content", "")
    if content or not output:
        output.append({
            "id": f"{response_id}_msg",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": str(content or ""), "annotations": []}],
        })

    # Tool calls
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        if isinstance(tc, dict):
            output.append({
                "id": tc.get("id", ""),
                "type": "function_call",
                "call_id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", ""),
            })

    finish_reason = choice.get("finish_reason", "")
    status = "completed" if finish_reason in ("stop", "tool_calls") else "in_progress"
    if finish_reason and finish_reason not in ("stop", "tool_calls"):
        status = finish_reason

    response: Dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": body.get("created", 0),
        "status": status,
        "model": body.get("model", ""),
        "output": output,
        "usage": _chat_usage_to_responses_usage(body.get("usage")),
    }

    if finish_reason == "length":
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}

    return response


def _response_id_from_chat_id(chat_id: Any) -> str:
    cid = str(chat_id or "compat")
    if cid.startswith("resp_"):
        return cid
    return f"resp_{cid}"


def _extract_reasoning_details(details: Any) -> str:
    if isinstance(details, str):
        return details
    if isinstance(details, list):
        parts = []
        for d in details:
            if isinstance(d, dict):
                if "summary" in d:
                    parts.append(str(d["summary"]))
                elif "parts" in d and isinstance(d["parts"], list):
                    for p in d["parts"]:
                        if isinstance(p, dict) and "text" in p:
                            parts.append(str(p["text"]))
            elif isinstance(d, str):
                parts.append(d)
        return "\n\n".join(parts)
    return str(details) if details is not None else ""


def _chat_usage_to_responses_usage(usage: Any) -> Dict[str, Any]:
    """
    将 Chat Completions 的 usage 转换为 Responses 的 usage。

    设计意图：
      - 多厂商兼容：OpenAI、Claude、Gemini 的 usage 字段命名各不相同，
        本函数做统一映射，使 Codex CLI 看到一致的 token 统计。
      - 防御式解析：usage 为 None 或非字典时返回零值，避免上游异常格式
        导致整个响应转换失败。

    字段映射表：
      - OpenAI: prompt_tokens -> input_tokens, completion_tokens -> output_tokens
      - Claude: cache_read_input_tokens / cache_creation_input_tokens 透传
      - Gemini: promptTokenCount / candidatesTokenCount / cachedContentTokenCount
        需要特殊处理：Gemini 将 cache tokens 计入 promptTokenCount，
        因此 fresh input = promptTokenCount - cachedContentTokenCount。

    Args:
        usage: Chat Completions 响应中的 usage 字段，可能为 dict/None/其他类型。

    Returns:
        Responses 格式的 usage 字典。
    """
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # OpenAI Chat style（最常用）
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

    result: Dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }

    # Details: OpenAI 的 prompt_tokens_details.cached_tokens
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict) and "cached_tokens" in prompt_details:
        result["input_tokens_details"] = {"cached_tokens": prompt_details["cached_tokens"]}

    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        result["output_tokens_details"] = completion_details

    # Claude-style cache fields
    if "cache_read_input_tokens" in usage:
        result["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
    if "cache_creation_input_tokens" in usage:
        result["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]

    # Gemini style: 字段命名完全不同，且 cache 被计入 prompt
    if "cachedContentTokenCount" in usage:
        result["input_tokens_details"] = {"cached_tokens": usage["cachedContentTokenCount"]}
        # Gemini counts cache tokens inside promptTokenCount; subtract for fresh input
        gemini_prompt = usage.get("promptTokenCount", 0)
        result["input_tokens"] = max(gemini_prompt - usage["cachedContentTokenCount"], 0)
        result["output_tokens"] = usage.get("candidatesTokenCount", output_tokens)
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"] + usage["cachedContentTokenCount"]

    return result


# ─────────────── Chat SSE -> Responses SSE ───────────────

class ChatSseToResponsesConverter:
    """
    [CONFIRMED-CODEX++] 将 OpenAI Chat Completions SSE 流转换为 Codex Responses SSE 流。

    这是流式响应转换的核心类。代理服务器从上游收到 Chat SSE（如 data: {...}），
    需要实时转码为 Responses SSE 事件（response.created、response.in_progress、
    response.content_part.delta 等），Codex CLI 才能正确渲染打字机效果。

    设计说明：
      - 增量处理：push_bytes 接收任意字节块，维护内部 buffer，支持不完整
        SSE 块跨多次调用拼接。
      - UTF-8 截断防护：多字节字符（中文、emoji）可能被 TCP 截断在 packet
        边界，通过保留 utf8_remainder 并在下次拼接后解码，避免 � 出现。
      - Buffer 上限：1MB 硬上限，防止恶意/异常上游发送无限流导致内存耗尽。

    当前限制 [PLACEHOLDER]：
      - tool call 的流式重建尚未完整实现（_push_tool_call_delta 为空）。
      - inline think detection（如 <think>...</think>）尚未接入。
    """

    def __init__(self, original_request: Optional[Dict[str, Any]] = None):
        self.buffer = ""
        self.utf8_remainder: bytes = b""
        self.response_started = False
        self.completed = False
        self.response_id = "resp_compat"
        self.model = ""
        self.created_at = 0
        self.next_output_index = 0
        self.text_item: Dict[str, Any] = {"added": False, "done": False, "text": "", "item_id": ""}
        self.reasoning_item: Dict[str, Any] = {"added": False, "done": False, "text": "", "item_id": ""}
        self.tools: Dict[int, Dict[str, Any]] = {}
        self.output_items: List[Dict[str, Any]] = []
        self.latest_usage: Optional[Dict[str, Any]] = None
        self.finish_reason: Optional[str] = None
        self.failed = False
        self.got_done = False
        self.original_request = original_request

    def push_bytes(self, data: bytes) -> str:
        """
        推送上游 SSE 字节流，返回转换后的 Responses SSE 事件字符串。

        工程权衡：
          - 返回值是字符串而非生成器：Flask/http.server 的流式响应通常以
            wfile.write(chunk) 方式发送，字符串便于直接拼接和编码。
          - 内部维护 buffer 状态：SSE 块以 \\n\\n 分隔，可能跨多个 TCP packet，
            不能假设每次 push_bytes 都收到完整块。

        UTF-8 截断处理细节：
          - 先将上次剩余字节拼接到本次数据前。
          - 尝试整体解码；失败时从末尾逐字节回退（最多回退 3 字节，因为 UTF-8
            最长 4 字节），找到合法截断点；若全部失败则用 errors="replace" 兜底。

        Buffer 溢出防护：
          - 上限 1MB：正常 SSE 流每块仅数百字节，1MB 足够容纳极端情况；
            超过则标记 failed 并返回 buffer_overflow 错误事件。

        Args:
            data: 从上游接收到的原始字节（可能为任意长度，甚至空）。

        Returns:
            转换后的 SSE 事件字符串（零个或多个事件拼接）。
        """
        # 合并上次剩余的不完整 UTF-8 字节，避免多字节字符被截断替换为 �
        data = self.utf8_remainder + data
        try:
            text = data.decode("utf-8")
            self.utf8_remainder = b""
        except UnicodeDecodeError:
            for i in range(len(data) - 1, max(len(data) - 4, -1), -1):
                try:
                    text = data[:i].decode("utf-8")
                    self.utf8_remainder = data[i:]
                    break
                except UnicodeDecodeError:
                    continue
            else:
                # 极端情况：整包数据都不是合法 UTF-8，用 replace 兜底，丢弃 remainder
                text = data.decode("utf-8", errors="replace")
                self.utf8_remainder = b""

        self.buffer += text
        # 防止 SSE buffer 无界增长（1 MB 上限）
        MAX_BUFFER = 1024 * 1024
        if len(self.buffer) > MAX_BUFFER:
            self.failed = True
            self.buffer = ""
            self.utf8_remainder = b""
            return self._sse_event("response.failed", {"error": {"message": "SSE buffer exceeded maximum size", "type": "buffer_overflow"}})

        parts: List[str] = []
        while True:
            block = self._take_sse_block()
            if block is None:
                break
            if block.strip() == "":
                continue
            part = self._handle_block(block)
            if part:
                parts.append(part)
            if self.failed:
                break
        return "".join(parts)

    def finish(self) -> str:
        parts: List[str] = []
        if not self.failed and not self.completed:
            parts.append(self._finalize())
        return "".join(parts)

    def fail(self, message: str, error_type: Optional[str] = None) -> str:
        result = self._failed(message, error_type)
        self.failed = True
        return result

    def _take_sse_block(self) -> Optional[str]:
        for sep in ("\n\n", "\r\n\r\n"):
            idx = self.buffer.find(sep)
            if idx != -1:
                block = self.buffer[:idx]
                self.buffer = self.buffer[idx + len(sep):]
                return block
        return None

    def _handle_block(self, block: str) -> str:
        event_name = None
        data_parts: List[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_parts.append(line[5:].strip())
        if not data_parts:
            return ""
        data_str = "\n".join(data_parts)
        if data_str.strip() == "[DONE]":
            self.got_done = True
            return self._finalize()
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return ""
        if event_name == "error" or "error" in chunk:
            msg = chunk.get("error", {}).get("message", "Unknown error") if isinstance(chunk.get("error"), dict) else str(chunk.get("error", "Unknown error"))
            typ = chunk.get("error", {}).get("type") if isinstance(chunk.get("error"), dict) else None
            self.failed = True
            return self._failed(msg, typ)
        return self._handle_chat_chunk(chunk)

    def _handle_chat_chunk(self, chunk: Dict[str, Any]) -> str:
        parts: List[str] = []
        cid = chunk.get("id")
        if isinstance(cid, str):
            self.response_id = _response_id_from_chat_id(cid)
        model = chunk.get("model")
        if isinstance(model, str) and model:
            self.model = model
        created = chunk.get("created")
        if isinstance(created, int):
            self.created_at = created

        parts.append(self._ensure_response_started())

        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            self.latest_usage = _chat_usage_to_responses_usage(usage)

        choices = chunk.get("choices", [])
        if not choices:
            return "".join(parts)
        choice = choices[0]
        if not isinstance(choice, dict):
            return "".join(parts)

        delta = choice.get("delta", {})
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(self._push_text_delta(content))
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                parts.append(self._push_reasoning_delta(reasoning))
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                for tc in tool_calls:
                    parts.append(self._push_tool_call_delta(tc))

        finish = choice.get("finish_reason")
        if isinstance(finish, str):
            self.finish_reason = finish

        return "".join(parts)

    def _ensure_response_started(self) -> str:
        if self.response_started:
            return ""
        self.response_started = True
        return (
            self._sse_event("response.created", {
                "type": "response.created",
                "response": self._base_response("in_progress", []),
            })
            + self._sse_event("response.in_progress", {
                "type": "response.in_progress",
                "response": self._base_response("in_progress", []),
            })
        )

    def _push_text_delta(self, delta: str) -> str:
        parts: List[str] = []
        if not self.text_item["added"]:
            idx = self._next_output_index()
            item_id = f"{self.response_id}_msg"
            self.text_item = {"added": True, "done": False, "text": "", "item_id": item_id, "output_index": idx}
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": idx,
                "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
            }))
            parts.append(self._sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "item_id": item_id,
                "output_index": idx,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }))
        self.text_item["text"] += delta
        idx = self.text_item.get("output_index", 0)
        parts.append(self._sse_event("response.output_text.delta", {
            "type": "response.output_text.delta",
            "item_id": self.text_item["item_id"],
            "output_index": idx,
            "content_index": 0,
            "delta": delta,
        }))
        return "".join(parts)

    def _push_reasoning_delta(self, delta: str) -> str:
        parts: List[str] = []
        if not self.reasoning_item["added"]:
            idx = self._next_output_index()
            item_id = f"rs_{self.response_id}"
            self.reasoning_item = {"added": True, "done": False, "text": "", "item_id": item_id, "output_index": idx}
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": idx,
                "item": {"id": item_id, "type": "reasoning", "status": "in_progress", "reasoning_content": "", "summary": []},
            }))
            parts.append(self._sse_event("response.reasoning_summary_part.added", {
                "type": "response.reasoning_summary_part.added",
                "item_id": item_id,
                "output_index": idx,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": ""},
            }))
        self.reasoning_item["text"] += delta
        idx = self.reasoning_item.get("output_index", 0)
        parts.append(self._sse_event("response.reasoning_summary_text.delta", {
            "type": "response.reasoning_summary_text.delta",
            "item_id": self.reasoning_item["item_id"],
            "output_index": idx,
            "summary_index": 0,
            "delta": delta,
        }))
        return "".join(parts)

    def _push_tool_call_delta(self, tc: Any) -> str:
        if not isinstance(tc, dict):
            return ""
        try:
            chat_index = int(tc.get("index", 0))
        except (TypeError, ValueError):
            chat_index = 0
        state = self.tools.setdefault(chat_index, {
            "added": False,
            "done": False,
            "call_id": "",
            "name": "",
            "arguments": "",
            "item_id": "",
            "output_index": None,
        })

        if isinstance(tc.get("id"), str) and tc["id"]:
            state["call_id"] = tc["id"]
        function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        if isinstance(function.get("name"), str) and function["name"]:
            state["name"] = function["name"]
        args_delta = function.get("arguments")
        if isinstance(args_delta, str) and args_delta:
            state["arguments"] += args_delta

        parts: List[str] = []
        if not state["added"] and (state["call_id"] or state["name"]):
            state["added"] = True
            if not state["call_id"]:
                state["call_id"] = f"call_{chat_index}"
            if not state["name"]:
                state["name"] = "unknown_tool"
            state["item_id"] = f"fc_{state['call_id']}"
            state["output_index"] = self._next_output_index()
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": state["output_index"],
                "item": {
                    "id": state["item_id"],
                    "type": "function_call",
                    "status": "in_progress",
                    "call_id": state["call_id"],
                    "name": state["name"],
                    "arguments": "",
                },
            }))

        if state["added"] and isinstance(args_delta, str) and args_delta:
            parts.append(self._sse_event("response.function_call_arguments.delta", {
                "type": "response.function_call_arguments.delta",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "delta": args_delta,
            }))
        return "".join(parts)

    def _finalize(self) -> str:
        if self.completed:
            return ""
        self.completed = True
        if not self.failed and (not self.response_started or (not self.got_done and self.finish_reason is None)):
            return self._failed("Stream closed before completion", "stream_incomplete")
        parts: List[str] = []
        # Finalize reasoning
        if self.reasoning_item.get("added") and not self.reasoning_item.get("done"):
            self.reasoning_item["done"] = True
            idx = self.reasoning_item.get("output_index", 0)
            item = {
                "id": self.reasoning_item["item_id"],
                "type": "reasoning",
                "status": "completed",
                "reasoning_content": self.reasoning_item.get("text", ""),
                "summary": [{"type": "summary_text", "text": self.reasoning_item.get("text", "")}],
            }
            self.output_items.append(item)
            parts.append(self._sse_event("response.reasoning_summary_part.done", {
                "type": "response.reasoning_summary_part.done",
                "item_id": self.reasoning_item["item_id"],
                "output_index": idx,
                "summary_index": 0,
            }))
            parts.append(self._sse_event("response.output_item.done", {
                "type": "response.output_item.done",
                "item_id": self.reasoning_item["item_id"],
                "output_index": idx,
                "item": item,
            }))
        # Finalize text
        if self.text_item.get("added") and not self.text_item.get("done"):
            self.text_item["done"] = True
            idx = self.text_item.get("output_index", 0)
            item = {
                "id": self.text_item["item_id"],
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.text_item.get("text", ""), "annotations": []}],
            }
            self.output_items.append(item)
            parts.append(self._sse_event("response.output_text.done", {
                "type": "response.output_text.done",
                "item_id": self.text_item["item_id"],
                "output_index": idx,
                "content_index": 0,
                "text": self.text_item.get("text", ""),
            }))
            parts.append(self._sse_event("response.content_part.done", {
                "type": "response.content_part.done",
                "item_id": self.text_item["item_id"],
                "output_index": idx,
                "content_index": 0,
            }))
            parts.append(self._sse_event("response.output_item.done", {
                "type": "response.output_item.done",
                "item_id": self.text_item["item_id"],
                "output_index": idx,
                "item": item,
            }))
        # Finalize tool calls
        for chat_index in sorted(self.tools):
            state = self.tools[chat_index]
            if state.get("done"):
                continue
            if not state.get("added"):
                state["added"] = True
                if not state.get("call_id"):
                    state["call_id"] = f"call_{chat_index}"
                if not state.get("name"):
                    state["name"] = "unknown_tool"
                state["item_id"] = f"fc_{state['call_id']}"
                state["output_index"] = self._next_output_index()
                parts.append(self._sse_event("response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": state["output_index"],
                    "item": {
                        "id": state["item_id"],
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": state["call_id"],
                        "name": state["name"],
                        "arguments": "",
                    },
                }))
            state["done"] = True
            item = {
                "id": state["item_id"],
                "type": "function_call",
                "status": "completed",
                "call_id": state["call_id"],
                "name": state["name"],
                "arguments": state.get("arguments", ""),
            }
            self.output_items.append(item)
            parts.append(self._sse_event("response.function_call_arguments.done", {
                "type": "response.function_call_arguments.done",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "arguments": state.get("arguments", ""),
            }))
            parts.append(self._sse_event("response.output_item.done", {
                "type": "response.output_item.done",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "item": item,
            }))
        # Completed
        base = self._base_response("completed", self.output_items)
        if self.latest_usage:
            base["usage"] = self.latest_usage
        parts.append(self._sse_event("response.completed", {
            "type": "response.completed",
            "response": base,
        }))
        parts.append("data: [DONE]\n\n")
        return "".join(parts)

    def _failed(self, message: str, error_type: Optional[str] = None) -> str:
        return self._sse_event("response.failed", {
            "type": "response.failed",
            "response": {
                "id": self.response_id,
                "object": "response",
                "status": "failed",
                "error": {
                    "message": message,
                    "type": error_type or "server_error",
                },
            },
        })

    def _base_response(self, status: str, output: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": output,
        }

    def _next_output_index(self) -> int:
        idx = self.next_output_index
        self.next_output_index += 1
        return idx

    def _sse_event(self, event: str, data: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─────────────── Error normalization ───────────────

def responses_error_from_upstream(status_code: int, content_type: str, body: bytes) -> Dict[str, Any]:
    """
    [CONFIRMED-CODEX++] Normalize upstream errors to Responses error shape.
    """
    if "json" in content_type.lower():
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and "error" in parsed:
                return parsed
            return {"error": {"message": str(parsed), "type": "upstream_error", "code": str(status_code)}}
        except Exception:
            pass
    text = body.decode("utf-8", errors="replace")[:1024]
    return {"error": {"message": text, "type": "upstream_error", "code": str(status_code)}}


# ─────────────── URL helpers ───────────────

def chat_completions_url(base_url: str) -> str:
    """[CONFIRMED-CODEX++] Build Chat Completions URL from base URL."""
    skip_version_prefix = base_url.strip().endswith("#")
    base = base_url.strip().rstrip("#").rstrip("/")
    if base.lower().endswith("/chat/completions"):
        return base
    origin_only = "://" not in base or (base.split("://", 1)[1].count("/") == 0)
    if skip_version_prefix or _has_version_suffix(base) or not origin_only:
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def models_url(base_url: str) -> str:
    """[CONFIRMED-CODEX++] Build Models URL from base URL."""
    skip_version_prefix = base_url.strip().endswith("#")
    base = base_url.strip().rstrip("#").rstrip("/")
    if base.lower().endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.lower().endswith("/models"):
        return base
    origin_only = "://" not in base or (base.split("://", 1)[1].count("/") == 0)
    if skip_version_prefix or _has_version_suffix(base) or not origin_only:
        url = f"{base}/models"
    else:
        url = f"{base}/v1/models"
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def responses_url(base_url: str) -> str:
    """Build Responses URL from a provider base URL."""
    skip_version_prefix = base_url.strip().endswith("#")
    base = base_url.strip().rstrip("#").rstrip("/")
    if base.lower().endswith("/responses"):
        return base
    origin_only = "://" not in base or (base.split("://", 1)[1].count("/") == 0)
    if skip_version_prefix or _has_version_suffix(base) or not origin_only:
        url = f"{base}/responses"
    else:
        url = f"{base}/v1/responses"
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def _has_version_suffix(base_url: str) -> bool:
    segment = base_url.split("/")[-1]
    if segment.startswith("v") and len(segment) > 1 and segment[1].isdigit():
        return True
    return False


def is_responses_proxy_path(path: str) -> bool:
    path = path.split("?", 1)[0]
    return path in RESPONSES_PROXY_PATHS


def is_chat_completions_proxy_path(path: str) -> bool:
    path = path.split("?", 1)[0]
    return path in CHAT_COMPLETIONS_PROXY_PATHS


def is_models_proxy_path(path: str) -> bool:
    path = path.split("?", 1)[0]
    return path in MODELS_PROXY_PATHS
