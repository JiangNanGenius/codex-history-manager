import sqlite3
import tempfile
import unittest
from pathlib import Path

from token_stats import TokenStats


class TokenStatsCacheTest(unittest.TestCase):
    def test_cc_switch_cache_read_and_creation_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cc_switch.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE proxy_request_logs (
                        id INTEGER PRIMARY KEY,
                        created_at INTEGER,
                        cache_read_tokens INTEGER,
                        cache_creation_tokens INTEGER,
                        cache_total_tokens INTEGER
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO proxy_request_logs
                    (created_at, cache_read_tokens, cache_creation_tokens, cache_total_tokens)
                    VALUES (?, ?, ?, ?)
                    """,
                    (1_780_000_000, 10, 4, 14),
                )
                conn.commit()
            finally:
                conn.close()

            stats = TokenStats("").get_cc_switch_cache_stats(str(db_path))

            self.assertTrue(stats["cache_supported"])
            self.assertEqual(stats["cache_read_tokens"], 10)
            self.assertEqual(stats["cache_creation_tokens"], 4)
            self.assertEqual(stats["cache_total_tokens"], 14)
            self.assertEqual(stats["cache_tables"][0]["table"], "proxy_request_logs")
            self.assertEqual(stats["cache_tables"][0]["columns"]["cache_total_tokens"], 14)

    def test_cc_switch_cache_uses_total_when_read_write_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cc_switch.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE usage_daily_rollups (
                        day TEXT,
                        cache_total_tokens INTEGER
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO usage_daily_rollups (day, cache_total_tokens) VALUES (?, ?)",
                    ("2026-06-07", 21),
                )
                conn.commit()
            finally:
                conn.close()

            stats = TokenStats("").get_cc_switch_cache_stats(str(db_path))

            self.assertTrue(stats["cache_supported"])
            self.assertEqual(stats["cache_read_tokens"], 0)
            self.assertEqual(stats["cache_creation_tokens"], 0)
            self.assertEqual(stats["cache_total_tokens"], 21)

    def test_cc_switch_cache_applies_unix_time_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cc_switch.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE proxy_request_logs (
                        created_at INTEGER,
                        cache_read_tokens INTEGER,
                        cache_creation_tokens INTEGER
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO proxy_request_logs
                    (created_at, cache_read_tokens, cache_creation_tokens)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (1_780_000_000, 1, 1),
                        (1_780_086_400, 8, 2),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            stats = TokenStats("").get_cc_switch_cache_stats(
                str(db_path),
                start=str(1_780_086_400),
                end=str(1_780_086_400),
            )

            self.assertEqual(stats["cache_read_tokens"], 8)
            self.assertEqual(stats["cache_creation_tokens"], 2)
            self.assertEqual(stats["cache_total_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
