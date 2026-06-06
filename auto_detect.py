"""
auto_detect.py - 自动检测 Codex 相关路径
自动发现 Codex 数据库、CLI、Codex++ 和 Sessions 目录
"""
import os
import glob
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def detect_codex_db() -> str:
    """自动检测 Codex SQLite 数据库路径（优先最大编号）"""
    candidates = []

    # 1. ~/.codex/ 下的 state_*.sqlite
    codex_home = Path.home() / ".codex"
    if codex_home.exists():
        candidates.extend(glob.glob(str(codex_home / "state_*.sqlite")))

    # 2. Windows: %LOCALAPPDATA%/OpenAI/Codex/
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidates.extend(glob.glob(os.path.join(local_appdata, "OpenAI", "Codex", "state_*.sqlite")))

    # 3. Windows: %APPDATA%/OpenAI/Codex/
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.extend(glob.glob(os.path.join(appdata, "OpenAI", "Codex", "state_*.sqlite")))

    if not candidates:
        return ""

    # 过滤只保留存在的文件，选最大编号的
    valid = [c for c in candidates if os.path.isfile(c)]
    if not valid:
        return ""

    # 按 state_N.sqlite 中的 N 排序，取最大的
    def extract_number(path: str) -> int:
        name = os.path.basename(path)
        # state_5.sqlite -> 5
        base = name.replace("state_", "").replace(".sqlite", "")
        try:
            return int(base)
        except ValueError:
            return 0

    valid.sort(key=extract_number, reverse=True)
    return valid[0]


def detect_codex_cli() -> str:
    """自动检测 Codex CLI 路径"""
    # 1. 从 config.toml 的 mcp_servers.CODEX_CLI_PATH 读取
    try:
        config_data = read_codex_config()
        cli_path = config_data.get("codex_cli_path", "")
        if cli_path and os.path.isfile(cli_path):
            return cli_path
    except Exception:
        pass

    # 2. %LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        bin_dir = os.path.join(local_appdata, "OpenAI", "Codex", "bin")
        if os.path.isdir(bin_dir):
            for d in os.listdir(bin_dir):
                exe = os.path.join(bin_dir, d, "codex.exe")
                if os.path.isfile(exe):
                    return exe
        # 也检查直接在 Codex 目录下
        direct_exe = os.path.join(local_appdata, "OpenAI", "Codex", "codex.exe")
        if os.path.isfile(direct_exe):
            return direct_exe

    # 3. where codex (PATH search)
    try:
        result = subprocess.run(
            ["where", "codex"],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip().splitlines()[0].strip()
            if os.path.isfile(path):
                return path
    except Exception:
        pass

    # 4. %PROGRAMFILES%/OpenAI/Codex/codex.exe
    prog_files = os.environ.get("PROGRAMFILES", "")
    if prog_files:
        exe = os.path.join(prog_files, "OpenAI", "Codex", "codex.exe")
        if os.path.isfile(exe):
            return exe

    return ""


def detect_codex_plus_plus() -> str:
    """自动检测 Codex++ 安装路径"""
    # 1. %LOCALAPPDATA%/Programs/Codex++/codex-plus-plus.exe
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        exe = os.path.join(local_appdata, "Programs", "Codex++", "codex-plus-plus.exe")
        if os.path.isfile(exe):
            return exe

    # 2. 注册表搜索 Uninstall 键
    try:
        import winreg
        for hive_key in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            for sub_key in [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                           r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]:
                try:
                    with winreg.OpenKey(hive_key, sub_key) as key:
                        i = 0
                        while True:
                            try:
                                subkey_name = winreg.EnumKey(key, i)
                                i += 1
                                with winreg.OpenKey(key, subkey_name) as sk:
                                    try:
                                        display_name = winreg.QueryValueEx(sk, "DisplayName")[0]
                                        if "codex" in display_name.lower() and "++" in display_name:
                                            install_loc = winreg.QueryValueEx(sk, "InstallLocation")[0]
                                            exe = os.path.join(install_loc, "codex-plus-plus.exe")
                                            if os.path.isfile(exe):
                                                return exe
                                    except Exception:
                                        continue
                            except OSError:
                                break
                except Exception:
                    continue
    except Exception:
        pass

    # 3. %PROGRAMFILES%/Codex++/codex-plus-plus.exe
    prog_files = os.environ.get("PROGRAMFILES", "")
    if prog_files:
        exe = os.path.join(prog_files, "Codex++", "codex-plus-plus.exe")
        if os.path.isfile(exe):
            return exe

    return ""


def detect_sessions_dir() -> str:
    """自动检测 Codex sessions 目录"""
    codex_home = Path.home() / ".codex"

    # 优先检测 sessions
    sessions_dir = codex_home / "sessions"
    if sessions_dir.is_dir():
        return str(sessions_dir)

    return ""


def detect_archived_dir() -> str:
    """自动检测 Codex archived_sessions 目录"""
    codex_home = Path.home() / ".codex"
    archived_dir = codex_home / "archived_sessions"
    if archived_dir.is_dir():
        return str(archived_dir)
    return ""


def read_codex_config() -> Dict:
    """读取 ~/.codex/config.toml 获取当前 model_provider 和 model"""
    codex_home = Path.home() / ".codex"
    config_path = codex_home / "config.toml"

    if not config_path.exists():
        return {
            "model_provider": "",
            "model": "",
            "codex_cli_path": "",
        }

    try:
        import tomllib
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except ImportError:
        # Python < 3.11 fallback
        try:
            import tomli
            with open(config_path, "rb") as f:
                data = tomli.load(f)
        except ImportError:
            # 简易行解析
            return _simple_toml_parse(config_path)
    except Exception:
        return _simple_toml_parse(config_path)

    result = {
        "model_provider": "",
        "model": "",
        "codex_cli_path": "",
    }

    # 顶层字段
    for key in ("model_provider", "modelProvider", "provider"):
        if key in data:
            result["model_provider"] = data[key]
            break
    for key in ("model",):
        if key in data:
            result["model"] = data[key]
            break

    # defaults 子表
    defaults = data.get("defaults", {})
    if not result["model_provider"] and "model_provider" in defaults:
        result["model_provider"] = defaults["model_provider"]
    if not result["model"] and "model" in defaults:
        result["model"] = defaults["model"]

    # mcp_servers.CODEX_CLI_PATH
    mcp = data.get("mcp_servers", {})
    if isinstance(mcp, dict):
        for server_key, server_val in mcp.items():
            if isinstance(server_val, dict) and "command" in server_val:
                cmd = server_val["command"]
                if "codex" in str(cmd).lower():
                    result["codex_cli_path"] = str(cmd)
                    break

    return result


def _simple_toml_parse(config_path: Path) -> Dict:
    """简易 TOML 行解析（只解析顶层 key=value）"""
    result = {
        "model_provider": "",
        "model": "",
        "codex_cli_path": "",
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                if key in ("model_provider", "modelProvider", "provider"):
                    result["model_provider"] = value
                elif key == "model":
                    result["model"] = value
    except Exception:
        pass
    return result


def detect_all() -> Dict:
    """检测所有路径，返回完整字典"""
    return {
        "db_path": detect_codex_db(),
        "codex_cli_path": detect_codex_cli(),
        "codex_plus_plus_path": detect_codex_plus_plus(),
        "sessions_dir": detect_sessions_dir(),
        "archived_dir": detect_archived_dir(),
        "codex_config": read_codex_config(),
    }
