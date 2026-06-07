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
from typing import Any, Dict, List, Optional, Set


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

        for provider in self.providers:
            if not provider.get("enabled", True):
                continue
            visibility = provider.get("catalog_visibility", "focused_only")
            if visibility not in ("hidden", "focused_only", "always_visible", "selected_models"):
                continue
            is_focused = bool(self.focus_provider_id and provider.get("id") == self.focus_provider_id)

            if visibility == "hidden":
                continue

            # Determine which models to include
            selected_only = visibility == "selected_models" and not is_focused
            include_all = visibility == "always_visible" or is_focused

            if visibility == "focused_only" and not is_focused:
                continue

            for model in provider.get("models", []):
                if not model.get("enabled", True):
                    continue
                if selected_only and not model.get("selected", False):
                    continue

                key = f"{provider.get('id')}::{model.get('id')}"
                if key in seen:
                    continue
                seen.add(key)

                entry = self._make_entry(provider, model, is_focused)
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
    def _make_entry(provider: Dict[str, Any], model: Dict[str, Any], is_focused: bool = False) -> Dict[str, Any]:
        alias = provider.get("short_alias") or provider.get("id")
        upstream_model_id = model.get("id") or "default"
        return {
            "codex_model_id": f"{alias}/{upstream_model_id}",
            "display_name": model.get("display_name") or upstream_model_id,
            "provider_id": provider.get("id"),
            "provider_alias": alias,
            "provider_display_name": provider.get("display_name"),
            "upstream_model_id": upstream_model_id,
            "api_format": provider.get("api_format"),
            "responses_profile": provider.get("responses_profile", {}),
            "context_window": model.get("context_window", 0),
            "capabilities": model.get("capabilities") or provider.get("capabilities", {}),
            "native_currency": model.get("native_currency") or provider.get("native_currency"),
            "catalog_visibility": provider.get("catalog_visibility"),
            "focused": is_focused,
        }
