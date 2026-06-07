"""
move_repair.py - 项目与会话移动修复模块

背景与问题本质：
  Codex CLI 在 Windows 上将会话数据分散存储于 SQLite（threads 表）、
  JSONL（session_meta）和 session_index.jsonl 三个位置。
  当 conversation 从普通聊天移动到 workspace 后，若这三个数据源的 cwd
  没有同步更新，会导致工作区只显示 .git、tracked files 不可见的状态。

设计意图：
  - 原子更新：修改 SQLite、JSONL、Index 三个数据源时，任一失败即回滚，
    防止出现「部分更新」导致的元数据不一致。
  - 备份优先：任何写操作前先创建带时间戳的备份，保留恢复路径。
  - 验证后置：更新后自动执行一致性校验，确保三端 cwd 对齐且指向有效 Git 仓库。
  - 零副作用预演：dry_run_move 只做只读验证，不触碰任何文件。

工程权衡：
  - 使用 shutil.copy2 而非 pathlib.copy：保留 Windows 上的完整文件元数据
    （含修改时间），这对 Codex CLI 判断文件 freshness 很重要。
  - JSONL 修改策略「全量读入 → 修改 → 写回」而非流式替换：
    session_index.jsonl 通常 <10MB，全量处理代码更简单、更安全；
    超大文件场景不在本模块设计目标内。
  - 数据库文件名兼容：任务描述为 threads.db，但实际 Codex CLI 使用
    state_5.sqlite；初始化时优先 threads.db，不存在则回退到 state_5.sqlite。

Windows 平台特殊性：
  - subprocess.run 附带 creationflags=CREATE_NO_WINDOW，防止 git 命令
    触发控制台窗口闪烁。
  - expanduser + expandvars 处理 Windows 环境变量如 %USERPROFILE%。

边界条件：
  - thread_id 对应的 JSONL 文件可能位于 sessions/ 或 archived_sessions/，
    搜索时覆盖两者。
  - session_index.jsonl 中可能缺失对应 thread_id 的行，此时 index 更新
    为空操作（no-op），不视为失败。
  - target_path 支持大小写不敏感比较（Windows 文件系统默认不敏感）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class MoveRepairManager:
    def __init__(self, codex_home: str = "", db_path: str = "", sessions_dir: str = ""):
        """
        初始化 MoveRepairManager。

        Args:
            codex_home: Codex 主目录，默认 ~/.codex。
            db_path: 显式指定 SQLite 路径；为空时从 codex_home 推导。
            sessions_dir: 显式指定 JSONL 会话目录；为空时从 codex_home 推导。
        """
        if codex_home:
            self.codex_home = Path(os.path.expandvars(codex_home)).expanduser()
        else:
            env = os.environ.get("CODEX_HOME")
            if env:
                self.codex_home = Path(os.path.expandvars(env)).expanduser()
            else:
                self.codex_home = Path.home() / ".codex"

        if db_path:
            self.db_path = Path(os.path.expandvars(db_path)).expanduser()
        else:
            # 优先 threads.db（任务描述），回退 state_5.sqlite（实际 Codex CLI）
            candidate = self.codex_home / "threads.db"
            self.db_path = candidate if candidate.exists() else self.codex_home / "state_5.sqlite"

        if sessions_dir:
            self.sessions_dir = Path(os.path.expandvars(sessions_dir)).expanduser()
        else:
            self.sessions_dir = self.codex_home / "sessions"

        self.archived_dir = self.codex_home / "archived_sessions"
        self.index_path = self.codex_home / "session_index.jsonl"
        self._backup_dir = Path.home() / ".codex_enhance_manager" / "move_repair_backups"

    # ─────────────── 读取元数据 ───────────────

    def read_thread_metadata(self, thread_id: str) -> Dict[str, Any]:
        """
        从 SQLite 和 JSONL 读取并合并 thread 元数据。

        设计意图：
          - 将分散在两处的元数据聚合为单一视图，便于前端展示和诊断。
          - JSONL 只读第一行（session_meta），避免加载大文件。

        边界条件：
          - SQLite 中无记录时，db_meta 为 {}，但仍尝试读取 JSONL。
          - JSONL 不存在或首行非 session_meta 时，jsonl_meta 为 {}。
        """
        db_meta = self._read_db_meta(thread_id)
        jsonl_meta = self._read_jsonl_meta(thread_id)

        merged = dict(db_meta)
        # JSONL 中的 cwd 优先级高于 DB（更贴近会话实际创建路径）
        for key in ("cwd", "title", "model", "provider"):
            if key in jsonl_meta and jsonl_meta[key]:
                merged[key] = jsonl_meta[key]
        merged["jsonl_found"] = jsonl_meta.get("_file_found", False)
        merged["jsonl_path"] = jsonl_meta.get("_file_path", "")
        return merged

    def _read_db_meta(self, thread_id: str) -> Dict[str, Any]:
        if not self.db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, cwd, title, created_at, model, provider, archived FROM threads WHERE id=?",
                (thread_id,),
            )
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else {}
        except Exception:
            return {}

    def _read_jsonl_meta(self, thread_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"_file_found": False, "_file_path": ""}
        path = self._find_jsonl_for_thread(thread_id)
        if not path:
            return result
        result["_file_found"] = True
        result["_file_path"] = str(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict) and record.get("type") == "session_meta":
                        payload = record.get("payload")
                        target = payload if isinstance(payload, dict) else record
                        result["cwd"] = target.get("cwd", "")
                        result["title"] = target.get("title", "")
                        result["model"] = target.get("model", "")
                        result["provider"] = target.get("provider", "")
                        break
                    break  # 只读第一行有效 JSON
        except Exception:
            pass
        return result

    def _find_jsonl_for_thread(self, thread_id: str) -> Optional[Path]:
        """在 sessions_dir 和 archived_dir 中搜索 thread_id 对应的 jsonl 文件。"""
        for base in (self.sessions_dir, self.archived_dir):
            if not base.exists():
                continue
            direct = base / f"{thread_id}.jsonl"
            if direct.exists():
                return direct
            # 模糊匹配（应对 Codex 在文件名前加时间戳等前缀的情况）
            for p in base.rglob(f"*{thread_id}*.jsonl"):
                return p
        return None

    # ─────────────── Dry Run ───────────────

    def dry_run_move(self, thread_id: str, target_path: str) -> Dict[str, Any]:
        """
        验证移动可行性，不修改任何数据。

        返回:
            {
                "can_move": bool,
                "reasons": List[str],
                "expected_changes": Dict[str, Any],
            }
        """
        reasons: List[str] = []
        target = Path(os.path.expandvars(target_path)).expanduser()

        if not target.exists():
            reasons.append(f"目标路径不存在: {target}")
        if not (target / ".git").exists():
            reasons.append(f"目标路径不是 Git 仓库（缺少 .git）: {target}")

        tracked_visible = False
        if target.exists() and (target / ".git").exists():
            tracked = self._git_ls_files(str(target))
            if tracked is None:
                reasons.append("无法执行 git ls-files，请检查 Git 是否安装")
            elif len(tracked) == 0:
                reasons.append("Git 仓库中无 tracked files（可能是空仓库）")
            else:
                tracked_visible = True
                reasons.append(f"检测到 {len(tracked)} 个 tracked files")

        meta = self.read_thread_metadata(thread_id)
        expected = {
            "sqlite_cwd_old": meta.get("cwd", ""),
            "sqlite_cwd_new": str(target),
            "jsonl_cwd_old": meta.get("cwd", ""),
            "jsonl_cwd_new": str(target),
            "index_cwd_old": meta.get("cwd", ""),
            "index_cwd_new": str(target),
        }

        can_move = target.exists() and (target / ".git").exists() and tracked_visible
        return {
            "can_move": can_move,
            "reasons": reasons,
            "expected_changes": expected,
        }

    def _git_ls_files(self, repo_path: str) -> Optional[List[str]]:
        """在指定仓库执行 git ls-files，返回 tracked files 列表；失败返回 None。"""
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "ls-files"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                return None
            return [line for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            return None

    # ─────────────── 执行移动 ───────────────

    def execute_move(self, thread_id: str, target_path: str) -> Dict[str, Any]:
        """
        原子执行会话 cwd 移动。

        流程：
          1. 备份 SQLite、JSONL、session_index.jsonl
          2. 更新 SQLite threads.cwd
          3. 更新 JSONL session_meta.cwd（首行）
          4. 更新 session_index.jsonl 对应行 cwd
          5. 一致性校验
          6. 任一失败则回滚备份

        返回:
            {
                "success": bool,
                "changes": Dict[str, Any],
                "verification": Dict[str, Any],
                "restart_required": bool,
                "error": str or None,
            }
        """
        target = Path(os.path.expandvars(target_path)).expanduser()
        result: Dict[str, Any] = {
            "success": False,
            "changes": {},
            "verification": {},
            "restart_required": True,
            "error": None,
        }

        # Step 0: 预检查
        dry = self.dry_run_move(thread_id, str(target))
        if not dry["can_move"]:
            result["error"] = "预检查失败: " + "; ".join(dry["reasons"])
            return result

        # Step 1: 定位文件并备份（记录原始路径 → 备份路径映射）
        jsonl_path = self._find_jsonl_for_thread(thread_id)
        backups: List[Tuple[Path, Path]] = []
        try:
            if self.db_path.exists():
                backups.append((self.db_path, Path(self._backup_file(self.db_path))))
            if jsonl_path and jsonl_path.exists():
                backups.append((jsonl_path, Path(self._backup_file(jsonl_path))))
            if self.index_path.exists():
                backups.append((self.index_path, Path(self._backup_file(self.index_path))))
        except Exception as e:
            result["error"] = f"备份失败: {e}"
            return result

        changes: Dict[str, Any] = {"db_updated": False, "jsonl_updated": False, "index_updated": False}

        def _do_rollback() -> None:
            """局部回滚函数：将每个备份文件 copy2 回原始路径。"""
            for original, backup in backups:
                if backup.exists():
                    try:
                        shutil.copy2(backup, original)
                    except Exception:
                        pass

        try:
            # Step 2: 更新 SQLite
            changes["db_updated"] = self._update_db_cwd(thread_id, str(target))
            if not changes["db_updated"]:
                # DB 无记录不视为致命错误，但记录日志
                pass

            # Step 3: 更新 JSONL
            if jsonl_path:
                changes["jsonl_updated"] = self._update_jsonl_cwd(jsonl_path, str(target))

            # Step 4: 更新 Index
            changes["index_updated"] = self._update_index_cwd(thread_id, str(target))

            # Step 5: 验证
            verification = self.verify_consistency(thread_id)
            result["verification"] = verification

            if not verification.get("consistent", False):
                raise MoveRepairError(f"一致性校验未通过: {verification.get('reasons', [])}")

            result["success"] = True
            result["changes"] = changes
            result["restart_required"] = True
        except Exception as e:
            result["error"] = str(e)
            _do_rollback()
            result["changes"] = {k: False for k in changes}
            result["verification"] = {}

        return result

    def _backup_file(self, path: Path) -> str:
        """创建时间戳备份，返回备份路径。"""
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{path.name}.{ts}.bak"
        dest = self._backup_dir / backup_name
        shutil.copy2(path, dest)
        return str(dest)

    def _update_db_cwd(self, thread_id: str, new_cwd: str) -> bool:
        if not self.db_path.exists():
            return False
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cur = conn.execute("SELECT cwd FROM threads WHERE id=?", (thread_id,))
            if not cur.fetchone():
                conn.close()
                return False
            conn.execute("UPDATE threads SET cwd=? WHERE id=?", (new_cwd, thread_id))
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def _update_jsonl_cwd(self, jsonl_path: Path, new_cwd: str) -> bool:
        if not jsonl_path.exists():
            return False
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                return False

            first = lines[0].strip()
            if not first:
                return False
            record = json.loads(first)
            if not (isinstance(record, dict) and record.get("type") == "session_meta"):
                return False

            payload = record.get("payload")
            target = payload if isinstance(payload, dict) else record
            old_cwd = target.get("cwd", "")
            if old_cwd == new_cwd:
                return False  # 无变化
            target["cwd"] = new_cwd
            if payload is not None and isinstance(payload, dict):
                record["payload"] = payload
            else:
                record = target

            new_first = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            # 原子写入
            fd, tmp = tempfile.mkstemp(prefix=".move_repair_", dir=str(jsonl_path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_f:
                    tmp_f.write(new_first)
                    for line in lines[1:]:
                        tmp_f.write(line)
                if jsonl_path.exists():
                    shutil.copystat(jsonl_path, tmp, follow_symlinks=False)
                os.replace(tmp, jsonl_path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            return True
        except Exception:
            return False

    def _update_index_cwd(self, thread_id: str, new_cwd: str) -> bool:
        if not self.index_path.exists():
            return False
        try:
            with open(self.index_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            changed = False
            new_lines: List[str] = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    new_lines.append(line)
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    new_lines.append(line)
                    continue
                if isinstance(record, dict) and str(record.get("id", "")) == thread_id:
                    old = record.get("cwd", "")
                    if old != new_cwd:
                        record["cwd"] = new_cwd
                        changed = True
                    new_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                else:
                    new_lines.append(line)

            if not changed:
                return False

            fd, tmp = tempfile.mkstemp(prefix=".move_repair_", dir=str(self.index_path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_f:
                    tmp_f.writelines(new_lines)
                if self.index_path.exists():
                    shutil.copystat(self.index_path, tmp, follow_symlinks=False)
                os.replace(tmp, self.index_path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            return True
        except Exception:
            return False

    # ─────────────── 一致性校验 ───────────────

    def verify_consistency(self, thread_id: str) -> Dict[str, Any]:
        """
        检查三端 cwd 是否一致且指向有效 Git 仓库。

        返回:
            {
                "consistent": bool,
                "reasons": List[str],
                "sqlite_cwd": str,
                "jsonl_cwd": str,
                "index_cwd": str,
                "git_valid": bool,
            }
        """
        reasons: List[str] = []

        db_meta = self._read_db_meta(thread_id)
        sqlite_cwd = db_meta.get("cwd", "") or ""

        jsonl_meta = self._read_jsonl_meta(thread_id)
        jsonl_cwd = jsonl_meta.get("cwd", "") or ""

        index_cwd = ""
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(record, dict) and str(record.get("id", "")) == thread_id:
                            index_cwd = record.get("cwd", "") or ""
                            break
            except Exception:
                pass

        cwds = [sqlite_cwd, jsonl_cwd, index_cwd]
        non_empty = [c for c in cwds if c]

        if not non_empty:
            reasons.append("所有数据源均未找到 cwd 信息")
            return {
                "consistent": False,
                "reasons": reasons,
                "sqlite_cwd": sqlite_cwd,
                "jsonl_cwd": jsonl_cwd,
                "index_cwd": index_cwd,
                "git_valid": False,
            }

        # 使用规范化路径比较（Windows 不区分大小写）
        normalized = [str(Path(c).resolve()).lower() for c in non_empty]
        all_same = len(set(normalized)) == 1

        if not all_same:
            reasons.append("SQLite / JSONL / Index 三端 cwd 不一致")
        else:
            reasons.append("三端 cwd 一致")

        git_valid = False
        if non_empty:
            target = Path(non_empty[0])
            if target.exists() and (target / ".git").exists():
                toplevel = self._git_rev_parse_show_toplevel(str(target))
                if toplevel is not None:
                    expected = str(target.resolve()).lower()
                    actual = str(Path(toplevel).resolve()).lower()
                    if expected == actual:
                        git_valid = True
                        reasons.append("Git 仓库验证通过")
                    else:
                        reasons.append(f"Git toplevel 不匹配: {toplevel} != {target}")
                else:
                    reasons.append("git rev-parse --show-toplevel 执行失败")
            else:
                reasons.append("cwd 指向的路径不是有效 Git 仓库")

        consistent = all_same and git_valid
        return {
            "consistent": consistent,
            "reasons": reasons,
            "sqlite_cwd": sqlite_cwd,
            "jsonl_cwd": jsonl_cwd,
            "index_cwd": index_cwd,
            "git_valid": git_valid,
        }

    def _git_rev_parse_show_toplevel(self, repo_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                return None
            return result.stdout.strip()
        except Exception:
            return None

    # ─────────────── 修复当前线程 ───────────────

    def repair_current_thread(self) -> Dict[str, Any]:
        """
        检测当前工作目录与 thread cwd 的匹配关系，并提供修复建议。

        设计意图：
          - 无感检测：用户可能在 Codex UI 中遇到「文件不可见」问题，
            本函数通过比对 os.getcwd() 与元数据 cwd，快速定位是否属于
            move state 问题。
          - 多候选支持：若当前目录无精确匹配，返回最近似候选列表，
            供前端让用户手动选择。

        边界条件：
          - 未找到任何 thread 时返回空候选列表，不抛异常。
          - DB 不可用时仅基于文件系统做降级检测。
        """
        current_cwd = os.getcwd()
        current_path = Path(current_cwd).resolve()

        result: Dict[str, Any] = {
            "current_cwd": current_cwd,
            "matched_threads": [],
            "mismatch_detected": False,
            "suggested_actions": [],
        }

        if not self.db_path.exists():
            result["suggested_actions"].append("未找到数据库，请检查 db_path 配置")
            return result

        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
            select = [c for c in ("id", "cwd", "title", "created_at") if c in columns]
            if not select:
                conn.close()
                return result

            rows = conn.execute(f"SELECT {', '.join(select)} FROM threads").fetchall()
            conn.close()
        except Exception:
            return result

        candidates: List[Dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            tid = row_dict.get("id", "")
            tcwd = row_dict.get("cwd", "") or ""
            if not tid:
                continue
            tcwd_path = Path(tcwd).resolve() if tcwd else None
            match = False
            if tcwd_path:
                try:
                    match = current_path == tcwd_path
                except Exception:
                    pass
            candidates.append({
                "thread_id": tid,
                "thread_cwd": tcwd,
                "title": row_dict.get("title", ""),
                "exact_match": match,
                "distance": 0 if match else self._path_distance(str(current_path), str(tcwd_path) if tcwd_path else ""),
            })

        exact = [c for c in candidates if c["exact_match"]]
        if exact:
            result["matched_threads"] = exact
            # 即使精确匹配，也做一次一致性校验
            verification = self.verify_consistency(exact[0]["thread_id"])
            if not verification["consistent"]:
                result["mismatch_detected"] = True
                result["suggested_actions"].append(
                    f"当前目录与 thread {exact[0]['thread_id']} 精确匹配，但一致性校验失败，建议执行修复"
                )
            else:
                result["suggested_actions"].append("当前目录与 thread cwd 精确匹配且状态一致，无需修复")
        else:
            # 无精确匹配：按路径相似度排序返回
            candidates.sort(key=lambda x: x["distance"])
            result["matched_threads"] = candidates[:5]
            result["mismatch_detected"] = True
            result["suggested_actions"].append("当前工作目录与所有 thread cwd 都不匹配，请确认是否已移动工作区")
            if candidates:
                result["suggested_actions"].append(
                    f"最近似候选: {candidates[0]['thread_id']} (cwd: {candidates[0]['thread_cwd']})"
                )

        return result

    @staticmethod
    def _path_distance(a: str, b: str) -> int:
        """简单的路径编辑距离（用于排序候选）。"""
        if not a or not b:
            return max(len(a), len(b))
        # 如果一个是另一个的父目录，距离更小
        a_norm = a.lower().rstrip("\\/")
        b_norm = b.lower().rstrip("\\/")
        if a_norm.startswith(b_norm) or b_norm.startswith(a_norm):
            return abs(len(a_norm) - len(b_norm))
        # 否则用集合差衡量
        a_parts = set(a_norm.split(os.sep))
        b_parts = set(b_norm.split(os.sep))
        return len(a_parts.symmetric_difference(b_parts))


class MoveRepairError(Exception):
    """移动修复过程中的可控异常。"""
    pass
