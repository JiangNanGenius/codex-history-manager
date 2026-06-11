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
import re
import sqlite3
import shutil
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from codex_config import load_config_toml as _load_config_toml


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
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0


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


def resolve_codex_db_path(codex_home: str = "", db_path: str = "") -> Path:
    """Resolve the Codex SQLite path, preferring explicit config and newest state_N."""
    if db_path:
        return Path(os.path.expandvars(db_path)).expanduser()
    home = resolve_codex_home(codex_home)
    threads_db = home / "threads.db"
    if threads_db.exists():
        return threads_db
    state_files = sorted(
        home.glob("state_*.sqlite"),
        key=lambda path: _state_db_number(path),
        reverse=True,
    )
    if state_files:
        return state_files[0]
    return home / "state_5.sqlite"


def _state_db_number(path: Path) -> int:
    try:
        return int(path.stem.replace("state_", ""))
    except ValueError:
        return 0


def load_config_toml(config_path: str) -> Dict[str, str]:
    """
    读取 config.toml 获取当前 model_provider 和 model。
    底层复用 codex_config.load_config_toml，避免 TOML 解析重复实现。

    字段兼容设计：
      - Codex 不同版本使用不同键名：model_provider、modelProvider、provider。
      - 本函数按优先级遍历，首个命中即停止，保证向后兼容。
      - 支持 [defaults] 子表：Codex 某些版本将默认值放在 [defaults] 下。

    边界条件：
      - 文件不存在或解析失败时返回 DEFAULT_PROVIDER / DEFAULT_MODEL，
        防止同步流程因配置读取失败而中断。

    Args:
        config_path: config.toml 的绝对路径。

    Returns:
        {"model_provider": str, "model": str}
    """
    data = _load_config_toml(config_path)
    if not data:
        return {"model_provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL}

    result = {}
    # 顶层字段：多键名兼容不同 Codex 版本
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


def _find_pids_by_image(image_name: str, timeout: int = 10) -> List[int]:
    """Find PIDs by image name using tasklist."""
    pids = []
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW
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


def _find_node_codex_pids(timeout: int = 10) -> List[int]:
    """
    查找运行 Codex 的 node.exe 进程 PID。

    设计意图与 Windows 平台特殊性：
      - Codex CLI 早期版本基于 Node.js 运行，主进程名为 node.exe 而非 codex.exe。
      - Windows 11 23H2+ 移除了 wmic 工具，因此使用 PowerShell Get-CimInstance
        替代，避免在新系统上命令失败。
      - creationflags=CREATE_NO_WINDOW 防止 PowerShell 窗口闪烁弹出，
        提升用户体验。
      - CSV 输出解析：通过 ConvertTo-Csv 获得结构化输出，比纯文本 tasklist
        更稳定（不受空格对齐影响）。

    边界条件：
      - PowerShell 执行失败（如被组策略禁用）时返回空列表，不阻断流程。
      - 命令行中只要包含 "codex" 子串即视为目标进程，可能误匹配其他 node 程序，
        但后续会与 codex.exe 搜索结果去重，不会导致误杀。

    Returns:
        node.exe 进程中疑似 Codex 的 PID 列表。
    """
    pids = []
    try:
        import subprocess
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \\\"Name='node.exe'\\\" | "
            "Select-Object ProcessId,CommandLine | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW
        )
        output = _decode_tasklist_output(result.stdout)
        lines = output.strip().splitlines()
        if len(lines) < 2:
            return pids
        # CSV header: "ProcessId","CommandLine"
        for line in lines[1:]:
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                pid_str = parts[0]
                cmdline = parts[1].lower()
                if "codex" in cmdline and pid_str.isdigit():
                    pids.append(int(pid_str))
    except Exception:
        pass
    return pids


def is_codex_running(timeout: int = 3) -> Tuple[bool, List[int]]:
    """
    检查 Codex 进程是否在运行。

    设计意图：
      - 多源检测：同时检查 codex.exe（新版 Electron 打包）和 node.exe（旧版），
        覆盖不同版本的 Codex CLI。
      - dict.fromkeys 去重：保持 PID 原始顺序的同时去除重复，
        避免同一进程被 image_name 和 node 搜索同时命中。

    Windows 平台特殊性：
      - tasklist 在 Windows 上可靠，但输出编码可能是 utf-8、gbk 或 cp936，
        _decode_tasklist_output 做多编码回退解码。

    Returns:
        (是否运行中, [PID列表])
    """
    pids = []
    safe_timeout = max(int(timeout or 3), 1)
    for image_name in CODEX_PROCESS_NAMES:
        pids.extend(_find_pids_by_image(image_name, timeout=safe_timeout))
    pids.extend(_find_node_codex_pids(timeout=safe_timeout))
    # Deduplicate：dict.fromkeys 保持顺序去重，Python 3.7+ 有序性保证
    unique_pids = list(dict.fromkeys(pids))
    return len(unique_pids) > 0, unique_pids


def kill_codex(timeout: int = 4) -> Tuple[bool, str]:
    """
    终止 Codex 进程
    返回 (是否成功, 消息)
    """
    safe_timeout = max(int(timeout or 4), 2)
    running, pids = is_codex_running(timeout=1)
    if not running:
        return True, "Codex 未在运行"

    try:
        import subprocess
        args = ["taskkill"]
        for pid in pids:
            args.extend(["/PID", str(pid)])
        args.extend(["/T", "/F"])
        subprocess.run(
            args,
            capture_output=True,
            timeout=safe_timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        time.sleep(0.4)
        # 验证是否已关闭
        still_running, still_pids = is_codex_running(timeout=1)
        if still_running:
            return False, f"已请求关闭 Codex，但仍检测到进程 (PID: {still_pids})"
        return True, f"已关闭 Codex (PID: {pids})"
    except Exception as e:
        return False, f"关闭 Codex 失败: {e}"


def _dedupe_existing_paths(paths: List[str]) -> List[str]:
    result = []
    seen = set()
    for raw in paths:
        if not raw:
            continue
        path = os.path.expandvars(str(raw).strip().strip('"'))
        key = path.lower()
        if key in seen or not os.path.exists(path):
            continue
        result.append(path)
        seen.add(key)
    return result


def _path_mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime
    except Exception:
        return 0.0


def _is_codex_desktop_root(app_root: str | Path) -> bool:
    try:
        root = Path(app_root)
        if (root / "resources" / "app.asar").is_file():
            return True
        if (root / "resources" / "app").is_dir():
            return True
        if (root / "Codex.exe").is_file():
            return True
        return False
    except Exception:
        return False


def _latest_windows_squirrel_app_dir(root: str | Path) -> Optional[str]:
    try:
        app_dirs = [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and re.match(r"app-", path.name, re.IGNORECASE)
        ]
    except Exception:
        return None
    if not app_dirs:
        return None
    def sort_key(path: Path) -> List[object]:
        return [
            int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)
        ]

    app_dirs.sort(key=sort_key)
    return str(app_dirs[-1])


def _windows_store_codex_root_candidates(package_root: str | Path) -> List[str]:
    root = Path(package_root)
    return [str(root / "app"), str(root)]


def _windows_codex_candidate_roots(root: str | Path) -> List[str]:
    root_path = Path(root)
    if not root_path.exists():
        return []
    candidates: List[str] = []
    try:
        entries = list(root_path.iterdir())
    except Exception:
        return candidates
    for entry in entries:
        if "codex" not in entry.name.lower():
            continue
        try:
            if not entry.is_dir():
                continue
        except Exception:
            continue
        candidates.append(str(entry))
        candidates.extend(_windows_store_codex_root_candidates(entry))
        latest = _latest_windows_squirrel_app_dir(entry)
        if latest:
            candidates.append(latest)
    return candidates


def _windows_store_codex_installs() -> List[str]:
    if os.name != "nt":
        return []
    import subprocess

    command = " ".join([
        "$pkgs = Get-AppxPackage | Where-Object {",
        "$_.Name -match 'Codex' -or $_.PackageFullName -match 'Codex' -or $_.InstallLocation -match 'Codex'",
        "} | Select-Object Name, InstallLocation;",
        "if ($pkgs) { $pkgs | ConvertTo-Json -Compress }",
    ])
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    output = (result.stdout or "").strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except Exception:
        return []
    rows = parsed if isinstance(parsed, list) else [parsed]
    installs: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        location = str(row.get("InstallLocation") or "").strip()
        if location:
            installs.extend(_windows_store_codex_root_candidates(location))
    return installs


def _codex_desktop_executable_from_root(app_root: str | Path) -> str:
    root = Path(app_root)
    if _is_codex_desktop_root(root):
        try:
            executables = [
                path
                for path in root.iterdir()
                if path.is_file() and path.suffix.lower() == ".exe" and "codex" in path.name.lower()
            ]
        except Exception:
            executables = []
        if executables:
            executables.sort(key=lambda p: (p.name.lower() != "codex.exe", p.name.lower()))
            return str(executables[0])
        fallback = root / "Codex.exe"
        return str(fallback) if fallback.exists() else ""
    # Fallback: if the root itself is a codex.exe, or contains one directly
    if root.is_file() and root.suffix.lower() == ".exe" and "codex" in root.name.lower():
        return str(root)
    try:
        for child in root.iterdir():
            if child.is_file() and child.suffix.lower() == ".exe" and child.name.lower() == "codex.exe":
                return str(child)
    except Exception:
        pass
    return ""


def find_codex_desktop_launchers(override: str = "") -> List[str]:
    """Return verified Codex Desktop launchers, excluding CLI shims."""
    candidates: List[str] = []
    if override:
        candidates.append(override)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
        named_dirs = [
            "Codex (Beta)",
            "Codex Beta",
            "codex-beta",
            "Codex",
            "codex",
        ]
        if local:
            candidates.extend(_windows_codex_candidate_roots(local))
            for name in named_dirs:
                candidates.append(str(Path(local) / "Programs" / name))
                candidates.append(str(Path(local) / name))
            candidates.extend(_windows_codex_candidate_roots(Path(local) / "Programs"))
        if program_files:
            for name in named_dirs:
                candidates.append(str(Path(program_files) / name))
            candidates.extend(_windows_codex_candidate_roots(Path(program_files) / "WindowsApps"))
            candidates.extend(_windows_codex_candidate_roots(program_files))
        if program_files_x86:
            for name in named_dirs:
                candidates.append(str(Path(program_files_x86) / name))
            candidates.extend(_windows_codex_candidate_roots(program_files_x86))
        candidates.extend(_windows_store_codex_installs())

    executables: List[str] = []
    for raw in candidates:
        path = Path(os.path.expandvars(str(raw).strip().strip('"')))
        if path.is_file() and path.suffix.lower() == ".exe":
            executable = str(path) if _is_codex_desktop_root(path.parent) else ""
        else:
            executable = _codex_desktop_executable_from_root(path)
        if executable:
            executables.append(executable)
    executables = _dedupe_existing_paths(executables)
    executables.sort(key=_path_mtime, reverse=True)
    return executables


def _windowsapps_codex_gui_candidates() -> List[str]:
    if os.name != "nt":
        return []
    return [path for path in find_codex_desktop_launchers() if "\\windowsapps\\" in path.replace("/", "\\").lower()]


def _looks_like_codex_gui_launcher(path: str) -> bool:
    candidate = Path(path)
    if candidate.name.lower() != "codex.exe":
        return False
    return _is_codex_desktop_root(candidate.parent)


def codex_launch_candidates(
    use_codex_plus_plus: bool = False,
    codex_plus_plus_path: str = "",
    codex_cli_path: str = "",
) -> List[str]:
    """Return visible GUI launchers first, then CLI shims as fallback."""
    cpp_paths: List[str] = []
    gui_paths: List[str] = []
    cli_paths: List[str] = []

    if use_codex_plus_plus:
        cpp_paths.extend([codex_plus_plus_path, CODEX_PLUS_PLUS_PATH])

    if codex_cli_path:
        if _looks_like_codex_gui_launcher(codex_cli_path):
            gui_paths.append(codex_cli_path)
        else:
            cli_paths.append(codex_cli_path)

    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        codex_app = os.path.join(appdata, "OpenAI", "Codex")
        bin_dir = os.path.join(codex_app, "bin")
        if os.path.exists(bin_dir):
            try:
                for d in sorted(os.listdir(bin_dir), reverse=True):
                    cli_paths.append(os.path.join(bin_dir, d, "codex.exe"))
            except Exception:
                pass

    gui_paths.extend(find_codex_desktop_launchers(codex_cli_path if _looks_like_codex_gui_launcher(codex_cli_path) else ""))

    env_cli = os.environ.get("CODEX_CLI_PATH", "")
    if env_cli:
        cli_paths.append(env_cli)

    which = shutil.which("codex")
    if which:
        cli_paths.append(which)

    return _dedupe_existing_paths(cpp_paths + gui_paths + cli_paths)


def _launch_codex_path(path: str, extra_args: Optional[List[str]] = None):
    import subprocess

    extra_args = extra_args or []
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    suffix = Path(path).suffix.lower()
    if os.name == "nt" and suffix in {".bat", ".cmd"}:
        return subprocess.Popen(
            ["cmd.exe", "/c", "start", "", path, *extra_args],
            creationflags=flags,
            close_fds=True,
        )
    return subprocess.Popen([path, *extra_args], creationflags=flags, close_fds=True)


def _wait_for_codex_start(process, timeout_seconds: float = 12.0) -> Tuple[bool, List[int], str]:
    """Wait briefly for a visible Codex process after launching."""
    deadline = time.monotonic() + max(float(timeout_seconds or 0), 0.5)
    last_exit_code = None
    while time.monotonic() < deadline:
        running, pids = is_codex_running(timeout=1)
        if running:
            return True, pids, ""
        try:
            if process is not None:
                last_exit_code = process.poll()
        except Exception:
            last_exit_code = None
        time.sleep(0.25)
    running, pids = is_codex_running(timeout=1)
    if running:
        return True, pids, ""
    if last_exit_code not in (None, 0):
        return False, [], f"启动器进程已退出，退出码 {last_exit_code}"
    return False, [], "启动命令已执行，但未检测到 Codex 进程"


def start_codex(
    use_codex_plus_plus: bool = False,
    codex_plus_plus_path: str = "",
    codex_cli_path: str = "",
    enable_cdp_injection: bool = False,
    cdp_port: int = 51236,
    backend_url: str = "",
) -> Tuple[bool, str]:
    """
    启动 Codex（或 Codex++）

    use_codex_plus_plus: 如果为 True，优先使用 Codex++ 启动器
    返回 (是否成功, 消息)
    """
    try:
        running, pids = is_codex_running(timeout=1)
        if running:
            return True, f"Codex 已在运行 (PID: {pids})"

        errors = []
        extra_args = []
        if enable_cdp_injection and not use_codex_plus_plus:
            extra_args = [f"--remote-debugging-port={int(cdp_port)}"]

        for path in codex_launch_candidates(use_codex_plus_plus, codex_plus_plus_path, codex_cli_path):
            try:
                process = _launch_codex_path(path, extra_args=extra_args)
                label = "Codex++" if "codex-plus-plus" in path.lower() else "Codex"
                started, started_pids, start_note = _wait_for_codex_start(process, timeout_seconds=12)
                if not started:
                    errors.append(f"{path}: {start_note}")
                    continue
                message = f"已启动 {label}: {path} (PID: {started_pids})"
                if enable_cdp_injection and not use_codex_plus_plus:
                    try:
                        from codex_injector import inject_codex_enhancements
                        injection = inject_codex_enhancements(
                            port=int(cdp_port),
                            backend_url=backend_url,
                            timeout_seconds=16,
                        )
                        if injection.get("success"):
                            message += f"；增强注入成功 ({injection.get('targets_injected')} 个窗口)"
                        else:
                            message += f"；增强注入未完成: {injection.get('error')}"
                    except Exception as inject_exc:
                        message += f"；增强注入失败: {inject_exc}"
                return True, message
            except Exception as exc:
                errors.append(f"{path}: {exc}")

        detail = "；".join(errors[:2])
        return False, "未找到可启动的 Codex，请在设置中填写 Codex 路径" + (f"（{detail}）" if detail else "")
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

        cur = conn.execute("PRAGMA table_info(threads)")
        columns = {row[1] for row in cur.fetchall()}
        if "id" not in columns:
            return 0, 0
        provider_cols = [c for c in ("model_provider", "modelProvider", "provider") if c in columns]
        model_cols = [c for c in ("model", "model_name", "modelName") if c in columns]
        update_cols = provider_cols + model_cols
        if not update_cols:
            return 0, 0

        select_cols = ["id"] + update_cols
        cur = conn.execute(f"SELECT {', '.join(select_cols)} FROM threads")
        rows = cur.fetchall()
        seen = len(rows)

        to_update = []
        for row in rows:
            row_id = row[0]
            values = dict(zip(select_cols, row))
            provider_changed = any(values.get(col) != target_provider for col in provider_cols)
            model_changed = any(values.get(col) != target_model for col in model_cols)
            if provider_changed or model_changed:
                to_update.append(row_id)
        updated = len(to_update)

        if to_update and not dry_run:
            try:
                conn.execute("BEGIN IMMEDIATE")
                assignments = ", ".join(f"{col} = ?" for col in update_cols)
                values = [target_provider] * len(provider_cols) + [target_model] * len(model_cols)
                conn.executemany(
                    f"UPDATE threads SET {assignments} WHERE id = ?",
                    ((*values, row_id) for row_id in to_update),
                )
                conn.commit()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower():
                    raise sqlite3.OperationalError("数据库被锁定：Codex 可能正在运行，请先关闭后再同步。") from e
                raise

        return seen, updated
    finally:
        conn.close()


_MAX_SYNC_FILE_MB = 50
_SKIP_SYNC_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv", ".env", "env"}


def sync_rollout_file(
    file_path: str,
    target_provider: str,
    target_model: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    同步单个 jsonl 文件中的所有 session_meta 记录
    返回 (是否修改了文件, 警告信息)
    """
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb > _MAX_SYNC_FILE_MB:
            return False, f"跳过超大文件 ({size_mb:.1f} MB): {file_path}"
    except Exception:
        pass

    changed = False
    saw_session_meta = False
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".codex_sync_", dir=os.path.dirname(file_path))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out_f:
            with open(file_path, "r", encoding="utf-8", errors="replace") as in_f:
                for raw_line in in_f:
                    line_body = raw_line.rstrip("\r\n")
                    newline = raw_line[len(line_body):]
                    if not line_body.strip():
                        out_f.write(raw_line)
                        continue
                    try:
                        record = json.loads(line_body)
                    except json.JSONDecodeError:
                        out_f.write(raw_line)
                        continue
                    if not isinstance(record, dict) or record.get("type") != "session_meta":
                        out_f.write(raw_line)
                        continue

                    saw_session_meta = True
                    if _apply_model_fields_to_session_meta(record, target_provider, target_model):
                        changed = True
                        out_f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + (newline or "\n"))
                    else:
                        out_f.write(raw_line)

        if changed and not dry_run:
            if os.path.exists(file_path):
                shutil.copystat(file_path, tmp_path, follow_symlinks=False)
            os.replace(tmp_path, file_path)
            tmp_path = ""

        return bool(saw_session_meta and changed), ""
    except Exception as e:
        return False, str(e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _apply_model_fields_to_session_meta(record: Dict, target_provider: str, target_model: str) -> bool:
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
    max_depth: int = 3,
) -> Tuple[int, int, List[str]]:
    """
    同步所有 rollout jsonl 文件
    返回 (seen, updated, warnings)
    """
    seen = 0
    updated = 0
    warnings: List[str] = []

    for base_dir in (sessions_dir, archived_dir):
        if not os.path.exists(base_dir):
            continue
        base_depth = base_dir.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(base_dir):
            # 深度限制
            current_depth = root.rstrip(os.sep).count(os.sep)
            if current_depth - base_depth >= max_depth:
                dirs[:] = []
                continue
            # 跳过隐藏目录和已知非会话目录
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in _SKIP_SYNC_DIRS
            ]
            for fname in files:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(root, fname)
                seen += 1
                try:
                    changed, warning = sync_rollout_file(fpath, target_provider, target_model, dry_run)
                    if changed:
                        updated += 1
                    if warning:
                        warnings.append(warning)
                except Exception as e:
                    warnings.append(f"{fpath}: {e}")

    return seen, updated, warnings


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

    seen = len(existing_entries)

    # 比较是否有变化：先流式写入临时文件，再与现有文件比较
    changed = False
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".codex_sync_idx_", dir=os.path.dirname(index_path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out_f:
            for entry in output:
                out_f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

        if os.path.exists(index_path):
            changed = not _files_equal(tmp_path, index_path)
        else:
            changed = os.path.getsize(tmp_path) > 0

        if changed and not dry_run:
            if os.path.exists(index_path):
                shutil.copystat(index_path, tmp_path, follow_symlinks=False)
            os.replace(tmp_path, index_path)
            tmp_path = ""
    except Exception:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    updated = len(output) if changed else 0
    return seen, updated, malformed


def _read_index_from_db(db_path: str, target_provider: str, target_model: str) -> Optional[List[Dict]]:
    """从 DB 读取 index 条目"""
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
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


def _files_equal(path_a: str, path_b: str, chunk_size: int = 8192) -> bool:
    """逐块比较两个文件内容，避免一次性加载大文件到内存。"""
    try:
        size_a = os.path.getsize(path_a)
        size_b = os.path.getsize(path_b)
        if size_a != size_b:
            return False
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            while True:
                ca = fa.read(chunk_size)
                cb = fb.read(chunk_size)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except Exception:
        return False


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
    db_path: str = "",
    sessions_dir: str = "",
    archived_dir: str = "",
    index_path: str = "",
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
    db_file = resolve_codex_db_path(str(home), db_path)
    if not config_path.exists() and db_file.parent.joinpath("config.toml").exists():
        config_path = db_file.parent / "config.toml"
    sessions_root = Path(os.path.expandvars(sessions_dir)).expanduser() if sessions_dir else home / "sessions"
    archived_root = Path(os.path.expandvars(archived_dir)).expanduser() if archived_dir else home / "archived_sessions"
    index_file = Path(os.path.expandvars(index_path)).expanduser() if index_path else home / "session_index.jsonl"

    # 1. 获取目标设置
    if not target_provider or not target_model:
        config = load_config_toml(str(config_path))
        target_provider = target_provider or config.get("model_provider", DEFAULT_PROVIDER)
        target_model = target_model or config.get("model", DEFAULT_MODEL)

    # 2. 同步 DB
    seen, updated = sync_state_database(str(db_file), target_provider, target_model, dry_run)
    stats.db_threads_seen = seen
    stats.db_threads_updated = updated

    # 3. 同步 jsonl 文件
    seen, updated, warnings = sync_rollout_files(
        str(sessions_root), str(archived_root),
        target_provider, target_model, dry_run
    )
    stats.rollout_files_seen = seen
    stats.rollout_files_updated = updated
    stats.errors.extend(warnings)

    # 4. 重建 session_index.jsonl
    seen, updated, malformed = sync_session_index(
        str(index_file), str(db_file),
        target_provider, target_model, dry_run
    )
    stats.index_rows_seen = seen
    stats.index_rows_updated = updated
    stats.malformed_lines = malformed

    return stats
