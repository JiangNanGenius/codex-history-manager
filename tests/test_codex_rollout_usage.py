import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import (
    _attach_usage_source_summary,
    _merge_cache_usage_sources,
    _merge_local_proxy_request_log_usage,
    _official_usage_visible_for_current_mode,
    _provider_focus_is_official_login,
)
from codex_rollout_usage import (
    discover_rollout_paths,
    get_codex_rollout_cache_stats,
    parse_token_usage_from_payload,
    read_rollout_usage,
)


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class CodexRolloutUsageTest(unittest.TestCase):
    def test_provider_focus_is_official_login_for_switch_only_entry(self):
        self.assertTrue(_provider_focus_is_official_login({
            "focus_provider_id": "codex_official",
            "providers": [],
        }))
        self.assertTrue(_provider_focus_is_official_login({
            "focus_provider_id": "official",
            "providers": [{
                "id": "official",
                "switch_only": True,
                "codex_login": True,
            }],
        }))
        self.assertFalse(_provider_focus_is_official_login({
            "focus_provider_id": "third-party",
            "providers": [{"id": "third-party", "switch_only": False}],
        }))

    def test_official_usage_visible_only_for_official_current_mode(self):
        official_payload = {
            "focus_provider_id": "codex_official",
            "providers": [],
        }
        third_party_payload = {
            "focus_provider_id": "third-party",
            "providers": [{
                "id": "third-party",
                "enabled": True,
                "base_url": "https://api.example.test/v1",
                "api_key": "redacted",
                "auth_mode": "provider_api_key",
            }],
        }

        self.assertTrue(_official_usage_visible_for_current_mode("official_oauth", official_payload))
        self.assertTrue(_official_usage_visible_for_current_mode(
            "official_oauth",
            {"focus_provider_id": "", "providers": []},
            last_start_mode="official_direct",
        ))
        self.assertTrue(_official_usage_visible_for_current_mode(
            "official_oauth",
            third_party_payload,
            last_start_mode="official_direct",
        ))
        self.assertFalse(_official_usage_visible_for_current_mode("official_oauth", third_party_payload))
        self.assertFalse(_official_usage_visible_for_current_mode(
            "official_oauth",
            {"focus_provider_id": "", "providers": []},
            last_start_mode="preserve_login_proxy",
        ))
        self.assertFalse(_official_usage_visible_for_current_mode(
            "official_oauth",
            official_payload,
            last_start_mode="preserve_login_proxy",
        ))

    def test_reads_codex_info_last_usage_cached_input_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(path, [
                {"type": "response_item", "payload": {"role": "user", "content": "ignored"}},
                {
                    "timestamp": "2026-06-07T01:00:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 999,
                                "cached_input_tokens": 888,
                                "output_tokens": 777,
                                "total_tokens": 1776,
                            },
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 30,
                                "output_tokens": 10,
                                "reasoning_output_tokens": 2,
                                "total_tokens": 110,
                            },
                            "model_context_window": 258400,
                        },
                    },
                },
                {
                    "timestamp": "2026-06-07T01:01:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 20,
                                "cached_input_tokens": 5,
                                "output_tokens": 3,
                                "reasoning_output_tokens": 1,
                                "total_tokens": 23,
                            }
                        },
                    },
                },
            ])

            stats = read_rollout_usage(str(path))

            self.assertTrue(stats["cache_supported"])
            self.assertEqual(stats["events_seen"], 3)
            self.assertEqual(stats["token_count_events"], 2)
            self.assertEqual(stats["input_tokens"], 120)
            self.assertEqual(stats["output_tokens"], 13)
            self.assertEqual(stats["reasoning_tokens"], 3)
            self.assertEqual(stats["total_tokens"], 133)
            self.assertEqual(stats["cache_read_tokens"], 35)
            self.assertEqual(stats["cache_creation_tokens"], 0)
            self.assertEqual(stats["cache_total_tokens"], 35)

    def test_normalizes_cache_field_aliases(self):
        cases = [
            (
                {
                    "type": "token_count",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "input_tokens_details": {"cached_tokens": 7},
                    },
                },
                7,
                0,
            ),
            (
                {
                    "type": "token_count",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "prompt_tokens_details": {"cached_tokens": 4},
                    },
                },
                4,
                0,
            ),
            (
                {
                    "type": "token_count",
                    "last_token_usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 3,
                        "cache_creation_input_tokens": 2,
                    },
                },
                3,
                2,
            ),
            (
                {
                    "type": "token_count",
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 12,
                        "cachedContentTokenCount": 6,
                    },
                },
                6,
                0,
            ),
            (
                {
                    "type": "token_count",
                    "lastTokenUsage": {
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "totalTokens": 120,
                        "cachedInputTokens": 45,
                        "cacheCreationInputTokens": 5,
                    },
                    "modelContextWindow": 200000,
                },
                45,
                5,
            ),
        ]

        for payload, expected_read, expected_creation in cases:
            with self.subTest(payload=payload):
                usage = parse_token_usage_from_payload(payload)
                self.assertEqual(usage["cache_read_tokens"], expected_read)
                self.assertEqual(usage["cache_creation_tokens"], expected_creation)
                self.assertTrue(usage["cache_field_seen"])

    def test_reads_camel_case_usage_and_context(self):
        usage = parse_token_usage_from_payload({
            "type": "token_count",
            "info": {
                "lastTokenUsage": {
                    "inputTokens": 32,
                    "outputTokens": 8,
                    "totalTokens": 40,
                    "cachedInputTokens": 12,
                },
                "modelContextWindow": 128000,
            },
        })

        self.assertEqual(usage["input_tokens"], 32)
        self.assertEqual(usage["output_tokens"], 8)
        self.assertEqual(usage["total_tokens"], 40)
        self.assertEqual(usage["cache_read_tokens"], 12)
        self.assertEqual(usage["context_window"], 128000)
        self.assertEqual(usage["context_used_tokens"], 40)

    def test_applies_time_filter_to_token_count_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(path, [
                {
                    "timestamp": "2026-06-06T23:59:59Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "last_token_usage": {"cached_input_tokens": 50}},
                },
                {
                    "timestamp": "2026-06-07T12:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "last_token_usage": {"cached_input_tokens": 9}},
                },
            ])

            stats = read_rollout_usage(str(path), start="2026-06-07", end="2026-06-07")

            self.assertEqual(stats["token_count_events"], 1)
            self.assertEqual(stats["cache_read_tokens"], 9)

    def test_discovers_rollout_paths_from_threads_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            rollout = tmp / "rollout.jsonl"
            rollout.write_text("", encoding="utf-8")
            db_path = tmp / "state.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at INTEGER)")
                conn.execute(
                    "INSERT INTO threads (id, rollout_path, updated_at) VALUES (?, ?, ?)",
                    ("thread-1", str(rollout), 1),
                )
                conn.execute(
                    "INSERT INTO threads (id, rollout_path, updated_at) VALUES (?, ?, ?)",
                    ("thread-missing", str(tmp / "missing.jsonl"), 2),
                )
                conn.commit()
            finally:
                conn.close()

            paths = discover_rollout_paths(db_path=str(db_path), sessions_dir=str(tmp))

            self.assertEqual(paths, [str(rollout.resolve())])

    def test_discovers_rollout_paths_with_updated_at_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            old_rollout = tmp / "old.jsonl"
            new_rollout = tmp / "new.jsonl"
            old_rollout.write_text("", encoding="utf-8")
            new_rollout.write_text("", encoding="utf-8")
            db_path = tmp / "state.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at INTEGER)")
                conn.execute(
                    "INSERT INTO threads (id, rollout_path, updated_at) VALUES (?, ?, ?)",
                    ("old", str(old_rollout), 1_780_000_000),
                )
                conn.execute(
                    "INSERT INTO threads (id, rollout_path, updated_at) VALUES (?, ?, ?)",
                    ("new", str(new_rollout), 1_780_086_400),
                )
                conn.commit()
            finally:
                conn.close()

            paths = discover_rollout_paths(
                db_path=str(db_path),
                start=str(1_780_086_400),
                end=str(1_780_086_400),
            )

            self.assertEqual(paths, [str(new_rollout.resolve())])

    def test_get_codex_rollout_cache_stats_uses_sessions_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(path, [
                {
                    "timestamp": "2026-06-07T12:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "last_token_usage": {"cached_input_tokens": 11}},
                }
            ])

            stats = get_codex_rollout_cache_stats(sessions_dir=tmpdir)

            self.assertTrue(stats["cache_supported"])
            self.assertEqual(stats["rollout_paths_discovered"], 1)
            self.assertEqual(stats["cache_read_tokens"], 11)

    def test_tail_scan_reads_recent_token_count_without_full_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rollout.jsonl"
            path.write_text(
                json.dumps({
                    "timestamp": "2026-06-07T11:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "last_token_usage": {"cached_input_tokens": 99}},
                }) + "\n" +
                ("x" * 5000) + "\n" +
                json.dumps({
                    "timestamp": "2026-06-07T12:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "last_token_usage": {"cached_input_tokens": 7}},
                }) + "\n",
                encoding="utf-8",
            )

            stats = read_rollout_usage(str(path), tail_bytes=1024)

            self.assertTrue(stats["cache_supported"])
            self.assertEqual(stats["cache_read_tokens"], 7)

    def test_merge_cache_usage_sources_keeps_source_fields(self):
        data = {}
        _merge_cache_usage_sources(
            data,
            {
                "cache_supported": True,
                "cache_read_tokens": 10,
                "cache_creation_tokens": 2,
                "rollout_files_scanned": 1,
                "rollout_paths_discovered": 1,
                "rollout_token_count_events": 3,
                "rollout_cache_field_events": 3,
                "cache_note": "rollout note",
            },
            {
                "cache_supported": True,
                "cache_read_tokens": 4,
                "cache_creation_tokens": 1,
                "cache_tables": [{"table": "proxy_request_logs"}],
                "cache_note": "cc note",
                "cache_strategy": "usage_daily_rollups",
                "cache_rollup_used": True,
            },
        )

        self.assertTrue(data["cache_supported"])
        self.assertFalse(data["codex_rollout_usage_supported"])
        self.assertEqual(data["cache_read_tokens"], 10)
        self.assertEqual(data["cache_creation_tokens"], 2)
        self.assertEqual(data["cache_total_tokens"], 12)
        self.assertEqual(data["codex_rollout_cache_total_tokens"], 12)
        self.assertEqual(data["cc_switch_cache_total_tokens"], 5)
        self.assertEqual(data["cache_sources"], ["codex_rollout", "cc_switch_db"])
        self.assertTrue(data["cache_overlap_risk"])
        self.assertEqual(data["cache_merge_strategy"], "codex_rollout_primary_cc_switch_separate")
        self.assertEqual(data["cache_tables"], [{"table": "proxy_request_logs"}])
        self.assertEqual(data["cc_switch_cache_strategy"], "usage_daily_rollups")
        self.assertTrue(data["cc_switch_cache_rollup_used"])

    def test_merge_cache_usage_sources_uses_rollout_total_as_source_of_truth(self):
        data = {"total_tokens": 999, "data_source": "codex_db"}
        _merge_cache_usage_sources(
            data,
            {
                "cache_supported": True,
                "input_tokens": 100,
                "output_tokens": 25,
                "total_tokens": 125,
                "rollout_token_count_events": 2,
                "latest_context_window": 200000,
                "latest_context_used_tokens": 125,
            },
            None,
            use_rollout_total=True,
        )

        self.assertEqual(data["codex_db_total_tokens"], 999)
        self.assertTrue(data["codex_rollout_usage_supported"])
        self.assertEqual(data["total_tokens"], 125)
        self.assertEqual(data["input_tokens"], 100)
        self.assertEqual(data["output_tokens"], 25)
        self.assertEqual(data["data_source"], "codex_rollout")
        self.assertEqual(data["codex_rollout_latest_context_window"], 200000)

    def test_merge_cache_usage_sources_uses_cc_switch_when_rollout_missing(self):
        data = {}
        _merge_cache_usage_sources(
            data,
            {"cache_supported": False},
            {
                "cache_supported": True,
                "cache_read_tokens": 4,
                "cache_creation_tokens": 1,
            },
        )

        self.assertEqual(data["cache_read_tokens"], 4)
        self.assertEqual(data["cache_creation_tokens"], 1)
        self.assertEqual(data["cache_total_tokens"], 5)
        self.assertEqual(data["cache_sources"], ["cc_switch_db"])
        self.assertFalse(data["cache_overlap_risk"])
        self.assertEqual(data["cache_merge_strategy"], "cc_switch_db")

    def test_merge_local_proxy_request_log_usage_fills_empty_totals(self):
        data = {"total_tokens": 0, "cache_supported": False, "cache_sources": []}

        _merge_local_proxy_request_log_usage(data, {
            "exists": True,
            "count": 2,
            "success_count": 1,
            "error_count": 1,
            "latest_timestamp": "2026-06-07T12:00:00Z",
            "tokens": {
                "input_tokens": 30,
                "output_tokens": 7,
                "total_tokens": 37,
                "reasoning_tokens": 3,
                "cache_read_tokens": 5,
                "cache_creation_tokens": 2,
            },
        })

        self.assertEqual(data["data_source"], "local_proxy_request_log")
        self.assertEqual(data["total_tokens"], 37)
        self.assertEqual(data["input_tokens"], 30)
        self.assertEqual(data["output_tokens"], 7)
        self.assertEqual(data["reasoning_tokens"], 3)
        self.assertEqual(data["cache_read_tokens"], 5)
        self.assertEqual(data["cache_creation_tokens"], 2)
        self.assertEqual(data["cache_total_tokens"], 7)
        self.assertEqual(data["cache_merge_strategy"], "local_proxy_request_log")
        self.assertEqual(data["cache_sources"], ["local_proxy_request_log"])

    def test_merge_local_proxy_request_log_usage_does_not_override_existing_total(self):
        data = {"total_tokens": 99, "data_source": "codex_db", "cache_supported": True, "cache_sources": ["codex_rollout"]}

        _merge_local_proxy_request_log_usage(data, {
            "exists": True,
            "count": 1,
            "success_count": 1,
            "tokens": {
                "input_tokens": 30,
                "output_tokens": 7,
                "total_tokens": 37,
                "cache_read_tokens": 5,
            },
        })

        self.assertEqual(data["data_source"], "codex_db")
        self.assertEqual(data["total_tokens"], 99)
        self.assertEqual(data["cache_sources"], ["codex_rollout", "local_proxy_request_log"])
        self.assertTrue(data["cache_overlap_risk"])

    def test_attach_usage_source_summary_adds_badges_and_tooltips(self):
        data = {
            "cc_switch_db_configured": True,
            "codex_rollout_cache_supported": True,
            "codex_rollout_paths_discovered": 5,
            "codex_rollout_files_scanned": 2,
            "cc_switch_cache_supported": False,
            "cc_switch_cache_note": "configured but no cache columns",
        }

        _attach_usage_source_summary(data, {"running": True})

        badges = {badge["id"]: badge for badge in data["usage_source_badges"]}
        self.assertEqual(set(badges), {"codex_db", "codex_rollout", "local_proxy", "cc_switch_db"})
        self.assertTrue(badges["codex_db"]["active"])
        self.assertIn("compatibility fallback", badges["codex_db"]["tooltip"])
        self.assertIn("cache read/write details require", badges["codex_db"]["tooltip"])
        self.assertTrue(badges["codex_rollout"]["active"])
        self.assertEqual(badges["local_proxy"]["status"], "running")
        self.assertEqual(badges["cc_switch_db"]["status"], "configured")

    def test_attach_usage_source_summary_uses_request_log_aggregate(self):
        data = {"cc_switch_db_configured": False}

        _attach_usage_source_summary(data, {"running": False}, {
            "exists": True,
            "count": 3,
            "success_count": 2,
            "error_count": 1,
            "tokens": {"total_tokens": 88},
        })

        badges = {badge["id"]: badge for badge in data["usage_source_badges"]}
        self.assertEqual(badges["local_proxy"]["status"], "active")
        self.assertTrue(badges["local_proxy"]["active"])
        self.assertIn("3 routed requests", badges["local_proxy"]["tooltip"])
        self.assertIn("88 tokens", badges["local_proxy"]["tooltip"])


if __name__ == "__main__":
    unittest.main()
