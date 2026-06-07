import json
import unittest

from anthropic_adapter import (
    AnthropicConversionError,
    AnthropicSseToResponsesConverter,
    anthropic_message_to_response,
    anthropic_messages_url,
    responses_to_anthropic_messages,
)


class ResponsesToAnthropicTest(unittest.TestCase):
    def test_basic_request_maps_system_and_messages(self):
        converted = responses_to_anthropic_messages({
            "model": "claude/claude-sonnet-4-5",
            "instructions": "You are terse.",
            "input": "hello",
            "max_output_tokens": 300,
            "temperature": 0.2,
        }, upstream_model="claude-sonnet-4-5")

        self.assertEqual(converted["model"], "claude-sonnet-4-5")
        self.assertEqual(converted["system"], "You are terse.")
        self.assertEqual(converted["max_tokens"], 300)
        self.assertEqual(converted["temperature"], 0.2)
        self.assertEqual(converted["messages"][0]["role"], "user")
        self.assertEqual(converted["messages"][0]["content"][0]["text"], "hello")

    def test_function_tool_maps_to_input_schema(self):
        converted = responses_to_anthropic_messages({
            "model": "claude",
            "input": "use tool",
            "tools": [{
                "type": "function",
                "name": "lookup",
                "description": "Lookup data",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            }],
            "tool_choice": {"type": "function", "name": "lookup"},
            "parallel_tool_calls": False,
        })

        self.assertEqual(converted["tools"][0]["name"], "lookup")
        self.assertEqual(converted["tools"][0]["input_schema"]["required"], ["q"])
        self.assertEqual(converted["tool_choice"]["type"], "tool")
        self.assertEqual(converted["tool_choice"]["name"], "lookup")
        self.assertTrue(converted["tool_choice"]["disable_parallel_tool_use"])

    def test_web_search_maps_to_verified_anthropic_server_tool(self):
        converted = responses_to_anthropic_messages({
            "model": "claude",
            "input": "search",
            "tools": [{
                "type": "web_search",
                "max_uses": 3,
                "filters": {"allowed_domains": ["example.com"]},
                "user_location": {
                    "type": "approximate",
                    "country": "US",
                    "city": "San Francisco",
                    "region": "California",
                    "timezone": "America/Los_Angeles",
                },
            }],
            "tool_choice": {"type": "web_search"},
        })

        tool = converted["tools"][0]
        self.assertEqual(tool["type"], "web_search_20250305")
        self.assertEqual(tool["name"], "web_search")
        self.assertEqual(tool["max_uses"], 3)
        self.assertEqual(tool["allowed_domains"], ["example.com"])
        self.assertEqual(tool["user_location"]["country"], "US")
        self.assertEqual(converted["tool_choice"]["type"], "tool")
        self.assertEqual(converted["tool_choice"]["name"], "web_search")

    def test_unsupported_custom_tool_is_rejected_instead_of_stubbed(self):
        with self.assertRaises(AnthropicConversionError):
            responses_to_anthropic_messages({
                "model": "claude",
                "input": "run",
                "tools": [{"type": "custom", "name": "freeform_exec"}],
            })

    def test_unmappable_web_search_controls_are_rejected(self):
        with self.assertRaises(AnthropicConversionError):
            responses_to_anthropic_messages({
                "model": "claude",
                "input": "search cached only",
                "tools": [{"type": "web_search", "external_web_access": False}],
            })

    def test_web_search_allows_only_one_domain_filter_mode(self):
        with self.assertRaises(AnthropicConversionError):
            responses_to_anthropic_messages({
                "model": "claude",
                "input": "search",
                "tools": [{
                    "type": "web_search",
                    "filters": {
                        "allowed_domains": ["example.com"],
                        "blocked_domains": ["blocked.example"],
                    },
                }],
            })

    def test_tool_history_maps_to_tool_use_and_result(self):
        converted = responses_to_anthropic_messages({
            "model": "claude",
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run"}]},
                {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": '{"cmd":"pwd"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": "C:/repo"},
            ],
        })

        self.assertEqual(converted["messages"][1]["role"], "assistant")
        self.assertEqual(converted["messages"][1]["content"][0]["type"], "tool_use")
        self.assertEqual(converted["messages"][1]["content"][0]["input"], {"cmd": "pwd"})
        self.assertEqual(converted["messages"][2]["role"], "user")
        self.assertEqual(converted["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(converted["messages"][2]["content"][0]["tool_use_id"], "call_1")

    def test_image_data_url_maps_to_anthropic_image_block(self):
        converted = responses_to_anthropic_messages({
            "model": "claude",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            }],
        })

        image = converted["messages"][0]["content"][1]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["source"]["type"], "base64")
        self.assertEqual(image["source"]["media_type"], "image/png")

    def test_missing_messages_is_rejected(self):
        with self.assertRaises(AnthropicConversionError):
            responses_to_anthropic_messages({"model": "claude"})


class AnthropicToResponsesTest(unittest.TestCase):
    def test_text_tool_and_cache_usage_response(self):
        converted = anthropic_message_to_response({
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        })

        self.assertEqual(converted["status"], "completed")
        self.assertEqual(converted["output"][0]["content"][0]["text"], "hi")
        self.assertEqual(converted["output"][1]["type"], "function_call")
        self.assertEqual(converted["output"][1]["arguments"], '{"q":"x"}')
        self.assertEqual(converted["usage"]["input_tokens"], 15)
        self.assertEqual(converted["usage"]["input_tokens_details"]["cached_tokens"], 3)
        self.assertEqual(converted["usage"]["cache_creation_input_tokens"], 2)

    def test_web_search_response_maps_call_and_citations(self):
        converted = anthropic_message_to_response({
            "id": "msg_search",
            "type": "message",
            "role": "assistant",
            "model": "claude",
            "content": [
                {
                    "type": "server_tool_use",
                    "id": "srvtoolu_1",
                    "name": "web_search",
                    "input": {"query": "claude shannon birth date"},
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": [{
                        "type": "web_search_result",
                        "url": "https://example.com/shannon",
                        "title": "Claude Shannon",
                        "page_age": "April 30, 2025",
                    }],
                },
                {
                    "type": "text",
                    "text": "Claude Shannon was born in 1916.",
                    "citations": [{
                        "type": "web_search_result_location",
                        "url": "https://example.com/shannon",
                        "title": "Claude Shannon",
                    }],
                },
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

        self.assertEqual(converted["output"][0]["type"], "message")
        self.assertEqual(converted["output"][1]["type"], "web_search_call")
        self.assertEqual(converted["output"][1]["action"]["query"], "claude shannon birth date")
        self.assertEqual(converted["output"][1]["action"]["sources"][0]["url"], "https://example.com/shannon")
        annotation = converted["output"][0]["content"][0]["annotations"][0]
        self.assertEqual(annotation["type"], "url_citation")
        self.assertEqual(annotation["url"], "https://example.com/shannon")

    def test_max_tokens_maps_to_incomplete(self):
        converted = anthropic_message_to_response({
            "id": "msg_len",
            "model": "claude",
            "content": [{"type": "text", "text": "truncated"}],
            "stop_reason": "max_tokens",
            "usage": {},
        })

        self.assertEqual(converted["status"], "incomplete")
        self.assertEqual(converted["incomplete_details"]["reason"], "max_output_tokens")


class AnthropicSseConverterTest(unittest.TestCase):
    def test_text_stream_emits_responses_text_events(self):
        converter = AnthropicSseToResponsesConverter({"model": "claude"})
        sse = (
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","model":"claude","usage":{"input_tokens":4}}}\n\n'
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\n'
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        out = converter.push_bytes(sse.encode("utf-8"))
        out += converter.finish()

        self.assertIn("response.created", out)
        self.assertIn("response.output_text.delta", out)
        self.assertIn("response.output_text.done", out)
        self.assertIn("response.completed", out)
        self.assertIn("data: [DONE]", out)

    def test_tool_stream_emits_argument_events(self):
        converter = AnthropicSseToResponsesConverter({"model": "claude"})
        sse = (
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_2","model":"claude","usage":{}}}\n\n'
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"lookup","input":{}}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":"}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\\"x\\"}"}}\n\n'
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        out = converter.push_bytes(sse.encode("utf-8"))

        self.assertIn("response.function_call_arguments.delta", out)
        self.assertIn("response.function_call_arguments.done", out)
        arguments = []
        for block in out.split("\n\n"):
            if "response.function_call_arguments.done" not in block:
                continue
            data_line = next(line for line in block.splitlines() if line.startswith("data:"))
            arguments.append(json.loads(data_line[5:].strip())["arguments"])
        self.assertEqual(arguments[-1], '{"q":"x"}')

    def test_web_search_stream_emits_web_search_call_events(self):
        converter = AnthropicSseToResponsesConverter({"model": "claude"})
        sse = (
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_ws","model":"claude","usage":{}}}\n\n'
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"server_tool_use","id":"srvtoolu_1","name":"web_search","input":{}}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":\\"latest news\\"}"}}\n\n'
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
            'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"web_search_tool_result","tool_use_id":"srvtoolu_1","content":[{"type":"web_search_result","url":"https://example.com","title":"Example"}]}}\n\n'
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        out = converter.push_bytes(sse.encode("utf-8"))

        self.assertIn("response.web_search_call.in_progress", out)
        self.assertIn("response.web_search_call.searching", out)
        self.assertIn("response.web_search_call.completed", out)
        done_items = []
        for block in out.split("\n\n"):
            if "response.output_item.done" not in block:
                continue
            data_line = next(line for line in block.splitlines() if line.startswith("data:"))
            done_items.append(json.loads(data_line[5:].strip())["item"])
        self.assertEqual(done_items[-1]["type"], "web_search_call")
        self.assertEqual(done_items[-1]["action"]["query"], "latest news")


class UrlHelperTest(unittest.TestCase):
    def test_anthropic_messages_url(self):
        self.assertEqual(anthropic_messages_url("https://api.anthropic.com"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(anthropic_messages_url("https://api.anthropic.com/v1"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(anthropic_messages_url("https://api.anthropic.com/v1/messages"), "https://api.anthropic.com/v1/messages")
        self.assertEqual(anthropic_messages_url("https://proxy.example/v1#"), "https://proxy.example/v1/messages")


if __name__ == "__main__":
    unittest.main()
