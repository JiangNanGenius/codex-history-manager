"""
config.py - Local JSON settings management.

Settings are stored under the user's Documents/Codex Enhance Manager folder.
The class intentionally stays small and dependency-light because it is used by
both the desktop app and tests.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any, Dict

from app_paths import LEGACY_CONFIG_FILE, app_data_path, ensure_app_dirs
from approval_broker import DEFAULT_AUTO_APPROVAL_SYSTEM_PROMPT, normalize_auto_approval_system_prompt
from local_proxy_auth import generate_local_proxy_bearer_token, local_proxy_token_is_strong
from auto_detect import (
    detect_archived_dir,
    detect_codex_db,
    detect_codex_desktop,
    detect_codex_launch_path,
    detect_codex_plus_plus,
    detect_sessions_dir,
)


LEGACY_DEFAULT_BACKUP_DIR = str(Path.home() / "codex_backups")
LEGACY_DEFAULT_PROVIDER_STORE = str(Path.home() / ".codex_enhance_manager" / "providers.json")

DEFAULT_CONFIG = {
    "db_path": "",
    "sessions_dir": "",
    "archived_dir": "",
    "backup_dir": str(app_data_path("backups")),
    "auto_backup": False,
    "backup_interval_hours": 6,
    "max_backups": 20,
    "page_size": 50,
    "max_lines_large_file": 2000,
    "large_file_threshold_mb": 500,
    "use_codex_plus_plus": False,
    "codex_cli_path": "",
    "codex_plus_plus_path": "",
    "cc_switch_db_path": "",
    "provider_store_path": str(app_data_path("providers", "providers.json")),
    "temp_dir": str(app_data_path("temp")),
    "diagnostics_dir": str(app_data_path("diagnostics")),
    "exports_dir": str(app_data_path("exports")),
    "request_log_path": str(app_data_path("logs", "proxy_requests.jsonl")),
    "request_log_retention_days": 30,
    "request_log_max_mb": 50,
    "close_button_action": "ask",
    "desktop_launch_action": "show_window",
    "desktop_monitor_enabled": True,
    "desktop_monitor_opacity": 88,
    "update_check_enabled": True,
    "update_include_prerelease": False,
    "plugin_unlock_enabled": False,
    "codex_cdp_port": 51236,
    "codex_injection_enabled": True,
    "codex_last_start_mode": "",
    "codex_goals_enabled": True,
    "codex_sandbox_auto_repair_enabled": False,
    "local_proxy_bearer_token": "",
    "proxy_upstream_timeout_seconds": 120,
    "proxy_retry_attempts": 0,
    "proxy_retry_backoff_ms": 250,
    "auto_approval_system_prompt": DEFAULT_AUTO_APPROVAL_SYSTEM_PROMPT,
    "startup_enabled": False,
    "startup_mode": "disabled",
    "startup_auto_elevate": False,
    "startup_task_name": "CodexEnhanceManager",
    "startup_shortcut_name": "CodexEnhanceManager.cmd",
    "startup_target_path": "",
    "startup_arguments": "",
    "proxy_port": 51235,
    "dark_mode": True,
    "theme_preset": "dark",
    "theme_custom": {
        "accent": "#3b82f6",
        "deep": "#020617",
        "background": "#0f172a",
        "elevated": "#1e293b",
        "surface": "#1e293b",
        "border": "#334155",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#94a3b8",
    },
    "display_currency": "USD",
    "exchange_rate_source": "manual",
    "exchange_rate_api_key": "",
    "exchange_rate_manual_overrides": {},
    "exchange_rate_cache": {},
    "exchange_rate_ttl_hours": 24,
    "monitor_fields": {
        "tokens": True,
        "progress": True,
        "threshold": True,
        "speed": True,
        "balance": True,
        "cache": True,
        "context_window": True,
        "updated_at": True,
    },
    "sort_by": "created_at_ms",
    "sort_order": "desc",
    "page_enhancements_enabled": False,
    "enable_session_delete": True,
    "enable_export": True,
    "enable_timeline": False,
    "enable_conversation_width": True,
    "conversation_width": "default",
    "enable_scroll_restore": True,
    "enable_service_tier": False,
}

CONFIG_FILE = app_data_path("config.json")


class Config:
    def __init__(self):
        self._write_locked = False
        self._write_lock_reason = ""
        ensure_app_dirs()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()
        token_changed = self._ensure_local_proxy_token()
        self._auto_detect_if_needed()
        if token_changed:
            self.save()

    def load(self):
        """Load settings, migrating the legacy config file when needed."""
        source = CONFIG_FILE
        if not source.exists() and LEGACY_CONFIG_FILE.exists():
            source = LEGACY_CONFIG_FILE
        if not source.exists():
            return

        try:
            with open(source, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self._data.update(saved)
            self._normalize_storage_defaults()
            token_changed = self._ensure_local_proxy_token()
            if (source == LEGACY_CONFIG_FILE and not CONFIG_FILE.exists()) or token_changed:
                self.save()
        except Exception:
            try:
                corrupted = source.with_suffix(source.suffix + ".corrupted")
                shutil.move(str(source), str(corrupted))
            except Exception:
                pass

    def save(self):
        """Atomically persist settings unless the process is write-locked."""
        self._ensure_writable()
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(CONFIG_FILE)
        except Exception as exc:
            print(f"Failed to save config: {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._ensure_writable()
        self._data[key] = value
        self._normalize_storage_defaults()
        self._ensure_local_proxy_token()
        self.save()

    def get_all(self) -> Dict:
        return copy.deepcopy(self._data)

    def update(self, data: Dict):
        self._ensure_writable()
        self._data.update(data)
        self._normalize_storage_defaults()
        self._ensure_local_proxy_token()
        self.save()

    def reset_defaults(self):
        self._ensure_writable()
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._auto_detect_if_needed()
        self._ensure_local_proxy_token()
        self.save()

    def lock_writes(self, reason: str):
        """Disable config writes until the process is restarted."""
        self._write_locked = True
        self._write_lock_reason = reason or "Writes are locked until restart."

    def is_write_locked(self) -> bool:
        return self._write_locked

    def write_lock_reason(self) -> str:
        return self._write_lock_reason

    def _ensure_writable(self):
        if self._write_locked:
            raise RuntimeError(self._write_lock_reason or "Writes are locked until restart.")

    def _normalize_storage_defaults(self):
        """Fill app-storage keys when loading legacy configs."""
        if self._data.get("backup_dir") == LEGACY_DEFAULT_BACKUP_DIR:
            self._data["backup_dir"] = DEFAULT_CONFIG["backup_dir"]
        if self._data.get("provider_store_path") == LEGACY_DEFAULT_PROVIDER_STORE:
            self._data["provider_store_path"] = DEFAULT_CONFIG["provider_store_path"]
        for key in ("backup_dir", "provider_store_path", "temp_dir", "diagnostics_dir", "exports_dir", "request_log_path"):
            if not self._data.get(key):
                self._data[key] = DEFAULT_CONFIG[key]
        if not isinstance(self._data.get("theme_custom"), dict):
            self._data["theme_custom"] = copy.deepcopy(DEFAULT_CONFIG["theme_custom"])
        else:
            merged_theme = copy.deepcopy(DEFAULT_CONFIG["theme_custom"])
            merged_theme.update(self._data["theme_custom"])
            self._data["theme_custom"] = merged_theme
        if not isinstance(self._data.get("monitor_fields"), dict):
            self._data["monitor_fields"] = copy.deepcopy(DEFAULT_CONFIG["monitor_fields"])
        else:
            merged_fields = copy.deepcopy(DEFAULT_CONFIG["monitor_fields"])
            merged_fields.update(self._data["monitor_fields"])
            self._data["monitor_fields"] = merged_fields
        if not isinstance(self._data.get("exchange_rate_manual_overrides"), dict):
            self._data["exchange_rate_manual_overrides"] = {}
        if not isinstance(self._data.get("exchange_rate_cache"), dict):
            self._data["exchange_rate_cache"] = {}
        if not self._data.get("display_currency"):
            self._data["display_currency"] = DEFAULT_CONFIG["display_currency"]
        if not self._data.get("exchange_rate_source"):
            self._data["exchange_rate_source"] = DEFAULT_CONFIG["exchange_rate_source"]
        if self._data.get("close_button_action") not in {"ask", "exit", "tray"}:
            self._data["close_button_action"] = DEFAULT_CONFIG["close_button_action"]
        if self._data.get("desktop_launch_action") not in {"show_window", "start_codex"}:
            self._data["desktop_launch_action"] = DEFAULT_CONFIG["desktop_launch_action"]
        monitor_enabled = self._data.get("desktop_monitor_enabled")
        if isinstance(monitor_enabled, str):
            self._data["desktop_monitor_enabled"] = monitor_enabled.strip().lower() not in {"0", "false", "no", "off"}
        elif not isinstance(monitor_enabled, bool):
            self._data["desktop_monitor_enabled"] = DEFAULT_CONFIG["desktop_monitor_enabled"]
        try:
            monitor_opacity = float(self._data.get("desktop_monitor_opacity", DEFAULT_CONFIG["desktop_monitor_opacity"]))
            if 0 < monitor_opacity <= 1:
                monitor_opacity *= 100
            self._data["desktop_monitor_opacity"] = min(max(int(round(monitor_opacity)), 35), 100)
        except (TypeError, ValueError):
            self._data["desktop_monitor_opacity"] = DEFAULT_CONFIG["desktop_monitor_opacity"]
        for key in (
            "update_check_enabled",
            "update_include_prerelease",
            "plugin_unlock_enabled",
            "codex_injection_enabled",
            "codex_goals_enabled",
            "codex_sandbox_auto_repair_enabled",
        ):
            value = self._data.get(key)
            if isinstance(value, str):
                self._data[key] = value.strip().lower() not in {"0", "false", "no", "off"}
            elif not isinstance(value, bool):
                self._data[key] = DEFAULT_CONFIG[key]
        try:
            self._data["codex_cdp_port"] = min(max(int(self._data.get("codex_cdp_port", 51236)), 1), 65535)
        except (TypeError, ValueError):
            self._data["codex_cdp_port"] = DEFAULT_CONFIG["codex_cdp_port"]
        if self._data.get("codex_last_start_mode") not in {"", "proxy_injection", "preserve_login_proxy", "official_direct"}:
            self._data["codex_last_start_mode"] = ""
        for key in (
            "startup_enabled",
            "startup_mode",
            "startup_auto_elevate",
            "startup_task_name",
            "startup_shortcut_name",
            "startup_target_path",
            "startup_arguments",
        ):
            if key not in self._data:
                self._data[key] = copy.deepcopy(DEFAULT_CONFIG[key])
        if self._data.get("startup_mode") not in {"disabled", "startup_folder", "scheduled_task_highest"}:
            self._data["startup_mode"] = DEFAULT_CONFIG["startup_mode"]
        self._data["startup_enabled"] = bool(self._data.get("startup_enabled"))
        self._data["startup_auto_elevate"] = bool(self._data.get("startup_auto_elevate"))
        if not self._data["startup_enabled"]:
            self._data["startup_mode"] = "disabled"
            self._data["startup_auto_elevate"] = False
        elif self._data["startup_auto_elevate"]:
            self._data["startup_mode"] = "scheduled_task_highest"
        elif self._data["startup_mode"] == "disabled":
            self._data["startup_mode"] = "startup_folder"
        elif self._data["startup_mode"] == "scheduled_task_highest":
            self._data["startup_auto_elevate"] = True
        for key in ("startup_task_name", "startup_shortcut_name", "startup_target_path", "startup_arguments"):
            self._data[key] = str(self._data.get(key) or DEFAULT_CONFIG[key])
        try:
            self._data["exchange_rate_ttl_hours"] = max(int(self._data.get("exchange_rate_ttl_hours", 24)), 1)
        except (TypeError, ValueError):
            self._data["exchange_rate_ttl_hours"] = 24
        try:
            self._data["request_log_retention_days"] = max(int(self._data.get("request_log_retention_days", 30)), 1)
        except (TypeError, ValueError):
            self._data["request_log_retention_days"] = 30
        try:
            self._data["request_log_max_mb"] = max(float(self._data.get("request_log_max_mb", 50)), 1.0)
        except (TypeError, ValueError):
            self._data["request_log_max_mb"] = 50
        try:
            self._data["proxy_upstream_timeout_seconds"] = min(max(int(self._data.get("proxy_upstream_timeout_seconds", 120)), 1), 3600)
        except (TypeError, ValueError):
            self._data["proxy_upstream_timeout_seconds"] = 120
        try:
            self._data["proxy_retry_attempts"] = min(max(int(self._data.get("proxy_retry_attempts", 0)), 0), 5)
        except (TypeError, ValueError):
            self._data["proxy_retry_attempts"] = 0
        try:
            self._data["proxy_retry_backoff_ms"] = min(max(int(self._data.get("proxy_retry_backoff_ms", 250)), 0), 30000)
        except (TypeError, ValueError):
            self._data["proxy_retry_backoff_ms"] = 250
        try:
            proxy_port = int(self._data.get("proxy_port", DEFAULT_CONFIG["proxy_port"]))
        except (TypeError, ValueError):
            proxy_port = DEFAULT_CONFIG["proxy_port"]
        if proxy_port == 8080 or proxy_port < 1 or proxy_port > 65535:
            proxy_port = DEFAULT_CONFIG["proxy_port"]
        self._data["proxy_port"] = proxy_port
        self._data["auto_approval_system_prompt"] = normalize_auto_approval_system_prompt(
            self._data.get("auto_approval_system_prompt")
        )

    def _ensure_local_proxy_token(self) -> bool:
        if not local_proxy_token_is_strong(self._data.get("local_proxy_bearer_token")):
            self._data["local_proxy_bearer_token"] = generate_local_proxy_bearer_token()
            return True
        return False

    def _auto_detect_if_needed(self):
        """Auto-fill Codex paths only when the user has not configured them."""
        changed = False

        if not self._data.get("db_path"):
            detected = detect_codex_db()
            if detected:
                self._data["db_path"] = detected
                changed = True

        if not self._data.get("sessions_dir"):
            detected = detect_sessions_dir()
            if detected:
                self._data["sessions_dir"] = detected
                changed = True

        if not self._data.get("archived_dir"):
            detected = detect_archived_dir()
            if detected:
                self._data["archived_dir"] = detected
                changed = True

        if not self._data.get("codex_plus_plus_path"):
            detected = detect_codex_plus_plus()
            if detected:
                self._data["codex_plus_plus_path"] = detected
                changed = True

        if not self._data.get("codex_cli_path"):
            detected = detect_codex_desktop() or detect_codex_launch_path()
            if detected:
                self._data["codex_cli_path"] = detected
                changed = True

        if changed:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any):
        self.set(key, value)
