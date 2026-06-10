"""
tests/test_amr_registry.py - AMRRegistry 单元测试与集成测试。

设计意图：
  - 覆盖 AMRRegistry 的所有公开方法：CRUD、持久化、损坏恢复、
    动态构建、路由集成。
  - 使用 tmp_path 保证测试隔离：每个测试在自己的临时目录中操作，
    互不干扰。
  - build_from_providers 使用 unittest.mock.MagicMock 模拟 ProviderRegistry，
    避免真实 I/O 依赖。

边界条件：
  - 测试 corrupted JSON recovery 时，直接写入非法内容再实例化 registry，
    验证旧文件被重命名且程序不崩溃。
  - 测试 route 集成时，验证 winner 选择符合 priority 排序和 capability 过滤。
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from capabilities import normalize_capabilities
from amr_registry import (
    AMRRegistry,
    DEFAULT_GROUP_DISPLAY_NAME,
    DEFAULT_GROUP_ID,
    normalize_group,
    normalize_candidate,
    _empty_store,
)


def test_default_store_path_uses_app_data():
    import amr_registry

    assert amr_registry.DEFAULT_STORE_PATH.name == "groups.json"
    assert amr_registry.DEFAULT_STORE_PATH.parent.name == "amr"
    assert "Codex Enhance Manager" in str(amr_registry.DEFAULT_STORE_PATH)


def test_default_group_question_corruption_is_repaired():
    group = normalize_group({"id": DEFAULT_GROUP_ID, "display_name": "??????"})

    assert group["display_name"] == DEFAULT_GROUP_DISPLAY_NAME


def test_default_store_migrates_legacy_path(tmp_path, monkeypatch):
    import amr_registry

    legacy_path = tmp_path / "legacy" / "amr_groups.json"
    default_path = tmp_path / "app-data" / "amr" / "groups.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps({
        "schema_version": 1,
        "groups": [{"id": "default", "display_name": "Default", "candidates": []}],
        "updated_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(amr_registry, "LEGACY_STORE_PATH", legacy_path)
    monkeypatch.setattr(amr_registry, "DEFAULT_STORE_PATH", default_path)

    reg = AMRRegistry()

    assert default_path.exists()
    assert legacy_path.exists()
    assert reg.list_groups()["groups"][0]["id"] == "default"


# ─────────────── Normalize ───────────────

class TestNormalize:
    def test_normalize_candidate_defaults(self):
        """candidate 全缺省时，所有字段应获得安全默认值。"""
        c = normalize_candidate({})
        assert c["id"].startswith("candidate-")
        assert c["provider_id"] == ""
        assert c["model_id"] == ""
        assert c["priority"] == 100
        assert c["enabled"] is True
        assert c["context_window"] == 0
        assert isinstance(c["capabilities"], dict)
        assert c["capabilities"]["text"] is True

    def test_normalize_candidate_partial(self):
        """candidate 部分字段缺失时，只填充缺失字段。"""
        c = normalize_candidate({"id": "c1", "provider_id": "p1", "priority": 1})
        assert c["id"] == "c1"
        assert c["provider_id"] == "p1"
        assert c["priority"] == 1
        assert c["model_id"] == ""  # 缺失字段填充默认值

    def test_normalize_group_defaults(self):
        """group 全缺省时，自动生成 ID 并填充默认值。"""
        g = normalize_group({})
        assert g["id"].startswith("group-")
        assert g["display_name"] == g["id"]
        assert g["candidates"] == []
        assert g["created_at"] == ""
        assert g["updated_at"] == ""

    def test_normalize_group_with_candidates(self):
        """group 包含 candidates 时，逐条消毒。"""
        g = normalize_group({
            "id": "g1",
            "display_name": "Test",
            "candidates": [
                {"provider_id": "p1", "model_id": "m1"},
                "not_a_dict",  # 非法元素应被过滤
            ],
        })
        assert g["id"] == "g1"
        assert len(g["candidates"]) == 1
        # provider_id + model_id 存在时，id 回退为 "p1/m1" 而非随机值
        assert g["candidates"][0]["id"] == "p1/m1"


# ─────────────── CRUD ───────────────

class TestCRUD:
    def test_create_and_get(self, tmp_path):
        """创建 group 后应能正确读取。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        created = reg.create_group({
            "display_name": "Test Group",
            "candidates": [{"id": "c1", "provider_id": "p1", "model_id": "m1"}],
        })
        assert created["id"] == "test-group"
        assert created["display_name"] == "Test Group"
        assert len(created["candidates"]) == 1

        got = reg.get_group("test-group")
        assert got is not None
        assert got["display_name"] == "Test Group"
        assert got["candidates"][0]["provider_id"] == "p1"

    def test_get_nonexistent(self, tmp_path):
        """读取不存在的 group 应返回 None。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        assert reg.get_group("no-such-group") is None

    def test_create_duplicate_name_auto_id(self, tmp_path):
        """同名 group 第二次创建时，ID 应自动加后缀避免冲突。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        g1 = reg.create_group({"display_name": "Alpha"})
        g2 = reg.create_group({"display_name": "Alpha"})
        assert g1["id"] == "alpha"
        assert g2["id"] == "alpha-2"

    def test_update_and_get(self, tmp_path):
        """更新 group 后读取应返回最新数据。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({"id": "g1", "display_name": "G1"})
        updated = reg.update_group("g1", {"display_name": "G1 Updated"})
        assert updated is not None
        assert updated["display_name"] == "G1 Updated"
        assert updated["created_at"] != ""  # created_at 应保留
        assert updated["updated_at"] != ""  # updated_at 应刷新

        got = reg.get_group("g1")
        assert got["display_name"] == "G1 Updated"

    def test_update_candidates_renormalize(self, tmp_path):
        """更新 candidates 时，应逐条重新 normalize。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({"id": "g1", "display_name": "G1", "candidates": []})
        updated = reg.update_group("g1", {
            "candidates": [{"provider_id": "p2", "model_id": "m2"}],
        })
        assert len(updated["candidates"]) == 1
        # provider_id + model_id 存在时，id 回退为 "p2/m2" 而非随机值
        assert updated["candidates"][0]["id"] == "p2/m2"
        assert updated["candidates"][0]["enabled"] is True

    def test_update_nonexistent(self, tmp_path):
        """更新不存在的 group 应返回 None。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        assert reg.update_group("missing", {"display_name": "X"}) is None

    def test_delete(self, tmp_path):
        """删除 group 后应无法再读取。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({"id": "g1", "display_name": "G1"})
        assert reg.delete_group("g1") is True
        assert reg.get_group("g1") is None

    def test_delete_nonexistent(self, tmp_path):
        """删除不存在的 group 应返回 False 且不写盘。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        assert reg.delete_group("missing") is False

    def test_list_groups(self, tmp_path):
        """list_groups 应返回所有 group。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({"display_name": "A"})
        reg.create_group({"display_name": "B"})
        result = reg.list_groups()
        assert len(result["groups"]) == 2
        assert result["schema_version"] == 1


# ─────────────── Persistence ───────────────

class TestPersistence:
    def test_write_and_reload(self, tmp_path):
        """写入后重新加载，数据应完全一致。"""
        path = tmp_path / "amr.json"
        reg1 = AMRRegistry(str(path))
        reg1.create_group({
            "display_name": "Persistent",
            "candidates": [{"provider_id": "p1", "model_id": "m1"}],
        })

        reg2 = AMRRegistry(str(path))
        group = reg2.get_group("persistent")
        assert group is not None
        assert group["display_name"] == "Persistent"
        assert len(group["candidates"]) == 1
        assert group["candidates"][0]["provider_id"] == "p1"

    def test_corrupted_json_recovery(self, tmp_path):
        """JSON 损坏时，应保留旧文件并返回空 store。"""
        path = tmp_path / "amr.json"
        path.write_text("not json {", encoding="utf-8")
        reg = AMRRegistry(str(path))
        store = reg._load_store()
        assert store["groups"] == []
        # 原始文件应被重命名为 .corrupted.*
        assert not path.exists()
        corrupted_files = [f for f in tmp_path.iterdir() if "corrupted" in f.name]
        assert len(corrupted_files) == 1


# ─────────────── Build From Providers ───────────────

class TestBuildFromProviders:
    def test_build_from_providers(self, tmp_path):
        """从 ProviderRegistry 同步应正确生成 candidates 和 priorities。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))

        mock_pr = MagicMock()
        mock_pr.list_providers.return_value = {
            "providers": [
                {
                    "id": "openai",
                    "enabled": True,
                    "catalog_visibility": "always_visible",
                    "capabilities": {"text": True, "vision": True},
                    "status": {"last_tested": "2026-06-07T00:00:00Z", "last_error": ""},
                    "models": [
                        {"id": "gpt-4", "enabled": True, "context_window": 128000, "capabilities": {"tools": True}},
                        {"id": "gpt-3.5", "enabled": False, "context_window": 16000},  # 禁用模型应被忽略
                    ],
                },
                {
                    "id": "anthropic",
                    "enabled": True,
                    "catalog_visibility": "focused_only",
                    "capabilities": {"text": True, "vision": False},
                    "status": {"last_tested": "2026-06-07T00:00:01Z", "last_error": "health timeout"},
                    "models": [
                        {"id": "claude-3", "enabled": True, "context_window": 200000},
                    ],
                },
                {
                    "id": "disabled_provider",
                    "enabled": False,
                    "capabilities": {"text": True},
                    "models": [
                        {"id": "m1", "enabled": True, "context_window": 1000},
                    ],
                },
            ]
        }

        group = reg.build_from_providers(mock_pr)
        assert group["id"] == "default"
        assert len(group["candidates"]) == 2  # gpt-4 + claude-3

        c1 = next(c for c in group["candidates"] if c["id"] == "openai/gpt-4")
        assert c1["priority"] == 1  # always_visible
        assert c1["context_window"] == 128000
        assert c1["capabilities"]["text"] is True
        assert c1["capabilities"]["vision"] is True
        assert c1["capabilities"]["tools"] is True  # model 级 capability 合并
        assert c1["health"]["last_error"] == ""

        c2 = next(c for c in group["candidates"] if c["id"] == "anthropic/claude-3")
        assert c2["priority"] == 2  # 非 always_visible
        assert c2["context_window"] == 200000
        assert c2["capabilities"]["vision"] is False
        assert c2["health"]["last_error"] == "health timeout"

    def test_build_from_providers_update_existing_default(self, tmp_path):
        """多次同步应更新同一个 default group，而非重复创建。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        mock_pr = MagicMock()
        mock_pr.list_providers.return_value = {
            "providers": [
                {
                    "id": "p1",
                    "enabled": True,
                    "catalog_visibility": "always_visible",
                    "capabilities": {"text": True},
                    "models": [{"id": "m1", "enabled": True, "context_window": 1000}],
                },
            ]
        }

        reg.build_from_providers(mock_pr)
        reg.build_from_providers(mock_pr)

        groups = reg.list_groups()["groups"]
        assert len(groups) == 1
        assert groups[0]["id"] == "default"

    def test_build_from_providers_inherits_provider_images_from_legacy_model_caps(self, tmp_path):
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        mock_pr = MagicMock()
        mock_pr.list_providers.return_value = {
            "providers": [
                {
                    "id": "mixed",
                    "enabled": True,
                    "catalog_visibility": "selected_models",
                    "capabilities": {"text": True, "images": True},
                    "models": [
                        {"id": "auto", "enabled": True, "context_window": 128000, "capabilities": normalize_capabilities(None)},
                    ],
                },
            ]
        }

        group = reg.build_from_providers(mock_pr)

        candidate = group["candidates"][0]
        assert candidate["id"] == "mixed/auto"
        assert candidate["capabilities"]["images"] is True

    def test_build_from_providers_infers_images_from_media_profile(self, tmp_path):
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        mock_pr = MagicMock()
        mock_pr.list_providers.return_value = {
            "providers": [
                {
                    "id": "native",
                    "enabled": True,
                    "api_format": "openai_responses",
                    "capabilities": {"text": True},
                    "media_profile": {"default_image_provider": True, "openai_compatible_media": True},
                    "models": [
                        {"id": "auto", "enabled": True, "context_window": 128000, "capabilities": normalize_capabilities(None)},
                    ],
                },
            ]
        }

        group = reg.build_from_providers(mock_pr)

        candidate = group["candidates"][0]
        assert candidate["id"] == "native/auto"
        assert candidate["capabilities"]["images"] is True


# ─────────────── Route Integration ───────────────

class TestAddCandidatesToGroup:
    def test_add_candidates_to_group_creates_default_group(self, tmp_path):
        reg = AMRRegistry(str(tmp_path / "amr.json"))

        group = reg.add_candidates_to_group("default", [
            {
                "provider_id": "p1",
                "model_id": "m1",
                "priority": 2,
                "context_window": 1000,
                "capabilities": {"text": True},
            },
        ])

        assert group["id"] == DEFAULT_GROUP_ID
        assert group["display_name"] == DEFAULT_GROUP_DISPLAY_NAME
        assert group["upserted_count"] == 1
        assert len(group["candidates"]) == 1
        assert group["candidates"][0]["id"] == "p1/m1"

    def test_add_candidates_to_group_upserts_existing_candidate(self, tmp_path):
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": DEFAULT_GROUP_ID,
            "display_name": DEFAULT_GROUP_DISPLAY_NAME,
            "candidates": [
                {
                    "id": "p1/m1",
                    "provider_id": "p1",
                    "model_id": "m1",
                    "priority": 2,
                    "context_window": 1000,
                    "capabilities": {"text": True, "vision": False},
                },
            ],
        })

        group = reg.add_candidates_to_group("default", [
            {
                "id": "p1/m1",
                "provider_id": "p1",
                "model_id": "m1",
                "priority": 1,
                "context_window": 2000,
                "capabilities": {"text": True, "vision": True},
            },
            {
                "provider_id": "p2",
                "model_id": "m2",
                "priority": 3,
                "context_window": 500,
                "capabilities": {"text": True},
            },
        ])

        assert group["upserted_count"] == 2
        assert len(group["candidates"]) == 2
        updated = next(c for c in group["candidates"] if c["id"] == "p1/m1")
        added = next(c for c in group["candidates"] if c["id"] == "p2/m2")
        assert updated["priority"] == 1
        assert updated["context_window"] == 2000
        assert updated["capabilities"]["vision"] is True
        assert added["priority"] == 3


class TestRouteIntegration:
    def test_route_success(self, tmp_path):
        """正常路由应按 priority 选择 winner。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "c1", "provider_id": "p1", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
                {"id": "c2", "provider_id": "p2", "model_id": "m2", "priority": 2, "enabled": True, "context_window": 500, "capabilities": {"text": True, "vision": True}},
            ],
        })

        result = reg.route("test", {"text"}, 0)
        assert result["success"] is True
        assert result["candidate_id"] == "c1"  # priority 1 胜出

        result2 = reg.route("test", {"vision"}, 0)
        assert result2["success"] is True
        assert result2["candidate_id"] == "c2"  # 只有 c2 支持 vision

    def test_same_priority_uses_saved_candidate_order(self, tmp_path):
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "z-provider/m2", "provider_id": "z-provider", "model_id": "m2", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
                {"id": "a-provider/m1", "provider_id": "a-provider", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
            ],
        })

        result = reg.route("test", {"text"}, 0)

        assert result["success"] is True
        assert result["candidate_id"] == "z-provider/m2"

    def test_route_context_window_failure(self, tmp_path):
        """上下文窗口不足时应返回失败。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "c1", "provider_id": "p1", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
            ],
        })
        result = reg.route("test", {"text"}, 2000)
        assert result["success"] is False
        assert "Context window" in result["error"]

    def test_route_missing_group(self, tmp_path):
        """路由到不存在的 group 应返回失败。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        result = reg.route("missing", {"text"}, 0)
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_to_rotation_engine(self, tmp_path):
        """to_rotation_engine 应返回可用的 AdaptiveModelRotation 实例。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "c1", "provider_id": "p1", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
            ],
        })
        engine = reg.to_rotation_engine()
        from model_rotation import AdaptiveModelRotation
        assert isinstance(engine, AdaptiveModelRotation)
        assert engine.get_group_context_window("test") == 1000

    def test_route_image_candidates_selects_image_model(self, tmp_path):
        """使用 candidate_list='image_candidates' 应正确选择图像模型。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "c1", "provider_id": "p1", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
            ],
            "image_candidates": [
                {"id": "img1", "provider_id": "p2", "model_id": "gpt-image-1", "priority": 1, "enabled": True, "context_window": 0, "capabilities": {"images": True}},
            ],
        })

        result = reg.route("test", {"images"}, 0, candidate_list="image_candidates")
        assert result["success"] is True
        assert result["candidate_id"] == "img1"
        assert result["provider_id"] == "p2"
        assert result["model_id"] == "gpt-image-1"

    def test_route_image_candidates_empty_returns_error(self, tmp_path):
        """image_candidates 为空时应返回失败，不回退到 candidates。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({
            "id": "test",
            "display_name": "Test",
            "candidates": [
                {"id": "c1", "provider_id": "p1", "model_id": "m1", "priority": 1, "enabled": True, "context_window": 1000, "capabilities": {"text": True}},
            ],
            "image_candidates": [],
        })

        result = reg.route("test", {"images"}, 0, candidate_list="image_candidates")
        assert result["success"] is False
        assert "image_candidates" in result["error"]

    def test_build_from_providers_separates_native_and_domestic_image_models(self, tmp_path):
        """build_from_providers 应将纯原生代理和国内代理的图像模型都归入 image_candidates。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        mock_pr = MagicMock()
        mock_pr.list_providers.return_value = {
            "providers": [
                {
                    "id": "native-proxy",
                    "enabled": True,
                    "api_format": "openai_responses",
                    "capabilities": {"text": True},
                    "models": [
                        {"id": "gpt-image-2", "enabled": True, "context_window": 0, "capabilities": normalize_capabilities({"images": True, "text": False})},
                    ],
                },
                {
                    "id": "domestic-proxy",
                    "enabled": True,
                    "api_format": "openai_responses",
                    "capabilities": {"text": True},
                    "models": [
                        {"id": "qwen-image-2.0-pro", "enabled": True, "context_window": 0, "capabilities": normalize_capabilities({"images": True, "text": False})},
                    ],
                },
            ]
        }

        group = reg.build_from_providers(mock_pr)

        assert len(group["image_candidates"]) == 2
        image_ids = {c["id"] for c in group["image_candidates"]}
        assert "native-proxy/gpt-image-2" in image_ids
        assert "domestic-proxy/qwen-image-2.0-pro" in image_ids
        assert len(group["candidates"]) == 0


# ─────────────── Export ───────────────

class TestExport:
    def test_export_bundle(self, tmp_path):
        """export_bundle 应包含 schema_version 和 groups。"""
        reg = AMRRegistry(str(tmp_path / "amr.json"))
        reg.create_group({"id": "g1", "display_name": "G1"})
        bundle = reg.export_bundle()
        assert bundle["schema_version"] == 1
        assert "exported_at" in bundle
        assert len(bundle["groups"]) == 1
        assert bundle["groups"][0]["id"] == "g1"
