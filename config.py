"""
config.py - 配置管理模块
保存用户设置到 codex_gui_config.json
Web 版增强：新增自动检测、Codex++ 路径、暗色主题等配置项
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from auto_detect import detect_codex_db, detect_sessions_dir, detect_archived_dir, detect_codex_plus_plus

DEFAULT_CONFIG = {
    "db_path": "",
    "sessions_dir": "",
    "archived_dir": "",
    "backup_dir": str(Path.home() / "codex_backups"),
    "auto_backup": False,
    "backup_interval_hours": 6,
    "max_backups": 20,
    "page_size": 50,
    "max_lines_large_file": 2000,
    "large_file_threshold_mb": 500,
    "use_codex_plus_plus": False,
    "codex_cli_path": "",
    "codex_plus_plus_path": "",
    "dark_mode": True,
    "sort_by": "created_at_ms",
    "sort_order": "desc",
}

CONFIG_FILE = Path.home() / ".codex_gui_config.json"


class Config:
    def __init__(self):
        self._data: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()
        # 首次运行时自动检测路径
        self._auto_detect_if_needed()

    def load(self):
        """从配置文件加载"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

    def save(self):
        """保存配置到文件"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        """设置配置值并保存"""
        self._data[key] = value
        self.save()

    def get_all(self) -> Dict:
        """获取所有配置"""
        return dict(self._data)

    def update(self, data: Dict):
        """批量更新配置"""
        self._data.update(data)
        self.save()

    def reset_defaults(self):
        """重置所有设置为默认值"""
        self._data = dict(DEFAULT_CONFIG)
        self._auto_detect_if_needed()
        self.save()

    def _auto_detect_if_needed(self):
        """自动检测路径（仅对空值填充）"""
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
