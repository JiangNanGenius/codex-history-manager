import tempfile
import unittest
from pathlib import Path

from startup_manager import (
    CommandResult,
    PACKAGED_RELEASE_EXE_NAME,
    STARTUP_CONFIRMATION,
    StartupManager,
)


class StartupManagerTest(unittest.TestCase):
    def test_startup_folder_apply_writes_cmd_without_real_windows_mutation(self):
        calls = []

        def runner(args):
            calls.append(args)
            return CommandResult(returncode=1, stderr="task not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StartupManager(
                startup_dir=Path(tmpdir),
                runner=runner,
                platform_name="Windows",
                module_dir=Path(tmpdir),
            )
            result = manager.apply(
                {
                    "startup_enabled": True,
                    "startup_mode": "startup_folder",
                    "startup_shortcut_name": "CodexEnhanceManager.cmd",
                    "startup_target_path": r"C:\Apps\CodexEnhanceManager.exe",
                    "startup_arguments": "--minimized",
                },
                confirmation=STARTUP_CONFIRMATION,
            )

            self.assertTrue(result["success"])
            entry = Path(tmpdir) / "CodexEnhanceManager.cmd"
            self.assertTrue(entry.exists())
            self.assertIn(r"C:\Apps\CodexEnhanceManager.exe", entry.read_text(encoding="utf-8"))
            self.assertEqual(calls[0][0], "schtasks.exe")
            self.assertIn("/Query", calls[0])

    def test_scheduled_task_preview_and_apply_use_onlogon_highest(self):
        calls = []

        def runner(args):
            calls.append(args)
            return CommandResult(returncode=0, stdout="ok")

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StartupManager(startup_dir=Path(tmpdir), runner=runner, platform_name="Windows")
            settings = {
                "startup_enabled": True,
                "startup_mode": "scheduled_task_highest",
                "startup_task_name": "CodexEnhanceManager",
                "startup_target_path": r"C:\Apps\CodexEnhanceManager.exe",
            }
            preview = manager.preview(settings)
            create_action = next(action for action in preview["actions"] if action["kind"] == "scheduled_task")

            self.assertEqual(create_action["action"], "create")
            self.assertIn("/SC", create_action["argv"])
            self.assertIn("ONLOGON", create_action["argv"])
            self.assertIn("/RL", create_action["argv"])
            self.assertIn("HIGHEST", create_action["argv"])

            result = manager.apply(settings, confirmation=STARTUP_CONFIRMATION)

            self.assertTrue(result["success"])
            self.assertEqual(calls[-1], create_action["argv"])

    def test_preview_reports_release_exe_target_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / PACKAGED_RELEASE_EXE_NAME
            exe_path.write_bytes(b"fake exe")
            manager = StartupManager(startup_dir=Path(tmpdir), platform_name="Windows")

            preview = manager.preview({
                "startup_enabled": True,
                "startup_mode": "startup_folder",
                "startup_target_path": str(exe_path),
            })

            diagnostics = preview["target_diagnostics"]
            self.assertTrue(diagnostics["target_exists"])
            self.assertTrue(diagnostics["target_is_exe"])
            self.assertTrue(diagnostics["target_matches_release_exe_name"])
            self.assertTrue(diagnostics["release_startup_ready"])
            self.assertEqual(diagnostics["warning_count"], 0)

    def test_preview_warns_when_startup_target_is_not_packaged_exe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "run-dev.py"
            script_path.write_text("print('dev')\n", encoding="utf-8")
            manager = StartupManager(startup_dir=Path(tmpdir), platform_name="Windows")

            preview = manager.preview({
                "startup_enabled": True,
                "startup_mode": "startup_folder",
                "startup_target_path": str(script_path),
            })

            diagnostics = preview["target_diagnostics"]
            self.assertTrue(diagnostics["target_exists"])
            self.assertFalse(diagnostics["target_is_exe"])
            self.assertFalse(diagnostics["release_startup_ready"])
            self.assertGreaterEqual(diagnostics["warning_count"], 1)
            self.assertTrue(any("not a Windows EXE" in item for item in diagnostics["warnings"]))

    def test_remove_deletes_startup_file_and_existing_task(self):
        calls = []

        def runner(args):
            calls.append(args)
            if "/Query" in args:
                return CommandResult(returncode=0, stdout="TaskName: CodexEnhanceManager")
            return CommandResult(returncode=0, stdout="deleted")

        with tempfile.TemporaryDirectory() as tmpdir:
            startup_dir = Path(tmpdir)
            entry = startup_dir / "CodexEnhanceManager.cmd"
            entry.write_text("@echo off\r\n", encoding="utf-8")
            manager = StartupManager(startup_dir=startup_dir, runner=runner, platform_name="Windows")

            result = manager.remove(
                {"startup_task_name": "CodexEnhanceManager", "startup_shortcut_name": "CodexEnhanceManager.cmd"},
                confirmation=STARTUP_CONFIRMATION,
            )

            self.assertTrue(result["success"])
            self.assertFalse(entry.exists())
            self.assertTrue(any("/Delete" in call for call in calls))

    def test_apply_requires_confirmation_and_windows(self):
        manager = StartupManager(platform_name="Linux")

        missing_confirmation = manager.apply({"startup_enabled": True, "startup_mode": "startup_folder"})
        self.assertFalse(missing_confirmation["success"])
        self.assertEqual(missing_confirmation["required_confirmation"], STARTUP_CONFIRMATION)

        unsupported = manager.apply(
            {"startup_enabled": True, "startup_mode": "startup_folder"},
            confirmation=STARTUP_CONFIRMATION,
        )
        self.assertFalse(unsupported["success"])
        self.assertIn("Windows", unsupported["error"])


if __name__ == "__main__":
    unittest.main()
