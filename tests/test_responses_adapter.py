import unittest
import json

from responses_adapter import (
    responses_to_chat_completions,
    chat_completion_to_response,
    ChatSseToResponsesConverter,
    responses_error_from_upstream,
    chat_completions_url,
    models_url,
    responses_url,
    is_responses_proxy_path,
    is_chat_completions_proxy_path,
    is_models_proxy_path,
)


class ResponsesToChatTest(unittest.TestCase):
    def test_basic_conversion(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "instructions": "You are helpful.",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}
            ],
            "max_output_tokens": 512,
            "temperature": 0.2,
            "stream": True,
            "tools": [
                {"type": "function", "name": "lookup", "description": "Lookup data", "parameters": {"type": "object"}}
            ],
        })
        self.assertEqual(converted["model"], "gpt-5-mini")
        self.assertEqual(converted["messages"], [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
        ])
        self.assertEqual(converted["max_tokens"], 512)
        self.assertEqual(converted["temperature"], 0.2)
        self.assertTrue(converted["stream"])
        self.assertEqual(converted["stream_options"]["include_usage"], True)
        self.assertEqual(converted["tools"][0]["type"], "function")
        self.assertEqual(converted["tools"][0]["function"]["name"], "lookup")

    def test_multimodal_content_preserves_input_image_blocks(self):
        converted = responses_to_chat_completions({
            "model": "vision-model",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    {"type": "input_text", "text": "then answer"},
                ],
            }],
        })

        content = converted["messages"][0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "look"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,AAAA")
        self.assertEqual(content[2], {"type": "text", "text": "then answer"})

    def test_tool_outputs_use_compact_json_when_structured(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{ \"b\": 2, \"a\": 1 }"},
                {"type": "function_call_output", "call_id": "call_1", "output": {"z": True, "a": [2, 1]}},
                {"type": "custom_tool_call_output", "call_id": "call_2", "output": ["main.py", "app.py"]},
            ],
        })

        self.assertEqual(converted["messages"][0]["tool_calls"][0]["function"]["arguments"], "{ \"b\": 2, \"a\": 1 }")
        self.assertEqual(converted["messages"][1]["content"], '{"a":[2,1],"z":true}')
        self.assertEqual(converted["messages"][2]["content"], '["main.py","app.py"]')

    def test_json_string_tool_output_is_canonicalized_when_parseable(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "{ \"z\": true, \"a\": [2, 1] }"},
            ],
        })

        self.assertEqual(converted["messages"][1]["content"], '{"a":[2,1],"z":true}')

    def test_developer_role_becomes_system(self):
        converted = responses_to_chat_completions({
            "model": "deepseek-chat",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            ],
        })
        self.assertEqual(converted["messages"][0]["role"], "system")
        self.assertEqual(converted["messages"][0]["content"], "dev")
        self.assertEqual(converted["messages"][1]["role"], "user")

    def test_collapses_multiple_system_messages(self):
        converted = responses_to_chat_completions({
            "model": "MiniMax-M2.7",
            "instructions": "root system",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "late developer"}]},
            ],
        })
        self.assertEqual(converted["messages"][0]["role"], "system")
        self.assertEqual(converted["messages"][0]["content"], "root system\n\nlate developer")
        system_count = sum(1 for m in converted["messages"] if m["role"] == "system")
        self.assertEqual(system_count, 1)

    def test_latest_reminder_to_user(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "input": [
                {"type": "message", "role": "latest_reminder", "content": [{"type": "input_text", "text": "remember"}]},
            ],
        })
        self.assertEqual(converted["messages"][0]["role"], "user")
        self.assertEqual(converted["messages"][0]["content"], "remember")

    def test_replay_reasoning_and_tool_history(self):
        converted = responses_to_chat_completions({
            "model": "deepseek-reasoner",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "use tool"}]},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Need to inspect."}]},
                {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": '{"cmd":"rg foo"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            ],
        })
        self.assertEqual(converted["messages"][1]["role"], "assistant")
        self.assertEqual(converted["messages"][1]["reasoning_content"], "Need to inspect.")
        self.assertEqual(converted["messages"][1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(converted["messages"][2]["role"], "tool")

    def test_normalizes_empty_assistant_messages(self):
        converted = responses_to_chat_completions({
            "model": "deepseek-chat",
            "input": [
                {"type": "message", "role": "assistant", "content": None},
                {"type": "message", "role": "assistant", "content": []},
            ],
        })
        self.assertEqual(converted["messages"][0]["content"], "")
        self.assertEqual(converted["messages"][1]["content"], "")

    def test_drops_tool_controls_when_no_tools(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "input": "hi",
            "tools": [{"type": "unknown_builtin", "name": "unsupported"}],
            "tool_choice": {"type": "required"},
            "parallel_tool_calls": True,
        })
        self.assertNotIn("tools", converted)
        self.assertNotIn("tool_choice", converted)
        self.assertNotIn("parallel_tool_calls", converted)

    def test_string_tool_choice_is_preserved_when_tools_exist(self):
        converted = responses_to_chat_completions({
            "model": "gpt-5-mini",
            "input": "use tool",
            "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
            "tool_choice": "required",
            "parallel_tool_calls": False,
        })

        self.assertEqual(converted["tool_choice"], "required")
        self.assertFalse(converted["parallel_tool_calls"])


class ChatToResponsesTest(unittest.TestCase):
    def test_basic_conversion(self):
        converted = chat_completion_to_response({
            "id": "chatcmpl_123",
            "created": 1710000000,
            "model": "gpt-5-mini",
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hi there"},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        self.assertEqual(converted["object"], "response")
        self.assertEqual(converted["status"], "completed")
        self.assertEqual(converted["model"], "gpt-5-mini")
        self.assertEqual(converted["usage"]["input_tokens"], 10)
        self.assertEqual(converted["usage"]["output_tokens"], 5)
        self.assertEqual(converted["output"][0]["type"], "message")
        self.assertEqual(converted["output"][0]["content"][0]["text"], "hi there")

    def test_reasoning_tool_calls_and_usage_details(self):
        converted = chat_completion_to_response({
            "id": "chatcmpl_1",
            "created": 123,
            "model": "gpt-5.4",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "reasoning_content": "I should check.",
                    "content": "Let me check.",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'},
                    }],
                },
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        })
        self.assertEqual(converted["output"][0]["type"], "reasoning")
        self.assertEqual(converted["output"][0]["summary"][0]["text"], "I should check.")
        self.assertEqual(converted["output"][1]["type"], "message")
        self.assertEqual(converted["output"][2]["type"], "function_call")
        self.assertEqual(converted["output"][2]["call_id"], "call_1")
        self.assertEqual(converted["usage"]["input_tokens_details"]["cached_tokens"], 3)
        self.assertEqual(converted["usage"]["output_tokens_details"]["reasoning_tokens"], 2)

    def test_length_finish_reason(self):
        converted = chat_completion_to_response({
            "id": "chatcmpl_len",
            "created": 123,
            "model": "gpt-5",
            "choices": [{
                "finish_reason": "length",
                "message": {"role": "assistant", "content": "trunc"},
            }],
            "usage": {},
        })
        self.assertEqual(converted["incomplete_details"]["reason"], "max_output_tokens")

    def test_tool_calls_finish_reason_maps_to_completed(self):
        converted = chat_completion_to_response({
            "id": "chatcmpl_tools",
            "created": 123,
            "model": "gpt-5",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    }],
                },
            }],
            "usage": {},
        })
        # tool_calls should map to "completed" status, not the literal "tool_calls"
        self.assertEqual(converted["status"], "completed")
        output_types = [item["type"] for item in converted["output"]]
        self.assertIn("function_call", output_types)

    def test_gemini_usage_negative_guard(self):
        from responses_adapter import _chat_usage_to_responses_usage
        usage = {
            "promptTokenCount": 5,
            "candidatesTokenCount": 3,
            "cachedContentTokenCount": 10,  # More than prompt tokens
        }
        result = _chat_usage_to_responses_usage(usage)
        self.assertGreaterEqual(result["input_tokens"], 0)
        self.assertEqual(result["input_tokens"], 0)  # max(5-10, 0)


class ChatSseConverterTest(unittest.TestCase):
    def test_stream_to_responses_sse(self):
        converter = ChatSseToResponsesConverter()
        sse_input = (
            'data: {"id":"chatcmpl_1","created":1,"model":"gpt-5","choices":[{"delta":{"content":"hello"}}]}\n\n'
            'data: {"id":"chatcmpl_1","created":1,"model":"gpt-5","choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n'
        )
        out = converter.push_bytes(sse_input.encode("utf-8"))
        out += converter.finish()
        self.assertIn("response.created", out)
        self.assertIn("response.in_progress", out)
        self.assertIn("response.output_text.delta", out)
        self.assertIn("response.output_text.done", out)
        self.assertIn("response.completed", out)
        self.assertIn("hello", out)
        self.assertIn(" world", out)

    def test_stream_tool_call_deltas_finalize_function_call_item(self):
        converter = ChatSseToResponsesConverter()
        sse_input = (
            'data: {"id":"chatcmpl_tool","created":1,"model":"gpt-5","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup"}}]}}]}\n\n'
            'data: {"id":"chatcmpl_tool","created":1,"model":"gpt-5","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"q\\":"}}]}}]}\n\n'
            'data: {"id":"chatcmpl_tool","created":1,"model":"gpt-5","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"docs\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
            'data: [DONE]\n\n'
        )
        out = converter.push_bytes(sse_input.encode("utf-8"))
        out += converter.finish()

        self.assertIn("response.function_call_arguments.delta", out)
        self.assertIn("response.function_call_arguments.done", out)
        done_items = _response_items_from_sse(out, "response.output_item.done")
        tool_items = [item for item in done_items if item.get("type") == "function_call"]
        self.assertEqual(tool_items[-1]["call_id"], "call_1")
        self.assertEqual(tool_items[-1]["name"], "lookup")
        self.assertEqual(tool_items[-1]["arguments"], '{"q":"docs"}')
        completed = _responses_from_sse(out, "response.completed")[-1]
        self.assertEqual(completed["output"][-1]["type"], "function_call")

    def test_error_event(self):
        converter = ChatSseToResponsesConverter()
        sse_input = (
            'event: error\ndata: {"error":{"message":"bad","type":"test_error"}}\n\n'
        )
        out = converter.push_bytes(sse_input.encode("utf-8"))
        self.assertIn("response.failed", out)
        self.assertIn("bad", out)


class ErrorNormalizationTest(unittest.TestCase):
    def test_json_error_passthrough(self):
        err = responses_error_from_upstream(
            400, "application/json",
            b'{"error":{"message":"bad request","type":"invalid_request_error","code":"bad_model"}}',
        )
        self.assertEqual(err["error"]["message"], "bad request")
        self.assertEqual(err["error"]["type"], "invalid_request_error")
        self.assertEqual(err["error"]["code"], "bad_model")

    def test_text_error_wrapped(self):
        err = responses_error_from_upstream(502, "text/html", b"<html>bad</html>")
        self.assertEqual(err["error"]["message"], "<html>bad</html>")
        self.assertEqual(err["error"]["type"], "upstream_error")
        self.assertEqual(err["error"]["code"], "502")


def _responses_from_sse(text, event_name):
    responses = []
    for block in text.split("\n\n"):
        if f"event: {event_name}" not in block:
            continue
        data_line = next((line for line in block.splitlines() if line.startswith("data:")), "")
        if not data_line or data_line.strip() == "data: [DONE]":
            continue
        payload = json.loads(data_line[5:].strip())
        if "response" in payload:
            responses.append(payload["response"])
    return responses


def _response_items_from_sse(text, event_name):
    items = []
    for block in text.split("\n\n"):
        if f"event: {event_name}" not in block:
            continue
        data_line = next((line for line in block.splitlines() if line.startswith("data:")), "")
        if not data_line:
            continue
        payload = json.loads(data_line[5:].strip())
        if "item" in payload:
            items.append(payload["item"])
    return items


class UrlHelperTest(unittest.TestCase):
    def test_chat_completions_url(self):
        self.assertEqual(chat_completions_url("https://api.openai.com"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(chat_completions_url("https://api.openai.com/v1"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(chat_completions_url("https://api.openai.com/v1#"), "https://api.openai.com/v1/chat/completions")

    def test_models_url(self):
        self.assertEqual(models_url("https://api.openai.com"), "https://api.openai.com/v1/models")
        self.assertEqual(models_url("https://api.openai.com/v1"), "https://api.openai.com/v1/models")

    def test_responses_url(self):
        self.assertEqual(responses_url("https://api.openai.com"), "https://api.openai.com/v1/responses")
        self.assertEqual(responses_url("https://api.openai.com/v1"), "https://api.openai.com/v1/responses")
        self.assertEqual(responses_url("https://api.openai.com/v1/responses"), "https://api.openai.com/v1/responses")
        self.assertEqual(responses_url("https://proxy.example/v1#"), "https://proxy.example/v1/responses")

    def test_proxy_path_matchers(self):
        self.assertTrue(is_responses_proxy_path("/v1/responses"))
        self.assertTrue(is_responses_proxy_path("/codex/v1/responses"))
        self.assertTrue(is_chat_completions_proxy_path("/v1/chat/completions"))
        self.assertTrue(is_models_proxy_path("/v1/models"))


class StreamFinalizationTest(unittest.TestCase):
    def test_stream_closed_before_completion(self):
        converter = ChatSseToResponsesConverter()
        sse_input = (
            'data: {"id":"chatcmpl_1","created":1,"model":"gpt-5","choices":[{"delta":{"content":"hello"}}]}\n\n'
        )
        converter.push_bytes(sse_input.encode("utf-8"))
        out = converter.finish()
        self.assertIn("response.failed", out)
        self.assertIn("stream_incomplete", out)

    def test_empty_stream_finalizes_to_failed(self):
        converter = ChatSseToResponsesConverter()
        out = converter.finish()
        self.assertIn("response.failed", out)
        self.assertIn("stream_incomplete", out)

    def test_converted_request_preserves_model(self):
        converted = responses_to_chat_completions({"model": "custom-model-v1"})
        self.assertEqual(converted["model"], "custom-model-v1")

    def test_reasoning_fields_preserved_in_conversion(self):
        converted = responses_to_chat_completions({
            "model": "o3-mini",
            "reasoning": {"effort": "high"},
        })
        self.assertEqual(converted["reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
