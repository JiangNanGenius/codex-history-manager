"""
db.py - 数据库操作层
负责读写 state_5.sqlite，封装所有 SQL 操作
Web 版增强：返回更多字段支持前端展示和 Token 统计

设计意图：
  - 封装所有 SQLite 操作，避免 SQL 散落在各 API 端点中，便于统一维护和测试。
  - 使用 sqlite3.Row 作为 row_factory：支持按列名索引（row["title"]），
    比按数字索引（row[1]）更易读、更不易因表结构变更而出错。
  - WAL 模式（journal_mode=WAL）：SQLite 的 Write-Ahead Logging 允许读取
    和写入并发进行，避免 Codex CLI 正在写 DB 时本工具查询导致 DATABASE_LOCKED。

线程安全：
  - 使用 threading.RLock 保护连接状态：虽然 SQLite 的 Connection 对象本身
    不是线程安全的，但 WAL 模式下读取并发安全；RLock 主要用于防止 connect/close
    的竞争条件。
  - check_same_thread=False：Flask 每个请求可能在不同线程处理，关闭此检查
    允许跨线程使用同一连接。注意：这要求外部通过锁或串行化访问保证安全。

Windows 平台特殊性：
  - Windows 上 SQLite 文件锁行为与 Unix 略有不同：WAL 模式能显著降低
    文件锁冲突概率，因为读取者不再持有数据库文件的排他锁。
"""
import sqlite3
import os
import threading
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple


class CodexDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def connect(self):
        """
        建立数据库连接。

        设计意图：
          - check_same_thread=False：Flask 多线程环境下允许跨线程使用连接。
            注意：这要求外部通过 RLock 或串行化访问保证线程安全。
          - row_factory = sqlite3.Row：支持按列名索引，代码可读性更高。
          - WAL 模式：读写并发，显著降低与 Codex CLI 同时访问时的锁冲突。

        Raises:
            FileNotFoundError: 数据库文件不存在时抛出（常见于首次启动未配置路径）。
        """
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"数据库文件不存在: {self.db_path}")
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                # 开启 WAL 模式，减少锁冲突
                self._conn.execute("PRAGMA journal_mode=WAL")

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _ensure_connected(self):
        with self._lock:
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
        """
        获取数据库统计信息。

        返回字段：
          - total: 总线程数
          - active: 未归档线程数
          - archived: 已归档线程数
          - user_threads: 用户创建的线程数（排除 agent 自动生成）
          - total_tokens: 所有线程的 tokens_used 总和

        边界条件：
          - 若表结构缺少某些列（如旧版本无 tokens_used），对应字段返回 0，
            不抛出异常。
          - 任何 SQL 异常被捕获并放入 stats["error"]，保证函数始终返回字典。
        """
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
        获取会话列表（增强版，支持更多过滤和排序）。

        设计意图：
          - 动态 SQL 构建：根据传入参数和实际表结构动态生成 WHERE、ORDER BY、
            LIMIT 子句，避免为每种组合写冗长的 SQL 模板。
          - 列名兼容：不同版本 Codex 的表结构可能不同（如 source vs thread_source），
            运行时通过 PRAGMA table_info 检查列存在性，选择可用列。
          - 排序安全：sort_by 使用白名单过滤，防止 SQL 注入；若指定字段不存在，
            自动回退到可用字段（created_at_ms -> updated_at -> created_at -> id）。

        边界条件：
          - page_size 限制在 [1, 500]：防止前端传错值导致内存/性能问题。
          - searchable_cols 动态检查：若 threads 表缺少 title/id/cwd 列，
            搜索条件自动跳过，不报错。

        Args:
            filter_mode: "all" | "active" | "archived"
            search: 搜索关键词（LIKE 模糊匹配 title/id/cwd）。
            page: 页码，从 0 开始。
            page_size: 每页条数。
            source_filter: "all" | "user" | "agent"
            sort_by: 排序字段白名单内值。
            sort_order: "asc" | "desc"
            model_filter: 按模型名精确过滤。
            provider_filter: 按 provider 名精确过滤。

        Returns:
            (rows 列表, total_count 总条数)
        """
        self._ensure_connected()
        columns = set(self.get_columns())
        conditions = []
        params = []

        if "archived" in columns and filter_mode == "active":
            conditions.append("archived=0")
        elif "archived" in columns and filter_mode == "archived":
            conditions.append("archived=1")

        # source 列名兼容
        source_col = "source" if "source" in columns else (
            "thread_source" if "thread_source" in columns else ""
        )
        if source_filter == "user" and source_col:
            conditions.append(f"{source_col}='user'")
        elif source_filter == "agent" and source_col:
            conditions.append(f"{source_col}='agent'")

        if model_filter and "model" in columns:
            conditions.append("model=?")
            params.append(model_filter)

        if provider_filter and "model_provider" in columns:
            conditions.append("model_provider=?")
            params.append(provider_filter)

        searchable_cols = [c for c in ("title", "id", "cwd") if c in columns]
        if search.strip() and searchable_cols:
            conditions.append("(" + " OR ".join(f"{c} LIKE ?" for c in searchable_cols) + ")")
            like = f"%{search.strip()}%"
            params.extend([like] * len(searchable_cols))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # 获取总数
        count_sql = f"SELECT COUNT(*) FROM threads {where}"
        total = self._conn.execute(count_sql, params).fetchone()[0]

        # 安全排序字段白名单
        allowed_sort = {"created_at_ms", "updated_at", "tokens_used", "title", "created_at", "id"}
        if sort_by not in allowed_sort:
            sort_by = "created_at_ms"
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        # 检查排序字段是否存在，并为旧 DB 选择可用回退字段
        if sort_by not in columns:
            sort_by = next((c for c in ("created_at_ms", "updated_at", "created_at", "id") if c in columns), "id")

        # 获取分页数据
        page = max(0, int(page or 0))
        page_size = min(max(1, int(page_size or 50)), 500)
        offset = page * page_size

        # 选择更多字段
        select_cols = [c for c in ("id", "title", "archived", "rollout_path", "created_at", "updated_at") if c in columns]
        for col in ["created_at_ms", "tokens_used", "model", "model_provider", "source", "thread_source", "cwd"]:
            if col in columns and col not in select_cols:
                select_cols.append(col)
        if not select_cols:
            return [], total

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
        """
        获取单条会话详情。

        Args:
            thread_id: 会话 ID。

        Returns:
            会话字典，或 None（未找到时）。
        """
        self._ensure_connected()
        cur = self._conn.execute(
            "SELECT * FROM threads WHERE id=?", (thread_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def set_archived(self, thread_id: str, archived: int) -> bool:
        """
        设置归档状态 (0=未归档, 1=已归档)。

        设计意图：
          - 使用整数而非布尔：SQLite 无原生布尔类型，0/1 是跨语言通用约定。
          - 立即 commit：确保前端操作后立即可见，不依赖连接关闭时的隐式提交。

        Args:
            thread_id: 会话 ID。
            archived: 0 或 1。

        Returns:
            操作是否成功。
        """
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
        """
        删除会话记录（仅从 DB 删除，不删 jsonl 文件）。

        设计意图：
          - 保守策略：只删索引，保留原始会话文件，防止用户误删后无法恢复。
          - 如需彻底清理磁盘，需额外实现「孤儿文件扫描」功能。

        Args:
            thread_id: 会话 ID。

        Returns:
            操作是否成功。
        """
        self._ensure_connected()
        try:
            self._conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))
            self._conn.commit()
            return True
        except Exception as e:
            print(f"删除会话失败: {e}")
            return False

    def get_threads_since(self, since_ts: str) -> List[Dict]:
        """
        获取某时间戳之后变更的所有 threads（用于增量备份）。

        设计意图：
          - 多时间戳策略：不同版本 Codex 使用不同的时间字段（updated_at、
            created_at_ms、created_at），本函数构建 OR 条件覆盖所有可能字段，
            确保不遗漏变更记录。
          - 类型兼容：created_at 可能是整数或 ISO 字符串，使用 CASE/GLOB 判断
            是否为纯数字，再选择整数比较或字符串比较。

        边界条件：
          - since_ts 为空或解析失败时 since_sec=0，查询返回所有记录（安全降级）。
          - 若表缺少所有时间字段，则 WHERE 为空，返回全表数据（增量退化为全量）。

        Args:
            since_ts: ISO 格式或 Unix 时间戳字符串。

        Returns:
            变更的 thread 字典列表。
        """
        self._ensure_connected()
        columns = set(self.get_columns())
        since_sec = _parse_since_to_epoch(since_ts)
        since_ms = since_sec * 1000
        clauses = []
        params = []

        if "updated_at" in columns:
            clauses.append("CAST(COALESCE(updated_at, 0) AS INTEGER) > ?")
            params.append(since_sec)
        if "created_at_ms" in columns:
            clauses.append("CAST(COALESCE(created_at_ms, 0) AS INTEGER) > ?")
            params.append(since_ms)
        if "created_at" in columns:
            # created_at 可能是整数时间戳或 ISO 字符串，做双路比较
            clauses.append(
                "(CASE WHEN CAST(created_at AS TEXT) GLOB '[0-9]*' "
                "THEN CAST(created_at AS INTEGER) > ? ELSE CAST(created_at AS TEXT) > ? END)"
            )
            params.extend([since_sec, _epoch_to_iso(since_sec)])

        where = " OR ".join(clauses)
        sql = "SELECT * FROM threads"
        if where:
            sql += f" WHERE {where}"
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def search_full_text(self, keyword: str, limit: int = 100) -> List[Dict]:
        """
        全文搜索 title（LIKE 模糊匹配）。

        设计意图：
          - SQLite 无全文索引（FTS），对 title 做 LIKE 模糊匹配足够应对
            <10万条记录的规模；若未来数据量激增，可迁移到 FTS5。
          - 动态列选择：若 title 列不存在，返回空列表而非报错。

        Args:
            keyword: 搜索关键词。
            limit: 最大返回条数。

        Returns:
            匹配的会话列表。
        """
        self._ensure_connected()
        columns = set(self.get_columns())
        if "title" not in columns:
            return []
        order_col = "created_at_ms" if "created_at_ms" in columns else ("updated_at" if "updated_at" in columns else "id")
        select_cols = [c for c in ("id", "title", "archived", "created_at") if c in columns]
        if not select_cols:
            return []
        cur = self._conn.execute(
            f"SELECT {', '.join(select_cols)} FROM threads "
            f"WHERE title LIKE ? ORDER BY {order_col} DESC LIMIT ?",
            (f"%{keyword}%", limit)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_provider_distribution(self) -> List[Dict]:
        """
        获取 model_provider 分布统计。

        Returns:
            [{"provider": str, "count": int}, ...]，按 count 降序排列。
        """
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
        """
        获取所有不同的 model 值（用于前端筛选下拉框）。

        Returns:
            去重且排序后的 model 名称列表。
        """
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
        """
        获取所有不同的 model_provider 值（用于前端筛选下拉框）。

        Returns:
            去重且排序后的 provider 名称列表。
        """
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


def _parse_since_to_epoch(value: str) -> int:
    """Parse ISO/numeric timestamps to epoch seconds for DB comparisons."""
    if not value:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        number = int(text)
        return int(number / 1000) if number > 10_000_000_000 else number
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return int(parsed.timestamp())
    except ValueError:
        return 0


def _epoch_to_iso(epoch_seconds: int) -> str:
    try:
        return datetime.fromtimestamp(epoch_seconds).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""
