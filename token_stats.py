"""
token_stats.py - Token 统计查询引擎
基于 threads 表的 tokens_used 字段提供统计查询
"""
import sqlite3
import os
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone


class TokenStats:
    """Token 统计查询引擎"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """建立数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _check_column(self, conn: sqlite3.Connection, column: str) -> bool:
        """检查表中是否有指定列"""
        cur = conn.execute("PRAGMA table_info(threads)")
        columns = {row[1] for row in cur.fetchall()}
        return column in columns

    def _list_tables(self, conn: sqlite3.Connection) -> List[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> List[str]:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [str(row[1]) for row in rows]

    def _parse_datetime_to_unix(self, value: Optional[str], end_of_day: bool = False) -> Optional[int]:
        """将前端日期/时间字符串解析为 Unix 秒级时间戳。"""
        if not value:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        try:
            if normalized.isdigit():
                number = int(normalized)
                if number > 10_000_000_000:
                    return int(number / 1000)
                return number

            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"

            if "T" in normalized or ":" in normalized:
                parsed = datetime.fromisoformat(normalized)
            else:
                parsed = datetime.fromisoformat(normalized)
                if end_of_day:
                    parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)

            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return int(parsed.timestamp())
        except (TypeError, ValueError):
            return None

    def _build_time_filter(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Tuple[str, List[Any], Optional[int], Optional[int]]:
        """根据 start/end 构建 updated_at 过滤 SQL。"""
        where_parts: List[str] = []
        params: List[Any] = []
        start_ts = self._parse_datetime_to_unix(start, end_of_day=False)
        end_ts = self._parse_datetime_to_unix(end, end_of_day=True)

        if start_ts is not None:
            where_parts.append("updated_at >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where_parts.append("updated_at <= ?")
            params.append(end_ts)

        where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""
        return where_sql, params, start_ts, end_ts

    def get_current_total(self) -> int:
        """获取当前所有会话的 Token 使用总量。"""
        data = self.get_current_stats()
        return int(data.get("total_tokens", 0) or 0)

    def get_current_stats(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        granularity: str = "total",
    ) -> Dict[str, Any]:
        """获取轻量当前统计，支持按 updated_at 过滤和可选趋势桶。"""
        result: Dict[str, Any] = {
            "total_tokens": 0,
            "session_count": 0,
            "start_ts": None,
            "end_ts": None,
            "granularity": granularity,
            "buckets": [],
            "data_source": "codex_db",
            "realtime_note": "Codex DB 统计实时性取决于 Codex 写入 state_5.sqlite 的频率。",
            "cache_supported": False,
            "cache_note": "Codex 原生数据库不保存缓存读写字段；缓存统计需要配置代理缓存数据库，官方 API 和自定义 API 只要经过代理都可统计。",
        }

        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_updated = self._check_column(conn, "updated_at")
                if not has_tokens:
                    result["warning"] = "threads 表未发现 tokens_used 字段。"
                    return result

                where_sql = ""
                params: List[Any] = []
                start_ts: Optional[int] = None
                end_ts: Optional[int] = None
                if has_updated:
                    where_sql, params, start_ts, end_ts = self._build_time_filter(start, end)
                elif start or end:
                    result["warning"] = "threads 表未发现 updated_at 字段，无法按时间段过滤。"

                row = conn.execute(
                    f"SELECT COUNT(*) AS cnt, COALESCE(SUM(tokens_used), 0) AS total FROM threads{where_sql}",
                    params,
                ).fetchone()
                result["session_count"] = int(row["cnt"] or 0)
                result["total_tokens"] = int(row["total"] or 0)
                result["start_ts"] = start_ts
                result["end_ts"] = end_ts

                if has_updated and granularity in {"hour", "day"}:
                    time_expr = "strftime('%Y-%m-%d %H:00', updated_at, 'unixepoch', 'localtime')"
                    if granularity == "day":
                        time_expr = "date(updated_at, 'unixepoch', 'localtime')"
                    bucket_rows = conn.execute(
                        f"SELECT {time_expr} AS bucket, COUNT(*) AS cnt, "
                        f"COALESCE(SUM(tokens_used), 0) AS tokens FROM threads{where_sql} "
                        f"GROUP BY bucket ORDER BY bucket",
                        params,
                    ).fetchall()
                    result["buckets"] = [
                        {
                            "bucket": row["bucket"] or "",
                            "sessions": int(row["cnt"] or 0),
                            "tokens": int(row["tokens"] or 0),
                        }
                        for row in bucket_rows
                    ]
            finally:
                conn.close()
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def get_cc_switch_cache_stats(
        self,
        cc_switch_db_path: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Best-effort proxy cache-token statistics.

        Proxy databases can observe cache fields for both official and custom APIs
        when requests are routed through its proxy. Schemas differ by version,
        so this scans tables for cache/token columns instead of hard-coding one
        table layout.
        """
        result: Dict[str, Any] = {
            "cache_supported": False,
            "cache_total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_tables": [],
            "cache_note": "",
        }

        if not cc_switch_db_path:
            result["cache_note"] = (
                "未配置代理缓存数据库；无法读取代理层缓存统计。"
            )
            return result

        if not os.path.exists(cc_switch_db_path):
            result["cache_note"] = f"代理缓存数据库不存在: {cc_switch_db_path}"
            return result

        start_ts = self._parse_datetime_to_unix(start, end_of_day=False)
        end_ts = self._parse_datetime_to_unix(end, end_of_day=True)

        try:
            conn = sqlite3.connect(cc_switch_db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                for table in self._list_tables(conn):
                    columns = self._table_columns(conn, table)
                    cache_cols = [
                        col for col in columns
                        if "cache" in col.lower() and "token" in col.lower()
                    ]
                    if not cache_cols:
                        continue

                    time_col = _pick_time_column(columns)
                    where_sql, params = _build_cc_time_filter(time_col, start_ts, end_ts)
                    select_parts = []
                    for col in cache_cols:
                        alias = _safe_alias(col)
                        select_parts.append(f'COALESCE(SUM(CAST("{col}" AS INTEGER)), 0) AS "{alias}"')
                    row = conn.execute(
                        f'SELECT {", ".join(select_parts)} FROM "{table}"{where_sql}',
                        params,
                    ).fetchone()

                    table_total = 0
                    table_read = 0
                    table_creation = 0
                    col_values: Dict[str, int] = {}
                    for col in cache_cols:
                        value = int(row[_safe_alias(col)] or 0)
                        col_values[col] = value
                        table_total += value
                        lower = col.lower()
                        if "read" in lower or "hit" in lower:
                            table_read += value
                        if "creation" in lower or "create" in lower or "write" in lower:
                            table_creation += value

                    if table_total <= 0 and not col_values:
                        continue

                    result["cache_supported"] = True
                    result["cache_total_tokens"] += table_total
                    result["cache_read_tokens"] += table_read
                    result["cache_creation_tokens"] += table_creation
                    result["cache_tables"].append({
                        "table": table,
                        "time_column": time_col or "",
                        "columns": col_values,
                    })
            finally:
                conn.close()
        except Exception as exc:
            result["cache_note"] = f"读取代理缓存统计失败: {exc}"
            return result

        if result["cache_supported"]:
            result["cache_note"] = (
                "缓存统计来自代理缓存数据库；官方 API 和自定义 API 只要经过代理，都可以统计缓存读写。"
            )
        else:
            result["cache_note"] = (
                "已配置代理缓存数据库，但未找到 cache/token 字段；请确认数据库版本或表结构。"
            )
        return result

    def get_overview(self) -> Dict:
        """总览：总会话数、总 Token、时间范围、今日/本周/本月使用量"""
        result = {
            "total_sessions": 0,
            "total_tokens": 0,
            "earliest_date": "",
            "latest_date": "",
            "today_sessions": 0,
            "today_tokens": 0,
            "week_sessions": 0,
            "week_tokens": 0,
            "month_sessions": 0,
            "month_tokens": 0,
        }

        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_updated = self._check_column(conn, "updated_at")

                # 基本统计
                token_col = "tokens_used" if has_tokens else "0"
                cur = conn.execute(f"SELECT COUNT(*), COALESCE(SUM({token_col}), 0) FROM threads")
                row = cur.fetchone()
                result["total_sessions"] = row[0]
                result["total_tokens"] = row[1] or 0

                # 时间范围
                if has_updated:
                    cur = conn.execute(
                        "SELECT MIN(updated_at), MAX(updated_at) FROM threads WHERE updated_at > 0"
                    )
                    row = cur.fetchone()
                    if row[0] and row[1]:
                        try:
                            earliest = datetime.fromtimestamp(row[0])
                            latest = datetime.fromtimestamp(row[1])
                            result["earliest_date"] = earliest.strftime("%Y-%m-%d")
                            result["latest_date"] = latest.strftime("%Y-%m-%d")
                        except Exception:
                            pass

                    # 今日
                    today_start = int(datetime.now().replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp())
                    cur = conn.execute(
                        f"SELECT COUNT(*), COALESCE(SUM({token_col}), 0) FROM threads WHERE updated_at >= ?",
                        (today_start,)
                    )
                    row = cur.fetchone()
                    result["today_sessions"] = row[0]
                    result["today_tokens"] = row[1] or 0

                    # 本周
                    week_start = int((datetime.now() - timedelta(days=datetime.now().weekday())).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp())
                    cur = conn.execute(
                        f"SELECT COUNT(*), COALESCE(SUM({token_col}), 0) FROM threads WHERE updated_at >= ?",
                        (week_start,)
                    )
                    row = cur.fetchone()
                    result["week_sessions"] = row[0]
                    result["week_tokens"] = row[1] or 0

                    # 本月
                    month_start = int(datetime.now().replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0
                    ).timestamp())
                    cur = conn.execute(
                        f"SELECT COUNT(*), COALESCE(SUM({token_col}), 0) FROM threads WHERE updated_at >= ?",
                        (month_start,)
                    )
                    row = cur.fetchone()
                    result["month_sessions"] = row[0]
                    result["month_tokens"] = row[1] or 0

            finally:
                conn.close()
        except Exception as e:
            result["error"] = str(e)

        return result

    def get_by_model(self) -> List[Dict]:
        """按模型分组：model, count, tokens"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_model = self._check_column(conn, "model")
                if not has_model:
                    return []

                token_col = "tokens_used" if has_tokens else "0"
                cur = conn.execute(
                    f"SELECT model, COUNT(*) as cnt, COALESCE(SUM({token_col}), 0) as tokens "
                    f"FROM threads GROUP BY model ORDER BY tokens DESC"
                )
                for row in cur.fetchall():
                    results.append({
                        "model": row["model"] or "unknown",
                        "count": row["cnt"],
                        "tokens": row["tokens"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_by_provider(self) -> List[Dict]:
        """按提供商分组：provider, count, tokens"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_provider = self._check_column(conn, "model_provider")
                if not has_provider:
                    return []

                token_col = "tokens_used" if has_tokens else "0"
                cur = conn.execute(
                    f"SELECT model_provider, COUNT(*) as cnt, COALESCE(SUM({token_col}), 0) as tokens "
                    f"FROM threads GROUP BY model_provider ORDER BY tokens DESC"
                )
                for row in cur.fetchall():
                    results.append({
                        "provider": row["model_provider"] or "unknown",
                        "count": row["cnt"],
                        "tokens": row["tokens"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_daily_trend(self, days: int = 30) -> List[Dict]:
        """每日趋势：date, sessions, tokens"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_updated = self._check_column(conn, "updated_at")
                if not has_updated:
                    return []

                token_col = "tokens_used" if has_tokens else "0"
                since = int((datetime.now() - timedelta(days=days)).timestamp())
                cur = conn.execute(
                    f"SELECT date(updated_at, 'unixepoch', 'localtime') as day, "
                    f"COUNT(*) as cnt, COALESCE(SUM({token_col}), 0) as tokens "
                    f"FROM threads WHERE updated_at > ? GROUP BY day ORDER BY day",
                    (since,)
                )
                for row in cur.fetchall():
                    results.append({
                        "date": row["day"],
                        "sessions": row["cnt"],
                        "tokens": row["tokens"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_top_sessions(self, limit: int = 20) -> List[Dict]:
        """最耗 Token 的会话排行"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                if not has_tokens:
                    return []

                cur = conn.execute(
                    "SELECT title, model, tokens_used, updated_at, id "
                    "FROM threads WHERE tokens_used > 0 "
                    "ORDER BY tokens_used DESC LIMIT ?",
                    (limit,)
                )
                for row in cur.fetchall():
                    results.append({
                        "title": row["title"] or "(无标题)",
                        "model": row["model"] or "",
                        "tokens": row["tokens_used"],
                        "updated_at": row["updated_at"],
                        "id": row["id"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_by_source(self) -> List[Dict]:
        """按来源分组（user/vscode/subagent）"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_source = self._check_column(conn, "source")
                if not has_source:
                    return []

                token_col = "tokens_used" if has_tokens else "0"
                cur = conn.execute(
                    f"SELECT source, COUNT(*) as cnt, COALESCE(SUM({token_col}), 0) as tokens "
                    f"FROM threads GROUP BY source ORDER BY tokens DESC"
                )
                for row in cur.fetchall():
                    results.append({
                        "source": row["source"] or "unknown",
                        "count": row["cnt"],
                        "tokens": row["tokens"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results

    def get_hourly_distribution(self) -> List[Dict]:
        """每小时使用分布（24h）"""
        results = []
        try:
            conn = self._connect()
            try:
                has_tokens = self._check_column(conn, "tokens_used")
                has_updated = self._check_column(conn, "updated_at")
                if not has_updated:
                    return []

                token_col = "tokens_used" if has_tokens else "0"
                cur = conn.execute(
                    f"SELECT strftime('%H', updated_at, 'unixepoch', 'localtime') as hour, "
                    f"COUNT(*) as cnt, COALESCE(SUM({token_col}), 0) as tokens "
                    f"FROM threads GROUP BY hour ORDER BY hour"
                )
                for row in cur.fetchall():
                    results.append({
                        "hour": row["hour"],
                        "sessions": row["cnt"],
                        "tokens": row["tokens"],
                    })
            finally:
                conn.close()
        except Exception as e:
            results.append({"error": str(e)})
        return results


def _pick_time_column(columns: List[str]) -> str:
    candidates = [
        "created_at", "createdAt", "timestamp", "time", "request_time",
        "started_at", "start_time", "created_at_ms", "updated_at",
    ]
    lowered = {col.lower(): col for col in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    for col in columns:
        lower = col.lower()
        if ("time" in lower or "date" in lower or lower.endswith("_at")) and "token" not in lower:
            return col
    return ""


def _build_cc_time_filter(time_col: str, start_ts: Optional[int], end_ts: Optional[int]) -> Tuple[str, List[Any]]:
    if not time_col or (start_ts is None and end_ts is None):
        return "", []

    clauses = []
    params: List[Any] = []
    col_expr = f'"{time_col}"'
    # Handles Unix seconds, Unix milliseconds, and ISO-like text timestamps.
    numeric_expr = f"CAST({col_expr} AS INTEGER)"
    text_expr = f"CAST({col_expr} AS TEXT)"
    is_numeric = f"({text_expr} NOT GLOB '*[^0-9]*' AND {text_expr} != '')"
    is_text = f"NOT {is_numeric}"
    iso_start = datetime.fromtimestamp(start_ts).isoformat() if start_ts is not None else ""
    iso_end = datetime.fromtimestamp(end_ts).isoformat() if end_ts is not None else ""
    if start_ts is not None:
        clauses.append(
            f"(({is_numeric} AND {numeric_expr} > 10000000000 AND {numeric_expr} >= ?) "
            f"OR ({is_numeric} AND {numeric_expr} <= 10000000000 AND {numeric_expr} >= ?) "
            f"OR ({is_text} AND {text_expr} >= ?))"
        )
        params.extend([start_ts * 1000, start_ts, iso_start])
    if end_ts is not None:
        clauses.append(
            f"(({is_numeric} AND {numeric_expr} > 10000000000 AND {numeric_expr} <= ?) "
            f"OR ({is_numeric} AND {numeric_expr} <= 10000000000 AND {numeric_expr} <= ?) "
            f"OR ({is_text} AND {text_expr} <= ?))"
        )
        params.extend([end_ts * 1000, end_ts, iso_end])
    return " WHERE " + " AND ".join(clauses), params


def _safe_alias(column: str) -> str:
    return "sum_" + "".join(ch if ch.isalnum() else "_" for ch in column)
