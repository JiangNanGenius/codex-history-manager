"""Read cache token usage from Codex rollout/jsonl files.

The Codex DB `threads.tokens_used` value is useful for totals, but it does not
store cache read/write details. Codex rollout files emit `event_msg` records
whose payload type is `token_count`; recent files place request-level usage at
`payload.info.last_token_usage`.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from reader import iter_jsonl_lines


READ_CACHE_KEYS = (
    "cached_input_tokens",
    "cache_read_input_tokens",
    "cache_read_tokens",
    "cached_tokens",
    "cachedContentTokenCount",
)
WRITE_CACHE_KEYS = (
    "cache_creation_input_tokens",
    "cache_creation_tokens",
    "cache_write_tokens",
    "cache_written_tokens",
    "cache_create_input_tokens",
)
INPUT_KEYS = ("input_tokens", "prompt_tokens", "promptTokenCount")
OUTPUT_KEYS = ("output_tokens", "completion_tokens", "candidatesTokenCount")
TOTAL_KEYS = ("total_tokens", "totalTokenCount")
REASONING_KEYS = ("reasoning_output_tokens", "reasoning_tokens")
DETAIL_KEYS = ("input_tokens_details", "prompt_tokens_details")


def parse_token_usage_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one rollout `token_count` payload into token fields."""
    result = _empty_usage()
    usage = _pick_usage_object(payload)
    if not usage:
        return result

    result["input_tokens"] = _first_int(usage, INPUT_KEYS)
    result["output_tokens"] = _first_int(usage, OUTPUT_KEYS)
    result["reasoning_tokens"] = _first_int(usage, REASONING_KEYS)
    result["total_tokens"] = _first_int(usage, TOTAL_KEYS)
    if result["total_tokens"] <= 0:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]

    cache_read, read_seen = _cache_read_tokens(usage)
    cache_creation, creation_seen = _cache_creation_tokens(usage)
    result["cache_read_tokens"] = cache_read
    result["cache_creation_tokens"] = cache_creation
    result["cache_total_tokens"] = cache_read + cache_creation
    result["cache_field_seen"] = read_seen or creation_seen
    return result


def read_rollout_usage(
    path: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Read cache usage from a single rollout/jsonl file."""
    result = _empty_rollout_result(path)
    if not path or not os.path.exists(path):
        result["error"] = f"rollout file does not exist: {path}"
        return result

    start_ts = _parse_datetime_to_unix(start, end_of_day=False)
    end_ts = _parse_datetime_to_unix(end, end_of_day=True)

    try:
        for obj in iter_jsonl_lines(path):
            result["events_seen"] += 1
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            event_ts = _event_timestamp(obj, payload)
            if not _within_time_filter(event_ts, start_ts, end_ts):
                continue

            usage = parse_token_usage_from_payload(payload)
            result["token_count_events"] += 1
            if usage["cache_field_seen"]:
                result["cache_field_events"] += 1
            _add_usage(result, usage)
    except Exception as exc:
        result["error"] = str(exc)

    result["cache_supported"] = result["cache_field_events"] > 0
    result["cache_total_tokens"] = result["cache_read_tokens"] + result["cache_creation_tokens"]
    return result


def read_rollout_usage_many(
    paths: Iterable[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate cache usage across many rollout files."""
    result = _empty_aggregate_result()
    seen: set[str] = set()

    for raw_path in paths:
        normalized = _normalize_path(raw_path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        file_result = read_rollout_usage(normalized, start=start, end=end)
        result["rollout_files_scanned"] += 1
        result["rollout_events_seen"] += int(file_result.get("events_seen", 0) or 0)
        result["rollout_token_count_events"] += int(file_result.get("token_count_events", 0) or 0)
        result["rollout_cache_field_events"] += int(file_result.get("cache_field_events", 0) or 0)
        _add_usage(result, file_result)

        source = {
            "path": normalized,
            "token_count_events": int(file_result.get("token_count_events", 0) or 0),
            "cache_read_tokens": int(file_result.get("cache_read_tokens", 0) or 0),
            "cache_creation_tokens": int(file_result.get("cache_creation_tokens", 0) or 0),
            "cache_total_tokens": int(file_result.get("cache_total_tokens", 0) or 0),
        }
        if file_result.get("error"):
            source["error"] = str(file_result["error"])
            result["errors"].append(str(file_result["error"]))
        result["rollout_usage_sources"].append(source)

    result["cache_supported"] = result["rollout_cache_field_events"] > 0
    result["cache_total_tokens"] = result["cache_read_tokens"] + result["cache_creation_tokens"]
    return result


def discover_rollout_paths(
    db_path: str = "",
    sessions_dir: str = "",
    limit: int = 5000,
) -> List[str]:
    """Find rollout/jsonl paths from Codex thread metadata and sessions dir."""
    paths: List[str] = []
    seen: set[str] = set()

    def add_path(value: Any) -> None:
        if len(paths) >= limit:
            return
        normalized = _normalize_path(value)
        if not normalized or normalized in seen or not os.path.exists(normalized):
            return
        seen.add(normalized)
        paths.append(normalized)

    for path in _rollout_paths_from_db(db_path, limit=limit):
        add_path(path)

    sessions_root = Path(sessions_dir).expanduser() if sessions_dir else None
    if not paths and sessions_root and sessions_root.exists():
        try:
            for path in sessions_root.rglob("*.jsonl"):
                add_path(str(path))
                if len(paths) >= limit:
                    break
        except OSError:
            pass

    return paths


def get_codex_rollout_cache_stats(
    db_path: str = "",
    sessions_dir: str = "",
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Return API-ready cache stats sourced from Codex rollout files."""
    paths = discover_rollout_paths(db_path=db_path, sessions_dir=sessions_dir, limit=limit)
    aggregate = read_rollout_usage_many(paths, start=start, end=end)
    aggregate["rollout_paths_discovered"] = len(paths)
    aggregate["cache_note"] = (
        "Codex rollout token_count events were scanned for cache read/write usage."
        if aggregate["cache_supported"]
        else "No Codex rollout cache read/write fields were found."
    )
    return aggregate


def _empty_usage() -> Dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_total_tokens": 0,
        "cache_field_seen": False,
    }


def _empty_rollout_result(path: str) -> Dict[str, Any]:
    result = _empty_usage()
    result.update({
        "path": path,
        "events_seen": 0,
        "token_count_events": 0,
        "cache_field_events": 0,
        "cache_supported": False,
        "error": "",
    })
    return result


def _empty_aggregate_result() -> Dict[str, Any]:
    result = _empty_usage()
    result.update({
        "cache_supported": False,
        "rollout_files_scanned": 0,
        "rollout_events_seen": 0,
        "rollout_token_count_events": 0,
        "rollout_cache_field_events": 0,
        "rollout_usage_sources": [],
        "errors": [],
    })
    return result


def _pick_usage_object(payload: Dict[str, Any]) -> Dict[str, Any]:
    info = payload.get("info")
    if isinstance(info, dict):
        last_usage = info.get("last_token_usage")
        if isinstance(last_usage, dict):
            return last_usage

    for key in ("last_token_usage", "usage", "token_usage", "usageMetadata"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    if isinstance(info, dict):
        total_usage = info.get("total_token_usage")
        if isinstance(total_usage, dict):
            return total_usage

    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        return response["usage"]

    return payload if _looks_like_usage(payload) else {}


def _looks_like_usage(value: Dict[str, Any]) -> bool:
    keys = set(value)
    known = set(READ_CACHE_KEYS + WRITE_CACHE_KEYS + INPUT_KEYS + OUTPUT_KEYS + TOTAL_KEYS)
    if keys & known:
        return True
    return any(isinstance(value.get(k), dict) for k in DETAIL_KEYS)


def _add_usage(target: Dict[str, Any], usage: Dict[str, Any]) -> None:
    for key in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
    ):
        target[key] = int(target.get(key, 0) or 0) + int(usage.get(key, 0) or 0)
    target["cache_total_tokens"] = int(target.get("cache_read_tokens", 0) or 0) + int(
        target.get("cache_creation_tokens", 0) or 0
    )


def _cache_read_tokens(usage: Dict[str, Any]) -> Tuple[int, bool]:
    values = [_int_value(usage.get(key)) for key in READ_CACHE_KEYS if key in usage]
    for details_key in DETAIL_KEYS:
        details = usage.get(details_key)
        if isinstance(details, dict):
            values.extend(_int_value(details.get(key)) for key in READ_CACHE_KEYS if key in details)
    values = [value for value in values if value > 0]
    return (max(values) if values else 0, bool(values))


def _cache_creation_tokens(usage: Dict[str, Any]) -> Tuple[int, bool]:
    values = [_int_value(usage.get(key)) for key in WRITE_CACHE_KEYS if key in usage]
    values = [value for value in values if value > 0]
    return (max(values) if values else 0, bool(values))


def _first_int(usage: Dict[str, Any], keys: Iterable[str]) -> int:
    for key in keys:
        value = _int_value(usage.get(key))
        if value > 0:
            return value
    return 0


def _int_value(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return max(0, int(float(stripped)))
        except ValueError:
            return 0
    return 0


def _event_timestamp(obj: Dict[str, Any], payload: Dict[str, Any]) -> Optional[int]:
    for value in (obj.get("timestamp"), payload.get("timestamp")):
        parsed = _parse_datetime_to_unix(value, end_of_day=False)
        if parsed is not None:
            return parsed
    return None


def _within_time_filter(
    event_ts: Optional[int],
    start_ts: Optional[int],
    end_ts: Optional[int],
) -> bool:
    if event_ts is None:
        return True
    if start_ts is not None and event_ts < start_ts:
        return False
    if end_ts is not None and event_ts > end_ts:
        return False
    return True


def _parse_datetime_to_unix(value: Optional[str], end_of_day: bool = False) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return int(number / 1000) if number > 10_000_000_000 else number
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    try:
        if normalized.isdigit():
            number = int(normalized)
            return int(number / 1000) if number > 10_000_000_000 else number
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if "T" not in normalized and ":" not in normalized and end_of_day:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _rollout_paths_from_db(db_path: str, limit: int = 5000) -> List[str]:
    if not db_path or not os.path.exists(db_path):
        return []

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
            if "rollout_path" not in columns:
                return []
            order_sql = " ORDER BY updated_at DESC" if "updated_at" in columns else ""
            rows = conn.execute(
                f"SELECT rollout_path FROM threads WHERE rollout_path IS NOT NULL "
                f"AND rollout_path != ''{order_sql} LIMIT ?",
                (limit,),
            ).fetchall()
            return [str(row[0]) for row in rows if row and row[0]]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _normalize_path(value: Any) -> str:
    if not value:
        return ""
    try:
        return str(Path(str(value)).expanduser().resolve())
    except OSError:
        return str(Path(str(value)).expanduser())
