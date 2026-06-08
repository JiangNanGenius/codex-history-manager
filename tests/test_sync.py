import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync


class SyncHelpersTest(unittest.TestCase):
    def test_sync_rollout_file_updates_all_session_meta_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            path.write_text(
                "not-json\n"
                '{"type":"event","payload":{"model":"old"}}\n'
                '{"type":"session_meta","payload":{"model_provider":"old","model":"old"}}\n'
                '{"type":"session_meta","provider":"old","model_name":"old"}\n',
                encoding="utf-8",
            )

            changed = sync.sync_rollout_file(str(path), "openai", "gpt-5")

            self.assertTrue(changed)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "not-json")
            self.assertEqual(json.loads(lines[1]), {"type": "event", "payload": {"model": "old"}})
            self.assertEqual(json.loads(lines[2])["payload"]["model_provider"], "openai")
            self.assertEqual(json.loads(lines[2])["payload"]["model"], "gpt-5")
            self.assertEqual(json.loads(lines[3])["provider"], "openai")
            self.assertEqual(json.loads(lines[3])["model_name"], "gpt-5")

    def test_sync_rollout_file_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            original = '{"type":"session_meta","payload":{"model_provider":"old","model":"old"}}\n'
            path.write_text(original, encoding="utf-8")

            changed = sync.sync_rollout_file(str(path), "openai", "gpt-5", dry_run=True)

            self.assertTrue(changed)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_sync_state_database_accepts_provider_and_model_name_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state_9.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, provider TEXT, model_name TEXT)")
            conn.execute("INSERT INTO threads (id, provider, model_name) VALUES (?, ?, ?)", ("t1", "old", "old-model"))
            conn.commit()
            conn.close()

            seen, updated = sync.sync_state_database(str(db_path), "openai", "gpt-5")

            self.assertEqual((seen, updated), (1, 1))
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT provider, model_name FROM threads WHERE id='t1'").fetchone()
            conn.close()
            self.assertEqual(row, ("openai", "gpt-5"))

    def test_full_sync_uses_configured_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex_home"
            codex_home.mkdir()
            db_path = root / "custom_state.sqlite"
            sessions_dir = root / "custom_sessions"
            archived_dir = root / "custom_archived"
            sessions_dir.mkdir()
            archived_dir.mkdir()
            index_path = root / "custom_index.jsonl"

            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, model_provider TEXT, model TEXT, archived INTEGER)")
            conn.execute(
                "INSERT INTO threads (id, cwd, model_provider, model, archived) VALUES (?, ?, ?, ?, ?)",
                ("thread-a", str(root), "old", "old-model", 0),
            )
            conn.commit()
            conn.close()

            rollout = sessions_dir / "thread-a.jsonl"
            rollout.write_text(
                json.dumps({"type": "session_meta", "payload": {"model_provider": "old", "model": "old-model"}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            index_path.write_text(
                json.dumps({"id": "thread-a", "model_provider": "old", "model": "old-model"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            stats = sync.full_sync(
                codex_home=str(codex_home),
                db_path=str(db_path),
                sessions_dir=str(sessions_dir),
                archived_dir=str(archived_dir),
                index_path=str(index_path),
                target_provider="openai",
                target_model="gpt-5",
            )

            self.assertEqual(stats.db_threads_updated, 1)
            self.assertEqual(stats.rollout_files_updated, 1)
            self.assertGreaterEqual(stats.index_rows_updated, 1)
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT model_provider, model FROM threads WHERE id='thread-a'").fetchone()
            conn.close()
            self.assertEqual(row, ("openai", "gpt-5"))
            self.assertEqual(json.loads(rollout.read_text(encoding="utf-8"))["payload"]["model"], "gpt-5")

    def test_kill_codex_uses_one_taskkill_for_process_tree(self):
        with patch.object(sync, "is_codex_running", side_effect=[(True, [123, 456]), (False, [])]), \
                patch("subprocess.run") as run, \
                patch.object(sync.time, "sleep") as sleep:
            ok, message = sync.kill_codex(timeout=4)

        self.assertTrue(ok)
        self.assertIn("已关闭 Codex", message)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "123", "/PID", "456", "/T", "/F"])
        self.assertEqual(run.call_args.kwargs["timeout"], 4)
        sleep.assert_called_once_with(0.4)

    def test_codex_launch_candidates_prefer_visible_gui_before_cli_shims(self):
        local = r"C:\Users\demo\AppData\Local"
        appdata_gui = local + r"\OpenAI\Codex\Codex.exe"
        configured_cli = local + r"\OpenAI\Codex\bin\custom\codex.exe"
        bin_dir = local + r"\OpenAI\Codex\bin"
        bin_cli = local + r"\OpenAI\Codex\bin\b\codex.exe"
        windowsapps_gui = r"C:\Program Files\WindowsApps\OpenAI.Codex_1.0.0.0_x64__demo\app\Codex.exe"
        path_cli = r"C:\Tools\codex.cmd"
        existing = {appdata_gui, configured_cli, bin_dir, bin_cli, windowsapps_gui, path_cli}

        with patch.dict(sync.os.environ, {"LOCALAPPDATA": local, "CODEX_CLI_PATH": ""}), \
                patch.object(sync.os.path, "exists", side_effect=lambda path: path in existing), \
                patch.object(sync.os, "listdir", return_value=["b"]), \
                patch.object(sync, "find_codex_desktop_launchers", return_value=[appdata_gui, windowsapps_gui]), \
                patch.object(sync.shutil, "which", return_value=path_cli):
            candidates = sync.codex_launch_candidates(codex_cli_path=configured_cli)

        self.assertEqual(candidates[:2], [appdata_gui, windowsapps_gui])
        self.assertLess(candidates.index(windowsapps_gui), candidates.index(configured_cli))
        self.assertIn(bin_cli, candidates)
        self.assertEqual(candidates[-1], path_cli)

    def test_find_codex_desktop_launchers_requires_app_asar_and_uses_latest_squirrel_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            old_root = local / "Programs" / "Codex" / "app-2.9.0"
            new_root = local / "Programs" / "Codex" / "app-2.10.0"
            cli_root = local / "OpenAI" / "Codex" / "bin" / "latest"
            for root in (old_root, new_root):
                (root / "resources").mkdir(parents=True)
                (root / "resources" / "app.asar").write_text("asar", encoding="utf-8")
                (root / "Codex.exe").write_text("", encoding="utf-8")
            cli_root.mkdir(parents=True)
            (cli_root / "codex.exe").write_text("", encoding="utf-8")

            with patch.object(sync.os, "name", "nt"), \
                    patch.dict(sync.os.environ, {
                        "LOCALAPPDATA": str(local),
                        "ProgramFiles": str(Path(tmp) / "PF"),
                        "ProgramFiles(x86)": "",
                    }), \
                    patch.object(sync, "_windows_store_codex_installs", return_value=[]):
                launchers = sync.find_codex_desktop_launchers()

        self.assertEqual(Path(launchers[0]).parent.name, "app-2.10.0")
        self.assertNotIn(str(cli_root / "codex.exe"), launchers)

    def test_launch_codex_path_passes_extra_args(self):
        with patch("subprocess.Popen") as popen, patch.object(sync.os, "name", "posix"):
            sync._launch_codex_path("/opt/codex/Codex", extra_args=["--remote-debugging-port=51236"])

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/opt/codex/Codex", "--remote-debugging-port=51236"])


if __name__ == "__main__":
    unittest.main()
