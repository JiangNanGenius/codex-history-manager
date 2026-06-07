import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from proxy_server import (
    LocalProxyServer,
    ProxyHandler,
    _build_upstream_headers,
    _extract_model_id_for_upstream,
    _load_providers_with_secrets,
    _resolve_provider_for_model,
    _set_provider_store_path,
    _upstream_request,
)


class ProviderRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "providers.json"
        _set_provider_store_path(str(self.store_path))
        self.store_path.write_text(
            json.dumps({
                "providers": [
                    {
                        "id": "openai-main",
                        "short_alias": "openai",
                        "display_name": "OpenAI",
                        "enabled": True,
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-openai",
                        "user_agent": "Custom-UA/1.0",
                        "headers": {"X-Custom": "value"},
                        "models": [
                            {"id": "gpt-5", "enabled": True},
                            {"id": "gpt-4", "enabled": True},
                        ],
                    },
                    {
                        "id": "qwen-cn",
                        "short_alias": "qwen",
                        "display_name": "Qwen",
                        "enabled": True,
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key": "sk-qwen",
                        "models": [
                            {"id": "qwen3-coder-plus", "enabled": True},
                            {"id": "qwen-vl", "enabled": False},
                        ],
                    },
                    {
                        "id": "disabled-provider",
                        "short_alias": "disabled",
                        "display_name": "Disabled",
                        "enabled": False,
                        "base_url": "https://example.com/v1",
                        "api_key": "sk-disabled",
                        "models": [
                            {"id": "some-model", "enabled": True},
                        ],
                    },
                ]
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_hard_route_by_short_alias(self):
        provider = _resolve_provider_for_model("qwen/qwen3-coder-plus")
        self.assertIsNotNone(provider)
        self.assertEqual(provider["id"], "qwen-cn")

    def test_hard_route_by_provider_id(self):
        provider = _resolve_provider_for_model("openai-main/gpt-5")
        self.assertIsNotNone(provider)
        self.assertEqual(provider["id"], "openai-main")

    def test_hard_route_unknown_prefix_returns_none(self):
        provider = _resolve_provider_for_model("unknown/model")
        self.assertIsNone(provider)

    def test_exact_model_match(self):
        provider = _resolve_provider_for_model("gpt-5")
        self.assertIsNotNone(provider)
        self.assertEqual(provider["id"], "openai-main")

    def test_disabled_provider_not_matched(self):
        provider = _resolve_provider_for_model("some-model")
        self.assertIsNone(provider)

    def test_disabled_model_not_matched(self):
        provider = _resolve_provider_for_model("qwen-vl")
        self.assertIsNone(provider)

    def test_empty_model_returns_none(self):
        provider = _resolve_provider_for_model("")
        self.assertIsNone(provider)

    def test_case_insensitive_match(self):
        provider = _resolve_provider_for_model("GPT-5")
        self.assertIsNotNone(provider)
        self.assertEqual(provider["id"], "openai-main")


class ModelIdExtractionTest(unittest.TestCase):
    def test_provider_prefix_removed(self):
        provider = {"aliases": {}}
        result = _extract_model_id_for_upstream({"model": "qwen/qwen3-coder-plus"}, provider)
        self.assertEqual(result, "qwen3-coder-plus")

    def test_plain_model_preserved(self):
        provider = {"aliases": {}}
        result = _extract_model_id_for_upstream({"model": "gpt-5"}, provider)
        self.assertEqual(result, "gpt-5")

    def test_alias_rewrite(self):
        provider = {"aliases": {"gpt-5": "gpt-5-turbo"}}
        result = _extract_model_id_for_upstream({"model": "gpt-5"}, provider)
        self.assertEqual(result, "gpt-5-turbo")

    def test_alias_after_prefix_removal(self):
        provider = {"aliases": {"qwen3-coder-plus": "qwen3-coder"}}
        result = _extract_model_id_for_upstream({"model": "qwen/qwen3-coder-plus"}, provider)
        self.assertEqual(result, "qwen3-coder")


class HeaderBuilderTest(unittest.TestCase):
    def test_api_key_becomes_bearer(self):
        provider = {"api_key": "sk-test", "user_agent": "", "headers": {}}
        headers = _build_upstream_headers(provider)
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_user_agent_from_provider(self):
        provider = {"api_key": "", "user_agent": "MyBot/1.0", "headers": {}}
        headers = _build_upstream_headers(provider)
        self.assertEqual(headers["User-Agent"], "MyBot/1.0")

    def test_user_agent_from_headers_fallback(self):
        provider = {"api_key": "", "user_agent": "", "headers": {"User-Agent": "Fallback/2.0"}}
        headers = _build_upstream_headers(provider)
        self.assertEqual(headers["User-Agent"], "Fallback/2.0")

    def test_default_user_agent(self):
        provider = {"api_key": "", "user_agent": "", "headers": {}}
        headers = _build_upstream_headers(provider)
        self.assertTrue(headers["User-Agent"].startswith("Codex-Enhance-Manager-Proxy"))

    def test_custom_headers_merged(self):
        provider = {"api_key": "", "user_agent": "", "headers": {"X-Custom": "value", "X-Other": "123"}}
        headers = _build_upstream_headers(provider)
        self.assertEqual(headers["X-Custom"], "value")
        self.assertEqual(headers["X-Other"], "123")

    def test_authorization_not_duplicated_from_headers(self):
        provider = {"api_key": "sk-main", "user_agent": "", "headers": {"Authorization": "Bearer sk-other"}}
        headers = _build_upstream_headers(provider)
        # provider api_key 优先，自定义 headers 中的 Authorization 应被忽略
        self.assertEqual(headers["Authorization"], "Bearer sk-main")
        self.assertNotIn("Bearer sk-other", headers.values())

    def test_no_auth_when_no_key(self):
        provider = {"api_key": "", "user_agent": "", "headers": {}}
        headers = _build_upstream_headers(provider)
        self.assertNotIn("Authorization", headers)

    def test_anthropic_headers_use_x_api_key_and_version(self):
        provider = {
            "api_format": "anthropic",
            "api_key": "sk-anthropic",
            "user_agent": "ClaudeUA/1.0",
            "headers": {"Authorization": "Bearer ignored", "anthropic-beta": "tools-2024-04-04"},
        }
        headers = _build_upstream_headers(provider)
        self.assertEqual(headers["x-api-key"], "sk-anthropic")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["anthropic-beta"], "tools-2024-04-04")
        self.assertEqual(headers["User-Agent"], "ClaudeUA/1.0")
        self.assertNotIn("Authorization", headers)


class LoadProvidersTest(unittest.TestCase):
    def test_loads_from_custom_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "providers.json"
            _set_provider_store_path(str(path))
            path.write_text(
                json.dumps({"providers": [{"id": "test", "enabled": True}]}),
                encoding="utf-8",
            )
            providers = _load_providers_with_secrets()
            self.assertEqual(len(providers), 1)
            self.assertEqual(providers[0]["id"], "test")

    def test_returns_empty_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _set_provider_store_path(str(Path(tmpdir) / "nonexistent.json"))
            providers = _load_providers_with_secrets()
            self.assertEqual(providers, [])

    def test_returns_empty_on_corrupted_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            _set_provider_store_path(str(path))
            path.write_text("not json", encoding="utf-8")
            providers = _load_providers_with_secrets()
            self.assertEqual(providers, [])


class UpstreamRequestTest(unittest.TestCase):
    @patch("proxy_server.urllib.request.build_opener")
    def test_disables_system_proxy(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = _upstream_request("POST", "https://api.test/v1/chat/completions", {}, b"{}")

        # 验证 ProxyHandler 被传入（禁用系统代理）
        call_args = mock_build_opener.call_args
        self.assertIsNotNone(call_args)
        # 第一个参数应该是 ProxyHandler 实例
        handler = call_args[0][0]
        self.assertIsInstance(handler, urllib.request.ProxyHandler)

    @patch("proxy_server.urllib.request.build_opener")
    def test_stream_adds_accept_header(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        _upstream_request("POST", "https://api.test/v1", {}, b"{}", stream=True)

        req = mock_opener.open.call_args[0][0]
        self.assertEqual(req.get_header("Accept"), "text/event-stream")


class LocalProxyServerTest(unittest.TestCase):
    def test_status_when_stopped(self):
        server = LocalProxyServer(port=18080)
        status = server.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["port"], 18080)
        self.assertEqual(status["requested_port"], 18080)
        self.assertEqual(status["base_url"], "")
        self.assertFalse(status["port_backoff"]["used"])

    def test_start_stop_cycle(self):
        server = LocalProxyServer(port=18081)
        ok = server.start()
        self.assertTrue(ok)
        self.assertTrue(server.is_running())
        self.assertTrue(server.status()["running"])

        server.stop()
        self.assertFalse(server.is_running())

    def test_idempotent_start(self):
        server = LocalProxyServer(port=18082)
        self.assertTrue(server.start())
        self.assertTrue(server.start())  # 第二次应直接返回 True
        server.stop()

    def test_port_conflict_auto_backs_off(self):
        server1 = LocalProxyServer(port=18083)
        server2 = LocalProxyServer(port=18083)
        self.assertTrue(server1.start())
        self.assertTrue(server2.start())
        status = server2.status()
        self.assertNotEqual(status["port"], 18083)
        self.assertEqual(status["requested_port"], 18083)
        self.assertTrue(status["port_backoff"]["used"])
        self.assertEqual(status["port_backoff"]["from"], 18083)
        self.assertEqual(status["port_backoff"]["to"], status["port"])
        self.assertEqual(status["base_url"], f"http://127.0.0.1:{status['port']}/v1")
        server2.stop()
        server1.stop()

    def test_sets_provider_store_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "providers.json")
            server = LocalProxyServer(port=18084, provider_store_path=path)
            server.start()
            self.assertEqual(str(server.status()["provider_store_path"]), path)
            server.stop()


class ProxyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "providers.json"
        _set_provider_store_path(str(self.store_path))
        self._write_providers({
            "providers": [
                {
                    "id": "openai-main",
                    "short_alias": "openai",
                    "display_name": "OpenAI",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-openai",
                    "user_agent": "TestUA/1.0",
                    "headers": {"X-Custom": "value"},
                    "models": [{"id": "gpt-5", "enabled": True}],
                }
            ]
        })

    def tearDown(self):
        self.tmpdir.cleanup()
        _set_provider_store_path("")

    def _write_providers(self, data):
        self.store_path.write_text(json.dumps(data), encoding="utf-8")

    def _make_handler(self, path, body=None, method="GET", headers=None):
        request_lines = [f"{method} {path} HTTP/1.1", "Host: localhost"]
        body_bytes = b""
        if body is not None:
            body_bytes = json.dumps(body).encode() if isinstance(body, dict) else body
            request_lines.append(f"Content-Length: {len(body_bytes)}")
        if headers:
            for k, v in headers.items():
                request_lines.append(f"{k}: {v}")
        request_lines.append("")
        request_lines.append("")
        request_bytes = "\r\n".join(request_lines).encode() + body_bytes

        class MockSocket:
            def __init__(self, data):
                self._rfile = io.BytesIO(data)
                self._wfile = io.BytesIO()

            def makefile(self, mode, *args, **kwargs):
                if "r" in mode:
                    return self._rfile
                if "w" in mode:
                    return self._wfile
                raise ValueError(mode)

            def sendall(self, b):
                self._wfile.write(b)

            def close(self):
                pass

        class MockServer:
            def __init__(self):
                self.server_address = ("127.0.0.1", 8080)

        mock_sock = MockSocket(request_bytes)
        handler = ProxyHandler(mock_sock, ("127.0.0.1", 12345), MockServer())
        return handler, mock_sock._wfile.getvalue()

    def _parse_response(self, raw):
        header_end = raw.find(b"\r\n\r\n")
        headers_text = raw[:header_end].decode()
        body = raw[header_end + 4 :]
        lines = headers_text.split("\r\n")
        status_code = int(lines[0].split()[1])
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k] = v
        return status_code, headers, body

    @patch("proxy_server._upstream_request")
    def test_chat_completions_non_streaming(self, mock_upstream):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-5",
            "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/chat/completions",
            body={"model": "openai/gpt-5", "messages": [{"role": "user", "content": "Hi"}]},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        resp_json = json.loads(body.decode())
        self.assertEqual(resp_json["choices"][0]["message"]["content"], "Hello")

        mock_upstream.assert_called_once()
        args = mock_upstream.call_args
        self.assertEqual(args[0][0], "POST")
        self.assertEqual(args[0][1], "https://api.openai.com/v1/chat/completions")
        upstream_headers = args[0][2]
        self.assertEqual(upstream_headers["Authorization"], "Bearer sk-openai")
        self.assertEqual(upstream_headers["User-Agent"], "TestUA/1.0")
        self.assertEqual(upstream_headers["X-Custom"], "value")
        self.assertEqual(upstream_headers["Content-Type"], "application/json")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "gpt-5")

    @patch("proxy_server._upstream_request")
    def test_chat_completions_streaming(self, mock_upstream):
        sse_data = (
            b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [sse_data, b""]
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "text/event-stream"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/chat/completions",
            body={"model": "gpt-5", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/event-stream; charset=utf-8")
        self.assertIn(b"data:", body)

    def test_models_list(self):
        handler, raw = self._make_handler("/v1/models", method="GET")
        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertEqual(data["object"], "list")
        ids = [m["id"] for m in data["data"]]
        self.assertIn("openai/gpt-5", ids)

    @patch("proxy_server._upstream_request")
    def test_responses_endpoint(self, mock_upstream):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-5",
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/responses",
            body={"model": "gpt-5", "input": "test"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        mock_upstream.assert_called_once()
        args = mock_upstream.call_args
        self.assertEqual(args[0][0], "POST")
        self.assertEqual(args[0][1], "https://api.openai.com/v1/chat/completions")
        upstream_body = json.loads(args[1]["body"])
        self.assertIn("messages", upstream_body)

    @patch("proxy_server._upstream_request")
    def test_responses_endpoint_with_native_responses_provider(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "openai-responses",
                    "short_alias": "oresp",
                    "display_name": "OpenAI Responses",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_format": "openai_responses",
                    "api_key": "sk-openai",
                    "models": [{"id": "gpt-5", "enabled": True}],
                }
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": "gpt-5",
            "output": [],
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/responses",
            body={
                "model": "oresp/gpt-5",
                "input": "test",
                "previous_response_id": "resp_prev",
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        response_json = json.loads(body.decode())
        self.assertEqual(response_json["id"], "resp_1")

        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://api.openai.com/v1/responses")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "gpt-5")
        self.assertEqual(upstream_body["previous_response_id"], "resp_prev")
        self.assertNotIn("messages", upstream_body)

    def test_domestic_partial_responses_blocks_unverified_custom_tool(self):
        self._write_providers({
            "providers": [
                {
                    "id": "bailian",
                    "short_alias": "qwen",
                    "display_name": "Bailian",
                    "enabled": True,
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_format": "openai_responses",
                    "api_key": "sk-qwen",
                    "responses_profile": {
                        "domestic_responses": True,
                        "partial_compatibility": True,
                        "requires_adapter": True,
                        "verified_docs_url": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
                    },
                    "models": [{"id": "qwen3.7-plus", "enabled": True}],
                }
            ]
        })

        handler, raw = self._make_handler(
            "/v1/responses",
            body={
                "model": "qwen/qwen3.7-plus",
                "input": "test",
                "tools": [{"type": "custom", "name": "shell"}],
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 400)
        response_json = json.loads(body.decode())
        self.assertEqual(response_json["error"]["type"], "domestic_responses_unsupported")
        self.assertIn("unsupported tool types: custom", response_json["error"]["message"])

    def test_domestic_partial_responses_blocks_compact(self):
        self._write_providers({
            "providers": [
                {
                    "id": "ark",
                    "short_alias": "ark",
                    "display_name": "Ark",
                    "enabled": True,
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_format": "openai_responses",
                    "api_key": "sk-ark",
                    "responses_profile": {
                        "domestic_responses": True,
                        "partial_compatibility": True,
                        "requires_adapter": True,
                        "verified_docs_url": "https://www.volcengine.com/docs/82379/1585128?lang=zh",
                    },
                    "models": [{"id": "doubao-seed", "enabled": True}],
                }
            ]
        })

        handler, raw = self._make_handler(
            "/v1/responses/compact",
            body={"model": "ark/doubao-seed", "input": "compact me"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 400)
        response_json = json.loads(body.decode())
        self.assertEqual(response_json["error"]["type"], "domestic_responses_unsupported")
        self.assertIn("/responses/compact", response_json["error"]["message"])

    @patch("proxy_server._upstream_request")
    def test_responses_endpoint_with_anthropic_provider(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "anthropic-main",
                    "short_alias": "claude",
                    "display_name": "Anthropic",
                    "enabled": True,
                    "base_url": "https://api.anthropic.com",
                    "api_format": "anthropic",
                    "api_key": "sk-claude",
                    "user_agent": "ClaudeUA/1.0",
                    "models": [{"id": "claude-sonnet-4-5", "enabled": True}],
                }
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/responses",
            body={
                "model": "claude/claude-sonnet-4-5",
                "instructions": "Be concise.",
                "input": "test",
                "max_output_tokens": 128,
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        response_json = json.loads(body.decode())
        self.assertEqual(response_json["output"][0]["content"][0]["text"], "OK")

        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://api.anthropic.com/v1/messages")
        upstream_headers = args[0][2]
        self.assertEqual(upstream_headers["x-api-key"], "sk-claude")
        self.assertNotIn("Authorization", upstream_headers)
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "claude-sonnet-4-5")
        self.assertEqual(upstream_body["system"], "Be concise.")
        self.assertEqual(upstream_body["max_tokens"], 128)
        self.assertEqual(upstream_body["messages"][0]["role"], "user")

    @patch("proxy_server._upstream_request")
    def test_upstream_error_passed_through(self, mock_upstream):
        err_body = b'{"error": {"message": "upstream bad gateway"}}'
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            502,
            "Bad Gateway",
            {"Content-Type": "application/json"},
            io.BytesIO(err_body),
        )
        mock_upstream.side_effect = error

        handler, raw = self._make_handler(
            "/v1/chat/completions",
            body={"model": "gpt-5", "messages": [{"role": "user", "content": "Hi"}]},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 502)
        resp_json = json.loads(body.decode())
        self.assertIn("upstream bad gateway", resp_json["error"]["message"])

    def test_provider_not_found(self):
        handler, raw = self._make_handler(
            "/v1/chat/completions",
            body={"model": "unknown-model", "messages": [{"role": "user", "content": "Hi"}]},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 404)
        resp_json = json.loads(body.decode())
        self.assertEqual(resp_json["error"]["type"], "provider_not_found")


if __name__ == "__main__":
    unittest.main()
