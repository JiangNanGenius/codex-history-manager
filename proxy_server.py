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
from typing import Any, Callable, Dict, List, Optional

from anthropic_adapter import (
    AnthropicConversionError,
    AnthropicSseToResponsesConverter,
    anthropic_message_to_response,
    anthropic_messages_url,
    responses_to_anthropic_messages,
)
from domestic_responses import (
    assess_domestic_responses_request,
    format_domestic_unsupported_reason,
)
from media_proxy import (
    canonical_media_path,
    evaluate_media_approval,
    extract_json_model,
    is_media_proxy_path,
    media_endpoint_url,
    media_forwarding_status,
    media_kind_for_path,
    media_operation_for_request,
    prepare_media_body,
    resolve_media_route,
)
from responses_adapter import (
    ChatSseToResponsesConverter,
    chat_completion_to_response,
    is_chat_completions_proxy_path,
    is_models_proxy_path,
    is_responses_proxy_path,
    responses_error_from_upstream,
    responses_to_chat_completions,
    responses_url,
)
from request_logs import RequestLogStore, build_proxy_log_entry, extract_usage_from_response, normalize_usage

DEFAULT_PROXY_PORT = 8080
PORT_BACKOFF_SCAN_LIMIT = 50
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
DEFAULT_UPSTREAM_TIMEOUT = 120  # 秒


class LocalProxyHTTPServer(HTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


# 全局配置（由 LocalProxyServer 在启动时设置）
_provider_store_path: Optional[Path] = None
_request_log_path: Optional[Path] = None
_request_log_retention_days = 30
_request_log_max_mb = 50.0
_request_log_currency_settings: Dict[str, Any] = {}
_media_approval_reviewer: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]] = None
_upstream_timeout_seconds = DEFAULT_UPSTREAM_TIMEOUT
_upstream_retry_attempts = 0
_upstream_retry_backoff_ms = 250


def _set_provider_store_path(path: str) -> None:
    """设置 provider registry 存储路径；供 LocalProxyServer 启动时调用。"""
    global _provider_store_path
    _provider_store_path = Path(path).expanduser() if path else None


def _set_request_log_config(
    path: str = "",
    retention_days: int = 30,
    max_mb: float = 50,
    currency_settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Configure metadata-only request logging for the proxy thread."""
    global _request_log_path, _request_log_retention_days, _request_log_max_mb, _request_log_currency_settings
    _request_log_path = Path(path).expanduser() if path else None
    try:
        _request_log_retention_days = max(int(retention_days), 1)
    except (TypeError, ValueError):
        _request_log_retention_days = 30
    try:
        _request_log_max_mb = max(float(max_mb), 1.0)
    except (TypeError, ValueError):
        _request_log_max_mb = 50.0
    _request_log_currency_settings = dict(currency_settings or {})


def _set_media_approval_reviewer(
    reviewer: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]]
) -> None:
    """Install an injectable media Auto Approval reviewer for tests/future runtime wiring."""
    global _media_approval_reviewer
    _media_approval_reviewer = reviewer


def _set_upstream_policy(
    timeout_seconds: Any = DEFAULT_UPSTREAM_TIMEOUT,
    retry_attempts: Any = 0,
    retry_backoff_ms: Any = 250,
) -> None:
    """Configure global upstream timeout/retry policy for proxy requests."""
    global _upstream_timeout_seconds, _upstream_retry_attempts, _upstream_retry_backoff_ms
    _upstream_timeout_seconds = _positive_int(timeout_seconds, DEFAULT_UPSTREAM_TIMEOUT, minimum=1, maximum=3600)
    _upstream_retry_attempts = _positive_int(retry_attempts, 0, minimum=0, maximum=5)
    _upstream_retry_backoff_ms = _positive_int(retry_backoff_ms, 250, minimum=0, maximum=30000)


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
    is_anthropic = provider.get("api_format") == "anthropic"

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
            if key.lower() not in ("authorization", "x-api-key") and isinstance(value, str):
                headers[key] = value

    # Authorization
    api_key = provider.get("api_key", "")
    if api_key:
        if is_anthropic:
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    if is_anthropic and not _has_header(headers, "anthropic-version"):
        headers["anthropic-version"] = str(provider.get("anthropic_version") or "2023-06-01")

    return headers


def _has_header(headers: Dict[str, str], name: str) -> bool:
    lower_name = name.lower()
    return any(key.lower() == lower_name for key in headers)


def _provider_api_format(provider: Dict[str, Any]) -> str:
    api_format = provider.get("api_format")
    if api_format in {"openai_responses", "openai_chat", "anthropic", "custom"}:
        return str(api_format)
    # Legacy provider stores created before api_format existed were Chat-based.
    return "openai_chat"


def _native_responses_unsupported_reason(
    provider: Dict[str, Any],
    request_json: Dict[str, Any],
    compact: bool = False,
) -> Optional[str]:
    """Return a clear reason when a partial domestic Responses route is unsafe."""
    report = assess_domestic_responses_request(provider, request_json, compact=compact)
    if not report.get("domestic_responses") or report.get("safe_to_forward"):
        return None
    return format_domestic_unsupported_reason(report)


def _provider_proxy_profile(provider: Dict[str, Any]) -> Dict[str, Any]:
    profile = provider.get("proxy_profile")
    if isinstance(profile, dict):
        return profile
    profile = provider.get("proxy")
    return profile if isinstance(profile, dict) else {}


def _provider_upstream_timeout(provider: Dict[str, Any]) -> int:
    profile = _provider_proxy_profile(provider)
    value = (
        provider.get("upstream_timeout_seconds")
        or provider.get("timeout_seconds")
        or profile.get("upstream_timeout_seconds")
        or profile.get("timeout_seconds")
        or _upstream_timeout_seconds
    )
    return _positive_int(value, _upstream_timeout_seconds, minimum=1, maximum=3600)


def _provider_retry_policy(provider: Dict[str, Any]) -> Dict[str, int]:
    profile = _provider_proxy_profile(provider)
    attempts = (
        provider.get("retry_attempts")
        or profile.get("retry_attempts")
        or profile.get("max_retries")
        or _upstream_retry_attempts
    )
    backoff_ms = (
        provider.get("retry_backoff_ms")
        or profile.get("retry_backoff_ms")
        or _upstream_retry_backoff_ms
    )
    return {
        "retry_attempts": _positive_int(attempts, _upstream_retry_attempts, minimum=0, maximum=5),
        "retry_backoff_ms": _positive_int(backoff_ms, _upstream_retry_backoff_ms, minimum=0, maximum=30000),
    }


def _provider_bypass_system_proxy(provider: Dict[str, Any]) -> bool:
    profile = _provider_proxy_profile(provider)
    value = provider.get("bypass_system_proxy")
    if value is None:
        value = profile.get("bypass_system_proxy", profile.get("proxy_bypass", True))
    return _coerce_bool(value, True)


def _upstream_request_for_provider(
    provider: Dict[str, Any],
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes] = None,
    stream: bool = False,
) -> urllib.request.addinfourl:
    retry_policy = _provider_retry_policy(provider)
    return _upstream_request(
        method,
        url,
        headers,
        body=body,
        timeout=_provider_upstream_timeout(provider),
        stream=stream,
        retry_attempts=retry_policy["retry_attempts"],
        retry_backoff_ms=retry_policy["retry_backoff_ms"],
        bypass_system_proxy=_provider_bypass_system_proxy(provider),
    )


def _upstream_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes] = None,
    timeout: Optional[int] = None,
    stream: bool = False,
    retry_attempts: Optional[int] = None,
    retry_backoff_ms: Optional[int] = None,
    bypass_system_proxy: bool = True,
) -> urllib.request.addinfourl:
    """
    执行上游 HTTP 请求。

    工程权衡：
      - 使用 urllib 而非 requests：避免第三方依赖，标准库足够。
      - 默认禁用系统代理：防止 Windows IE 代理设置干扰本地到上游直连。
        Provider 可显式关闭 bypass，以使用系统代理。
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
    timeout_value = _positive_int(timeout, _upstream_timeout_seconds, minimum=1, maximum=3600)
    retries = _positive_int(retry_attempts, _upstream_retry_attempts, minimum=0, maximum=5)
    backoff_ms = _positive_int(retry_backoff_ms, _upstream_retry_backoff_ms, minimum=0, maximum=30000)
    last_error: Optional[BaseException] = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        if stream:
            req.add_header("Accept", "text/event-stream")

        # 默认禁用系统代理，确保直连上游；provider 可选择使用系统代理。
        if _coerce_bool(bypass_system_proxy, True):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        try:
            return opener.open(req, timeout=timeout_value)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if not _should_retry_http_error(exc) or attempt >= retries:
                raise
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        if backoff_ms > 0:
            time.sleep((backoff_ms / 1000.0) * (attempt + 1))

    if last_error:
        raise last_error
    raise urllib.error.URLError("upstream request failed without a response")


def _should_retry_http_error(exc: urllib.error.HTTPError) -> bool:
    return int(getattr(exc, "code", 0) or 0) in {408, 409, 425, 429, 500, 502, 503, 504}


def _make_request_log_context(
    endpoint: str,
    method: str,
    provider: Dict[str, Any],
    model: str = "",
    upstream_model: str = "",
    stream: bool = False,
    media_kind: str = "",
    usage_hint: Optional[Dict[str, Any]] = None,
    route_explanation: str = "",
) -> Dict[str, Any]:
    return {
        "started_at": time.time(),
        "endpoint": endpoint,
        "method": method,
        "provider": provider,
        "provider_id": provider.get("id") if isinstance(provider, dict) else "",
        "provider_alias": provider.get("short_alias") if isinstance(provider, dict) else "",
        "api_format": _provider_api_format(provider) if isinstance(provider, dict) else "",
        "model": model or upstream_model,
        "upstream_model": upstream_model or model,
        "stream": bool(stream),
        "media_kind": media_kind,
        "usage_hint": usage_hint or {},
        "route_explanation": route_explanation,
    }


def _record_request_log(
    context: Optional[Dict[str, Any]],
    status_code: int,
    response_json: Any = None,
    usage: Optional[Dict[str, Any]] = None,
    error_type: str = "",
    error_message: str = "",
) -> None:
    if not context or _request_log_path is None:
        return
    try:
        duration_ms = (time.time() - float(context.get("started_at") or time.time())) * 1000
        response_usage = usage or extract_usage_from_response(response_json) or context.get("usage_hint") or {}
        entry = build_proxy_log_entry(
            context,
            response_json=response_json,
            usage=normalize_usage(response_usage),
            status_code=status_code,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=error_message,
            currency_settings=_request_log_currency_settings,
        )
        RequestLogStore(
            _request_log_path,
            retention_days=_request_log_retention_days,
            max_mb=_request_log_max_mb,
        ).append(entry)
    except Exception:
        pass


def _media_usage_hint(body: bytes, content_type: str, media_kind: str) -> Dict[str, Any]:
    if "json" not in str(content_type or "").lower() or not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if media_kind == "image":
        return {"image_count": _safe_positive_int(payload.get("n"), default=1)}
    if media_kind == "video":
        hint = {"video_job_count": 1}
        duration = payload.get("duration") or payload.get("video_seconds")
        try:
            hint["video_seconds"] = max(int(float(duration or 0)), 0)
        except (TypeError, ValueError):
            pass
        return hint
    return {}


def _safe_positive_int(value: Any, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _positive_int(value: Any, default: int, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(max(result, minimum), maximum)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


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
        if is_media_proxy_path(path):
            self._handle_media(body=b"", method="GET")
            return
        _send_error(self, 404, "Not found", "not_found")

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if is_media_proxy_path(path):
            self._handle_media(body=b"", method="DELETE")
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
        if is_media_proxy_path(path):
            self._handle_media(body, method="POST")
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
        if provider.get("api_format") == "anthropic":
            _send_error(
                self,
                400,
                "Anthropic providers are supported through /v1/responses; /v1/chat/completions requires a separate Chat response adapter.",
                "unsupported_api_format",
            )
            return

        upstream_model = _extract_model_id_for_upstream(request_json, provider)
        request_json["model"] = upstream_model
        upstream_body = json.dumps(request_json, ensure_ascii=False).encode("utf-8")

        upstream_url = f"{base_url}/chat/completions"
        headers = _build_upstream_headers(provider)
        is_stream = request_json.get("stream", False)
        log_context = _make_request_log_context(
            "chat_completions",
            "POST",
            provider,
            model=model_id,
            upstream_model=upstream_model,
            stream=bool(is_stream),
            route_explanation="provider/model route to Chat Completions upstream",
        )

        try:
            upstream_resp = _upstream_request_for_provider(
                provider,
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            # 透传上游 HTTP 错误
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            _record_request_log(log_context, e.code, error_type="upstream_http_error", error_message=error_body)
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=str(e))
            _send_error(self, 502, f"Upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_stream(upstream_resp)
        else:
            self._forward_non_streaming(upstream_resp, log_context=log_context)

    def _handle_media(self, body: bytes, method: str = "POST") -> None:
        """Forward OpenAI-compatible image/video requests to the media provider."""
        path = self.path.split("?", 1)[0]
        media_kind = media_kind_for_path(path)
        canonical_path = canonical_media_path(path)
        media_operation = media_operation_for_request(method, canonical_path)
        content_type = self.headers.get("Content-Type", "")
        model_id = extract_json_model(body, content_type)
        route = resolve_media_route(_load_providers_with_secrets(), media_kind, model_id=model_id)
        provider = route.get("provider")
        if not provider:
            _send_error(
                self,
                404,
                f"No enabled {media_kind or 'media'} provider found. Configure a default media provider or use provider/model.",
                "media_provider_not_found",
            )
            return

        status = media_forwarding_status(provider, media_kind)
        if not status.get("can_forward"):
            _send_error(self, 400, str(status.get("message") or "Media provider cannot be forwarded"), str(status.get("error_type") or "media_unsupported"))
            return

        media_endpoint = canonical_path.strip("/")
        if media_endpoint.startswith("v1/"):
            media_endpoint = media_endpoint[3:]
        route_explanation = "; ".join(route.get("route_explanation") or ["media provider route"])
        log_context = _make_request_log_context(
            media_endpoint or "media",
            method,
            provider,
            model=model_id,
            upstream_model=str(route.get("upstream_model_id") or model_id or ""),
            stream=False,
            media_kind=str(media_kind or ""),
            usage_hint=_media_usage_hint(body, content_type, str(media_kind or "")),
            route_explanation=route_explanation,
        )
        approval = evaluate_media_approval(
            provider,
            str(media_kind or ""),
            media_operation,
            canonical_path,
            model_id=model_id,
            upstream_model_id=str(route.get("upstream_model_id") or model_id or ""),
            route_explanation=route.get("route_explanation") or [],
            reviewer=_media_approval_reviewer,
        )
        if not approval.get("approved"):
            message = str(approval.get("message") or "Auto Approval did not approve this media request.")
            error_type = str(approval.get("error_type") or "media_auto_approval_declined")
            _record_request_log(log_context, 403, error_type=error_type, error_message=message)
            _send_error(self, 403, message, error_type)
            return

        base_url = provider.get("base_url", "").rstrip("/")
        if not base_url:
            _send_error(self, 502, f"Provider '{provider.get('id')}' has no base_url configured.", "provider_misconfigured")
            return

        upstream_body = prepare_media_body(
            body,
            content_type,
            provider,
            upstream_model_id=str(route.get("upstream_model_id") or ""),
        ) if body else None
        upstream_url = media_endpoint_url(base_url, canonical_path)
        headers = _build_upstream_headers(provider)
        if content_type:
            headers["Content-Type"] = content_type

        try:
            upstream_resp = _upstream_request_for_provider(
                provider,
                method,
                upstream_url,
                headers,
                body=upstream_body,
                stream=False,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            _record_request_log(log_context, e.code, error_type="upstream_http_error", error_message=error_body)
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=str(e))
            _send_error(self, 502, f"Media upstream connection failed: {e}", "upstream_error")
            return

        self._forward_non_streaming(upstream_resp, log_context=log_context)

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

        model_id = request_json.get("model", "")
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

        api_format = _provider_api_format(provider)
        if api_format == "anthropic":
            self._handle_responses_anthropic(request_json, provider, base_url)
            return
        if api_format == "openai_responses":
            self._handle_responses_native(request_json, provider, base_url, compact=compact)
            return

        # 转换 Responses -> Chat Completions
        try:
            chat_request = responses_to_chat_completions(request_json)
        except Exception as e:
            _send_error(self, 400, f"Request conversion failed: {e}", "invalid_request_error")
            return

        upstream_model = _extract_model_id_for_upstream(chat_request, provider)
        chat_request["model"] = upstream_model
        upstream_body = json.dumps(chat_request, ensure_ascii=False).encode("utf-8")

        upstream_url = f"{base_url}/chat/completions"
        headers = _build_upstream_headers(provider)
        is_stream = chat_request.get("stream", False)
        log_context = _make_request_log_context(
            "responses",
            "POST",
            provider,
            model=model_id,
            upstream_model=upstream_model,
            stream=bool(is_stream),
            route_explanation="Responses request converted to Chat Completions upstream",
        )

        try:
            upstream_resp = _upstream_request_for_provider(
                provider,
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            _record_request_log(log_context, e.code, error_type="upstream_http_error", error_message=error_body)
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=str(e))
            _send_error(self, 502, f"Upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_responses_stream(upstream_resp, request_json)
        else:
            self._forward_responses_non_streaming(upstream_resp, request_json, log_context=log_context)

    def _handle_responses_native(
        self,
        request_json: Dict[str, Any],
        provider: Dict[str, Any],
        base_url: str,
        compact: bool = False,
    ) -> None:
        """Forward a Responses request to an upstream that natively speaks Responses."""
        unsupported_reason = _native_responses_unsupported_reason(provider, request_json, compact=compact)
        if unsupported_reason:
            _send_error(self, 400, unsupported_reason, "domestic_responses_unsupported")
            return

        upstream_request = dict(request_json)
        upstream_model = _extract_model_id_for_upstream(request_json, provider)
        upstream_request["model"] = upstream_model
        upstream_body = json.dumps(upstream_request, ensure_ascii=False).encode("utf-8")

        upstream_url = responses_url(base_url)
        headers = _build_upstream_headers(provider)
        is_stream = bool(upstream_request.get("stream"))
        log_context = _make_request_log_context(
            "responses",
            "POST",
            provider,
            model=str(request_json.get("model") or ""),
            upstream_model=upstream_model,
            stream=is_stream,
            route_explanation="native Responses upstream",
        )

        try:
            upstream_resp = _upstream_request_for_provider(
                provider,
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            _record_request_log(log_context, e.code, error_type="upstream_http_error", error_message=error_body)
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=str(e))
            _send_error(self, 502, f"Responses upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_stream(upstream_resp)
        else:
            self._forward_non_streaming(upstream_resp, log_context=log_context)

    def _handle_responses_anthropic(
        self,
        request_json: Dict[str, Any],
        provider: Dict[str, Any],
        base_url: str,
    ) -> None:
        """Forward a Responses request to an Anthropic Messages upstream."""
        upstream_model = _extract_model_id_for_upstream(request_json, provider)
        try:
            anthropic_request = responses_to_anthropic_messages(request_json, upstream_model=upstream_model)
        except AnthropicConversionError as e:
            _send_error(self, 400, f"Anthropic request conversion failed: {e}", "invalid_request_error")
            return

        upstream_body = json.dumps(anthropic_request, ensure_ascii=False).encode("utf-8")
        upstream_url = anthropic_messages_url(base_url)
        headers = _build_upstream_headers(provider)
        is_stream = bool(anthropic_request.get("stream"))
        log_context = _make_request_log_context(
            "responses",
            "POST",
            provider,
            model=str(request_json.get("model") or ""),
            upstream_model=upstream_model,
            stream=is_stream,
            route_explanation="Responses request converted to Anthropic Messages upstream",
        )

        try:
            upstream_resp = _upstream_request_for_provider(
                provider,
                "POST",
                upstream_url,
                headers,
                body=upstream_body,
                stream=is_stream,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else "{}"
            _record_request_log(log_context, e.code, error_type="upstream_http_error", error_message=error_body)
            self.send_response(e.code)
            for header_key, header_value in e.headers.items():
                if header_key.lower() in ("content-type", "content-length"):
                    self.send_header(header_key, header_value)
            self.end_headers()
            self.wfile.write(error_body.encode("utf-8"))
            return
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=str(e))
            _send_error(self, 502, f"Anthropic upstream connection failed: {e}", "upstream_error")
            return

        if is_stream:
            self._forward_anthropic_responses_stream(upstream_resp, request_json)
        else:
            self._forward_anthropic_responses_non_streaming(upstream_resp, request_json, log_context=log_context)

    def _forward_non_streaming(
        self,
        upstream_resp: urllib.request.addinfourl,
        log_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        转发非流式上游响应。

        设计意图：
          - 读取完整响应体并直接转发，不做 JSON 解析/修改。
          - 保留上游的 Content-Type 头。
        """
        resp_body = upstream_resp.read()
        status_code = upstream_resp.getcode() or 200
        response_json = None
        try:
            response_json = json.loads(resp_body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            response_json = None
        _record_request_log(log_context, status_code, response_json=response_json)
        self.send_response(status_code)
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

    def _forward_anthropic_responses_non_streaming(
        self,
        upstream_resp: urllib.request.addinfourl,
        original_request: Dict[str, Any],
        log_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Convert a non-streaming Anthropic Messages response to Responses JSON."""
        try:
            resp_body = upstream_resp.read()
            anthropic_json = json.loads(resp_body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=f"Invalid JSON: {e}")
            _send_error(self, 502, f"Anthropic upstream returned invalid JSON: {e}", "upstream_error")
            return

        try:
            response_json = anthropic_message_to_response(anthropic_json, original_request)
        except Exception as e:
            _record_request_log(log_context, 502, error_type="proxy_error", error_message=f"Anthropic conversion failed: {e}")
            _send_error(self, 502, f"Anthropic response conversion failed: {e}", "proxy_error")
            return

        _record_request_log(log_context, 200, response_json=response_json)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(response_json, ensure_ascii=False).encode("utf-8"))

    def _forward_anthropic_responses_stream(
        self,
        upstream_resp: urllib.request.addinfourl,
        original_request: Dict[str, Any],
    ) -> None:
        """Convert Anthropic Messages SSE into Responses SSE."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        converter = AnthropicSseToResponsesConverter(original_request)

        try:
            while True:
                chunk = upstream_resp.read(4096)
                if not chunk:
                    break
                event_text = converter.push_bytes(chunk)
                if event_text:
                    try:
                        self.wfile.write(event_text.encode("utf-8"))
                        self.wfile.flush()
                    except (ConnectionResetError, BrokenPipeError, OSError):
                        return

            terminal = converter.finish()
            if terminal:
                try:
                    self.wfile.write(terminal.encode("utf-8"))
                    self.wfile.flush()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
        except Exception:
            try:
                self.wfile.write(converter.fail("Anthropic stream processing error", "proxy_error").encode("utf-8"))
            except Exception:
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
        log_context: Optional[Dict[str, Any]] = None,
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
            _record_request_log(log_context, 502, error_type="upstream_error", error_message=f"Invalid JSON: {e}")
            _send_error(self, 502, f"Upstream returned invalid JSON: {e}", "upstream_error")
            return

        try:
            response_json = chat_completion_to_response(chat_resp_json, original_request)
        except Exception as e:
            _record_request_log(log_context, 502, error_type="proxy_error", error_message=f"Response conversion failed: {e}")
            _send_error(self, 502, f"Response conversion failed: {e}", "proxy_error")
            return

        _record_request_log(log_context, 200, response_json=response_json)
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
            terminal = converter.finish()
            if terminal:
                try:
                    self.wfile.write(terminal.encode("utf-8"))
                    self.wfile.flush()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass
        except Exception:
            # 任何未预料异常都发送 response.failed 并结束
            try:
                failed_event = converter.finish() or (
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
      - OSError 捕获：若端口已被占用（如上次崩溃未释放），start() 自动尝试
        后续端口，并在 status() 中暴露退避结果，便于上层提示用户。
    """

    def __init__(
        self,
        port: int = DEFAULT_PROXY_PORT,
        provider_store_path: str = "",
        request_log_path: str = "",
        request_log_retention_days: int = 30,
        request_log_max_mb: float = 50,
        currency_settings: Optional[Dict[str, Any]] = None,
        upstream_timeout_seconds: int = DEFAULT_UPSTREAM_TIMEOUT,
        retry_attempts: int = 0,
        retry_backoff_ms: int = 250,
        media_approval_reviewer: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]] = None,
    ):
        self.port = port
        self.provider_store_path = provider_store_path
        self.request_log_path = request_log_path
        self.request_log_retention_days = request_log_retention_days
        self.request_log_max_mb = request_log_max_mb
        self.currency_settings = dict(currency_settings or {})
        self.upstream_timeout_seconds = upstream_timeout_seconds
        self.retry_attempts = retry_attempts
        self.retry_backoff_ms = retry_backoff_ms
        self.media_approval_reviewer = media_approval_reviewer
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._last_requested_port = port
        self._last_start_error = ""
        self._last_port_backoff: Dict[str, Any] = {
            "used": False,
            "from": port,
            "to": port,
            "attempts": 0,
            "scan_limit": PORT_BACKOFF_SCAN_LIMIT,
            "host": "127.0.0.1",
            "range_end": port,
        }

    def start(self) -> bool:
        if self._server is not None:
            _set_media_approval_reviewer(self.media_approval_reviewer)
            self.port = int(self._server.server_address[1])
            return True
        # 设置全局 provider store 路径，供 ProxyHandler 使用
        if self.provider_store_path:
            _set_provider_store_path(self.provider_store_path)
        _set_request_log_config(
            self.request_log_path,
            retention_days=self.request_log_retention_days,
            max_mb=self.request_log_max_mb,
            currency_settings=self.currency_settings,
        )
        _set_upstream_policy(
            self.upstream_timeout_seconds,
            retry_attempts=self.retry_attempts,
            retry_backoff_ms=self.retry_backoff_ms,
        )
        _set_media_approval_reviewer(self.media_approval_reviewer)

        if not isinstance(self.port, int) or self.port < 1 or self.port > 65535:
            self._last_requested_port = self.port
            self._last_start_error = f"Invalid proxy port: {self.port}"
            self._last_port_backoff = {
                "used": False,
                "from": self.port,
                "to": self.port,
                "attempts": 0,
                "scan_limit": PORT_BACKOFF_SCAN_LIMIT,
                "host": "127.0.0.1",
                "range_end": self.port,
            }
            return False

        original_port = self.port
        self._last_requested_port = original_port
        self._last_start_error = ""
        attempts = 0
        range_end = min(original_port + PORT_BACKOFF_SCAN_LIMIT - 1, 65535)

        for candidate_port in range(original_port, range_end + 1):
            attempts += 1
            try:
                self._server = LocalProxyHTTPServer(("127.0.0.1", candidate_port), ProxyHandler)
                self.port = candidate_port
                self._last_port_backoff = {
                    "used": candidate_port != original_port,
                    "from": original_port,
                    "to": candidate_port,
                    "attempts": attempts,
                    "scan_limit": PORT_BACKOFF_SCAN_LIMIT,
                    "host": "127.0.0.1",
                    "range_end": range_end,
                }
                self._last_start_error = ""
                break
            except OSError as e:
                self._last_start_error = f"Port {candidate_port} unavailable: {e}"
                self._server = None
                continue
        if self._server is None:
            self.port = original_port
            self._last_port_backoff = {
                "used": False,
                "from": original_port,
                "to": original_port,
                "attempts": attempts,
                "scan_limit": PORT_BACKOFF_SCAN_LIMIT,
                "host": "127.0.0.1",
                "range_end": range_end,
            }
            if not self._last_start_error:
                self._last_start_error = f"No available proxy port in range {original_port}-{range_end}"
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            server = self._server
            self._server = None
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        _set_media_approval_reviewer(None)

    def is_running(self) -> bool:
        return self._server is not None

    def status(self) -> Dict[str, Any]:
        bound_port = int(self._server.server_address[1]) if self._server is not None else self.port
        effective_log_path = _request_log_path
        if effective_log_path is None and self.request_log_path:
            effective_log_path = Path(self.request_log_path).expanduser()
        effective_retention_days = _request_log_retention_days if _request_log_path is not None else self.request_log_retention_days
        effective_max_mb = _request_log_max_mb if _request_log_path is not None else self.request_log_max_mb
        return {
            "running": self.is_running(),
            "port": bound_port,
            "requested_port": self._last_requested_port,
            "base_url": f"http://127.0.0.1:{bound_port}/v1" if self.is_running() else "",
            "provider_store_path": str(_get_provider_store_path()),
            "request_log_enabled": effective_log_path is not None,
            "request_log_path": str(effective_log_path) if effective_log_path is not None else "",
            "request_log_retention_days": effective_retention_days,
            "request_log_max_mb": effective_max_mb,
            "upstream_timeout_seconds": _upstream_timeout_seconds,
            "retry_attempts": _upstream_retry_attempts,
            "retry_backoff_ms": _upstream_retry_backoff_ms,
            "media_auto_approval_reviewer_connected": _media_approval_reviewer is not None,
            "port_backoff": self._last_port_backoff,
            "last_start_error": self._last_start_error,
        }
