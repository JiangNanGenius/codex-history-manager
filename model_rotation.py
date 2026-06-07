"""
model_rotation.py - Adaptive Model Rotation (AMR) engine.
自适应模型路由引擎。

设计意图：
  - AMR 解决「多个 Provider / 多个模型如何选」的问题：根据请求所需能力
    （如 vision、tools）、上下文长度、优先级、健康状态，动态选择最佳候选。
  - 与负载均衡不同：AMR 是「能力感知路由」，而非简单的轮询或随机。
    例如 vision 请求必须路由到支持 vision 的模型，即使它的优先级较低。
  - Cooldown 机制：上游实际故障后，候选进入冷却期（默认 60 秒），
    期间不再被选中，避免反复请求已知故障节点。
  - 组内 advertised context window = 所有启用候选的最小上下文窗口：
    这是保守策略，确保任何发往该组的请求都不会因某个候选窗口不足而失败。

工程权衡：
  - 纯内存状态：cooldowns 存在内存字典中，进程重启后失效。这是有意设计：
    持久化 cooldown 意义不大（重启后上游可能已恢复），且增加了复杂度。
  - threading.Lock 保护 cooldowns：route 和 report_failure 可能并发调用，
    加锁防止竞态条件（如 report_failure 刚写 cooldown，route 同时读取）。
  - 无真实网络调用：本模块只产生路由决策和 explanation，真实 HTTP 调用
    由 proxy_server 执行，职责分离便于测试。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Set


class AdaptiveModelRotation:
    """AMR engine: given a rotation group and request characteristics, pick the best candidate."""

    def __init__(self, groups: List[Dict[str, Any]]):
        """
        初始化 AMR 引擎。

        Args:
            groups: 旋转组列表，每个组包含 candidates（候选模型列表）。
        """
        self.groups = groups
        self._cooldowns: Dict[str, float] = {}
        self._lock = threading.Lock()

    def route(
        self,
        group_id: str,
        required_capabilities: Optional[Set[str]] = None,
        required_context: int = 0,
    ) -> Dict[str, Any]:
        """
        将请求路由到旋转组内的最佳候选模型。

        路由逻辑（按优先级过滤）：
          1. 按 priority 排序（数值越小优先级越高）。
          2. 能力过滤：剔除不支持 required_capabilities 的候选。
             若无候选通过，返回失败（并附详细的能力缺失报告）。
          3. 上下文过滤：剔除 context_window < required_context 的候选。
             若无候选通过，返回当前最大可用窗口信息。
          4. Cooldown 过滤：剔除仍处于冷却期的候选。
             若全部 capable 候选都在冷却中，选择冷却时间最短者（带 priority 打破平局）。
          5. 选择 available 列表中优先级最高者作为 winner。

        边界条件：
          - required_capabilities 默认为 {"text"}：保证无特殊需求时至少选文本模型。
          - 空组或零候选时提前返回错误，避免后续空列表操作引发 IndexError。

        Args:
            group_id: 旋转组 ID。
            required_capabilities: 请求所需能力集合。
            required_context: 请求所需上下文长度（token 数）。

        Returns:
            路由决策字典，含 success、provider_id、model_id、explanation 等。
        """
        required_capabilities = required_capabilities or {"text"}
        with self._lock:
            group = self._find_group(group_id)
            if not group:
                return {
                    "success": False,
                    "error": f"Rotation group not found: {group_id}",
                    "explanation": ["Group lookup failed."],
                }

            candidates = [c for c in group.get("candidates", []) if c.get("enabled", True)]
            if not candidates:
                return {
                    "success": False,
                    "error": "No candidates configured in group",
                    "explanation": ["Group has zero enabled candidates."],
                }

            # Sort by priority, then by id for stable tie-breaking
            candidates = sorted(candidates, key=lambda c: (c.get("priority", 100), c.get("id", "")))

            # Filter by capability
            capable = [
                c for c in candidates
                if self._candidate_has_capabilities(c, required_capabilities)
            ]
            if not capable:
                missing = required_capabilities - self._all_group_capabilities(group)
                return {
                    "success": False,
                    "error": "No candidate supports required capabilities",
                    "explanation": [
                        f"Missing capabilities: {sorted(missing)}"
                    ],
                    "candidate_status": [
                        {
                            "candidate_id": c.get("id"),
                            "capabilities": c.get("capabilities", {}),
                            "available": self._candidate_has_capabilities(c, required_capabilities),
                        }
                        for c in candidates
                    ],
                }

            # Filter by context window
            context_capable = [
                c for c in capable
                if (c.get("context_window", 0) or 0) >= required_context
            ]
            if not context_capable:
                max_ctx = max((c.get("context_window", 0) or 0) for c in capable)
                return {
                    "success": False,
                    "error": "Context window too large for all capable candidates",
                    "explanation": [
                        f"Required context: {required_context}, max available: {max_ctx}"
                    ],
                }

            # Filter out cooldown candidates
            now = time.time()
            available = [
                c for c in context_capable
                if now >= self._cooldowns.get(c.get("id", ""), 0)
            ]
            if not available:
                # All capable candidates in cooldown; pick the one with shortest remaining cooldown,
                # tie-break by priority so the highest-priority candidate wins.
                available = sorted(
                    context_capable,
                    key=lambda c: (self._cooldowns.get(c.get("id", ""), 0), c.get("priority", 100)),
                )

            # Pick highest priority available candidate
            winner = available[0]
            explanation = [
                f"Group: {group.get('id')}",
                f"Required capabilities: {sorted(required_capabilities)}",
                f"Required context: {required_context}",
                f"Candidates evaluated: {len(candidates)}",
                f"Capable candidates: {len(capable)}",
                f"Winner: {winner.get('provider_id')}/{winner.get('model_id')} (priority {winner.get('priority')})",
            ]

            return {
                "success": True,
                "group_id": group.get("id"),
                "provider_id": winner.get("provider_id"),
                "model_id": winner.get("model_id"),
                "candidate_id": winner.get("id"),
                "priority": winner.get("priority"),
                "context_window": winner.get("context_window", 0),
                "explanation": explanation,
            }

    def report_failure(self, candidate_id: str, cooldown_seconds: int = 60) -> None:
        """
        报告候选模型故障，将其加入冷却期。

        设计意图：
          - 仅在实际上游失败时调用（如 HTTP 5xx、连接超时），能力不匹配
            （如 vision 请求发给文本模型）不应触发冷却。
          - 冷却期默认 60 秒：足够让短暂故障恢复，又不至于长期禁用健康节点。

        Args:
            candidate_id: 故障候选的 ID。
            cooldown_seconds: 冷却时长（秒）。
        """
        with self._lock:
            self._cooldowns[candidate_id] = time.time() + cooldown_seconds

    def get_group_context_window(self, group_id: str) -> int:
        """
        返回旋转组的 advertised context window。

        设计意图：
          - 采用「最小值」策略（保守策略）：组内所有启用候选的最小上下文窗口。
            这保证了发往该组的任何请求都不会因某个候选窗口不足而失败。
          - 若组内模型窗口差异大（如 128K vs 8K），限制候选为 8K 的模型
            会拉低整组能力；用户应通过分组策略隔离不同窗口级别的模型。

        Args:
            group_id: 旋转组 ID。

        Returns:
            组内启用候选的最小 context_window，或 0（组不存在/无候选时）。
        """
        group = self._find_group(group_id)
        if not group:
            return 0
        candidates = group.get("candidates", [])
        if not candidates:
            return 0
        enabled = [c for c in candidates if c.get("enabled", True)]
        if not enabled:
            return 0
        return min(c.get("context_window", 0) or 0 for c in enabled)

    def list_groups(self) -> List[Dict[str, Any]]:
        """
        返回所有旋转组及其有效上下文窗口信息。

        Returns:
            [{"id": str, "display_name": str, "effective_context_window": int,
              "limiting_candidate_id": str, "candidate_count": int}, ...]
        """
        result = []
        for group in self.groups:
            eff_ctx = self.get_group_context_window(group.get("id", ""))
            limiting = None
            candidates = group.get("candidates", [])
            enabled = [c for c in candidates if c.get("enabled", True)]
            for c in enabled:
                if (c.get("context_window", 0) or 0) == eff_ctx:
                    limiting = c.get("id")
                    break
            result.append({
                "id": group.get("id"),
                "display_name": group.get("display_name"),
                "effective_context_window": eff_ctx,
                "limiting_candidate_id": limiting,
                "candidate_count": len(enabled),
            })
        return result

    def _find_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        for group in self.groups:
            if group.get("id") == group_id:
                return group
        return None

    @staticmethod
    def _candidate_has_capabilities(candidate: Dict[str, Any], required: Set[str]) -> bool:
        caps = candidate.get("capabilities", {})
        if not isinstance(caps, dict):
            return False
        for req in required:
            if not caps.get(req, False):
                return False
        return True

    @staticmethod
    def _all_group_capabilities(group: Dict[str, Any]) -> Set[str]:
        result: Set[str] = set()
        for c in group.get("candidates", []):
            if not c.get("enabled", True):
                continue
            caps = c.get("capabilities", {})
            if not isinstance(caps, dict):
                continue
            for key, val in caps.items():
                if val:
                    result.add(key)
        return result
