import io
import json
import socket
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
    _set_amr_store_path,
    _set_media_approval_reviewer,
    _set_request_log_config,
    _set_provider_store_path,
    _set_upstream_policy,
    _upstream_request,
    _upstream_request_for_provider,
)
from request_logs import RequestLogStore


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
    def tearDown(self):
        _set_upstream_policy()

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
    def test_can_use_system_proxy_when_bypass_disabled(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        result = _upstream_request(
            "POST",
            "https://api.test/v1/chat/completions",
            {},
            b"{}",
            bypass_system_proxy=False,
        )

        self.assertIs(result, mock_resp)
        mock_build_opener.assert_called_once_with()

    @patch("proxy_server.urllib.request.build_opener")
    def test_stream_adds_accept_header(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_opener.open.return_value = mock_resp
        mock_build_opener.return_value = mock_opener

        _upstream_request("POST", "https://api.test/v1", {}, b"{}", stream=True)

        req = mock_opener.open.call_args[0][0]
        self.assertEqual(req.get_header("Accept"), "text/event-stream")

    @patch("proxy_server.urllib.request.build_opener")
    def test_global_timeout_and_retry_policy(self, mock_build_opener):
        _set_upstream_policy(timeout_seconds=7, retry_attempts=1, retry_backoff_ms=0)
        mock_opener = MagicMock()
        mock_resp = MagicMock()
        mock_opener.open.side_effect = [urllib.error.URLError("temporary"), mock_resp]
        mock_build_opener.return_value = mock_opener

        result = _upstream_request("POST", "https://api.test/v1", {}, b"{}")

        self.assertIs(result, mock_resp)
        self.assertEqual(mock_opener.open.call_count, 2)
        self.assertEqual(mock_opener.open.call_args_list[0].kwargs["timeout"], 7)

    @patch("proxy_server._upstream_request")
    def test_provider_timeout_and_retry_policy_override_global(self, mock_upstream_request):
        _set_upstream_policy(timeout_seconds=120, retry_attempts=0, retry_backoff_ms=250)
        provider = {
            "proxy_profile": {
                "bypass_system_proxy": False,
                "upstream_timeout_seconds": 9,
                "retry_attempts": 2,
                "retry_backoff_ms": 5,
            }
        }
        mock_resp = MagicMock()
        mock_upstream_request.return_value = mock_resp

        result = _upstream_request_for_provider(provider, "POST", "https://api.test/v1", {}, body=b"{}")

        self.assertIs(result, mock_resp)
        kwargs = mock_upstream_request.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 9)
        self.assertEqual(kwargs["retry_attempts"], 2)
        self.assertEqual(kwargs["retry_backoff_ms"], 5)
        self.assertFalse(kwargs["bypass_system_proxy"])


class LocalProxyServerTest(unittest.TestCase):
    def test_status_when_stopped(self):
        server = LocalProxyServer(port=18080)
        status = server.status()
        self.assertFalse(status["running"])
        self.assertEqual(status["port"], 18080)
        self.assertEqual(status["requested_port"], 18080)
        self.assertEqual(status["base_url"], "")
        self.assertFalse(status["port_backoff"]["used"])

    def test_status_exposes_upstream_policy_after_start(self):
        server = LocalProxyServer(
            port=18085,
            upstream_timeout_seconds=33,
            retry_attempts=2,
            retry_backoff_ms=10,
        )
        try:
            self.assertTrue(server.start())
            status = server.status()
            self.assertEqual(status["upstream_timeout_seconds"], 33)
            self.assertEqual(status["retry_attempts"], 2)
            self.assertEqual(status["retry_backoff_ms"], 10)
        finally:
            server.stop()
            _set_upstream_policy()

    def test_start_installs_and_stop_clears_media_approval_reviewer(self):
        reviewer = lambda action, profile, provider: {
            "decision": "accept",
            "risk_level": "low",
            "reason": "Allowed.",
        }
        server = LocalProxyServer(port=18086, media_approval_reviewer=reviewer)
        try:
            self.assertTrue(server.start())
            self.assertTrue(server.status()["media_auto_approval_reviewer_connected"])
        finally:
            server.stop()
        self.assertFalse(server.status()["media_auto_approval_reviewer_connected"])

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
        self.assertEqual(status["port_backoff"]["host"], "127.0.0.1")
        self.assertGreaterEqual(status["port_backoff"]["range_end"], status["port"])
        self.assertEqual(status["last_start_error"], "")
        self.assertEqual(status["base_url"], f"http://127.0.0.1:{status['port']}/v1")
        server2.stop()
        server1.stop()

    def test_external_port_conflict_auto_backs_off(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        occupied_port = int(blocker.getsockname()[1])
        server = LocalProxyServer(port=occupied_port)
        try:
            self.assertTrue(server.start())
            status = server.status()
            self.assertTrue(status["running"])
            self.assertNotEqual(status["port"], occupied_port)
            self.assertEqual(status["requested_port"], occupied_port)
            self.assertTrue(status["port_backoff"]["used"])
            self.assertEqual(status["port_backoff"]["from"], occupied_port)
            self.assertEqual(status["port_backoff"]["to"], status["port"])
            self.assertEqual(status["last_start_error"], "")
        finally:
            server.stop()
            blocker.close()

    def test_sets_provider_store_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "providers.json")
            server = LocalProxyServer(port=18084, provider_store_path=path)
            server.start()
            self.assertEqual(str(server.status()["provider_store_path"]), path)
            server.stop()

    def test_sets_amr_store_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "amr_groups.json")
            server = LocalProxyServer(port=18087, amr_store_path=path)
            server.start()
            self.assertEqual(str(server.status()["amr_store_path"]), path)
            server.stop()
            _set_amr_store_path("")


class ProxyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "providers.json"
        self.amr_store_path = Path(self.tmpdir.name) / "amr_groups.json"
        _set_provider_store_path(str(self.store_path))
        _set_amr_store_path(str(self.amr_store_path))
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
        _set_amr_store_path("")
        _set_request_log_config("")
        _set_media_approval_reviewer(None)

    def _write_providers(self, data):
        self.store_path.write_text(json.dumps(data), encoding="utf-8")

    def _write_amr(self, groups):
        self.amr_store_path.write_text(
            json.dumps({"schema_version": 1, "groups": groups}),
            encoding="utf-8",
        )

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
    def test_chat_completions_non_streaming_writes_metadata_log(self, mock_upstream):
        log_path = Path(self.tmpdir.name) / "proxy_requests.jsonl"
        _set_request_log_config(
            str(log_path),
            retention_days=30,
            max_mb=1,
            currency_settings={
                "display_currency": "USD",
                "exchange_rate_manual_overrides": {},
            },
        )
        self._write_providers({
            "providers": [
                {
                    "id": "openai-main",
                    "short_alias": "openai",
                    "display_name": "OpenAI",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-openai",
                    "native_currency": "USD",
                    "pricing": {"input_per_million": 1.0, "output_per_million": 2.0},
                    "models": [{"id": "gpt-5", "enabled": True}],
                }
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "gpt-5",
            "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/chat/completions",
            body={"model": "openai/gpt-5", "messages": [{"role": "user", "content": "private prompt"}]},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        entries = RequestLogStore(log_path).read_entries(limit=10)["entries"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["endpoint"], "chat_completions")
        self.assertEqual(entry["provider_id"], "openai-main")
        self.assertEqual(entry["model"], "openai/gpt-5")
        self.assertEqual(entry["upstream_model"], "gpt-5")
        self.assertEqual(entry["usage"]["input_tokens"], 10)
        self.assertEqual(entry["usage"]["output_tokens"], 5)
        self.assertTrue(entry["cost_estimate"]["estimate"])
        log_text = log_path.read_text(encoding="utf-8")
        self.assertNotIn("private prompt", log_text)
        self.assertNotIn("sk-openai", log_text)

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

    @patch("proxy_server._upstream_request")
    def test_image_generation_uses_default_media_provider(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "text-main",
                    "short_alias": "txt",
                    "display_name": "Text Provider",
                    "enabled": True,
                    "base_url": "https://text.example.test/v1",
                    "api_key": "sk-text",
                    "capabilities": {"text": True, "images": False},
                    "models": [{"id": "gpt-5", "enabled": True}],
                },
                {
                    "id": "image-main",
                    "short_alias": "img",
                    "display_name": "Image Provider",
                    "enabled": True,
                    "base_url": "https://image.example.test/v1",
                    "api_format": "openai_images",
                    "api_key": "sk-image",
                    "user_agent": "ImageUA/1.0",
                    "capabilities": {"images": True},
                    "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "gpt-image-1", "enabled": True, "capabilities": {"images": True}}],
                },
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"created": 1, "data": [{"url": "https://example.test/a.png"}]}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/images/generations",
            body={"model": "img/gpt-image-1", "prompt": "test image"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode())["data"][0]["url"], "https://example.test/a.png")
        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://image.example.test/v1/images/generations")
        self.assertEqual(args[0][2]["User-Agent"], "ImageUA/1.0")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "gpt-image-1")

    @patch("proxy_server._upstream_request")
    def test_image_generation_uses_per_model_media_override(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "default-image",
                    "short_alias": "default",
                    "display_name": "Default Image Provider",
                    "enabled": True,
                    "base_url": "https://default-image.example.test/v1",
                    "api_format": "openai_images",
                    "api_key": "sk-default",
                    "capabilities": {"images": True},
                    "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "gpt-image-1", "enabled": True, "capabilities": {"images": True}}],
                },
                {
                    "id": "special-image",
                    "short_alias": "special",
                    "display_name": "Special Image Provider",
                    "enabled": True,
                    "base_url": "https://special-image.example.test/v1",
                    "api_format": "openai_images",
                    "api_key": "sk-special",
                    "capabilities": {"images": True},
                    "media_profile": {
                        "openai_compatible_media": True,
                        "image_model_overrides": {"cover-art": "gpt-image-1.5"},
                    },
                    "models": [{"id": "gpt-image-1.5", "enabled": True, "capabilities": {"images": True}}],
                },
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"created": 1, "data": [{"b64_json": "..."}]}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/images/generations",
            body={"model": "cover-art", "prompt": "album cover"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://special-image.example.test/v1/images/generations")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "gpt-image-1.5")

    @patch("proxy_server._upstream_request")
    def test_video_create_uses_default_video_provider(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "video-main",
                    "short_alias": "vid",
                    "display_name": "Video Provider",
                    "enabled": True,
                    "base_url": "https://video.example.test/v1",
                    "api_format": "openai_videos",
                    "api_key": "sk-video",
                    "capabilities": {"videos": True},
                    "media_profile": {"default_video_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "sora-2", "enabled": True, "capabilities": {"videos": True}}],
                },
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "video_1", "object": "video", "status": "queued"}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/videos",
            body={"model": "sora-2", "prompt": "test video"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode())["id"], "video_1")
        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://video.example.test/v1/videos")

    @patch("proxy_server._upstream_request")
    def test_video_retrieve_uses_default_video_provider(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "video-main",
                    "short_alias": "vid",
                    "display_name": "Video Provider",
                    "enabled": True,
                    "base_url": "https://video.example.test/v1",
                    "api_format": "openai_videos",
                    "api_key": "sk-video",
                    "capabilities": {"videos": True},
                    "media_profile": {"default_video_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "sora-2", "enabled": True, "capabilities": {"videos": True}}],
                },
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "video_1", "object": "video", "status": "completed"}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler("/v1/videos/video_1", method="GET")

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode())["status"], "completed")
        args = mock_upstream.call_args
        self.assertEqual(args[0][0], "GET")
        self.assertEqual(args[0][1], "https://video.example.test/v1/videos/video_1")

    @patch("proxy_server._upstream_request")
    def test_media_auto_approval_decline_blocks_upstream(self, mock_upstream):
        log_path = Path(self.tmpdir.name) / "proxy_requests.jsonl"
        _set_request_log_config(str(log_path))
        self._write_providers({
            "providers": [
                {
                    "id": "image-main",
                    "short_alias": "img",
                    "display_name": "Image Provider",
                    "enabled": True,
                    "base_url": "https://image.example.test/v1",
                    "api_format": "openai_images",
                    "api_key": "sk-image",
                    "capabilities": {"images": True},
                    "approval_profile": {"mode": "proxy_auto_approve"},
                    "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "gpt-image-1", "enabled": True, "capabilities": {"images": True}}],
                },
            ]
        })

        _set_media_approval_reviewer(
            lambda action, profile, provider: {
                "decision": "decline",
                "risk_level": "high",
                "reason": "Media request is outside policy.",
            }
        )

        handler, raw = self._make_handler(
            "/v1/images/generations",
            body={"model": "gpt-image-1", "prompt": "private prompt"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body.decode())["error"]["type"], "media_auto_approval_declined")
        mock_upstream.assert_not_called()
        self.assertNotIn("private prompt", log_path.read_text(encoding="utf-8"))

    @patch("proxy_server._upstream_request")
    def test_media_auto_approval_runs_for_submit_poll_and_cancel(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "video-main",
                    "short_alias": "vid",
                    "display_name": "Video Provider",
                    "enabled": True,
                    "base_url": "https://video.example.test/v1",
                    "api_format": "openai_videos",
                    "api_key": "sk-video",
                    "capabilities": {"videos": True},
                    "approval_profile": {"mode": "proxy_auto_approve"},
                    "media_profile": {"default_video_provider": True, "openai_compatible_media": True},
                    "models": [{"id": "sora-2", "enabled": True, "capabilities": {"videos": True}}],
                },
            ]
        })
        seen_operations = []

        def reviewer(action, profile, provider):
            seen_operations.append(action["media"]["operation"])
            self.assertNotIn("prompt", json.dumps(action).lower())
            return {"decision": "accept", "risk_level": "low", "reason": "Allowed media operation."}

        _set_media_approval_reviewer(reviewer)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "video_1", "object": "video", "status": "queued"}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        self._make_handler(
            "/v1/videos",
            body={"model": "sora-2", "prompt": "private video prompt"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        self._make_handler("/v1/videos/video_1", method="GET")
        self._make_handler("/v1/videos/video_1", method="DELETE")

        self.assertEqual(seen_operations, ["submit", "poll", "cancel"])
        self.assertEqual(mock_upstream.call_count, 3)

    def test_adapter_required_media_provider_returns_clear_error(self):
        self._write_providers({
            "providers": [
                {
                    "id": "ark",
                    "short_alias": "ark",
                    "display_name": "Ark",
                    "enabled": True,
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "sk-ark",
                    "capabilities": {"videos": True},
                    "media_profile": {"default_video_provider": True, "adapter_required": True, "openai_compatible_media": False},
                    "models": [{"id": "seedance", "enabled": True, "capabilities": {"videos": True}}],
                }
            ]
        })

        handler, raw = self._make_handler(
            "/v1/videos",
            body={"model": "seedance", "prompt": "test video"},
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 400)
        response_json = json.loads(body.decode())
        self.assertEqual(response_json["error"]["type"], "media_adapter_required")
        self.assertIn("Vendor media payload conversion is not implemented yet", response_json["error"]["message"])

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
    def test_responses_amr_routes_vision_request_to_capable_candidate(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "openai-main",
                    "short_alias": "openai",
                    "display_name": "OpenAI",
                    "enabled": True,
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-openai",
                    "models": [{"id": "gpt-5", "enabled": True}],
                },
                {
                    "id": "qwen-cn",
                    "short_alias": "qwen",
                    "display_name": "Qwen",
                    "enabled": True,
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "sk-qwen",
                    "models": [{"id": "qwen-vl", "enabled": True}],
                },
            ]
        })
        self._write_amr([
            {
                "id": "coder-pro",
                "display_name": "Coder Pro",
                "candidates": [
                    {
                        "id": "openai-main/gpt-5",
                        "provider_id": "openai-main",
                        "model_id": "gpt-5",
                        "priority": 1,
                        "enabled": True,
                        "context_window": 128000,
                        "capabilities": {"text": True, "vision": False},
                    },
                    {
                        "id": "qwen-cn/qwen-vl",
                        "provider_id": "qwen-cn",
                        "model_id": "qwen-vl",
                        "priority": 2,
                        "enabled": True,
                        "context_window": 128000,
                        "capabilities": {"text": True, "vision": True},
                    },
                ],
            }
        ])
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-vision",
            "object": "chat.completion",
            "model": "qwen-vl",
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        self._make_handler(
            "/v1/responses",
            body={
                "model": "coder-pro",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "describe"},
                            {"type": "input_image", "image_url": "https://example.test/a.png"},
                        ],
                    }
                ],
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "qwen-vl")

    @patch("proxy_server._upstream_request")
    def test_responses_amr_custom_tool_requires_custom_tool_candidate(self, mock_upstream):
        self._write_providers({
            "providers": [
                {
                    "id": "basic-tools",
                    "short_alias": "basic",
                    "display_name": "Basic Tools",
                    "enabled": True,
                    "base_url": "https://basic.example.test/v1",
                    "api_key": "sk-basic",
                    "models": [{"id": "basic-model", "enabled": True}],
                },
                {
                    "id": "custom-tools",
                    "short_alias": "custom",
                    "display_name": "Custom Tools",
                    "enabled": True,
                    "base_url": "https://custom.example.test/v1",
                    "api_key": "sk-custom",
                    "models": [{"id": "custom-model", "enabled": True}],
                },
            ]
        })
        self._write_amr([
            {
                "id": "tool-pro",
                "display_name": "Tool Pro",
                "candidates": [
                    {
                        "id": "basic-tools/basic-model",
                        "provider_id": "basic-tools",
                        "model_id": "basic-model",
                        "priority": 1,
                        "enabled": True,
                        "context_window": 128000,
                        "capabilities": {"text": True, "tools": True, "custom_tools": False},
                    },
                    {
                        "id": "custom-tools/custom-model",
                        "provider_id": "custom-tools",
                        "model_id": "custom-model",
                        "priority": 2,
                        "enabled": True,
                        "context_window": 128000,
                        "capabilities": {"text": True, "tools": True, "custom_tools": True},
                    },
                ],
            }
        ])
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "chatcmpl-tool",
            "object": "chat.completion",
            "model": "custom-model",
            "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        }).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        self._make_handler(
            "/v1/responses",
            body={
                "model": "tool-pro",
                "input": "run custom tool",
                "tools": [{"type": "custom", "name": "shell"}],
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://custom.example.test/v1/chat/completions")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "custom-model")

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

    @patch("proxy_server._upstream_request")
    def test_domestic_partial_responses_allows_verified_input_image(self, mock_upstream):
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
                        "profile_id": "alibaba_bailian",
                        "partial_compatibility": True,
                        "requires_adapter": True,
                        "verified_docs_url": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
                    },
                    "models": [{"id": "qwen-plus", "enabled": True}],
                }
            ]
        })
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"id": "resp_1", "object": "response"}).encode()
        mock_resp.getcode.return_value = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_upstream.return_value = mock_resp

        handler, raw = self._make_handler(
            "/v1/responses",
            body={
                "model": "qwen/qwen-plus",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "describe"},
                            {"type": "input_image", "image_url": "https://example.test/a.png"},
                        ],
                    }
                ],
            },
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        status, headers, body = self._parse_response(raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode())["id"], "resp_1")
        args = mock_upstream.call_args
        self.assertEqual(args[0][1], "https://dashscope.aliyuncs.com/compatible-mode/v1/responses")
        upstream_body = json.loads(args[1]["body"])
        self.assertEqual(upstream_body["model"], "qwen-plus")

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
