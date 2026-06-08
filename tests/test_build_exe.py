import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import build_exe
from startup_manager import PACKAGED_RELEASE_EXE_NAME


class BuildExeReleaseTest(unittest.TestCase):
    def test_release_exe_name_matches_startup_diagnostics(self):
        self.assertEqual(build_exe.EXE_NAME, PACKAGED_RELEASE_EXE_NAME)

    def test_hidden_imports_cover_runtime_modules(self):
        required = {
            "app",
            "amr_registry",
            "capabilities",
            "currency",
            "desktop_shortcuts",
            "diagnostics",
            "domestic_responses",
            "media_adapters",
            "media_proxy",
            "model_catalog",
            "model_rotation",
            "providers",
            "quota",
            "request_capabilities",
            "request_logs",
            "responses_adapter",
            "startup_manager",
        }

        missing = required - set(build_exe.LOCAL_MODULES)

        self.assertEqual(missing, set())

    def test_build_dependencies_use_importable_module_names(self):
        dependencies = dict(build_exe.BUILD_DEPENDENCIES)

        self.assertEqual(dependencies["pyinstaller"], "PyInstaller")
        self.assertEqual(dependencies["pywebview"], "webview")
        self.assertEqual(dependencies["Pillow"], "PIL")

    def test_release_manifest_records_required_exe_asset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            exe_path = tmp_path / build_exe.EXE_NAME
            payload = b"fake-exe" * 128
            exe_path.write_bytes(payload)

            with patch.object(build_exe, "OUTPUT_DIR", tmp_path):
                manifest_path = build_exe.write_release_manifest(exe_path, smoke_tested=True)

            self.assertIsNotNone(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            asset = manifest["release_assets"][0]
            self.assertEqual(asset["name"], build_exe.EXE_NAME)
            self.assertEqual(asset["path"], str(exe_path.resolve()).replace("\\", "/"))
            self.assertEqual(asset["size_bytes"], len(payload))
            self.assertEqual(asset["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertTrue(asset["required_for_github_release"])
            self.assertIn("source archives alone are not enough", manifest["release_rule"])
            self.assertTrue(manifest["smoke_test"]["required_for_github_release"])
            self.assertTrue(manifest["smoke_test"]["passed"])
            self.assertEqual(manifest["smoke_test"]["command"], f"{build_exe.EXE_NAME} --smoke-test")
            self.assertIn("WebView window options", manifest["smoke_test"]["covers"])

    def test_verify_exe_can_check_release_asset_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / build_exe.EXE_NAME
            exe_path.write_bytes(b"x" * 2048)

            self.assertTrue(build_exe.verify_exe(exe_path, min_size_mb=0))
            self.assertFalse(build_exe.verify_exe(exe_path, min_size_mb=1))

    def test_smoke_test_exe_invokes_packaged_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe_path = Path(tmpdir) / build_exe.EXE_NAME
            exe_path.write_bytes(b"fake-exe")
            completed = MagicMock(returncode=0)

            with patch.object(build_exe.subprocess, "run", return_value=completed) as mock_run:
                self.assertTrue(build_exe.smoke_test_exe(exe_path, timeout_seconds=3))

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            self.assertEqual(args[0], [str(exe_path), "--smoke-test"])
            self.assertEqual(kwargs["timeout"], 3)

    def test_release_workflow_uploads_exe_and_manifest(self):
        workflow = (build_exe.PROJECT_DIR / ".github" / "workflows" / "windows-release.yml").read_text(
            encoding="utf-8",
        )

        self.assertIn("release:", workflow)
        self.assertIn("types: [published]", workflow)
        self.assertIn("--smoke-test", workflow)
        self.assertIn("Verify release assets and smoke test EXE", workflow)
        self.assertIn("dist/CodexHistoryManager.exe", workflow)
        self.assertIn("dist/release-manifest.json", workflow)
        self.assertIn("Required release asset missing", workflow)


if __name__ == "__main__":
    unittest.main()
