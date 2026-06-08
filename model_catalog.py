"""
model_catalog.py - Unified Model Catalog (UMC) generator.
统一模型目录生成器。

设计意图：
  - UMC 是 Codex CLI 看到的「可用模型列表」的来源。本模块根据 provider registry
    的状态生成最终目录，决定哪些模型对用户可见。
  - 支持四种 visibility 策略：
    - hidden：完全隐藏（如停用或测试中的 provider）。
    - focused_only：仅当设为 focus provider 时显示（默认策略，避免列表过长）。
    - always_visible：常驻显示（如主力 OpenAI provider）。
    - selected_models：仅显示被用户明确勾选的模型（适合国内厂商，模型众多但
      用户只关心其中几个）。
  - 与 ProviderRegistry.preview_catalog 的区别：
    - ProviderRegistry 侧重于「当前 registry 状态的预览」。
    - UnifiedModelCatalog 侧重于「生成可注入 Codex 的标准化数据结构」。

工程权衡：
  - 纯函数设计：build_catalog 无副作用，同一输入始终产生同一输出，便于测试和缓存。
  - 去重机制：provider_id + model_id 组合为唯一键，防止同一模型因 visibility
    规则重叠而被重复加入。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set

from capabilities import merge_provider_model_capabilities
from provider_routing import provider_allows_local_routing


class UnifiedModelCatalog:
    """
    从 provider registry 状态生成统一模型目录。

    设计意图：
      - 封装 Catalog 构建逻辑，与 providers.py 的 registry 存储解耦。
      - 支持 focus provider 概念：当用户「聚焦」某个 provider 时，其所有启用模型
        强制可见，不受常规 visibility 限制。
    """

    def __init__(self, providers: List[Dict[str, Any]], focus_provider_id: str = ""):
        """
        初始化 UMC 生成器。

        Args:
            providers: provider 列表（通常来自 ProviderRegistry.list_providers）。
            focus_provider_id: 当前焦点 provider ID，其模型强制可见。
        """
        self.providers = providers
        self.focus_provider_id = focus_provider_id

    def build_catalog(self) -> Dict[str, Any]:
        """
        构建完整 Catalog 并附带解释信息。

        设计意图：
          - route_explanation 记录每个条目被纳入的原因（focus/always_visible/
            selected），帮助用户理解 Catalog 的构建逻辑，排查「为什么某模型
            没有出现」的问题。
          - seen 集合去重：同一 provider 下的同一模型因规则重叠不应重复出现。

        Returns:
            {"focus_provider_id": str, "entries": [...], "entry_count": int,
             "route_explanation": [...]}
        """
        entries: List[Dict[str, Any]] = []
        explanations: List[str] = []
        seen: Set[str] = set()
        provider_aliases: Dict[str, str] = {}

        has_focus = bool(self.focus_provider_id)
        for provider in self.providers:
            if not provider_allows_local_routing(provider):
                continue
            visibility = provider.get("catalog_visibility", "focused_only")
            if visibility not in ("hidden", "focused_only", "always_visible", "selected_models"):
                continue
            is_focused = bool(self.focus_provider_id and provider.get("id") == self.focus_provider_id)

            if has_focus:
                if is_focused:
                    models_to_include = [
                        model for model in provider.get("models", [])
                        if _model_visible_for_catalog(model)
                    ]
                else:
                    if visibility == "hidden":
                        continue
                    primary_model = _provider_catalog_primary_model(provider)
                    models_to_include = [primary_model] if primary_model else []
            else:
                if visibility == "hidden":
                    continue
                selected_only = visibility == "selected_models"
                include_all = visibility == "always_visible"
                if not include_all and visibility not in {"selected_models", "focused_only"}:
                    continue
                if visibility == "focused_only":
                    continue
                models_to_include = []
                for model in provider.get("models", []):
                    if not _model_visible_for_catalog(model):
                        continue
                    if selected_only and not model.get("selected", False):
                        continue
                    models_to_include.append(model)

            for model in models_to_include:
                if not isinstance(model, dict):
                    continue

                key = f"{provider.get('id')}::{model.get('id')}"
                if key in seen:
                    continue
                seen.add(key)

                entry = self._make_entry(provider, model, is_focused, provider_aliases)
                entries.append(entry)

                if is_focused:
                    reason = "focus provider"
                elif visibility == "always_visible":
                    reason = "always visible provider"
                elif model.get("primary", False):
                    reason = "provider primary model"
                elif has_focus:
                    reason = "derived provider primary model"
                elif visibility == "selected_models":
                    reason = "selected model"
                else:
                    reason = visibility
                explanations.append(f"{entry['codex_model_id']} included by {reason}.")

        explanations.extend(resolve_catalog_id_collisions(entries))

        return {
            "focus_provider_id": self.focus_provider_id,
            "entries": entries,
            "entry_count": len(entries),
            "route_explanation": explanations,
        }

    def build_injection_data(self) -> List[Dict[str, Any]]:
        """
        构建供 Codex 注入的最小数据结构。

        设计意图：
          - 比 build_catalog 更精简，只保留 id、name、provider，
            用于实际写入 Codex model_catalog.json。

        Returns:
            [{"id": str, "name": str, "provider": str}, ...]
        """
        catalog = self.build_catalog()
        return [
            {
                "id": entry["codex_model_id"],
                "name": entry["display_name"],
                "provider": entry["provider_id"],
            }
            for entry in catalog["entries"]
        ]

    def find_entry(self, codex_model_id: str) -> Optional[Dict[str, Any]]:
        """
        按 codex_model_id 查找 Catalog 条目。

        Args:
            codex_model_id: 如 "openai/gpt-5"。

        Returns:
            匹配的 entry 字典，或 None。
        """
        catalog = self.build_catalog()
        for entry in catalog["entries"]:
            if entry["codex_model_id"] == codex_model_id:
                return entry
        return None

    @staticmethod
    def _make_entry(
        provider: Dict[str, Any],
        model: Dict[str, Any],
        is_focused: bool = False,
        provider_aliases: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        alias = provider.get("short_alias") or provider.get("id")
        visible_provider_alias = _unique_provider_visible_alias(provider, provider_aliases)
        upstream_model_id = model.get("id") or "default"
        visible_model_id = _model_visible_id(model, upstream_model_id)
        pricing: Dict[str, Any] = {}
        if isinstance(provider.get("pricing"), dict):
            pricing.update(copy.deepcopy(provider["pricing"]))
        if isinstance(model.get("pricing"), dict):
            pricing.update(copy.deepcopy(model["pricing"]))
        return {
            "codex_model_id": f"{visible_provider_alias}/{visible_model_id}",
            "display_name": model.get("display_name") or upstream_model_id,
            "provider_id": provider.get("id"),
            "provider_alias": alias,
            "provider_visible_alias": visible_provider_alias,
            "provider_display_name": provider.get("display_name"),
            "upstream_model_id": upstream_model_id,
            "visible_model_id": visible_model_id,
            "codex_visible_id": model.get("codex_visible_id", ""),
            "api_format": provider.get("api_format"),
            "responses_profile": provider.get("responses_profile", {}),
            "context_window": model.get("context_window", 0),
            "max_output_tokens": model.get("max_output_tokens", 0),
            "capabilities": merge_provider_model_capabilities(provider, model),
            "reasoning_effort_profile": model.get("reasoning_effort_profile", {}),
            "native_currency": model.get("native_currency") or provider.get("native_currency") or pricing.get("native_currency"),
            "pricing": pricing,
            "has_model_pricing": bool(isinstance(model.get("pricing"), dict) and model.get("pricing")),
            "catalog_visibility": provider.get("catalog_visibility"),
            "focused": is_focused,
            "primary": bool(model.get("primary", False)),
            "catalog_hidden": bool(model.get("catalog_hidden", False)),
        }


def resolve_catalog_id_collisions(entries: List[Dict[str, Any]]) -> List[str]:
    """Ensure Codex-visible model ids are unique while preserving routeability."""
    counts: Dict[str, int] = {}
    for entry in entries:
        codex_id = str(entry.get("codex_model_id") or "")
        if codex_id:
            counts[codex_id] = counts.get(codex_id, 0) + 1

    collided = {codex_id for codex_id, count in counts.items() if count > 1}
    if not collided:
        for entry in entries:
            entry.setdefault("catalog_collision", False)
            entry.setdefault("original_codex_model_id", "")
        return []

    used_ids: Set[str] = {codex_id for codex_id, count in counts.items() if count == 1}
    explanations: List[str] = []
    for entry in entries:
        original = str(entry.get("codex_model_id") or "")
        if original not in collided:
            entry.setdefault("catalog_collision", False)
            entry.setdefault("original_codex_model_id", "")
            continue
        resolved = _collision_safe_codex_model_id(entry, used_ids, collided)
        used_ids.add(resolved)
        entry["catalog_collision"] = True
        entry["original_codex_model_id"] = original
        entry["codex_model_id"] = resolved
        explanations.append(f"Catalog ID collision for '{original}' resolved to '{resolved}'.")
    return explanations


def _model_visible_for_catalog(model: Dict[str, Any]) -> bool:
    if not isinstance(model, dict):
        return False
    if not model.get("enabled", True):
        return False
    if model.get("catalog_hidden", False):
        return False
    return True


def _model_visible_id(model: Dict[str, Any], fallback: str) -> str:
    visible = (
        model.get("codex_visible_id")
        or model.get("visible_id")
        or model.get("display_id")
        or model.get("display_name")
    )
    return _catalog_segment(visible, fallback)


def _catalog_segment(value: Any, fallback: str = "") -> str:
    text = str(value or fallback or "").strip().replace("/", "／")
    return text or str(fallback or "default").strip() or "default"


def _unique_provider_visible_alias(provider: Dict[str, Any], seen: Optional[Dict[str, str]]) -> str:
    base = _catalog_segment(
        provider.get("codex_visible_alias")
        or provider.get("provider_visible_alias")
        or provider.get("visible_alias")
        or provider.get("display_name")
        or provider.get("short_alias")
        or provider.get("id"),
        str(provider.get("short_alias") or provider.get("id") or "provider"),
    )
    if seen is None:
        return base
    provider_id = str(provider.get("id") or "")
    existing = seen.get(base.lower())
    if not existing or existing == provider_id:
        seen[base.lower()] = provider_id
        return base
    suffix = _catalog_id_segment(provider_id or provider.get("short_alias") or "provider")
    candidate = f"{base} ({suffix})"
    counter = 2
    while candidate.lower() in seen and seen[candidate.lower()] != provider_id:
        candidate = f"{base} ({suffix}-{counter})"
        counter += 1
    seen[candidate.lower()] = provider_id
    return candidate


def _provider_catalog_primary_model(provider: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    models = [model for model in provider.get("models", []) if _model_visible_for_catalog(model)]
    for model in models:
        if model.get("primary", False):
            return model
    for model in models:
        if model.get("selected", False):
            return model
    return models[0] if models else None


def _collision_safe_codex_model_id(
    entry: Dict[str, Any],
    used_ids: Set[str],
    collided_ids: Set[str],
) -> str:
    provider_segment = _catalog_id_segment(entry.get("provider_id") or entry.get("provider_alias") or "provider")
    upstream_model_id = str(entry.get("upstream_model_id") or "default").strip() or "default"
    base = f"{provider_segment}/{upstream_model_id}"
    candidate = base
    suffix = 2
    while candidate in used_ids or candidate in collided_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _catalog_id_segment(value: Any) -> str:
    text = str(value or "provider").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-")
    return text or "provider"
