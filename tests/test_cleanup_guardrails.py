import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import _cleanup_target, _uninstall_cleanup_targets


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


if __name__ == "__main__":
    unittest.main()
