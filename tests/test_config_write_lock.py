import unittest

from config import Config


class ConfigWriteLockTest(unittest.TestCase):
    def make_config(self):
        cfg = Config.__new__(Config)
        cfg._data = {}
        cfg._write_locked = False
        cfg._write_lock_reason = ""
        return cfg

    def test_write_lock_blocks_update_without_mutating_memory(self):
        cfg = self.make_config()
        cfg.lock_writes("locked for uninstall")

        with self.assertRaises(RuntimeError):
            cfg.update({"db_path": "should-not-stick"})

        self.assertEqual(cfg._data, {})

    def test_write_lock_blocks_set_without_mutating_memory(self):
        cfg = self.make_config()
        cfg.lock_writes("locked for uninstall")

        with self.assertRaises(RuntimeError):
            cfg.set("db_path", "should-not-stick")

        self.assertEqual(cfg._data, {})

    def test_write_lock_state_reports_reason(self):
        cfg = self.make_config()
        cfg.lock_writes("restart required")

        self.assertTrue(cfg.is_write_locked())
        self.assertEqual(cfg.write_lock_reason(), "restart required")


if __name__ == "__main__":
    unittest.main()
