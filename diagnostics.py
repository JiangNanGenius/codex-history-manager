"""
diagnostics.py - 诊断信息收集与安全导出模块。

设计意图：
  - 提供统一的诊断信息收集入口，供前端 Diagnostics 页面展示。
  - 默认脱敏：所有诊断 API 返回的数据默认不包含 api_key、access_token 等敏感信息，
    防止用户截图或导出时意外泄露凭据。
  - 结构化输出：每个 section 为独立字典，便于前端按需渲染折叠面板。

工程权衡：
  - DiagnosticsCollector 接收外部依赖（config、provider_registry、proxy_server），
    而非自行构造，保证测试时可注入 mock 对象。
  - amr_registry 为可选依赖：AMR 可能由另一 agent 并行实现，若未就绪则 section 留空。
  - check_provider_connectivity 只做 HEAD/GET /models，不发送含 key 的真实请求：
    这是「最小权限连通性探测」，避免在诊断过程中产生计费或副作用。
  - 错误收集器为内存环型缓冲区：只保留最近 50 条，避免长期运行后内存膨胀。

边界条件：
  - config.toml 不存在时，codex_config section 标记 exists=False，其余字段为空。
  - auth.json 不存在时，auth_mode 返回 "none"。
  - provider_store 为空或损坏时，providers section 返回空列表而非报错。
  - 连通性测试超时或 SSL 错误时，返回明确错误类型而非抛异常。
  - 配置文件路径直接引用 config 模块常量，因 Config 类本身不暴露文件路径属性。
"""
from __future__ import annotations

import json
import platform
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from codex_config import CodexConfigManager, detect_auth_mode
from currency import exchange_rate_status_summary
from providers import ProviderRegistry, redact_secrets
from request_logs import RequestLogStore

# 引用 config 模块的配置文件路径常量（Config 类未暴露此属性）
from config import CONFIG_FILE


class DiagnosticsCollector:
    """
    诊断信息收集器。

    职责：
      - 从各子模块聚合运行状态、配置状态、网络连通性。
      - 提供脱敏视图与安全导出。
      - 维护最近错误日志环型缓冲区。
    """

    def __init__(
        self,
        config,
        provider_registry: ProviderRegistry,
        proxy_server,
        amr_registry=None,
        quota_manager=None,
    ):
        """
        初始化 DiagnosticsCollector。

        Args:
            config: Config 实例，用于读取应用设置。
            provider_registry: ProviderRegistry 实例，用于读取 provider 状态。
            proxy_server: LocalProxyServer 实例，用于读取代理状态。
            amr_registry: 可选的 AMR registry（如 AdaptiveModelRotation 实例）。
                          若另一 agent 尚未实现该模块，传入 None 即可。
            quota_manager: 可选 QuotaManager，用于读取已缓存的余额/额度快照；
                           不在诊断收集时触发网络请求。
        """
        self.config = config
        self.provider_registry = provider_registry
        self.proxy_server = proxy_server
        self.amr_registry = amr_registry
        self.quota_manager = quota_manager
        self._recent_errors: List[Dict[str, Any]] = []

    def record_error(self, source: str, message: str) -> None:
        """
        记录一条诊断错误。

        设计意图：
          - 各模块可在捕获异常后调用此方法，将错误汇入诊断视图，
            无需引入全局日志框架。
          - 保留最近 50 条，超出时丢弃最旧记录。

        Args:
            source: 错误来源模块名或端点名。
            message: 错误描述。
        """
        self._recent_errors.append({
            "source": source,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        if len(self._recent_errors) > 50:
            self._recent_errors = self._recent_errors[-50:]

    def collect_all(self) -> Dict[str, Any]:
        """
        收集完整诊断信息。

        Returns:
            包含 codex_config、auth_mode、local_proxy、providers、model_catalog、
            amr、system、errors 等 section 的字典。
            注意：本方法返回**未脱敏**的原始数据，仅供服务端内部使用；
            对外暴露请调用 collect_redacted()。
        """
        mgr = CodexConfigManager()
        config_data = mgr.read_config()
        auth_data = mgr.read_auth()
        auth_mode = detect_auth_mode(auth_data)
        permissions_audit = mgr.inspect_permissions()

        # ── a) Codex 配置状态 ──
        known_top_keys = {"model_provider", "model", "provider", "defaults"}
        custom_top_keys = set(config_data.keys()) - known_top_keys
        # 若存在嵌套 section（值为 dict），其内部键也算自定义键
        has_custom_keys = bool(custom_top_keys)
        for key, value in config_data.items():
            if isinstance(value, dict):
                nested_known = set()  # 当前不做嵌套 known 过滤，有嵌套即算自定义
                nested_custom = set(value.keys()) - nested_known
                if nested_custom:
                    has_custom_keys = True
                    break

        codex_config_section = {
            "exists": mgr.config_path.exists(),
            "path": str(mgr.config_path),
            "model_provider": config_data.get("model_provider", ""),
            "model": config_data.get("model", ""),
            "has_custom_keys": has_custom_keys,
            "custom_keys_count": len(custom_top_keys),
        }

        # ── b) 认证模式状态 ──
        auth_section = {
            "mode": auth_mode,
            "preserve_official_login": auth_mode == "official_oauth",
            "auth_path_exists": mgr.auth_path.exists(),
            "auth_path": str(mgr.auth_path),
        }

        # ── c) 本地代理状态 ──
        proxy_status = self.proxy_server.status()
        # 补充已配置 provider 数量（从代理视角读取 store 中的 secrets 版本）
        try:
            providers_raw = self.provider_registry.list_providers(include_secrets=True)
            configured_provider_count = len(providers_raw.get("providers", []))
        except Exception:
            configured_provider_count = 0

        proxy_status["configured_provider_count"] = configured_provider_count

        # ── d) Providers 摘要 ──
        providers_data = self.provider_registry.list_providers(include_secrets=True)
        providers_summary: List[Dict[str, Any]] = []
        for p in providers_data.get("providers", []):
            if not p.get("enabled", True):
                continue
            enabled_models = [m for m in p.get("models", []) if m.get("enabled", True)]
            providers_summary.append({
                "id": p.get("id"),
                "display_name": p.get("display_name"),
                "short_alias": p.get("short_alias"),
                "base_url": p.get("base_url"),
                "api_format": p.get("api_format"),
                "enabled_models_count": len(enabled_models),
                "country_region": p.get("country_region", ""),
                "native_currency": p.get("native_currency", ""),
                "api_key": p.get("api_key", ""),
                "headers": p.get("headers", {}),
            })

        # ── e) Model Catalog 摘要 ──
        catalog = self.provider_registry.preview_catalog(
            focus_provider_id=providers_data.get("focus_provider_id", "")
        )
        always_visible_count = sum(
            1 for p in providers_data.get("providers", [])
            if p.get("catalog_visibility") == "always_visible" and p.get("enabled", True)
        )

        model_catalog_section = {
            "entry_count": catalog.get("entry_count", 0),
            "focus_provider_id": catalog.get("focus_provider_id", ""),
            "always_visible_count": always_visible_count,
            "generated_at": catalog.get("generated_at", ""),
        }

        # ── f) AMR 状态 ──
        amr_section: Dict[str, Any] = {}
        if self.amr_registry is not None:
            try:
                if hasattr(self.amr_registry, "list_groups"):
                    amr_section["groups"] = self.amr_registry.list_groups()
                else:
                    amr_section["groups"] = []
            except Exception as e:
                amr_section = {"error": str(e), "groups": []}

        # ── g) 系统环境 ──
        system_section = {
            "python_version": sys.version,
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "config_file_path": str(CONFIG_FILE),
        }

        # ── h) 最近错误 ──
        errors_section = self._recent_errors[-10:]

        # ── i) 余额/额度缓存状态 ──
        quota_section: Dict[str, Any] = {"snapshots": {}}
        if self.quota_manager is not None:
            try:
                quota_section = self.quota_manager.list_cached()
            except Exception as e:
                quota_section = {"error": str(e), "snapshots": {}}

        request_logs_section: Dict[str, Any] = {}
        try:
            request_logs_section = RequestLogStore(
                self.config.get("request_log_path", ""),
                retention_days=self.config.get("request_log_retention_days", 30),
                max_mb=self.config.get("request_log_max_mb", 50),
            ).summary()
        except Exception as e:
            request_logs_section = {"error": str(e)}

        try:
            currency_section = exchange_rate_status_summary(self.config.get_all())
        except Exception as e:
            currency_section = {"error": str(e)}

        return {
            "codex_config": codex_config_section,
            "codex_permissions": permissions_audit,
            "auth_mode": auth_section,
            "local_proxy": proxy_status,
            "providers": {
                "count": len(providers_summary),
                "providers": providers_summary,
                "store_path": providers_data.get("store_path", ""),
            },
            "model_catalog": model_catalog_section,
            "amr": amr_section,
            "quota": quota_section,
            "request_logs": request_logs_section,
            "currency": currency_section,
            "system": system_section,
            "errors": errors_section,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def collect_redacted(self) -> Dict[str, Any]:
        """
        收集脱敏诊断信息。

        设计意图：
          - 所有 api_key、access_token、secret 等敏感字段会被替换为 ******** 或空串。
          - 这是默认对外暴露的数据视图，供前端 Diagnostics 页面和导出包使用。

        Returns:
            与 collect_all() 结构相同但已脱敏的字典。
        """
        data = self.collect_all()
        return redact_secrets(data)

    def export_safe_bundle(self) -> str:
        """
        导出安全诊断包（JSON 字符串）。

        设计意图：
          - 用户可将此 JSON 发送给开发者排障，无需担心凭据泄露。
          - 包含时间戳和脱敏标记，便于追溯。

        Returns:
            格式化的 JSON 字符串（ensure_ascii=False，支持中文）。
        """
        bundle = {
            **self.collect_redacted(),
            "export_meta": {
                "version": "1.0",
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "redacted": True,
            },
        }
        return json.dumps(bundle, ensure_ascii=False, indent=2)

    def check_provider_connectivity(self, provider_id: str, timeout: int = 10) -> Dict[str, Any]:
        """
        测试单个 provider 的网络连通性。

        设计意图：
          - 仅做最小权限探测（HEAD /models 或 HEAD base_url），不发送含 API key 的请求。
          - 返回结构化结果，前端可根据 reachable/success 展示不同颜色图标。

        工程权衡：
          - 使用 urllib 而非 requests：与 proxy_server.py 保持一致，避免新增依赖。
          - 禁用系统代理：防止 Windows IE 代理设置干扰诊断结果。
          - 对 401/403/404/405 视为「可达」：这些状态码说明网络路径已通，
            只是认证或路径细节问题，与「完全连不上」应区分。

        边界条件：
          - provider 不存在时返回明确错误。
          - base_url 为空时直接返回错误，避免无意义请求。
          - SSL 错误单独标注，帮助用户识别证书/中间人问题。

        Args:
            provider_id: ProviderRegistry 中的 provider ID。
            timeout: 请求超时秒数，默认 10 秒。

        Returns:
            连通性结果字典，含 success、reachable、status_code、error 等字段。
        """
        provider = self.provider_registry.get_provider(provider_id, include_secrets=True)
        if not provider:
            return {
                "success": False,
                "reachable": False,
                "provider_id": provider_id,
                "error": f"Provider not found: {provider_id}",
            }

        base_url = provider.get("base_url", "").rstrip("/")
        if not base_url:
            return {
                "success": False,
                "reachable": False,
                "provider_id": provider_id,
                "error": "Provider has no base_url configured.",
            }

        # 测试端点优先级：多数 OpenAI 兼容服务支持 GET/HEAD /models
        urls_to_try = [
            f"{base_url}/models",
            base_url,
        ]

        for url in urls_to_try:
            try:
                req = urllib.request.Request(
                    url,
                    method="HEAD",
                    headers=_provider_health_check_headers(provider),
                )
                # 禁用系统代理，确保直连
                proxy_handler = urllib.request.ProxyHandler({})
                opener = urllib.request.build_opener(proxy_handler)
                with opener.open(req, timeout=timeout) as resp:
                    return {
                        "success": True,
                        "reachable": True,
                        "provider_id": provider_id,
                        "status_code": resp.getcode(),
                        "url": url,
                        "method": "HEAD",
                    }
            except urllib.error.HTTPError as e:
                # 服务器可达但返回 HTTP 错误：对 401/403/404/405/422 视为「网络通」
                if e.code in (401, 403, 404, 405, 422):
                    return {
                        "success": True,
                        "reachable": True,
                        "provider_id": provider_id,
                        "status_code": e.code,
                        "url": url,
                        "method": "HEAD",
                        "note": "Server responded with expected auth/method error; network path is open.",
                    }
                return {
                    "success": False,
                    "reachable": True,
                    "provider_id": provider_id,
                    "status_code": e.code,
                    "url": url,
                    "method": "HEAD",
                    "error": f"HTTP error: {e.code}",
                }
            except urllib.error.URLError as e:
                reason = str(e.reason) if hasattr(e, "reason") else str(e)
                # SSL/TLS 错误单独分类
                if "SSL" in reason or "CERTIFICATE" in reason.upper():
                    return {
                        "success": False,
                        "reachable": False,
                        "provider_id": provider_id,
                        "error": f"SSL/TLS error: {reason}",
                        "url": url,
                    }
                # 其他 URLError（如 DNS 失败、连接拒绝）继续尝试下一个 URL
                continue
            except socket.timeout:
                continue

        # 所有 URL 均失败
        return {
            "success": False,
            "reachable": False,
            "provider_id": provider_id,
            "error": "Could not connect to any tested endpoint.",
            "urls_tested": urls_to_try,
        }


def _provider_health_check_headers(provider: Dict[str, Any]) -> Dict[str, str]:
    """Build low-risk headers for provider connectivity probes.

    Health checks should verify the network path without sending credentials.
    User-Agent is still first-class because some providers and gateways use it
    for allowlists, analytics, or routing.
    """
    headers: Dict[str, str] = {}
    configured_headers = provider.get("headers") if isinstance(provider.get("headers"), dict) else {}
    for key, value in configured_headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.lower() in {"authorization", "x-api-key", "api-key", "apikey", "bearer"}:
            continue
        headers[key] = value

    user_agent = str(provider.get("user_agent") or configured_headers.get("User-Agent") or "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    elif "User-Agent" not in headers:
        headers["User-Agent"] = "Codex-Enhance-Manager-HealthCheck/1.0"

    return headers
