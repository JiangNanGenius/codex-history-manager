import os
import tempfile
import time
import unittest
from pathlib import Path

from backup import BackupManager


class _Config:
    def __init__(self, backup_dir: str, max_backups: int = 2):
        self.backup_dir = backup_dir
        self.max_backups = max_backups

    def get(self, key, default=None):
        if key == "backup_dir":
            return self.backup_dir
        if key == "max_backups":
            return self.max_backups
        return default


class BackupManagerPruneTest(unittest.TestCase):
    def test_prune_backups_removes_oldest_zip_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / "backups"
            backup_dir.mkdir()
            files = [
                backup_dir / "codex_backup_manual_20260101_000000.zip",
                backup_dir / "codex_incremental_20260102_000000.zip",
                backup_dir / "codex_backup_manual_20260103_000000.zip",
            ]
            base_time = time.time() - 100
            for idx, path in enumerate(files):
                path.write_text("zip-placeholder", encoding="utf-8")
                os.utime(path, (base_time + idx, base_time + idx))

            manager = BackupManager(_Config(str(backup_dir), max_backups=2), db=None)
            result = manager.prune_backups()

            self.assertTrue(result["success"])
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(files[0].exists())
            self.assertTrue(files[1].exists())
            self.assertTrue(files[2].exists())


if __name__ == "__main__":
    unittest.main()
