import json
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
                patch.object(sync, "_windowsapps_codex_gui_candidates", return_value=[windowsapps_gui]), \
                patch.object(sync.shutil, "which", return_value=path_cli):
            candidates = sync.codex_launch_candidates(codex_cli_path=configured_cli)

        self.assertEqual(candidates[:2], [appdata_gui, windowsapps_gui])
        self.assertLess(candidates.index(windowsapps_gui), candidates.index(configured_cli))
        self.assertIn(bin_cli, candidates)
        self.assertEqual(candidates[-1], path_cli)


if __name__ == "__main__":
    unittest.main()
