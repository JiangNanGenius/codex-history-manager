"""
amr_registry.py - AMR (Adaptive Model Rotation) 旋转组持久化注册表。
AMR 旋转组持久化存储与动态构建模块。

设计意图：
  - 本模块为 AdaptiveModelRotation 引擎提供「配置持久化层」，解决
    model_rotation.py 中 groups 仅驻留内存、进程重启后丢失的问题。
  - 与 ProviderRegistry 平行：同样采用 JSON 文件、schema_version、
    normalize + CRUD + 原子写入的架构，降低心智负担。
  - 支持从 ProviderRegistry 一键同步：将当前启用的 provider/model
    自动转换为 rotation candidates，减少手动维护成本。

工程权衡：
  - 使用纯 JSON 而非 SQLite：group 数量极少（通常 <20），JSON 便于
    人工阅读和版本控制；与 providers.json 保持一致。
  - 写操作原子替换：先写 .tmp 再 replace/copy2，防止写一半崩溃截断。
  - normalize_group / normalize_candidate 是防御式编程核心：任何外部输入
    （UI 提交、旧版本数据、preset）都必须消毒，确保字段完整、类型正确。
  - 损坏 JSON 恢复：重命名为 .corrupted.<uuid> 后返回空 store，
    既保证服务不崩溃，又保留现场供人工排查。

边界条件：
  - 文件不存在：返回空 store（带默认 schema），首次使用无报错。
  - candidate id 为空时自动生成 uuid，保证绝不出现空串 ID。
  - group id 为空时自动生成 uuid，避免 _find_group 匹配到意外结果。
  - build_from_providers 始终操作 "default" group：不覆盖用户手动创建的
    其他 group，实现「自动同步」与「手工配置」共存。

Windows 平台特殊性：
  - store_path 使用 Path.expanduser() 解析 ~，在 Windows 上对应
    C:\\Users\\<username>。
  - _save_store 针对 Windows 文件锁定做 3 次重试 + shutil.copy2 回退。
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app_paths import app_data_path
from model_rotation import AdaptiveModelRotation
from capabilities import merge_provider_model_capabilities
from provider_routing import provider_allows_local_routing
from providers import ProviderRegistry, normalize_capabilities, redact_secrets

# Schema version：当 group/candidate 数据结构发生不兼容变更时递增。
SCHEMA_VERSION = 1
DEFAULT_GROUP_ID = "default"
DEFAULT_GROUP_DISPLAY_NAME = "默认智能路由"
# 默认存储路径：Windows 下为 Documents/Codex Enhance Manager/amr/groups.json。
# 首次使用新路径时会从旧版 ~/.codex_enhance_manager/amr_groups.json 迁移。
LEGACY_STORE_PATH = Path.home() / ".codex_enhance_manager" / "amr_groups.json"
DEFAULT_STORE_PATH = app_data_path("amr", "groups.json")


def _empty_store() -> Dict[str, Any]:
    """返回空 store 模板，用于首次启动或 JSON 损坏后的恢复。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "groups": [],
        "updated_at": "",
    }


def _looks_question_corrupted(value: Any) -> bool:
    text = str(value or "").strip()
    if "??" not in text:
        return False
    return not any(ord(ch) > 127 for ch in text)


def _group_display_name_fallback(group_id: str) -> str:
    if group_id == DEFAULT_GROUP_ID:
        return DEFAULT_GROUP_DISPLAY_NAME
    return group_id


def normalize_candidate(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将任意 candidate 输入消毒为标准 schema。

    设计意图：
      - candidate 是 AMR 路由的最小单元，字段缺失会导致路由逻辑异常
        （如 capabilities 为 None 时 _candidate_has_capabilities 直接返回 False）。
      - 防御式默认值：enabled=True（避免误禁用）、priority=100（最低优先级）、
        context_window=0（不限制，但会被保守策略过滤）。
      - ID 回退策略：优先使用用户输入的 id；缺失时回退到 provider_id/model_id
        组合，保证 build_from_providers 生成的 candidate 具有可读 ID。

    Args:
        data: 原始 candidate 字典，可能来自 UI、旧版本 store 或动态构建。

    Returns:
        完全符合 schema 的标准 candidate 字典。
    """
    raw = copy.deepcopy(data or {})
    candidate_id = str(raw.get("id") or "").strip()
    provider_id = str(raw.get("provider_id") or "").strip()
    model_id = str(raw.get("model_id") or "").strip()
    if not candidate_id:
        # ID 回退：provider_id/model_id 组合是最自然的可读标识
        if provider_id and model_id:
            candidate_id = f"{provider_id}/{model_id}"
        elif provider_id:
            candidate_id = provider_id
        elif model_id:
            candidate_id = model_id
        else:
            # 所有标识均为空时自动生成，避免 AMR 内部 cooldown 字典使用空键
            candidate_id = f"candidate-{uuid.uuid4().hex[:8]}"
    return {
        "id": candidate_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "priority": int(raw.get("priority") if raw.get("priority") is not None else 100),
        "enabled": bool(raw.get("enabled", True)),
        "context_window": int(raw.get("context_window") or 0),
        "capabilities": normalize_capabilities(raw.get("capabilities")),
        "health": normalize_candidate_health(raw.get("health") or raw.get("status")),
    }


def normalize_candidate_health(data: Any) -> Dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    explicit = raw.get("healthy")
    if explicit is None and "success" in raw:
        explicit = raw.get("success")
    if explicit is None and "reachable" in raw:
        explicit = raw.get("reachable")
    last_success = raw.get("last_success")
    if last_success is None and "success" in raw:
        last_success = raw.get("success")
    return {
        "enabled": bool(raw.get("enabled", True)),
        "healthy": None if explicit is None else bool(explicit),
        "last_success": None if last_success is None else bool(last_success),
        "last_tested": str(raw.get("last_tested") or raw.get("tested_at") or ""),
        "last_error": str(raw.get("last_error") or raw.get("error") or ""),
    }


def normalize_group(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将任意 group 输入消毒为标准 schema。

    设计意图：
      - group 是 candidates 的容器，display_name 缺失时回退到 id，
        保证 UI 永远有可展示的文本。
      - candidates 列表经过逐条 normalize_candidate，防止旧数据或
        用户手抖输入的非法字段破坏 schema。
      - 空 candidates 列表是合法状态：允许用户先创建 group 再添加 candidate。
      - ID 回退策略：优先使用用户输入的 id；缺失时回退到 display_name 的
        sanitize 结果，保持可读性（如 "Test Group" -> "test-group"）。

    Args:
        data: 原始 group 字典，可能来自任何来源。

    Returns:
        完全符合 schema 的标准 group 字典。
    """
    raw = copy.deepcopy(data or {})
    group_id = str(raw.get("id") or "").strip()
    display_name = str(raw.get("display_name") or raw.get("name") or "").strip()
    if not group_id:
        # ID 回退：基于 display_name 生成可读 ID；display_name 也为空时才用随机值
        if display_name:
            group_id = re.sub(r"[^a-z0-9_-]+", "-", display_name.lower()).strip("-_")
        if not group_id:
            group_id = f"group-{uuid.uuid4().hex[:8]}"
    display_name_fallback = _group_display_name_fallback(group_id)
    if _looks_question_corrupted(display_name):
        display_name = display_name_fallback
    if not display_name:
        display_name = display_name_fallback
    candidates = raw.get("candidates", [])
    if not isinstance(candidates, list):
        # 防御旧数据 corruption：candidates 不是列表时重置为空
        candidates = []
    normalized_candidates = [normalize_candidate(c) for c in candidates if isinstance(c, dict)]
    return {
        "id": group_id,
        "display_name": display_name,
        "candidates": normalized_candidates,
        "created_at": raw.get("created_at") or "",
        "updated_at": raw.get("updated_at") or "",
    }


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AMRRegistry:
    """
    JSON-backed AMR rotation group registry.
    AMR 旋转组持久化注册表：所有 CRUD、动态构建、路由的入口类。

    线程安全说明：
      - 当前实现未使用显式线程锁，与 ProviderRegistry 保持一致。
      - 若未来需要多线程并发写，应在 _save_store 和 _load_store 上加锁。
      - _save_store 的原子写（tmp + replace）已能防止自身写一半崩溃。
    """

    def __init__(self, store_path: str = ""):
        """
        初始化 AMRRegistry。

        Args:
            store_path: 自定义存储路径。空字符串则使用 DEFAULT_STORE_PATH。
                        Windows 下支持 ~ 展开为当前用户主目录。
        """
        # expanduser 在 Windows 上将 ~ 解析为 %USERPROFILE%，确保跨平台一致
        self.store_path = Path(store_path).expanduser() if store_path else DEFAULT_STORE_PATH
        if not store_path:
            self._migrate_legacy_store()

    # ─────────────── 公开 CRUD API ───────────────

    def list_groups(self) -> Dict[str, Any]:
        """
        列出所有 rotation group。

        Returns:
            包含 schema_version、store_path、groups 列表、updated_at 的字典。
        """
        store = self._load_store()
        return {
            "schema_version": store.get("schema_version", SCHEMA_VERSION),
            "store_path": str(self.store_path),
            "groups": copy.deepcopy(store.get("groups", [])),
            "updated_at": store.get("updated_at", ""),
        }

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        读取单个 group。

        Args:
            group_id: group 的唯一标识。

        Returns:
            Group 字典，或 None（未找到时）。
            使用 deepcopy 防止调用方修改内部状态。
        """
        store = self._load_store()
        group = self._find_group(store, group_id)
        if not group:
            return None
        return copy.deepcopy(group)

    def create_group(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新 group。

        设计意图：
          - 所有输入必须经过 normalize_group 消毒，防止旧版本数据或
            用户手抖输入的非法字段破坏 schema。
          - ID 自动生成并去重；display_name 缺失时回退到 ID。

        Args:
            data: 原始 group 字典（来自 UI 或外部导入）。

        Returns:
            创建后的 group。
        """
        store = self._load_store()
        group = normalize_group(data)
        # ID 分配：优先使用 sanitize 处理用户输入，若冲突则自动加后缀 -2, -3...
        group["id"] = self._allocate_group_id(store, group)
        group["created_at"] = now_iso()
        group["updated_at"] = group["created_at"]
        store.setdefault("groups", []).append(group)
        self._save_store(store)
        return copy.deepcopy(group)

    def update_group(self, group_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        更新已有 group。

        工程权衡：
          - 使用 merge 而非直接覆盖：保留 created_at 不变，只更新提交的字段。
          - candidates 列表若被提交，会逐条重新 normalize，保证 schema 完整。
          - id 不可变：从 update 字典中移除，防止前端误传篡改 ID。

        Args:
            group_id: 目标 group ID。
            data: 更新字段字典。

        Returns:
            更新后的 group，或 None（未找到时）。
        """
        store = self._load_store()
        groups = store.setdefault("groups", [])
        for idx, existing in enumerate(groups):
            if existing.get("id") != group_id:
                continue
            merged = copy.deepcopy(existing)
            update_copy = copy.deepcopy(data or {})
            # ID 是不可变标识，不允许通过 update 修改
            update_copy.pop("id", None)
            for key, value in update_copy.items():
                if key == "candidates" and isinstance(value, list):
                    # candidates 需要逐条消毒，防止传入脏数据
                    merged[key] = [normalize_candidate(c) for c in value if isinstance(c, dict)]
                else:
                    merged[key] = value
            # 最终再过 normalize：确保合并后的数据仍然符合最新 schema
            merged = normalize_group(merged)
            merged["id"] = group_id
            merged["created_at"] = existing.get("created_at") or now_iso()
            merged["updated_at"] = now_iso()
            groups[idx] = merged
            self._save_store(store)
            return copy.deepcopy(merged)
        return None

    def delete_group(self, group_id: str) -> bool:
        """
        删除 group。

        边界条件：
          - 若 group 不存在，返回 False 且**不**触发无意义写盘。

        Args:
            group_id: 目标 group ID。

        Returns:
            是否实际发生了删除。
        """
        store = self._load_store()
        before = len(store.get("groups", []))
        store["groups"] = [g for g in store.get("groups", []) if g.get("id") != group_id]
        changed = len(store["groups"]) != before
        if changed:
            self._save_store(store)
        return changed

    # ─────────────── 动态构建与路由 ───────────────

    def build_from_providers(
        self,
        provider_registry: ProviderRegistry,
        extra_providers: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        从 ProviderRegistry 动态构建候选列表。

        设计意图：
          - 将每个 enabled provider 的每个 enabled model 转换为一个 candidate，
            实现「一键同步」：用户新增 provider 后，无需手动维护 AMR group。
          - always_visible 的 provider 的 candidate priority=1（常驻，优先级最高），
            其余 priority=2（普通候选）。
          - candidate capabilities 采用「model 级优先、provider 级回退」的继承策略，
            与 Unified Model Catalog 保持一致。
          - 同步结果写入名为 "default" 的 group：不覆盖用户手动创建的其他 group。

        Args:
            provider_registry: ProviderRegistry 实例。

        Returns:
            创建或更新后的 "default" group。
        """
        providers_data = provider_registry.list_providers(
            include_secrets=False,
            extra_providers=extra_providers,
        )
        candidates: List[Dict[str, Any]] = []
        for p in providers_data.get("providers", []):
            if not provider_allows_local_routing(p):
                continue
            for m in p.get("models", []):
                if not m.get("enabled", True):
                    continue
                candidates.append({
                    "id": f"{p['id']}/{m['id']}",
                    "provider_id": p["id"],
                    "model_id": m["id"],
                    "priority": 1 if p.get("catalog_visibility") == "always_visible" else 2,
                    "enabled": True,
                    "context_window": m.get("context_window", 0),
                    "capabilities": merge_provider_model_capabilities(p, m),
                    "health": normalize_candidate_health(p.get("status")),
                })

        store = self._load_store()
        groups = store.setdefault("groups", [])
        default_group = next((g for g in groups if g.get("id") == "default"), None)
        if default_group:
            default_group["candidates"] = candidates
            default_group["updated_at"] = now_iso()
            default_group = normalize_group(default_group)
            for idx, g in enumerate(groups):
                if g.get("id") == "default":
                    groups[idx] = default_group
                    break
        else:
            default_group = normalize_group({
                "id": DEFAULT_GROUP_ID,
                "display_name": DEFAULT_GROUP_DISPLAY_NAME,
                "candidates": candidates,
            })
            default_group["created_at"] = now_iso()
            default_group["updated_at"] = default_group["created_at"]
            groups.append(default_group)
        self._save_store(store)
        return copy.deepcopy(default_group)

    def add_candidates_to_group(
        self,
        group_id: str,
        candidates: List[Dict[str, Any]],
        display_name: str = "",
    ) -> Dict[str, Any]:
        """Upsert candidates into a local AMR group, creating the group if needed."""
        target_group_id = str(group_id or "default").strip() or "default"
        normalized_new = [normalize_candidate(c) for c in candidates if isinstance(c, dict)]
        store = self._load_store()
        groups = store.setdefault("groups", [])
        group = self._find_group(store, target_group_id)
        if not group:
            group = normalize_group({
                "id": target_group_id,
                "display_name": display_name or _group_display_name_fallback(target_group_id),
                "candidates": [],
            })
            group["created_at"] = now_iso()
            groups.append(group)

        existing_by_id = {}
        for candidate in group.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            normalized = normalize_candidate(candidate)
            existing_by_id[normalized["id"]] = normalized
        for candidate in normalized_new:
            existing_by_id[candidate["id"]] = candidate

        group["candidates"] = list(existing_by_id.values())
        group["updated_at"] = now_iso()
        normalized_group = normalize_group(group)
        normalized_group["id"] = target_group_id
        for idx, item in enumerate(groups):
            if item.get("id") == target_group_id:
                groups[idx] = normalized_group
                break
        self._save_store(store)
        result = copy.deepcopy(normalized_group)
        result["upserted_count"] = len(normalized_new)
        return result

    def to_rotation_engine(self) -> AdaptiveModelRotation:
        """
        将当前持久化 group 转换为 AdaptiveModelRotation 引擎实例。

        工程权衡：
          - 每次调用都重新构造 engine：group 数据量极小，构造开销可忽略；
            这样保证 engine 始终与最新持久化状态一致，无需维护内存同步。

        Returns:
            AdaptiveModelRotation 实例。
        """
        store = self._load_store()
        groups = copy.deepcopy(store.get("groups", []))
        return AdaptiveModelRotation(groups)

    def route(
        self,
        group_id: str,
        request_capabilities: Optional[Set[str]] = None,
        required_context: int = 0,
    ) -> Dict[str, Any]:
        """
        使用内部 engine 执行路由。

        Args:
            group_id: 目标旋转组 ID。
            request_capabilities: 请求所需能力集合。默认为 {"text"}。
            required_context: 请求所需上下文长度（token 数）。

        Returns:
            路由决策字典，结构与 AdaptiveModelRotation.route 一致。
        """
        engine = self.to_rotation_engine()
        return engine.route(
            group_id=group_id,
            required_capabilities=request_capabilities,
            required_context=required_context,
        )

    def export_bundle(self) -> Dict[str, Any]:
        """
        导出所有 group 的脱敏版本。

        设计意图：
          - 虽然 group 结构本身不存储 api_key，但 capabilities 或其他扩展字段
            未来可能引入敏感信息；使用 redact_secrets 做兜底脱敏，保证安全。
          - 供诊断和备份使用。

        Returns:
            包含 schema_version、exported_at、groups 的字典。
        """
        store = self._load_store()
        groups = redact_secrets(copy.deepcopy(store.get("groups", [])))
        return {
            "schema_version": store.get("schema_version", SCHEMA_VERSION),
            "exported_at": now_iso(),
            "groups": groups,
        }

    # ─────────────── 内部方法 ───────────────

    def _migrate_legacy_store(self) -> None:
        if self.store_path.exists() or not LEGACY_STORE_PATH.exists():
            return
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(LEGACY_STORE_PATH), str(self.store_path))
        except Exception:
            pass

    def _load_store(self) -> Dict[str, Any]:
        """
        加载本地 AMR store。

        工程权衡与边界条件：
          - 文件不存在：返回空 store（带有默认 schema），首次启动无报错。
          - JSON 损坏：保留旧文件（重命名为 .corrupted.<随机后缀>），供人工恢复；
            同时返回空 store 保证程序不崩溃。
          - 类型防护：若 groups 列表中混入非字典元素，通过 isinstance 过滤丢弃。
          - 所有 group 经过 normalize_group：保证升级 schema 后旧数据也能
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
        store["groups"] = [normalize_group(g) for g in store.get("groups", []) if isinstance(g, dict)]
        return store

    def _save_store(self, store: Dict[str, Any]):
        """
        原子写入 AMR store。

        设计意图：
          - 使用「写临时文件 + 原子替换」模式，防止写一半进程崩溃导致 JSON 截断。
          - Windows 上 os.replace / Path.replace 可能在目标文件被其他进程锁定时
            抛出 PermissionError。

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

    @staticmethod
    def _find_group(store: Dict[str, Any], group_id: str) -> Optional[Dict[str, Any]]:
        """在 store 中按 ID 查找 group。返回深拷贝前的原始引用（内部使用）。"""
        return next((g for g in store.get("groups", []) if g.get("id") == group_id), None)

    def _allocate_group_id(self, store: Dict[str, Any], group: Dict[str, Any]) -> str:
        """
        为新建 group 分配唯一 ID。

        设计意图：
          - 优先使用用户输入或 display_name 的 sanitize 结果，保持可读性。
          - 若冲突则追加数字后缀（-2, -3...），避免用户手动重试。

        边界条件：
          - 若所有候选字段均为空，回退到 group-<随机8位hex>，保证绝不返回空串。

        Args:
            store: 当前 store（用于检查已有 ID）。
            group: 待分配 ID 的 group 数据。

        Returns:
            全局唯一的 group ID 字符串。
        """
        requested = str(group.get("id") or group.get("display_name") or "").strip().lower()
        requested = re.sub(r"[^a-z0-9_-]+", "-", requested).strip("-_")
        if not requested:
            requested = f"group-{uuid.uuid4().hex[:8]}"
        existing = {g.get("id") for g in store.get("groups", [])}
        candidate = requested
        index = 2
        while candidate in existing:
            candidate = f"{requested}-{index}"
            index += 1
        return candidate
