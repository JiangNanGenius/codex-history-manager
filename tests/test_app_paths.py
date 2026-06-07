import tempfile
import unittest
from pathlib import Path

from app_paths import is_within


class AppPathsTest(unittest.TestCase):
    def test_is_within_accepts_child_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child = root / "a" / "b"
            child.mkdir(parents=True)

            self.assertTrue(is_within(child, root))

    def test_is_within_rejects_sibling_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            sibling = Path(tmpdir) / "sibling"
            root.mkdir()
            sibling.mkdir()

            self.assertFalse(is_within(sibling, root))


if __name__ == "__main__":
    unittest.main()
