"""
app.py - Flask Web 应用 + 所有 API 端点
提供 RESTful API 供前端 SPA 调用

设计意图：
  - 纯 API 后端：所有 HTML 渲染由前端 SPA（static/js/*.js）完成，
    Flask 只负责数据接口和静态文件服务。
  - 全局状态管理：create_app 内初始化 Config、CodexDB、BackupManager、
    TokenStats、ProviderRegistry 等实例，通过闭包在 API 端点间共享。
  - 异常捕获：每个端点统一用 try/except 包裹，返回 JSON 错误响应，
    防止后端崩溃导致前端收到 500 HTML 页面。

工程权衡：
  - 不使用 Blueprint：当前端点数量适中（~40 个），全部写在 create_app 内
    可读性尚可；若未来端点翻倍，建议拆分为 Blueprint。
  - _refresh_provider_registry_path：provider_store_path 可能在设置中被修改，
    每次访问 provider API 前刷新路径，保证一致性。
  - JSON_AS_ASCII = False：确保中文错误消息、模型名称等在前端正确显示，
    而非被转义为 \\uXXXX。

Windows 平台特殊性：
  - send_from_directory 服务静态文件：Windows 路径分隔符差异由 Flask/Pathlib
    自动处理，无需手动替换。
"""
import os
import copy
import hashlib
import hmac
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from flask import Flask, has_request_context, jsonify, request, send_from_directory

from config import Config, CONFIG_FILE, DEFAULT_CONFIG
from db import CodexDB
from reader import read_messages, export_to_markdown, export_to_text, get_file_size_mb
from backup import BackupManager
from sync import full_sync, is_codex_running, kill_codex, start_codex, resolve_codex_home, CODEX_PLUS_PLUS_PATH
from auto_detect import detect_all
from token_stats import TokenStats
from codex_rollout_usage import get_codex_rollout_cache_stats
from providers import ProviderRegistry, DEFAULT_STORE_PATH, merge_provider_update, normalize_model
from amr_registry import AMRRegistry, DEFAULT_GROUP_DISPLAY_NAME, DEFAULT_GROUP_ID
from codex_config import (
    CodexConfigManager,
    backup_file,
    codex_goals_enabled_from_config,
    detect_auth_mode,
    is_official_oauth,
    merge_codex_goals_feature,
    redact_auth_for_preview,
    restore_file,
    sanitize_codex_config_for_managed_write,
    save_config_toml,
    load_config_toml as _load_config_toml,
)
from codex_official_provider import build_official_login_provider, resolve_effective_codex_settings
from proxy_server import (
    DEFAULT_PROXY_PORT,
    LocalProxyServer,
    _build_upstream_headers,
    _extract_model_id_for_upstream,
    _provider_alias_map,
    _provider_alias_patterns,
    _route_api_format,
)
from domestic_responses import build_domestic_responses_probe_preview
from diagnostics import DiagnosticsCollector
from move_repair import MoveRepairManager
from guardrails import codex_mutation_error_payload, has_codex_mutation_confirmation
from app_paths import LEGACY_APP_DIR, LEGACY_CONFIG_FILE, app_data_dir, ensure_app_dirs, is_within
from currency import (
    build_rate_snapshot,
    convert_amount,
    normalize_currency_settings,
    preserve_redacted_currency_secret,
    redact_currency_settings,
    update_currency_config,
)
from costing import estimate_request_cost, pricing_preview_payload
from quota import QuotaManager, refresh_provider_quota_preview
from request_logs import RequestLogStore
from startup_manager import STARTUP_CONFIG_KEYS, StartupManager
from desktop_shortcuts import DesktopShortcutManager
from auto_approval_runtime import AutoApprovalModelReviewer
from capabilities import merge_provider_model_capabilities
from media_adapters import build_media_adapter_preview_bundle
from media_proxy import build_media_route_readiness
from codex_approval_bridge import CodexApprovalBridgeError, build_codex_approval_bridge_preview
from app_version import APP_REPOSITORY_URL, APP_VERSION
from updater import UpdateManager
from codex_injector import DEFAULT_CDP_PORT, backend_url_from_env, inject_codex_enhancements
from provider_routing import provider_allows_local_routing
from local_proxy_auth import preserve_redacted_local_proxy_token, redact_local_proxy_token
from responses_adapter import models_url


def _bundle_root() -> Path:
    """Return the source root, or PyInstaller's extraction root in packaged mode."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _static_dir() -> Path:
    return _bundle_root() / "static"


def _asset_dir() -> Path:
    return _bundle_root()


UNINSTALL_CLEANUP_CONFIRMATION = "UNINSTALL_CLEANUP"
START_MODE_PROXY_INJECTION = "proxy_injection"
START_MODE_PRESERVE_LOGIN_PROXY = "preserve_login_proxy"
START_MODE_OFFICIAL_DIRECT = "official_direct"
START_MODE_CURRENT_FOCUS = "current_focus"
START_MODES = {
    START_MODE_PROXY_INJECTION,
    START_MODE_PRESERVE_LOGIN_PROXY,
    START_MODE_OFFICIAL_DIRECT,
}
START_MODE_FOCUS_ALIASES = {
    "",
    START_MODE_CURRENT_FOCUS,
    "auto",
    "focused_provider",
}
SECRET_REVEAL_FIELDS = {"api_key", "secondary_usage_key"}
CHAT_HISTORY_RISK_CONFIRMATION = "CHAT_HISTORY_MAY_BE_LOST"
CONFIG_REPAIR_REMOVED_KEYS = [
    "model_provider",
    "provider",
    "defaults",
    "model_providers",
    "mcp_servers",
    "notify",
    "hooks",
    "openai_base_url",
    "chatgpt_base_url",
    "model_catalog_json",
    "model_instructions_file",
    "experimental_compact_prompt_file",
    "profile",
    "profiles",
    "sandbox_workspace_write",
    "permissions",
    "agents",
    "otel",
]
CONFIG_TEMPLATE_SCALAR_KEYS = [
    "model",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "model_supports_reasoning_summaries",
    "service_tier",
    "file_opener",
    "hide_agent_reasoning",
    "show_raw_agent_reasoning",
    "check_for_update_on_startup",
    "project_doc_max_bytes",
    "project_doc_fallback_filenames",
    "project_root_markers",
    "cli_auth_credentials_store",
    "mcp_oauth_credentials_store",
]
VALID_WINDOWS_SANDBOXES = {"elevated", "unelevated"}
VALID_CLI_AUTH_STORES = {"file", "keyring", "auto"}
FETCHED_MODEL_DEFAULT_CONTEXT_WINDOW = 200000
MODEL_LIST_FETCH_TIMEOUT_SECONDS = 30


def _normalize_codex_start_mode(
    body: Dict[str, Any],
    login_defaults: Dict[str, Any],
    provider_payload: Dict[str, Any] | None = None,
) -> str:
    raw_mode = str(body.get("start_mode") or "").strip()
    if raw_mode in START_MODES:
        return raw_mode
    if body.get("official_mode") is True:
        return START_MODE_OFFICIAL_DIRECT
    if raw_mode in START_MODE_FOCUS_ALIASES and isinstance(provider_payload, dict):
        if _provider_focus_is_official_login(provider_payload):
            return START_MODE_OFFICIAL_DIRECT
        if _provider_focus_uses_other_api(provider_payload):
            return START_MODE_PRESERVE_LOGIN_PROXY
    return str(login_defaults.get("default_start_mode") or START_MODE_PROXY_INJECTION)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    if value is None:
        return default
    return bool(value)


def _coerce_port(value: Any, default: int = DEFAULT_CDP_PORT) -> int:
    try:
        return min(max(int(value), 1), 65535)
    except (TypeError, ValueError):
        return default


def _tcp_port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((host, int(port))) != 0
    except Exception:
        return False


def _select_available_cdp_port(preferred: int, scan_limit: int = 40) -> int:
    preferred = _coerce_port(preferred, DEFAULT_CDP_PORT)
    if _tcp_port_is_available(preferred):
        return preferred
    for offset in range(1, max(int(scan_limit), 1) + 1):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if _tcp_port_is_available(candidate):
            return candidate
    return preferred


def _secret_reveal_password_configured(config_obj: Config) -> bool:
    return bool(config_obj.get("secret_reveal_password_hash") and config_obj.get("secret_reveal_password_salt"))


def _hash_secret_reveal_password(password: str, salt_hex: str = "", iterations: int = 210000) -> Dict[str, Any]:
    iterations = min(max(int(iterations or 210000), 100000), 1000000)
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    return {
        "secret_reveal_password_hash": digest.hex(),
        "secret_reveal_password_salt": salt.hex(),
        "secret_reveal_password_iterations": iterations,
    }


def _verify_secret_reveal_password(config_obj: Config, password: str) -> bool:
    if not _secret_reveal_password_configured(config_obj):
        return True
    try:
        expected = str(config_obj.get("secret_reveal_password_hash") or "")
        salt = str(config_obj.get("secret_reveal_password_salt") or "")
        iterations = int(config_obj.get("secret_reveal_password_iterations", 210000) or 210000)
        candidate = _hash_secret_reveal_password(password or "", salt, iterations)["secret_reveal_password_hash"]
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def _redact_secret_reveal_settings(settings: Dict[str, Any], config_obj: Config | None = None) -> Dict[str, Any]:
    redacted = copy.deepcopy(settings or {})
    configured = bool(redacted.get("secret_reveal_password_hash") and redacted.get("secret_reveal_password_salt"))
    if config_obj is not None:
        configured = _secret_reveal_password_configured(config_obj)
    redacted.pop("secret_reveal_password_hash", None)
    redacted.pop("secret_reveal_password_salt", None)
    redacted.pop("secret_reveal_password_iterations", None)
    redacted["secret_reveal_password_configured"] = configured
    return redacted


def _settings_response_payload(settings: Dict[str, Any], config_obj: Config | None = None) -> Dict[str, Any]:
    return _redact_secret_reveal_settings(
        redact_local_proxy_token(redact_currency_settings(settings)),
        config_obj,
    )


def _drop_secret_reveal_response_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = copy.deepcopy(data or {})
    for key in (
        "secret_reveal_password_hash",
        "secret_reveal_password_salt",
        "secret_reveal_password_iterations",
        "secret_reveal_password_configured",
    ):
        cleaned.pop(key, None)
    return cleaned


def _resolve_codex_injection_settings(
    config_obj: Config,
    body: Dict[str, Any],
    *,
    use_codex_plus_plus: bool,
    official_mode: bool = False,
    persist: bool = False,
    avoid_occupied_port: bool = False,
) -> Dict[str, Any]:
    configured_enabled = _coerce_bool(config_obj.get("codex_injection_enabled", True), True)
    requested_enabled = _coerce_bool(body.get("enable_cdp_injection"), configured_enabled)
    enabled = requested_enabled and not use_codex_plus_plus
    cdp_port = _coerce_port(body.get("cdp_port"), _coerce_port(config_obj.get("codex_cdp_port", DEFAULT_CDP_PORT)))
    if enabled and avoid_occupied_port:
        cdp_port = _select_available_cdp_port(cdp_port)
    if persist:
        config_obj.update({
            "codex_injection_enabled": requested_enabled,
            "codex_cdp_port": cdp_port,
        })
    return {
        "enabled": enabled,
        "requested_enabled": requested_enabled,
        "cdp_port": cdp_port,
    }


def _official_login_defaults(mgr: CodexConfigManager) -> Dict[str, Any]:
    official = is_official_oauth(mgr.read_auth())
    return {
        "official_oauth_detected": official,
        "default_preserve_official_auth": official,
        "default_start_mode": "preserve_login_proxy" if official else "proxy_injection",
        "available_start_modes": [
            START_MODE_PRESERVE_LOGIN_PROXY,
            START_MODE_OFFICIAL_DIRECT,
            START_MODE_PROXY_INJECTION,
        ],
    }


def _official_provider_extra(mgr: CodexConfigManager | None = None) -> list[Dict[str, Any]]:
    try:
        manager = mgr or CodexConfigManager()
        provider = build_official_login_provider(
            manager.read_config(),
            manager.read_auth(),
            allow_placeholder=True,
        )
        return [provider] if provider else []
    except Exception:
        return []


def _effective_codex_settings(config_data: Dict[str, Any], auth_data: Dict[str, Any]) -> Dict[str, Any]:
    auth_mode = detect_auth_mode(auth_data if isinstance(auth_data, dict) else {})
    settings = resolve_effective_codex_settings(config_data if isinstance(config_data, dict) else {}, auth_mode)
    settings["auth_mode"] = auth_mode
    return settings


def _provider_focus_is_official_login(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    focus_provider_id = str(payload.get("focus_provider_id") or "").strip()
    if focus_provider_id == "codex_official":
        return True
    if not focus_provider_id:
        return False
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    for provider in providers:
        if not isinstance(provider, dict) or str(provider.get("id") or "") != focus_provider_id:
            continue
        return bool(
            provider.get("codex_login")
            or provider.get("switch_only")
            or provider.get("auth_mode") == "official_oauth"
            or provider.get("kind") == "codex_official_login"
        )
    return False


def _focused_provider_from_payload(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return "", None
    focus_provider_id = str(payload.get("focus_provider_id") or "").strip()
    if not focus_provider_id:
        return "", None
    providers = payload.get("providers") if isinstance(payload.get("providers"), list) else []
    for provider in providers:
        if isinstance(provider, dict) and str(provider.get("id") or "") == focus_provider_id:
            return focus_provider_id, provider
    return focus_provider_id, None


def _provider_focus_uses_other_api(payload: Dict[str, Any]) -> bool:
    focus_provider_id, provider = _focused_provider_from_payload(payload)
    if not focus_provider_id or _provider_focus_is_official_login(payload):
        return False
    if provider is None:
        return False
    return provider_allows_local_routing(provider)


def _official_usage_visible_for_current_mode(
    auth_mode: str,
    payload: Dict[str, Any],
    last_start_mode: str = "",
) -> bool:
    if _provider_focus_is_official_login(payload):
        return True
    if _provider_focus_uses_other_api(payload):
        return False
    if last_start_mode in {START_MODE_PROXY_INJECTION, START_MODE_PRESERVE_LOGIN_PROXY}:
        return False
    if last_start_mode == START_MODE_OFFICIAL_DIRECT:
        return auth_mode == "official_oauth"
    focus_provider_id, _provider = _focused_provider_from_payload(payload)
    if focus_provider_id:
        return False
    return auth_mode == "official_oauth"


def _config_goals_enabled(config_obj: Config) -> bool:
    return _coerce_bool(config_obj.get("codex_goals_enabled", True), True)


def disable_codex_enhance_provider_config(
    mgr: CodexConfigManager,
    goals_enabled: bool | None = None,
) -> Dict[str, Any]:
    """Remove local proxy routing from Codex config while preserving official auth."""
    result = {
        "success": True,
        "restart_required": True,
        "changed": False,
        "backups": {},
        "message": "本地代理 provider 已禁用。需要重启 Codex 使变更生效。",
    }
    current_config = mgr.read_config()
    next_config = sanitize_codex_config_for_managed_write(current_config)
    if next_config.get("model_provider") == "codex_enhance_manager":
        next_config["model_provider"] = "openai"
    if str(next_config.get("model") or "").strip().lower() == "auto":
        next_config.pop("model", None)
    model_providers = next_config.get("model_providers")
    if isinstance(model_providers, dict) and "codex_enhance_manager" in model_providers:
        model_providers = dict(model_providers)
        del model_providers["codex_enhance_manager"]
        if model_providers:
            next_config["model_providers"] = model_providers
        else:
            next_config.pop("model_providers", None)
    if goals_enabled is not None:
        next_config = merge_codex_goals_feature(next_config, bool(goals_enabled))
        result["goals_enabled"] = bool(goals_enabled)

    changed = next_config != current_config
    result["changed"] = changed
    if changed:
        if mgr.config_path.exists():
            backup_path = backup_file(str(mgr.config_path), mgr.backup_dir)
            if backup_path:
                result["backups"]["config_toml"] = backup_path
        save_config_toml(str(mgr.config_path), next_config)
    else:
        result["message"] = "当前已是官方登录优先配置。"
    return result


def _is_safe_toml_scalar(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, list):
        return all(_is_safe_toml_scalar(item) and not isinstance(item, list) for item in value)
    return False


def _looks_like_wsl_or_unix_path(value: Any) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    lowered = text.lower()
    return (
        lowered.startswith("/home/")
        or lowered.startswith("/mnt/")
        or lowered.startswith("/usr/")
        or lowered.startswith("/bin/")
        or lowered.startswith("~/")
        or lowered.startswith("wsl ")
        or lowered == "wsl"
        or "\\\\wsl$" in str(value or "").lower()
    )


def _windows_path_missing(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _looks_like_wsl_or_unix_path(text):
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", text) or text.startswith("\\\\"):
        try:
            return not Path(text).exists()
        except Exception:
            return False
    return False


def _first_command_part(command: Any) -> str:
    if isinstance(command, list) and command:
        return str(command[0] or "").strip()
    text = str(command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        match = re.match(r'"([^"]+)"', text)
        if match:
            return match.group(1)
    if text.startswith("'"):
        match = re.match(r"'([^']+)'", text)
        if match:
            return match.group(1)
    return text.split()[0]


def _append_config_issue(
    issues: list[Dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    path: str,
    recommendation: str,
) -> None:
    issues.append({
        "severity": severity,
        "code": code,
        "message": message,
        "path": path,
        "recommendation": recommendation,
    })


def inspect_codex_config_risks(
    mgr: CodexConfigManager,
    config_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Inspect startup-sensitive Codex config keys without modifying files."""
    config_data = config_data if isinstance(config_data, dict) else mgr.read_config()
    issues: list[Dict[str, Any]] = []
    strict_toml_ok = True
    parse_error = ""
    raw_content = ""

    if mgr.config_path.exists():
        try:
            raw_content = mgr.config_path.read_text(encoding="utf-8")
            import tomllib
            tomllib.loads(raw_content)
        except Exception as exc:
            strict_toml_ok = False
            parse_error = str(exc)
            _append_config_issue(
                issues,
                "critical",
                "toml_parse_error",
                "config.toml 不是严格合法的 TOML，重复表、截断或非法字符串会让 Codex 启动期直接报错或卡住。",
                "config.toml",
                "备份后重置为模板配置，再按需重新开启 provider/MCP/hooks。",
            )

    for legacy_key in ("profile", "profiles"):
        if legacy_key in config_data:
            _append_config_issue(
                issues,
                "warning",
                "legacy_profile_config",
                "检测到旧版 profile 配置；新版 Codex 使用独立的 profile-name.config.toml，旧配置可能造成启动提示或配置不生效。",
                legacy_key,
                "迁移到独立 profile 文件，或在模板修复时移除旧键。",
            )

    model_provider = str(config_data.get("model_provider") or config_data.get("provider") or "").strip()
    model_providers = config_data.get("model_providers") if isinstance(config_data.get("model_providers"), dict) else {}
    if model_provider and model_provider not in {"openai", "ollama", "lmstudio", "amazon-bedrock"} and model_provider not in model_providers:
        _append_config_issue(
            issues,
            "critical",
            "missing_selected_provider",
            f"当前 model_provider 指向 {model_provider!r}，但 config.toml 中没有对应 provider 定义。",
            "model_provider",
            "切回官方默认 provider，或补齐对应 [model_providers.<id>]。",
        )
    if "openai" in model_providers or "ollama" in model_providers or "lmstudio" in model_providers:
        _append_config_issue(
            issues,
            "warning",
            "reserved_provider_override",
            "自定义 provider 使用了 Codex 内置保留 ID，可能被忽略或导致 provider 选择异常。",
            "model_providers",
            "不要自定义 openai/ollama/lmstudio；OpenAI 代理用 openai_base_url 或单独 provider id。",
        )
    for provider_id, provider in model_providers.items():
        if not isinstance(provider, dict):
            _append_config_issue(
                issues,
                "warning",
                "invalid_provider_shape",
                f"provider {provider_id!r} 不是表结构。",
                f"model_providers.{provider_id}",
                "删除异常 provider 或按官方 TOML 表结构重建。",
            )
            continue
        auth = provider.get("auth")
        if isinstance(auth, dict):
            command = _first_command_part(auth.get("command"))
            if _looks_like_wsl_or_unix_path(command) and os.name == "nt":
                _append_config_issue(
                    issues,
                    "critical",
                    "provider_auth_wsl_command",
                    f"provider {provider_id!r} 的认证命令看起来是 WSL/Linux 路径，Windows 原生 Codex 启动时可能执行失败。",
                    f"model_providers.{provider_id}.auth.command",
                    "改成 Windows 可执行文件，或切到 WSL 内运行 Codex；模板修复会移除该 provider。",
                )
            elif _windows_path_missing(command):
                _append_config_issue(
                    issues,
                    "warning",
                    "provider_auth_missing_command",
                    f"provider {provider_id!r} 的认证命令路径不存在。",
                    f"model_providers.{provider_id}.auth.command",
                    "修正认证 helper 路径，或移除该 provider。",
                )

    for base_key in ("openai_base_url", "chatgpt_base_url", "experimental_realtime_ws_base_url"):
        if config_data.get(base_key):
            _append_config_issue(
                issues,
                "warning",
                "base_url_override",
                f"{base_key} 会改写官方 OpenAI/ChatGPT 连接目标，代理或旧地址失效时会造成登录、用量或模型请求异常。",
                base_key,
                "官方登录态下建议不要在修复模板中保留该项。",
            )

    mcp_servers = config_data.get("mcp_servers")
    if isinstance(mcp_servers, dict):
        if len(mcp_servers) > 6:
            _append_config_issue(
                issues,
                "warning",
                "many_mcp_servers",
                "配置了较多 MCP server；Codex 启动时会初始化 MCP，远端/OAuth server 会明显拖慢启动。",
                "mcp_servers",
                "只保留必要 MCP，并给远端 server 设置合理 timeout；模板修复会移除全部用户级 MCP。",
            )
        for server_id, server in mcp_servers.items():
            if not isinstance(server, dict):
                _append_config_issue(
                    issues,
                    "warning",
                    "invalid_mcp_shape",
                    f"MCP server {server_id!r} 不是表结构。",
                    f"mcp_servers.{server_id}",
                    "删除异常 MCP 配置后重新添加。",
                )
                continue
            enabled = _coerce_bool(server.get("enabled"), True)
            required = _coerce_bool(server.get("required"), False)
            if required and enabled:
                _append_config_issue(
                    issues,
                    "critical",
                    "required_mcp_can_block_startup",
                    f"MCP server {server_id!r} 标记为 required，初始化失败会导致 Codex 启动失败。",
                    f"mcp_servers.{server_id}.required",
                    "除非必须，改为 required=false；模板修复会移除该 MCP。",
                )
            command = _first_command_part(server.get("command"))
            if command:
                if _looks_like_wsl_or_unix_path(command) and os.name == "nt":
                    _append_config_issue(
                        issues,
                        "critical",
                        "mcp_wsl_command_on_windows",
                        f"MCP server {server_id!r} 使用 WSL/Linux 命令路径，Windows 原生 Codex 可能启动失败。",
                        f"mcp_servers.{server_id}.command",
                        "改成 Windows 命令，或在 WSL 环境运行 Codex；模板修复会移除该 MCP。",
                    )
                elif _windows_path_missing(command):
                    _append_config_issue(
                        issues,
                        "warning",
                        "mcp_missing_command",
                        f"MCP server {server_id!r} 的 command 路径不存在。",
                        f"mcp_servers.{server_id}.command",
                        "修正 command 路径或删除该 MCP。",
                    )
            if server.get("url"):
                url = str(server.get("url") or "")
                if not re.match(r"^https?://(127\.0\.0\.1|localhost|::1|\[::1\])", url, re.I):
                    _append_config_issue(
                        issues,
                        "warning",
                        "remote_http_mcp",
                        f"MCP server {server_id!r} 是远端 HTTP server；网络/VPN/OAuth 元数据不可达时可能卡启动。",
                        f"mcp_servers.{server_id}.url",
                        "必要时降低 startup_timeout_sec，或在模板修复中移除。",
                    )
            timeout = server.get("startup_timeout_sec")
            try:
                if timeout is not None and float(timeout) > 30:
                    _append_config_issue(
                        issues,
                        "warning",
                        "long_mcp_startup_timeout",
                        f"MCP server {server_id!r} 的 startup_timeout_sec 较长，失败时用户会误以为 Codex 卡住。",
                        f"mcp_servers.{server_id}.startup_timeout_sec",
                        "把启动超时降到 3-10 秒，或关闭/移除不稳定 MCP。",
                    )
            except (TypeError, ValueError):
                _append_config_issue(
                    issues,
                    "warning",
                    "invalid_mcp_startup_timeout",
                    f"MCP server {server_id!r} 的 startup_timeout_sec 不是数字。",
                    f"mcp_servers.{server_id}.startup_timeout_sec",
                    "改成秒数数字。",
                )
    elif mcp_servers is not None:
        _append_config_issue(
            issues,
            "warning",
            "invalid_mcp_root",
            "mcp_servers 不是表结构。",
            "mcp_servers",
            "删除并通过 codex mcp add 重新创建。",
        )

    for oauth_key in ("mcp_oauth_callback_port", "mcp_oauth_callback_url"):
        if oauth_key not in config_data:
            continue
        value = config_data.get(oauth_key)
        if oauth_key.endswith("_port"):
            try:
                port = int(value)
                if port < 1 or port > 65535:
                    raise ValueError
            except Exception:
                _append_config_issue(
                    issues,
                    "warning",
                    "invalid_mcp_oauth_port",
                    "mcp_oauth_callback_port 不是合法端口。",
                    oauth_key,
                    "移除该项或改成 1-65535 之间的端口。",
                )
        elif value and "localhost" not in str(value).lower() and "127.0.0.1" not in str(value):
            _append_config_issue(
                issues,
                "warning",
                "nonlocal_mcp_oauth_callback",
                "mcp_oauth_callback_url 是非本地 URL；Codex 会绑定 0.0.0.0，网络环境异常时可能影响 OAuth 初始化。",
                oauth_key,
                "本地使用时移除该项，或确认远端 callback 可达。",
            )

    notify = config_data.get("notify")
    if notify:
        command = _first_command_part(notify)
        if _looks_like_wsl_or_unix_path(command) and os.name == "nt":
            _append_config_issue(
                issues,
                "warning",
                "notify_wsl_command_on_windows",
                "notify 使用 WSL/Linux 命令，Windows 原生 Codex 回调通知可能失败。",
                "notify",
                "改成 Windows 可执行文件，或移除 notify。",
            )
        elif _windows_path_missing(command):
            _append_config_issue(
                issues,
                "warning",
                "notify_missing_command",
                "notify 指向的可执行文件不存在。",
                "notify",
                "修正路径或移除 notify。",
            )

    if isinstance(config_data.get("hooks"), dict):
        _append_config_issue(
            issues,
            "warning",
            "inline_hooks_present",
            "config.toml 内有 inline hooks；hook 命令、信任状态或超时会影响会话启动和回合结束。",
            "hooks",
            "异常排查时先移除 inline hooks，或用 /hooks 逐项审查。",
        )
    hooks_json = mgr.codex_home / "hooks.json"
    if hooks_json.exists():
        _append_config_issue(
            issues,
            "warning",
            "hooks_json_present",
            "~/.codex/hooks.json 存在；即使 config.toml 重置，用户级 hooks 仍可能被加载。",
            str(hooks_json),
            "如果模板修复后仍卡住，再备份并临时移走 hooks.json。",
        )

    features = config_data.get("features")
    if isinstance(features, dict) and isinstance(features.get("network_proxy"), dict):
        _append_config_issue(
            issues,
            "info",
            "network_proxy_feature",
            "features.network_proxy 会改变 sandboxed networking 行为，错误规则可能造成网络请求异常。",
            "features.network_proxy",
            "排查启动/网络问题时先回到模板网络设置。",
        )

    windows_cfg = config_data.get("windows")
    if isinstance(windows_cfg, dict):
        sandbox_value = str(windows_cfg.get("sandbox") or "").strip().lower()
        if sandbox_value and sandbox_value not in VALID_WINDOWS_SANDBOXES:
            _append_config_issue(
                issues,
                "critical",
                "invalid_windows_sandbox",
                "windows.sandbox 不是官方支持的 elevated/unelevated。",
                "windows.sandbox",
                "Windows 原生运行建议设为 elevated；模板修复会写回 elevated。",
            )
    elif windows_cfg is not None:
        _append_config_issue(
            issues,
            "warning",
            "invalid_windows_table",
            "windows 配置不是表结构。",
            "windows",
            "删除异常 windows 配置，模板修复会重建。",
        )

    workspace_cfg = config_data.get("sandbox_workspace_write")
    if isinstance(workspace_cfg, dict):
        roots = workspace_cfg.get("writable_roots")
        if isinstance(roots, list):
            for idx, root in enumerate(roots):
                if os.name == "nt" and _looks_like_wsl_or_unix_path(root):
                    _append_config_issue(
                        issues,
                        "warning",
                        "workspace_root_wsl_on_windows",
                        "writable_roots 包含 WSL/Linux 路径，Windows sandbox 刷新/权限判断可能失败。",
                        f"sandbox_workspace_write.writable_roots[{idx}]",
                        "改成 Windows 绝对路径或移除该 root。",
                    )
                elif _windows_path_missing(root):
                    _append_config_issue(
                        issues,
                        "warning",
                        "workspace_root_missing",
                        "writable_roots 包含不存在的 Windows 路径。",
                        f"sandbox_workspace_write.writable_roots[{idx}]",
                        "移除不存在路径。",
                    )

    for path_key in ("model_catalog_json", "model_instructions_file", "experimental_compact_prompt_file", "log_dir", "sqlite_home"):
        if _windows_path_missing(config_data.get(path_key)):
            _append_config_issue(
                issues,
                "warning",
                "missing_config_path",
                f"{path_key} 指向的路径不存在，启动期读取可能失败或回退。",
                path_key,
                "修正路径，或由模板修复移除该项。",
            )
        elif os.name == "nt" and _looks_like_wsl_or_unix_path(config_data.get(path_key)):
            _append_config_issue(
                issues,
                "warning",
                "wsl_path_on_windows",
                f"{path_key} 看起来是 WSL/Linux 路径，Windows 原生 Codex 可能无法访问。",
                path_key,
                "改成 Windows 路径或切到 WSL Codex。",
            )

    auth_store = str(config_data.get("cli_auth_credentials_store") or "").strip().lower()
    if auth_store and auth_store not in VALID_CLI_AUTH_STORES:
        _append_config_issue(
            issues,
            "warning",
            "invalid_cli_auth_store",
            "cli_auth_credentials_store 不是 file/keyring/auto。",
            "cli_auth_credentials_store",
            "改回 file/keyring/auto；模板修复会保留合法值。",
        )
    if config_data.get("forced_login_method") or config_data.get("forced_chatgpt_workspace_id"):
        _append_config_issue(
            issues,
            "warning",
            "forced_login_can_logout",
            "forced_login_method/forced_chatgpt_workspace_id 会在凭据不匹配时让 Codex 登出并退出。",
            "forced_login_method",
            "个人环境排查时移除强制登录限制。",
        )

    plugins = config_data.get("plugins")
    if isinstance(plugins, dict):
        for plugin_id, plugin_cfg in plugins.items():
            if isinstance(plugin_cfg, dict) and isinstance(plugin_cfg.get("mcp_servers"), dict):
                _append_config_issue(
                    issues,
                    "warning",
                    "plugin_mcp_overrides",
                    f"插件 {plugin_id!r} 有 MCP 覆盖配置；插件自带 MCP 也可能影响启动速度或可用性。",
                    f"plugins.{plugin_id}.mcp_servers",
                    "模板修复只保留插件 enabled 状态，不保留 MCP 覆盖。",
                )

    counts = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues:
        severity = issue.get("severity")
        if severity in counts:
            counts[severity] += 1

    return {
        "strict_toml_ok": strict_toml_ok,
        "parse_error": parse_error,
        "issue_count": len(issues),
        "counts": counts,
        "issues": issues,
        "repair_template_removes": list(CONFIG_REPAIR_REMOVED_KEYS),
        "sources": [
            "OpenAI Codex config/auth/MCP/hooks/Windows docs",
            "openai/codex GitHub issue reports for duplicate TOML and MCP startup latency",
        ],
        "has_raw_config": bool(raw_content),
    }


def _sanitize_codex_projects(projects: Any) -> Dict[str, Any]:
    if not isinstance(projects, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for project_path, project_cfg in projects.items():
        if not isinstance(project_cfg, dict):
            continue
        trust_level = str(project_cfg.get("trust_level") or "").strip()
        if trust_level not in {"trusted", "untrusted"}:
            continue
        sanitized[str(project_path)] = {"trust_level": trust_level}
    return sanitized


def _sanitize_codex_plugins(plugins: Any) -> Dict[str, Any]:
    if not isinstance(plugins, dict):
        return {}
    sanitized: Dict[str, Any] = {}
    for plugin_id, plugin_cfg in plugins.items():
        if not isinstance(plugin_cfg, dict):
            continue
        if "enabled" in plugin_cfg:
            sanitized[str(plugin_id)] = {"enabled": _coerce_bool(plugin_cfg.get("enabled"), True)}
    return sanitized


def build_codex_config_template_state(
    config_data: Dict[str, Any],
    *,
    goals_enabled: bool = True,
) -> Dict[str, Any]:
    """Build a conservative Windows-native Codex template that preserves only low-risk preferences."""
    current = config_data if isinstance(config_data, dict) else {}
    template: Dict[str, Any] = {}
    for key in CONFIG_TEMPLATE_SCALAR_KEYS:
        value = current.get(key)
        if value is None or not _is_safe_toml_scalar(value):
            continue
        if key == "cli_auth_credentials_store" and str(value).strip().lower() not in VALID_CLI_AUTH_STORES:
            continue
        template[key] = value

    if not str(template.get("model") or "").strip():
        template["model"] = "gpt-5.5"

    features = {"goals": bool(goals_enabled)}
    template["features"] = features

    if os.name == "nt":
        current_windows = current.get("windows") if isinstance(current.get("windows"), dict) else {}
        sandbox = str(current_windows.get("sandbox") or "elevated").strip().lower()
        if sandbox not in VALID_WINDOWS_SANDBOXES:
            sandbox = "elevated"
        windows_template: Dict[str, Any] = {"sandbox": sandbox}
        if isinstance(current_windows.get("sandbox_private_desktop"), bool):
            windows_template["sandbox_private_desktop"] = current_windows["sandbox_private_desktop"]
        template["windows"] = windows_template

    projects = _sanitize_codex_projects(current.get("projects"))
    if projects:
        template["projects"] = projects
    plugins = _sanitize_codex_plugins(current.get("plugins"))
    if plugins:
        template["plugins"] = plugins
    return template


def repair_codex_config_template(
    mgr: CodexConfigManager,
    *,
    goals_enabled: bool = True,
    restart_windows: bool = False,
) -> Dict[str, Any]:
    """Back up config.toml and replace it with a conservative template state."""
    current_config = mgr.read_config()
    before_risks = inspect_codex_config_risks(mgr, current_config)
    next_config = build_codex_config_template_state(current_config, goals_enabled=goals_enabled)
    removed_keys = [key for key in CONFIG_REPAIR_REMOVED_KEYS if key in current_config]
    backup_path = ""
    if mgr.config_path.exists():
        backup_path = backup_file(str(mgr.config_path), mgr.backup_dir)

    save_config_toml(str(mgr.config_path), next_config)
    after_risks = inspect_codex_config_risks(mgr, next_config)
    result: Dict[str, Any] = {
        "success": True,
        "changed": next_config != current_config,
        "restart_required": True,
        "reboot_recommended": bool(restart_windows),
        "windows_restart_started": False,
        "config_path": str(mgr.config_path),
        "backup_path": backup_path,
        "removed_keys": removed_keys,
        "preserved_keys": list(next_config.keys()),
        "template_config": next_config,
        "before_risks": before_risks,
        "after_risks": after_risks,
        "message": "已备份并重置 Codex config.toml 到模板态。auth.json 和会话历史未被修改，请重启 Codex 使配置生效。",
    }

    if restart_windows:
        if os.name != "nt":
            result["success"] = False
            result["error"] = "Windows restart is only available on Windows."
            return result
        try:
            subprocess.Popen(
                [
                    "shutdown",
                    "/r",
                    "/t",
                    "15",
                    "/c",
                    "Codex config template repair completed. Restarting to clear stuck agent environment.",
                ],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            result["windows_restart_started"] = True
        except Exception as exc:
            result["success"] = False
            result["error"] = f"Windows restart failed: {exc}"
    return result


def build_codex_sandbox_repair_state(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a full-access sandbox/approval repair state.

    This is an explicit repair option for users who already chose Codex full
    access but got stuck in repeated approval/sandbox prompts after external
    switchers rewrote config.toml. It normalizes only permission-related fields.
    """
    next_config = copy.deepcopy(config_data if isinstance(config_data, dict) else {})
    next_config["approval_policy"] = "never"
    next_config["sandbox_mode"] = "danger-full-access"
    next_config["default_permissions"] = ":danger-full-access"
    next_config.pop("sandbox_workspace_write", None)
    if os.name == "nt":
        windows = next_config.get("windows") if isinstance(next_config.get("windows"), dict) else {}
        windows = dict(windows)
        current_sandbox = str(windows.get("sandbox") or "").strip().lower()
        windows["sandbox"] = current_sandbox if current_sandbox in VALID_WINDOWS_SANDBOXES else "elevated"
        next_config["windows"] = windows
    return next_config


def repair_codex_sandbox_permissions(mgr: CodexConfigManager) -> Dict[str, Any]:
    """Back up config.toml and normalize approval/sandbox keys only."""
    current_config = mgr.read_config()
    next_config = build_codex_sandbox_repair_state(current_config)
    before = mgr.inspect_permissions()
    backup_path = ""
    if mgr.config_path.exists():
        backup_path = backup_file(str(mgr.config_path), mgr.backup_dir)
    try:
        save_config_toml(str(mgr.config_path), next_config)
    except Exception as exc:
        if backup_path:
            restore_file(str(mgr.config_path), backup_path)
        return {
            "success": False,
            "changed": False,
            "restart_required": True,
            "backup_path": backup_path,
            "error": f"config.toml write failed: {exc}",
        }
    after = mgr.inspect_permissions()
    return {
        "success": True,
        "changed": next_config != current_config,
        "restart_required": True,
        "config_path": str(mgr.config_path),
        "backup_path": backup_path,
        "before": before,
        "after": after,
        "message": "Codex sandbox/approval config repaired. Restart Codex for the change to take effect.",
    }


def reset_codex_for_official_login(
    mgr: CodexConfigManager,
    *,
    restart_windows: bool = False,
) -> Dict[str, Any]:
    """Back up and remove Codex config/auth so Codex can perform first official login."""
    result: Dict[str, Any] = {
        "success": False,
        "changed": False,
        "restart_windows_requested": bool(restart_windows),
        "windows_restart_started": False,
        "reboot_required": True,
        "chat_history_risk": True,
        "risk_message": (
            "清除 Codex config.toml/auth.json 后，聊天记录索引和登录态重建期间可能暂时不可见，"
            "极端情况下有概率丢失。请确认已经备份重要记录。"
        ),
        "backups": {},
        "removed_files": [],
        "skipped_files": [],
        "errors": [],
    }

    kill_ok, kill_msg = kill_codex()
    result["kill_codex"] = {"success": kill_ok, "message": kill_msg}
    if not kill_ok:
        result["errors"].append(f"关闭 Codex 失败: {kill_msg}")
        return result

    for key, path in (("config_toml", mgr.config_path), ("auth_json", mgr.auth_path)):
        try:
            if not path.exists():
                result["skipped_files"].append({"id": key, "path": str(path), "reason": "missing"})
                continue
            backup_path = backup_file(str(path), mgr.backup_dir)
            if not backup_path:
                result["errors"].append(f"{path.name} backup failed")
                continue
            path.unlink()
            result["backups"][key] = backup_path
            result["removed_files"].append({"id": key, "path": str(path), "backup_path": backup_path})
            result["changed"] = True
        except Exception as exc:
            result["errors"].append(f"{path}: {exc}")

    if result["errors"]:
        return result

    if restart_windows:
        if os.name != "nt":
            result["errors"].append("Windows restart is only available on Windows.")
            return result
        try:
            subprocess.Popen(
                [
                    "shutdown",
                    "/r",
                    "/t",
                    "15",
                    "/c",
                    "Codex official login reset completed. Restarting to finish login-state cleanup.",
                ],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            result["windows_restart_started"] = True
        except Exception as exc:
            result["errors"].append(f"Windows restart failed: {exc}")
            return result

    result["success"] = True
    result["message"] = (
        "已备份并移除 Codex config.toml/auth.json。请重启电脑后打开 Codex，按官方登录流程重新登录。"
    )
    return result


def create_app() -> Flask:
    """
    创建 Flask 应用实例。

    设计意图：
      - 工厂模式：便于测试时创建独立实例，避免全局 app 污染。
      - 静态文件直接服务：index.html 和 monitor.html 通过 send_from_directory
        提供，无需模板引擎。

    Returns:
        配置完成的 Flask 应用实例。
    """
    app = Flask(
        __name__,
        static_folder=str(_static_dir()),
        static_url_path="",
    )
    app.config["CODEX_BUNDLE_ROOT"] = str(_bundle_root())
    app.config["CODEX_STATIC_DIR"] = str(_static_dir())
    app.config["JSON_AS_ASCII"] = False

    # 全局状态
    config = Config()
    db = CodexDB(config.get("db_path"))
    backup_mgr = BackupManager(config, db)
    token_stats = TokenStats(config.get("db_path"))
    provider_registry = ProviderRegistry(config.get("provider_store_path", ""))
    auto_approval_reviewer = AutoApprovalModelReviewer(
        lambda: provider_registry.list_providers(include_secrets=True).get("providers", []),
        lambda: config.get("auto_approval_system_prompt", DEFAULT_CONFIG["auto_approval_system_prompt"]),
    )
    proxy_server = LocalProxyServer(
        port=config.get("proxy_port", DEFAULT_PROXY_PORT),
        provider_store_path=config.get("provider_store_path", ""),
        request_log_path=config.get("request_log_path", ""),
        request_log_retention_days=config.get("request_log_retention_days", 30),
        request_log_max_mb=config.get("request_log_max_mb", 50),
        currency_settings=config.get_all(),
        upstream_timeout_seconds=config.get("proxy_upstream_timeout_seconds", 120),
        retry_attempts=config.get("proxy_retry_attempts", 0),
        retry_backoff_ms=config.get("proxy_retry_backoff_ms", 250),
        media_approval_reviewer=auto_approval_reviewer.review,
        local_proxy_bearer_token=config.get("local_proxy_bearer_token", ""),
    )
    amr_registry = AMRRegistry()
    quota_manager = QuotaManager(lambda: provider_registry.list_providers(include_secrets=True).get("providers", []))
    startup_manager = StartupManager()
    desktop_shortcut_manager = DesktopShortcutManager()
    update_manager = UpdateManager(current_version=APP_VERSION, repository_url=APP_REPOSITORY_URL)

    diagnostics_collector = DiagnosticsCollector(
        config=config,
        provider_registry=provider_registry,
        proxy_server=proxy_server,
        amr_registry=amr_registry,
        quota_manager=quota_manager,
    )
    codex_start_jobs: Dict[str, Dict[str, Any]] = {}
    codex_start_jobs_lock = threading.Lock()

    def _refresh_provider_registry_path():
        provider_registry.store_path = Path(config.get("provider_store_path", "") or DEFAULT_STORE_PATH).expanduser()

    def _current_official_provider_extra() -> list[Dict[str, Any]]:
        return _official_provider_extra()

    def _provider_payload(include_secrets: bool = False) -> Dict[str, Any]:
        return provider_registry.list_providers(
            include_secrets=include_secrets,
            extra_providers=_current_official_provider_extra(),
        )

    def _request_log_store() -> RequestLogStore:
        return RequestLogStore(
            config.get("request_log_path", ""),
            retention_days=config.get("request_log_retention_days", 30),
            max_mb=config.get("request_log_max_mb", 50),
        )

    def _startup_settings_from_body(body: Dict) -> Dict:
        settings = config.get_all()
        for key in STARTUP_CONFIG_KEYS:
            if key in body:
                settings[key] = body[key]
        return settings

    def _startup_config_update(settings: Dict) -> Dict:
        normalized = startup_manager.normalize_settings(settings)
        return {key: normalized.get(key, settings.get(key, "")) for key in STARTUP_CONFIG_KEYS}

    def _sync_proxy_request_log_config():
        proxy_server.request_log_path = config.get("request_log_path", "")
        proxy_server.request_log_retention_days = config.get("request_log_retention_days", 30)
        proxy_server.request_log_max_mb = config.get("request_log_max_mb", 50)
        proxy_server.currency_settings = config.get_all()
        proxy_server.upstream_timeout_seconds = config.get("proxy_upstream_timeout_seconds", 120)
        proxy_server.retry_attempts = config.get("proxy_retry_attempts", 0)
        proxy_server.retry_backoff_ms = config.get("proxy_retry_backoff_ms", 250)
        proxy_server.local_proxy_bearer_token = config.get("local_proxy_bearer_token", "")

    def _require_codex_mutation_confirmation(body: Dict, action: str):
        """Require a typed confirmation for endpoints that mutate Codex state."""
        if has_codex_mutation_confirmation(body):
            return None
        return jsonify(codex_mutation_error_payload(action)), 409

    def _current_proxy_base_url() -> str:
        status = proxy_server.status()
        if status.get("base_url"):
            return status["base_url"]
        port = status.get("port") or config.get("proxy_port", DEFAULT_PROXY_PORT)
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            port_int = DEFAULT_PROXY_PORT
        if port_int < 1 or port_int > 65535:
            port_int = DEFAULT_PROXY_PORT
        return f"http://127.0.0.1:{port_int}/v1"

    def _current_backend_url() -> str:
        if has_request_context():
            return str(request.host_url or "").rstrip("/") or backend_url_from_env()
        return backend_url_from_env()

    def _sync_codex_proxy_provider_config(
        proxy_base_url: str = "",
        proxy_model: str = "auto",
        preserve_official_auth: Any = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        if os.environ.get("CODEX_ENHANCE_MANAGER_DISABLE_CODEX_AUTOWRITE") == "1":
            return {"success": True, "changed": False, "skipped": True, "reason": "disabled_by_env"}
        if (
            (os.environ.get("PYTEST_CURRENT_TEST") or app.config.get("TESTING"))
            and os.environ.get("CODEX_ENHANCE_MANAGER_ALLOW_PYTEST_CODEX_AUTOWRITE") != "1"
        ):
            return {"success": True, "changed": False, "skipped": True, "reason": "pytest_codex_autowrite_skip"}
        mgr = CodexConfigManager()
        login_defaults = _official_login_defaults(mgr)
        preserve_auth = (
            login_defaults["default_preserve_official_auth"]
            if preserve_official_auth is None
            else _coerce_bool(preserve_official_auth, login_defaults["default_preserve_official_auth"])
        )
        base_url = str(proxy_base_url or _current_proxy_base_url())
        preview = mgr.preview_write_provider(
            proxy_base_url=base_url,
            proxy_model=proxy_model or "auto",
            goals_enabled=_config_goals_enabled(config),
            local_proxy_bearer_token=config.get("local_proxy_bearer_token", ""),
        )
        if not preview.get("will_write_config") and not preview.get("will_write_auth"):
            return {
                "success": True,
                "changed": False,
                "skipped": True,
                "message": "Codex 本地代理配置已是最新。",
                "proxy_base_url": base_url,
                "reason": reason,
            }
        result = mgr.write_provider_config(
            proxy_base_url=base_url,
            proxy_model=proxy_model or "auto",
            preserve_official_auth=preserve_auth,
            goals_enabled=_config_goals_enabled(config),
            local_proxy_bearer_token=config.get("local_proxy_bearer_token", ""),
        )
        result["changed"] = bool(result.get("success"))
        result["proxy_base_url"] = base_url
        result["reason"] = reason
        result.update(login_defaults)
        return result

    def _ensure_local_proxy_started() -> Dict[str, Any]:
        _sync_proxy_request_log_config()
        status = proxy_server.status()
        if status.get("running"):
            return {"success": True, "status": status, "started": False}
        ok = proxy_server.start()
        status = proxy_server.status()
        if ok:
            config.set("proxy_port", status.get("port", proxy_server.port))
            return {"success": True, "status": status, "started": True}
        return {
            "success": False,
            "status": status,
            "error": "未能在配置端口及后续端口中找到可用代理端口",
        }

    def _focused_or_enabled_provider_needs_proxy() -> bool:
        try:
            payload = _provider_payload(include_secrets=False)
        except Exception:
            return False
        providers = [p for p in payload.get("providers", []) if isinstance(p, dict)]
        focus_provider_id = str(payload.get("focus_provider_id") or "").strip()
        if focus_provider_id:
            focused = next((p for p in providers if str(p.get("id") or "") == focus_provider_id), None)
            return bool(focused and provider_allows_local_routing(focused))
        return any(provider_allows_local_routing(p) for p in providers)

    def _ensure_proxy_for_current_provider(reason: str = "startup", sync_codex_config: bool = False) -> Dict[str, Any]:
        if not _focused_or_enabled_provider_needs_proxy():
            return {
                "success": True,
                "started": False,
                "skipped": True,
                "reason": "official_or_no_local_provider",
                "status": proxy_server.status(),
            }
        result = _ensure_local_proxy_started()
        result["reason"] = reason
        status = result.get("status") if isinstance(result.get("status"), dict) else {}
        if result.get("success") and sync_codex_config and status.get("base_url"):
            result["provider_config"] = _sync_codex_proxy_provider_config(
                proxy_base_url=str(status.get("base_url") or ""),
                reason=reason,
            )
        return result

    try:
        auto_proxy = _ensure_proxy_for_current_provider("app_start", sync_codex_config=False)
        if not auto_proxy.get("success"):
            diagnostics_collector.record_error("proxy.auto_start", auto_proxy.get("error") or "Local proxy auto-start failed")
    except Exception as exc:
        diagnostics_collector.record_error("proxy.auto_start", str(exc))

    def _sync_paths_from_config() -> Dict[str, str]:
        return {
            "db_path": config.get("db_path", ""),
            "sessions_dir": config.get("sessions_dir", ""),
            "archived_dir": config.get("archived_dir", ""),
        }

    def _history_sync_signature(
        path_config: Dict[str, str],
        target_provider: str,
        target_model: str,
        start_mode: str,
    ) -> tuple[str, Dict[str, Any]]:
        def normalize_path(value: Any) -> str:
            raw = str(value or "").strip()
            if not raw:
                return ""
            try:
                return str(Path(os.path.expandvars(raw)).expanduser())
            except Exception:
                return raw

        try:
            codex_home = str(resolve_codex_home())
        except Exception:
            codex_home = ""
        payload = {
            "version": 1,
            "start_mode": start_mode,
            "target_provider": str(target_provider or ""),
            "target_model": str(target_model or ""),
            "codex_home": codex_home,
            "paths": {
                "db_path": normalize_path(path_config.get("db_path")),
                "sessions_dir": normalize_path(path_config.get("sessions_dir")),
                "archived_dir": normalize_path(path_config.get("archived_dir")),
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), payload

    def _history_sync_skip_payload(
        signature: str,
        signature_payload: Dict[str, Any],
        *,
        reason: str = "history_sync_signature_unchanged",
        message: str = "历史同步目标未变化，已跳过全量扫描。",
    ) -> Dict[str, Any]:
        return {
            "success": True,
            "skipped": True,
            "reason": reason,
            "message": message,
            "backup_path": "",
            "skipped_backup": True,
            "backup_before_sync": False,
            "signature": signature,
            "signature_payload": signature_payload,
        }

    def _history_provider_family(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if normalized in {"openai", "official", "chatgpt", "official_oauth", "openai_official"}:
            return "official"
        if normalized:
            return "third_party"
        return ""

    def _history_sync_skip_decision(target_provider: str) -> Dict[str, Any]:
        target_family = _history_provider_family(target_provider)
        decision = {
            "skip": False,
            "reason": "",
            "message": "",
            "target_family": target_family,
            "current_family": "",
            "distribution": [],
        }
        if not target_family:
            return decision
        try:
            distribution = db.get_provider_distribution()
        except Exception as exc:
            diagnostics_collector.record_error("history_sync.provider_distribution", str(exc))
            return decision
        if not distribution:
            decision.update({
                "skip": True,
                "reason": "history_sync_no_history",
                "message": "没有检测到可迁移的历史记录，已跳过历史迁移。",
                "current_family": "empty",
            })
            return decision
        families = set()
        for row in distribution:
            provider = str((row or {}).get("provider") or "").strip()
            family = _history_provider_family(provider)
            count = int((row or {}).get("count") or 0)
            decision["distribution"].append({
                "provider": provider,
                "count": count,
                "family": family or "unknown",
            })
            if family:
                families.add(family)
            else:
                families.add("unknown")
        if len(families) != 1:
            decision["current_family"] = "mixed" if families else "empty"
            return decision
        current_family = next(iter(families))
        decision["current_family"] = current_family
        if current_family == target_family:
            decision.update({
                "skip": True,
                "reason": "history_sync_same_provider_family",
                "message": "当前历史记录与启动目标同属官方或第三方，已跳过历史迁移。",
            })
        return decision

    def _persist_history_sync_signature(
        signature: str,
        target_provider: str,
        target_model: str,
        sync_payload: Dict[str, Any],
    ) -> None:
        try:
            config.update({
                "history_sync_signature": signature,
                "history_sync_last_at": _codex_start_now(),
                "history_sync_last_result": {
                    "target_provider": target_provider,
                    "target_model": target_model,
                    "changed": bool(sync_payload.get("changed")),
                    "skipped": bool(sync_payload.get("skipped")),
                    "reason": sync_payload.get("reason", ""),
                    "db_threads_seen": sync_payload.get("db_threads_seen", 0),
                    "db_threads_updated": sync_payload.get("db_threads_updated", 0),
                    "rollout_files_seen": sync_payload.get("rollout_files_seen", 0),
                    "rollout_files_updated": sync_payload.get("rollout_files_updated", 0),
                    "index_rows_seen": sync_payload.get("index_rows_seen", 0),
                    "index_rows_updated": sync_payload.get("index_rows_updated", 0),
                    "malformed_lines": sync_payload.get("malformed_lines", 0),
                },
            })
        except Exception as exc:
            diagnostics_collector.record_error("history_sync.signature_persist", str(exc))

    def _official_history_sync_target(mgr: CodexConfigManager) -> tuple[str, str, Dict[str, Any]]:
        try:
            effective = _effective_codex_settings(mgr.read_config(), mgr.read_auth())
        except Exception as exc:
            diagnostics_collector.record_error("history_sync.official_target", str(exc))
            effective = {"model_provider": "openai", "model": "gpt-5"}
        target_provider = str(effective.get("model_provider") or "openai")
        if target_provider == "codex_enhance_manager":
            target_provider = "openai"
        target_model = str(effective.get("model") or "gpt-5")
        return target_provider, target_model, effective

    def _run_conditional_history_sync(
        job_id: str,
        body: Dict[str, Any],
        start_mode: str,
        target_provider: str,
        target_model: str,
        *,
        progress: int,
        changed_message: str,
        skipped_message: str = "历史同步目标未变化，已跳过全量扫描。",
    ) -> tuple[Dict[str, Any], int]:
        backup_before = _coerce_bool(body.get("backup_before_sync"), False)
        force_history_sync = (
            _coerce_bool(body.get("force_history_sync"), False)
            or _coerce_bool(body.get("force_sync"), False)
        )
        path_config = _sync_paths_from_config()
        sync_signature, sync_signature_payload = _history_sync_signature(
            path_config,
            target_provider=target_provider,
            target_model=target_model,
            start_mode=start_mode,
        )
        previous_signature = str(config.get("history_sync_signature", "") or "")
        if not force_history_sync and not backup_before and previous_signature == sync_signature:
            sync_payload = _history_sync_skip_payload(sync_signature, sync_signature_payload)
            sync_payload["target_provider"] = target_provider
            sync_payload["target_model"] = target_model
            _set_codex_start_progress(
                job_id,
                "history_sync_skipped",
                68,
                skipped_message,
                sync=sync_payload,
            )
            return sync_payload, 200

        skip_decision = _history_sync_skip_decision(target_provider)
        if not force_history_sync and not backup_before and skip_decision.get("skip"):
            sync_payload = _history_sync_skip_payload(
                sync_signature,
                sync_signature_payload,
                reason=skip_decision.get("reason") or "history_sync_same_provider_family",
                message=skip_decision.get("message") or "当前历史记录与启动目标同类，已跳过历史迁移。",
            )
            sync_payload["target_provider"] = target_provider
            sync_payload["target_model"] = target_model
            sync_payload["target_family"] = skip_decision.get("target_family", "")
            sync_payload["current_family"] = skip_decision.get("current_family", "")
            sync_payload["provider_distribution"] = skip_decision.get("distribution", [])
            _persist_history_sync_signature(sync_signature, target_provider, target_model, sync_payload)
            _set_codex_start_progress(
                job_id,
                "history_sync_skipped",
                68,
                sync_payload["message"],
                sync=sync_payload,
            )
            return sync_payload, 200

        _set_codex_start_progress(job_id, "history_sync", progress, changed_message)
        heartbeat_stop = threading.Event()
        if job_id:
            interval = 0.15 if app.config.get("TESTING") else 3.0
            max_progress = min(66, max(progress + 1, progress + 24))

            def heartbeat() -> None:
                started_at = time.time()
                tick = 0
                while not heartbeat_stop.wait(interval):
                    tick += 1
                    elapsed = int(time.time() - started_at)
                    progress_value = min(max_progress, progress + tick)
                    _set_codex_start_progress(
                        job_id,
                        "history_sync",
                        progress_value,
                        f"{changed_message} 已用时 {elapsed} 秒，仍在扫描历史记录...",
                    )

            threading.Thread(target=heartbeat, daemon=True).start()
        try:
            sync_payload, sync_status = _run_sync_with_backup(
                backup_mgr,
                path_config=path_config,
                target_provider=target_provider,
                target_model=target_model,
                backup_before=backup_before,
            )
        finally:
            heartbeat_stop.set()
        if sync_status >= 400:
            return sync_payload, sync_status

        sync_payload["signature"] = sync_signature
        sync_payload["signature_payload"] = sync_signature_payload
        sync_payload["target_provider"] = target_provider
        sync_payload["target_model"] = target_model
        _persist_history_sync_signature(sync_signature, target_provider, target_model, sync_payload)
        _set_codex_start_progress(job_id, "history_synced", 68, "历史同步完成，正在准备启动 Codex...", sync=sync_payload)
        return sync_payload, sync_status

    def _move_repair_manager() -> MoveRepairManager:
        return MoveRepairManager(
            db_path=config.get("db_path", ""),
            sessions_dir=config.get("sessions_dir", ""),
            archived_dir=config.get("archived_dir", ""),
        )

    def _codex_start_now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _set_codex_start_job(job_id: str, **fields) -> None:
        if not job_id:
            return
        with codex_start_jobs_lock:
            job = codex_start_jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            job["updated_at"] = _codex_start_now()
            job["_updated_ts"] = time.time()

    def _codex_start_job_snapshot(job_id: str) -> Dict[str, Any] | None:
        with codex_start_jobs_lock:
            job = codex_start_jobs.get(job_id)
            if not job:
                return None
            return {key: value for key, value in job.items() if not key.startswith("_")}

    def _cleanup_codex_start_jobs() -> None:
        cutoff = time.time() - 1800
        with codex_start_jobs_lock:
            for job_id in [
                item_id for item_id, job in codex_start_jobs.items()
                if float(job.get("_updated_ts") or job.get("_created_ts") or 0) < cutoff
            ]:
                codex_start_jobs.pop(job_id, None)

    def _set_codex_start_progress(job_id: str, stage: str, progress: int, message: str, **extra) -> None:
        if job_id:
            try:
                print(f"codex_start_job {job_id[:8]} stage={stage} progress={int(progress)} message={message}", flush=True)
            except Exception:
                pass
        _set_codex_start_job(
            job_id,
            stage=stage,
            progress=min(max(int(progress), 0), 100),
            message=message,
            **extra,
        )

    def _start_codex_with_timeout(kwargs: Dict[str, Any], timeout_seconds: float = 30.0) -> tuple[bool, str]:
        result: Dict[str, Any] = {}

        def worker() -> None:
            try:
                ok, message = start_codex(**kwargs)
                result["value"] = (ok, message)
            except Exception as exc:
                result["value"] = (False, f"启动 Codex 失败: {exc}")

        thread = threading.Thread(target=worker, name="codex-launch-call", daemon=True)
        thread.start()
        thread.join(max(float(timeout_seconds or 30.0), 1.0))
        if thread.is_alive():
            return False, "启动 Codex 超时。请检查 config.toml 是否含有 Codex 不支持字段，或先手动关闭残留 Codex 进程后重试。"
        return result.get("value") or (False, "启动 Codex 未返回结果")

    def _run_codex_start_flow(body: Dict[str, Any], job_id: str = "") -> Dict[str, Any]:
        body = dict(body or {})
        _set_codex_start_progress(job_id, "preparing", 5, "正在读取 Codex 登录态和启动配置...")
        mgr = CodexConfigManager()
        login_defaults = _official_login_defaults(mgr)
        try:
            provider_payload_for_start = _provider_payload(include_secrets=False)
        except Exception as exc:
            provider_payload_for_start = {}
            diagnostics_collector.record_error("codex.start_provider_focus", str(exc))
        start_mode = _normalize_codex_start_mode(body, login_defaults, provider_payload_for_start)
        official_mode = start_mode == START_MODE_OFFICIAL_DIRECT
        sandbox_repair_payload = {"success": True, "skipped": True, "reason": "disabled"}
        restart_payload = {"success": True, "skipped": True, "reason": "not_required"}
        if _coerce_bool(config.get("codex_sandbox_auto_repair_enabled", False), False):
            _set_codex_start_progress(job_id, "sandbox_repair", 8, "正在修复 Codex sandbox/approval 配置...")
            sandbox_repair_payload = repair_codex_sandbox_permissions(mgr)
            if not sandbox_repair_payload.get("success"):
                result = {
                    "success": False,
                    "error": sandbox_repair_payload.get("error") or "Codex sandbox/approval repair failed",
                    "sandbox_repair": sandbox_repair_payload,
                }
                _set_codex_start_progress(job_id, "sandbox_repair_failed", 100, result["error"], result=result)
                return result
        preserve_official_auth = bool(body.get(
            "preserve_official_auth",
            start_mode == START_MODE_PRESERVE_LOGIN_PROXY
            or login_defaults["default_preserve_official_auth"],
        ))
        official_payload = {}
        if official_mode:
            _set_codex_start_progress(job_id, "official_config", 25, "正在切回官方登录直连配置...")
            official_payload = disable_codex_enhance_provider_config(
                mgr,
                goals_enabled=_config_goals_enabled(config),
            )
            config.update({
                "use_codex_plus_plus": False,
                "plugin_unlock_enabled": False,
            })
            use_cpp = False
            target_provider, target_model, official_effective = _official_history_sync_target(mgr)
            sync_payload, sync_status = _run_conditional_history_sync(
                job_id,
                body,
                start_mode,
                target_provider=target_provider,
                target_model=target_model,
                progress=42,
                changed_message="正在按官方登录态检查聊天记录同步；目标未变化时会自动跳过...",
            )
            sync_payload["official_effective_settings"] = official_effective
            if sync_status >= 400:
                result = {"success": False, "error": "同步失败，取消启动", "sync": sync_payload}
                _set_codex_start_progress(job_id, "sync_failed", 100, "历史同步失败，已取消启动。", result=result, sync=sync_payload)
                return result
        else:
            use_cpp = body.get("use_codex_plus_plus", config.get("use_codex_plus_plus", False))
            _set_codex_start_progress(job_id, "proxy_start", 12, "正在启动本地代理并自动退避端口...")
            proxy_payload = _ensure_local_proxy_started()
            if not proxy_payload.get("success"):
                result = {
                    "success": False,
                    "error": proxy_payload.get("error") or "本地代理启动失败",
                    "proxy": proxy_payload,
                }
                _set_codex_start_progress(job_id, "proxy_failed", 100, result["error"], result=result)
                return result
            proxy_status = proxy_payload.get("status") or {}
            proxy_base_url = str(body.get("proxy_base_url") or proxy_status.get("base_url") or _current_proxy_base_url())
            _set_codex_start_progress(job_id, "provider_config", 16, "正在确认 Codex 本地代理配置...")
            provider_write = _sync_codex_proxy_provider_config(
                proxy_base_url=proxy_base_url,
                proxy_model=body.get("proxy_model", "auto"),
                preserve_official_auth=preserve_official_auth,
                reason="codex_start",
            )
            if not provider_write.get("success"):
                result = {
                    "success": False,
                    "error": "Codex 本地代理配置写入失败",
                    "provider_config": provider_write,
                    "proxy": proxy_payload,
                }
                _set_codex_start_progress(job_id, "provider_config_failed", 100, result["error"], result=result)
                return result
            target_provider = "codex_enhance_manager"
            target_model = str(body.get("proxy_model", "auto") or "auto")
            sync_payload, sync_status = _run_conditional_history_sync(
                job_id,
                body,
                start_mode,
                target_provider=target_provider,
                target_model=target_model,
                progress=20,
                changed_message="正在同步历史记录；大历史可能需要几分钟，默认不做完整备份...",
            )
            if sync_status >= 400:
                result = {"success": False, "error": "同步失败，取消启动", "sync": sync_payload}
                _set_codex_start_progress(job_id, "sync_failed", 100, "历史同步失败，已取消启动。", result=result, sync=sync_payload)
                return result

        injection_settings = _resolve_codex_injection_settings(
            config,
            body,
            use_codex_plus_plus=bool(use_cpp),
            official_mode=official_mode,
            persist=True,
            avoid_occupied_port=True,
        )
        _set_codex_start_progress(job_id, "prelaunch", 74, "正在确认 Codex 进程和增强注入端口...")
        if injection_settings["enabled"]:
            try:
                running, pids = is_codex_running(timeout=1)
            except Exception:
                running, pids = False, []
            if running:
                _set_codex_start_progress(
                    job_id,
                    "restarting_codex",
                    76,
                    "检测到 Codex 正在运行，正在重启以确保增强注入能够附加。",
                    pids=pids,
                )
                kill_ok, kill_msg = kill_codex(timeout=8)
                restart_payload = {
                    "success": bool(kill_ok),
                    "skipped": False,
                    "pids": pids,
                    "message": kill_msg,
                }
                if not kill_ok:
                    result = {
                        "success": False,
                        "error": "Codex 正在运行，但自动重启失败；增强注入无法保证生效。",
                        "restart": restart_payload,
                    }
                    _set_codex_start_progress(job_id, "restart_failed", 100, result["error"], result=result)
                    return result
        _set_codex_start_progress(job_id, "launching", 82, "正在启动 Codex 并确认进程...")
        ok, msg = _start_codex_with_timeout({
            "use_codex_plus_plus": use_cpp,
            "codex_plus_plus_path": config.get("codex_plus_plus_path", ""),
            "codex_cli_path": config.get("codex_cli_path", ""),
            "enable_cdp_injection": injection_settings["enabled"],
            "cdp_port": injection_settings["cdp_port"],
            "backend_url": body.get("_backend_url") or body.get("backend_url") or _current_backend_url(),
        })
        if ok:
            try:
                config.set("codex_last_start_mode", start_mode)
            except Exception as exc:
                diagnostics_collector.record_error("codex.start_mode_persist", str(exc))
        result = {
            "success": ok,
            "message": msg,
            "sync": sync_payload,
            "start_mode": start_mode,
            "official_mode": official_mode,
            "preserve_official_auth": preserve_official_auth,
            **login_defaults,
            "official_mode_changes": official_payload,
            "proxy": proxy_payload if not official_mode else {},
            "provider_config": provider_write if not official_mode else {},
            "sandbox_repair": sandbox_repair_payload,
            "restart": restart_payload,
            "plugin_unlock_enabled": bool(config.get("plugin_unlock_enabled", False)),
            "codex_injection_enabled": injection_settings["requested_enabled"],
            "codex_cdp_port": injection_settings["cdp_port"],
            "codex_injection_active": injection_settings["enabled"],
            "codex_goals_enabled": _config_goals_enabled(config),
        }
        _set_codex_start_progress(job_id, "done" if ok else "failed", 100, msg, result=result, sync=sync_payload)
        return result

    def _start_codex_background_job(body: Dict[str, Any]) -> Dict[str, Any]:
        _cleanup_codex_start_jobs()
        job_id = uuid.uuid4().hex
        now = _codex_start_now()
        with codex_start_jobs_lock:
            codex_start_jobs[job_id] = {
                "id": job_id,
                "status": "running",
                "stage": "queued",
                "progress": 0,
                "message": "启动任务已开始，正在排队...",
                "created_at": now,
                "updated_at": now,
                "_created_ts": time.time(),
                "_updated_ts": time.time(),
            }

        def worker():
            try:
                result = _run_codex_start_flow(body, job_id)
                _set_codex_start_job(
                    job_id,
                    status="complete" if result.get("success") else "failed",
                    result=result,
                    error="" if result.get("success") else (result.get("error") or result.get("message") or "Codex 启动失败"),
                )
            except Exception as exc:
                _set_codex_start_job(
                    job_id,
                    status="failed",
                    stage="failed",
                    progress=100,
                    message=str(exc),
                    error=str(exc),
                    result={"success": False, "error": str(exc)},
                )

        threading.Thread(target=worker, name=f"codex-start-{job_id[:8]}", daemon=True).start()
        return {
            "success": True,
            "async": True,
            "job_id": job_id,
            "status_url": f"/api/codex/start/status/{job_id}",
            "message": "启动任务已开始，正在同步历史记录...",
        }

    @app.before_request
    def _block_writes_after_uninstall_cleanup():
        """After uninstall cleanup, keep the current process read-only."""
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if request.path == "/api/uninstall-cleanup/execute":
            return None
        if config.is_write_locked():
            return jsonify({
                "error": "Local writes are locked until restart.",
                "write_locked": True,
                "reason": config.write_lock_reason(),
            }), 423
        return None

    @app.after_request
    def _allow_injected_codex_menu(response):
        if request.path.startswith("/api/codex-injection/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    # 启动自动备份
    if config.get("auto_backup") and os.environ.get("CODEX_ENHANCE_MANAGER_SMOKE_TEST") != "1":
        backup_mgr.start_auto_backup()

    # 尝试连接数据库
    try:
        db.connect()
    except Exception:
        pass

    # ─────────────── 页面路由 ───────────────

    @app.route("/")
    def index():
        """返回 SPA 主页面"""
        return send_from_directory(str(_static_dir()), "index.html")

    @app.route("/app-icon.png")
    def app_icon_png():
        """返回应用内品牌图标。"""
        return send_from_directory(str(_asset_dir()), "icon.png")

    @app.route("/favicon.ico")
    def favicon():
        """返回窗口和浏览器使用的图标。"""
        return send_from_directory(str(_asset_dir()), "icon.ico")

    @app.route("/monitor")
    def monitor():
        """返回桌面 Token 悬浮监控窗页面"""
        return send_from_directory(str(_static_dir()), "monitor.html")

    # ─────────────── 会话 API ───────────────

    @app.route("/api/sessions")
    def list_sessions():
        """获取会话列表（支持搜索、分页、排序）"""
        try:
            page = _clamp_int(request.args.get("page", 0), 0, 0, 100000)
            page_size = _clamp_int(request.args.get("page_size", config.get("page_size", 50)), 50, 10, 200)
            search = request.args.get("search", "")
            filter_mode = request.args.get("filter", "all")
            source = request.args.get("source", "all")
            sort_by = request.args.get("sort_by", "created_at_ms")
            sort_order = request.args.get("sort_order", "desc")
            model_filter = request.args.get("model", "")
            provider_filter = request.args.get("provider", "")

            rows, total = db.list_threads(
                filter_mode=filter_mode,
                search=search,
                page=page,
                page_size=page_size,
                source_filter=source,
                sort_by=sort_by,
                sort_order=sort_order,
                model_filter=model_filter,
                provider_filter=provider_filter,
            )
            return jsonify({"sessions": rows, "total": total, "page": page, "page_size": page_size})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>")
    def get_session(session_id):
        """获取会话详情（含消息内容）"""
        try:
            thread = db.get_thread(session_id)
            if not thread:
                return jsonify({"error": "会话不存在"}), 404

            rollout_path = thread.get("rollout_path") or ""

            # 如果没有 rollout_path，尝试从 sessions 目录推断
            if not rollout_path or not os.path.exists(rollout_path):
                rollout_path = _find_file_for_thread(thread, config)

            if rollout_path and os.path.exists(rollout_path):
                max_msgs = config.get("max_lines_large_file", 2000)
                large_thresh = config.get("large_file_threshold_mb", 500)
                data = read_messages(rollout_path, max_messages=max_msgs, large_file_limit=large_thresh)
                thread["rollout_path"] = rollout_path
                thread["jsonl_path"] = rollout_path
                thread["messages"] = data.get("messages", [])
                thread["message_count"] = len(data.get("messages", []))
                thread["is_large_file"] = data.get("is_large_file", False)
                thread["truncated"] = data.get("truncated", False)
                thread["file_size_mb"] = data.get("file_size_mb", 0)
                thread["file_error"] = data.get("error")
                thread["file_not_found"] = False
            else:
                thread["messages"] = []
                thread["message_count"] = 0
                thread["file_not_found"] = True

            return jsonify(thread)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/archive", methods=["POST"])
    def archive_session(session_id):
        """归档会话"""
        try:
            ok = db.set_archived(session_id, 1)
            if ok:
                return jsonify({"success": True, "message": "已归档"})
            return jsonify({"error": "操作失败"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/unarchive", methods=["POST"])
    def unarchive_session(session_id):
        """取消归档"""
        try:
            ok = db.set_archived(session_id, 0)
            if ok:
                return jsonify({"success": True, "message": "已取消归档"})
            return jsonify({"error": "操作失败"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sessions/<session_id>/export/<fmt>")
    def export_session(session_id, fmt):
        """导出会话为 md/json/txt"""
        try:
            thread = db.get_thread(session_id)
            if not thread:
                return jsonify({"error": "会话不存在"}), 404

            rollout_path = thread.get("rollout_path") or ""
            if not rollout_path or not os.path.exists(rollout_path):
                rollout_path = _find_file_for_thread(thread, config)

            if not rollout_path or not os.path.exists(rollout_path):
                return jsonify({"error": "找不到 jsonl 文件"}), 404

            title = thread.get("title") or "会话"
            safe_title = _safe_export_filename(title)

            if fmt == "md":
                content = export_to_markdown(rollout_path, title=title)
                return jsonify({"content": content, "filename": f"{safe_title}.md", "format": "markdown"})
            elif fmt == "txt":
                content = export_to_text(rollout_path, title=title)
                return jsonify({"content": content, "filename": f"{safe_title}.txt", "format": "text"})
            elif fmt == "json":
                data = read_messages(rollout_path, max_messages=99999, large_file_limit=99999)
                output = {"thread": thread, "messages": data["messages"]}
                return jsonify({"content": json.dumps(output, ensure_ascii=False, indent=2), "filename": f"{safe_title}.json", "format": "json"})
            else:
                return jsonify({"error": f"不支持的格式: {fmt}"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Token 统计 API ───────────────

    @app.route("/api/token/current")
    def get_current_tokens():
        """获取轻量当前 Token 统计（用量追踪/实时面板轮询使用）。"""
        try:
            token_stats.db_path = config.get("db_path")
            start = request.args.get("start", "")
            end = request.args.get("end", "")
            granularity = request.args.get("granularity", "total")
            auth_mode = "unknown"
            try:
                auth_mode = CodexConfigManager().get_auth_mode()
            except Exception:
                pass
            provider_payload: Dict[str, Any] = {}
            try:
                provider_payload = _provider_payload(include_secrets=False)
            except Exception:
                provider_payload = {}
            focus_provider_id, _focused_provider = _focused_provider_from_payload(provider_payload)
            official_focus_provider = _provider_focus_is_official_login(provider_payload)
            third_party_focus_provider = _provider_focus_uses_other_api(provider_payload)
            last_start_mode = str(config.get("codex_last_start_mode", "") or "")
            official_usage_default = _official_usage_visible_for_current_mode(
                auth_mode,
                provider_payload,
                last_start_mode=last_start_mode,
            )
            rollout_scan_fallback = str(
                request.args.get("rollout_scan_fallback", "1" if official_usage_default else "")
            ).lower() in {"1", "true", "yes"}
            rollout_total_source = str(
                request.args.get("rollout_total_source", "1" if official_usage_default else "")
            ).lower() in {"1", "true", "yes"}
            default_rollout_limit = 200 if rollout_total_source or rollout_scan_fallback else 25
            rollout_limit = _clamp_int(request.args.get("rollout_limit", default_rollout_limit), default_rollout_limit, 1, 1000)
            default_tail_bytes = 0 if rollout_total_source else 262_144
            rollout_tail_bytes = _clamp_int(request.args.get("rollout_tail_bytes", default_tail_bytes), default_tail_bytes, 0, 8_388_608)
            data = token_stats.get_current_stats(
                start=start,
                end=end,
                granularity=granularity,
            )
            rollout_cache_data = get_codex_rollout_cache_stats(
                db_path=config.get("db_path", ""),
                sessions_dir=config.get("sessions_dir", "") if rollout_scan_fallback else "",
                start=start,
                end=end,
                limit=rollout_limit,
                tail_bytes=rollout_tail_bytes,
            )
            data["codex_rollout_scan_fallback"] = rollout_scan_fallback
            data["codex_rollout_scan_limit"] = rollout_limit
            data["codex_rollout_tail_bytes"] = rollout_tail_bytes
            data["codex_rollout_total_source_requested"] = rollout_total_source
            data["auth_mode"] = auth_mode
            data["official_usage_default"] = official_usage_default
            data["official_focus_provider"] = official_focus_provider
            data["official_usage_hidden_by_provider"] = bool(
                auth_mode == "official_oauth"
                and not official_usage_default
                and (third_party_focus_provider or last_start_mode in {START_MODE_PROXY_INJECTION, START_MODE_PRESERVE_LOGIN_PROXY})
            )
            data["focused_provider_id"] = focus_provider_id
            data["third_party_focus_provider"] = third_party_focus_provider
            data["codex_last_start_mode"] = last_start_mode
            cc_switch_db_path = config.get("cc_switch_db_path", "")
            data["cc_switch_db_configured"] = bool(cc_switch_db_path)
            data["cc_switch_db_path"] = cc_switch_db_path
            cc_cache_data = None
            if cc_switch_db_path:
                cc_cache_data = token_stats.get_cc_switch_cache_stats(
                    cc_switch_db_path=cc_switch_db_path,
                    start=start,
                    end=end,
                )
            else:
                data["cache_note"] = (
                    "未配置代理缓存数据库；缓存统计需要请求经过代理数据源，官方 API 和自定义 API 都可被统计。"
                )
            _merge_cache_usage_sources(data, rollout_cache_data, cc_cache_data, use_rollout_total=rollout_total_source)
            request_log_provider_filter = focus_provider_id if third_party_focus_provider else ""
            request_log_summary = _request_log_store().summary(provider_id=request_log_provider_filter)
            data["request_log_summary_scope"] = "focused_provider" if request_log_provider_filter else "all"
            _merge_local_proxy_request_log_usage(data, request_log_summary)
            _attach_usage_source_summary(data, proxy_server.status(), request_log_summary)
            data.update(_resolve_current_context_window(config, provider_registry))
            if data.get("codex_rollout_latest_context_window"):
                data["current_context_window"] = data["codex_rollout_latest_context_window"]
                data["current_context_used_tokens"] = data["codex_rollout_latest_context_used_tokens"]
                data["current_context_source"] = "codex_rollout"
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/overview")
    def stats_overview():
        """Token 总览"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_overview()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/by-model")
    def stats_by_model():
        """按模型分组统计"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_by_model()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/by-provider")
    def stats_by_provider():
        """按提供商分组统计"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_by_provider()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/daily-trend")
    def stats_daily_trend():
        """每日趋势"""
        try:
            days = _clamp_int(request.args.get("days", 30), 30, 1, 365)
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_daily_trend(days=days)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/top-sessions")
    def stats_top_sessions():
        """最耗 Token 会话排行"""
        try:
            limit = _clamp_int(request.args.get("limit", 20), 20, 1, 100)
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_top_sessions(limit=limit)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/stats/hourly")
    def stats_hourly():
        """每小时使用分布"""
        try:
            token_stats.db_path = config.get("db_path")
            data = token_stats.get_hourly_distribution()
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 同步 API ───────────────

    @app.route("/api/sync/preview", methods=["POST"])
    def sync_preview():
        """预览同步变更（Dry Run）"""
        try:
            body = request.get_json(silent=True) or {}
            target_provider = body.get("target_provider", "")
            target_model = body.get("target_model", "")

            stats = full_sync(
                target_provider=target_provider,
                target_model=target_model,
                dry_run=True,
                **_sync_paths_from_config(),
            )
            return jsonify(_sync_stats_to_dict(stats))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sync/execute", methods=["POST"])
    def sync_execute():
        """执行同步"""
        try:
            body = request.get_json(silent=True) or {}
            target_provider = body.get("target_provider", "")
            target_model = body.get("target_model", "")

            payload, status = _run_sync_with_backup(
                backup_mgr,
                path_config=_sync_paths_from_config(),
                target_provider=target_provider,
                target_model=target_model,
                backup_before=_coerce_bool(body.get("backup_before_sync"), False),
            )
            if status >= 400:
                return jsonify(payload), status

            # 同步后刷新数据库连接
            try:
                db.close()
                db.db_path = config.get("db_path")
                db.connect()
            except Exception:
                pass

            return jsonify(payload)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/sync/status")
    def sync_status():
        """获取同步状态（provider 分布等）"""
        try:
            codex_home = resolve_codex_home()
            config_data = _load_config_toml(str(codex_home / "config.toml"))
            auth_data = CodexConfigManager(codex_home=str(codex_home)).read_auth()
            effective = _effective_codex_settings(config_data, auth_data)
            provider_dist = db.get_provider_distribution()
            running, pids = is_codex_running(timeout=1)

            return jsonify({
                "current_provider": effective.get("model_provider", ""),
                "current_model": effective.get("model", ""),
                "current_provider_source": effective.get("model_provider_source", ""),
                "current_model_source": effective.get("model_source", ""),
                "raw_current_provider": effective.get("raw_model_provider", ""),
                "raw_current_model": effective.get("raw_model", ""),
                "auth_mode": effective.get("auth_mode", ""),
                "official_oauth_implied_provider": effective.get("official_oauth_implied_provider", False),
                "provider_distribution": provider_dist,
                "codex_running": running,
                "codex_pids": pids,
                "use_codex_plus_plus": config.get("use_codex_plus_plus", False),
                "codex_plus_plus_path": config.get("codex_plus_plus_path", CODEX_PLUS_PLUS_PATH),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/status")
    def codex_status():
        """获取 Codex 进程状态"""
        try:
            running, pids = is_codex_running(timeout=1)
            return jsonify({"running": running, "pids": pids})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/kill", methods=["POST"])
    def codex_kill():
        """关闭 Codex 进程"""
        try:
            ok, msg = kill_codex()
            return jsonify({"success": ok, "message": msg})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/start", methods=["POST"])
    def codex_start():
        """启动 Codex/Codex++（启动前自动同步当前 provider/model）。"""
        try:
            body = request.get_json(silent=True) or {}
            body["_backend_url"] = _current_backend_url()
            if _coerce_bool(body.get("async"), False):
                return jsonify(_start_codex_background_job(body))
            return jsonify(_run_codex_start_flow(body))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex/start/status/<job_id>")
    def codex_start_status(job_id):
        """Return progress for a background Codex start task."""
        try:
            job = _codex_start_job_snapshot(job_id)
            if not job:
                return jsonify({"error": "Start job not found"}), 404
            return jsonify(job)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 备份 API ───────────────

    @app.route("/api/backups")
    def list_backups():
        """获取备份列表"""
        try:
            backups = backup_mgr.list_backups()
            return jsonify(backups)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/create", methods=["POST"])
    def create_backup():
        """创建完整备份"""
        try:
            result = backup_mgr.do_full_backup(label="manual")
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/<path:backup_id>/restore", methods=["POST"])
    def restore_backup(backup_id):
        """还原备份"""
        try:
            backup_path = _resolve_backup_path(backup_id, backup_mgr.get_backup_dir())
            if not backup_path or not os.path.exists(backup_path):
                return jsonify({"error": "备份文件不存在"}), 404
            result = backup_mgr.restore_backup(str(backup_path))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/incremental", methods=["POST"])
    def create_incremental_backup():
        """创建增量备份"""
        try:
            result = backup_mgr.do_incremental_backup()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/backups/prune", methods=["POST"])
    def prune_backups():
        """清理超出保留数量的旧备份。"""
        try:
            body = request.get_json(silent=True) or {}
            keep = body.get("max_backups")
            result = backup_mgr.prune_backups(keep if keep is not None else config.get("max_backups", 20))
            return jsonify(result), 200 if result.get("success") else 207
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 设置 API ───────────────

    @app.route("/api/settings")
    def get_settings():
        """读取设置"""
        try:
            settings = _settings_response_payload(config.get_all(), config)
            try:
                mgr = CodexConfigManager()
                config_data = mgr.read_config()
                current_goals = codex_goals_enabled_from_config(config_data, True)
                settings["codex_goals_config"] = {
                    "enabled": current_goals,
                    "configured": isinstance(config_data.get("features"), dict)
                    and "goals" in config_data.get("features", {}),
                    "matches_setting": current_goals == _config_goals_enabled(config),
                    "config_path": str(mgr.config_path),
                }
            except Exception as exc:
                settings["codex_goals_config"] = {
                    "enabled": True,
                    "configured": False,
                    "matches_setting": False,
                    "error": str(exc),
                }
            settings["auto_approval_system_prompt_default"] = DEFAULT_CONFIG["auto_approval_system_prompt"]
            settings["defaults"] = _settings_response_payload(DEFAULT_CONFIG)
            settings["app_version"] = APP_VERSION
            settings["repository_url"] = APP_REPOSITORY_URL
            return jsonify(settings)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        """保存设置"""
        try:
            data = request.get_json(silent=True) or {}
            data = preserve_redacted_currency_secret(data, config.get_all())
            data = preserve_redacted_local_proxy_token(data, config.get_all())
            data = _drop_secret_reveal_response_fields(data)
            config.update(data)
            _sync_proxy_request_log_config()
            goals_sync = None
            if "codex_goals_enabled" in data:
                goals_sync = {
                    "success": True,
                    "skipped": True,
                    "reason": "deferred_until_codex_start",
                    "message": "Codex goals setting saved locally and will be written when Codex is started from this app.",
                }

            # 重新连接数据库（路径可能变了）
            if "db_path" in data:
                try:
                    db.close()
                    db.db_path = data["db_path"]
                    db.connect()
                    token_stats.db_path = data["db_path"]
                except Exception as e:
                    return jsonify({"success": True, "warning": f"数据库重连失败: {e}"})

            # 自动备份开关
            if "auto_backup" in data:
                if data["auto_backup"]:
                    backup_mgr.start_auto_backup()
                else:
                    backup_mgr.stop_auto_backup()

            payload = {"success": True}
            if goals_sync is not None:
                payload["codex_goals_sync"] = goals_sync
                if not goals_sync.get("success"):
                    payload["warning"] = "Codex goals setting saved locally, but config.toml sync failed."
            return jsonify(payload)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/reset", methods=["POST"])
    def reset_settings():
        """重置为默认"""
        try:
            config.reset_defaults()
            _sync_proxy_request_log_config()
            # 重连数据库
            try:
                db.close()
                db.db_path = config.get("db_path")
                db.connect()
                token_stats.db_path = config.get("db_path")
            except Exception:
                pass
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/secret-reveal-password", methods=["POST"])
    def update_secret_reveal_password():
        """Set or clear the optional local password used before revealing provider secrets."""
        try:
            body = request.get_json(silent=True) or {}
            configured = _secret_reveal_password_configured(config)
            if configured and not _verify_secret_reveal_password(config, str(body.get("current_password") or "")):
                return jsonify({"error": "Secondary password is incorrect.", "password_required": True}), 403

            if bool(body.get("clear")):
                config.update({
                    "secret_reveal_password_hash": "",
                    "secret_reveal_password_salt": "",
                    "secret_reveal_password_iterations": DEFAULT_CONFIG["secret_reveal_password_iterations"],
                })
                return jsonify({"success": True, "configured": False})

            password = str(body.get("password") or "")
            if not password:
                return jsonify({"error": "Secondary password is optional; leave it unset or enter a password."}), 400
            if len(password) < 4:
                return jsonify({"error": "Secondary password must be at least 4 characters."}), 400

            config.update(_hash_secret_reveal_password(
                password,
                iterations=DEFAULT_CONFIG["secret_reveal_password_iterations"],
            ))
            return jsonify({"success": True, "configured": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/storage")
    def settings_storage():
        """返回配置、临时文件和导出目录位置。"""
        try:
            ensure_app_dirs()
            settings = config.get_all()
            return jsonify({
                "app_data_dir": str(app_data_dir()),
                "config_file": str(CONFIG_FILE),
                "legacy_config_file": str(LEGACY_CONFIG_FILE),
                "legacy_config_exists": LEGACY_CONFIG_FILE.exists(),
                "backup_dir": settings.get("backup_dir", ""),
                "provider_store_path": settings.get("provider_store_path", ""),
                "temp_dir": settings.get("temp_dir", ""),
                "diagnostics_dir": settings.get("diagnostics_dir", ""),
                "exports_dir": settings.get("exports_dir", ""),
                "request_log_path": settings.get("request_log_path", ""),
                "request_log_retention_days": settings.get("request_log_retention_days", 30),
                "request_log_max_mb": settings.get("request_log_max_mb", 50),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/export")
    def export_settings():
        """导出当前本地配置。"""
        try:
            return jsonify({
                "schema": "codex_enhance_manager.settings.v1",
                "exported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "settings": _settings_response_payload(config.get_all(), config),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/import", methods=["POST"])
    def import_settings():
        """导入本地配置 JSON。不会写 Codex auth/config。"""
        try:
            body = request.get_json(silent=True) or {}
            imported = body.get("settings") if isinstance(body.get("settings"), dict) else body
            if not isinstance(imported, dict):
                return jsonify({"error": "Invalid settings payload"}), 400
            imported = preserve_redacted_currency_secret(imported, config.get_all())
            imported = preserve_redacted_local_proxy_token(imported, config.get_all())
            imported = _drop_secret_reveal_response_fields(imported)
            config.update(imported)
            ensure_app_dirs()
            _sync_proxy_request_log_config()
            return jsonify({"success": True, "settings": _settings_response_payload(config.get_all(), config)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/updates/check")
    def updates_check():
        """Check GitHub Releases for a newer packaged EXE."""
        try:
            include_prerelease = request.args.get("include_prerelease")
            if include_prerelease is None:
                include = bool(config.get("update_include_prerelease", False))
            else:
                include = include_prerelease.strip().lower() in {"1", "true", "yes", "on"}
            return jsonify(update_manager.check_latest(include_prerelease=include))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/updates/download", methods=["POST"])
    def updates_download():
        """Download the latest packaged EXE to local update storage."""
        try:
            body = request.get_json(silent=True) or {}
            include = bool(body.get("include_prerelease", config.get("update_include_prerelease", False)))
            return jsonify(update_manager.download_latest(include_prerelease=include))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/startup/status")
    def startup_status():
        """Read Windows startup integration status without mutating the OS."""
        try:
            return jsonify(startup_manager.status(config.get_all()))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/startup/preview", methods=["POST"])
    def startup_preview():
        """Preview startup-folder or scheduled-task changes."""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(startup_manager.preview(_startup_settings_from_body(body)))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/startup/apply", methods=["POST"])
    def startup_apply():
        """Apply Windows startup integration after explicit confirmation."""
        try:
            body = request.get_json(silent=True) or {}
            settings = _startup_settings_from_body(body)
            result = startup_manager.apply(settings, confirmation=str(body.get("confirmation") or ""))
            if not result.get("success"):
                status = 409 if result.get("required_confirmation") else 400
                return jsonify(result), status
            config.update(_startup_config_update(settings))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/startup/remove", methods=["POST"])
    def startup_remove():
        """Remove this app's startup-folder entry and scheduled task."""
        try:
            body = request.get_json(silent=True) or {}
            settings = _startup_settings_from_body(body)
            result = startup_manager.remove(settings, confirmation=str(body.get("confirmation") or ""))
            if not result.get("success"):
                status = 409 if result.get("required_confirmation") else 400
                return jsonify(result), status
            update = _startup_config_update({**settings, "startup_enabled": False, "startup_mode": "disabled"})
            config.update(update)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/desktop-shortcuts/create", methods=["POST"])
    def create_desktop_shortcuts():
        """Create optional desktop .lnk shortcuts for the portable app."""
        try:
            body = request.get_json(silent=True) or {}
            kind = str(body.get("kind") or "").strip()
            normal = bool(body.get("normal", False))
            start_codex_shortcut = bool(body.get("start_codex", False))
            if kind == "normal":
                normal = True
            elif kind == "start_codex":
                start_codex_shortcut = True
            if not normal and not start_codex_shortcut:
                normal = True
                start_codex_shortcut = True
            result = desktop_shortcut_manager.create_shortcuts(
                normal=normal,
                start_codex=start_codex_shortcut,
            )
            return jsonify(result), 200 if result.get("success") else 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/settings")
    def currency_settings():
        """返回成本/币种设置，默认脱敏。"""
        try:
            return jsonify(redact_currency_settings(normalize_currency_settings(config.get_all())))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/settings", methods=["POST"])
    def save_currency_settings():
        """保存成本/币种设置。只写本地 config，不写 Codex。"""
        try:
            body = request.get_json(silent=True) or {}
            update = update_currency_config(config.get_all(), body)
            config.update(update)
            _sync_proxy_request_log_config()
            return jsonify({"success": True, "settings": redact_currency_settings(normalize_currency_settings(config.get_all()))})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/rate")
    def currency_rate():
        """返回汇率快照；不进行未复核在线抓取。"""
        try:
            from_currency = request.args.get("from", "")
            to_currency = request.args.get("to", "")
            return jsonify(build_rate_snapshot(config.get_all(), from_currency, to_currency))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/currency/convert", methods=["POST"])
    def currency_convert():
        """按当前汇率设置换算金额并返回本次使用的汇率快照。"""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(convert_amount(
                config.get_all(),
                body.get("amount", 0),
                body.get("from_currency") or body.get("from") or "",
                body.get("to_currency") or body.get("to") or config.get("display_currency", "USD"),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cost/estimate", methods=["POST"])
    def cost_estimate():
        """估算单次请求成本；不写日志，不调用供应商。"""
        try:
            body = request.get_json(silent=True) or {}
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            pricing = body.get("pricing") if isinstance(body.get("pricing"), dict) else {}
            provider_id = str(body.get("provider_id") or "")
            model_id = str(body.get("model_id") or "")
            native_currency = str(body.get("native_currency") or "")

            if provider_id:
                _refresh_provider_registry_path()
                provider = provider_registry.get_provider(provider_id, include_secrets=False)
                if not provider:
                    return jsonify({"error": "Provider not found"}), 404
                preview = pricing_preview_payload(provider, model_id=model_id)
                provider_pricing = preview.get("pricing") if isinstance(preview.get("pricing"), dict) else {}
                merged_pricing = dict(provider_pricing)
                merged_pricing.update(pricing)
                pricing = merged_pricing
                native_currency = native_currency or str(preview.get("native_currency") or "")

            return jsonify(estimate_request_cost(
                usage=usage,
                pricing=pricing,
                currency_settings=config.get_all(),
                native_currency=native_currency,
                display_currency=str(body.get("display_currency") or ""),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/request-logs")
    def request_logs():
        """Read redacted local proxy request logs."""
        try:
            success_arg = request.args.get("success")
            success_filter = None
            if success_arg is not None:
                success_filter = str(success_arg).lower() in {"1", "true", "yes", "success"}
            return jsonify(_request_log_store().read_entries(
                limit=int(request.args.get("limit", 100)),
                provider_id=request.args.get("provider_id", ""),
                endpoint=request.args.get("endpoint", ""),
                media_kind=request.args.get("media_kind", ""),
                error_type=request.args.get("error_type", ""),
                since=request.args.get("since", ""),
                success=success_filter,
            ))
        except ValueError:
            return jsonify({"error": "Invalid request log query"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/request-logs/summary")
    def request_logs_summary():
        """Read local proxy request-log aggregates without network calls."""
        try:
            success_raw = str(request.args.get("success", "") or "").strip().lower()
            success_filter = None
            if success_raw:
                success_filter = success_raw in {"1", "true", "yes", "ok", "success"}
            return jsonify(_request_log_store().summary(
                provider_id=request.args.get("provider_id", ""),
                endpoint=request.args.get("endpoint", ""),
                media_kind=request.args.get("media_kind", ""),
                error_type=request.args.get("error_type", ""),
                since=request.args.get("since", ""),
                success=success_filter,
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/request-logs/retention/apply", methods=["POST"])
    def apply_request_log_retention():
        """Apply local request-log retention policy."""
        try:
            return jsonify({"success": True, "result": _request_log_store().enforce_retention()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/quota")
    def list_quota_cache():
        """读取内存中的余额/额度快照缓存。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(quota_manager.list_cached())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/quota")
    def get_provider_quota_cache(provider_id):
        """读取单个 provider 的缓存额度快照，不触发网络请求。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(quota_manager.cached_provider_quota(provider_id))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/quota/refresh", methods=["POST"])
    def refresh_provider_quota(provider_id):
        """按 provider quota_check 手动刷新额度/余额。"""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            result = quota_manager.refresh_provider_quota(provider_id, force=bool(body.get("force", True)))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/quota/refresh-draft", methods=["POST"])
    def refresh_provider_quota_draft(provider_id):
        """Run quota_check from the current provider form draft without saving it."""
        try:
            _refresh_provider_registry_path()
            existing = provider_registry.get_provider(provider_id, include_secrets=True)
            if not existing:
                return jsonify({"error": "Provider not found"}), 404
            body = request.get_json(silent=True) or {}
            draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
            provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
            return jsonify(refresh_provider_quota_preview(provider))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cleanup/preview")
    def cleanup_preview():
        """预览可安全清理的本地缓存/临时目录。"""
        try:
            return jsonify({"targets": _cleanup_targets(config)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cleanup/execute", methods=["POST"])
    def cleanup_execute():
        """执行安全清理。只删除 allowlisted local temp/cache paths。"""
        try:
            body = request.get_json(silent=True) or {}
            if body.get("confirmation") != "CLEAN_LOCAL_CACHE":
                return jsonify({
                    "error": "Cleanup confirmation required.",
                    "required_confirmation": "CLEAN_LOCAL_CACHE",
                }), 409
            requested = set(body.get("targets") or [])
            results = []
            for target in _cleanup_targets(config):
                if requested and target["id"] not in requested:
                    continue
                results.append(_cleanup_target(target))
            ensure_app_dirs()
            return jsonify({"success": True, "results": results})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Provider Registry API ───────────────

    # ---------- Local uninstall cleanup API ----------

    @app.route("/api/uninstall-cleanup/status")
    def uninstall_cleanup_status():
        """Return current uninstall cleanup write-lock state."""
        return jsonify({
            "write_locked": config.is_write_locked(),
            "reason": config.write_lock_reason(),
            "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
        })

    @app.route("/api/uninstall-cleanup/preview")
    def uninstall_cleanup_preview():
        """Preview app-owned files that uninstall cleanup can remove."""
        try:
            return jsonify({
                "targets": _uninstall_cleanup_targets(config),
                "write_locked": config.is_write_locked(),
                "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/uninstall-cleanup/execute", methods=["POST"])
    def uninstall_cleanup_execute():
        """Remove app-owned local data and lock writes until process restart."""
        try:
            body = request.get_json(silent=True) or {}
            if body.get("confirmation") != UNINSTALL_CLEANUP_CONFIRMATION:
                return jsonify({
                    "error": "Uninstall cleanup confirmation required.",
                    "required_confirmation": UNINSTALL_CLEANUP_CONFIRMATION,
                }), 409

            reason = "Uninstall cleanup completed. Restart the app to enable writes again."
            config.lock_writes(reason)

            results = []
            for target in _uninstall_cleanup_targets(config):
                if not target.get("exists"):
                    results.append({"id": target["id"], "success": True, "skipped": True, "path": target["path"]})
                    continue
                results.append(_cleanup_target(target))
            return jsonify({
                "success": all(item.get("success") for item in results),
                "write_locked": True,
                "reason": reason,
                "results": results,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ---------- Provider Registry API ----------

    @app.route("/api/providers")
    def list_providers():
        """读取本地 provider registry（默认脱敏）。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(_provider_payload(include_secrets=False))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers", methods=["POST"])
    def create_provider():
        """创建 provider。只写本地 registry，不写 Codex 配置。"""
        try:
            _refresh_provider_registry_path()
            data = request.get_json(silent=True) or {}
            provider = provider_registry.create_provider(data)
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>")
    def get_provider(provider_id):
        """读取单个 provider（默认脱敏）。"""
        try:
            _refresh_provider_registry_path()
            provider = provider_registry.get_provider(
                provider_id,
                include_secrets=False,
                extra_providers=_current_official_provider_extra(),
            )
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify(provider)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/secret", methods=["POST"])
    def reveal_provider_secret(provider_id):
        """Reveal one local provider secret after optional secondary-password verification."""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            field = str(body.get("field") or "api_key").strip()
            if field not in SECRET_REVEAL_FIELDS:
                return jsonify({"error": "Unsupported secret field"}), 400

            configured = _secret_reveal_password_configured(config)
            if configured and not _verify_secret_reveal_password(config, str(body.get("password") or "")):
                return jsonify({
                    "error": "Secondary password is required to reveal this secret.",
                    "password_required": True,
                }), 403

            provider = provider_registry.get_provider(
                provider_id,
                include_secrets=True,
                extra_providers=_current_official_provider_extra(),
            )
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({
                "success": True,
                "field": field,
                "secret": str(provider.get(field) or ""),
                "password_required": configured,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>", methods=["PUT", "PATCH"])
    def update_provider(provider_id):
        """更新 provider。只写本地 registry，不写 Codex 配置。"""
        try:
            _refresh_provider_registry_path()
            data = request.get_json(silent=True) or {}
            provider = provider_registry.update_provider(provider_id, data)
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>", methods=["DELETE"])
    def delete_provider(provider_id):
        """删除本地 provider registry 记录。"""
        try:
            _refresh_provider_registry_path()
            deleted = provider_registry.delete_provider(provider_id)
            if not deleted:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/provider-presets")
    def list_provider_presets():
        """读取内置 provider preset。"""
        try:
            return jsonify(provider_registry.list_presets())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/import-preset", methods=["POST"])
    def import_provider_preset():
        """从 preset 创建 provider，可带少量 override。"""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            provider = provider_registry.import_preset(
                preset_id=body.get("preset_id", ""),
                overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
            )
            return jsonify({"success": True, "provider": provider})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/test", methods=["POST"])
    def test_provider(provider_id):
        """本地 provider 配置校验；不做真实网络请求，不写 Codex。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(provider_registry.test_provider(provider_id=provider_id))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/test", methods=["POST"])
    def test_provider_payload():
        """校验未保存的 provider payload；不做真实网络请求，不写 Codex。"""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(provider_registry.test_provider(provider_data=body))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/responses-profile/probe-preview", methods=["GET", "POST"])
    def provider_responses_probe_preview(provider_id):
        """预览国内 Responses 请求；不联网、不写 Codex、不写 provider registry。"""
        try:
            _refresh_provider_registry_path()
            provider = provider_registry.get_provider(provider_id, include_secrets=True)
            if not provider:
                return jsonify({"error": "Provider not found"}), 404
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
            else:
                body = {}
            request_json = body.get("request_json") if isinstance(body.get("request_json"), dict) else None
            compact = bool(body.get("compact", False))
            return jsonify(build_domestic_responses_probe_preview(provider, request_json=request_json, compact=compact))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/responses-profile/probe-preview", methods=["POST"])
    def provider_draft_responses_probe_preview():
        """预览未保存 provider 草稿的国内 Responses 请求；纯 dry-run。"""
        try:
            body = request.get_json(silent=True) or {}
            provider = body.get("provider") if isinstance(body.get("provider"), dict) else body
            request_json = body.get("request_json") if isinstance(body.get("request_json"), dict) else None
            compact = bool(body.get("compact", False))
            return jsonify(build_domestic_responses_probe_preview(provider, request_json=request_json, compact=compact))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/media-adapter/preview-draft", methods=["POST"])
    def preview_media_adapter_draft(provider_id):
        """Preview adapter-required media routes from the current provider form draft."""
        try:
            _refresh_provider_registry_path()
            existing = provider_registry.get_provider(provider_id, include_secrets=True)
            if not existing:
                return jsonify({"error": "Provider not found"}), 404
            body = request.get_json(silent=True) or {}
            draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
            provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
            request_json = body.get("request") if isinstance(body.get("request"), dict) else {}
            return jsonify(build_media_adapter_preview_bundle(
                provider,
                request_json=request_json,
                media_kind=str(body.get("media_kind") or ""),
                model_id=str(body.get("model") or ""),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/media-route/preview-draft", methods=["POST"])
    def preview_media_route_draft(provider_id):
        """Preview OpenAI-compatible media proxy readiness from the current provider form draft."""
        try:
            _refresh_provider_registry_path()
            existing = provider_registry.get_provider(provider_id, include_secrets=True)
            if not existing:
                return jsonify({"error": "Provider not found"}), 404
            body = request.get_json(silent=True) or {}
            draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
            provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
            return jsonify(build_media_route_readiness(
                provider,
                model_id=str(body.get("model") or ""),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/export")
    def export_provider_bundle():
        """导出脱敏 provider bundle，供诊断/备份使用。"""
        try:
            _refresh_provider_registry_path()
            return jsonify(provider_registry.export_bundle())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/model-catalog/preview")
    def preview_model_catalog():
        """Unified Model Catalog 预览；不写 Codex model catalog。"""
        try:
            _refresh_provider_registry_path()
            focus_provider_id = request.args.get("focus_provider_id", "")
            return jsonify(provider_registry.preview_catalog(
                focus_provider_id=focus_provider_id,
                extra_providers=_current_official_provider_extra(),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/focus", methods=["GET", "POST"])
    def provider_focus_api():
        """Read or update the current quick-switch provider focus."""
        try:
            _refresh_provider_registry_path()
            if request.method == "GET":
                payload = _provider_payload(include_secrets=False)
                focus_provider_id = str(payload.get("focus_provider_id") or "")
                providers = [
                    {
                        "id": p.get("id", ""),
                        "display_name": p.get("display_name") or p.get("id", ""),
                        "short_alias": p.get("short_alias", ""),
                        "enabled": p.get("enabled", True),
                        "catalog_visibility": p.get("catalog_visibility", "focused_only"),
                        "switch_only": bool(p.get("switch_only")),
                        "amr_excluded": bool(p.get("amr_excluded")),
                        "local_proxy_routing": p.get("local_proxy_routing", True),
                        "routing_mode": p.get("routing_mode", ""),
                        "codex_login": bool(p.get("codex_login")),
                        "focused": p.get("id") == focus_provider_id,
                    }
                    for p in payload.get("providers", [])
                    if isinstance(p, dict)
                ]
                return jsonify({
                    "success": True,
                    "focus_provider_id": focus_provider_id,
                    "providers": providers,
                })
            body = request.get_json(silent=True) or {}
            provider_id = str(body.get("provider_id", "") or "")
            result = provider_registry.set_focus_provider(
                provider_id,
                extra_providers=_current_official_provider_extra(),
            )
            if not result.get("success"):
                return jsonify(result), 404
            confirmed_payload = _provider_payload(include_secrets=False)
            confirmed_focus = str(confirmed_payload.get("focus_provider_id") or "")
            result["store_path"] = confirmed_payload.get("store_path", "")
            result["verified_focus_provider_id"] = confirmed_focus
            if confirmed_focus != provider_id:
                return jsonify({
                    **result,
                    "success": False,
                    "error": "Provider focus did not persist to providers.json.",
                }), 500
            selected_provider = None
            if provider_id:
                selected_provider = next(
                    (p for p in confirmed_payload.get("providers", []) if isinstance(p, dict) and p.get("id") == provider_id),
                    None,
                )
            result["proxy"] = {"started": False, "skipped": True}
            if selected_provider and provider_allows_local_routing(selected_provider):
                try:
                    config.set("codex_last_start_mode", START_MODE_PRESERVE_LOGIN_PROXY)
                except Exception:
                    pass
                result["proxy"] = _ensure_proxy_for_current_provider("provider_focus")
            elif selected_provider:
                try:
                    config.set("codex_last_start_mode", START_MODE_OFFICIAL_DIRECT)
                except Exception:
                    pass
                result["routing_mode"] = selected_provider.get("routing_mode") or "official_direct"
                result["switch_only"] = bool(selected_provider.get("switch_only"))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-injection/quick-settings", methods=["GET", "POST"])
    def codex_injection_quick_settings():
        """Small hot-settings surface for the injected Codex quick panel."""
        try:
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                provider_id = str(body.get("provider_id", body.get("focus_provider_id", "")) or "")
                if "provider_id" in body or "focus_provider_id" in body:
                    focus_result = provider_registry.set_focus_provider(
                        provider_id,
                        extra_providers=_current_official_provider_extra(),
                    )
                    if not focus_result.get("success"):
                        return jsonify(focus_result), 404
                    if provider_id:
                        selected_payload = _provider_payload(include_secrets=False)
                        selected_provider = next(
                            (
                                p for p in selected_payload.get("providers", [])
                                if isinstance(p, dict) and p.get("id") == provider_id
                            ),
                            None,
                        )
                        if selected_provider and provider_allows_local_routing(selected_provider):
                            _ensure_proxy_for_current_provider("injected_quick_settings")
                updates: Dict[str, Any] = {}
                allowed = {
                    "desktop_monitor_enabled",
                    "codex_injection_enabled",
                    "plugin_unlock_enabled",
                }
                for key in allowed:
                    if key in body:
                        updates[key] = _coerce_bool(body.get(key), bool(config.get(key, DEFAULT_CONFIG.get(key))))
                if updates:
                    config.update(updates)
            providers_payload = _provider_payload(include_secrets=False)
            focus_provider_id = str(providers_payload.get("focus_provider_id") or "")
            focus_id, _focused_provider = _focused_provider_from_payload(providers_payload)
            auth_mode = "unknown"
            try:
                auth_mode = CodexConfigManager().get_auth_mode()
            except Exception:
                pass
            last_start_mode = str(config.get("codex_last_start_mode", "") or "")
            official_usage_default = _official_usage_visible_for_current_mode(
                auth_mode,
                providers_payload,
                last_start_mode=last_start_mode,
            )
            third_party_focus_provider = _provider_focus_uses_other_api(providers_payload)
            request_log_provider_filter = focus_id if third_party_focus_provider else ""
            request_log_summary = _request_log_store().summary(provider_id=request_log_provider_filter)
            token_snapshot: Dict[str, Any] = {}
            try:
                token_stats.db_path = config.get("db_path")
                token_snapshot = token_stats.get_current_stats(granularity="total")
            except Exception as exc:
                token_snapshot = {"error": str(exc)}
            quota_snapshot: Dict[str, Any] = {}
            if focus_id and third_party_focus_provider:
                try:
                    quota_snapshot = quota_manager.cached_provider_quota(focus_id)
                except Exception as exc:
                    quota_snapshot = {"success": False, "provider_id": focus_id, "error": str(exc)}
            usage = {
                "auth_mode": auth_mode,
                "official_usage_default": official_usage_default,
                "official_usage_hidden_by_provider": bool(
                    auth_mode == "official_oauth"
                    and not official_usage_default
                    and (third_party_focus_provider or last_start_mode in {START_MODE_PROXY_INJECTION, START_MODE_PRESERVE_LOGIN_PROXY})
                ),
                "third_party_focus_provider": third_party_focus_provider,
                "focused_provider_id": focus_id,
                "codex_last_start_mode": last_start_mode,
                "total_tokens": _safe_int(token_snapshot.get("total_tokens") or token_snapshot.get("current_total_tokens")),
                "current_total_tokens": _safe_int(token_snapshot.get("current_total_tokens") or token_snapshot.get("total_tokens")),
                "current_context_used_tokens": _safe_int(token_snapshot.get("current_context_used_tokens")),
                "current_context_window": _safe_int(token_snapshot.get("current_context_window")),
                "request_log_summary_scope": "focused_provider" if request_log_provider_filter else "all",
                "request_log_summary": request_log_summary,
                "quota": quota_snapshot,
            }
            providers = [
                {
                    "id": p.get("id", ""),
                    "display_name": p.get("display_name") or p.get("id", ""),
                    "short_alias": p.get("short_alias", ""),
                    "enabled": p.get("enabled", True),
                    "switch_only": bool(p.get("switch_only")),
                    "codex_login": bool(p.get("codex_login")),
                    "local_proxy_routing": p.get("local_proxy_routing", True),
                    "focused": p.get("id") == focus_provider_id,
                }
                for p in providers_payload.get("providers", [])
                if isinstance(p, dict)
            ]
            settings = {
                "desktop_monitor_enabled": _coerce_bool(config.get("desktop_monitor_enabled", True), True),
                "codex_injection_enabled": _coerce_bool(config.get("codex_injection_enabled", True), True),
                "plugin_unlock_enabled": bool(config.get("plugin_unlock_enabled", False)),
                "codex_last_start_mode": str(config.get("codex_last_start_mode", "") or ""),
            }
            return jsonify({
                "success": True,
                "settings": settings,
                "focus_provider_id": focus_provider_id,
                "providers": providers,
                "usage": usage,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/model-catalog/preview-draft", methods=["POST"])
    def preview_model_catalog_draft(provider_id):
        """Preview Unified Model Catalog with the current provider form draft."""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
            result = provider_registry.preview_catalog_with_provider_draft(
                provider_id,
                draft if isinstance(draft, dict) else {},
            )
            if not result.get("success", True):
                return jsonify(result), 404 if result.get("error") == "Provider not found" else 400
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/bulk-models", methods=["POST"])
    def bulk_update_provider_models(provider_id):
        """
        批量更新 provider 下的模型选择状态。
        支持：select_all、deselect_all、select_vision、select_low_cost、select_high_context。
        """
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            action = body.get("action", "")
            criteria = body.get("criteria")
            result = provider_registry.bulk_update_models(provider_id, action, criteria)
            if not result.get("success"):
                return jsonify(result), 404 if result.get("error") == "Provider not found" else 400
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/amr/add-selected", methods=["POST"])
    def add_selected_provider_models_to_amr(provider_id):
        """Add selected provider models into a local AMR group."""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            group_id = str(body.get("group_id") or "default").strip() or "default"

            providers_data = _provider_payload(include_secrets=False)
            provider = next(
                (p for p in providers_data.get("providers", []) if p.get("id") == provider_id),
                None,
            )
            if not provider:
                return jsonify({"success": False, "error": "Provider not found"}), 404
            if not provider_allows_local_routing(provider):
                return jsonify({
                    "success": False,
                    "error": "Official login is switch-only and cannot be added to AMR.",
                    "group_id": group_id,
                    "added_count": 0,
                }), 400

            default_priority = 1 if provider.get("catalog_visibility") == "always_visible" else 2
            priority = _clamp_int(body.get("priority", default_priority), default_priority, 1, 100)
            candidates = _selected_provider_models_to_amr_candidates(provider, priority)
            if not candidates:
                return jsonify({
                    "success": False,
                    "error": "No selected enabled models found",
                    "group_id": group_id,
                    "added_count": 0,
                }), 400

            group = amr_registry.add_candidates_to_group(
                group_id,
                candidates,
                DEFAULT_GROUP_DISPLAY_NAME if group_id == DEFAULT_GROUP_ID else group_id,
            )
            return jsonify({
                "success": True,
                "group": group,
                "group_id": group.get("id", group_id),
                "added_count": len(candidates),
                "upserted_count": group.get("upserted_count", len(candidates)),
            })
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/visibility", methods=["POST"])
    def set_provider_visibility_api(provider_id):
        """设置 provider 的 catalog visibility。"""
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            visibility = body.get("visibility", "")
            result = provider_registry.set_provider_visibility(provider_id, visibility)
            if not result.get("success"):
                return jsonify(result), 404
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/model-rotation/simulate", methods=["POST"])
    def simulate_model_rotation():
        """
        AMR 路由模拟：根据当前 provider registry 状态返回路由决策。

        设计意图：
          - 这是 Route Simulator UI 的后端支撑：用户选择 capability 和 model，
            后端基于当前 provider registry 动态构建 AMR 候选列表并执行路由。
          - 动态候选构建：将每个 provider 的每个启用模型转换为一个 candidate，
            always_visible 的 provider 优先级设为 1（最高），其余为 2。
          - 纯模拟：不触发真实网络请求，只返回路由决策和 explanation，
            供用户预览路由行为。

        边界条件：
          - 若无任何启用 provider/model，AMR route 会返回 "No candidates" 错误，
            前端展示即可，非 500 异常。
        """
        try:
            _refresh_provider_registry_path()
            body = request.get_json(silent=True) or {}
            required_capabilities = _normalize_route_capabilities(body)
            required_context = _clamp_int(body.get("required_context", 0), 0, 0, 10_000_000)
            model_filter = str(body.get("model") or "").strip()

            providers_data = _provider_payload(include_secrets=False)
            candidates = []
            for p in providers_data.get("providers", []):
                if not provider_allows_local_routing(p):
                    continue
                alias = str(p.get("short_alias") or p.get("id") or "").strip()
                for m in p.get("models", []):
                    if not m.get("enabled", True):
                        continue
                    model_id = str(m.get("id") or "").strip()
                    merged_caps = merge_provider_model_capabilities(p, m)
                    candidates.append({
                        "id": f"{p['id']}/{model_id}",
                        "provider_id": p["id"],
                        "provider_display_name": p.get("display_name", ""),
                        "short_alias": alias,
                        "model_id": model_id,
                        "codex_model_id": f"{alias}/{model_id}" if alias and model_id else model_id,
                        "display_name": m.get("display_name") or model_id,
                        "priority": 1 if p.get("catalog_visibility") == "always_visible" else 2,
                        "capabilities": merged_caps,
                        "context_window": m.get("context_window", 0),
                    })

            unfiltered_candidates = candidates
            if model_filter:
                candidates = [c for c in candidates if _route_candidate_matches_model(c, model_filter)]
                if not candidates:
                    return jsonify({
                        "success": False,
                        "error": "No candidate matched requested model filter",
                        "model_filter": model_filter,
                        "required_capabilities": sorted(required_capabilities),
                        "required_context": required_context,
                        "candidate_count": len(unfiltered_candidates),
                        "candidate_status": _route_candidate_status(unfiltered_candidates, required_capabilities, required_context, model_filter),
                        "explanation": [
                            f"Model filter '{model_filter}' did not match provider/model ids, aliases, or Codex-visible ids.",
                            "No network request was made.",
                        ],
                    })

            from model_rotation import AdaptiveModelRotation
            amr = AdaptiveModelRotation([{
                "id": DEFAULT_GROUP_ID,
                "name": DEFAULT_GROUP_DISPLAY_NAME,
                "candidates": candidates,
            }])

            decision = amr.route(
                group_id=DEFAULT_GROUP_ID,
                required_capabilities=required_capabilities,
                required_context=required_context,
            )
            decision["required_capabilities"] = sorted(required_capabilities)
            decision["required_context"] = required_context
            decision["model_filter"] = model_filter
            decision["candidate_count"] = len(candidates)
            decision["candidate_status"] = _route_candidate_status(candidates, required_capabilities, required_context, model_filter)
            decision.setdefault("explanation", [])
            decision["explanation"].append("Simulation only; no provider request or Codex config write was performed.")
            return jsonify(decision)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── AMR Registry API ───────────────

    @app.route("/api/amr/groups")
    def list_amr_groups():
        """列出所有 rotation groups。"""
        try:
            return jsonify(amr_registry.list_groups())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups", methods=["POST"])
    def create_amr_group():
        """创建 AMR rotation group。"""
        try:
            data = request.get_json(silent=True) or {}
            group = amr_registry.create_group(data)
            return jsonify({"success": True, "group": group})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>")
    def get_amr_group(group_id):
        """读取单个 rotation group。"""
        try:
            group = amr_registry.get_group(group_id)
            if not group:
                return jsonify({"error": "Group not found"}), 404
            return jsonify(group)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>", methods=["PUT", "PATCH"])
    def update_amr_group(group_id):
        """更新 AMR rotation group。"""
        try:
            data = request.get_json(silent=True) or {}
            group = amr_registry.update_group(group_id, data)
            if not group:
                return jsonify({"error": "Group not found"}), 404
            return jsonify({"success": True, "group": group})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/groups/<group_id>", methods=["DELETE"])
    def delete_amr_group(group_id):
        """删除 AMR rotation group。"""
        try:
            deleted = amr_registry.delete_group(group_id)
            if not deleted:
                return jsonify({"error": "Group not found"}), 404
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/sync-from-providers", methods=["POST"])
    def sync_amr_from_providers():
        """从当前 provider registry 同步生成 AMR 候选。"""
        try:
            _refresh_provider_registry_path()
            group = amr_registry.build_from_providers(
                provider_registry,
                extra_providers=_current_official_provider_extra(),
            )
            return jsonify({"success": True, "group": group})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/amr/route", methods=["POST"])
    def amr_route():
        """执行 AMR 路由测试。"""
        try:
            body = request.get_json(silent=True) or {}
            group_id = body.get("group_id", "")
            if not group_id:
                return jsonify({"error": "group_id is required"}), 400
            capabilities = body.get("capabilities", ["text"])
            if isinstance(capabilities, list):
                capabilities = set(capabilities)
            context = int(body.get("context", 0))
            decision = amr_registry.route(
                group_id=group_id,
                request_capabilities=capabilities,
                required_context=context,
            )
            return jsonify(decision)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Codex Integration API ───────────────

    @app.route("/api/codex-integration/status")
    def codex_integration_status():
        """读取当前 Codex config/auth 状态，用于 Diff Preview 和诊断。"""
        try:
            mgr = CodexConfigManager()
            config_data = mgr.read_config()
            auth_data = mgr.read_auth()
            auth_mode = detect_auth_mode(auth_data)
            effective = resolve_effective_codex_settings(config_data, auth_mode)
            permissions = mgr.inspect_permissions()
            login_defaults = _official_login_defaults(mgr)
            return jsonify({
                "config": config_data,
                "auth_redacted": redact_auth_for_preview(auth_data),
                "auth_mode": auth_mode,
                "effective_config": effective,
                "effective_model_provider": effective.get("model_provider", ""),
                "effective_model": effective.get("model", ""),
                "effective_model_provider_source": effective.get("model_provider_source", ""),
                "effective_model_source": effective.get("model_source", ""),
                "official_oauth_implied_provider": effective.get("official_oauth_implied_provider", False),
                **login_defaults,
                "codex_home": str(mgr.codex_home),
                "config_path": str(mgr.config_path),
                "auth_path": str(mgr.auth_path),
                "permissions": permissions,
                "proxy_status": proxy_server.status(),
                "default_proxy_base_url": _current_proxy_base_url(),
                "plugin_unlock_enabled": bool(config.get("plugin_unlock_enabled", False)),
                "codex_injection_enabled": _coerce_bool(config.get("codex_injection_enabled", True), True),
                "codex_cdp_port": _coerce_port(config.get("codex_cdp_port", DEFAULT_CDP_PORT)),
                "codex_goals_enabled": _config_goals_enabled(config),
                "codex_goals_config_enabled": codex_goals_enabled_from_config(config_data, True),
                "config_risk_assessment": inspect_codex_config_risks(mgr, config_data),
                "backend_url": _current_backend_url(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/preview", methods=["POST"])
    def codex_integration_preview():
        """预览写入 local proxy provider 后的 diff；不做任何写入。"""
        try:
            body = request.get_json(silent=True) or {}
            mgr = CodexConfigManager()
            login_defaults = _official_login_defaults(mgr)
            preview = mgr.preview_write_provider(
                proxy_base_url=body.get("proxy_base_url") or _current_proxy_base_url(),
                proxy_model=body.get("proxy_model", "auto"),
                goals_enabled=_config_goals_enabled(config),
                local_proxy_bearer_token=config.get("local_proxy_bearer_token", ""),
            )
            preview["auth_redacted"] = redact_auth_for_preview(mgr.read_auth())
            preview.update(login_defaults)
            preview["start_mode"] = START_MODE_PRESERVE_LOGIN_PROXY
            return jsonify(preview)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/permissions-preview", methods=["POST"])
    def codex_integration_permissions_preview():
        """Preview approval/sandbox config changes without writing Codex files."""
        try:
            body = request.get_json(silent=True) or {}
            workspace_roots = body.get("writable_roots", [])
            if isinstance(workspace_roots, str):
                workspace_roots = [
                    item.strip()
                    for item in workspace_roots.replace("\n", ",").split(",")
                    if item.strip()
                ]
            workspace_update = None
            if body.get("sandbox_mode") == "workspace-write" or workspace_roots:
                workspace_update = {
                    "writable_roots": workspace_roots,
                    "network_access": bool(body.get("network_access", False)),
                    "exclude_tmpdir_env_var": bool(body.get("exclude_tmpdir_env_var", False)),
                    "exclude_slash_tmp": bool(body.get("exclude_slash_tmp", False)),
                }
            mgr = CodexConfigManager()
            preview = mgr.preview_permissions_update(
                approval_policy=body.get("approval_policy") or None,
                sandbox_mode=body.get("sandbox_mode") or None,
                default_permissions=body.get("default_permissions") or None,
                windows_sandbox=body.get("windows_sandbox") or None,
                sandbox_workspace_write=workspace_update,
            )
            return jsonify(preview)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/permissions-repair", methods=["POST"])
    def codex_integration_permissions_repair():
        """Apply the recommended approval/sandbox repair after explicit confirmation."""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "repair_codex_sandbox_permissions")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = repair_codex_sandbox_permissions(mgr)
            return jsonify(result), 200 if result.get("success") else 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/approval-bridge-preview", methods=["POST"])
    def codex_integration_approval_bridge_preview():
        """Preview Codex app-server approval JSON-RPC bridge mapping without replying to Codex."""
        try:
            body = request.get_json(silent=True) or {}
            message = body.get("message") if isinstance(body.get("message"), dict) else body.get("request")
            if not isinstance(message, dict) and isinstance(body.get("method"), str):
                message = body
            if not isinstance(message, dict):
                return jsonify({"success": False, "error": "JSON-RPC approval message is required"}), 400
            decision = body.get("decision") if isinstance(body.get("decision"), dict) else None
            return jsonify(build_codex_approval_bridge_preview(message, decision))
        except (CodexApprovalBridgeError, ValueError) as e:
            return jsonify({"success": False, "preview": True, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/apply", methods=["POST"])
    def codex_integration_apply():
        """Preview local proxy provider config; actual Codex writes happen only in the start flow."""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "apply_codex_provider_config")
            if denied:
                return denied
            mgr = CodexConfigManager()
            login_defaults = _official_login_defaults(mgr)
            result = mgr.preview_write_provider(
                proxy_base_url=body.get("proxy_base_url") or _current_proxy_base_url(),
                proxy_model=body.get("proxy_model", "auto"),
                goals_enabled=_config_goals_enabled(config),
                local_proxy_bearer_token=config.get("local_proxy_bearer_token", ""),
            )
            result.update(login_defaults)
            result["start_mode"] = START_MODE_PRESERVE_LOGIN_PROXY
            result["success"] = True
            result["applied"] = False
            result["deferred_until_codex_start"] = True
            result["message"] = "Codex config changes are deferred. Click Start Codex to write config.toml and launch Codex."
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/backups")
    def codex_integration_backups():
        """列出 Codex config/auth 备份。"""
        try:
            mgr = CodexConfigManager()
            return jsonify({"backups": mgr.list_all_backups()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restore-config", methods=["POST"])
    def codex_integration_restore_config():
        """从备份恢复 config.toml。"""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restore_codex_config")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = mgr.restore_config(body.get("backup_path", ""))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restore-auth", methods=["POST"])
    def codex_integration_restore_auth():
        """从备份恢复 auth.json。"""
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restore_codex_auth")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = mgr.restore_auth(body.get("backup_path", ""))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/disable-proxy-provider", methods=["POST"])
    def codex_integration_disable_proxy_provider():
        """
        禁用本地代理 provider：从 Codex config.toml 中移除 codex_enhance_manager
        provider 配置，恢复到默认状态。保留官方登录态。
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "disable_codex_proxy_provider")
            if denied:
                return denied
            mgr = CodexConfigManager()
            return jsonify(disable_codex_enhance_provider_config(
                mgr,
                goals_enabled=_config_goals_enabled(config),
            ))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/reset-for-official-login", methods=["POST"])
    def codex_integration_reset_for_official_login():
        """
        Reset Codex config/auth for users moving from pure proxy mode to first official login.

        This is intentionally high-friction: it backs up and removes config.toml/auth.json,
        warns about chat-history visibility/loss risk, and can optionally restart Windows.
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "reset_codex_for_official_login")
            if denied:
                return denied
            if body.get("risk_confirmation") != CHAT_HISTORY_RISK_CONFIRMATION:
                return jsonify({
                    "error": "Chat history risk confirmation required.",
                    "required_risk_confirmation": CHAT_HISTORY_RISK_CONFIRMATION,
                    "chat_history_risk": True,
                    "message": (
                        "This operation removes Codex config.toml/auth.json after backup. "
                        "Chat history may become temporarily invisible or may be lost. "
                        f"Send risk_confirmation={CHAT_HISTORY_RISK_CONFIRMATION!r} after human review."
                    ),
                }), 409
            mgr = CodexConfigManager()
            result = reset_codex_for_official_login(
                mgr,
                restart_windows=_coerce_bool(body.get("restart_windows"), False),
            )
            status = 200 if result.get("success") else 500
            return jsonify(result), status
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/repair-config-template", methods=["POST"])
    def codex_integration_repair_config_template():
        """
        Reset config.toml to a conservative template state while preserving auth.json.

        This fixes common startup crashes/stalls caused by stale MCP, hooks, provider
        commands, WSL paths in native Windows mode, duplicate TOML tables, or legacy
        profile/provider settings.
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "repair_codex_config_template")
            if denied:
                return denied
            mgr = CodexConfigManager()
            result = repair_codex_config_template(
                mgr,
                goals_enabled=_config_goals_enabled(config),
                restart_windows=_coerce_bool(body.get("restart_windows"), False),
            )
            status = 200 if result.get("success") else 500
            return jsonify(result), status
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-integration/restart-codex", methods=["POST"])
    def codex_integration_restart_codex():
        """
        重启 Codex：先 kill 再 start。同步当前 provider/model 配置后启动。
        """
        try:
            body = request.get_json(silent=True) or {}
            denied = _require_codex_mutation_confirmation(body, "restart_codex_process")
            if denied:
                return denied
            use_cpp = body.get("use_codex_plus_plus", config.get("use_codex_plus_plus", False))

            # 可选：先同步配置
            if body.get("sync_before_restart", True):
                sync_payload, sync_status = _run_sync_with_backup(backup_mgr, path_config=_sync_paths_from_config())
                if sync_status >= 400:
                    return jsonify({"error": "同步失败，取消重启", "sync": sync_payload}), 500

            injection_settings = _resolve_codex_injection_settings(
                config,
                body,
                use_codex_plus_plus=bool(use_cpp),
                persist=True,
            )

            # Kill Codex
            kill_ok, kill_msg = kill_codex()

            # Start Codex
            start_ok, start_msg = start_codex(
                use_codex_plus_plus=use_cpp,
                codex_plus_plus_path=config.get("codex_plus_plus_path", ""),
                codex_cli_path=config.get("codex_cli_path", ""),
                enable_cdp_injection=injection_settings["enabled"],
                cdp_port=injection_settings["cdp_port"],
                backend_url=_current_backend_url(),
            )

            return jsonify({
                "success": start_ok,
                "killed": kill_ok,
                "kill_message": kill_msg,
                "start_message": start_msg,
                "codex_injection_enabled": injection_settings["requested_enabled"],
                "codex_cdp_port": injection_settings["cdp_port"],
                "codex_injection_active": injection_settings["enabled"],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Local Proxy API ───────────────

    @app.route("/api/proxy/status")
    def proxy_status():
        """获取本地代理状态。"""
        try:
            status = proxy_server.status()
            if not isinstance(status, dict):
                status = {}
            if not status.get("running") and _focused_or_enabled_provider_needs_proxy():
                recovery = _ensure_proxy_for_current_provider("proxy_status_auto_recover", sync_codex_config=False)
                recovered_status = recovery.get("status") if isinstance(recovery.get("status"), dict) else proxy_server.status()
                status = dict(recovered_status) if isinstance(recovered_status, dict) else {}
                status["auto_start"] = recovery
            return jsonify(status)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/start", methods=["POST"])
    def proxy_start():
        """启动本地代理服务器。"""
        try:
            body = request.get_json(silent=True) or {}
            new_port = body.get("port")
            if new_port is not None:
                if isinstance(new_port, bool) or not isinstance(new_port, int) or new_port < 1 or new_port > 65535:
                    return jsonify({"error": "Invalid proxy port", "status": proxy_server.status()}), 400
                proxy_server.port = new_port
            new_store = body.get("provider_store_path")
            if new_store:
                proxy_server.provider_store_path = new_store
            _sync_proxy_request_log_config()
            ok = proxy_server.start()
            if ok:
                status = proxy_server.status()
                config.set("proxy_port", status.get("port", proxy_server.port))
                return jsonify({
                    "success": True,
                    "status": status,
                    "provider_config": {
                        "success": True,
                        "skipped": True,
                        "reason": "deferred_until_codex_start",
                        "message": "Local proxy started. Codex config will be written only when Codex is started from this app.",
                    },
                })
            return jsonify({
                "error": "未能在配置端口及后续端口中找到可用代理端口",
                "status": proxy_server.status(),
            }), 409
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proxy/stop", methods=["POST"])
    def proxy_stop():
        """停止本地代理服务器。"""
        try:
            proxy_server.stop()
            return jsonify({"success": True, "status": proxy_server.status()})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/codex-injection/status")
    def codex_injection_status():
        """Status endpoint consumed by the injected Codex renderer menu."""
        auth_mode = "unknown"
        provider_payload: Dict[str, Any] = {}
        try:
            auth_mode = CodexConfigManager().get_auth_mode()
        except Exception:
            pass
        try:
            provider_payload = _provider_payload(include_secrets=False)
        except Exception:
            provider_payload = {}
        official_usage_visible = _official_usage_visible_for_current_mode(
            auth_mode,
            provider_payload,
            last_start_mode=str(config.get("codex_last_start_mode", "") or ""),
        )
        plugin_unlock = bool(config.get("plugin_unlock_enabled", False))
        return jsonify({
            "success": True,
            "enabled": _coerce_bool(config.get("codex_injection_enabled", True), True),
            "plugin_unlock_enabled": plugin_unlock,
            "plugin_marketplace_unlock": plugin_unlock,
            "plugin_entry_unlock": plugin_unlock,
            "force_plugin_install": plugin_unlock,
            "auth_mode": auth_mode,
            "official_usage_visible": official_usage_visible,
            "hide_official_usage_alert": not official_usage_visible,
            "official_focus_provider": _provider_focus_is_official_login(provider_payload),
            "third_party_focus_provider": _provider_focus_uses_other_api(provider_payload),
            "focused_provider_id": str(provider_payload.get("focus_provider_id") or "") if isinstance(provider_payload, dict) else "",
            "codex_last_start_mode": str(config.get("codex_last_start_mode", "") or ""),
            "cdp_port": _coerce_port(config.get("codex_cdp_port", DEFAULT_CDP_PORT)),
            "backend_url": _current_backend_url(),
        })

    @app.route("/api/codex-injection/apply", methods=["POST"])
    def codex_injection_apply():
        """Manually retry CDP injection against an already running Codex window."""
        try:
            body = request.get_json(silent=True) or {}
            injection_settings = _resolve_codex_injection_settings(
                config,
                body,
                use_codex_plus_plus=False,
                persist=True,
            )
            if not injection_settings["requested_enabled"]:
                return jsonify({
                    "success": False,
                    "enabled": False,
                    "error": "Codex enhancement injection is disabled.",
                    "cdp_port": injection_settings["cdp_port"],
                }), 409
            result = inject_codex_enhancements(
                port=injection_settings["cdp_port"],
                backend_url=body.get("backend_url") or _current_backend_url(),
                timeout_seconds=float(body.get("timeout_seconds") or 4),
            )
            result["enabled"] = injection_settings["requested_enabled"]
            result["cdp_port"] = injection_settings["cdp_port"]
            return jsonify(result), 200 if result.get("success") else 409
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/proxy/test-route", methods=["POST"])
    def proxy_test_route():
        """
        测试代理路由：给定 model ID，返回会路由到哪个 provider，不触发真实网络请求。
        """
        try:
            body = request.get_json(silent=True) or {}
            model_id = body.get("model", "")
            from proxy_server import _resolve_provider_for_model
            provider = _resolve_provider_for_model(model_id)
            if provider:
                return jsonify({
                    "success": True,
                    "provider_id": provider.get("id"),
                    "display_name": provider.get("display_name"),
                    "base_url": provider.get("base_url"),
                    "api_format": provider.get("api_format"),
                    "short_alias": provider.get("short_alias"),
                })
            return jsonify({"success": False, "error": f"No provider found for model '{model_id}'"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── Move Repair API ───────────────

    @app.route("/api/move-repair/status/<thread_id>")
    def move_repair_status(thread_id):
        """读取 thread 元数据（SQLite + JSONL 合并视图）。"""
        try:
            mgr = _move_repair_manager()
            data = mgr.read_thread_metadata(thread_id)
            return jsonify({"success": True, "metadata": data})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/dry-run", methods=["POST"])
    def move_repair_dry_run():
        """预演移动：验证 target_path 是否有效 Git 仓库，不修改数据。"""
        try:
            body = request.get_json(silent=True) or {}
            thread_id = body.get("thread_id", "")
            target_path = body.get("target_path", "")
            if not thread_id or not target_path:
                return jsonify({"error": "thread_id 和 target_path 必填"}), 400
            mgr = _move_repair_manager()
            result = mgr.dry_run_move(thread_id, target_path)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/execute", methods=["POST"])
    def move_repair_execute():
        """执行移动：原子更新 SQLite、JSONL、Index，失败自动回滚。"""
        try:
            body = request.get_json(silent=True) or {}
            thread_id = body.get("thread_id", "")
            target_path = body.get("target_path", "")
            if not thread_id or not target_path:
                return jsonify({"error": "thread_id 和 target_path 必填"}), 400
            mgr = _move_repair_manager()
            result = mgr.execute_move(thread_id, target_path)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/verify/<thread_id>")
    def move_repair_verify(thread_id):
        """一致性校验：检查三端 cwd 是否对齐且指向有效 Git 仓库。"""
        try:
            mgr = _move_repair_manager()
            result = mgr.verify_consistency(thread_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/move-repair/repair-current", methods=["POST"])
    def move_repair_repair_current():
        """检测当前工作目录与 thread cwd 匹配关系，提供修复建议。"""
        try:
            mgr = _move_repair_manager()
            result = mgr.repair_current_thread()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 自动检测 API ───────────────

    @app.route("/api/detect")
    def detect_paths():
        """检测所有路径"""
        try:
            results = detect_all()
            return jsonify(results)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────── 过滤器选项 API ───────────────

    @app.route("/api/filters")
    def get_filters():
        """获取过滤选项（model列表、provider列表）"""
        try:
            models = db.get_model_list()
            providers = db.get_provider_list()
            return jsonify({"models": models, "providers": providers})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/health")
    def health():
        """轻量健康检查，供前端和打包后排障使用。"""
        settings = config.get_all()
        db_path = settings.get("db_path", "")
        sessions_dir = settings.get("sessions_dir", "")
        return jsonify({
            "ok": True,
            "desktop_mode": os.environ.get("CODEX_ENHANCE_MANAGER_DESKTOP") == "1",
            "desktop_port": os.environ.get("CODEX_ENHANCE_MANAGER_PORT", ""),
            "db_path_configured": bool(db_path),
            "db_path_exists": bool(db_path and os.path.exists(db_path)),
            "sessions_dir_configured": bool(sessions_dir),
            "sessions_dir_exists": bool(sessions_dir and os.path.isdir(sessions_dir)),
            "auto_backup": bool(settings.get("auto_backup")),
        })

    # ─────────────── 诊断 API ───────────────

    @app.route("/api/diagnostics")
    def get_diagnostics():
        """
        获取完整诊断信息。

        设计意图：
          - 默认返回脱敏数据，防止用户截图或分享时泄露 api_key。
          - 加 ?include_secrets=1 可返回完整版（需管理员权限前端校验）。
        """
        try:
            include_secrets = request.args.get("include_secrets", "0") == "1"
            if include_secrets:
                data = diagnostics_collector.collect_all()
            else:
                data = diagnostics_collector.collect_redacted()
            return jsonify(data)
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/api/diagnostics/export", methods=["POST"])
    def export_diagnostics():
        """
        导出安全诊断包（JSON 下载）。

        工程权衡：
          - 使用 POST 而非 GET：避免浏览器预加载或缓存意外触发下载。
          - 返回 Content-Type: application/json，前端可用 Blob + URL.createObjectURL
            模拟下载，无需后端发送 attachment 头。
        """
        try:
            bundle = diagnostics_collector.export_safe_bundle()
            return bundle, 200, {"Content-Type": "application/json; charset=utf-8"}
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics.export", str(e))
            return jsonify({"error": str(e)}), 500

    def _provider_connectivity_result(provider_id: str) -> Dict[str, Any]:
        _refresh_provider_registry_path()
        return diagnostics_collector.check_provider_connectivity(provider_id)

    def _provider_draft_connectivity_result(provider_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _refresh_provider_registry_path()
        existing = provider_registry.get_provider(provider_id, include_secrets=True)
        if not existing:
            return {
                "success": False,
                "reachable": False,
                "provider_id": provider_id,
                "error": f"Provider not found: {provider_id}",
                "preview": True,
            }
        draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
        provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
        return diagnostics_collector.check_provider_payload_connectivity(provider)

    def _provider_draft_request_preview(provider_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _refresh_provider_registry_path()
        existing = provider_registry.get_provider(provider_id, include_secrets=True)
        if not existing:
            return {
                "success": False,
                "provider_id": provider_id,
                "error": f"Provider not found: {provider_id}",
                "preview": True,
            }
        draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
        provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
        request_json = body.get("request") if isinstance(body.get("request"), dict) else {}
        requested_model = _request_preview_model(provider, body, request_json)
        upstream_model = _extract_model_id_for_upstream({"model": requested_model}, provider) if requested_model else ""
        return {
            "success": True,
            "preview": True,
            "network_request": False,
            "uses_real_proxy_headers": True,
            "provider_id": str(provider.get("id") or provider_id),
            "base_url": str(provider.get("base_url") or ""),
            "requested_model": requested_model,
            "upstream_model": upstream_model,
            "api_format": _route_api_format(provider, upstream_model),
            "headers": _redact_request_preview_headers(_build_upstream_headers(provider)),
            "route_explanation": _request_preview_route_explanation(requested_model, upstream_model, provider),
        }

    def _provider_draft_models_fetch(provider_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        _refresh_provider_registry_path()
        existing = provider_registry.get_provider(provider_id, include_secrets=True)
        if not existing:
            return {
                "success": False,
                "provider_id": provider_id,
                "error": f"Provider not found: {provider_id}",
                "preview": True,
            }
        draft = body.get("provider") if isinstance(body.get("provider"), dict) else body
        provider = merge_provider_update(existing, draft if isinstance(draft, dict) else {})
        return fetch_provider_models_preview(provider)

    @app.route("/api/providers/<provider_id>/health-check", methods=["POST"])
    def provider_health_check(provider_id):
        """
        Provider-scoped network health check.

        This is a section-local test for the Providers page. It performs the
        same low-risk HEAD probe as diagnostics, but returns 200 even when the
        provider is unreachable so the UI can render the structured result.
        """
        try:
            return jsonify(_provider_connectivity_result(provider_id))
        except Exception as e:
            diagnostics_collector.record_error("api.providers.health_check", str(e))
            return jsonify({"success": False, "reachable": False, "provider_id": provider_id, "error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/health-check-draft", methods=["POST"])
    def provider_health_check_draft(provider_id):
        """Run the provider network health check against the current form draft."""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(_provider_draft_connectivity_result(provider_id, body))
        except Exception as e:
            diagnostics_collector.record_error("api.providers.health_check_draft", str(e))
            return jsonify({"success": False, "reachable": False, "provider_id": provider_id, "error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/request-preview-draft", methods=["POST"])
    def provider_request_preview_draft(provider_id):
        """Preview the real proxy route and headers for the current provider draft."""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(_provider_draft_request_preview(provider_id, body))
        except Exception as e:
            diagnostics_collector.record_error("api.providers.request_preview_draft", str(e))
            return jsonify({"success": False, "provider_id": provider_id, "preview": True, "error": str(e)}), 500

    @app.route("/api/providers/<provider_id>/models/fetch-draft", methods=["POST"])
    def provider_models_fetch_draft(provider_id):
        """Fetch an OpenAI-compatible /models list from the current provider form draft without saving."""
        try:
            body = request.get_json(silent=True) or {}
            return jsonify(_provider_draft_models_fetch(provider_id, body))
        except Exception as e:
            diagnostics_collector.record_error("api.providers.models_fetch_draft", str(e))
            return jsonify({"success": False, "provider_id": provider_id, "preview": True, "error": str(e)}), 500

    @app.route("/api/diagnostics/test-provider/<provider_id>", methods=["POST"])
    def test_provider_connectivity(provider_id):
        """
        测试单个 provider 的网络连通性。

        设计意图：
          - 与 /api/providers/<id>/test 区分：后者只做本地配置校验，
            本端点做真实网络探测（HEAD 请求）。
        """
        try:
            result = _provider_connectivity_result(provider_id)
            status_code = 200 if result.get("success") else 503
            return jsonify(result), status_code
        except Exception as e:
            diagnostics_collector.record_error("api.diagnostics.test_provider", str(e))
            return jsonify({"error": str(e)}), 500

    @app.route("/api/diagnostics/system")
    def get_diagnostics_system():
        """返回系统环境信息（轻量子集，供快速排障）。"""
        try:
            data = diagnostics_collector.collect_all()
            return jsonify({"system": data.get("system", {})})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def fetch_provider_models_preview(provider: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch and merge an OpenAI-compatible model list without saving provider state."""
    provider_id = str(provider.get("id") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    if not base_url:
        return {
            "success": False,
            "preview": True,
            "provider_id": provider_id,
            "error": "Provider base_url is empty.",
        }

    url = models_url(base_url)
    headers = _build_upstream_headers(provider)
    headers.setdefault("Accept", "application/json")
    request_obj = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request_obj, timeout=MODEL_LIST_FETCH_TIMEOUT_SECONDS) as response:
            raw_body = response.read()
            status_code = int(response.getcode() or 200)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read() if hasattr(exc, "read") else b""
        return {
            "success": False,
            "preview": True,
            "provider_id": provider_id,
            "url": url,
            "status_code": int(exc.code or 0),
            "error": f"HTTP {exc.code}",
            "body_preview": _body_preview(raw_body),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "success": False,
            "preview": True,
            "provider_id": provider_id,
            "url": url,
            "error": f"Model list request failed: {exc}",
        }

    try:
        payload = json.loads(raw_body.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "preview": True,
            "provider_id": provider_id,
            "url": url,
            "status_code": status_code,
            "error": f"Model list response is not JSON: {exc}",
            "body_preview": _body_preview(raw_body),
        }

    fetched_models = parse_openai_compatible_model_list(payload)
    if not fetched_models:
        return {
            "success": False,
            "preview": True,
            "provider_id": provider_id,
            "url": url,
            "status_code": status_code,
            "error": "No model ids were found in the response.",
            "response_shape": _model_list_response_shape(payload),
        }

    merged_models, added_count, updated_count = merge_fetched_provider_models(
        provider.get("models") if isinstance(provider.get("models"), list) else [],
        fetched_models,
    )
    return {
        "success": True,
        "preview": True,
        "provider_id": provider_id,
        "url": url,
        "status_code": status_code,
        "fetched_count": len(fetched_models),
        "added_count": added_count,
        "updated_count": updated_count,
        "existing_count": len(provider.get("models") if isinstance(provider.get("models"), list) else []),
        "models": fetched_models,
        "merged_models": merged_models,
        "default_context_window": FETCHED_MODEL_DEFAULT_CONTEXT_WINDOW,
    }


def parse_openai_compatible_model_list(payload: Any) -> list[Dict[str, Any]]:
    items = _model_list_items(payload)
    models: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        model = _model_from_list_item(item)
        model_id = str(model.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(normalize_model(model))
    return models


def _model_list_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "models", "model_list", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    nested = payload.get("result")
    if isinstance(nested, dict):
        return _model_list_items(nested)
    return []


def _model_from_list_item(item: Any) -> Dict[str, Any]:
    if isinstance(item, str):
        return _fetched_model_defaults(item, item, context_window=0, max_output_tokens=0, raw={})
    if not isinstance(item, dict):
        return {}
    model_id = str(
        item.get("id")
        or item.get("model")
        or item.get("name")
        or item.get("model_id")
        or ""
    ).strip()
    display_name = str(item.get("display_name") or item.get("displayName") or item.get("name") or model_id).strip()
    return _fetched_model_defaults(
        model_id,
        display_name or model_id,
        context_window=_first_positive_int(
            item,
            (
                "context_window",
                "contextWindow",
                "context_length",
                "contextLength",
                "max_context",
                "max_context_length",
                "max_input_tokens",
                "maxInputTokens",
            ),
        ),
        max_output_tokens=_first_positive_int(
            item,
            (
                "max_output_tokens",
                "maxOutputTokens",
                "max_tokens",
                "maxTokens",
                "max_completion_tokens",
                "output_token_limit",
            ),
        ),
        raw=item,
    )


def _fetched_model_defaults(
    model_id: str,
    display_name: str,
    *,
    context_window: int,
    max_output_tokens: int,
    raw: Dict[str, Any],
) -> Dict[str, Any]:
    capabilities = _capabilities_from_model_list_item(raw)
    resolved_context = context_window or FETCHED_MODEL_DEFAULT_CONTEXT_WINDOW
    model = {
        "id": model_id,
        "display_name": display_name or model_id,
        "enabled": True,
        "selected": False,
        "catalog_hidden": True,
        "primary": False,
        "context_window": resolved_context,
        "context_window_source": "provider" if context_window else "default_200k",
        "capabilities": capabilities,
        "capability_overrides": capabilities,
    }
    if max_output_tokens:
        model["max_output_tokens"] = max_output_tokens
    return model


def _capabilities_from_model_list_item(item: Dict[str, Any]) -> Dict[str, bool]:
    capabilities = {
        "text": True,
        "vision": False,
        "tools": True,
        "streaming": True,
        "reasoning": False,
        "custom_tools": False,
        "images": False,
        "videos": False,
    }
    raw_caps = item.get("capabilities")
    if isinstance(raw_caps, dict):
        for key, value in raw_caps.items():
            key_text = str(key).strip()
            if key_text:
                capabilities[key_text] = bool(value)
    for token in _model_list_capability_tokens(item):
        if token in {"image", "images", "vision", "visual"}:
            capabilities["vision"] = True
        elif token in {"video", "videos"}:
            capabilities["videos"] = True
        elif token in {"tool", "tools", "function", "function_call", "function_calling"}:
            capabilities["tools"] = True
        elif token in {"reasoning", "thinking"}:
            capabilities["reasoning"] = True
        elif token in {"text", "chat", "completion"}:
            capabilities["text"] = True
    return capabilities


def _model_list_capability_tokens(item: Dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("input", "inputs", "modalities", "input_modalities", "supported_modalities"):
        value = item.get(key)
        if isinstance(value, list):
            tokens.update(str(part).strip().lower() for part in value if str(part).strip())
    architecture = item.get("architecture")
    if isinstance(architecture, dict):
        for key in ("input_modalities", "modality", "modalities"):
            value = architecture.get(key)
            if isinstance(value, list):
                tokens.update(str(part).strip().lower() for part in value if str(part).strip())
            elif isinstance(value, str):
                tokens.add(value.strip().lower())
    return tokens


def _first_positive_int(data: Dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("tokens") or value.get("value")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def merge_fetched_provider_models(
    existing_models: list[Dict[str, Any]],
    fetched_models: list[Dict[str, Any]],
) -> tuple[list[Dict[str, Any]], int, int]:
    merged: list[Dict[str, Any]] = [json.loads(json.dumps(model)) for model in existing_models if isinstance(model, dict)]
    index_by_id = {
        str(model.get("id") or "").strip(): idx
        for idx, model in enumerate(merged)
        if str(model.get("id") or "").strip()
    }
    added_count = 0
    updated_count = 0
    for fetched in fetched_models:
        model_id = str(fetched.get("id") or "").strip()
        if not model_id:
            continue
        if model_id not in index_by_id:
            merged.append(json.loads(json.dumps(fetched)))
            index_by_id[model_id] = len(merged) - 1
            added_count += 1
            continue
        target = merged[index_by_id[model_id]]
        changed = False
        if not int(target.get("context_window") or 0):
            target["context_window"] = int(fetched.get("context_window") or FETCHED_MODEL_DEFAULT_CONTEXT_WINDOW)
            target["context_window_source"] = fetched.get("context_window_source") or "provider_or_default"
            changed = True
        if not int(target.get("max_output_tokens") or 0) and int(fetched.get("max_output_tokens") or 0):
            target["max_output_tokens"] = int(fetched.get("max_output_tokens") or 0)
            changed = True
        if not str(target.get("display_name") or "").strip():
            target["display_name"] = fetched.get("display_name") or model_id
            changed = True
        if "catalog_hidden" not in target:
            target["catalog_hidden"] = not bool(target.get("selected", False))
            changed = True
        if "primary" not in target:
            target["primary"] = False
            changed = True
        if changed:
            updated_count += 1
    return merged, added_count, updated_count


def _model_list_response_shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "array"
    if isinstance(payload, dict):
        keys = ", ".join(sorted(str(key) for key in payload.keys())[:12])
        return f"object keys: {keys}"
    return type(payload).__name__


def _body_preview(raw_body: bytes, limit: int = 1000) -> str:
    text = raw_body.decode("utf-8", errors="replace")
    return text[:limit]


def _selected_provider_models_to_amr_candidates(provider: Dict[str, Any], priority: int) -> list[Dict[str, Any]]:
    if not provider_allows_local_routing(provider):
        return []
    provider_id = str(provider.get("id") or "").strip()
    candidates: list[Dict[str, Any]] = []
    for model in provider.get("models", []):
        if not isinstance(model, dict):
            continue
        if not model.get("enabled", True) or not model.get("selected", False):
            continue
        model_id = str(model.get("id") or "").strip()
        if not provider_id or not model_id:
            continue
        candidates.append({
            "id": f"{provider_id}/{model_id}",
            "provider_id": provider_id,
            "model_id": model_id,
            "priority": int(priority),
            "enabled": True,
            "context_window": _clamp_int(model.get("context_window", 0), 0, 0, 10_000_000),
            "capabilities": merge_provider_model_capabilities(provider, model),
        })
    return candidates


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _normalize_route_capabilities(body: Dict[str, Any]) -> set[str]:
    raw = body.get("capabilities")
    if raw is None:
        raw = body.get("capability", "text")
    if isinstance(raw, str):
        parts = [item.strip().lower() for item in re.split(r"[,|\s]+", raw) if item.strip()]
    elif isinstance(raw, list):
        parts = [str(item).strip().lower() for item in raw if str(item).strip()]
    else:
        parts = []
    allowed = {"text", "vision", "tools", "custom_tools", "reasoning", "images", "videos"}
    capabilities = {item for item in parts if item in allowed}
    return capabilities or {"text"}


def _route_candidate_matches_model(candidate: Dict[str, Any], model_filter: str) -> bool:
    needle = str(model_filter or "").strip().lower()
    if not needle:
        return True
    aliases = {
        str(candidate.get("id") or ""),
        str(candidate.get("model_id") or ""),
        str(candidate.get("codex_model_id") or ""),
        str(candidate.get("display_name") or ""),
        f"{candidate.get('provider_id')}/{candidate.get('model_id')}",
        f"{candidate.get('short_alias')}/{candidate.get('model_id')}",
    }
    return any(needle == alias.lower() for alias in aliases if alias)


def _request_preview_model(provider: Dict[str, Any], body: Dict[str, Any], request_json: Dict[str, Any]) -> str:
    for value in (request_json.get("model"), body.get("model")):
        text = str(value or "").strip()
        if text:
            return text
    models = provider.get("models") if isinstance(provider.get("models"), list) else []
    for selected_only in (True, False):
        for model in models:
            if not isinstance(model, dict):
                continue
            if model.get("enabled", True) is False:
                continue
            if selected_only and not model.get("selected", False):
                continue
            model_id = str(model.get("id") or "").strip()
            if model_id:
                return model_id
    for value in (provider.get("default_model"), provider.get("model")):
        text = str(value or "").strip()
        if text:
            return text
    aliases = _provider_alias_map(provider)
    for alias in aliases.keys():
        text = str(alias or "").strip()
        if text:
            return text
    return ""


def _redact_request_preview_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    redacted: Dict[str, str] = {}
    for key, value in headers.items():
        name = str(key)
        text = str(value)
        lower_name = name.lower()
        lower_value = text.lower()
        if (
            lower_name in {"authorization", "proxy-authorization", "x-api-key", "api-key", "apikey", "api_key"}
            or any(part in lower_name for part in ("token", "secret", "password"))
            or lower_value.startswith("bearer ")
            or lower_value.startswith(("sk-", "sk_"))
        ):
            redacted[name] = "********"
        else:
            redacted[name] = text
    return redacted


def _request_preview_route_explanation(requested_model: str, upstream_model: str, provider: Dict[str, Any]) -> list[str]:
    if not requested_model:
        return ["No model is selected yet; choose or add a model before sending requests.", "Preview only; no provider request was sent."]

    explanation: list[str] = []
    requested_upstream = requested_model
    if "/" in requested_model:
        _prefix, requested_upstream = requested_model.split("/", 1)
        explanation.append(f"Provider prefix removed: {requested_model} -> {requested_upstream}.")

    aliases = _provider_alias_map(provider)
    matched = False
    if requested_upstream in aliases and aliases[requested_upstream] == upstream_model:
        explanation.append(f"Exact model alias applied: {requested_upstream} -> {upstream_model}.")
        matched = True
    else:
        lowered = requested_upstream.lower().strip()
        for source, target in aliases.items():
            if source.lower().strip() == lowered and target == upstream_model:
                explanation.append(f"Case-insensitive model alias applied: {requested_upstream} -> {upstream_model}.")
                matched = True
                break

    if not matched:
        for pattern in _provider_alias_patterns(provider):
            try:
                if re.search(pattern["pattern"], requested_upstream):
                    rewritten = re.sub(pattern["pattern"], pattern["replacement"], requested_upstream, count=1)
                    if rewritten == upstream_model:
                        explanation.append(f"Regex model mapping applied: {pattern['pattern']} -> {upstream_model}.")
                        matched = True
                        break
            except re.error:
                continue

    if not matched:
        if upstream_model and upstream_model != requested_upstream:
            explanation.append(f"Model mapping applied: {requested_upstream} -> {upstream_model}.")
        else:
            explanation.append("Model is forwarded unchanged.")
    explanation.append("Preview only; no provider request was sent.")
    return explanation


def _route_candidate_status(
    candidates: list[Dict[str, Any]],
    required_capabilities: set[str],
    required_context: int,
    model_filter: str = "",
) -> list[Dict[str, Any]]:
    rows = []
    for candidate in candidates:
        caps = candidate.get("capabilities") if isinstance(candidate.get("capabilities"), dict) else {}
        missing = sorted(cap for cap in required_capabilities if not caps.get(cap, False))
        context_window = _safe_int(candidate.get("context_window"))
        model_match = _route_candidate_matches_model(candidate, model_filter)
        context_match = required_context <= 0 or context_window >= required_context
        rows.append({
            "candidate_id": candidate.get("id", ""),
            "provider_id": candidate.get("provider_id", ""),
            "model_id": candidate.get("model_id", ""),
            "codex_model_id": candidate.get("codex_model_id", ""),
            "priority": candidate.get("priority", 100),
            "context_window": context_window,
            "capabilities": caps,
            "missing_capabilities": missing,
            "capability_match": not missing,
            "context_match": context_match,
            "model_match": model_match,
            "available": not missing and context_match and model_match,
        })
    return rows


def _merge_cache_usage_sources(
    data: Dict[str, Any],
    rollout_cache_data: Dict[str, Any] | None,
    cc_cache_data: Dict[str, Any] | None,
    use_rollout_total: bool = False,
) -> None:
    rollout_cache_data = rollout_cache_data or {}
    cc_cache_data = cc_cache_data or {}

    rollout_events = _safe_int(rollout_cache_data.get("rollout_token_count_events"))
    rollout_input = _safe_int(rollout_cache_data.get("input_tokens"))
    rollout_output = _safe_int(rollout_cache_data.get("output_tokens"))
    rollout_reasoning = _safe_int(rollout_cache_data.get("reasoning_tokens"))
    rollout_total = _safe_int(rollout_cache_data.get("total_tokens"))
    rollout_read = _safe_int(rollout_cache_data.get("cache_read_tokens"))
    rollout_creation = _safe_int(rollout_cache_data.get("cache_creation_tokens"))
    cc_read = _safe_int(cc_cache_data.get("cache_read_tokens"))
    cc_creation = _safe_int(cc_cache_data.get("cache_creation_tokens"))

    data["codex_db_total_tokens"] = _safe_int(data.get("total_tokens"))
    data["codex_rollout_usage_supported"] = rollout_events > 0 and rollout_total > 0
    data["codex_rollout_input_tokens"] = rollout_input
    data["codex_rollout_output_tokens"] = rollout_output
    data["codex_rollout_reasoning_tokens"] = rollout_reasoning
    data["codex_rollout_total_tokens"] = rollout_total
    data["codex_rollout_latest_context_window"] = _safe_int(rollout_cache_data.get("latest_context_window"))
    data["codex_rollout_latest_context_used_tokens"] = _safe_int(rollout_cache_data.get("latest_context_used_tokens"))
    data["codex_rollout_latest_context_at"] = rollout_cache_data.get("latest_context_at")
    if use_rollout_total and data["codex_rollout_usage_supported"]:
        data["input_tokens"] = rollout_input
        data["output_tokens"] = rollout_output
        data["reasoning_tokens"] = rollout_reasoning
        data["total_tokens"] = rollout_total
        data["data_source"] = "codex_rollout"
        data["realtime_note"] = "Codex rollout token_count events are used as the usage source of truth when available."

    data["codex_rollout_cache_supported"] = bool(rollout_cache_data.get("cache_supported"))
    data["codex_rollout_cache_read_tokens"] = rollout_read
    data["codex_rollout_cache_creation_tokens"] = rollout_creation
    data["codex_rollout_cache_total_tokens"] = rollout_read + rollout_creation
    data["codex_rollout_files_scanned"] = _safe_int(rollout_cache_data.get("rollout_files_scanned"))
    data["codex_rollout_paths_discovered"] = _safe_int(rollout_cache_data.get("rollout_paths_discovered"))
    data["codex_rollout_token_count_events"] = rollout_events
    data["codex_rollout_cache_field_events"] = _safe_int(rollout_cache_data.get("rollout_cache_field_events"))
    data["codex_rollout_usage_sources"] = rollout_cache_data.get("rollout_usage_sources", [])
    data["codex_rollout_cache_note"] = rollout_cache_data.get("cache_note", "")

    data["cc_switch_cache_supported"] = bool(cc_cache_data.get("cache_supported"))
    data["cc_switch_cache_read_tokens"] = cc_read
    data["cc_switch_cache_creation_tokens"] = cc_creation
    data["cc_switch_cache_total_tokens"] = cc_read + cc_creation
    data["cc_switch_cache_tables"] = cc_cache_data.get("cache_tables", [])
    data["cc_switch_cache_note"] = cc_cache_data.get("cache_note", "")
    data["cc_switch_cache_strategy"] = cc_cache_data.get("cache_strategy", "")
    data["cc_switch_cache_rollup_used"] = bool(cc_cache_data.get("cache_rollup_used"))

    data["cache_supported"] = data["codex_rollout_cache_supported"] or data["cc_switch_cache_supported"]
    data["cache_tables"] = data["cc_switch_cache_tables"]
    data["cache_sources"] = []
    data["cache_overlap_risk"] = False
    data["cache_merge_strategy"] = "none"

    if data["codex_rollout_cache_supported"]:
        data["cache_sources"].append("codex_rollout")
    if data["cc_switch_cache_supported"]:
        data["cache_sources"].append("cc_switch_db")

    if data["codex_rollout_cache_supported"]:
        data["cache_read_tokens"] = rollout_read
        data["cache_creation_tokens"] = rollout_creation
        data["cache_merge_strategy"] = "codex_rollout_primary"
        if data["cc_switch_cache_supported"]:
            data["cache_overlap_risk"] = True
            data["cache_merge_strategy"] = "codex_rollout_primary_cc_switch_separate"
    elif data["cc_switch_cache_supported"]:
        data["cache_read_tokens"] = cc_read
        data["cache_creation_tokens"] = cc_creation
        data["cache_merge_strategy"] = "cc_switch_db"
    else:
        data["cache_read_tokens"] = 0
        data["cache_creation_tokens"] = 0

    data["cache_total_tokens"] = data["cache_read_tokens"] + data["cache_creation_tokens"]

    notes = [
        note
        for note in (
            data.get("codex_rollout_cache_note"),
            data.get("cc_switch_cache_note"),
        )
        if note
    ]
    if not notes and not cc_cache_data:
        notes.append("No proxy cache database is configured.")
    data["cache_note"] = " ".join(notes)


def _merge_local_proxy_request_log_usage(data: Dict[str, Any], request_log_summary: Dict[str, Any] | None) -> None:
    summary = request_log_summary if isinstance(request_log_summary, dict) else {}
    tokens = summary.get("tokens") if isinstance(summary.get("tokens"), dict) else {}
    proxy_total = _safe_int(tokens.get("total_tokens"))
    proxy_input = _safe_int(tokens.get("input_tokens"))
    proxy_output = _safe_int(tokens.get("output_tokens"))
    proxy_reasoning = _safe_int(tokens.get("reasoning_tokens"))
    proxy_cache_read = _safe_int(tokens.get("cache_read_tokens"))
    proxy_cache_creation = _safe_int(tokens.get("cache_creation_tokens"))
    proxy_cache_total = proxy_cache_read + proxy_cache_creation

    data["local_proxy_request_log"] = summary
    data["local_proxy_request_log_exists"] = bool(summary.get("exists"))
    data["local_proxy_request_count"] = _safe_int(summary.get("count"))
    data["local_proxy_success_count"] = _safe_int(summary.get("success_count"))
    data["local_proxy_error_count"] = _safe_int(summary.get("error_count"))
    data["local_proxy_latest_timestamp"] = summary.get("latest_timestamp") or ""
    data["local_proxy_total_tokens"] = proxy_total
    data["local_proxy_input_tokens"] = proxy_input
    data["local_proxy_output_tokens"] = proxy_output
    data["local_proxy_reasoning_tokens"] = proxy_reasoning
    data["local_proxy_cache_read_tokens"] = proxy_cache_read
    data["local_proxy_cache_creation_tokens"] = proxy_cache_creation
    data["local_proxy_cache_total_tokens"] = proxy_cache_total
    data["local_proxy_cache_supported"] = proxy_cache_total > 0

    if proxy_cache_total > 0:
        cache_sources = data.setdefault("cache_sources", [])
        if "local_proxy_request_log" not in cache_sources:
            cache_sources.append("local_proxy_request_log")
        if not data.get("cache_supported"):
            data["cache_supported"] = True
            data["cache_read_tokens"] = proxy_cache_read
            data["cache_creation_tokens"] = proxy_cache_creation
            data["cache_total_tokens"] = proxy_cache_total
            data["cache_merge_strategy"] = "local_proxy_request_log"
        else:
            data["cache_overlap_risk"] = True

    if _safe_int(data.get("total_tokens")) <= 0 and proxy_total > 0:
        data["input_tokens"] = proxy_input
        data["output_tokens"] = proxy_output
        data["reasoning_tokens"] = proxy_reasoning
        data["total_tokens"] = proxy_total
        data["data_source"] = "local_proxy_request_log"
        data["realtime_note"] = "Local proxy request logs are used because Codex DB and rollout usage have no token total."


def _attach_usage_source_summary(
    data: Dict[str, Any],
    proxy_status: Dict[str, Any] | None = None,
    request_log_summary: Dict[str, Any] | None = None,
) -> None:
    proxy_status = proxy_status or {}
    request_log_summary = request_log_summary if isinstance(request_log_summary, dict) else {}
    rollout_discovered = _safe_int(data.get("codex_rollout_paths_discovered"))
    rollout_scanned = _safe_int(data.get("codex_rollout_files_scanned"))
    cc_configured = bool(data.get("cc_switch_db_configured"))
    cc_supported = bool(data.get("cc_switch_cache_supported"))
    cc_strategy = str(data.get("cc_switch_cache_strategy") or "")
    proxy_running = bool(proxy_status.get("running"))
    proxy_log_exists = bool(request_log_summary.get("exists"))
    proxy_log_count = _safe_int(request_log_summary.get("count"))
    proxy_log_tokens = _safe_int(
        (request_log_summary.get("tokens") if isinstance(request_log_summary.get("tokens"), dict) else {}).get("total_tokens")
    )
    proxy_log_success = _safe_int(request_log_summary.get("success_count"))
    proxy_log_errors = _safe_int(request_log_summary.get("error_count"))
    if proxy_log_count:
        proxy_status_label = "active"
    elif proxy_running:
        proxy_status_label = "running"
    elif proxy_log_exists:
        proxy_status_label = "empty"
    else:
        proxy_status_label = "stopped"

    sources = [
        {
            "id": "codex_db",
            "label": "Codex DB",
            "badge": "Codex DB",
            "status": "active",
            "active": True,
            "kind": "total_tokens",
            "tooltip": (
                "Codex DB threads.tokens_used remains the compatibility fallback for total tokens; "
                "cache read/write details require Codex rollout or proxy/CC Switch sources."
            ),
        },
        {
            "id": "codex_rollout",
            "label": "Codex rollout",
            "badge": "rollout",
            "status": "active" if data.get("codex_rollout_usage_supported") or data.get("codex_rollout_cache_supported") else (
                "available" if rollout_discovered else "missing"
            ),
            "active": bool(data.get("codex_rollout_usage_supported") or data.get("codex_rollout_cache_supported")),
            "kind": "codex_output_usage",
            "tooltip": (
                f"Scanned {rollout_scanned} of {rollout_discovered} discovered rollout files. "
                "Reads token_count events for total, input/output, cache, and context usage when available."
            ),
        },
        {
            "id": "local_proxy",
            "label": "Local proxy",
            "badge": "local proxy",
            "status": proxy_status_label,
            "active": proxy_running or proxy_log_count > 0,
            "kind": "proxy_request_log",
            "tooltip": (
                f"Request log has {proxy_log_count} routed requests "
                f"({proxy_log_success} success, {proxy_log_errors} errors) and {proxy_log_tokens} tokens. "
                f"Proxy runtime is {'running' if proxy_running else 'stopped'}."
            ),
        },
        {
            "id": "cc_switch_db",
            "label": "CC Switch DB",
            "badge": "CC Switch DB",
            "status": "active" if cc_supported else ("configured" if cc_configured else "not_configured"),
            "active": cc_supported,
            "kind": "cache_tokens",
            "tooltip": (
                (
                    f"{data.get('cc_switch_cache_note')} Strategy: {cc_strategy}."
                    if cc_strategy
                    else data.get("cc_switch_cache_note")
                )
                or "Configure a proxy cache database to read cache_read_tokens/cache_creation_tokens."
            ),
        },
    ]
    data["usage_sources"] = sources
    data["usage_source_badges"] = [
        {
            "id": source["id"],
            "label": source["badge"],
            "status": source["status"],
            "active": source["active"],
            "tooltip": source["tooltip"],
        }
        for source in sources
    ]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_export_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(title or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "session")[:80]


def _resolve_backup_path(backup_id: str, backup_dir: Path) -> Path | None:
    if not backup_id or "/" in backup_id or "\\" in backup_id or backup_id.startswith("."):
        return None
    base = backup_dir.resolve()
    candidate = (base / backup_id).resolve()
    if candidate.parent != base:
        return None
    return candidate


def _sync_stats_to_dict(stats) -> Dict:
    return {
        "db_threads_seen": stats.db_threads_seen,
        "db_threads_updated": stats.db_threads_updated,
        "rollout_files_seen": stats.rollout_files_seen,
        "rollout_files_updated": stats.rollout_files_updated,
        "index_rows_seen": stats.index_rows_seen,
        "index_rows_updated": stats.index_rows_updated,
        "malformed_lines": stats.malformed_lines,
        "errors": stats.errors,
        "changed": stats.changed,
    }


def _run_sync_with_backup(
    backup_mgr: BackupManager,
    path_config: Dict[str, str] | None = None,
    target_provider: str = "",
    target_model: str = "",
    backup_before: bool = False,
) -> tuple[Dict, int]:
    path_config = path_config or {}
    preview = full_sync(
        target_provider=target_provider,
        target_model=target_model,
        dry_run=True,
        **path_config,
    )
    if not preview.changed:
        payload = _sync_stats_to_dict(preview)
        payload["backup_path"] = ""
        payload["skipped_backup"] = True
        payload["backup_before_sync"] = bool(backup_before)
        return payload, 200

    pre_backup = {}
    if backup_before:
        pre_backup = backup_mgr.do_full_backup(label="pre_sync")
        if not pre_backup.get("success"):
            return {"error": f"同步前数据库备份失败: {pre_backup.get('error', 'unknown')}"}, 500

    stats = full_sync(
        target_provider=target_provider,
        target_model=target_model,
        dry_run=False,
        **path_config,
    )
    payload = _sync_stats_to_dict(stats)
    payload["backup_path"] = pre_backup.get("path", "")
    payload["skipped_backup"] = not bool(backup_before)
    payload["backup_before_sync"] = bool(backup_before)
    return payload, 200


def _find_file_for_thread(thread: Dict, config: Config) -> str:
    """根据 thread_id、rollout_path 和 JSONL 元数据推断文件路径。"""
    import glob as glob_module
    tid = thread.get("id", "")
    archived = thread.get("archived", 0)
    configured_dirs = [
        config.get("archived_dir") if archived else config.get("sessions_dir"),
        config.get("sessions_dir"),
        config.get("archived_dir"),
    ]

    for key in ("rollout_path", "jsonl_path", "file_path", "path"):
        candidate = str(thread.get(key) or "").strip()
        if candidate and os.path.exists(candidate):
            return candidate

    base_dirs = []
    seen_dirs = set()
    for raw in configured_dirs:
        if not raw:
            continue
        expanded = os.path.expandvars(str(raw))
        key = expanded.lower()
        if key not in seen_dirs and os.path.isdir(expanded):
            base_dirs.append(expanded)
            seen_dirs.add(key)

    for base_dir in base_dirs:
        patterns = [
            os.path.join(base_dir, f"*{tid}*.jsonl"),
            os.path.join(base_dir, "**", f"*{tid}*.jsonl"),
        ]
        for pat in patterns:
            files = glob_module.glob(pat, recursive=True)
            if files:
                return files[0]

    try:
        mgr = MoveRepairManager(
            db_path=config.get("db_path", ""),
            sessions_dir=config.get("sessions_dir", ""),
            archived_dir=config.get("archived_dir", ""),
        )
        found = mgr.find_jsonl_for_thread(tid)
        if found and found.exists():
            return str(found)
    except Exception:
        pass
    return ""


def _cleanup_targets(config: Config) -> list[Dict]:
    """Build allowlisted cleanup targets with size estimates."""
    root = app_data_dir()
    configured = config.get_all()
    candidates = [
        (
            "temp",
            Path(configured.get("temp_dir") or root / "temp"),
            root,
            "Temporary app files",
            "Removes files created by the app for short-lived local work.",
        ),
        (
            "diagnostics",
            Path(configured.get("diagnostics_dir") or root / "diagnostics"),
            root,
            "Diagnostics exports",
            "Removes redacted diagnostics bundles generated by this app.",
        ),
        (
            "exports",
            Path(configured.get("exports_dir") or root / "exports"),
            root,
            "User exports",
            "Removes files exported through this app.",
        ),
        (
            "repo_pycache",
            Path.cwd() / "__pycache__",
            Path.cwd(),
            "Repository Python cache",
            "Removes generated __pycache__ files in the repository root.",
        ),
        (
            "repo_pytest_cache",
            Path.cwd() / ".pytest_cache",
            Path.cwd(),
            "Pytest cache",
            "Removes local pytest cache files from this repository.",
        ),
        (
            "tests_pycache",
            Path.cwd() / "tests" / "__pycache__",
            Path.cwd(),
            "Tests Python cache",
            "Removes generated __pycache__ files under tests.",
        ),
    ]
    targets = []
    for target_id, path, safe_root, description, effect in candidates:
        resolved = path.expanduser()
        safe = is_within(resolved, safe_root)
        targets.append(_cleanup_target_descriptor(target_id, resolved, safe, description, effect))
    return targets


def _uninstall_cleanup_targets(config: Config) -> list[Dict]:
    """Build app-owned uninstall cleanup targets.

    This intentionally excludes ~/.codex auth/config and any user-custom path
    outside the app-owned Documents folder.
    """
    root = app_data_dir()
    targets = [
        _cleanup_target_descriptor(
            "app_data_dir",
            root,
            safe=True,
            description="Documents app data directory",
            effect="Removes settings, provider registry, exports, diagnostics, temp files, and app backups.",
        ),
        _cleanup_target_descriptor(
            "legacy_config_file",
            LEGACY_CONFIG_FILE,
            safe=LEGACY_CONFIG_FILE == Path.home() / ".codex_gui_config.json",
            description="Legacy settings JSON",
            effect="Removes the old root-level settings file if it still exists.",
        ),
        _cleanup_target_descriptor(
            "legacy_app_dir",
            LEGACY_APP_DIR,
            safe=LEGACY_APP_DIR == Path.home() / ".codex_enhance_manager",
            description="Legacy app data directory",
            effect="Removes the old provider/cache directory if it still exists.",
        ),
    ]

    provider_store_raw = str(config.get("provider_store_path", "") or "").strip()
    provider_store = Path(provider_store_raw).expanduser() if provider_store_raw else None
    if provider_store and provider_store.exists() and not is_within(provider_store, root) and provider_store != LEGACY_APP_DIR / "providers.json":
        targets.append(_cleanup_target_descriptor(
            "external_provider_store",
            provider_store,
            safe=False,
            description="Custom provider registry outside app data",
            effect="Not removed automatically; export or remove it manually if desired.",
        ))
    return targets


def _cleanup_target_descriptor(target_id: str, path: Path, safe: bool, description: str, effect: str) -> Dict:
    resolved = path.expanduser()
    exists = resolved.exists()
    return {
        "id": target_id,
        "path": str(resolved),
        "exists": exists,
        "safe": safe,
        "size_bytes": _path_size(resolved) if exists and safe else 0,
        "kind": "directory" if resolved.is_dir() else "file",
        "description": description,
        "effect": effect,
    }


def _cleanup_target(target: Dict) -> Dict:
    path = Path(target["path"])
    if not target.get("safe"):
        return {"id": target["id"], "success": False, "error": "Target is outside cleanup allowlist"}
    if not path.exists():
        return {"id": target["id"], "success": True, "skipped": True, "path": str(path)}
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"id": target["id"], "success": True, "path": str(path)}
    except Exception as exc:
        return {"id": target["id"], "success": False, "path": str(path), "error": str(exc)}


def _path_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _resolve_current_context_window(config: Config, provider_registry: ProviderRegistry) -> Dict:
    """Best-effort current model context window for monitor display."""
    result = {
        "current_model": "",
        "current_model_provider": "",
        "current_context_window": 0,
        "current_context_source": "",
    }
    try:
        codex_home = resolve_codex_home()
        config_data = _load_config_toml(str(codex_home / "config.toml"))
        mgr = CodexConfigManager(codex_home=str(codex_home))
        auth_data = mgr.read_auth()
        effective = _effective_codex_settings(config_data, auth_data)
        model = str(effective.get("model") or "")
        provider = str(effective.get("model_provider") or "")
        result["current_model"] = model
        result["current_model_provider"] = provider
        result["current_model_provider_source"] = effective.get("model_provider_source", "")
        if not model:
            return result

        provider_registry.store_path = Path(config.get("provider_store_path", "") or DEFAULT_STORE_PATH).expanduser()
        preview = provider_registry.preview_catalog(
            focus_provider_id="",
            extra_providers=_official_provider_extra(mgr),
        )
        for entry in preview.get("entries", []):
            if model in {entry.get("codex_model_id"), entry.get("upstream_model_id")}:
                result["current_context_window"] = int(entry.get("context_window") or 0)
                result["current_context_source"] = "provider_registry"
                return result
    except Exception as exc:
        result["current_context_error"] = str(exc)
    return result
