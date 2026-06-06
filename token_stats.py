"""
token_stats.py - Token 统计查询引擎
基于 threads 表的 tokens_used 字段提供统计查询
"""
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime, timedelta


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
