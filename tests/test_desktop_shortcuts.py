import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_shortcuts import (
    START_CODEX_ARG,
    DesktopShortcutManager,
    ShortcutCommandResult,
)


class DesktopShortcutManagerTest(unittest.TestCase):
    def test_shortcut_specs_include_normal_and_start_codex_argument(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DesktopShortcutManager(
                desktop_dir=Path(tmpdir),
                platform_name="Windows",
                module_dir=Path(tmpdir),
            )

            specs = manager.shortcut_specs(normal=True, start_codex=True)

            self.assertEqual([item["kind"] for item in specs], ["normal", "start_codex"])
            self.assertNotIn(START_CODEX_ARG, specs[0]["arguments"])
            self.assertIn(START_CODEX_ARG, specs[1]["arguments"])
            self.assertTrue(specs[0]["path"].endswith(".lnk"))
            self.assertTrue(specs[1]["path"].endswith(".lnk"))

    def test_create_shortcuts_uses_powershell_com_without_real_desktop_mutation(self):
        calls = []

        def runner(args):
            calls.append(args)
            script = args[-1]
            marker = "$shortcut = $shell.CreateShortcut('"
            start = script.index(marker) + len(marker)
            end = script.index("')", start)
            Path(script[start:end]).write_text("fake shortcut", encoding="utf-8")
            return ShortcutCommandResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DesktopShortcutManager(
                desktop_dir=Path(tmpdir),
                runner=runner,
                platform_name="Windows",
                module_dir=Path(tmpdir),
            )

            result = manager.create_shortcuts(normal=False, start_codex=True)

            self.assertTrue(result["success"])
            self.assertEqual(len(result["shortcuts"]), 1)
            self.assertEqual(result["shortcuts"][0]["kind"], "start_codex")
            self.assertIn("-ExecutionPolicy", calls[0])
            self.assertIn(START_CODEX_ARG, calls[0][-1])

    def test_create_shortcuts_rejects_non_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = DesktopShortcutManager(desktop_dir=Path(tmpdir), platform_name="Linux")

            result = manager.create_shortcuts(normal=True, start_codex=False)

            self.assertFalse(result["success"])
            self.assertFalse(result["supported"])

    def test_frozen_target_uses_executable_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("desktop_shortcuts.sys.frozen", True, create=True), \
                patch("desktop_shortcuts.sys.executable", str(Path(tmpdir) / "CodexHistoryManager.exe")):
            manager = DesktopShortcutManager(desktop_dir=Path(tmpdir), platform_name="Windows")

            target, args, _ = manager.resolve_target_and_arguments([START_CODEX_ARG])

            self.assertTrue(target.endswith("CodexHistoryManager.exe"))
            self.assertEqual(args, START_CODEX_ARG)


if __name__ == "__main__":
    unittest.main()
