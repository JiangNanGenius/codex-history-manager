"""
tests/test_move_repair.py - MoveRepairManager 单元测试。

设计意图：
  - 不依赖真实 Codex 配置、Git 命令或用户主目录：所有外部依赖均用 unittest.mock 替换。
  - 使用 tempfile.TemporaryDirectory 构建完整 fake Codex home（SQLite + JSONL + Index）。
  - 覆盖读取、dry_run、执行移动、回滚、一致性校验、修复检测六个核心场景。
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from move_repair import MoveRepairManager


def _git_mock(cmd, **kwargs):
    """通用 git 命令 mock：ls-files 返回两个文件，rev-parse 返回传入路径本身。"""
    mock = MagicMock()
    mock.returncode = 0
    if "ls-files" in cmd:
        mock.stdout = "README.md\nsrc/main.py\n"
    elif "rev-parse" in cmd and "--show-toplevel" in cmd:
        repo_path = cmd[2]
        mock.stdout = str(Path(repo_path).resolve()) + "\n"
    else:
        mock.returncode = 1
        mock.stdout = ""
    return mock


class MoveRepairTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.codex_home = Path(self.tmpdir.name) / "codex"
        self.codex_home.mkdir()
        self.db_path = self.codex_home / "threads.db"
        self.sessions_dir = self.codex_home / "sessions"
        self.sessions_dir.mkdir()
        self.index_path = self.codex_home / "session_index.jsonl"
        self.backup_dir = Path(self.tmpdir.name) / "backups"
        self.backup_dir.mkdir()

        # 创建 fake Git 仓库（仅目录结构，git 命令全部 mock）
        self.fake_repo = Path(self.tmpdir.name) / "repo"
        self.fake_repo.mkdir()
        (self.fake_repo / ".git").mkdir()

        self.target_repo = Path(self.tmpdir.name) / "target_repo"
        self.target_repo.mkdir()
        (self.target_repo / ".git").mkdir()

        self.mgr = MoveRepairManager(
            codex_home=str(self.codex_home),
            db_path=str(self.db_path),
            sessions_dir=str(self.sessions_dir),
        )
        # 重定向备份目录，避免污染真实 home
        self.mgr._backup_dir = self.backup_dir

        self.thread_id = "test-thread-123"
        self.original_cwd = str(self.fake_repo)

    def _create_db(self, thread_id, cwd):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                cwd TEXT,
                title TEXT,
                created_at TEXT,
                model TEXT,
                provider TEXT,
                archived INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO threads (id, cwd, title, created_at, model, provider, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, cwd, "Test Thread", "2024-01-01T00:00:00", "gpt-4", "openai", 0),
        )
        conn.commit()
        conn.close()

    def _create_jsonl(self, thread_id, cwd):
        path = self.sessions_dir / f"{thread_id}.jsonl"
        record = {
            "type": "session_meta",
            "payload": {
                "cwd": cwd,
                "title": "Test Thread",
                "model": "gpt-4",
                "provider": "openai",
            },
        }
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def _create_index(self, thread_id, cwd):
        record = {"id": thread_id, "cwd": cwd, "title": "Test Thread"}
        self.index_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    def _read_db_cwd(self, thread_id):
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute("SELECT cwd FROM threads WHERE id=?", (thread_id,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def _read_jsonl_cwd(self, thread_id):
        path = self.sessions_dir / f"{thread_id}.jsonl"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict) and record.get("type") == "session_meta":
                    payload = record.get("payload", {})
                    return payload.get("cwd", "") if isinstance(payload, dict) else record.get("cwd", "")
        return None

    def _read_index_cwd(self, thread_id):
        if not self.index_path.exists():
            return None
        with open(self.index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict) and str(record.get("id", "")) == thread_id:
                    return record.get("cwd", "")
        return None


class TestReadMetadata(MoveRepairTestBase):
    def test_read_thread_metadata(self):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)

        meta = self.mgr.read_thread_metadata(self.thread_id)

        self.assertEqual(meta.get("id"), self.thread_id)
        self.assertEqual(meta.get("cwd"), self.original_cwd)
        self.assertEqual(meta.get("title"), "Test Thread")
        self.assertEqual(meta.get("model"), "gpt-4")
        self.assertEqual(meta.get("provider"), "openai")
        self.assertTrue(meta.get("jsonl_found"))
        self.assertIn(self.thread_id, meta.get("jsonl_path", ""))


class TestDryRun(MoveRepairTestBase):
    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    def test_dry_run_move_valid_target(self, _mock_run):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)

        result = self.mgr.dry_run_move(self.thread_id, str(self.target_repo))

        self.assertTrue(result["can_move"])
        self.assertTrue(any("2 个 tracked files" in r for r in result["reasons"]))
        self.assertEqual(result["expected_changes"]["sqlite_cwd_new"], str(self.target_repo))
        self.assertFalse(result["restart_required"])
        self.assertFalse(result["restart_guidance"]["ui_refresh_required"])
        self.assertIn("Dry-run", result["restart_guidance"]["message"])

    def test_dry_run_move_invalid_target(self):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)

        result = self.mgr.dry_run_move(self.thread_id, "/nonexistent/path/12345")

        self.assertFalse(result["can_move"])
        self.assertTrue(any("不存在" in r for r in result["reasons"]))


class TestExecuteMove(MoveRepairTestBase):
    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    def test_execute_move_updates_all_sources(self, _mock_run):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)
        self._create_index(self.thread_id, self.original_cwd)

        result = self.mgr.execute_move(self.thread_id, str(self.target_repo))

        self.assertTrue(result["success"])
        self.assertFalse(result["restart_required"])
        self.assertTrue(result["restart_guidance"]["ui_refresh_required"])
        self.assertTrue(result["restart_guidance"]["restart_recommended"])
        self.assertIn("Refresh or reopen", result["restart_guidance"]["next_action"])
        self.assertTrue(result["changes"]["db_updated"])
        self.assertTrue(result["changes"]["jsonl_updated"])
        self.assertTrue(result["changes"]["index_updated"])
        self.assertTrue(result["verification"]["consistent"])

        self.assertEqual(self._read_db_cwd(self.thread_id), str(self.target_repo))
        self.assertEqual(self._read_jsonl_cwd(self.thread_id), str(self.target_repo))
        self.assertEqual(self._read_index_cwd(self.thread_id), str(self.target_repo))

    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    @patch.object(MoveRepairManager, "verify_consistency")
    def test_execute_move_rollback_on_failure(self, mock_verify, _mock_run):
        mock_verify.return_value = {"consistent": False, "reasons": ["mock failure"]}

        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)
        self._create_index(self.thread_id, self.original_cwd)

        result = self.mgr.execute_move(self.thread_id, str(self.target_repo))

        self.assertFalse(result["success"])
        self.assertTrue(result["error"])
        self.assertFalse(result["restart_required"])
        self.assertFalse(result["restart_guidance"]["ui_refresh_required"])
        self.assertIn("rolled back", result["restart_guidance"]["message"])
        self.assertFalse(result["changes"]["db_updated"])
        self.assertFalse(result["changes"]["jsonl_updated"])
        self.assertFalse(result["changes"]["index_updated"])

        # 验证 rollback 后数据恢复
        self.assertEqual(self._read_db_cwd(self.thread_id), self.original_cwd)
        self.assertEqual(self._read_jsonl_cwd(self.thread_id), self.original_cwd)
        self.assertEqual(self._read_index_cwd(self.thread_id), self.original_cwd)


class TestVerifyConsistency(MoveRepairTestBase):
    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    def test_verify_consistency_passes(self, _mock_run):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)
        self._create_index(self.thread_id, self.original_cwd)

        result = self.mgr.verify_consistency(self.thread_id)

        self.assertTrue(result["consistent"])
        self.assertTrue(result["git_valid"])
        self.assertEqual(result["sqlite_cwd"], self.original_cwd)
        self.assertEqual(result["jsonl_cwd"], self.original_cwd)
        self.assertEqual(result["index_cwd"], self.original_cwd)
        self.assertFalse(result["restart_required"])
        self.assertIn("valid Git workspace", result["restart_guidance"]["message"])

    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    def test_verify_consistency_fails(self, _mock_run):
        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, str(self.target_repo))  # 不一致
        self._create_index(self.thread_id, "/another/path")

        result = self.mgr.verify_consistency(self.thread_id)

        self.assertFalse(result["consistent"])
        self.assertTrue(any("不一致" in r for r in result["reasons"]))
        self.assertFalse(result["restart_required"])
        self.assertIn("restarting alone", result["restart_guidance"]["next_action"])


class TestRepairCurrentThread(MoveRepairTestBase):
    @patch("move_repair.subprocess.run", side_effect=_git_mock)
    @patch("move_repair.os.getcwd")
    def test_repair_current_thread_no_change_needed(self, mock_getcwd, _mock_run):
        mock_getcwd.return_value = self.original_cwd

        self._create_db(self.thread_id, self.original_cwd)
        self._create_jsonl(self.thread_id, self.original_cwd)
        self._create_index(self.thread_id, self.original_cwd)

        result = self.mgr.repair_current_thread()

        self.assertFalse(result["mismatch_detected"])
        self.assertTrue(any("无需修复" in a for a in result["suggested_actions"]))
        self.assertEqual(len(result["matched_threads"]), 1)
        self.assertEqual(result["matched_threads"][0]["thread_id"], self.thread_id)
        self.assertFalse(result["restart_required"])
        self.assertIn("already match", result["restart_guidance"]["message"])


if __name__ == "__main__":
    unittest.main()
