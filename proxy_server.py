"""
proxy_server.py - Local OpenAI-compatible proxy server.
本地 OpenAI 兼容代理服务器。

设计意图：
  - 作为独立 HTTP 服务器运行（不依赖 Flask），可在主应用之外单独启停。
  - 接收 Codex CLI 发来的 OpenAI/Responses 格式请求，路由到合适的上游 Provider。
  - 使用 responses_adapter.py 做协议转换：Codex 发 Responses 格式，
    第三方 Provider 多支持 Chat Completions 格式。
  - 支持非流式和 SSE 流式两种模式：流式模式下逐 chunk 转发，不缓存完整响应。

工程权衡：
  - 使用 http.server.BaseHTTPRequestHandler：无需额外依赖，标准库即可运行。
    性能不如 uvicorn/gunicorn，但本地代理并发极低（仅 Codex CLI 一个客户端），
    完全够用。
  - 独立线程 serve_forever：与 Flask 应用解耦，代理可以单独启动、监控、重启。
  - MAX_BODY_SIZE = 10MB：防止恶意/异常请求导致内存耗尽；正常请求
    （即使含图片 base64）通常 <5MB。
  - 自行读取 providers.json：proxy_server 作为独立进程/线程，不依赖 Flask
    的 ProviderRegistry 实例。每次请求前刷新 provider 缓存，保证配置变更
    即时生效，无需重启代理。
  - urllib 而非 requests：避免引入第三方 HTTP 库依赖，标准库足够处理
    OpenAI 兼容的 JSON/SSE 请求。

Windows 平台特殊性：
  - HTTPServer 绑定 127.0.0.1 而非 localhost：避免 Windows hosts 配置
    导致 localhost 解析到 ::1 而 IPv4 连接失败。
  - urllib 在 Windows 上使用系统代理设置（如 IE 代理），可能干扰本地到
    上游的请求。通过 ProxyHandler 禁用 urllib 默认代理。
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from responses_adapter import (
    ChatSseToResponsesConverter,
    chat_completion_to_response,
    is_chat_completions_proxy_path,
    is_models_proxy_path,
    is_responses_proxy_path,
    responses_error_from_upstream,
    responses_to_chat_completions,
)

DEFAULT_PROXY_PORT = 8080
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
DEFAULT_UPSTREAM_TIMEOUT = 120  # 秒

# 全局配置（由 LocalProxyServer 在启动时设置）
_provider_store_path: Optional[Path] = None


def _set_provider_store_path(path: str) -> None:
    """设置 provider registry 存储路径；供 LocalProxyServer 启动时调用。"""
    global _provider_store_path
    _provider_store_path = Path(path).expanduser() if path else None


def _get_provider_store_path() -> Path:
    """获取当前 provider store 路径；回退到默认值。"""
    if _provider_store_path is not None:
        return _provider_store_path
    # 默认路径与 ProviderRegistry 保持一致
    return Path.home() / ".codex_enhance_manager" / "providers.json"


def _load_providers_with_secrets() -> List[Dict[str, Any]]:
    """
    独立读取 providers.json，包含 secrets。

    设计意图：
      - proxy_server 作为独立 HTTP 服务器线程，无法直接访问 Flask 进程中的
        ProviderRegistry 实例。因此自行读取 JSON 文件。
      - 包含 secrets 是因为代理需要 api_key 来构造 Authorization 头。
      - 文件不存在或损坏时返回空列表，不崩溃。

    Returns:
        provider 字典列表（含 api_key 等 secrets）。
    """
    store_path = _get_provider_store_path()
    if not store_path.exists():
        return []
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception:
        return []
    providers = store.get("providers", []) if isinstance(store, dict) else []
    return [p for p in providers if isinstance(p, dict)]


def _resolve_provider_for_model(model_id: str) -> Optional[Dict[str, Any]]:
    """
    根据 model ID 解析应路由到的 provider。

    路由优先级：
      1. Provider-prefix hard routing：model_id 为 "qwen/qwen3-coder-plus" 格式时，
         直接匹配 short_alias 或 id 为 "qwen" 的 provider。
      2. 精确模型 ID 匹配：查找包含该 model_id 的 enabled provider。
      3. 无匹配时返回 None，由调用方返回 404/400 错误。

    工程权衡：
      - 若多个 provider 包含同名模型，选择第一个 enabled 的。
        这在实际中极少发生；若发生，用户应使用 provider-prefix 消除歧义。
      - 只考虑 enabled=True 的 provider，disabled 的 provider 不参与路由。

    Args:
        model_id: 请求中的 model 参数值。

    Returns:
        匹配的 provider 字典，或 None。
    """
    providers = _load_providers_with_secrets()
    if not model_id:
        return None

    # 1. Provider-prefix hard routing: "qwen/qwen3-coder-plus"
    if "/" in model_id:
        prefix, _ = model_id.split("/", 1)
        prefix = prefix.lower().strip()
        for p in providers:
            if not p.get("enabled", True):
                continue
            if p.get("short_alias", "").lower() == prefix or p.get("id", "").lower() == prefix:
                return p
        return None

    # 2. Exact model ID match within enabled providers (model must also be enabled)
    model_id_lower = model_id.lower().strip()
    for p in providers:
        if not p.get("enabled", True):
            continue
        for m in p.get("models", []):
            if not isinstance(m, dict):
                continue
            if not m.get("enabled", True):
                continue
            if m.get("id", "").lower().strip() == model_id_lower:
                return p

    return None


def _extract_model_id_for_upstream(request_json: Dict[str, Any], provider: Dict[str, Any]) -> str:
    """
    提取应发送给上游的模型 ID。

    设计意图：
      - 如果原始请求使用 provider-prefix 格式（如 "qwen/qwen3-coder-plus"），
        上游通常只接受 "qwen3-coder-plus" 部分。
      - 如果 provider 有 aliases，执行 alias 映射。

    Args:
        request_json: 原始请求 JSON。
        provider: 选中的 provider 配置。

    Returns:
        发送给上游的模型 ID 字符串。
    """
    raw_model = request_json.get("model", "")
    if "/" in raw_model:
        _, upstream_model = raw_model.split("/", 1)
    else:
        upstream_model = raw_model

    # Alias rewrite
    aliases = provider.get("aliases", {})
    if isinstance(aliases, dict) and upstream_model in aliases:
        return str(aliases[upstream_model])
    return upstream_model


def _build_upstream_headers(provider: Dict[str, Any]) -> Dict[str, str]:
    """
    构建发送给上游 Provider 的 HTTP 请求头。

    设计意图：
      - Authorization：优先使用 provider 的 api_key；若不存在则不添加。
      - User-Agent：provider 配置中的 user_agent 优先；若未设置则使用默认。
      - 自定义 headers：provider 的 headers 字典中除 Authorization 外的键
        都合并进来，允许用户覆盖默认行为。
      - Content-Type：固定为 application/json。

    安全注意：
      - 不在日志中记录完整 Authorization 值。
      - 不在错误响应中回传 api_key。

    Args:
        provider: provider 配置字典。

    Returns:
        请求头字典。
    """
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
    }

    # User-Agent：provider 级优先
    ua = provider.get("user_agent", "")
    if not ua and isinstance(provider.get("headers"), dict):
        ua = provider["headers"].get("User-Agent", "")
    if not ua:
        ua = "Codex-Enhance-Manager-Proxy/1.0"
    headers["User-Agent"] = ua

    # 自定义 headers（排除 Authorization，避免重复）
    custom = provider.get("headers", {})
    if isinstance(custom, dict):
        for key, value in custom.items():
            if key.lower() != "authorization" and isinstance(value, str):
                headers[key] = value

    # Authorization
    api_key = provider.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def _upstream_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes] = None,
    timeout: int = DEFAULT_UPSTREAM_TIMEOUT,
    stream: bool = False,
) -> urllib.request.addinfourl:
    """
    执行上游 HTTP 请求。

    工程权衡：
      - 使用 urllib 而非 requests：避免第三方依赖，标准库足够。
      - 禁用系统代理：防止 Windows IE 代理设置干扰本地到上游直连。
      - stream=True 时不读取完整响应体：返回 addinfourl 对象，供调用方
        逐 chunk 读取。

    Args:
        method: HTTP 方法（POST、GET 等）。
        url: 上游 URL。
        headers: 请求头字典。
        body: 请求体字节串。
        timeout: 超时秒数。
        stream: 是否为流式请求（SSE）。

    Returns:
        urllib 响应对象。

    Raises:
        urllib.error.HTTPError: 上游返回 HTTP 错误状态码。
        urllib.error.URLError: 连接失败等网络错误。
        socket.timeout: 请求超时。
    """
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if stream:
        req.add_header("Accept", "text/event-stream")

    # 禁用系统代理，确保直连上游
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    return opener.open(req, timeout=timeout)


def _send_error(self: BaseHTTPRequestHandler, status: int, message: str, error_type: str = "proxy_error") -> None:
    """发送统一的 JSON 错误响应。"""
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.end_headers()
    payload = json.dumps({"error": {"message": message, "type": error_type}}, ensure_ascii=False)
    self.wfile.write(payload.encode("utf-8"))


class ProxyHandler(BaseHTTPRequestHandler):
    """
    本地代理请求处理器。

    设计意图：
      - 只处理已知的 OpenAI 兼容路径（/v1/responses、/v1/chat/completions、
        /v1/models），其余返回 404。
      - Content-Length 校验：拒绝负数或超 10MB 的请求体，防止畸形请求。
      - 日志静默：log_message 为空 pass，避免默认日志刷屏；结构化日志待后续接入。
      - 超时处理：上游请求有 120 秒默认超时，防止无限挂起。
    """

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if is_models_proxy_path(path):
            self._handle_models()
            return
        _send_error(self, 404, "Not found", "not_found")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = -1
        if content_length < 0 or content_length > MAX_BODY_SIZE:
            _send_error(
                self,
                400 if content_length < 0 else 413,
                "Invalid or too large Content-Length",
                "invalid_request_error",
            )
            return
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if is_responses_proxy_path(path):
            self._handle_responses(body, compact="/compact" in path)
            return
        if is_chat_completions_proxy_path(path):
            self._handle_chat_completions(body)
            return

        _send_error(self, 404, "Not found", "not_found")

    def _handle_models(self) -> None:
        """
        返回当前可用的模型列表。

        设计意图：
          - 从 providers.json 读取所有 enabled provider 的 enabled models。
          - 模型 ID 格式为 "{short_alias}/{model_id}"，避免同名模型冲突。
          - 这是 Codex CLI 选择模型时的来源之一。
        """
        providers = _load_providers_with_secrets()
        data: List[Dict[str, Any]] = []
        for p in providers:
            if not p.get("enabled", True):
                continue
            alias = p.get("short_alias", p.get("id", "unknown"))
            for m in p.get("models", []):
                if not isinstance(m, dict):
                    continue
                if not m.get("enabled", True):
                    continue
                model_id = m.get("id", "")
                data.append({
                    "id": f"{alias}/{model_id}",
                    "object": "model",
                    "owned_by": p.get("display_name", alias),
                })

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"object": "list", "data": data}, ensure_ascii=False).encode("utf-8"))

    def _handle_chat_completions(self, body: bytes) -> None:
        """
        处理 /v1/chat/completions 请求。

        路由逻辑：
          1. 解析请求 JSON，提取 model 字段。
          2. 使用 _resolve_provider_for_model 找到匹配的 provider。
          3. 若无匹配，返回 404 provider_not_found 错误。
          4. 构建上游 URL（provider base_url + /chat/completions）。
          5. 判断 stream 模式：请求体中 "stream": true 时走 SSE 流式转发。
          6. 非流式：等待完整响应后转发 JSON。
          7. 流式：逐 chunk 读取上游 SSE，逐 chunk 转发给客户端。

        错误处理：
          - JSON 解析失败 → 400
          - 找不到 provider → 404
          - 上游连接失败 → 502
          - 上游 HTTP 错误 → 透传状态码和错误体
        """
        try:
            request_json = json.loads(body.decode("utf-8", errors="replace")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            _send_error(self, 400, "Invalid JSON", "invalid_request_error")
            return

        model_id = request_json.get("model", "")
        provider = _resolve_provider_for_model(model_id)
        if not provider:
            _send_error(
                self,
                404,
                f"No enabled provider found for model '{model_id}'. "
                "Use 'provider/model' format or configure a provider that includes this model.",
                "provider_not_found",
            )
            return

        base_url = provider.get("base_url", "").rstrip("/")
        if not base_url:
            _send_error(self, 502, f"Provider '{provider.get('id')}' has no base_url configured.", "provider_misconfigured")
            return

        # 替换 model ID 为上游可用的格式
        upstream_model = _extract_model_id_for_upstream(request_json, provider)
        request_json["model"] = upstream_model
        upstream_body = json.dumps(request_json, ensure_ascii=False).encode("utf-8")

        upstream_url = f"{base_url}/chat/completions"
        headers = _build_upstream_headers(provider)
        is_stream = request_json.get("stream", False)

        try:
            upstream_resp = _upstream_request(
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            # 透传上游 HTTP 错误
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _send_error(self, 502, f"Upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_stream(upstream_resp)
        else:
            self._forward_non_streaming(upstream_resp)

    def _handle_responses(self, body: bytes, compact: bool = False) -> None:
        """
        处理 /v1/responses 请求。

        路由逻辑：
          1. 解析请求 JSON。
          2. 使用 responses_to_chat_completions 转换为 Chat Completions 格式。
          3. 从原始请求中提取 model（ Responses 请求中的 model 字段），
             或从转换后的 chat_request 中提取。
          4. 路由到匹配的 provider。
          5. 非流式：转换响应后返回 Responses 格式。
          6. 流式：转换请求 → 流式 Chat Completions → 流式 Responses SSE。
             流式 Responses 转换使用 ChatSseToResponsesConverter。

        工程权衡：
          - Responses 流式转换较复杂，当前版本先支持非流式完整路径。
          - 流式 Responses 在后续迭代中完善。
          - compact 标志目前仅透传，不影响路由逻辑。
        """
        try:
            request_json = json.loads(body.decode("utf-8", errors="replace")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            _send_error(self, 400, "Invalid JSON", "invalid_request_error")
            return

        # 转换 Responses -> Chat Completions
        try:
            chat_request = responses_to_chat_completions(request_json)
        except Exception as e:
            _send_error(self, 400, f"Request conversion failed: {e}", "invalid_request_error")
            return

        model_id = chat_request.get("model", "") or request_json.get("model", "")
        provider = _resolve_provider_for_model(model_id)
        if not provider:
            _send_error(
                self,
                404,
                f"No enabled provider found for model '{model_id}' (from Responses request). "
                "Use 'provider/model' format or configure a provider that includes this model.",
                "provider_not_found",
            )
            return

        base_url = provider.get("base_url", "").rstrip("/")
        if not base_url:
            _send_error(self, 502, f"Provider '{provider.get('id')}' has no base_url configured.", "provider_misconfigured")
            return

        # 替换 model ID
        upstream_model = _extract_model_id_for_upstream(chat_request, provider)
        chat_request["model"] = upstream_model
        upstream_body = json.dumps(chat_request, ensure_ascii=False).encode("utf-8")

        upstream_url = f"{base_url}/chat/completions"
        headers = _build_upstream_headers(provider)
        is_stream = chat_request.get("stream", False)

        try:
            upstream_resp = _upstream_request(
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _send_error(self, 502, f"Upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_responses_stream(upstream_resp, request_json)
        else:
            self._forward_responses_non_streaming(upstream_resp, request_json)

    def _forward_non_streaming(self, upstream_resp: urllib.request.addinfourl) -> None:
        """
        转发非流式上游响应。

        设计意图：
          - 读取完整响应体并直接转发，不做 JSON 解析/修改。
          - 保留上游的 Content-Type 头。
        """
        resp_body = upstream_resp.read()
        self.send_response(upstream_resp.getcode() or 200)
        ct = upstream_resp.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ct)
        self.end_headers()
        self.wfile.write(resp_body)

    def _forward_stream(self, upstream_resp: urllib.request.addinfourl) -> None:
        """
        流式转发上游 SSE 响应（Chat Completions 格式）。

        设计意图：
          - 逐 chunk 读取上游响应，不缓存完整内容。
          - 保持 SSE 格式：每行以 "data:" 开头，以两个换行符结束。
          - 使用 chunked transfer encoding 避免需要预先知道 Content-Length。
          - 客户端断开连接时（ConnectionResetError、BrokenPipeError）
            静默退出，不抛出未处理异常。

        Windows 平台特殊性：
          - 某些 Windows 网络栈在连接断开时会抛出不同异常，需要广泛捕获。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            while True:
                chunk = upstream_resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                # 每写完一个 chunk 尝试 flush，减少客户端感知延迟
                try:
                    self.wfile.flush()
                except Exception:
                    break
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                upstream_resp.close()
            except Exception:
                pass

    def _forward_responses_non_streaming(
        self,
        upstream_resp: urllib.request.addinfourl,
        original_request: Dict[str, Any],
    ) -> None:
        """
        非流式 Responses：将 Chat Completions 响应转换回 Responses 格式。

        设计意图：
          - 读取上游 Chat Completions JSON 响应。
          - 使用 chat_completion_to_response 转换为 Responses 形状。
          - 返回转换后的 JSON 给客户端（Codex CLI）。
        """
        try:
            chat_resp_body = upstream_resp.read()
            chat_resp_json = json.loads(chat_resp_body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _send_error(self, 502, f"Upstream returned invalid JSON: {e}", "upstream_error")
            return

        try:
            response_json = chat_completion_to_response(chat_resp_json, original_request)
        except Exception as e:
            _send_error(self, 502, f"Response conversion failed: {e}", "proxy_error")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(response_json, ensure_ascii=False).encode("utf-8"))

    def _forward_responses_stream(
        self,
        upstream_resp: urllib.request.addinfourl,
        original_request: Dict[str, Any],
    ) -> None:
        """
        流式 Responses：将 Chat Completions SSE 转换为 Responses SSE。

        设计意图：
          - 使用 ChatSseToResponsesConverter 逐行转换上游 SSE 事件。
          - 转换后的 Responses SSE 事件直接写入客户端。
          - 保证 terminal event（response.completed 或 response.failed）。

        工程权衡：
          - 逐行读取而非逐 chunk：SSE 事件以双换行符分隔，按行处理更可靠。
          - 缓冲区大小限制：ChatSseToResponsesConverter 内部有 1MB 缓冲区上限，
            防止异常大的上游响应导致内存耗尽。

        边界条件：
          - 上游连接异常中断：触发 response.failed 并结束流。
          - 客户端断开：静默处理，不抛出未处理异常。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        converter = ChatSseToResponsesConverter(original_request)

        try:
            buffer = b""
            while True:
                chunk = upstream_resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                # 处理完整行（以 \n\n 分隔的 SSE 事件）
                while b"\n\n" in buffer:
                    event_bytes, buffer = buffer.split(b"\n\n", 1)
                    event_text = converter.push_bytes(event_bytes + b"\n\n")
                    if event_text:
                        try:
                            self.wfile.write(event_text.encode("utf-8"))
                            self.wfile.flush()
                        except (ConnectionResetError, BrokenPipeError, OSError):
                            return

            # 处理剩余数据
            if buffer:
                event_text = converter.push_bytes(buffer)
                if event_text:
                    try:
                        self.wfile.write(event_text.encode("utf-8"))
                        self.wfile.flush()
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        return

            # 发送 terminal event
            terminal = converter.finalize()
            if terminal:
                try:
                    self.wfile.write(terminal.encode("utf-8"))
                    self.wfile.flush()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
        except Exception:
            # 任何未预料异常都发送 response.failed 并结束
            try:
                failed_event = converter.finalize() or (
                    "event: response.failed\n"
                    'data: {"error":{"type":"proxy_error","message":"Stream processing error"}}\n\n'
                )
                self.wfile.write(failed_event.encode("utf-8"))
            except Exception:
                pass
        finally:
            try:
                upstream_resp.close()
            except Exception:
                pass


class LocalProxyServer:
    """
    本地 HTTP 代理服务器的管理器。

    设计意图：
      - 封装 HTTPServer 和 Thread 的生命周期：start、stop、is_running、status。
      - start() 幂等：若服务器已在运行，直接返回 True，不重复创建端口监听。
      - stop() 优雅关闭：先 shutdown() 停止接受新连接，再 join(timeout=5)
        等待处理线程结束，防止端口残留（Windows 上 TIME_WAIT 可能导致快速
        重启时端口不可用）。
      - 支持设置 provider_store_path：代理启动时传入 registry 路径，
        使 ProxyHandler 能读取正确的 provider 配置。

    Windows 平台特殊性：
      - OSError 捕获：若端口已被占用（如上次崩溃未释放），start() 返回 False
        而非抛出未处理异常，便于上层提示用户。
    """

    def __init__(self, port: int = DEFAULT_PROXY_PORT, provider_store_path: str = ""):
        self.port = port
        self.provider_store_path = provider_store_path
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if self._server is not None:
            return True
        # 预检端口是否已被占用（Windows 上 SO_REUSEADDR 默认允许重复绑定）
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1)
            test_sock.bind(("127.0.0.1", self.port))
            test_sock.close()
        except OSError:
            return False
        # 设置全局 provider store 路径，供 ProxyHandler 使用
        if self.provider_store_path:
            _set_provider_store_path(self.provider_store_path)
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), ProxyHandler)
        except OSError:
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._server is not None

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running(),
            "port": self.port,
            "base_url": f"http://127.0.0.1:{self.port}/v1" if self.is_running() else "",
            "provider_store_path": str(_get_provider_store_path()),
        }
