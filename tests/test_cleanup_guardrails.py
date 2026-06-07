import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import _cleanup_target, _cleanup_targets, _uninstall_cleanup_targets


class CleanupGuardrailTest(unittest.TestCase):
    def test_cleanup_target_refuses_unsafe_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "keep"
            path.mkdir()

            result = _cleanup_target({
                "id": "unsafe",
                "path": str(path),
                "safe": False,
            })

            self.assertFalse(result["success"])
            self.assertTrue(path.exists())

    def test_uninstall_preview_marks_external_provider_store_manual(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            app_root = tmp / "Codex Enhance Manager"
            app_root.mkdir()
            external_store = tmp / "external" / "providers.json"
            external_store.parent.mkdir()
            external_store.write_text("{}", encoding="utf-8")

            class StubConfig:
                def get(self, key, default=None):
                    if key == "provider_store_path":
                        return str(external_store)
                    return default

            with (
                patch("app.app_data_dir", return_value=app_root),
                patch("app.LEGACY_CONFIG_FILE", tmp / "legacy.json"),
                patch("app.LEGACY_APP_DIR", tmp / "legacy_app"),
            ):
                targets = _uninstall_cleanup_targets(StubConfig())

            external = next(target for target in targets if target["id"] == "external_provider_store")
            self.assertFalse(external["safe"])
            self.assertEqual(external["path"], str(external_store))

    def test_cleanup_preview_targets_have_descriptions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            app_root = tmp / "Codex Enhance Manager"
            app_root.mkdir()
            temp_dir = app_root / "temp"
            temp_dir.mkdir()

            class StubConfig:
                def get_all(self):
                    return {
                        "temp_dir": str(temp_dir),
                        "diagnostics_dir": str(app_root / "diagnostics"),
                        "exports_dir": str(app_root / "exports"),
                    }

            with patch("app.app_data_dir", return_value=app_root):
                targets = _cleanup_targets(StubConfig())

            temp = next(target for target in targets if target["id"] == "temp")
            self.assertTrue(temp["safe"])
            self.assertEqual(temp["description"], "Temporary app files")
            self.assertIn("short-lived", temp["effect"])


if __name__ == "__main__":
    unittest.main()
