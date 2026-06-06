"""
db.py - 数据库操作层
负责读写 state_5.sqlite，封装所有 SQL 操作
Web 版增强：返回更多字段支持前端展示和 Token 统计
"""
import sqlite3
import os
from typing import List, Dict, Optional, Tuple


class CodexDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        """建立数据库连接"""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"数据库文件不存在: {self.db_path}")
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 开启 WAL 模式，减少锁冲突
        self._conn.execute("PRAGMA journal_mode=WAL")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self):
        if not self._conn:
            self.connect()

    def get_columns(self) -> List[str]:
        """获取 threads 表的列名"""
        self._ensure_connected()
        cur = self._conn.execute("PRAGMA table_info(threads)")
        return [row[1] for row in cur.fetchall()]

    def has_column(self, column: str) -> bool:
        """检查是否存在指定列"""
        return column in self.get_columns()

    def get_stats(self) -> Dict:
        """获取统计信息"""
        self._ensure_connected()
        stats = {}
        try:
            cur = self._conn.execute("SELECT COUNT(*) FROM threads")
            stats["total"] = cur.fetchone()[0]
            cur = self._conn.execute("SELECT COUNT(*) FROM threads WHERE archived=0")
            stats["active"] = cur.fetchone()[0]
            cur = self._conn.execute("SELECT COUNT(*) FROM threads WHERE archived=1")
            stats["archived"] = cur.fetchone()[0]

            if self.has_column("source"):
                cur = self._conn.execute("SELECT COUNT(*) FROM threads WHERE source='user'")
                stats["user_threads"] = cur.fetchone()[0]
            elif self.has_column("thread_source"):
                cur = self._conn.execute("SELECT COUNT(*) FROM threads WHERE thread_source='user'")
                stats["user_threads"] = cur.fetchone()[0]
            else:
                stats["user_threads"] = 0

            # Token 总计
            if self.has_column("tokens_used"):
                cur = self._conn.execute("SELECT COALESCE(SUM(tokens_used), 0) FROM threads")
                stats["total_tokens"] = cur.fetchone()[0]
            else:
                stats["total_tokens"] = 0

        except Exception as e:
            stats["error"] = str(e)
        return stats

    def list_threads(
        self,
        filter_mode: str = "all",
        search: str = "",
        page: int = 0,
        page_size: int = 50,
        source_filter: str = "all",
        sort_by: str = "created_at_ms",
        sort_order: str = "desc",
        model_filter: str = "",
        provider_filter: str = "",
    ) -> Tuple[List[Dict], int]:
        """
        获取会话列表（增强版，支持更多过滤和排序）
        filter_mode: all / active / archived
        source_filter: all / user / agent
        sort_by: created_at_ms / updated_at / tokens_used / title
        sort_order: asc / desc
        model_filter: 按模型筛选
        provider_filter: 按提供商筛选
        返回 (rows, total_count)
        """
        self._ensure_connected()
        conditions = []
        params = []

        if filter_mode == "active":
            conditions.append("archived=0")
        elif filter_mode == "archived":
            conditions.append("archived=1")

        # source 列名兼容
        source_col = "source" if self.has_column("source") else (
            "thread_source" if self.has_column("thread_source") else ""
        )
        if source_filter == "user" and source_col:
            conditions.append(f"{source_col}='user'")
        elif source_filter == "agent" and source_col:
            conditions.append(f"{source_col}='agent'")

        if model_filter and self.has_column("model"):
            conditions.append("model=?")
            params.append(model_filter)

        if provider_filter and self.has_column("model_provider"):
            conditions.append("model_provider=?")
            params.append(provider_filter)

        if search.strip():
            conditions.append("(title LIKE ? OR id LIKE ?)")
            like = f"%{search.strip()}%"
            params.extend([like, like])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # 获取总数
        count_sql = f"SELECT COUNT(*) FROM threads {where}"
        total = self._conn.execute(count_sql, params).fetchone()[0]

        # 安全排序字段白名单
        allowed_sort = {"created_at_ms", "updated_at", "tokens_used", "title", "created_at"}
        if sort_by not in allowed_sort:
            sort_by = "created_at_ms"
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        # 检查排序字段是否存在
        if sort_by != "created_at_ms" and not self.has_column(sort_by):
            sort_by = "created_at_ms"

        # 获取分页数据
        offset = page * page_size

        # 选择更多字段
        select_cols = [
            "id", "title", "archived",
            "rollout_path", "created_at", "updated_at",
        ]
        for col in ["created_at_ms", "tokens_used", "model", "model_provider", "source", "thread_source", "cwd"]:
            if self.has_column(col):
                select_cols.append(col)

        data_sql = f"""
            SELECT {', '.join(select_cols)}
            FROM threads {where}
            ORDER BY {sort_by} {sort_order.upper()}
            LIMIT ? OFFSET ?
        """
        cur = self._conn.execute(data_sql, params + [page_size, offset])
        rows = [dict(row) for row in cur.fetchall()]
        return rows, total

    def get_thread(self, thread_id: str) -> Optional[Dict]:
        """获取单条会话详情"""
        self._ensure_connected()
        cur = self._conn.execute(
            "SELECT * FROM threads WHERE id=?", (thread_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def set_archived(self, thread_id: str, archived: int) -> bool:
        """设置归档状态 (0=未归档, 1=已归档)"""
        self._ensure_connected()
        try:
            self._conn.execute(
                "UPDATE threads SET archived=? WHERE id=?",
                (archived, thread_id)
            )
            self._conn.commit()
            return True
        except Exception as e:
            print(f"设置归档失败: {e}")
            return False

    def delete_thread(self, thread_id: str) -> bool:
        """删除会话记录（从DB删除，不删文件）"""
        self._ensure_connected()
        try:
            self._conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))
            self._conn.commit()
            return True
        except Exception as e:
            print(f"删除会话失败: {e}")
            return False

    def get_threads_since(self, since_ts: str) -> List[Dict]:
        """获取某时间戳之后变更的所有 threads（用于增量备份）"""
        self._ensure_connected()
        cur = self._conn.execute(
            "SELECT * FROM threads WHERE updated_at > ? OR created_at > ?",
            (since_ts, since_ts)
        )
        return [dict(row) for row in cur.fetchall()]

    def search_full_text(self, keyword: str, limit: int = 100) -> List[Dict]:
        """全文搜索 title"""
        self._ensure_connected()
        cur = self._conn.execute(
            "SELECT id, title, archived, created_at FROM threads "
            "WHERE title LIKE ? ORDER BY created_at_ms DESC LIMIT ?",
            (f"%{keyword}%", limit)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_provider_distribution(self) -> List[Dict]:
        """获取 model_provider 分布"""
        self._ensure_connected()
        results = []
        if not self.has_column("model_provider"):
            return results
        try:
            cur = self._conn.execute(
                "SELECT model_provider, COUNT(*) as cnt FROM threads "
                "GROUP BY model_provider ORDER BY cnt DESC"
            )
            for row in cur.fetchall():
                results.append({"provider": row[0], "count": row[1]})
        except Exception:
            pass
        return results

    def get_model_list(self) -> List[str]:
        """获取所有不同的 model 值"""
        self._ensure_connected()
        if not self.has_column("model"):
            return []
        try:
            cur = self._conn.execute(
                "SELECT DISTINCT model FROM threads WHERE model IS NOT NULL AND model != '' ORDER BY model"
            )
            return [row[0] for row in cur.fetchall()]
        except Exception:
            return []

    def get_provider_list(self) -> List[str]:
        """获取所有不同的 model_provider 值"""
        self._ensure_connected()
        if not self.has_column("model_provider"):
            return []
        try:
            cur = self._conn.execute(
                "SELECT DISTINCT model_provider FROM threads WHERE model_provider IS NOT NULL AND model_provider != '' ORDER BY model_provider"
            )
            return [row[0] for row in cur.fetchall()]
        except Exception:
            return []
