"""
config.py - 配置管理模块
保存用户设置到 codex_gui_config.json
Web 版增强：新增自动检测、Codex++ 路径、暗色主题等配置项

设计意图：
  - 简单的 JSON 配置文件管理：适合存储少量键值对（<50 个），无需引入
    SQLite 或 TOML 的复杂性。
  - 自动检测路径：首次启动或 reset_defaults 时，若 db_path/sessions_dir
    等关键路径为空，自动调用 auto_detect.py 探测，降低新用户配置门槛。
  - 原子写入：tmp + replace 防止写一半崩溃导致 JSON 损坏。

工程权衡：
  - DEFAULT_CONFIG 使用硬编码默认值：明确、可预测，避免外部依赖缺失时
    启动失败。
  - 损坏恢复：load() 时若 JSON 解析失败，将原文件重命名为 .corrupted，
    并回退到 DEFAULT_CONFIG，保证应用始终可启动。

Windows 平台特殊性：
  - Path.home() 在 Windows 下对应 %USERPROFILE%，配置文件存放在用户目录下，
    避免写入 Program Files 等需要管理员权限的目录。
"""
import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from auto_detect import detect_codex_db, detect_sessions_dir, detect_archived_dir, detect_codex_plus_plus
from app_paths import LEGACY_CONFIG_FILE, app_data_path, ensure_app_dirs

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
    "proxy_port": 8080,
    "dark_mode": True,
    "theme_preset": "dark",
    "theme_custom": {
        "accent": "#3b82f6",
        "surface": "#1e293b",
        "background": "#0f172a",
    },
    "monitor_fields": {
        "tokens": True,
        "progress": True,
        "threshold": True,
        "cache": True,
        "context_window": True,
        "updated_at": True,
    },
    "sort_by": "created_at_ms",
    "sort_order": "desc",
    # Phase 11: Codex Page Enhancements settings
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
        """
        初始化配置管理器。

        流程：
          1. 加载默认配置。
          2. 尝试从文件加载已保存配置并合并。
          3. 若关键路径为空，自动探测并填充。
        """
        ensure_app_dirs()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()
        # 首次运行时自动检测路径
        self._auto_detect_if_needed()

    def load(self):
        """
        从配置文件加载。

        边界条件：
          - 文件损坏时重命名为 .corrupted 并回退到默认值，保证应用始终可启动。
          - 静默处理 rename 失败：Windows 上若文件被占用可能无法重命名，
            此时直接回退到默认配置，不阻塞启动。
        """
        source = CONFIG_FILE
        if not source.exists() and LEGACY_CONFIG_FILE.exists():
            source = LEGACY_CONFIG_FILE
        if source.exists():
            try:
                with open(source, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
                self._normalize_storage_defaults()
                if source == LEGACY_CONFIG_FILE and not CONFIG_FILE.exists():
                    self.save()
            except Exception:
                try:
                    corrupted = source.with_suffix(source.suffix + ".corrupted")
                    shutil.move(str(source), str(corrupted))
                except Exception:
                    pass

    def save(self):
        """
        原子写入保存配置到文件。

        使用 tmp + replace 模式，防止写一半崩溃导致 JSON 截断。
        """
        try:
            tmp = CONFIG_FILE.with_suffix(".tmp")
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(CONFIG_FILE)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。若 key 不存在，返回 default。"""
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        """设置配置值并立即保存到文件。"""
        self._data[key] = value
        self._normalize_storage_defaults()
        self.save()

    def get_all(self) -> Dict:
        """获取所有配置的深拷贝，防止调用方修改内部状态。"""
        return dict(self._data)

    def update(self, data: Dict):
        """批量更新配置并保存。"""
        self._data.update(data)
        self._normalize_storage_defaults()
        self.save()

    def reset_defaults(self):
        """重置所有设置为默认值，并重新自动检测路径。"""
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._auto_detect_if_needed()
        self.save()

    def _normalize_storage_defaults(self):
        """Fill app-storage keys when loading legacy configs."""
        if self._data.get("backup_dir") == LEGACY_DEFAULT_BACKUP_DIR:
            self._data["backup_dir"] = DEFAULT_CONFIG["backup_dir"]
        if self._data.get("provider_store_path") == LEGACY_DEFAULT_PROVIDER_STORE:
            self._data["provider_store_path"] = DEFAULT_CONFIG["provider_store_path"]
        for key in ("backup_dir", "provider_store_path", "temp_dir", "diagnostics_dir", "exports_dir"):
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

    def _auto_detect_if_needed(self):
        """
        自动检测路径（仅对空值填充）。

        设计意图：
          - 首次运行或重置设置后，用户通常不知道 Codex DB 在哪里；
            自动检测能「开箱即用」。
          - 仅填充空值：不覆盖用户已手动设置的路径，尊重用户选择。

        检测顺序：
          1. detect_codex_db()：在 ~/.codex/ 和 %LOCALAPPDATA% 下搜索 state_*.sqlite。
          2. detect_sessions_dir() / detect_archived_dir()：在 ~/.codex/ 下查找。
          3. detect_codex_plus_plus()：在 %LOCALAPPDATA%/Programs/Codex++/ 和注册表查找。
        """
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

        if changed:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any):
        self.set(key, value)
