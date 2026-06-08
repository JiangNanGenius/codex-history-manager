"""
providers.py - Local provider registry and first-pass model catalog preview.
本地 Provider Registry 与模型目录预览模块。

设计意图：
  - 本模块是 Codex Enhance Manager 的「配置中心」，负责管理所有第三方
    Provider（OpenAI、Anthropic、国内厂商等）的元数据、认证、模型列表。
  - 与 Codex 官方配置解耦：本模块**绝不**直接写入 Codex 的 auth.json 或
    config.toml；所有写操作只发生在本地 JSON store（providers.json）。
    这是安全护栏——避免在未经 diff preview / backup / rollback 的情况下
    破坏用户官方登录态。
  - 支持预设（preset）导入、字段脱敏（redaction）、本地校验、Catalog 预览。

工程权衡：
  - 使用纯 JSON 文件而非 SQLite：provider 数量极少（<100），JSON 便于
    人工阅读和版本控制；且避免了 SQLite 在 Windows 上的文件锁问题。
  - 所有写操作采用「原子替换」模式：先写 .tmp，再 replace/copy2，
    避免写一半崩溃导致 JSON 截断。
  - normalize_provider 是「防御式编程」核心：任何外部输入（包括用户 UI
    提交和旧版本数据）都必须经过此函数消毒，确保字段类型、默认值、
    枚举值符合当前 schema。

Windows 平台特殊性：
  - store_path 使用 Path.expanduser() 解析 ~，在 Windows 上对应
    C:\\Users\\<username>。
  - _save_store 中针对 Windows 文件锁定（如其他进程持有 providers.json
    句柄）做了 3 次重试 + shutil.copy2 回退。
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app_paths import app_data_path
from capabilities import (
    effective_provider_capabilities,
    has_locked_native_capabilities,
    is_codex_login_provider,
    is_native_responses_provider,
    merge_provider_model_capabilities,
    model_capability_overrides,
    normalize_capabilities as _normalize_capabilities,
    responses_profile_mode,
)
from model_catalog import resolve_catalog_id_collisions
from provider_routing import provider_allows_local_routing


# Schema version：当数据结构发生不兼容变更时递增，用于未来迁移逻辑。
SCHEMA_VERSION = 1
# 默认存储路径：用户文档目录下的 Codex Enhance Manager/providers/providers.json。
DEFAULT_STORE_PATH = app_data_path("providers", "providers.json")

CATALOG_VISIBILITY = {"hidden", "focused_only", "always_visible", "selected_models"}
API_FORMATS = {
    "openai_responses",
    "openai_chat",
    "openai_images",
    "openai_videos",
    "openai_compatible",
    "anthropic",
    "custom",
}
AUTH_MODES = {
    "provider_api_key",
    "global_auth_json",
    "official_oauth",
    "no_auth",
}
APPROVAL_MODES = {
    "manual_only",
    "official_guardian",
    "proxy_auto_approve",
}


def is_provider_read_only(provider: Dict[str, Any]) -> bool:
    """Provider entries backed by Codex official login are status-only here."""
    return is_codex_login_provider(provider)

# Token-based matching to avoid false positives like "monkey" or "tokenize"
# 设计说明：使用分词后匹配，而非简单子串包含，避免 "monkey" 被误判为 secret。
# 工程权衡：维护成本低的白名单方式；若未来出现新的 secret 字段命名，
# 只需在此追加即可，无需修改 is_secret_key 的正则逻辑。
_SECRET_FIELD_HINTS = {
    "apikey", "api_key", "api-key",
    "auth", "authorization",
    "bearer",
    "token",
    "secret",
    "password",
    "xapikey", "x-api-key", "x_api_key",
}
# 脱敏后的占位符。UI 和诊断日志中统一使用此常量，便于全局搜索识别。
REDACTED_VALUE = "********"


class ProviderRegistry:
    """
    JSON-backed local provider registry.
    本地 Provider Registry：所有 CRUD、预设导入、Catalog 预览的入口类。

    线程安全说明：
      - 当前实现未使用显式线程锁，因为 Flask 是单线程多路复用（开发模式）
        或进程隔离（生产模式）。若未来需要多线程并发写，应在 _save_store
        和 _load_store 上加 threading.Lock。
      - 但 _save_store 的原子写（tmp + replace）已能防止自身写一半崩溃。
    """

    def __init__(self, store_path: str = ""):
        """
        初始化 ProviderRegistry。

        Args:
            store_path: 自定义存储路径。空字符串则使用 DEFAULT_STORE_PATH。
                        Windows 下支持 ~ 展开为当前用户主目录。
        """
        # expanduser 在 Windows 上将 ~ 解析为 %USERPROFILE%，确保跨平台一致
        self.store_path = Path(store_path).expanduser() if store_path else DEFAULT_STORE_PATH

    def list_providers(
        self,
        include_secrets: bool = False,
        extra_providers: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        列出所有 provider。

        Args:
            include_secrets: 是否包含原始 api_key 等敏感字段。
                             默认 False，所有 secret 字段会被替换为 ********。
                             这是安全设计：UI 和导出诊断包默认不脱敏。

        Returns:
            包含 schema_version、store_path、providers 列表、focus_provider_id 的字典。
        """
        store = self._load_store()
        providers = self._with_extra_providers(store.get("providers", []), extra_providers)
        if not include_secrets:
            providers = redact_secrets(providers)
        return {
            "schema_version": store.get("schema_version", SCHEMA_VERSION),
            "store_path": str(self.store_path),
            "providers": providers,
            "focus_provider_id": store.get("focus_provider_id", ""),
            "updated_at": store.get("updated_at", ""),
        }

    def get_provider(
        self,
        provider_id: str,
        include_secrets: bool = False,
        extra_providers: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        读取单个 provider。

        Args:
            provider_id: provider 的唯一标识。
            include_secrets: 同 list_providers。

        Returns:
            Provider 字典，或 None（未找到时）。
            使用 deepcopy 防止调用方修改内部状态。
        """
        store = self._load_store()
        provider = self._find_provider(store, provider_id)
        if not provider:
            provider = self._find_extra_provider(extra_providers, provider_id)
        if not provider:
            return None
        # deepcopy 隔离：防止调用方修改返回字典后意外污染内存中的 store
        result = copy.deepcopy(provider)
        return result if include_secrets else redact_secrets(result)

    def create_provider(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新 provider。

        设计意图：
          - 所有输入必须经过 normalize_provider 消毒，防止旧版本数据或
            用户手抖输入的非法字段破坏 schema。
          - ID 自动生成并去重；short_alias 全局唯一校验，避免 Catalog 中
            出现重复的 codex_model_id 前缀（如 "openai/gpt-5"）。

        Args:
            data: 原始 provider 字典（来自 UI 或 preset）。

        Returns:
            创建后的 provider（已脱敏）。

        Raises:
            ValueError: short_alias 已存在时抛出。
        """
        store = self._load_store()
        provider = normalize_provider(data)
        # ID 分配：优先使用 sanitize_id 处理用户输入，若冲突则自动加后缀 -2, -3...
        provider["id"] = self._allocate_provider_id(store, provider)
        # short_alias 唯一性校验：Catalog 中 alias/model_id 的命名空间基础
        self._validate_unique_alias(store, provider["short_alias"], provider["id"])
        provider["created_at"] = now_iso()
        provider["updated_at"] = provider["created_at"]
        store.setdefault("providers", []).append(provider)
        self._save_store(store)
        return redact_secrets(provider)

    def update_provider(self, provider_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        更新已有 provider。

        工程权衡：
          - 使用 merge_provider_update 而非直接覆盖：UI 提交时 secret 字段
            可能显示为 ********，直接覆盖会导致 key 丢失。merge 逻辑会
            识别 REDACTED_VALUE 并跳过这些字段，保留原值。
          - created_at 不可变：从 existing 继承，防止前端误传篡改创建时间。

        Args:
            provider_id: 目标 provider ID。
            data: 更新字段字典。

        Returns:
            更新后的 provider（已脱敏），或 None（未找到时）。

        Raises:
            ValueError: short_alias 与其他 provider 冲突时抛出。
        """
        store = self._load_store()
        providers = store.setdefault("providers", [])
        for idx, existing in enumerate(providers):
            if existing.get("id") != provider_id:
                continue
            # merge 而非 replace：保护 secret 字段不被 REDACTED_VALUE 覆盖
            if is_provider_read_only(existing):
                raise ValueError("Codex login providers are read-only in Provider settings.")
            merged = merge_provider_update(existing, data)
            merged["id"] = provider_id
            merged["created_at"] = existing.get("created_at") or now_iso()
            merged["updated_at"] = now_iso()
            self._validate_unique_alias(store, merged["short_alias"], provider_id)
            providers[idx] = merged
            self._save_store(store)
            return redact_secrets(merged)
        return None

    def delete_provider(self, provider_id: str) -> bool:
        """
        删除 provider。

        边界条件：
          - 若被删除的是当前 focus_provider，自动清空 focus_provider_id，
            防止 Catalog 预览引用不存在的 provider。
          - 若 provider 不存在，返回 False 且**不**触发无意义写盘。

        Args:
            provider_id: 目标 provider ID。

        Returns:
            是否实际发生了删除。
        """
        store = self._load_store()
        existing = self._find_provider(store, provider_id)
        if existing and is_provider_read_only(existing):
            raise ValueError("Codex login providers are read-only in Provider settings.")
        before = len(store.get("providers", []))
        store["providers"] = [p for p in store.get("providers", []) if p.get("id") != provider_id]
        # 级联清理 focus：避免 dangling reference
        if store.get("focus_provider_id") == provider_id:
            store["focus_provider_id"] = ""
        changed = len(store["providers"]) != before
        if changed:
            self._save_store(store)
        return changed

    def list_presets(self) -> Dict[str, Any]:
        """
        返回内置 provider preset 列表。

        设计意图：
          - Preset 包含完整 provider 模板（如 OpenAI-compatible、Bailian、Ark）。
          - 返回 deepcopy + redact，防止调用方意外修改内置常量，同时确保
            即使 preset 未来包含 secret 模板也不会泄露。
        """
        return {"presets": redact_secrets(copy.deepcopy(PROVIDER_PRESETS))}

    def import_preset(self, preset_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        从预设创建 provider。

        Args:
            preset_id: 预设标识，如 "openai-compatible-responses"。
            overrides: 可选覆盖字段（如用户自定义 base_url、api_key）。

        Returns:
            新创建的 provider（已脱敏）。

        Raises:
            ValueError: preset_id 不存在时抛出。
        """
        preset = next((p for p in PROVIDER_PRESETS if p.get("preset_id") == preset_id), None)
        if not preset:
            raise ValueError(f"Unknown provider preset: {preset_id}")
        data = copy.deepcopy(preset.get("provider", {}))
        # deep_merge 允许用户只覆盖部分字段，保留 preset 中的默认值
        if overrides:
            data = deep_merge(data, overrides)
        return self.create_provider(data)

    def test_provider(self, provider_id: str = "", provider_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        本地 provider 配置校验（不做真实网络请求）。

        设计意图：
          - 当前阶段只做静态校验（字段完整性、正则匹配、枚举值检查）。
          - 真实网络 health-check 计划在后续阶段接入，以避免在配置阶段
            因网络抖动导致误报。
          - 若传 provider_id，会自动将结果写回 registry 的 status 字段，
            供 UI 显示测试状态。

        Args:
            provider_id: 已存在 provider 的 ID。与 provider_data 二选一。
            provider_data: 未保存的草稿 provider 数据，用于「Test This Section」
                           按钮在保存前预览校验结果。

        Returns:
            包含 success、errors、warnings、status 的字典。
        """
        if provider_id:
            provider = self.get_provider(provider_id, include_secrets=True)
            if not provider:
                return {
                    "success": False,
                    "mode": "local_validation",
                    "message": "Provider not found.",
                    "errors": ["provider_not_found"],
                    "warnings": [],
                    "tested_at": now_iso(),
                }
        else:
            # 草稿模式：直接消毒传入数据，无需读盘
            provider = normalize_provider(provider_data or {})

        errors, warnings = validate_provider(provider)
        status = {
            "last_tested": now_iso(),
            "last_error": "; ".join(errors) if errors else None,
            "needs_restart": False,
        }

        # 回写状态：让用户在 Providers 页面看到「上次测试时间 / 错误」
        if provider_id:
            existing = self.get_provider(provider_id, include_secrets=True)
            if existing and not is_provider_read_only(existing):
                existing["status"] = {**existing.get("status", {}), **status}
                self.update_provider(provider_id, existing)

        return {
            "success": not errors,
            "mode": "local_validation",
            "message": "本地配置校验通过；真实网络探测会在后续 health-check 中接入。" if not errors else "本地配置校验失败。",
            "errors": errors,
            "warnings": warnings,
            "status": status,
            "tested_at": status["last_tested"],
        }

    def bulk_update_models(
        self,
        provider_id: str,
        action: str,
        filter_criteria: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        批量更新 provider 下的模型选择状态。

        设计意图：
          - UMC 页面需要批量操作：全选、全不选、只选 vision-capable、
            只选低成本、只选高上下文窗口等。
          - 操作粒度是「单个 provider 下的 models」，不是全局操作，
            避免误改其他 provider 的模型状态。

        Args:
            provider_id: 目标 provider ID。
            action: 操作类型：
                - "select_all": 选中该 provider 下所有 enabled 模型。
                - "deselect_all": 取消选中所有模型。
                - "select_vision": 只选中支持 vision 的模型。
                - "select_low_cost": 只选中标记为低成本的模型。
                - "select_high_context": 只选中上下文窗口 >= threshold 的模型。
            filter_criteria: 可选过滤条件，目前支持：
                - "context_threshold": int，用于 "select_high_context"。
                - "cost_tier": str，用于 "select_low_cost"。

        Returns:
            {"success": bool, "changed": int, "action": str}

        Raises:
            ValueError: action 不合法时抛出。
        """
        valid_actions = {"select_all", "deselect_all", "select_vision", "select_low_cost", "select_high_context"}
        if action not in valid_actions:
            raise ValueError(f"Invalid bulk action: {action}. Valid: {valid_actions}")

        provider = self.get_provider(provider_id, include_secrets=True)
        if not provider:
            return {"success": False, "changed": 0, "action": action, "error": "Provider not found"}
        if is_provider_read_only(provider):
            raise ValueError("Codex login providers are read-only in Provider settings.")

        models = provider.get("models", [])
        changed = 0
        criteria = filter_criteria or {}

        for model in models:
            if not model.get("enabled", True):
                continue
            prev = model.get("selected", False)
            new_val = prev

            if action == "select_all":
                new_val = True
            elif action == "deselect_all":
                new_val = False
            elif action == "select_vision":
                caps = merge_provider_model_capabilities(provider, model)
                new_val = bool(caps.get("vision", False))
            elif action == "select_low_cost":
                # 低成本：模型标签含 "low-cost" 或 pricing 中未设置价格（假设开源/低成本）
                tags = model.get("tags", [])
                pricing = model.get("pricing", {})
                new_val = "low-cost" in tags or not pricing
            elif action == "select_high_context":
                threshold = criteria.get("context_threshold", 128000)
                ctx = model.get("context_window", 0) or 0
                new_val = ctx >= threshold

            if new_val != prev:
                model["selected"] = new_val
                changed += 1

        if changed > 0:
            self.update_provider(provider_id, provider)

        return {"success": True, "changed": changed, "action": action}

    def set_provider_visibility(self, provider_id: str, visibility: str) -> Dict[str, Any]:
        """
        设置 provider 的 catalog visibility。

        Args:
            provider_id: 目标 provider ID。
            visibility: hidden | focused_only | always_visible | selected_models

        Returns:
            {"success": bool, "previous": str, "current": str}
        """
        valid = {"hidden", "focused_only", "always_visible", "selected_models"}
        if visibility not in valid:
            raise ValueError(f"Invalid visibility: {visibility}. Valid: {valid}")

        provider = self.get_provider(provider_id, include_secrets=True)
        if not provider:
            return {"success": False, "error": "Provider not found"}
        if is_provider_read_only(provider):
            raise ValueError("Codex login providers are read-only in Provider settings.")

        previous = provider.get("catalog_visibility", "focused_only")
        if previous == visibility:
            return {"success": True, "previous": previous, "current": visibility, "changed": False}

        provider["catalog_visibility"] = visibility
        self.update_provider(provider_id, provider)
        return {"success": True, "previous": previous, "current": visibility, "changed": True}

    def set_focus_provider(
        self,
        provider_id: str = "",
        extra_providers: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Persist the provider used for quick switching and focused routing.

        An empty provider_id clears the focus. Disabled providers can still be
        stored but will not be used by routing until re-enabled.
        """
        store = self._load_store()
        provider_id = str(provider_id or "").strip()
        previous = str(store.get("focus_provider_id") or "")
        if provider_id and not self._find_provider(store, provider_id) and not self._find_extra_provider(extra_providers, provider_id):
            return {"success": False, "error": "Provider not found", "focus_provider_id": previous}
        if previous == provider_id:
            return {"success": True, "focus_provider_id": provider_id, "previous": previous, "changed": False}
        store["focus_provider_id"] = provider_id
        self._save_store(store)
        return {"success": True, "focus_provider_id": provider_id, "previous": previous, "changed": True}

    def preview_catalog(
        self,
        focus_provider_id: str = "",
        extra_providers: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        生成 Unified Model Catalog（UMC）预览。

        设计意图：
          - 这是 Codex Enhance Manager 的核心功能之一：在真正写入 Codex 之前，
            让用户看到「哪些模型会出现在 Codex 的模型列表中」。
          - 支持四种 visibility 策略：hidden（隐藏）、focused_only（仅焦点时显示）、
            always_visible（常驻）、selected_models（仅选中模型显示）。
          - 去重机制：provider_id + model_id 组合为唯一键，防止同一模型被重复加入。

        Args:
            focus_provider_id: 当前焦点 provider ID。该 provider 下的所有启用模型
                               都会被强制纳入 Catalog，不受 visibility 限制。

        Returns:
            包含 entries、entry_count、route_explanation 的字典。
        """
        store = self._load_store()
        if not focus_provider_id:
            focus_provider_id = str(store.get("focus_provider_id") or "")
        return build_catalog_preview_from_providers(
            self._with_extra_providers(store.get("providers", []), extra_providers),
            focus_provider_id=focus_provider_id,
        )

    def preview_catalog_with_provider_draft(self, provider_id: str, draft: Dict[str, Any]) -> Dict[str, Any]:
        """
        Preview UMC with one provider replaced by an unsaved form draft.

        This is read-only: it merges the draft with the saved provider in memory
        so redacted secrets are preserved, then builds the same catalog preview.
        """
        store = self._load_store()
        providers: List[Dict[str, Any]] = []
        found = False
        for provider in store.get("providers", []):
            if provider.get("id") == provider_id:
                merged = merge_provider_update(provider, draft if isinstance(draft, dict) else {})
                merged["id"] = provider_id
                providers.append(merged)
                found = True
            else:
                providers.append(provider)
        if not found:
            return {
                "success": False,
                "error": "Provider not found",
                "focus_provider_id": provider_id,
                "entries": [],
                "entry_count": 0,
                "route_explanation": [],
                "generated_at": now_iso(),
                "preview": True,
            }

        preview = build_catalog_preview_from_providers(providers, focus_provider_id=provider_id)
        preview["success"] = True
        preview["preview"] = True
        return preview

    def export_bundle(self) -> Dict[str, Any]:
        """
        导出脱敏后的 provider bundle，供诊断和备份使用。

        设计意图：
          - 用户遇到问题时，可将此 bundle 发送给开发者，而无需担心 key 泄露。
          - 始终脱敏：即使调用方忘记处理，本函数也会强制 redact。
        """
        store = self._load_store()
        return {
            "schema_version": store.get("schema_version", SCHEMA_VERSION),
            "exported_at": now_iso(),
            "providers": redact_secrets(store.get("providers", [])),
        }

    def _load_store(self) -> Dict[str, Any]:
        """
        加载本地 provider store。

        工程权衡与边界条件：
          - 文件不存在：返回空 store（带有默认 schema），首次启动无报错。
          - JSON 损坏：保留旧文件（重命名为 .corrupted.<随机后缀>），供人工恢复；
            同时返回空 store 保证程序不崩溃。这是「数据安全第一」原则。
          - 类型防护：若 providers 列表中混入非字典元素（如旧版本 bug 导致），
            通过 isinstance 过滤丢弃，避免后续逻辑抛出 AttributeError。
          - 所有 provider 经过 normalize_provider：保证升级 schema 后旧数据也能
            自动获得新字段的默认值。

        Windows 平台特殊性：
          - rename 在 Windows 上若文件被占用可能失败，因此用 try/except 包裹，
            失败时静默回退到空 store，不阻断启动。
        """
        if not self.store_path.exists():
            return _empty_store()
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # JSON 损坏时保留旧文件供人工恢复，而非直接清空
            try:
                corrupted_path = self.store_path.with_suffix(f".json.corrupted.{uuid.uuid4().hex[:4]}")
                self.store_path.rename(corrupted_path)
            except Exception:
                pass
            return _empty_store()

        store = _empty_store()
        store.update(raw if isinstance(raw, dict) else {})
        # 逐条 normalize：防御旧数据或 schema 升级后的字段缺失
        store["providers"] = [normalize_provider(p) for p in store.get("providers", []) if isinstance(p, dict)]
        return store

    def _save_store(self, store: Dict[str, Any]):
        """
        原子写入 provider store。

        设计意图：
          - 使用「写临时文件 + 原子替换」模式，防止写一半进程崩溃导致 JSON 截断。
          - Windows 上 os.replace / Path.replace 可能在目标文件被其他进程（如
            防病毒软件、备份工具）锁定时抛出 PermissionError。

        工程权衡：
          - 3 次重试 + 50ms 退避：覆盖绝大多数临时锁场景。
          - 最终回退到 shutil.copy2 + unlink：copy2 在 Windows 上可覆盖只读文件，
            且保留权限；unlink 失败不影响数据完整性（tmp 文件残留无危害）。

        Args:
            store: 要写入的完整 store 字典。
        """
        store["schema_version"] = SCHEMA_VERSION
        store["updated_at"] = now_iso()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.store_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        # Windows 上若文件被其他进程锁定，replace 可能失败；短暂重试
        for _attempt in range(3):
            try:
                tmp_path.replace(self.store_path)
                break
            except PermissionError:
                import time
                time.sleep(0.05)
        else:
            # 最终回退：copy2 + 延迟删除
            import shutil
            shutil.copy2(str(tmp_path), str(self.store_path))
            try:
                tmp_path.unlink()
            except Exception:
                pass

    def _find_provider(self, store: Dict[str, Any], provider_id: str) -> Optional[Dict[str, Any]]:
        """在 store 中按 ID 查找 provider。返回深拷贝前的原始引用（内部使用）。"""
        return next((p for p in store.get("providers", []) if p.get("id") == provider_id), None)

    def _find_extra_provider(
        self,
        extra_providers: Optional[List[Dict[str, Any]]],
        provider_id: str,
    ) -> Optional[Dict[str, Any]]:
        provider_id = str(provider_id or "").strip()
        if not provider_id:
            return None
        return next((p for p in extra_providers or [] if isinstance(p, dict) and p.get("id") == provider_id), None)

    def _with_extra_providers(
        self,
        providers: List[Dict[str, Any]],
        extra_providers: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        result = [copy.deepcopy(p) for p in providers if isinstance(p, dict)]
        extras = [normalize_provider(p) for p in (extra_providers or []) if isinstance(p, dict)]
        if not extras:
            return result
        extra_ids = {p.get("id") for p in extras}
        result = [p for p in result if p.get("id") not in extra_ids]
        return result + extras

    def _allocate_provider_id(self, store: Dict[str, Any], provider: Dict[str, Any]) -> str:
        """
        为新建 provider 分配唯一 ID。

        设计意图：
          - 优先使用用户输入或 display_name 的 sanitize 结果，保持可读性。
          - 若冲突则追加数字后缀（-2, -3...），避免用户手动重试。

        边界条件：
          - 若所有候选字段均为空，回退到 provider-<随机8位hex>，保证绝不返回空串。

        Args:
            store: 当前 store（用于检查已有 ID）。
            provider: 待分配 ID 的 provider 数据。

        Returns:
            全局唯一的 provider ID 字符串。
        """
        requested = sanitize_id(provider.get("id") or provider.get("short_alias") or provider.get("display_name"))
        if not requested:
            requested = f"provider-{uuid.uuid4().hex[:8]}"
        existing = {p.get("id") for p in store.get("providers", [])}
        candidate = requested
        index = 2
        while candidate in existing:
            candidate = f"{requested}-{index}"
            index += 1
        return candidate

    def _validate_unique_alias(self, store: Dict[str, Any], alias: str, provider_id: str):
        """
        校验 short_alias 全局唯一。

        设计意图：
          - short_alias 是 UMC 中 codex_model_id 的前缀（如 "openai/gpt-5"），
            若重复会导致模型 ID 冲突和路由歧义。
          - 更新时排除自身 provider_id，允许「不改 alias」的更新通过。

        Raises:
            ValueError: alias 已被其他 provider 占用时抛出。
        """
        for provider in store.get("providers", []):
            if provider.get("id") != provider_id and provider.get("short_alias") == alias:
                raise ValueError(f"Provider short_alias already exists: {alias}")


def normalize_provider(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将任意 provider 输入消毒为标准 schema。

    设计意图：
      - 这是整个系统的「数据防火墙」：无论数据来自用户 UI、旧版本 store、
        preset 导入还是外部 API，都必须经过此函数才能进入内存。
      - 防御式默认值：任何缺失字段都会获得安全默认值（如 enabled=True），
        防止后续逻辑出现 KeyError。
      - 类型强制：所有字段在返回前被转换为预期类型（str、bool、int、dict）。

    工程权衡：
      - 使用 copy.deepcopy 隔离输入，防止调用方的后续修改意外影响本函数输出。
      - 模型列表为空时自动插入默认模型：保证每个 provider 至少有一个模型，
        避免下游 Catalog 构建出现空指针。

    Args:
        data: 原始 provider 字典，可能来自任何来源。

    Returns:
        完全符合 schema 的标准 provider 字典。
    """
    raw = copy.deepcopy(data or {})
    short_alias = sanitize_alias(raw.get("short_alias") or raw.get("id") or raw.get("display_name") or "provider")
    display_name = str(raw.get("display_name") or short_alias).strip()
    api_format = raw.get("api_format") if raw.get("api_format") in API_FORMATS else "openai_responses"
    auth_mode = raw.get("auth_mode") if raw.get("auth_mode") in AUTH_MODES else "provider_api_key"
    visibility = raw.get("catalog_visibility")
    if visibility not in CATALOG_VISIBILITY:
        visibility = "focused_only"

    headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
    user_agent = str(raw.get("user_agent") or headers.get("User-Agent") or "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent

    provider = {
        "id": sanitize_id(raw.get("id") or short_alias),
        "display_name": display_name,
        "kind": str(raw.get("kind") or "openai_compatible").strip(),
        "short_alias": short_alias,
        "base_url": str(raw.get("base_url") or "").strip(),
        "endpoint_overrides": raw.get("endpoint_overrides") if isinstance(raw.get("endpoint_overrides"), dict) else {},
        "api_format": api_format,
        "auth_mode": auth_mode,
        "codex_login": _coerce_bool(raw.get("codex_login"), False),
        "switch_only": _coerce_bool(raw.get("switch_only"), False),
        "amr_excluded": _coerce_bool(raw.get("amr_excluded"), False),
        "local_proxy_routing": _coerce_bool(raw.get("local_proxy_routing"), True),
        "routing_mode": str(raw.get("routing_mode") or "").strip(),
        "api_key": str(raw.get("api_key") or "").strip(),
        "secondary_usage_key": str(raw.get("secondary_usage_key") or "").strip(),
        "headers": headers,
        "user_agent": user_agent,
        "capabilities": normalize_capabilities(raw.get("capabilities")),
        "approval_profile": normalize_approval_profile(raw.get("approval_profile")),
        "responses_profile": normalize_responses_profile(raw.get("responses_profile")),
        "media_profile": normalize_media_profile(raw.get("media_profile")),
        "proxy_profile": normalize_proxy_profile(raw.get("proxy_profile") or raw.get("proxy")),
        "models": [normalize_model(m) for m in raw.get("models", []) if isinstance(m, dict)],
        "aliases": normalize_alias_map(raw.get("aliases") or raw.get("model_aliases")),
        "alias_patterns": normalize_alias_patterns(raw.get("alias_patterns") or raw.get("regex_aliases")),
        "health_check": raw.get("health_check") if isinstance(raw.get("health_check"), dict) else {},
        "quota_check": raw.get("quota_check") if isinstance(raw.get("quota_check"), dict) else {},
        "priority": int(raw.get("priority") if raw.get("priority") is not None else 100),
        "enabled": bool(raw.get("enabled", True)),
        "fallback_enabled": bool(raw.get("fallback_enabled", True)),
        "country_region": str(raw.get("country_region") or "").strip(),
        "native_currency": normalize_currency(raw.get("native_currency") or "USD"),
        "catalog_visibility": visibility,
        "status": normalize_status(raw.get("status")),
        "notes": str(raw.get("notes") or "").strip(),
        "caveat": str(raw.get("caveat") or "").strip(),
        "created_at": raw.get("created_at") or "",
        "updated_at": raw.get("updated_at") or "",
    }
    provider["capabilities"] = effective_provider_capabilities(provider)
    provider["read_only"] = is_provider_read_only(provider)
    provider["native_responses"] = is_native_responses_provider(provider)
    provider["native_capabilities_locked"] = has_locked_native_capabilities(provider)

    if not provider["models"]:
        provider["models"] = [normalize_model({"id": "default", "display_name": "Default model"})]
    return provider


def merge_provider_update(existing: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并 provider 更新，保护 secret 字段不被 REDACTED_VALUE 覆盖。

    设计意图与 bug fix：
      - 早期 bug：UI 显示 api_key 为 ********，用户修改其他字段后提交，
        后端直接用 update 覆盖 existing，导致 key 被替换为空或 ********。
      - 修复：识别 REDACTED_VALUE 并跳过这些字段，保留 existing 中的原值。
      - headers 同理：Authorization、X-Api-Key 等 header 若在 UI 被脱敏显示，
        提交时不应被覆盖。

    Args:
        existing: 当前存储的 provider 字典。
        update: 来自 UI 的更新字典，可能包含 REDACTED_VALUE。

    Returns:
        合并后的 provider 字典（再经 normalize_provider 消毒）。
    """
    merged = copy.deepcopy(existing)
    update_copy = copy.deepcopy(update or {})
    for key, value in update_copy.items():
        # 跳过脱敏的 secret 字段，防止 UI 的 ******** 覆盖真实 key
        if key in {"api_key", "secondary_usage_key"} and value == REDACTED_VALUE:
            continue
        if key == "headers" and isinstance(value, dict):
            merged_headers = dict(merged.get("headers") or {})
            for header_key, header_value in value.items():
                if header_value == REDACTED_VALUE:
                    continue
                merged_headers[header_key] = header_value
            merged[key] = merged_headers
            continue
        if key == "models" and isinstance(value, list):
            merged[key] = merge_model_updates(merged.get("models", []), value)
            continue
        merged[key] = value
    # 最终再过 normalize：确保合并后的数据仍然符合最新 schema
    return normalize_provider(merged)


def merge_model_updates(existing_models: Any, update_models: Any) -> List[Dict[str, Any]]:
    """
    Merge text-area model edits while preserving per-model metadata.

    The provider UI edits models as id|display|context|selected rows, so updates
    may omit capabilities, pricing, tags, aliases, and capability_overrides.
    Preserve those fields for models whose id still matches.
    """
    existing_by_id: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(existing_models, list):
        for model in existing_models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            existing_by_id.setdefault(model_id, []).append(copy.deepcopy(model))

    merged_models: List[Dict[str, Any]] = []
    if not isinstance(update_models, list):
        return merged_models
    for update_model in update_models:
        if not isinstance(update_model, dict):
            continue
        model_id = str(update_model.get("id") or "").strip()
        base: Dict[str, Any] = {}
        if model_id and existing_by_id.get(model_id):
            base = existing_by_id[model_id].pop(0)
        base.update(copy.deepcopy(update_model))
        merged_models.append(base)
    return merged_models


def normalize_model(data: Dict[str, Any]) -> Dict[str, Any]:
    model_id = str(data.get("id") or data.get("model") or "default").strip()
    capabilities = normalize_capabilities(data.get("capabilities"))
    capability_overrides = model_capability_overrides(data)
    context_window = int(data.get("context_window") or data.get("context") or 0)
    api_format = str(data.get("api_format") or data.get("interface") or "").strip()
    if api_format not in API_FORMATS:
        api_format = ""
    native_approval_raw = (
        data.get("native_approval")
        if "native_approval" in data
        else data.get("supports_native_approval", capability_overrides.get("native_approval", False))
    )
    native_approval = bool(native_approval_raw)
    if "native_approval" in data or "supports_native_approval" in data or native_approval:
        capability_overrides["native_approval"] = native_approval
    return {
        "id": model_id,
        "display_name": str(data.get("display_name") or model_id).strip(),
        "enabled": bool(data.get("enabled", True)),
        "selected": bool(data.get("selected", False)),
        "context_window": max(context_window, 0),
        "api_format": api_format,
        "native_approval": native_approval,
        "capabilities": capabilities,
        "capability_overrides": capability_overrides,
        "native_currency": normalize_currency(data.get("native_currency") or ""),
        "pricing": data.get("pricing") if isinstance(data.get("pricing"), dict) else {},
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "aliases": normalize_string_list(data.get("aliases") or data.get("model_aliases")),
        "reasoning_default": data.get("reasoning_default", ""),
    }


def normalize_capabilities(data: Any) -> Dict[str, bool]:
    """
    标准化 capabilities 字段。

    设计意图：
      - capabilities 描述 provider/model 支持的功能（text、vision、tools 等）。
      - 默认值策略：text/streaming/models 默认为 True（绝大多数 provider 支持），
        其余敏感功能默认 False，防止用户未明确开启时误用不支持的 API。
      - 保留自定义 capability：第三方 provider 可能有独特功能（如 "audio"），
        不静默丢弃，而是设为 True 并透传。

    Args:
        data: 可能为 list（如 ["text", "vision"]）、dict（如 {"text": True}）
              或 None/其他类型。

    Returns:
        完整的 capabilities 字典，所有已知键均为 bool 类型。
    """
    return _normalize_capabilities(data)


def normalize_string_map(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, item in value.items():
        key_str = str(key).strip()
        item_str = str(item).strip()
        if key_str and item_str:
            normalized[key_str] = item_str
    return normalized


def normalize_alias_map(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        return normalize_string_map(value)
    if not isinstance(value, list):
        return {}
    normalized: Dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("from") or item.get("alias") or "").strip()
        target = str(item.get("target") or item.get("to") or item.get("model") or "").strip()
        if source and target:
            normalized[source] = target
    return normalized


def normalize_alias_patterns(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or item.get("from") or "").strip()
        replacement = str(item.get("replacement") or item.get("to") or "").strip()
        if not pattern or not replacement:
            continue
        normalized.append({
            "pattern": pattern,
            "replacement": replacement,
            "enabled": bool(item.get("enabled", True)),
            "description": str(item.get("description") or "").strip(),
        })
    return normalized


def normalize_approval_profile(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    raw_mode = str(raw.get("mode") or "").strip()
    mode_source = "explicit" if raw_mode else "default"
    if not raw_mode:
        if raw.get("proxy_auto_approve") or raw.get("auto_approve") or raw.get("model_prompt_fallback"):
            raw_mode = "proxy_auto_approve"
            mode_source = "legacy"
        elif raw.get("official_guardian"):
            raw_mode = "official_guardian"
            mode_source = "legacy"
        else:
            raw_mode = "proxy_auto_approve"
    mode = raw_mode if raw_mode in APPROVAL_MODES else "proxy_auto_approve"
    if raw_mode not in APPROVAL_MODES:
        mode_source = "default"
    allowed_actions = normalize_string_list(raw.get("allowed_actions"))
    if not allowed_actions:
        allowed_actions = ["exec", "apply_patch", "network", "permissions", "mcp_tool"]
    on_review_error = str(raw.get("on_review_error") or "decline").strip()
    if on_review_error not in {"decline", "ask_user", "allow"}:
        on_review_error = "decline"
    try:
        timeout_ms = int(raw.get("timeout_ms") or 90000)
    except (TypeError, ValueError):
        timeout_ms = 90000
    try:
        max_retries = int(raw.get("max_retries") or 1)
    except (TypeError, ValueError):
        max_retries = 1
    try:
        decision_schema_version = int(raw.get("decision_schema_version") or 1)
    except (TypeError, ValueError):
        decision_schema_version = 1
    return {
        "mode": mode,
        "mode_source": mode_source,
        "official_guardian": mode == "official_guardian",
        "proxy_auto_approve": mode == "proxy_auto_approve",
        "reviewer_model": str(raw.get("reviewer_model") or "").strip(),
        "prompt_template_id": str(raw.get("prompt_template_id") or "codex_guardian_compatible").strip(),
        "decision_schema_version": max(decision_schema_version, 1),
        "risk_policy": str(raw.get("risk_policy") or "codex_guardian_compatible").strip(),
        "allowed_actions": allowed_actions,
        "require_structured_json": bool(raw.get("require_structured_json", True)),
        "auto_accept_low_risk": bool(raw.get("auto_accept_low_risk", True)),
        "auto_decline_high_risk": bool(raw.get("auto_decline_high_risk", True)),
        "on_review_error": on_review_error,
        "timeout_ms": max(timeout_ms, 1000),
        "max_retries": max(max_retries, 0),
        "audit_decisions": bool(raw.get("audit_decisions", True)),
    }


def normalize_media_profile(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    return {
        "default_image_provider": bool(raw.get("default_image_provider", False)),
        "default_video_provider": bool(raw.get("default_video_provider", False)),
        "openai_compatible_media": bool(raw.get("openai_compatible_media", False)),
        "adapter_required": bool(raw.get("adapter_required", False)),
        "adapter": str(raw.get("adapter") or "").strip(),
        "async_submit": bool(raw.get("async_submit", False)),
        "poll_required": bool(raw.get("poll_required", False)),
        "cancel_supported": bool(raw.get("cancel_supported", False)),
        "supports_url_output": bool(raw.get("supports_url_output", True)),
        "supports_base64_output": bool(raw.get("supports_base64_output", True)),
        "image_model_overrides": normalize_string_map(raw.get("image_model_overrides") or raw.get("image_overrides")),
        "video_model_overrides": normalize_string_map(raw.get("video_model_overrides") or raw.get("video_overrides")),
    }


def normalize_proxy_profile(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    try:
        timeout_seconds = int(raw.get("upstream_timeout_seconds") or raw.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        timeout_seconds = 0
    try:
        retry_attempts = int(raw.get("retry_attempts") or raw.get("max_retries") or 0)
    except (TypeError, ValueError):
        retry_attempts = 0
    try:
        retry_backoff_ms = int(raw.get("retry_backoff_ms") or 0)
    except (TypeError, ValueError):
        retry_backoff_ms = 0
    bypass = raw.get("bypass_system_proxy", raw.get("proxy_bypass", True))
    return {
        "bypass_system_proxy": _coerce_bool(bypass, True),
        "upstream_timeout_seconds": min(max(timeout_seconds, 0), 3600),
        "retry_attempts": min(max(retry_attempts, 0), 5),
        "retry_backoff_ms": min(max(retry_backoff_ms, 0), 30000),
    }


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


def normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def normalize_responses_profile(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    unsupported = raw.get("unsupported_fields")
    if not isinstance(unsupported, list):
        unsupported = []
    mode = responses_profile_mode(raw)
    return {
        "mode": mode,
        "native_responses": mode == "native",
        "profile_id": str(raw.get("profile_id") or "").strip(),
        "domestic_responses": bool(raw.get("domestic_responses", False)),
        "partial_compatibility": bool(raw.get("partial_compatibility", False)),
        "requires_adapter": bool(raw.get("requires_adapter", False)),
        "verified_docs_url": str(raw.get("verified_docs_url") or "").strip(),
        "compatibility_notes": str(raw.get("compatibility_notes") or "").strip(),
        "unsupported_fields": [str(item) for item in unsupported],
        "verified_features": normalize_string_list(raw.get("verified_features")),
        "partial_or_unsupported_features": normalize_string_list(raw.get("partial_or_unsupported_features")),
        "verified_event_types": normalize_string_list(raw.get("verified_event_types")),
        "allowed_tool_types": normalize_string_list(raw.get("allowed_tool_types")),
        "allowed_input_content_types": normalize_string_list(raw.get("allowed_input_content_types")),
    }


def normalize_status(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "last_tested": str(raw.get("last_tested") or ""),
        "last_error": str(raw.get("last_error") or ""),
        "needs_restart": bool(raw.get("needs_restart", False)),
        "source_of_truth": str(raw.get("source_of_truth") or "local provider registry"),
    }


def validate_provider(provider: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    本地 provider 配置校验。

    设计意图：
      - errors 是「阻断性问题」：保存前必须修复，否则 provider 可能无法工作。
      - warnings 是「建议性问题」：不影响保存，但会在 UI 中提示用户。
      - short_alias 正则：限制小写字母/数字/_/-，且必须以字母开头，
        保证 URL-safe 和文件系统安全（Windows 对大小写不敏感，但统一小写
        可避免跨平台不一致）。
      - 3 字母货币代码：ISO 4217 标准，用于后续成本计算统一货币单位。

    边界条件：
      - base_url 为空时仅 warning 而非 error：用户可能先创建 provider 再填地址。
      - api_key 为空时仅 warning：本地测试阶段可能不需要真实 key。

    Args:
        provider: 已 normalize 的 provider 字典。

    Returns:
        (errors 列表, warnings 列表)。
    """
    errors: List[str] = []
    warnings: List[str] = []
    if not provider.get("display_name"):
        errors.append("display_name is required")
    if not re.match(r"^[a-z][a-z0-9_-]{0,31}$", provider.get("short_alias", "")):
        errors.append("short_alias must start with a letter and use lowercase letters, numbers, _ or -")
    if provider.get("catalog_visibility") not in CATALOG_VISIBILITY:
        errors.append("catalog_visibility is invalid")
    if provider.get("api_format") not in API_FORMATS:
        errors.append("api_format is invalid")
    responses_profile = provider.get("responses_profile") or {}
    if responses_profile.get("domestic_responses") and not responses_profile.get("verified_docs_url"):
        warnings.append("domestic_responses is enabled but verified_docs_url is empty")
    if not re.match(r"^[A-Z]{3}$", provider.get("native_currency", "")):
        errors.append("native_currency must be a 3-letter currency code")
    if provider.get("enabled") and not provider.get("base_url") and provider.get("auth_mode") != "no_auth":
        warnings.append("base_url is empty; network health checks will be skipped until configured")
    if provider.get("auth_mode") == "provider_api_key" and not provider.get("api_key"):
        warnings.append("api_key is empty; save a key before enabling real upstream calls")
    capabilities = provider.get("capabilities") if isinstance(provider.get("capabilities"), dict) else {}
    media_profile = provider.get("media_profile") if isinstance(provider.get("media_profile"), dict) else {}
    media_requested = bool(
        capabilities.get("images")
        or capabilities.get("videos")
        or media_profile.get("default_image_provider")
        or media_profile.get("default_video_provider")
    )
    media_route_enabled = bool(
        media_profile.get("openai_compatible_media")
        or media_profile.get("adapter_required")
        or provider.get("api_format") in {"openai_images", "openai_videos"}
    )
    if media_requested and not media_route_enabled:
        warnings.append("media capability is enabled, but Media Mode is disabled; image/video routes will not be forwarded")
    if media_route_enabled and not media_requested and provider.get("api_format") not in {"openai_images", "openai_videos"}:
        warnings.append("Media Mode is enabled, but no Images/Videos capability or default media provider is selected; media routes will still appear unsupported")
    if not provider.get("models"):
        warnings.append("no models configured")
    return errors, warnings


def redact_secrets(value: Any) -> Any:
    """
    递归脱敏字典/列表中的 secret 字段。

    设计意图：
      - 递归处理：secret 可能嵌套在 headers、config、metadata 等任意层级。
      - 空值保留空串：若 api_key 本来就是空，脱敏后显示 "" 而非 ********，
        帮助用户区分「未设置」和「已设置但被隐藏」。

    Args:
        value: 任意 JSON-serializable 数据。

    Returns:
        结构相同但 secret 字段被替换后的数据。
    """
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if is_secret_key(key):
                redacted[key] = REDACTED_VALUE if item else ""
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    return value


def is_secret_key(key: str) -> bool:
    """
    判断字段名是否疑似 secret 字段。

    实现细节：
      - 先将字段名按非字母数字字符拆分（如 "x-api-key" -> ["x", "api", "key"]）。
      - 生成单字词和双字词组合（如 "api_key"），加入候选集。
      - 与 _SECRET_FIELD_HINTS 做交集判断。

    边界条件：
      - "monkey" 不会匹配：拆分后为 ["monkey"]，不在 HINTS 中。
      - "x_api_key" 会匹配：拆分后生成 "api_key" 在 HINTS 中。

    Args:
        key: 字段名字符串。

    Returns:
        是否疑似 secret 字段。
    """
    tokens = re.split(r"[^a-zA-Z0-9]+", key.lower())
    candidates = set(tokens)
    for i in range(len(tokens) - 1):
        candidates.add(tokens[i] + "_" + tokens[i + 1])
    return bool(candidates & _SECRET_FIELD_HINTS)


def sanitize_alias(value: Any) -> str:
    """
    消毒 short_alias：生成 URL-safe、文件系统安全的别名。

    规则：
      - 只保留小写字母、数字、下划线、连字符。
      - 必须以字母开头（确保可作为合法变量名/URL 段）。
      - 长度限制 32：避免过长别名在 UI 中换行或作为文件名时超限
        （Windows 传统 FAT32 对长文件名支持有限，虽然 NTFS 无此限制，
         但 32 字符是视觉舒适区）。

    Args:
        value: 任意输入值。

    Returns:
        消毒后的 alias 字符串。
    """
    raw = str(value or "provider").lower().strip()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    if not raw or not raw[0].isalpha():
        raw = "p-" + raw
    return raw[:32]


def sanitize_id(value: Any) -> str:
    """
    消毒 provider ID。

    与 sanitize_alias 的区别：
      - ID 允许更长（64 字符），因为不需要在 UI 中频繁展示。
      - 若输入为空，回退到 provider-<随机hex>，而非 "p-" 前缀，
        因为 ID 需要全局唯一，随机性比可读性更重要。

    Args:
        value: 任意输入值。

    Returns:
        消毒后的 ID 字符串。
    """
    raw = str(value or "").lower().strip()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    if not raw:
        raw = "provider-" + uuid.uuid4().hex[:8]
    return raw[:64]


def normalize_currency(value: Any) -> str:
    raw = str(value or "").upper().strip()
    if not raw:
        return ""
    return raw[:3]


def deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_store() -> Dict[str, Any]:
    """返回空 store 模板，用于首次启动或 JSON 损坏后的恢复。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "providers": [],
        "focus_provider_id": "",
        "updated_at": "",
    }


def build_catalog_preview_from_providers(
    providers: List[Dict[str, Any]],
    focus_provider_id: str = "",
) -> Dict[str, Any]:
    """
    Build a Unified Model Catalog preview from an in-memory provider list.

    Keeping this pure makes saved previews and unsaved draft previews share the
    exact same inclusion, capability inheritance, and collision logic.
    """
    entries: List[Dict[str, Any]] = []
    explanations: List[str] = []
    seen: set[str] = set()

    for provider in providers:
        if not provider_allows_local_routing(provider):
            continue
        visibility = provider.get("catalog_visibility", "focused_only")
        is_focused = bool(focus_provider_id and provider.get("id") == focus_provider_id)
        if visibility == "hidden":
            continue

        selected_only = visibility == "selected_models" and not is_focused
        include_all = visibility == "always_visible" or is_focused
        if not include_all and visibility not in {"selected_models", "focused_only"}:
            continue
        if visibility == "focused_only" and not is_focused:
            continue

        for model in provider.get("models", []):
            if not isinstance(model, dict) or not model.get("enabled", True):
                continue
            if selected_only and not model.get("selected", False):
                continue

            key = f"{provider.get('id')}::{model.get('id')}"
            if key in seen:
                continue
            seen.add(key)

            entry = _catalog_entry(provider, model, is_focused=is_focused)
            entries.append(entry)
            if is_focused:
                reason = "focus provider"
            elif visibility == "always_visible":
                reason = "always visible provider"
            elif visibility == "selected_models":
                reason = "selected model"
            else:
                reason = visibility
            explanations.append(f"{entry['codex_model_id']} included by {reason}.")

    explanations.extend(resolve_catalog_id_collisions(entries))

    return {
        "focus_provider_id": focus_provider_id,
        "entries": entries,
        "entry_count": len(entries),
        "route_explanation": explanations,
        "generated_at": now_iso(),
    }


def _catalog_entry(provider: Dict[str, Any], model: Dict[str, Any], is_focused: bool = False) -> Dict[str, Any]:
    """
    生成单个 UMC Catalog 条目。

    设计意图：
      - codex_model_id 采用 alias/upstream_model_id 格式，与 Codex 官方
        model ID 风格一致（如 "gpt-5"、"o3-mini"），便于用户理解和路由匹配。
      - capabilities 继承策略：模型级 capability 优先，缺失时回退到 provider 级。
        这允许为同一 provider 的不同模型设置不同能力（如 Qwen-VL 支持 vision，
        Qwen-Text 不支持）。

    Args:
        provider: provider 字典。
        model: model 字典。
        is_focused: 是否为当前焦点 provider。

    Returns:
        UMC entry 字典。
    """
    alias = provider.get("short_alias") or provider.get("id")
    upstream_model_id = model.get("id") or "default"
    pricing: Dict[str, Any] = {}
    if isinstance(provider.get("pricing"), dict):
        pricing.update(copy.deepcopy(provider["pricing"]))
    if isinstance(model.get("pricing"), dict):
        pricing.update(copy.deepcopy(model["pricing"]))
    return {
        "codex_model_id": f"{alias}/{upstream_model_id}",
        "display_name": model.get("display_name") or upstream_model_id,
        "provider_id": provider.get("id"),
        "provider_alias": alias,
        "provider_display_name": provider.get("display_name"),
        "upstream_model_id": upstream_model_id,
        "api_format": model.get("api_format") or provider.get("api_format"),
        "provider_api_format": provider.get("api_format"),
        "model_api_format": model.get("api_format") or "",
        "api_format_source": "model" if model.get("api_format") else "provider",
        "responses_profile": provider.get("responses_profile", {}),
        "context_window": model.get("context_window", 0),
        "capabilities": merge_provider_model_capabilities(provider, model),
        "native_currency": model.get("native_currency") or provider.get("native_currency") or pricing.get("native_currency"),
        "pricing": pricing,
        "has_model_pricing": bool(isinstance(model.get("pricing"), dict) and model.get("pricing")),
        "catalog_visibility": provider.get("catalog_visibility"),
        "focused": is_focused,
    }


PROVIDER_PRESETS: List[Dict[str, Any]] = [
    {
        "preset_id": "openai-compatible-responses",
        "name": "OpenAI-compatible Responses",
        "category": "text",
        "description": "Generic OpenAI-compatible provider. Fill in the provider URL and key.",
        "provider": {
            "id": "openai-compatible",
            "display_name": "OpenAI-compatible Responses",
            "kind": "openai_compatible",
            "short_alias": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_format": "openai_responses",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "custom_tools": True,
                "reasoning": True,
                "streaming": True,
                "compact": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "verified_docs_url": "https://platform.openai.com/docs/api-reference/responses",
                "compatibility_notes": "Generic OpenAI-compatible setup. Verify tools and media support before daily use.",
                "unsupported_fields": [],
            },
            "models": [
                {
                    "id": "gpt-5",
                    "display_name": "GPT-5",
                    "selected": True,
                    "context_window": 256000,
                    "capabilities": {"text": True, "vision": True, "tools": True, "reasoning": True, "streaming": True},
                    "native_currency": "USD",
                }
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
        },
    },
    {
        "preset_id": "custom-openai-chat",
        "name": "Custom OpenAI Chat",
        "category": "text",
        "description": "Chat Completions compatible provider. Responses adapter will be added later.",
        "provider": {
            "id": "custom-chat",
            "display_name": "Custom Chat Provider",
            "kind": "custom",
            "short_alias": "custom",
            "base_url": "",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "",
            "catalog_visibility": "focused_only",
            "capabilities": {"text": True, "streaming": True, "tools": True, "models": True},
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": True,
                "compatibility_notes": "Chat provider; Responses support requires adapter.",
                "unsupported_fields": ["native_responses"],
            },
            "models": [{"id": "custom-chat-model", "display_name": "Custom Chat Model", "selected": True, "context_window": 128000}],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
        },
    },
    {
        "preset_id": "openai-compatible-images",
        "name": "OpenAI-compatible Images",
        "category": "media",
        "description": "OpenAI Image API compatible provider for /v1/images/generations, edits, and variations.",
        "provider": {
            "id": "openai-images",
            "display_name": "OpenAI-compatible Images",
            "kind": "openai_compatible_media",
            "short_alias": "img",
            "base_url": "https://api.openai.com/v1",
            "api_format": "openai_images",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "hidden",
            "capabilities": {"text": False, "images": True, "models": True},
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Dedicated OpenAI-compatible Image API provider; not a text Responses provider.",
                "unsupported_fields": [],
            },
            "media_profile": {
                "default_image_provider": True,
                "default_video_provider": False,
                "openai_compatible_media": True,
                "adapter_required": False,
                "async_submit": False,
                "poll_required": False,
                "supports_url_output": True,
                "supports_base64_output": True,
            },
            "models": [
                {"id": "gpt-image-1.5", "display_name": "GPT Image 1.5", "selected": True, "capabilities": {"text": False, "images": True}, "native_currency": "USD"},
                {"id": "gpt-image-1", "display_name": "GPT Image 1", "selected": False, "capabilities": {"text": False, "images": True}, "native_currency": "USD"},
                {"id": "dall-e-3", "display_name": "DALL-E 3", "selected": False, "capabilities": {"text": False, "images": True}, "native_currency": "USD"},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "Pass-through only. Provider-specific image adapters must be verified before enabling adapter mode.",
        },
    },
    {
        "preset_id": "openai-compatible-videos",
        "name": "OpenAI-compatible Videos",
        "category": "media",
        "description": "OpenAI Video API compatible provider for /v1/videos submit/retrieve/delete.",
        "provider": {
            "id": "openai-videos",
            "display_name": "OpenAI-compatible Videos",
            "kind": "openai_compatible_media",
            "short_alias": "vid",
            "base_url": "https://api.openai.com/v1",
            "api_format": "openai_videos",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "hidden",
            "capabilities": {"text": False, "videos": True, "models": True},
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Dedicated OpenAI-compatible Video API provider; not a text Responses provider.",
                "unsupported_fields": [],
            },
            "media_profile": {
                "default_image_provider": False,
                "default_video_provider": True,
                "openai_compatible_media": True,
                "adapter_required": False,
                "async_submit": True,
                "poll_required": True,
                "cancel_supported": False,
                "supports_url_output": True,
                "supports_base64_output": False,
            },
            "models": [
                {"id": "sora-2", "display_name": "Sora 2", "selected": True, "capabilities": {"text": False, "videos": True}, "native_currency": "USD"},
                {"id": "sora-2-pro", "display_name": "Sora 2 Pro", "selected": False, "capabilities": {"text": False, "videos": True}, "native_currency": "USD"},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "Pass-through only. Video jobs are async; retrieve/delete endpoints are forwarded to the same provider.",
        },
    },
    {
        "preset_id": "custom-openai-responses",
        "name": "Custom OpenAI Responses",
        "category": "text",
        "description": "Custom OpenAI-compatible provider.",
        "provider": {
            "id": "custom-responses",
            "display_name": "Custom Responses Provider",
            "kind": "custom",
            "short_alias": "resp",
            "base_url": "",
            "api_format": "openai_responses",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "",
            "catalog_visibility": "focused_only",
            "capabilities": {"text": True, "vision": True, "streaming": True, "models": True},
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Custom OpenAI-compatible provider. Verify tools, compact mode, and media support before daily use.",
                "unsupported_fields": [],
            },
            "models": [{"id": "custom-responses-model", "display_name": "Custom Responses Model", "selected": True, "context_window": 128000}],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
        },
    },
    {
        "preset_id": "codex-api-key-mixin",
        "name": "Local Proxy Media Bridge",
        "category": "media",
        "description": "Use one local proxy provider for text and OpenAI-compatible image generation, with account settings kept separate from provider keys.",
        "provider": {
            "id": "codex-api-key-mixin",
            "display_name": "Local Proxy Media Bridge",
            "kind": "custom",
            "short_alias": "mix",
            "base_url": "https://your-custom-gateway.example.com/v1",
            "api_format": "openai_responses",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": True,
                "streaming": True,
                "images": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Use this when the provider can handle text requests and optional image generation through the same local proxy. Verify tools and media support before daily use.",
                "unsupported_fields": [],
            },
            "media_profile": {
                "default_image_provider": True,
                "default_video_provider": False,
                "openai_compatible_media": True,
                "adapter_required": False,
                "async_submit": False,
                "poll_required": False,
                "supports_url_output": True,
                "supports_base64_output": True,
            },
            "models": [
                {"id": "auto", "display_name": "Auto / upstream default", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "本地代理增强：不会改登录文件；如需生成图片，请确认供应商支持 OpenAI 兼容的图片接口。",
            "notes": "适合把文本模型和图片模型放在同一个本地代理里；如果上游没有图片接口，可在图片/视频设置里启用全局兜底。",
        },
    },
    {
        "preset_id": "alibaba-bailian-text-media",
        "name": "Alibaba Bailian / DashScope",
        "category": "domestic",
        "description": "Placeholder preset for Bailian text and media. Region-specific compatible-mode URLs must be confirmed before live use.",
        "provider": {
            "id": "alibaba-bailian",
            "display_name": "Alibaba Bailian",
            "kind": "alibaba_bailian",
            "short_alias": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {"text": True, "vision": True, "streaming": True, "tools": True, "images": True, "videos": True, "models": True},
            "responses_profile": {
                "profile_id": "alibaba_bailian",
                "domestic_responses": True,
                "partial_compatibility": True,
                "requires_adapter": True,
                "verified_docs_url": "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses",
                "compatibility_notes": "Official Bailian docs confirm OpenAI-compatible /compatible-mode/v1/responses, previous_response_id, and output_text streaming events. It is still marked partial: Codex custom tools, compact, and media item routing need adapter probes before real routing.",
                "unsupported_fields": ["codex_custom_tools_until_adapter_verified", "compact_until_verified", "media_items_until_adapter_verified"],
                "verified_features": ["text_input_output", "streaming_response_output_text_delta", "previous_response_id", "input_image", "function_tools", "code_interpreter", "web_search", "mcp_tool", "json_mode", "structured_outputs"],
                "verified_event_types": ["response.output_text.delta", "response.completed"],
                "allowed_tool_types": ["function", "web_search", "code_interpreter", "mcp"],
                "allowed_input_content_types": ["input_text", "output_text", "text", "input_image"],
            },
            "media_profile": {
                "default_image_provider": True,
                "default_video_provider": True,
                "openai_compatible_media": False,
                "adapter_required": True,
                "adapter": "alibaba_bailian",
                "async_submit": True,
                "poll_required": True,
            },
            "models": [
                {"id": "qwen3-coder-plus", "display_name": "Qwen3 Coder Plus", "selected": True, "context_window": 128000, "native_currency": "CNY"},
                {"id": "qwen-vl-plus", "display_name": "Qwen VL Plus", "selected": False, "context_window": 128000, "capabilities": {"text": True, "vision": True, "streaming": True}, "native_currency": "CNY"},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "notes": "Public docs indicate OpenAI-compatible APIs for text, image, audio, and video; exact media payloads need doc/login verification.",
        },
    },
    {
        "preset_id": "volcengine-ark-text-media",
        "name": "Volcengine Ark",
        "category": "domestic",
        "description": "Placeholder preset for Ark text plus Seedream/Seedance media adapters.",
        "provider": {
            "id": "volcengine-ark",
            "display_name": "Volcengine Ark",
            "kind": "volcengine_ark",
            "short_alias": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "focused_only",
            "capabilities": {"text": True, "vision": True, "streaming": True, "tools": True, "images": True, "videos": True, "models": True},
            "responses_profile": {
                "profile_id": "volcengine_ark",
                "domestic_responses": True,
                "partial_compatibility": True,
                "requires_adapter": True,
                "verified_docs_url": "https://www.volcengine.com/docs/82379/1585128?lang=zh",
                "compatibility_notes": "Official Ark Responses docs entry is recorded from the user-provided URL. Treat as partial compatibility: payload details, stream lifecycle, tools, and media behavior must be re-verified from readable docs/source before real routing.",
                "unsupported_fields": ["payload_until_verified", "stream_lifecycle_until_verified", "tools_until_verified", "media_items_until_adapter_verified"],
                "verified_features": ["text_input_output", "streaming_response_events", "previous_response_id", "input_image", "function_tools", "web_search", "image_process_tool", "knowledge_search_tool"],
                "verified_event_types": ["response.created", "response.reasoning_summary_part.added", "response.reasoning_summary_text_delta"],
                "allowed_tool_types": ["function", "web_search", "image_process", "knowledge_search"],
                "allowed_input_content_types": ["input_text", "output_text", "text", "input_image"],
            },
            "media_profile": {
                "default_image_provider": False,
                "default_video_provider": False,
                "openai_compatible_media": False,
                "adapter_required": True,
                "adapter": "volcengine_ark",
                "async_submit": True,
                "poll_required": True,
            },
            "models": [
                {"id": "doubao-seed-1-6", "display_name": "Doubao Seed", "selected": True, "context_window": 128000, "native_currency": "CNY"},
                {"id": "seedream", "display_name": "Seedream Image", "selected": False, "capabilities": {"text": False, "images": True}, "native_currency": "CNY"},
                {"id": "seedance", "display_name": "Seedance Video", "selected": False, "capabilities": {"text": False, "videos": True}, "native_currency": "CNY"},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "notes": "Seedream/Seedance payloads require implementation-time doc verification.",
        },
    },
    {
        "preset_id": "openrouter",
        "name": "OpenRouter",
        "category": "aggregator",
        "description": "OpenRouter 聚合平台，提供多供应商模型的统一接入。",
        "provider": {
            "id": "openrouter",
            "display_name": "OpenRouter",
            "kind": "openai_compatible",
            "short_alias": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": True,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "OpenRouter 使用 OpenAI 兼容格式，支持多供应商模型路由。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "openai/gpt-4o", "display_name": "OpenAI GPT-4o", "selected": True, "context_window": 128000, "capabilities": {"text": True, "vision": True, "tools": True, "reasoning": True, "streaming": True}},
                {"id": "anthropic/claude-3.5-sonnet", "display_name": "Anthropic Claude 3.5 Sonnet", "selected": True, "context_window": 200000, "capabilities": {"text": True, "vision": True, "tools": True, "reasoning": True, "streaming": True}},
                {"id": "google/gemini-pro", "display_name": "Google Gemini Pro", "selected": True, "context_window": 128000, "capabilities": {"text": True, "vision": True, "tools": True, "reasoning": True, "streaming": True}},
                {"id": "meta-llama/llama-3-70b", "display_name": "Meta Llama 3 70B", "selected": True, "context_window": 128000, "capabilities": {"text": True, "vision": False, "tools": True, "reasoning": True, "streaming": True}},
                {"id": "deepseek/deepseek-chat", "display_name": "DeepSeek Chat", "selected": True, "context_window": 128000, "capabilities": {"text": True, "vision": False, "tools": True, "reasoning": True, "streaming": True}},
            ],
            "headers": {"User-Agent": "OpenAI-Compatible-Client/1.0"},
            "user_agent": "OpenAI-Compatible-Client/1.0",
            "caveat": "OpenRouter 提供多供应商聚合，某些模型可能有速率限制或可用性波动。",
            "notes": "OpenRouter 是一个第三方聚合平台，支持访问多个提供商的模型。",
        },
    },
    {
        "preset_id": "deepseek-official",
        "name": "DeepSeek Official",
        "category": "domestic",
        "description": "DeepSeek 官方 API，使用 Chat Completions 格式。",
        "provider": {
            "id": "deepseek-official",
            "display_name": "DeepSeek Official",
            "kind": "openai_compatible",
            "short_alias": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": False,
                "tools": True,
                "reasoning": True,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "DeepSeek 官方 API 使用 Chat Completions 格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "deepseek-chat", "display_name": "DeepSeek Chat", "selected": True, "context_window": 128000},
                {"id": "deepseek-coder", "display_name": "DeepSeek Coder", "selected": True, "context_window": 128000},
                {"id": "deepseek-reasoner", "display_name": "DeepSeek Reasoner", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "DeepSeek 官方 API 使用 Chat Completions 格式，不支持原生 Responses API。",
            "notes": "DeepSeek 提供高性能大语言模型，deepseek-reasoner 支持推理能力。",
        },
    },
    {
        "preset_id": "moonshot-kimi",
        "name": "Moonshot AI (Kimi)",
        "category": "domestic",
        "description": "Moonshot AI Kimi 模型，使用 OpenAI 兼容格式。",
        "provider": {
            "id": "moonshot-kimi",
            "display_name": "Moonshot AI (Kimi)",
            "kind": "openai_compatible",
            "short_alias": "kimi",
            "base_url": "https://api.moonshot.cn/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Moonshot API 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "kimi-k2", "display_name": "Kimi K2", "selected": True, "context_window": 256000},
                {"id": "moonshot-v1-8k", "display_name": "Moonshot V1 8K", "selected": False, "context_window": 8192},
                {"id": "moonshot-v1-32k", "display_name": "Moonshot V1 32K", "selected": False, "context_window": 32768},
                {"id": "moonshot-v1-128k", "display_name": "Moonshot V1 128K", "selected": False, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "Moonshot API 使用 OpenAI 兼容格式，支持 tool calling 和 vision input。",
            "notes": "Moonshot Kimi 系列模型在长文本处理方面表现优异。",
        },
    },
    {
        "preset_id": "zhipu-glm",
        "name": "Zhipu AI (GLM)",
        "category": "domestic",
        "description": "智谱 AI GLM 系列模型，使用 OpenAI 兼容格式。",
        "provider": {
            "id": "zhipu-glm",
            "display_name": "Zhipu AI (GLM)",
            "kind": "openai_compatible",
            "short_alias": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "智谱 GLM API 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "glm-4", "display_name": "GLM-4", "selected": True, "context_window": 128000},
                {"id": "glm-4v", "display_name": "GLM-4V", "selected": True, "context_window": 128000, "capabilities": {"text": True, "vision": True, "tools": True, "streaming": True}},
                {"id": "glm-4-flash", "display_name": "GLM-4 Flash", "selected": False, "context_window": 128000},
                {"id": "codegeex-4", "display_name": "CodeGeeX-4", "selected": False, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "智谱 GLM API 使用 OpenAI 兼容格式，glm-4v 支持 vision input。",
            "notes": "智谱 AI 的 GLM 系列模型在中文理解和代码生成方面具有优势。",
        },
    },
    {
        "preset_id": "siliconflow",
        "name": "SiliconFlow",
        "category": "domestic",
        "description": "SiliconFlow 提供多种开源模型的统一 API 接入。",
        "provider": {
            "id": "siliconflow",
            "display_name": "SiliconFlow",
            "kind": "openai_compatible",
            "short_alias": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": False,
                "tools": True,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "SiliconFlow 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "Qwen/Qwen2.5-72B-Instruct", "display_name": "Qwen2.5 72B Instruct", "selected": True, "context_window": 128000},
                {"id": "deepseek-ai/DeepSeek-V3", "display_name": "DeepSeek V3", "selected": True, "context_window": 128000},
                {"id": "meta-llama/Llama-3.3-70B-Instruct", "display_name": "Llama 3.3 70B Instruct", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "SiliconFlow 提供多种开源模型的统一 API 接入。",
            "notes": "SiliconFlow 聚合了多个开源大模型，便于快速切换和对比。",
        },
    },
    {
        "preset_id": "minimax",
        "name": "MiniMax",
        "category": "domestic",
        "description": "MiniMax API，部分功能可能与 OpenAI 标准有差异。",
        "provider": {
            "id": "minimax",
            "display_name": "MiniMax",
            "kind": "openai_compatible",
            "short_alias": "minimax",
            "base_url": "https://api.minimax.chat/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": False,
                "tools": False,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "MiniMax API 部分功能可能与 OpenAI 标准有差异。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "abab6.5s-chat", "display_name": "abab6.5s Chat", "selected": True, "context_window": 128000},
                {"id": "abab6-chat", "display_name": "abab6 Chat", "selected": False, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "MiniMax API 部分功能可能与 OpenAI 标准有差异。",
            "notes": "MiniMax 提供 abab 系列大模型，适用于文本生成场景。",
        },
    },
    {
        "preset_id": "azure-openai",
        "name": "Azure OpenAI Service",
        "category": "text",
        "description": "Azure OpenAI Service，需要替换 base_url 中的 resource name 和 deployment id。",
        "provider": {
            "id": "azure-openai",
            "display_name": "Azure OpenAI Service",
            "kind": "openai_compatible",
            "short_alias": "azure",
            "base_url": "https://{your-resource-name}.openai.azure.com/openai/deployments/{deployment-id}",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Azure OpenAI uses Chat Completions format.",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "gpt-4o", "display_name": "GPT-4o", "selected": True, "context_window": 128000},
                {"id": "gpt-4", "display_name": "GPT-4", "selected": True, "context_window": 128000},
                {"id": "gpt-35-turbo", "display_name": "GPT-3.5 Turbo", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1", "api-key": "${AZURE_API_KEY}"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "Azure OpenAI 需要设置 api_version 查询参数（如 2024-10-21），base_url 中的 resource name 和 deployment id 必须替换为实际值。不支持原生 Responses API，使用 Chat Completions 格式。",
            "notes": "Azure OpenAI 提供企业级的 OpenAI 模型托管服务。",
        },
    },
    {
        "preset_id": "custom-responses",
        "name": "Custom OpenAI Responses Compatible",
        "category": "text",
        "description": "自定义 OpenAI Responses 兼容网关。需要确认上游功能支持情况。",
        "provider": {
            "id": "custom-responses",
            "display_name": "Custom Responses Compatible",
            "kind": "custom",
            "short_alias": "custom-resp",
            "base_url": "https://your-custom-gateway.example.com/v1",
            "api_format": "openai_responses",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": True,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "Custom OpenAI-compatible provider. Verify tools, compact mode, and media support before daily use.",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "auto", "display_name": "Auto", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "自定义 Responses 兼容网关。需要确认上游是否完整支持 tools、custom tools、streaming terminal events、previous_response_id。",
            "notes": "用于接入私有或第三方的 OpenAI Responses 兼容端点。",
        },
    },
    {
        "preset_id": "modelscope",
        "name": "ModelScope",
        "category": "domestic",
        "description": "魔搭社区提供多种开源模型推理服务。",
        "provider": {
            "id": "modelscope",
            "display_name": "ModelScope",
            "kind": "openai_compatible",
            "short_alias": "modelscope",
            "base_url": "https://www.modelscope.cn/api/v1/studio",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": False,
                "tools": False,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "ModelScope 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "qwen2.5-72b-instruct", "display_name": "Qwen2.5 72B Instruct", "selected": True, "context_window": 128000},
                {"id": "llama3.1-70b-instruct", "display_name": "Llama 3.1 70B Instruct", "selected": True, "context_window": 128000},
                {"id": "glm-4-9b-chat", "display_name": "GLM-4 9B Chat", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "ModelScope 提供多种开源模型推理服务。具体 base URL 和模型可用性请以 ModelScope 官方文档为准。",
            "notes": "魔搭社区是阿里云旗下的开源模型平台。",
        },
    },
    {
        "preset_id": "stepfun",
        "name": "StepFun",
        "category": "domestic",
        "description": "阶跃星辰 StepFun API，使用 OpenAI 兼容格式。",
        "provider": {
            "id": "stepfun",
            "display_name": "StepFun",
            "kind": "openai_compatible",
            "short_alias": "stepfun",
            "base_url": "https://api.stepfun.com/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "CNY",
            "country_region": "CN",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": True,
                "tools": True,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "StepFun API 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "step-1-8k", "display_name": "Step-1 8K", "selected": True, "context_window": 8192},
                {"id": "step-1-32k", "display_name": "Step-1 32K", "selected": True, "context_window": 32768},
                {"id": "step-1-128k", "display_name": "Step-1 128K", "selected": True, "context_window": 128000},
                {"id": "step-1-256k", "display_name": "Step-1 256K", "selected": True, "context_window": 256000},
                {"id": "step-1v-32k", "display_name": "Step-1V 32K", "selected": True, "context_window": 32768, "capabilities": {"text": True, "vision": True, "tools": True, "streaming": True}},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "阶跃星辰 StepFun API 使用 OpenAI 兼容格式。step-1v 系列支持 vision input。",
            "notes": "阶跃星辰提供 Step-1 系列大语言模型。",
        },
    },
    {
        "preset_id": "nvidia-build",
        "name": "NVIDIA Build",
        "category": "text",
        "description": "NVIDIA build endpoint，提供多种开源模型推理。",
        "provider": {
            "id": "nvidia-build",
            "display_name": "NVIDIA Build",
            "kind": "openai_compatible",
            "short_alias": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_format": "openai_chat",
            "auth_mode": "provider_api_key",
            "native_currency": "USD",
            "country_region": "US",
            "catalog_visibility": "selected_models",
            "capabilities": {
                "text": True,
                "vision": False,
                "tools": False,
                "reasoning": False,
                "streaming": True,
                "models": True,
            },
            "responses_profile": {
                "domestic_responses": False,
                "partial_compatibility": False,
                "requires_adapter": False,
                "compatibility_notes": "NVIDIA build endpoint 使用 OpenAI 兼容格式。",
                "unsupported_fields": [],
            },
            "models": [
                {"id": "meta/llama3-70b-instruct", "display_name": "Meta Llama 3 70B Instruct", "selected": True, "context_window": 128000},
                {"id": "meta/llama3-8b-instruct", "display_name": "Meta Llama 3 8B Instruct", "selected": True, "context_window": 128000},
                {"id": "nvidia/nemotron-4-340b-instruct", "display_name": "NVIDIA Nemotron-4 340B Instruct", "selected": True, "context_window": 128000},
            ],
            "headers": {"User-Agent": "Codex-Enhance-Manager/0.1"},
            "user_agent": "Codex-Enhance-Manager/0.1",
            "caveat": "NVIDIA build endpoint 提供多种开源模型推理。需要 NVIDIA API 密钥。",
            "notes": "NVIDIA 提供企业级的开源模型推理端点。",
        },
    },
]
