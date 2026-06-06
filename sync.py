"""
sync.py - Codex 历史记录同步引擎
核心功能：将当前 config.toml 的 model_provider/model 同步到所有历史记录

问题本质：
  Codex 用 model_provider 字段过滤 TUI 会话列表。
  用官方账户登录时，只显示 model_provider='openai' 的会话；
  切到 API 中转账户，只显示 model_provider='custom' 的会话。
  另一半就"消失"了。

  同步 = 把所有 threads 的 model_provider 和 model 统一改成当前配置值，
  让切换账户后所有历史会话都可见。

参照：pangkk18/codex-history-sync（GitHub）
"""
import os
import json
import sqlite3
import shutil
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone


DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5"

# Codex 进程名（Windows）
CODEX_PROCESS_NAMES = ["codex.exe", "Codex.exe"]

# Codex++ 启动器路径
CODEX_PLUS_PLUS_PATH = os.path.expandvars(
    r"C:\Users\zhaos\AppData\Local\Programs\Codex++\codex-plus-plus.exe"
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DETACHED_PROCESS = 0x00000008 if os.name == "nt" else 0


@dataclass
class SyncStats:
    """同步结果统计"""
    db_threads_seen: int = 0
    db_threads_updated: int = 0
    rollout_files_seen: int = 0
    rollout_files_updated: int = 0
    index_rows_seen: int = 0
    index_rows_updated: int = 0
    malformed_lines: int = 0
    backup_path: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.db_threads_updated or self.rollout_files_updated or self.index_rows_updated)


def resolve_codex_home(codex_home: str = "") -> Path:
    """获取 Codex 主目录"""
    if codex_home:
        return Path(os.path.expandvars(codex_home)).expanduser()
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(os.path.expandvars(env)).expanduser()
    return Path.home() / ".codex"


def load_config_toml(config_path: str) -> Dict[str, str]:
    """
    读取 config.toml 获取当前 model_provider 和 model
    使用简易 TOML 解析（不引入第三方库）
    """
    path = Path(config_path)
    if not path.exists():
        return {"model_provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL}

    result = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {"model_provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL}

    # 简易 TOML 解析（只需要顶层 key=value）
    try:
        import tomllib
        data = tomllib.loads(content)
        for key in ("model_provider", "modelProvider", "provider"):
            if key in data:
                result["model_provider"] = data[key]
                break
        for key in ("model",):
            if key in data:
                result["model"] = data[key]
                break
        # 检查 [defaults] 子表
        defaults = data.get("defaults", {})
        if "model_provider" not in result and "model_provider" in defaults:
            result["model_provider"] = defaults["model_provider"]
        if "model" not in result and "model" in defaults:
            result["model"] = defaults["model"]
    except Exception:
        # 回退到简易行解析
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key == "model_provider" or key == "modelProvider" or key == "provider":
                result["model_provider"] = value
            elif key == "model":
                result["model"] = value

    result.setdefault("model_provider", DEFAULT_PROVIDER)
    result.setdefault("model", DEFAULT_MODEL)
    return result


def _decode_tasklist_output(stdout: bytes) -> str:
    """Decode tasklist output with multiple encoding fallback."""
    for encoding in ("utf-8", "gbk", "cp936", "cp1252"):
        try:
            return stdout.decode(encoding)
        except Exception:
            continue
    return stdout.decode("utf-8", errors="replace")


def _find_pids_by_image(image_name: str) -> List[int]:
    """Find PIDs by image name using tasklist."""
    pids = []
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW
        )
        output = _decode_tasklist_output(result.stdout)
        for line in output.strip().splitlines():
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                name = parts[0]
                pid_str = parts[1]
                if name.lower() == image_name.lower() and pid_str.isdigit():
                    pids.append(int(pid_str))
    except Exception:
        pass
    return pids


def _find_node_codex_pids() -> List[int]:
    """Find node.exe processes that appear to be running codex."""
    pids = []
    try:
        import subprocess
        result = subprocess.run(
            ["wmic", "process", "where", "name='node.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW
        )
        output = _decode_tasklist_output(result.stdout)
        for line in output.strip().splitlines():
            if "node" not in line.lower():
                continue
            parts = line.strip('"').split('","')
            if len(parts) >= 3:
                cmdline = parts[-2].lower()
                pid_str = parts[-1]
                if "codex" in cmdline and pid_str.isdigit():
                    pids.append(int(pid_str))
    except Exception:
        pass
    return pids


def is_codex_running() -> Tuple[bool, List[int]]:
    """
    检查 Codex 进程是否在运行
    返回 (是否运行中, [PID列表])
    """
    pids = []
    for image_name in CODEX_PROCESS_NAMES:
        pids.extend(_find_pids_by_image(image_name))
    pids.extend(_find_node_codex_pids())
    # Deduplicate
    unique_pids = list(dict.fromkeys(pids))
    return len(unique_pids) > 0, unique_pids


def kill_codex() -> Tuple[bool, str]:
    """
    终止 Codex 进程
    返回 (是否成功, 消息)
    """
    running, pids = is_codex_running()
    if not running:
        return True, "Codex 未在运行"

    try:
        import subprocess
        for pid in pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        time.sleep(1)
        # 验证是否已关闭
        still_running, _ = is_codex_running()
        if still_running:
            return False, f"未能关闭 Codex (PID: {pids})"
        return True, f"已关闭 Codex (PID: {pids})"
    except Exception as e:
        return False, f"关闭 Codex 失败: {e}"


def start_codex(
    use_codex_plus_plus: bool = False,
    codex_plus_plus_path: str = "",
    codex_cli_path: str = "",
) -> Tuple[bool, str]:
    """
    启动 Codex（或 Codex++）

    use_codex_plus_plus: 如果为 True，优先使用 Codex++ 启动器
    返回 (是否成功, 消息)
    """
    try:
        import subprocess

        codex_paths = []

        # Codex++ 优先级最高（用独立启动器）
        if use_codex_plus_plus:
            for candidate in (codex_plus_plus_path, CODEX_PLUS_PLUS_PATH):
                if candidate and os.path.exists(candidate):
                    codex_paths.append(candidate)

        # 1. 从用户设置或 CODEX_CLI_PATH 环境变量（config.toml 中 MCP 配置）
        if codex_cli_path and os.path.exists(codex_cli_path):
            codex_paths.append(codex_cli_path)
        env_cli = os.environ.get("CODEX_CLI_PATH", "")
        if env_cli and os.path.exists(env_cli):
            codex_paths.append(env_cli)
        # 2. 从 AppData 目录搜索
        appdata = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            codex_app = os.path.join(appdata, "OpenAI", "Codex")
            if os.path.exists(codex_app):
                codex_paths.append(os.path.join(codex_app, "Codex.exe"))
                # 搜索 bin 子目录
                bin_dir = os.path.join(codex_app, "bin")
                if os.path.exists(bin_dir):
                    for d in os.listdir(bin_dir):
                        exe = os.path.join(bin_dir, d, "codex.exe")
                        if os.path.exists(exe):
                            codex_paths.append(exe)
        # 3. 系统 PATH
        which = shutil.which("codex")
        if which:
            codex_paths.append(which)

        for path in dict.fromkeys(codex_paths):
            if os.path.exists(path):
                subprocess.Popen([path], creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW)
                label = "Codex++" if "codex-plus-plus" in path.lower() else "Codex"
                return True, f"已启动 {label}: {path}"

        return False, "未找到 Codex 可执行文件，请手动启动"
    except Exception as e:
        return False, f"启动 Codex 失败: {e}"


# ─────────────── 同步核心逻辑 ───────────────

def sync_state_database(
    db_path: str,
    target_provider: str,
    target_model: str,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """
    同步 state_5.sqlite 的 threads 表
    返回 (seen, updated)
    """
    if not os.path.exists(db_path):
        return 0, 0

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode=WAL")

        # 检查表结构
        cur = conn.execute("PRAGMA table_info(threads)")
        columns = {row[1] for row in cur.fetchall()}
        if not {"id", "model_provider", "model"}.issubset(columns):
            return 0, 0

        # 查询需要更新的行
        cur = conn.execute("SELECT id, model_provider, model FROM threads")
        rows = cur.fetchall()
        seen = len(rows)

        to_update = [
            row_id for row_id, provider, model in rows
            if provider != target_provider or model != target_model
        ]
        updated = len(to_update)

        if to_update and not dry_run:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "UPDATE threads SET model_provider = ?, model = ? WHERE id = ?",
                ((target_provider, target_model, row_id) for row_id in to_update)
            )
            conn.commit()

        return seen, updated
    finally:
        conn.close()


def sync_rollout_file(
    file_path: str,
    target_provider: str,
    target_model: str,
    dry_run: bool = False,
) -> bool:
    """
    同步单个 jsonl 文件的 session_meta 头部
    返回是否修改了文件
    """
    # 流式读取第一行获取 session_meta
    first_line = None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    first_line = line
                    break
    except Exception:
        return False

    if not first_line:
        return False

    try:
        record = json.loads(first_line)
    except json.JSONDecodeError:
        return False

    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return False

    # 找到 payload 或 record 本身
    payload = record.get("payload")
    target = payload if isinstance(payload, dict) else record

    changed = False

    # 更新 provider 字段
    for key in ("model_provider", "modelProvider", "provider"):
        if key in target and target[key] != target_provider:
            target[key] = target_provider
            changed = True

    # 如果没有任何 provider 字段，添加
    if not any(k in target for k in ("model_provider", "modelProvider", "provider")):
        target["model_provider"] = target_provider
        changed = True

    # 更新 model 字段
    for key in ("model", "model_name", "modelName"):
        if key in target and target[key] != target_model:
            target[key] = target_model
            changed = True

    if not any(k in target for k in ("model", "model_name", "modelName")):
        target["model"] = target_model
        changed = True

    if changed and not dry_run:
        # 原子写入：只替换第一行
        _atomic_update_first_line(file_path, json.dumps(record, ensure_ascii=False, separators=(",", ":")))

    return changed


def _atomic_update_first_line(file_path: str, new_first_line: str):
    """原子性替换 jsonl 文件的第一行（其余行保持不变）"""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".codex_sync_", dir=os.path.dirname(file_path))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as tmp_f:
            tmp_f.write(new_first_line + "\n")
            # 复制剩余行
            with open(file_path, "r", encoding="utf-8", errors="replace") as src_f:
                first = True
                for line in src_f:
                    if first:
                        first = False
                        continue  # 跳过原始第一行
                    tmp_f.write(line)
        # 保留原文件权限
        if os.path.exists(file_path):
            shutil.copystat(file_path, tmp_path, follow_symlinks=False)
        os.replace(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def sync_rollout_files(
    sessions_dir: str,
    archived_dir: str,
    target_provider: str,
    target_model: str,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """
    同步所有 rollout jsonl 文件
    返回 (seen, updated)
    """
    seen = 0
    updated = 0

    for base_dir in (sessions_dir, archived_dir):
        if not os.path.exists(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            for fname in files:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(root, fname)
                seen += 1
                try:
                    changed = sync_rollout_file(fpath, target_provider, target_model, dry_run)
                    if changed:
                        updated += 1
                except Exception:
                    pass

    return seen, updated


def sync_session_index(
    index_path: str,
    db_path: str,
    target_provider: str,
    target_model: str,
    dry_run: bool = False,
) -> Tuple[int, int, int]:
    """
    同步 session_index.jsonl
    从 DB 重建 + 合并已有条目 + 统一 model_provider/model
    返回 (seen, updated, malformed)
    """
    # 读取现有 index 条目
    existing_entries = {}
    existing_order = []
    malformed = 0

    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    continue
                tid = str(record.get("id", "")).strip()
                if tid:
                    existing_entries[tid] = record
                    existing_order.append(tid)

    # 从 DB 读取条目
    db_entries = _read_index_from_db(db_path, target_provider, target_model)

    if db_entries is not None:
        # 合并：DB 条目优先
        db_ids = {e["id"] for e in db_entries}
        index_only = [existing_entries[tid] for tid in existing_order if tid not in db_ids]
        output = db_entries + index_only
        # 统一 model 字段
        for entry in output:
            _apply_model_fields(entry, target_provider, target_model)
        # 按时间排序
        output.sort(key=lambda x: (x.get("updated_at", ""), x.get("id", "")))
    else:
        output = []
        for tid in existing_order:
            record = dict(existing_entries[tid])
            _apply_model_fields(record, target_provider, target_model)
            output.append(record)

    # 比较是否有变化
    desired_text = "\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in output)
    if desired_text:
        desired_text += "\n"

    current_text = ""
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8", errors="replace") as f:
            current_text = f.read()

    seen = len(existing_entries)
    changed = desired_text != current_text
    updated = len(output) if changed else 0

    if changed and not dry_run:
        _atomic_write_text(index_path, desired_text)

    return seen, updated, malformed


def _read_index_from_db(db_path: str, target_provider: str, target_model: str) -> Optional[List[Dict]]:
    """从 DB 读取 index 条目"""
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
        if "id" not in columns:
            return None

        selected = ["id"]
        for col in ("title", "updated_at", "cwd", "git_branch", "git_sha", "git_origin_url", "rollout_path"):
            if col in columns:
                selected.append(col)

        where = "WHERE archived = 0" if "archived" in columns else ""
        rows = conn.execute(f"SELECT {', '.join(selected)} FROM threads {where} ORDER BY id ASC").fetchall()
    finally:
        conn.close()

    entries = []
    for row in rows:
        entry = {
            "id": str(row["id"]),
            "thread_name": str(row["title"]) if "title" in row.keys() and row["title"] else str(row["id"]),
            "model_provider": target_provider,
            "model": target_model,
        }
        if "updated_at" in row.keys() and row["updated_at"]:
            ts = int(row["updated_at"])
            if ts > 10_000_000_000:
                ts //= 1000
            entry["updated_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")

        # git 元数据
        git_meta = {}
        for key, row_key in [("branch", "git_branch"), ("commit_hash", "git_sha"), ("repository_url", "git_origin_url")]:
            if row_key in row.keys() and row[row_key]:
                git_meta[key] = str(row[row_key])
        if git_meta:
            entry["git"] = git_meta

        if "rollout_path" in row.keys() and row["rollout_path"]:
            entry["rollout_path"] = str(row["rollout_path"])

        entries.append(entry)
    return entries


def _apply_model_fields(record: Dict, provider: str, model: str) -> bool:
    """将 model_provider 和 model 字段应用到记录"""
    changed = False
    for key in ("model_provider", "modelProvider", "provider"):
        if key in record and record.get(key) != provider:
            record[key] = provider
            changed = True
    for key in ("model", "model_name", "modelName"):
        if key in record and record.get(key) != model:
            record[key] = model
            changed = True
    if not any(k in record for k in ("model_provider", "modelProvider", "provider")):
        record["model_provider"] = provider
        changed = True
    if not any(k in record for k in ("model", "model_name", "modelName")):
        record["model"] = model
        changed = True
    return changed


def _atomic_write_text(path: str, content: str):
    """原子写入文本文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".codex_sync_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        if os.path.exists(path):
            shutil.copystat(path, tmp, follow_symlinks=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def full_sync(
    codex_home: str = "",
    target_provider: str = "",
    target_model: str = "",
    dry_run: bool = False,
) -> SyncStats:
    """
    执行完整同步流程

    1. 读 config.toml 获取目标 provider/model
    2. 同步 state_5.sqlite
    3. 同步所有 rollout jsonl 文件
    4. 重建 session_index.jsonl
    """
    stats = SyncStats()

    home = resolve_codex_home(codex_home)
    config_path = home / "config.toml"
    db_path = home / "state_5.sqlite"
    sessions_dir = home / "sessions"
    archived_dir = home / "archived_sessions"
    index_path = home / "session_index.jsonl"

    # 1. 获取目标设置
    if not target_provider or not target_model:
        config = load_config_toml(str(config_path))
        target_provider = target_provider or config.get("model_provider", DEFAULT_PROVIDER)
        target_model = target_model or config.get("model", DEFAULT_MODEL)

    # 2. 同步 DB
    seen, updated = sync_state_database(str(db_path), target_provider, target_model, dry_run)
    stats.db_threads_seen = seen
    stats.db_threads_updated = updated

    # 3. 同步 jsonl 文件
    seen, updated = sync_rollout_files(
        str(sessions_dir), str(archived_dir),
        target_provider, target_model, dry_run
    )
    stats.rollout_files_seen = seen
    stats.rollout_files_updated = updated

    # 4. 重建 session_index.jsonl
    seen, updated, malformed = sync_session_index(
        str(index_path), str(db_path),
        target_provider, target_model, dry_run
    )
    stats.index_rows_seen = seen
    stats.index_rows_updated = updated
    stats.malformed_lines = malformed

    return stats
