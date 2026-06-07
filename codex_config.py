"""
codex_config.py - Safe Codex config/auth read/write with backup/rollback.
安全读写 Codex 配置与认证文件的模块，带自动备份与回滚能力。

本模块职责：
- 读取 ~/.codex/config.toml
- 读取 ~/.codex/auth.json
- 检测官方 OAuth 与 legacy API-key 认证模式
- 将 codex_enhance_manager provider 条目写入 config.toml
- 任何写入前自动备份
- 写入失败时从备份恢复
- 保留未知 TOML 键（合并而非替换，防止破坏用户自定义配置）
- 写入前生成 diff preview

设计意图：
  - 与 providers.py 解耦：providers.py 只管理本地 registry，本模块才负责
    与 Codex 官方配置的交互。这是安全护栏，防止未经预览的写操作破坏
    用户的官方登录态。
  - 原子写入 + 备份：所有写操作先备份旧文件，失败时自动 rollback。

Windows 平台特殊性：
  - 使用 shutil.copystat 保留文件权限和时间戳；在 Windows 上 follow_symlinks=False
    避免对符号链接的意外跟随（虽然 Windows 符号链接较少见，但防御式编程）。
  - expanduser + expandvars 解析 Windows 环境变量如 %USERPROFILE%。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app_paths import app_data_path

CODEX_CONFIG_BACKUP_DIR = app_data_path("codex_backups")
REDACTED = "********"


def resolve_codex_home(codex_home: str = "") -> Path:
    """Resolve Codex home directory."""
    if codex_home:
        return Path(os.path.expandvars(codex_home)).expanduser()
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(os.path.expandvars(env)).expanduser()
    return Path.home() / ".codex"


def load_config_toml(config_path: str) -> Dict[str, Any]:
    """
    安全读取 config.toml，保留所有键。

    工程权衡：
      - Python 3.11+ 有标准库 tomllib，但仅支持读不支持写；因此写操作使用
        自定义简单 TOML 生成器（save_config_toml）。
      - 若 tomllib 解析失败（如 TOML 含非法语法），回退到简单行解析器：
        虽然无法处理嵌套表和复杂类型，但能提取顶层键值对，避免直接返回空字典。
      - 数组字面量尝试 json.loads 解析：TOML 数组与 JSON 数组语法子集兼容，
        这是 pragmatic 的折中方案。

    Args:
        config_path: config.toml 的绝对路径。

    Returns:
        解析后的字典。文件不存在或解析失败时返回空字典或扁平字典。
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {}

    try:
        import tomllib
        return tomllib.loads(content)
    except Exception:
        # Fallback: simple line parser returns flat dict only
        result: Dict[str, Any] = {}
        current_section = result
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                section_name = stripped[1:-1].strip()
                current_section = {}
                result[section_name] = current_section
                continue
            if "=" in stripped:
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"\'')
                # 尝试解析数组字面量，避免将 [1, 2, 3] 当作字符串
                if value.startswith("[") and value.endswith("]"):
                    try:
                        import json
                        value = json.loads(value)
                    except Exception:
                        pass
                current_section[key] = value
        return result


def save_config_toml(config_path: str, data: Dict[str, Any]) -> None:
    """
    原子写入 config.toml。

    设计意图：
      - 使用 tmp + replace 模式，避免写一半崩溃导致 config.toml 截断。
      - copystat 保留原文件权限和时间戳：Windows 上某些工具（如 Codex CLI）
        可能依赖文件时间戳判断配置是否变更。
      - 简单扁平序列化：当前实现仅支持单层嵌套表（[section]），这是 pragmatic
        的权衡——Codex config.toml 结构简单，无需引入 tomli_w 等第三方依赖。

    Args:
        config_path: 目标文件路径。
        data: 要写入的字典。值为 dict 的键会被序列化为 [section]。
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for sub_key, sub_value in value.items():
                lines.append(_toml_line(sub_key, sub_value))
            lines.append("")
        else:
            lines.append(_toml_line(key, value))

    content = "\n".join(lines) + "\n"
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    if path.exists():
        shutil.copystat(path, tmp_path, follow_symlinks=False)
    tmp_path.replace(path)


def _toml_line(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return f'{key} = {"true" if value else "false"}'
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    if isinstance(value, list):
        items = ", ".join(_toml_value_repr(v) for v in value)
        return f"{key} = [{items}]"
    return f'{key} = {_toml_value_repr(value)}'


def _toml_value_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Proper TOML string escaping for double-quoted strings
    text = text.replace("\\", "\\\\")
    text = text.replace("\"", "\\\"")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\r")
    text = text.replace("\t", "\\t")
    return f'"{text}"'


def load_auth_json(auth_path: str) -> Dict[str, Any]:
    """Read auth.json safely."""
    path = Path(auth_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_auth_json(auth_path: str, data: Dict[str, Any]) -> None:
    """Write auth.json atomically."""
    path = Path(auth_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if path.exists():
        shutil.copystat(path, tmp_path, follow_symlinks=False)
    tmp_path.replace(path)


def backup_file(file_path: str, backup_dir: Optional[Path] = None) -> str:
    """Create a timestamped backup of a file. Returns backup path."""
    path = Path(file_path)
    if not path.exists():
        return ""
    dest_dir = backup_dir or CODEX_CONFIG_BACKUP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_name = f"{path.name}.{ts}.bak"
    dest = dest_dir / backup_name
    shutil.copy2(path, dest)
    return str(dest)


def restore_file(file_path: str, backup_path: str) -> bool:
    """Restore a file from its backup."""
    src = Path(backup_path)
    dst = Path(file_path)
    if not src.exists():
        return False
    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def list_backups(backup_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    列出可用的 config 备份文件。

    设计意图：
      - 备份文件命名格式：{原文件名}.{timestamp}.bak，按修改时间倒序排列，
        最新备份在列表首位，便于「恢复到最近备份」场景直接取 backups[0]。
      - 精确匹配：防止误将其他临时文件（如 .tmp、.corrupted）列入备份列表。

    边界条件：
      - 备份目录不存在时返回空列表，而非抛出异常。
      - Windows 下 st_mtime 为本地时间戳，fromtimestamp 配合 tz=timezone.utc
        转为 ISO 格式字符串，避免时区歧义。

    Args:
        backup_dir: 自定义备份目录，默认使用 CODEX_CONFIG_BACKUP_DIR。

    Returns:
        备份文件信息字典列表。
    """
    dest_dir = backup_dir or CODEX_CONFIG_BACKUP_DIR
    if not dest_dir.exists():
        return []
    backups = []
    for f in sorted(dest_dir.iterdir(), reverse=True):
        # 精确匹配备份文件：以 .bak 结尾，或文件名包含 .bak. 时间戳后缀
        if f.suffix == ".bak" or f.name.startswith("config.toml.") and ".bak" in f.name:
            backups.append({
                "path": str(f),
                "name": f.name,
                "mtime": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                "size": f.stat().st_size,
            })
    return backups


def detect_auth_mode(auth_data: Dict[str, Any]) -> str:
    """
    从 auth.json 内容检测当前认证模式。

    设计意图：
      - Codex CLI 有两种认证方式：
        1. official_oauth：官方 OAuth 登录，access_token 为 JWT 格式（不以 sk- 开头）。
        2. legacy_api_key：用户手动填入的 OpenAI API key（以 sk- 开头），
           或直接存储在 auth.json / 环境变量中。
      - 区分二者至关重要：official_oauth 模式下**绝不能**覆盖 auth.json，
        否则会破坏用户官方登录态，导致需要重新扫码/授权。

    边界条件：
      - access_token 以 sk- 开头：legacy_api_key（OpenAI key 的固定前缀）。
      - access_token 存在但非 sk- 开头：official_oauth（JWT 或自定义 token）。
      - 无 access_token 但有 api_key：legacy_api_key。

    Args:
        auth_data: auth.json 解析后的字典。

    Returns:
        "official_oauth" | "legacy_api_key" | "none" | "unknown"
    """
    if not auth_data:
        return "none"
    # Official OAuth: has access_token with specific prefixes or expires_at
    if auth_data.get("access_token"):
        token = str(auth_data["access_token"])
        if token.startswith("sk-"):
            return "legacy_api_key"
        return "official_oauth"
    # Legacy API key stored directly
    if auth_data.get("api_key") or auth_data.get("OPENAI_API_KEY"):
        return "legacy_api_key"
    return "unknown"


def is_official_oauth(auth_data: Dict[str, Any]) -> bool:
    return detect_auth_mode(auth_data) == "official_oauth"


def is_legacy_api_key(auth_data: Dict[str, Any]) -> bool:
    return detect_auth_mode(auth_data) == "legacy_api_key"


def merge_toml_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge updates into base without removing unknown keys."""
    merged = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_toml_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def build_codex_enhance_provider_config(
    proxy_base_url: str = "http://localhost:8080/v1",
    proxy_model: str = "auto",
) -> Dict[str, Any]:
    """Build a config.toml fragment for the local proxy provider."""
    return {
        "model_provider": "codex_enhance_manager",
        "model": proxy_model,
        "provider": "codex_enhance_manager",
        "defaults": {
            "model_provider": "codex_enhance_manager",
            "model": proxy_model,
        },
    }


class CodexConfigManager:
    """High-level manager for safe Codex config/auth operations."""

    def __init__(self, codex_home: str = ""):
        self.codex_home = resolve_codex_home(codex_home)
        self.config_path = self.codex_home / "config.toml"
        self.auth_path = self.codex_home / "auth.json"
        self.model_catalog_path = self.codex_home / "model_catalog.json"
        self.backup_dir = CODEX_CONFIG_BACKUP_DIR

    def read_config(self) -> Dict[str, Any]:
        return load_config_toml(str(self.config_path))

    def read_auth(self) -> Dict[str, Any]:
        return load_auth_json(str(self.auth_path))

    def read_model_catalog(self) -> Dict[str, Any]:
        return load_auth_json(str(self.model_catalog_path))  # reuse JSON loader

    def get_auth_mode(self) -> str:
        return detect_auth_mode(self.read_auth())

    def preview_write_provider(
        self,
        proxy_base_url: str = "http://localhost:8080/v1",
        proxy_model: str = "auto",
    ) -> Dict[str, Any]:
        """Generate a diff preview without writing anything."""
        current_config = self.read_config()
        desired_updates = build_codex_enhance_provider_config(proxy_base_url, proxy_model)
        merged = merge_toml_dict(current_config, desired_updates)

        current_auth = self.read_auth()
        # We intentionally do NOT write third-party keys into auth.json by default.
        desired_auth = copy.deepcopy(current_auth)

        return {
            "will_write_config": current_config != merged,
            "will_write_auth": current_auth != desired_auth,
            "will_write_catalog": False,
            "config_diff": _compute_diff(current_config, merged),
            "auth_diff": _compute_diff(current_auth, desired_auth),
            "restart_required": True,
            "preserve_official_oauth": is_official_oauth(current_auth),
            "auth_mode": self.get_auth_mode(),
            "warnings": self._collect_warnings(current_auth, merged),
        }

    def write_provider_config(
        self,
        proxy_base_url: str = "http://localhost:8080/v1",
        proxy_model: str = "auto",
        preserve_official_auth: bool = True,
    ) -> Dict[str, Any]:
        """
        将本地代理 provider 写入 Codex config.toml。

        设计意图：
          - 这是「唯一」会直接修改 Codex 配置的入口，因此必须极度谨慎。
          - 写入前强制备份 config.toml 和 auth.json，失败时自动 rollback。
          - 默认保护官方 OAuth：若检测到 official_oauth，绝不触碰 auth.json，
            防止用户需要重新登录。

        工程权衡：
          - 无论 preserve_official_auth 如何，都先备份 auth.json：
            这样即使用户后续关闭保护开关，也能通过 rollback 恢复到原始 OAuth 态。
          - 合并而非替换：merge_toml_dict 保留用户自定义的未知键（如 mcp_servers）。

        Args:
            proxy_base_url: 本地代理地址，默认 http://localhost:8080/v1。
            proxy_model: 代理模型标识，默认 "auto"。
            preserve_official_auth: 是否保护官方 OAuth 登录态。

        Returns:
            包含 success、backups、errors 的结果字典。
        """
        result = {
            "success": False,
            "backups": {},
            "restart_required": True,
            "errors": [],
        }

        # 无论是否 preserve_official_auth，先备份 auth.json（如果存在）。
        # 这样即使用户后续关闭 preserve，也能通过 rollback 恢复到原始 OAuth 态。
        if self.auth_path.exists():
            auth_backup = backup_file(str(self.auth_path), self.backup_dir)
            if auth_backup:
                result["backups"]["auth_json"] = auth_backup

        current_auth = self.read_auth()
        if preserve_official_auth and is_official_oauth(current_auth):
            # Do not touch auth.json when official OAuth is active
            pass

        # Back up config.toml
        if self.config_path.exists():
            config_backup = backup_file(str(self.config_path), self.backup_dir)
            if config_backup:
                result["backups"]["config_toml"] = config_backup

        # Build and merge config
        current_config = self.read_config()
        updates = build_codex_enhance_provider_config(proxy_base_url, proxy_model)
        merged = merge_toml_dict(current_config, updates)

        # Write config.toml
        try:
            save_config_toml(str(self.config_path), merged)
        except Exception as e:
            result["errors"].append(f"config.toml write failed: {e}")
            # Rollback：将备份 copy 回原始路径，尽可能恢复到写入前状态
            if "config_toml" in result["backups"]:
                restore_file(str(self.config_path), result["backups"]["config_toml"])
            return result

        result["success"] = len(result["errors"]) == 0
        return result

    def restore_config(self, backup_path: str = "") -> Dict[str, Any]:
        """Restore config.toml from a specific backup or the most recent one."""
        if not backup_path:
            backups = [
                b for b in list_backups(self.backup_dir)
                if "config.toml" in b["name"]
            ]
            if not backups:
                return {"success": False, "error": "No config.toml backups found"}
            backup_path = backups[0]["path"]

        ok = restore_file(str(self.config_path), backup_path)
        return {
            "success": ok,
            "restored_from": backup_path if ok else "",
            "restart_required": True,
        }

    def restore_auth(self, backup_path: str = "") -> Dict[str, Any]:
        """Restore auth.json from a specific backup or the most recent one."""
        if not backup_path:
            backups = [
                b for b in list_backups(self.backup_dir)
                if "auth.json" in b["name"]
            ]
            if not backups:
                return {"success": False, "error": "No auth.json backups found"}
            backup_path = backups[0]["path"]

        ok = restore_file(str(self.auth_path), backup_path)
        return {
            "success": ok,
            "restored_from": backup_path if ok else "",
            "restart_required": True,
        }

    def list_all_backups(self) -> List[Dict[str, Any]]:
        return list_backups(self.backup_dir)

    def _collect_warnings(
        self,
        auth_data: Dict[str, Any],
        merged_config: Dict[str, Any],
    ) -> List[str]:
        warnings: List[str] = []
        if is_official_oauth(auth_data):
            warnings.append(
                "Official OAuth detected. Local proxy will be written to config.toml, "
                "but auth.json will NOT be modified. Codex must remain logged in."
            )
        elif is_legacy_api_key(auth_data):
            warnings.append(
                "Legacy API key detected. If you switch to the local proxy, "
                "Codex will use proxy upstream instead of direct OpenAI calls."
            )
        else:
            warnings.append(
                "No auth detected in auth.json. Codex may not be logged in."
            )
        if merged_config.get("model_provider") == "codex_enhance_manager":
            warnings.append(
                "config.toml already points to codex_enhance_manager. "
                "Restart Codex if provider settings changed."
            )
        return warnings


def _compute_diff(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a simple added/removed/changed diff between two dicts."""
    diff: Dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        if key not in old:
            diff["added"][key] = new[key]
        elif key not in new:
            diff["removed"][key] = old[key]
        elif old[key] != new[key]:
            diff["changed"][key] = {"old": old[key], "new": new[key]}
    return diff


def redact_auth_for_preview(data: Dict[str, Any]) -> Dict[str, Any]:
    """Redact secrets from auth data for UI display."""
    redacted = copy.deepcopy(data)
    for key in ("access_token", "api_key", "refresh_token", "id_token"):
        if key in redacted and redacted[key]:
            redacted[key] = REDACTED
    return redacted
