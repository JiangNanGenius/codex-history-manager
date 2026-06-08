"""
Anthropic Messages <-> OpenAI Responses compatibility helpers.

Protocol facts used here are verified from Anthropic's generated SDK types and
public API docs:
- Messages requests use top-level ``system`` and ``messages``.
- Client tools use ``tools[].input_schema`` and return ``tool_use`` blocks.
- Tool results are user-message ``tool_result`` blocks.
- Streaming deltas include ``text_delta``, ``input_json_delta`` and
  ``thinking_delta``.
- Usage can include cache read/write token counters.

Anything not covered by those shapes is left out or rejected explicitly rather
than guessed.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional


DEFAULT_MAX_TOKENS = 4096
SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
OPENAI_WEB_SEARCH_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "web_search_preview_2025_03_11",
}


class AnthropicConversionError(ValueError):
    """Raised when a request cannot be safely converted to Anthropic Messages."""


def responses_to_anthropic_messages(
    body: Dict[str, Any],
    upstream_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a Codex/OpenAI Responses request into Anthropic Messages format."""
    if not isinstance(body, dict):
        raise AnthropicConversionError("request body must be a JSON object")

    model = upstream_model or body.get("model")
    if not model:
        raise AnthropicConversionError("Anthropic Messages requires a model")

    result: Dict[str, Any] = {"model": str(model)}
    result["max_tokens"] = _coerce_max_tokens(body)

    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []

    if "instructions" in body:
        text = _instruction_text(body["instructions"])
        if text:
            system_parts.append(text)

    if "input" in body:
        _append_responses_input(body["input"], messages, system_parts)

    if not messages:
        raise AnthropicConversionError("Anthropic Messages requires at least one input message")

    result["messages"] = _merge_adjacent_messages(messages)

    if system_parts:
        result["system"] = "\n\n".join(part for part in system_parts if part)

    for key in ("temperature", "top_p", "top_k", "stream", "metadata", "service_tier", "output_config"):
        if key in body:
            result[key] = body[key]

    if "stop" in body:
        stops = body["stop"]
        if isinstance(stops, str):
            result["stop_sequences"] = [stops]
        elif isinstance(stops, list):
            result["stop_sequences"] = [str(item) for item in stops if item is not None]

    if isinstance(body.get("thinking"), dict):
        result["thinking"] = body["thinking"]

    tools = _responses_tools_to_anthropic_tools(body.get("tools"))
    if tools:
        result["tools"] = tools
        tool_choice = _responses_tool_choice_to_anthropic(
            body.get("tool_choice"),
            parallel_tool_calls=body.get("parallel_tool_calls"),
        )
        if tool_choice:
            result["tool_choice"] = tool_choice

    return result


def anthropic_message_to_response(
    body: Dict[str, Any],
    original_request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert a non-streaming Anthropic Messages response to Responses format."""
    if not isinstance(body, dict):
        raise AnthropicConversionError("Anthropic response body must be a JSON object")

    response_id = _response_id_from_anthropic(body.get("id"))
    created_at = int(time.time())
    output: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    annotations: List[Dict[str, Any]] = []

    for block in body.get("content") or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text", ""))
            annotations.extend(_anthropic_citations_to_response_annotations(
                block.get("citations"),
                offset=sum(len(part) for part in text_parts),
                text_length=len(text),
            ))
            text_parts.append(text)
        elif block_type == "thinking":
            thinking_text = str(block.get("thinking", ""))
            if thinking_text:
                output.append({
                    "id": f"rs_{response_id}_{len(output)}",
                    "type": "reasoning",
                    "status": "completed",
                    "reasoning_content": thinking_text,
                    "summary": [{"type": "summary_text", "text": thinking_text}],
                })
        elif block_type == "tool_use":
            output.append(_tool_use_block_to_response_item(block))
        elif block_type == "server_tool_use":
            output.append(_server_tool_use_block_to_response_item(block))
        elif block_type == "web_search_tool_result":
            _apply_web_search_result_to_output(output, block)
            continue

    if text_parts or not output:
        output.insert(0, {
            "id": f"{response_id}_msg",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "".join(text_parts), "annotations": annotations}],
        })

    status = "completed"
    stop_reason = body.get("stop_reason")
    response: Dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": body.get("model", (original_request or {}).get("model", "")),
        "output": output,
        "usage": _anthropic_usage_to_responses_usage(body.get("usage")),
    }

    if stop_reason == "max_tokens":
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}

    return response


class AnthropicSseToResponsesConverter:
    """Convert Anthropic Messages SSE events into OpenAI Responses SSE events."""

    def __init__(self, original_request: Optional[Dict[str, Any]] = None):
        self.buffer = ""
        self.utf8_remainder = b""
        self.response_started = False
        self.completed = False
        self.failed = False
        self.response_id = "resp_anthropic"
        self.model = (original_request or {}).get("model", "")
        self.created_at = int(time.time())
        self.next_output_index = 0
        self.blocks: Dict[int, Dict[str, Any]] = {}
        self.output_items: List[Dict[str, Any]] = []
        self.usage: Dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.stop_reason: Optional[str] = None
        self.original_request = original_request or {}

    def push_bytes(self, data: bytes) -> str:
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
                text = data.decode("utf-8", errors="replace")
                self.utf8_remainder = b""

        self.buffer += text
        if len(self.buffer) > 1024 * 1024:
            self.failed = True
            self.buffer = ""
            return self._failed("SSE buffer exceeded maximum size", "buffer_overflow")

        parts: List[str] = []
        while True:
            block = self._take_sse_block()
            if block is None:
                break
            if not block.strip():
                continue
            converted = self._handle_block(block)
            if converted:
                parts.append(converted)
            if self.failed or self.completed:
                break
        return "".join(parts)

    def finish(self) -> str:
        if self.failed or self.completed:
            return ""
        return self._failed("Anthropic stream closed before message_stop", "stream_incomplete")

    def fail(self, message: str, error_type: Optional[str] = None) -> str:
        self.failed = True
        return self._failed(message, error_type)

    def _take_sse_block(self) -> Optional[str]:
        for sep in ("\n\n", "\r\n\r\n"):
            idx = self.buffer.find(sep)
            if idx != -1:
                block = self.buffer[:idx]
                self.buffer = self.buffer[idx + len(sep):]
                return block
        return None

    def _handle_block(self, block: str) -> str:
        data_parts: List[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_parts.append(line[5:].strip())
        if not data_parts:
            return ""
        data_str = "\n".join(data_parts)
        if data_str == "[DONE]":
            return self._complete()
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            return ""
        if not isinstance(event, dict):
            return ""
        if event.get("type") == "error" or "error" in event:
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            self.failed = True
            return self._failed(str(error.get("message", "Anthropic stream error")), str(error.get("type", "upstream_error")))

        event_type = event.get("type")
        if event_type == "message_start":
            return self._handle_message_start(event)
        if event_type == "content_block_start":
            return self._handle_content_block_start(event)
        if event_type == "content_block_delta":
            return self._handle_content_block_delta(event)
        if event_type == "content_block_stop":
            return self._handle_content_block_stop(event)
        if event_type == "message_delta":
            return self._handle_message_delta(event)
        if event_type == "message_stop":
            return self._complete()
        return ""

    def _handle_message_start(self, event: Dict[str, Any]) -> str:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if isinstance(message.get("id"), str):
            self.response_id = _response_id_from_anthropic(message["id"])
        if isinstance(message.get("model"), str):
            self.model = message["model"]
        self.usage = _anthropic_usage_to_responses_usage(message.get("usage"))
        return self._ensure_response_started()

    def _handle_content_block_start(self, event: Dict[str, Any]) -> str:
        parts = [self._ensure_response_started()]
        index = int(event.get("index", 0))
        block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
        block_type = block.get("type")
        output_index = self._next_output_index()

        if block_type == "text":
            item_id = f"{self.response_id}_msg_{index}"
            state = {
                "type": "text",
                "item_id": item_id,
                "output_index": output_index,
                "text": str(block.get("text", "")),
                "done": False,
            }
            self.blocks[index] = state
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
            }))
            parts.append(self._sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "item_id": item_id,
                "output_index": output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }))
        elif block_type == "tool_use":
            call_id = str(block.get("id") or f"toolu_{index}")
            name = str(block.get("name") or "")
            arguments = _json_string(block.get("input") if isinstance(block.get("input"), dict) else {})
            item_id = call_id
            state = {
                "type": "tool_use",
                "item_id": item_id,
                "call_id": call_id,
                "name": name,
                "arguments": arguments if arguments != "{}" else "",
                "output_index": output_index,
                "done": False,
            }
            self.blocks[index] = state
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "status": "in_progress",
                    "call_id": call_id,
                    "name": name,
                    "arguments": state["arguments"],
                },
            }))
        elif block_type == "server_tool_use":
            if str(block.get("name") or "") != "web_search":
                self.failed = True
                return "".join(parts) + self._failed(
                    f"Unsupported Anthropic server tool block: {block.get('name')}",
                    "unsupported_server_tool",
                )
            item_id = str(block.get("id") or f"ws_{index}")
            state = {
                "type": "web_search",
                "item_id": item_id,
                "output_index": output_index,
                "arguments": _json_string(block.get("input") if isinstance(block.get("input"), dict) else {}),
                "done": False,
            }
            if state["arguments"] == "{}":
                state["arguments"] = ""
            self.blocks[index] = state
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": _web_search_call_item(item_id=item_id, arguments=state["arguments"], status="in_progress"),
            }))
            parts.append(self._sse_event("response.web_search_call.in_progress", {
                "type": "response.web_search_call.in_progress",
                "item_id": item_id,
                "output_index": output_index,
            }))
            parts.append(self._sse_event("response.web_search_call.searching", {
                "type": "response.web_search_call.searching",
                "item_id": item_id,
                "output_index": output_index,
            }))
        elif block_type == "web_search_tool_result":
            tool_use_id = str(block.get("tool_use_id") or "")
            state = self._find_web_search_state(tool_use_id)
            if state and not state.get("done"):
                parts.append(self._complete_web_search_state(state, block))
            self.blocks[index] = {
                "type": "web_search_tool_result",
                "item_id": tool_use_id,
                "output_index": output_index,
                "done": True,
            }
        elif block_type == "thinking":
            item_id = f"rs_{self.response_id}_{index}"
            state = {
                "type": "thinking",
                "item_id": item_id,
                "output_index": output_index,
                "text": str(block.get("thinking", "")),
                "done": False,
            }
            self.blocks[index] = state
            parts.append(self._sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {"id": item_id, "type": "reasoning", "status": "in_progress", "summary": []},
            }))
            parts.append(self._sse_event("response.reasoning_summary_part.added", {
                "type": "response.reasoning_summary_part.added",
                "item_id": item_id,
                "output_index": output_index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": ""},
            }))
        return "".join(parts)

    def _handle_content_block_delta(self, event: Dict[str, Any]) -> str:
        index = int(event.get("index", 0))
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        state = self.blocks.get(index)
        if not state:
            return ""
        delta_type = delta.get("type")
        if delta_type == "text_delta" and state.get("type") == "text":
            text = str(delta.get("text", ""))
            state["text"] += text
            return self._sse_event("response.output_text.delta", {
                "type": "response.output_text.delta",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "content_index": 0,
                "delta": text,
            })
        if delta_type == "input_json_delta" and state.get("type") in ("tool_use", "web_search"):
            partial = str(delta.get("partial_json", ""))
            state["arguments"] += partial
            if state.get("type") == "web_search":
                return ""
            return self._sse_event("response.function_call_arguments.delta", {
                "type": "response.function_call_arguments.delta",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "delta": partial,
            })
        if delta_type == "thinking_delta" and state.get("type") == "thinking":
            text = str(delta.get("thinking", ""))
            state["text"] += text
            return self._sse_event("response.reasoning_summary_text.delta", {
                "type": "response.reasoning_summary_text.delta",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "summary_index": 0,
                "delta": text,
            })
        return ""

    def _handle_content_block_stop(self, event: Dict[str, Any]) -> str:
        index = int(event.get("index", 0))
        state = self.blocks.get(index)
        if not state or state.get("done"):
            return ""
        state["done"] = True
        if state.get("type") == "text":
            item = {
                "id": state["item_id"],
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": state.get("text", ""), "annotations": []}],
            }
            self.output_items.append(item)
            return (
                self._sse_event("response.output_text.done", {
                    "type": "response.output_text.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "content_index": 0,
                    "text": state.get("text", ""),
                })
                + self._sse_event("response.content_part.done", {
                    "type": "response.content_part.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "content_index": 0,
                })
                + self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "item": item,
                })
            )
        if state.get("type") == "tool_use":
            item = _function_call_item(
                item_id=state["item_id"],
                call_id=state["call_id"],
                name=state["name"],
                arguments=state.get("arguments", ""),
                status="completed",
            )
            self.output_items.append(item)
            return (
                self._sse_event("response.function_call_arguments.done", {
                    "type": "response.function_call_arguments.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "arguments": state.get("arguments", ""),
                })
                + self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "item": item,
                })
            )
        if state.get("type") == "web_search":
            # Anthropic sends a web_search_tool_result block after the
            # server_tool_use block. Completion is emitted when that result
            # arrives, or at stream end as a fallback.
            state["done"] = False
            return ""
        if state.get("type") == "thinking":
            item = {
                "id": state["item_id"],
                "type": "reasoning",
                "status": "completed",
                "reasoning_content": state.get("text", ""),
                "summary": [{"type": "summary_text", "text": state.get("text", "")}],
            }
            self.output_items.append(item)
            return (
                self._sse_event("response.reasoning_summary_part.done", {
                    "type": "response.reasoning_summary_part.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "summary_index": 0,
                })
                + self._sse_event("response.output_item.done", {
                    "type": "response.output_item.done",
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "item": item,
                })
            )
        return ""

    def _handle_message_delta(self, event: Dict[str, Any]) -> str:
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        if isinstance(delta.get("stop_reason"), str):
            self.stop_reason = delta["stop_reason"]
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        if usage:
            self.usage = _merge_usage(self.usage, _anthropic_usage_to_responses_usage(usage))
        return ""

    def _ensure_response_started(self) -> str:
        if self.response_started:
            return ""
        self.response_started = True
        return (
            self._sse_event("response.created", {
                "type": "response.created",
                "response": self._base_response("in_progress"),
            })
            + self._sse_event("response.in_progress", {
                "type": "response.in_progress",
                "response": self._base_response("in_progress"),
            })
        )

    def _complete(self) -> str:
        if self.completed:
            return ""
        self.completed = True
        pending_tool_events = self._complete_pending_web_search_calls()
        status = "completed"
        response = self._base_response(status)
        if self.stop_reason == "max_tokens":
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        if self.usage:
            response["usage"] = self.usage
        return (
            pending_tool_events
            +
            self._sse_event("response.completed", {
                "type": "response.completed",
                "response": response,
            })
            + "data: [DONE]\n\n"
        )

    def _find_web_search_state(self, item_id: str) -> Optional[Dict[str, Any]]:
        for state in self.blocks.values():
            if state.get("type") == "web_search" and state.get("item_id") == item_id:
                return state
        return None

    def _complete_pending_web_search_calls(self) -> str:
        parts: List[str] = []
        for state in list(self.blocks.values()):
            if state.get("type") == "web_search" and not state.get("done"):
                parts.append(self._complete_web_search_state(state))
        return "".join(parts)

    def _complete_web_search_state(
        self,
        state: Dict[str, Any],
        result_block: Optional[Dict[str, Any]] = None,
    ) -> str:
        state["done"] = True
        status = "failed" if _web_search_result_is_error(result_block) else "completed"
        item = _web_search_call_item(
            item_id=state["item_id"],
            arguments=state.get("arguments", ""),
            status=status,
            result_block=result_block,
        )
        self.output_items.append(item)
        return (
            self._sse_event("response.web_search_call.completed", {
                "type": "response.web_search_call.completed",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
            })
            + self._sse_event("response.output_item.done", {
                "type": "response.output_item.done",
                "item_id": state["item_id"],
                "output_index": state["output_index"],
                "item": item,
            })
        )

    def _failed(self, message: str, error_type: Optional[str] = None) -> str:
        return self._sse_event("response.failed", {
            "type": "response.failed",
            "response": {
                "id": self.response_id,
                "object": "response",
                "created_at": self.created_at,
                "status": "failed",
                "model": self.model,
                "error": {"message": message, "type": error_type or "server_error"},
            },
        })

    def _base_response(self, status: str) -> Dict[str, Any]:
        return {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": status,
            "model": self.model,
            "output": list(self.output_items),
        }

    def _next_output_index(self) -> int:
        idx = self.next_output_index
        self.next_output_index += 1
        return idx

    def _sse_event(self, event: str, data: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def anthropic_messages_url(base_url: str) -> str:
    """Build the Anthropic Messages URL from a provider base URL."""
    skip_version_prefix = base_url.strip().endswith("#")
    base = base_url.strip().rstrip("#").rstrip("/")
    if base.lower().endswith("/messages"):
        return base
    origin_only = "://" not in base or (base.split("://", 1)[1].count("/") == 0)
    if skip_version_prefix or _has_version_suffix(base) or not origin_only:
        url = f"{base}/messages"
    else:
        url = f"{base}/v1/messages"
    while "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    return url


def _append_responses_input(input_value: Any, messages: List[Dict[str, Any]], system_parts: List[str]) -> None:
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": [{"type": "text", "text": input_value}]})
        return
    if not isinstance(input_value, list):
        return

    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            role = str(item.get("role", "user"))
            content_blocks = _responses_content_to_anthropic_blocks(item.get("content"))
            if role in ("developer", "system"):
                system_text = _anthropic_blocks_to_text(content_blocks)
                if system_text:
                    system_parts.append(system_text)
                continue
            if role == "latest_reminder":
                role = "user"
            if role not in ("user", "assistant"):
                role = "user"
            messages.append({"role": role, "content": content_blocks})
        elif item_type in ("function_call", "custom_tool_call", "tool_call"):
            name = str(item.get("name", ""))
            namespace = str(item.get("namespace", ""))
            if namespace and name and "." not in name:
                name = f"{namespace}.{name}"
            call_id = str(item.get("call_id") or item.get("id") or f"toolu_{len(messages)}")
            input_data = item.get("input", item.get("arguments", {}))
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": _coerce_tool_input(input_data),
                }],
            })
        elif item_type in ("function_call_output", "custom_tool_call_output", "tool_result"):
            call_id = str(item.get("call_id") or item.get("tool_call_id") or item.get("tool_use_id") or "")
            output = item.get("output", item.get("content", ""))
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": _stringify_tool_result(output),
                }],
            })


def _responses_content_to_anthropic_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": "" if content is None else str(content)}]

    blocks: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in ("input_text", "output_text", "text"):
            blocks.append({"type": "text", "text": str(part.get("text", ""))})
        elif part_type == "input_image":
            blocks.append(_input_image_to_anthropic(part))
        elif part_type == "tool_result":
            blocks.append({
                "type": "tool_result",
                "tool_use_id": str(part.get("tool_use_id") or part.get("call_id") or ""),
                "content": _stringify_tool_result(part.get("content", "")),
            })
    return blocks or [{"type": "text", "text": ""}]


def _input_image_to_anthropic(part: Dict[str, Any]) -> Dict[str, Any]:
    image_url = part.get("image_url")
    if not isinstance(image_url, str) or not image_url:
        raise AnthropicConversionError("Anthropic image conversion requires input_image.image_url")
    if image_url.startswith("data:"):
        match = re.match(r"^data:([^;,]+);base64,(.*)$", image_url, flags=re.DOTALL)
        if not match:
            raise AnthropicConversionError("input_image.image_url data URL must be base64 encoded")
        media_type, data = match.group(1), match.group(2)
        if media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
            raise AnthropicConversionError(f"unsupported Anthropic image media type: {media_type}")
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": image_url}}


def _responses_tools_to_anthropic_tools(tools: Any) -> List[Dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    result: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "function":
            result.append(_function_tool_to_anthropic(tool))
        elif tool_type == "namespace":
            namespace = str(tool.get("name", ""))
            for sub in tool.get("tools") or []:
                if isinstance(sub, dict) and sub.get("type") == "function":
                    copy_sub = dict(sub)
                    copy_sub["name"] = f"{namespace}.{sub.get('name', '')}" if namespace else sub.get("name", "")
                    result.append(_function_tool_to_anthropic(copy_sub))
        elif _is_responses_web_search_tool(tool):
            result.append(_web_search_tool_to_anthropic(tool))
        elif tool_type in ("custom", "built_in", "web_search_preview"):
            raise AnthropicConversionError(
                f"unsupported Responses tool for Anthropic adapter: {tool_type}. "
                "Only function tools, namespace function tools, and verified web_search are supported."
            )
        elif tool_type:
            raise AnthropicConversionError(
                f"unsupported Responses tool for Anthropic adapter: {tool_type}"
            )
    return result


def _function_tool_to_anthropic(tool: Dict[str, Any]) -> Dict[str, Any]:
    schema = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
    schema = dict(schema)
    if not schema.get("type"):
        schema["type"] = "object"
    if "properties" not in schema:
        schema["properties"] = {}
    if "required" not in schema:
        schema["required"] = []
    return {
        "name": str(tool.get("name", "")),
        "description": str(tool.get("description", "")),
        "input_schema": schema,
    }


def _responses_tool_choice_to_anthropic(tool_choice: Any, parallel_tool_calls: Any = None) -> Optional[Dict[str, Any]]:
    disable_parallel = parallel_tool_calls is False
    if isinstance(tool_choice, str):
        choice_type = tool_choice
        name = ""
    elif isinstance(tool_choice, dict):
        choice_type = str(tool_choice.get("type", ""))
        name = str(tool_choice.get("name") or tool_choice.get("function", {}).get("name") or "")
    else:
        return {"type": "auto", "disable_parallel_tool_use": disable_parallel} if disable_parallel else None

    if choice_type == "auto":
        return {"type": "auto", "disable_parallel_tool_use": disable_parallel}
    if choice_type == "none":
        return {"type": "none", "disable_parallel_tool_use": disable_parallel}
    if choice_type == "required":
        return {"type": "any", "disable_parallel_tool_use": disable_parallel}
    if _is_web_search_tool_choice(choice_type, name):
        return {"type": "tool", "name": "web_search", "disable_parallel_tool_use": disable_parallel}
    if choice_type in ("function", "custom", "tool") and name:
        return {"type": "tool", "name": name, "disable_parallel_tool_use": disable_parallel}
    return None


def _is_responses_web_search_tool(tool: Dict[str, Any]) -> bool:
    tool_type = str(tool.get("type") or "")
    name = str(tool.get("name") or "")
    if tool_type in OPENAI_WEB_SEARCH_TOOL_TYPES:
        return True
    return tool_type == "built_in" and name in OPENAI_WEB_SEARCH_TOOL_TYPES.union({"web_search"})


def _is_web_search_tool_choice(choice_type: str, name: str) -> bool:
    if choice_type in OPENAI_WEB_SEARCH_TOOL_TYPES:
        return True
    return choice_type in ("built_in", "tool") and name in OPENAI_WEB_SEARCH_TOOL_TYPES.union({"web_search"})


def _web_search_tool_to_anthropic(tool: Dict[str, Any]) -> Dict[str, Any]:
    if tool.get("external_web_access") is False:
        raise AnthropicConversionError(
            "OpenAI web_search external_web_access=false cannot be represented by Anthropic web_search_20250305"
        )
    unsupported_keys = [
        key for key in ("return_token_budget", "search_context_size")
        if key in tool and tool.get(key) not in (None, "", False)
    ]
    if unsupported_keys:
        raise AnthropicConversionError(
            "unsupported OpenAI web_search fields for Anthropic adapter: "
            + ", ".join(sorted(unsupported_keys))
        )

    result: Dict[str, Any] = {"type": "web_search_20250305", "name": "web_search"}
    max_uses = tool.get("max_uses")
    if max_uses is not None:
        try:
            result["max_uses"] = int(max_uses)
        except (TypeError, ValueError):
            raise AnthropicConversionError("web_search.max_uses must be an integer")

    filters = tool.get("filters") if isinstance(tool.get("filters"), dict) else {}
    unknown_filter_keys = [
        key for key, value in filters.items()
        if key not in {"allowed_domains", "blocked_domains"} and value not in (None, "", [], {})
    ]
    if unknown_filter_keys:
        raise AnthropicConversionError(
            "unsupported OpenAI web_search.filters fields for Anthropic adapter: "
            + ", ".join(sorted(str(key) for key in unknown_filter_keys))
        )
    allowed_domains = _string_list(filters.get("allowed_domains") or tool.get("allowed_domains"))
    blocked_domains = _string_list(filters.get("blocked_domains") or tool.get("blocked_domains"))
    if allowed_domains and blocked_domains:
        raise AnthropicConversionError(
            "Anthropic web_search_20250305 accepts either allowed_domains or blocked_domains, not both"
        )
    if allowed_domains:
        result["allowed_domains"] = allowed_domains
    if blocked_domains:
        result["blocked_domains"] = blocked_domains

    user_location = tool.get("user_location")
    if isinstance(user_location, dict):
        location = user_location.get("approximate") if isinstance(user_location.get("approximate"), dict) else user_location
        mapped_location = {
            key: str(location[key])
            for key in ("type", "city", "region", "country", "timezone")
            if key in location and location[key] is not None
        }
        if mapped_location:
            mapped_location.setdefault("type", "approximate")
            if mapped_location.get("type") != "approximate":
                raise AnthropicConversionError("Anthropic web_search user_location.type must be approximate")
            result["user_location"] = mapped_location

    return result


def _anthropic_usage_to_responses_usage(usage: Any) -> Dict[str, Any]:
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    fresh_input = _int_value(usage.get("input_tokens"))
    cache_read = _int_value(usage.get("cache_read_input_tokens"))
    cache_write = _int_value(usage.get("cache_creation_input_tokens"))
    output_tokens = _int_value(usage.get("output_tokens"))
    input_total = fresh_input + cache_read + cache_write
    result: Dict[str, Any] = {
        "input_tokens": input_total,
        "output_tokens": output_tokens,
        "total_tokens": input_total + output_tokens,
    }
    if cache_read:
        result["input_tokens_details"] = {"cached_tokens": cache_read}
        result["cache_read_input_tokens"] = cache_read
    if cache_write:
        result["cache_creation_input_tokens"] = cache_write
    if isinstance(usage.get("output_tokens_details"), dict):
        result["output_tokens_details"] = usage["output_tokens_details"]
    return result


def _merge_usage(current: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(current or {})
    for key, value in new.items():
        if isinstance(value, int):
            merged[key] = max(_int_value(merged.get(key)), value)
        elif isinstance(value, dict):
            old = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**old, **value}
        else:
            merged[key] = value
    return merged


def _tool_use_block_to_response_item(block: Dict[str, Any]) -> Dict[str, Any]:
    return _function_call_item(
        item_id=str(block.get("id", "")),
        call_id=str(block.get("id", "")),
        name=str(block.get("name", "")),
        arguments=_json_string(block.get("input") if isinstance(block.get("input"), dict) else {}),
        status="completed",
    )


def _server_tool_use_block_to_response_item(block: Dict[str, Any]) -> Dict[str, Any]:
    name = str(block.get("name") or "")
    if name != "web_search":
        raise AnthropicConversionError(f"unsupported Anthropic server tool block: {name}")
    return _web_search_call_item(
        item_id=str(block.get("id") or ""),
        arguments=_json_string(block.get("input") if isinstance(block.get("input"), dict) else {}),
        status="completed",
    )


def _web_search_call_item(
    item_id: str,
    arguments: Any,
    status: str,
    result_block: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    parsed_arguments = _parse_json_object(arguments)
    action: Dict[str, Any] = {"type": "search"}
    if parsed_arguments.get("query") is not None:
        action["query"] = str(parsed_arguments["query"])

    sources = _web_search_sources_from_result(result_block)
    if sources:
        action["sources"] = sources

    return {
        "id": item_id,
        "type": "web_search_call",
        "status": status,
        "action": action,
    }


def _web_search_sources_from_result(result_block: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(result_block, dict):
        return []
    content = result_block.get("content")
    if not isinstance(content, list):
        return []
    sources: List[Dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "web_search_result":
            continue
        source = {
            key: item[key]
            for key in ("url", "title", "page_age")
            if item.get(key) is not None
        }
        if source:
            sources.append(source)
    return sources


def _apply_web_search_result_to_output(output: List[Dict[str, Any]], result_block: Dict[str, Any]) -> None:
    tool_use_id = str(result_block.get("tool_use_id") or "")
    if not tool_use_id:
        return
    for item in output:
        if item.get("type") != "web_search_call" or item.get("id") != tool_use_id:
            continue
        if _web_search_result_is_error(result_block):
            item["status"] = "failed"
            return
        sources = _web_search_sources_from_result(result_block)
        if sources:
            action = item.setdefault("action", {"type": "search"})
            if isinstance(action, dict):
                action["sources"] = sources
        return


def _web_search_result_is_error(result_block: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(result_block, dict):
        return False
    content = result_block.get("content")
    return isinstance(content, dict) and content.get("type") == "web_search_tool_result_error"


def _function_call_item(item_id: str, call_id: str, name: str, arguments: str, status: str) -> Dict[str, Any]:
    return {
        "id": item_id,
        "type": "function_call",
        "status": status,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _coerce_tool_input(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    if value is None:
        return {}
    return {"value": value}


def _anthropic_citations_to_response_annotations(
    citations: Any,
    offset: int,
    text_length: int,
) -> List[Dict[str, Any]]:
    if not isinstance(citations, list):
        return []
    annotations: List[Dict[str, Any]] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        if citation.get("type") != "web_search_result_location":
            continue
        url = citation.get("url")
        if not isinstance(url, str) or not url:
            continue
        start_index = _int_value(citation.get("start_index"))
        end_index = _int_value(citation.get("end_index"))
        if not end_index:
            start_index = offset
            end_index = offset + text_length
        annotations.append({
            "type": "url_citation",
            "start_index": start_index,
            "end_index": end_index,
            "url": url,
            "title": str(citation.get("title") or ""),
        })
    return annotations


def _stringify_tool_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _anthropic_blocks_to_text(blocks: List[Dict[str, Any]]) -> str:
    parts = []
    for block in blocks:
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(part for part in parts if part)


def _merge_adjacent_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for message in messages:
        if merged and merged[-1].get("role") == message.get("role"):
            existing = merged[-1].setdefault("content", [])
            content = message.get("content") or []
            if isinstance(existing, list) and isinstance(content, list):
                existing.extend(content)
                continue
        merged.append(message)
    return merged


def _instruction_text(instructions: Any) -> str:
    if isinstance(instructions, str):
        return instructions
    if isinstance(instructions, list):
        parts = []
        for item in instructions:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("input_text", "text"):
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(instructions) if instructions is not None else ""


def _coerce_max_tokens(body: Dict[str, Any]) -> int:
    value = body.get("max_output_tokens", body.get("max_tokens", DEFAULT_MAX_TOKENS))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AnthropicConversionError("max_output_tokens must be an integer")
    if parsed < 0:
        raise AnthropicConversionError("max_output_tokens must be non-negative")
    return parsed


def _response_id_from_anthropic(value: Any) -> str:
    raw = str(value or "anthropic")
    if raw.startswith("resp_"):
        return raw
    return f"resp_{raw}"


def _json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_version_suffix(base_url: str) -> bool:
    segment = base_url.split("/")[-1]
    return segment.startswith("v") and len(segment) > 1 and segment[1].isdigit()
