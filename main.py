"""
main.py - 启动入口（PyWebView 内嵌窗口）
启动 Flask 后台线程 + PyWebView 内嵌浏览器窗口

设计意图：
  - 将 Flask Web 应用打包为桌面程序，用户无需手动安装浏览器或记忆 URL。
  - PyWebView 使用系统原生 WebView（Windows 上为 Edge WebView2），
    体积远小于打包 Chromium，且能利用系统更新保持安全补丁最新。
  - 系统托盘（pystray）支持最小化到托盘而非退出，符合 Windows 桌面应用习惯。

工程权衡：
  - Flask 在 daemon 线程运行：主线程被 PyWebView 的事件循环占据，
    Flask 必须在后台线程启动。daemon=True 确保主窗口关闭时 Flask 线程不会阻止退出。
  - _wait_for_flask 轮询替代 time.sleep：旧版用固定 sleep(2)，在慢机器上可能
    不够、快机器上浪费启动时间；轮询精确检测端口就绪，最多等待 10 秒。
  - _exit_app 的双重退出机制：先 sys.exit(0) 执行清理（atexit、缓冲区刷新），
    若 daemon 线程阻塞导致不退出，再用 os._exit(0) 强制终止。这是 Windows 上
    处理遗留线程的可靠模式。

Windows 平台特殊性：
  - CREATE_NO_WINDOW = 0x08000000：创建子进程时不显示控制台窗口，
    用于托盘菜单等子命令。
  - ctypes.windll.user32.MessageBoxW：关闭窗口时弹出自定义系统对话框，
    提供「最小化到托盘 / 直接退出 / 取消」三选一，比 pywebview 默认对话框更灵活。
  - os._exit(0)：Windows 上 sys.exit 在某些情况下会被 C runtime 拦截，
    os._exit 绕过 Python 清理直接终止进程，作为最后兜底。
"""
import sys
import os
import json
import threading
import traceback
import urllib.error
import urllib.request
import queue
import socket
import time
import webview
from datetime import datetime
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from desktop_shortcuts import START_CODEX_ARG

DEFAULT_PORT = 51234
PORT = DEFAULT_PORT
URL = f"http://127.0.0.1:{PORT}"
APP_TITLE = "Codex 历史记录管理器"
SMOKE_TEST_ARG = "--smoke-test"
DESKTOP_LOG_RETAIN_COUNT = 3
DESKTOP_LAUNCH_ACTION_SHOW_WINDOW = "show_window"
DESKTOP_LAUNCH_ACTION_START_CODEX = "start_codex"
SMOKE_TEST_ENV = "CODEX_ENHANCE_MANAGER_SMOKE_TEST"
WEBVIEW_MONITOR_BACKGROUND = "#111827"
MONITOR_WINDOW_WIDTH = 360
MONITOR_WINDOW_EXPANDED_HEIGHT = 226
MONITOR_WINDOW_COMPACT_HEIGHT = 92
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
GWL_EXSTYLE = -20
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDYES = 6
IDNO = 7
LOCAL_API_TIMEOUT = 10
PORT_SCAN_LIMIT = 30
TRAY_MENU_TEXT = {
    "show_main": "显示主窗口",
    "show_settings": "打开设置",
    "show_monitor": "显示悬浮窗",
    "hide_monitor": "隐藏悬浮窗",
    "start_codex": "启动 Codex",
    "quick_switch_provider": "快速切换供应商",
    "auto_provider": "自动选择供应商",
    "no_providers": "暂无可切换供应商",
    "exit": "退出程序",
}

tray_icon = None
allow_exit = False
main_window = None
monitor_window = None
desktop_api = None
single_instance_lock = None
loaded_icon_handles: list[int] = []
pending_launch_action = DESKTOP_LAUNCH_ACTION_SHOW_WINDOW
desktop_log_handle = None


def _desktop_log_files(log_dir=None, retain_count: int = DESKTOP_LOG_RETAIN_COUNT) -> list[Path]:
    from app_paths import app_data_path

    safe_count = max(1, int(retain_count or 1))
    base_dir = Path(log_dir) if log_dir is not None else app_data_path("logs")
    files = [base_dir / "desktop.log"]
    files.extend(base_dir / f"desktop.{index}.log" for index in range(1, safe_count))
    return files


def _rotate_desktop_logs(log_dir=None, retain_count: int = DESKTOP_LOG_RETAIN_COUNT) -> Path:
    files = _desktop_log_files(log_dir=log_dir, retain_count=retain_count)
    files[0].parent.mkdir(parents=True, exist_ok=True)
    for index in range(len(files) - 1, 0, -1):
        if files[index].exists():
            files[index].unlink()
        if files[index - 1].exists():
            files[index - 1].replace(files[index])
    return files[0]


def _configure_desktop_run_logging(retain_count: int = DESKTOP_LOG_RETAIN_COUNT) -> dict:
    global desktop_log_handle
    if os.environ.get("CODEX_ENHANCE_MANAGER_DISABLE_RUN_LOGS") == "1":
        return {"success": True, "disabled": True}
    try:
        from app_paths import ensure_app_dirs

        ensure_app_dirs()
        log_path = _rotate_desktop_logs(retain_count=retain_count)
        desktop_log_handle = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = desktop_log_handle
        sys.stderr = desktop_log_handle
        print(f"Codex Enhance Manager started at {datetime.now().isoformat(timespec='seconds')}")
        print(f"argv={sys.argv}")
        return {
            "success": True,
            "path": str(log_path),
            "retained": [str(path) for path in _desktop_log_files(retain_count=retain_count)],
        }
    except Exception as exc:
        try:
            print(f"Desktop logging setup failed: {exc}")
        except Exception:
            pass
        return {"success": False, "error": str(exc)}


def _flush_desktop_run_logging() -> None:
    try:
        if desktop_log_handle is not None:
            desktop_log_handle.flush()
    except Exception:
        pass


def _resource_path(name: str) -> str:
    """Resolve bundled data files both in source and PyInstaller onefile mode."""
    candidates = []
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        candidates.append(os.path.join(bundle_dir, name))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), name))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return candidates[0] if candidates else name


def _start_pyinstaller_parent_watchdog() -> bool:
    """
    Exit the onefile child process if the PyInstaller launcher parent dies.

    PyInstaller onefile apps run through a small parent launcher that extracts
    files and starts the real Python child process. If the launcher is killed
    externally, Windows does not automatically kill the child; this watchdog
    prevents a ghost Flask/WebView process from staying behind.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    if not os.environ.get("_PYI_APPLICATION_HOME_DIR"):
        return False
    try:
        parent_pid = os.getppid()
    except Exception:
        return False
    if not parent_pid or parent_pid <= 0:
        return False

    def watch_parent():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            synchronize = 0x00100000
            infinite = 0xFFFFFFFF
            wait_object_0 = 0x00000000
            handle = kernel32.OpenProcess(synchronize, False, int(parent_pid))
            if not handle:
                return
            try:
                result = kernel32.WaitForSingleObject(handle, infinite)
                if result == wait_object_0:
                    os._exit(0)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass

    watcher = threading.Thread(target=watch_parent, name="pyinstaller-parent-watchdog", daemon=True)
    watcher.start()
    return True


def _desktop_backend_state_path():
    try:
        from app_paths import app_data_path, ensure_app_dirs

        ensure_app_dirs()
        return app_data_path("desktop_backend.json")
    except Exception:
        return None


def _write_desktop_backend_state(port: int) -> bool:
    path = _desktop_backend_state_path()
    if path is None:
        return False
    try:
        path.write_text(
            json.dumps({"port": int(port), "url": f"http://127.0.0.1:{int(port)}", "pid": os.getpid()}),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _pid_is_running(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _clear_desktop_backend_state(expected_pid: int | None = None) -> bool:
    path = _desktop_backend_state_path()
    if path is None or not path.exists():
        return False
    try:
        if expected_pid is not None:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("pid") or 0) != int(expected_pid):
                return False
        path.unlink()
        return True
    except Exception:
        return False


def _desktop_backend_port_candidates() -> list[int]:
    candidates: list[int] = []
    path = _desktop_backend_state_path()
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            port = int(data.get("port") or 0)
            pid = int(data.get("pid") or 0)
            if pid and not _pid_is_running(pid):
                _clear_desktop_backend_state(expected_pid=pid)
            elif 0 < port <= 65535:
                candidates.append(port)
        except Exception:
            pass
    candidates.append(DEFAULT_PORT)
    result: list[int] = []
    for port in candidates:
        if port not in result:
            result.append(port)
    return result


def _request_existing_desktop_start_codex(port: int) -> dict:
    try:
        body = json.dumps({"start_mode": "current_focus", "async": True}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}/api/codex/start",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=LOCAL_API_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {"success": True}
            return data if isinstance(data, dict) else {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _request_existing_desktop_action(action: str) -> bool:
    if action != DESKTOP_LAUNCH_ACTION_START_CODEX:
        return False
    for port in _desktop_backend_port_candidates():
        health = _desktop_backend_health(port)
        if not health.get("desktop_mode"):
            continue
        result = _request_existing_desktop_start_codex(port)
        if result.get("success") is not False:
            return True
    return False


def _existing_instance_responds(action: str = DESKTOP_LAUNCH_ACTION_SHOW_WINDOW) -> bool:
    for port in _desktop_backend_port_candidates():
        health = _desktop_backend_health(port)
        if health.get("desktop_mode"):
            if action == DESKTOP_LAUNCH_ACTION_START_CODEX:
                _request_existing_desktop_start_codex(port)
            else:
                _show_existing_desktop_window()
            return True
    return False


def _set_backend_port(port: int):
    """Update the local backend URL before creating WebView windows."""
    global PORT, URL
    PORT = int(port)
    URL = f"http://127.0.0.1:{PORT}"


def _desktop_backend_health(port: int = DEFAULT_PORT) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/api/health", timeout=0.8) as response:
            if response.status != 200:
                return {}
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _port_is_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", int(port))) != 0
    except Exception:
        return False


def _select_backend_port(preferred: int = DEFAULT_PORT) -> int:
    """Choose a backend port, skipping non-desktop services left by tests/dev servers."""
    health = _desktop_backend_health(preferred)
    if health.get("desktop_mode"):
        return int(preferred)
    if _port_is_free(preferred):
        return int(preferred)
    for offset in range(1, PORT_SCAN_LIMIT + 1):
        candidate = int(preferred) + offset
        if _port_is_free(candidate):
            return candidate
    raise RuntimeError(f"No free local port found near {preferred}.")


def _show_existing_desktop_window() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handles: list[int] = []

        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_proc(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value == APP_TITLE:
                    handles.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc_type(enum_proc), 0)
        for hwnd in handles:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        return False
    return False


def _acquire_single_instance_lock() -> bool:
    """Prevent duplicate desktop instances from creating extra WebView/tray processes."""
    global single_instance_lock
    if single_instance_lock is not None:
        return True
    try:
        import msvcrt
        from app_paths import app_data_path, ensure_app_dirs

        ensure_app_dirs()
        lock_path = app_data_path("manager.lock")
        lock_file = open(lock_path, "a+b")
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            return False
        single_instance_lock = lock_file
        return True
    except Exception:
        # If locking is unavailable, the HTTP preflight still prevents most duplicates.
        return True


class DesktopApi:
    def show_monitor(self):
        return _show_monitor()

    def hide_monitor(self):
        return _hide_monitor()

    def show_main(self):
        if main_window is not None:
            _show_window(main_window)
            return {"success": True}
        return {"success": False, "error": "Main window is not ready."}

    def show_settings(self):
        return _show_main_page("settings")

    def start_codex(self):
        return _start_codex_from_desktop()

    def exit_app(self):
        return _exit_app(main_window)

    def list_quick_providers(self):
        return _quick_switch_payload()

    def switch_provider(self, provider_id: str):
        return _set_focus_provider(provider_id)

    def resize_monitor(self, width: int = MONITOR_WINDOW_WIDTH, height: int = MONITOR_WINDOW_EXPANDED_HEIGHT):
        return _resize_monitor(width, height)

    def notify_monitor_alert(self, message: str):
        try:
            if tray_icon is not None:
                tray_icon.notify(message, APP_TITLE)
        except Exception:
            pass


def start_flask():
    """
    在后台线程启动 Flask，不自动重载。

    设计意图：
      - 使用 127.0.0.1 而非 localhost：避免 IPv6/IPv4 解析歧义，
        某些 Windows 配置下 localhost 解析为 ::1 导致连接失败。
      - debug=False, use_reloader=False：生产/打包环境必须关闭重载器，
        否则会在子进程中再次启动 Flask，导致端口冲突和窗口重复。
    """
    from app import create_app
    os.environ["CODEX_ENHANCE_MANAGER_DESKTOP"] = "1"
    os.environ["CODEX_ENHANCE_MANAGER_PORT"] = str(PORT)
    app = create_app()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


def _load_tray_image():
    """加载托盘图标，缺失时生成一个简单 fallback。"""
    if Image is None:
        return None
    icon_path = _resource_path("icon.ico")
    if os.path.exists(icon_path):
        return Image.open(icon_path)

    image = Image.new("RGBA", (256, 256), (12, 18, 32, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((36, 36, 220, 220), radius=44, fill=(37, 99, 235, 255))
    draw.ellipse((76, 74, 180, 178), fill=(16, 185, 129, 255))
    draw.rectangle((118, 44, 138, 212), fill=(255, 255, 255, 230))
    return image


def _show_window(window):
    try:
        window.show()
        window.restore()
        try:
            window.focus()
        except Exception:
            pass
    except Exception:
        pass


def _show_main_page(page: str = "") -> dict:
    if main_window is None:
        return {"success": False, "error": "Main window is not ready."}
    _show_window(main_window)
    safe_page = "".join(ch for ch in str(page or "") if ch.isalnum() or ch in {"-", "_"})
    if safe_page:
        try:
            main_window.evaluate_js(f"navigateTo({json.dumps(safe_page)})")
        except Exception:
            pass
    return {"success": True}


def _hide_to_tray(window) -> bool:
    if tray_icon is None:
        try:
            window.minimize()
            return True
        except Exception:
            return False
    try:
        window.hide()
        tray_icon.notify("已最小化到系统托盘", APP_TITLE)
        return True
    except Exception:
        return False


def _configured_close_action() -> str:
    try:
        from config import Config
        action = Config().get("close_button_action", "ask")
        if action in {"ask", "exit", "tray"}:
            return action
    except Exception:
        pass
    return "ask"


def _configured_launch_action() -> str:
    try:
        from config import Config
        action = Config().get("desktop_launch_action", DESKTOP_LAUNCH_ACTION_SHOW_WINDOW)
        if action in {DESKTOP_LAUNCH_ACTION_SHOW_WINDOW, DESKTOP_LAUNCH_ACTION_START_CODEX}:
            return action
    except Exception:
        pass
    return DESKTOP_LAUNCH_ACTION_SHOW_WINDOW


def _launch_action_from_args(argv: list[str] | None = None) -> str:
    args = list(sys.argv if argv is None else argv)
    if START_CODEX_ARG in args:
        return DESKTOP_LAUNCH_ACTION_START_CODEX
    return _configured_launch_action()


def _monitor_auto_show_enabled() -> bool:
    try:
        from config import Config
        return bool(Config().get("desktop_monitor_enabled", True))
    except Exception:
        return True


def _monitor_opacity() -> float:
    try:
        from config import Config
        value = float(Config().get("desktop_monitor_opacity", 88))
        if value > 1:
            value = value / 100
        return min(max(value, 0.35), 1.0)
    except Exception:
        return 0.88


def _format_monitor_number(value, *, compact: bool = False) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    if not compact:
        return f"{number:,}"
    abs_number = abs(number)
    for suffix, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs_number >= divisor:
            scaled = number / divisor
            text = f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{number:,}"


def _local_post_json(path: str, payload: dict | None = None) -> dict:
    try:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{URL}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=LOCAL_API_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            data = {"error": str(exc)}
        data.setdefault("success", False)
        return data
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _provider_registry_for_desktop():
    from config import Config
    from providers import ProviderRegistry

    config = Config()
    return ProviderRegistry(config.get("provider_store_path", ""))


def _official_provider_extra_for_desktop() -> list[dict]:
    try:
        from codex_config import CodexConfigManager
        from codex_official_provider import build_official_login_provider

        mgr = CodexConfigManager()
        provider = build_official_login_provider(mgr.read_config(), mgr.read_auth())
        return [provider] if provider else []
    except Exception:
        return []


def _quick_switch_payload() -> dict:
    try:
        registry = _provider_registry_for_desktop()
        payload = registry.list_providers(
            include_secrets=False,
            extra_providers=_official_provider_extra_for_desktop(),
        )
        focus_provider_id = str(payload.get("focus_provider_id") or "")
        providers = []
        for provider in payload.get("providers", []):
            if not isinstance(provider, dict) or provider.get("enabled") is False:
                continue
            providers.append({
                "id": provider.get("id", ""),
                "display_name": provider.get("display_name") or provider.get("id", ""),
                "short_alias": provider.get("short_alias", ""),
                "catalog_visibility": provider.get("catalog_visibility", "focused_only"),
                "focused": provider.get("id") == focus_provider_id,
            })
        return {"success": True, "focus_provider_id": focus_provider_id, "providers": providers}
    except Exception as exc:
        return {"success": False, "error": str(exc), "providers": []}


def _set_focus_provider(provider_id: str = "") -> dict:
    try:
        registry = _provider_registry_for_desktop()
        result = registry.set_focus_provider(
            provider_id,
            extra_providers=_official_provider_extra_for_desktop(),
        )
        if tray_icon is not None:
            try:
                tray_icon.update_menu()
            except Exception:
                pass
        return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _start_codex_from_desktop() -> dict:
    def worker():
        result = _local_post_json("/api/codex/start", {"start_mode": "current_focus"})
        try:
            if tray_icon is not None:
                if result.get("success"):
                    tray_icon.notify(result.get("message") or "Codex 已启动", APP_TITLE)
                else:
                    tray_icon.notify(result.get("error") or result.get("message") or "Codex 启动失败", APP_TITLE)
        except Exception:
            pass

    try:
        thread = threading.Thread(target=worker, name="codex-start-request", daemon=True)
        thread.start()
        return {"success": True, "message": "正在后台启动 Codex"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _place_monitor_window():
    if monitor_window is None:
        return
    try:
        x, y = _default_monitor_position()
        monitor_window.move(x, y)
    except Exception:
        pass


def _native_window_handle(window) -> int:
    handle_provider = getattr(window, "window_handle", None)
    if callable(handle_provider):
        try:
            handle = int(handle_provider() or 0)
            if handle:
                return handle
        except Exception:
            pass
    direct_handle = getattr(window, "hwnd", None)
    if direct_handle:
        try:
            return int(direct_handle)
        except Exception:
            pass
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return 0
    try:
        return int(handle.ToInt64())
    except Exception:
        try:
            return int(handle.ToInt32())
        except Exception:
            try:
                return int(handle)
            except Exception:
                return 0


def _owned_window_handles_by_title(title: str, visible_only: bool = False) -> list[int]:
    if os.name != "nt":
        return []
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        current_pid = os.getpid()
        handles: list[int] = []

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def callback(hwnd, lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == current_pid:
                if visible_only and not user32.IsWindowVisible(hwnd):
                    return True
                buffer = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buffer, len(buffer))
                if buffer.value == title:
                    handles.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        return handles
    except Exception:
        return []


def _native_monitor_window_handle(window=None) -> int:
    """Prefer the visible Tk top-level HWND over Tk child handles."""
    target = window or monitor_window
    for hwnd in _owned_window_handles_by_title("Token Monitor", visible_only=True):
        if hwnd:
            return hwnd
    hwnd = _native_window_handle(target)
    if hwnd:
        return hwnd
    handles = _owned_window_handles_by_title("Token Monitor")
    return handles[0] if handles else 0


def _apply_monitor_style_to_hwnd(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        style = int(get_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        style &= ~WS_EX_APPWINDOW
        set_long(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception:
        return False


def _set_window_icon_for_hwnd(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    icon_path = _resource_path("icon.ico")
    if not os.path.exists(icon_path):
        return False
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [
            ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.LPCWSTR,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.LoadImageW.restype = ctypes.wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.c_uint,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.wintypes.LPARAM

        large_icon = user32.LoadImageW(
            None,
            icon_path,
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        small_icon = user32.LoadImageW(
            None,
            icon_path,
            IMAGE_ICON,
            16,
            16,
            LR_LOADFROMFILE,
        )
        applied = False
        if large_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, int(large_icon))
            loaded_icon_handles.append(int(large_icon))
            applied = True
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, int(small_icon))
            loaded_icon_handles.append(int(small_icon))
            applied = True
        return applied
    except Exception:
        return False


def _apply_main_window_icon(window=None) -> bool:
    """Apply the bundled icon to the visible WebView window after native creation."""
    if os.name != "nt":
        return False
    target = window or main_window
    handles = []
    native_hwnd = _native_window_handle(target)
    if native_hwnd:
        handles.append(native_hwnd)
    handles.extend(hwnd for hwnd in _owned_window_handles_by_title(APP_TITLE) if hwnd not in handles)
    return any(_set_window_icon_for_hwnd(hwnd) for hwnd in handles)


def _apply_rounded_region_to_hwnd(hwnd: int, width: int, height: int, radius: int = 28) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        import ctypes.wintypes

        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = ctypes.wintypes.HRGN
        user32.SetWindowRgn.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HRGN, ctypes.c_bool]
        user32.SetWindowRgn.restype = ctypes.c_int
        region = gdi32.CreateRoundRectRgn(0, 0, int(width) + 1, int(height) + 1, int(radius), int(radius))
        if not region:
            return False
        # After SetWindowRgn succeeds, Windows owns the region handle.
        return bool(user32.SetWindowRgn(hwnd, region, True))
    except Exception:
        return False


def _apply_monitor_native_style(window=None) -> bool:
    """Make the monitor a real Windows tool window: no taskbar button, topmost."""
    if os.name != "nt":
        return False
    target = window or monitor_window
    handles = []
    native_hwnd = _native_monitor_window_handle(target)
    if native_hwnd:
        handles.append(native_hwnd)
    handles.extend(hwnd for hwnd in _owned_window_handles_by_title("Token Monitor") if hwnd not in handles)
    applied = any(_apply_monitor_style_to_hwnd(hwnd) for hwnd in handles)
    try:
        native = getattr(target, "native", None)
        if native is not None:
            try:
                native.ShowInTaskbar = False
                native.TopMost = True
            except Exception:
                pass
    except Exception:
        pass
    return applied


def _set_monitor_native_position(window=None) -> bool:
    if os.name != "nt":
        return False
    target = window or monitor_window
    hwnd = _native_monitor_window_handle(target)
    if not hwnd:
        return False
    try:
        import ctypes

        x, y = _default_monitor_position()
        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            int(x),
            int(y),
            MONITOR_WINDOW_WIDTH,
            MONITOR_WINDOW_EXPANDED_HEIGHT,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        return True
    except Exception:
        return False


def _prepare_monitor_before_show(window=None):
    target = window or monitor_window
    native = getattr(target, "native", None)
    if native is not None:
        try:
            native.ShowInTaskbar = False
            native.TopMost = True
        except Exception:
            pass
    _apply_monitor_native_style(target)


def _ensure_monitor_window():
    global monitor_window
    if monitor_window is not None:
        return monitor_window
    if desktop_api is None:
        return None
    try:
        monitor_window = _create_monitor_window(desktop_api)
    except Exception:
        traceback.print_exc()
        monitor_window = None
    return monitor_window


def _show_monitor(api=None):
    try:
        window = _ensure_monitor_window()
        if window is None:
            return {
                "success": False,
                "error": "Monitor window is not ready. Please restart the desktop app.",
            }
        _prepare_monitor_before_show(window)
        try:
            window.on_top = True
        except Exception:
            pass
        try:
            window.show()
            window.restore()
        except Exception:
            pass
        _prepare_monitor_before_show(window)
        if not _set_monitor_native_position(window):
            _place_monitor_window()
        _resize_monitor(MONITOR_WINDOW_WIDTH, MONITOR_WINDOW_EXPANDED_HEIGHT)
        _prepare_monitor_before_show(window)
        return {"success": True, "message": "Monitor window requested."}
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": str(exc)}


def _hide_monitor():
    try:
        if monitor_window is not None:
            if monitor_window.__class__.__name__ == "NativeTokenMonitor":
                monitor_window.hide()
                return {"success": True}
            hwnd = _native_window_handle(monitor_window)
            if os.name == "nt" and hwnd:
                try:
                    import ctypes

                    ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
                    return {"success": True}
                except Exception:
                    pass
            monitor_window.hide()
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _resize_monitor(width: int, height: int) -> dict[str, int]:
    safe_width = min(max(int(width or MONITOR_WINDOW_WIDTH), 260), 520)
    safe_height = min(max(int(height or MONITOR_WINDOW_EXPANDED_HEIGHT), MONITOR_WINDOW_COMPACT_HEIGHT), 360)
    try:
        if monitor_window is not None:
            monitor_window.resize(safe_width, safe_height)
    except Exception:
        try:
            if monitor_window is not None:
                monitor_window.set_window_size(safe_width, safe_height)
        except Exception:
            pass
    return {"width": safe_width, "height": safe_height}


def _default_monitor_position() -> tuple[int, int]:
    width = MONITOR_WINDOW_WIDTH
    margin = 28
    fallback = (960, 96)
    if os.name != "nt":
        return fallback
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        spi_get_work_area = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(spi_get_work_area, 0, ctypes.byref(rect), 0):
            return (
                max(int(rect.right) - width - margin, margin),
                max(int(rect.top) + margin, margin),
            )
    except Exception:
        pass
    return fallback


class NativeTokenMonitor:
    """A lightweight native floating monitor that avoids a second WebView2."""

    def __init__(self, api, hidden: bool = False):
        self.api = api
        self.hidden = bool(hidden)
        self.transparent = False
        self.background_color = WEBVIEW_MONITOR_BACKGROUND
        self.initial_x, self.initial_y = _default_monitor_position()
        self.width = MONITOR_WINDOW_WIDTH
        self.height = MONITOR_WINDOW_EXPANDED_HEIGHT
        self.on_top = True
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root = None
        self._labels: dict[str, object] = {}
        self._drag_start = (0, 0)
        self._refresh_in_flight = False
        self._speed_samples: list[tuple[float, int]] = []

    def _start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="native-token-monitor", daemon=True)
        self._thread.start()

    def show(self):
        self._start()
        self._commands.put(("show",))

    def restore(self):
        self.show()

    def hide(self):
        self._commands.put(("hide",))

    def destroy(self):
        self._commands.put(("destroy",))

    def move(self, x, y):
        self.initial_x = int(x)
        self.initial_y = int(y)
        self._commands.put(("move", self.initial_x, self.initial_y))

    def resize(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self._commands.put(("resize", self.width, self.height))

    def set_window_size(self, width, height):
        self.resize(width, height)

    def window_handle(self) -> int:
        try:
            if self._root is not None:
                return int(self._root.winfo_id())
        except Exception:
            pass
        return 0

    def _run(self):
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except Exception:
            return

        root = tk.Tk()
        self._root = root
        root.title("Token Monitor")
        root.configure(bg=WEBVIEW_MONITOR_BACKGROUND)
        root.geometry(f"{self.width}x{self.height}+{self.initial_x}+{self.initial_y}")
        root.resizable(False, False)
        root.overrideredirect(True)
        icon_path = _resource_path("icon.ico")
        if os.path.exists(icon_path):
            try:
                root.iconbitmap(icon_path)
            except Exception:
                pass
        try:
            root.attributes("-topmost", True)
            root.attributes("-alpha", _monitor_opacity())
            root.wm_attributes("-toolwindow", True)
        except Exception:
            pass

        title_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        value_font = tkfont.Font(family="Consolas", size=24, weight="bold")
        small_font = tkfont.Font(family="Segoe UI", size=8)
        frame = tk.Frame(root, bg=WEBVIEW_MONITOR_BACKGROUND, padx=14, pady=12)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=WEBVIEW_MONITOR_BACKGROUND)
        header.pack(fill="x")
        tk.Label(header, text="Token Monitor", fg="#93c5fd", bg=WEBVIEW_MONITOR_BACKGROUND, font=title_font).pack(side="left")
        close_btn = tk.Label(header, text="×", fg="#94a3b8", bg=WEBVIEW_MONITOR_BACKGROUND, font=("Segoe UI", 12, "bold"), cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda event: self.hide())

        self._labels["value"] = tk.Label(frame, text="--", fg="#f8fafc", bg=WEBVIEW_MONITOR_BACKGROUND, font=value_font)
        self._labels["value"].pack(anchor="w", pady=(8, 2))
        self._labels["context"] = tk.Label(frame, text="上下文 Context: --", fg="#cbd5e1", bg=WEBVIEW_MONITOR_BACKGROUND, font=small_font)
        self._labels["context"].pack(anchor="w")
        self._labels["speed"] = tk.Label(frame, text="速度 Speed: --", fg="#bae6fd", bg=WEBVIEW_MONITOR_BACKGROUND, font=small_font)
        self._labels["speed"].pack(anchor="w", pady=(3, 0))
        self._labels["quota"] = tk.Label(frame, text="额度 Quota: --", fg="#fbbf24", bg=WEBVIEW_MONITOR_BACKGROUND, font=small_font)
        self._labels["quota"].pack(anchor="w", pady=(3, 0))
        self._labels["cache"] = tk.Label(frame, text="缓存复用 Reuse: --", fg="#94a3b8", bg=WEBVIEW_MONITOR_BACKGROUND, font=small_font)
        self._labels["cache"].pack(anchor="w", pady=(3, 0))
        self._labels["updated"] = tk.Label(frame, text="更新时间 Updated: --", fg="#64748b", bg=WEBVIEW_MONITOR_BACKGROUND, font=small_font)
        self._labels["updated"].pack(anchor="w", pady=(3, 0))

        menu = tk.Menu(root, tearoff=0)

        def show_context_menu(event):
            self._rebuild_menu(menu)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
            return "break"

        def start_drag(event):
            self._drag_start = (event.x_root - root.winfo_x(), event.y_root - root.winfo_y())

        def drag(event):
            x = event.x_root - self._drag_start[0]
            y = event.y_root - self._drag_start[1]
            self.initial_x = x
            self.initial_y = y
            root.geometry(f"+{x}+{y}")

        for widget in (root, frame, header, self._labels["value"], self._labels["context"], self._labels["speed"], self._labels["quota"], self._labels["cache"], self._labels["updated"]):
            widget.bind("<ButtonRelease-3>", show_context_menu)
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", drag)

        self._apply_native_treatment()
        if self.hidden:
            root.withdraw()
        else:
            self._show_root()
        root.after(100, self._process_commands)
        root.after(300, self._refresh_stats)
        root.mainloop()

    def _show_root(self):
        root = self._root
        if root is None:
            return
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.geometry(f"{self.width}x{self.height}+{self.initial_x}+{self.initial_y}")
            self._apply_native_treatment()
            self._schedule_native_treatment()
        except Exception:
            pass

    def _apply_native_treatment(self):
        root = self._root
        if root is None:
            return
        try:
            root.update_idletasks()
            root.attributes("-topmost", True)
            root.attributes("-alpha", _monitor_opacity())
        except Exception:
            pass
        try:
            _apply_monitor_native_style(self)
            hwnd = _native_monitor_window_handle(self)
            if hwnd:
                _apply_rounded_region_to_hwnd(hwnd, self.width, self.height)
        except Exception:
            pass

    def _schedule_native_treatment(self):
        root = self._root
        if root is None:
            return
        for delay in (80, 250):
            try:
                root.after(delay, self._apply_native_treatment)
            except Exception:
                pass

    def _process_commands(self):
        root = self._root
        if root is None:
            return
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            action = command[0]
            if action == "show":
                self.hidden = False
                self._show_root()
                self._refresh_stats()
            elif action == "hide":
                self.hidden = True
                root.withdraw()
            elif action == "move":
                root.geometry(f"+{command[1]}+{command[2]}")
            elif action == "resize":
                root.geometry(f"{command[1]}x{command[2]}+{self.initial_x}+{self.initial_y}")
                self._apply_native_treatment()
                self._schedule_native_treatment()
            elif action == "stats":
                self._refresh_in_flight = False
                ok = bool(command[1])
                payload = command[2] if len(command) > 2 else {}
                if ok:
                    self._apply_stats(payload)
                    root.after(10000, self._refresh_stats)
                else:
                    self._labels["updated"].configure(text="更新时间 Updated: reconnecting")
                    root.after(2000, self._refresh_stats)
            elif action == "destroy":
                root.destroy()
                return
        root.after(100, self._process_commands)

    def _refresh_stats(self):
        root = self._root
        if root is None:
            return
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        thread = threading.Thread(target=self._fetch_stats_worker, name="native-monitor-token-refresh", daemon=True)
        thread.start()

    def _fetch_stats_worker(self):
        try:
            with urllib.request.urlopen(
                f"{URL}/api/token/current?rollout_total_source=1&rollout_scan_fallback=1",
                timeout=12,
            ) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            self._commands.put(("stats", True, data))
        except Exception:
            self._commands.put(("stats", False, {}))

    def _apply_stats(self, data: dict):
        total = int(data.get("total_tokens") or data.get("current_total_tokens") or 0)
        context_used = data.get("current_context_used_tokens")
        context_window = data.get("current_context_window")
        cache_total = data.get("cache_total_tokens") or data.get("cache_total") or 0
        speed = self._remember_speed_sample(total)
        self._labels["value"].configure(text=_format_monitor_number(total, compact=True))
        if context_used and context_window:
            self._labels["context"].configure(
                text=f"上下文 Context: {_format_monitor_number(context_used)} / {_format_monitor_number(context_window)}"
            )
        else:
            self._labels["context"].configure(text="上下文 Context: --")
        if speed:
            self._labels["speed"].configure(text=f"速度 Speed: {_format_monitor_number(speed, compact=True)} tok/min")
        else:
            self._labels["speed"].configure(text="速度 Speed: --")
        self._labels["quota"].configure(text=self._format_quota_snapshot(data.get("quota")))
        self._labels["cache"].configure(text=f"缓存复用 Reuse: {_format_monitor_number(cache_total)}")
        self._labels["updated"].configure(text="更新时间 Updated: now")

    def _format_quota_snapshot(self, snapshot: dict | None) -> str:
        if not isinstance(snapshot, dict) or snapshot.get("success") is False:
            return "额度 Quota: --"
        values = snapshot.get("values") if isinstance(snapshot.get("values"), dict) else {}
        tiers = values.get("tiers") if isinstance(values.get("tiers"), list) else []
        parts: list[str] = []
        for tier in tiers[:3]:
            if not isinstance(tier, dict):
                continue
            raw = tier.get("utilization")
            if raw is None and tier.get("remaining_percent") is not None:
                try:
                    raw = 100 - float(tier.get("remaining_percent") or 0)
                except Exception:
                    raw = None
            try:
                pct = float(raw)
            except Exception:
                continue
            name = str(tier.get("name") or "quota").replace("_", " ")
            parts.append(f"{name} {pct:.0f}%")
        if not parts:
            raw = values.get("quota_percent") or values.get("utilization")
            try:
                pct = float(raw)
                parts.append(f"{pct:.0f}%")
            except Exception:
                pass
        return "额度 Quota: " + (" / ".join(parts) if parts else "--")

    def _remember_speed_sample(self, total: int) -> int | None:
        now = time.time()
        self._speed_samples.append((now, int(total or 0)))
        self._speed_samples = [
            sample for sample in self._speed_samples if now - sample[0] <= 60
        ][-20:]
        if len(self._speed_samples) < 2:
            return None
        first_ts, first_total = self._speed_samples[0]
        last_ts, last_total = self._speed_samples[-1]
        elapsed_minutes = max((last_ts - first_ts) / 60, 0)
        if elapsed_minutes <= 0:
            return None
        return max(int(round((last_total - first_total) / elapsed_minutes)), 0)

    def _run_menu_action(self, callback):
        def runner():
            try:
                callback()
            except Exception:
                pass
        threading.Thread(target=runner, name="native-monitor-menu-action", daemon=True).start()

    def _rebuild_menu(self, menu):
        menu.delete(0, "end")
        menu.add_command(label=TRAY_MENU_TEXT["start_codex"], command=lambda: self._run_menu_action(_start_codex_from_desktop))
        menu.add_command(label=TRAY_MENU_TEXT["show_main"], command=lambda: self._run_menu_action(lambda: _show_window(main_window) if main_window else None))
        menu.add_command(label=TRAY_MENU_TEXT["show_settings"], command=lambda: self._run_menu_action(lambda: _show_main_page("settings")))
        providers = _quick_switch_payload().get("providers") or []
        if providers:
            provider_menu = menu.__class__(menu, tearoff=0)
            provider_menu.add_command(label=TRAY_MENU_TEXT["auto_provider"], command=lambda: self._run_menu_action(lambda: _set_focus_provider("")))
            provider_menu.add_separator()
            for provider in providers:
                provider_id = str(provider.get("id") or "")
                label = provider.get("display_name") or provider_id
                alias = provider.get("short_alias")
                if alias:
                    label = f"{label} ({alias})"
                provider_menu.add_command(label=label, command=lambda pid=provider_id: self._run_menu_action(lambda: _set_focus_provider(pid)))
            menu.add_cascade(label=TRAY_MENU_TEXT["quick_switch_provider"], menu=provider_menu)
        menu.add_separator()
        menu.add_command(label=TRAY_MENU_TEXT["hide_monitor"], command=self.hide)
        menu.add_command(label=TRAY_MENU_TEXT["exit"], command=lambda: self._run_menu_action(lambda: _exit_app(main_window)))


def _exit_app(window):
    """
    退出应用程序。

    设计意图：
      - 优雅退出优先：先停止托盘图标、销毁窗口、执行 sys.exit(0)，
        让 Python 有机会运行 atexit 回调、刷新 stdout/stderr 缓冲区。
      - 强制兜底：若存在未结束的 daemon 线程（如 Flask 后台线程），
        sys.exit(0) 可能阻塞不返回；此时 os._exit(0) 直接终止进程。

    Windows 平台特殊性：
      - os._exit 绕过 Python 的清理逻辑，直接调用 C 库 _exit()，
        在 Windows 上能确保进程立即终止，即使有挂起的 Win32 句柄。
      - 注意：os._exit 不会触发 atexit 和 finally 块，因此只在 sys.exit
        失败后才调用。

    Args:
        window: 主窗口对象（当前未使用，保留接口一致性）。
    """
    global allow_exit
    allow_exit = True
    # Some tray/WebView callbacks can block during shutdown on Windows. Arm a
    # short hard-exit fallback before best-effort cleanup so Exit always exits.
    try:
        killer = threading.Timer(0.25, lambda: os._exit(0))
        killer.daemon = True
        killer.start()
    except Exception:
        pass
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass
    for candidate in (monitor_window, window):
        try:
            if candidate is not None:
                candidate.destroy()
        except Exception:
            pass
    _clear_desktop_backend_state(expected_pid=os.getpid())
    _flush_desktop_run_logging()
    # 先尝试正常退出，让 Python 执行 atexit、刷新缓冲区等清理
    try:
        os._exit(0)
    except Exception:
        pass
    os._exit(0)


def _ask_close_action(window) -> str:
    """Return tray, exit, or cancel for the close button."""
    configured = _configured_close_action()
    if configured in {"exit", "tray"}:
        return configured

    if os.name == "nt":
        try:
            import ctypes
            result = ctypes.windll.user32.MessageBoxW(
                0,
                "选择“是”退出程序。\n选择“否”缩小到系统托盘。\n选择“取消”返回窗口。",
                APP_TITLE,
                0x00000003 | 0x00000040,
            )
            if result == IDYES:
                return "exit"
            if result == IDNO:
                return "tray"
            return "cancel"
        except Exception:
            pass

    choice = window.create_confirmation_dialog(
        "关闭 Codex 历史记录管理器",
        "选择“确定”退出程序。\n选择“取消”缩小到系统托盘。"
    )
    return "exit" if choice else "tray"


def _setup_tray(window):
    """创建系统托盘菜单。pystray 缺失时自动降级为普通窗口。"""
    global tray_icon
    if pystray is None:
        return

    image = _load_tray_image()
    if image is None:
        return

    def show_from_menu(icon=None, item=None):
        _show_window(window)

    def show_settings_from_menu(icon=None, item=None):
        _show_main_page("settings")

    def show_monitor_from_menu(icon=None, item=None):
        _show_monitor()

    def hide_monitor_from_menu(icon=None, item=None):
        _hide_monitor()

    def start_codex_from_menu(icon=None, item=None):
        _start_codex_from_desktop()

    def exit_from_menu(icon=None, item=None):
        _exit_app(window)

    def provider_menu_items():
        payload = _quick_switch_payload()
        providers = payload.get("providers") or []
        focus_provider_id = str(payload.get("focus_provider_id") or "")
        if not providers:
            yield pystray.MenuItem(TRAY_MENU_TEXT["no_providers"], None, enabled=False)
            return

        def clear_focus(icon, item):
            _set_focus_provider("")

        def auto_is_checked(item):
            return not _quick_switch_payload().get("focus_provider_id")

        yield pystray.MenuItem(
            TRAY_MENU_TEXT["auto_provider"],
            clear_focus,
            checked=auto_is_checked,
            radio=True,
            enabled=bool(focus_provider_id),
        )
        yield pystray.Menu.SEPARATOR
        for provider in providers:
            provider_id = str(provider.get("id") or "")
            label = provider.get("display_name") or provider_id
            alias = provider.get("short_alias")
            if alias:
                label = f"{label} ({alias})"
            focused = bool(provider.get("focused"))

            def make_select_provider(pid):
                def select_provider(icon, item):
                    _set_focus_provider(pid)
                return select_provider

            def make_provider_checked(pid):
                def provider_is_checked(item):
                    return _quick_switch_payload().get("focus_provider_id") == pid
                return provider_is_checked

            yield pystray.MenuItem(
                label,
                make_select_provider(provider_id),
                checked=make_provider_checked(provider_id),
                radio=True,
                enabled=not focused,
            )

    def build_menu():
        return pystray.Menu(
            pystray.MenuItem(TRAY_MENU_TEXT["show_main"], show_from_menu, default=True),
            pystray.MenuItem(TRAY_MENU_TEXT["show_settings"], show_settings_from_menu),
            pystray.MenuItem(TRAY_MENU_TEXT["show_monitor"], show_monitor_from_menu),
            pystray.MenuItem(TRAY_MENU_TEXT["hide_monitor"], hide_monitor_from_menu),
            pystray.MenuItem(TRAY_MENU_TEXT["start_codex"], start_codex_from_menu),
            pystray.MenuItem(TRAY_MENU_TEXT["quick_switch_provider"], pystray.Menu(provider_menu_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(TRAY_MENU_TEXT["exit"], exit_from_menu),
        )

    tray_icon = pystray.Icon(
        "CodexHistoryManager",
        image,
        APP_TITLE,
        menu=build_menu(),
    )
    tray_icon.run_detached()

    def on_closing():
        if allow_exit:
            return True
        action = _ask_close_action(window)
        if action == "tray":
            _hide_to_tray(window)
        elif action == "exit":
            _exit_app(window)
        return False

    def on_minimized():
        if not allow_exit:
            _hide_to_tray(window)

    window.events.closing += on_closing
    window.events.minimized += on_minimized


def _wait_for_flask(host="127.0.0.1", port=None, timeout=10):
    """
    轮询等待 Flask 服务就绪，替代脆弱的 time.sleep(2)。

    设计意图：
      - 精确检测：通过 socket.create_connection 尝试连接，确认 Flask 已真正
        绑定端口并开始接受请求，而非仅仅线程已启动。
      - 退避策略：每 100ms 重试一次，0.5 秒连接超时，在响应速度和 CPU 占用
        之间平衡。

    边界条件：
      - 若 Flask 启动失败（如端口被占用），轮询 10 秒后返回 False，
        main() 中会打印错误并 sys.exit(1)。
      - Windows 防火墙首次拦截时可能短暂阻塞，10 秒通常足够用户授权。

    Args:
        host: 监听地址，默认 127.0.0.1。
        port: 监听端口，默认 PORT(51234)。
        timeout: 最大等待秒数。

    Returns:
        是否在超时前检测到服务就绪。
    """
    import time
    target_port = PORT if port is None else int(port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, target_port), timeout=0.5):
                health = _desktop_backend_health(target_port)
                if health.get("desktop_mode"):
                    return True
        except (socket.error, OSError):
            pass
        time.sleep(0.1)
    return False


def _create_main_window(api, hidden: bool = False):
    window = webview.create_window(
        title=APP_TITLE,
        url=URL,
        js_api=api,
        width=1280,
        height=800,
        min_size=(900, 600),
        hidden=bool(hidden),
        confirm_close=False,
        text_select=True,
        background_color="#0f172a",
    )
    try:
        window.events.shown += lambda window=window: _apply_main_window_icon(window)
    except Exception:
        pass
    return window


def _create_monitor_window(api, hidden: bool | None = None):
    should_hide = not _monitor_auto_show_enabled() if hidden is None else bool(hidden)
    return NativeTokenMonitor(api, hidden=should_hide)


def _create_desktop_windows(api, hide_main: bool = False):
    return _create_main_window(api, hidden=hide_main), _create_monitor_window(api)


def _on_webview_started():
    """Run deferred desktop startup actions after WebView is ready."""
    started_action = False
    if pending_launch_action == DESKTOP_LAUNCH_ACTION_START_CODEX:
        try:
            starter = threading.Timer(0.8, _start_codex_from_desktop)
            starter.daemon = True
            starter.start()
            started_action = True
        except Exception:
            pass
    if not _monitor_auto_show_enabled():
        return started_action
    try:
        timer = threading.Timer(2.5, _show_monitor)
        timer.daemon = True
        timer.start()
        return True
    except Exception:
        return started_action


def _smoke_test_webview_window_creation() -> bool:
    original_windows = list(webview.windows)
    try:
        _create_desktop_windows(DesktopApi())
        return True
    except Exception:
        traceback.print_exc()
        return False
    finally:
        webview.windows[:] = original_windows


def run_smoke_test() -> int:
    """
    Run a packaged-EXE smoke test without opening the WebView window.

    The release workflow launches the built EXE with --smoke-test. Keeping this
    in the real entrypoint verifies PyInstaller hidden imports, Flask app
    construction, and bundled static files while avoiding GUI flakiness in CI.
    """
    os.environ[SMOKE_TEST_ENV] = "1"
    try:
        if not _smoke_test_webview_window_creation():
            print("Smoke test failed: WebView window options are invalid")
            return 1

        from app import create_app

        flask_app = create_app()
        checks = [
            ("/", "html"),
            ("/monitor", "html"),
            ("/api/diagnostics", "json"),
        ]
        with flask_app.test_client() as client:
            for path, expected in checks:
                response = client.get(path)
                if response.status_code != 200:
                    print(f"Smoke test failed: {path} returned {response.status_code}")
                    return 1
                if expected == "json":
                    payload = response.get_json(silent=True)
                    if not isinstance(payload, dict) or "providers" not in payload:
                        print(f"Smoke test failed: {path} did not return diagnostics JSON")
                        return 1
                elif not response.get_data():
                    print(f"Smoke test failed: {path} returned an empty body")
                    return 1
                elif expected == "html":
                    body = response.get_data(as_text=True)
                    broken_markers = (
                        "?/title>",
                        "?/span>",
                        "?/button>",
                        "?/div>",
                        "?/p>",
                        "?/label>",
                        "?/option>",
                        "?/h1>",
                        "?/h2>",
                        "?/h3>",
                    )
                    if any(marker in body for marker in broken_markers):
                        print(f"Smoke test failed: {path} contains broken HTML closing tags")
                        return 1
                    if path == "/" and "</title>" not in body.split("</head>", 1)[0]:
                        print("Smoke test failed: / is missing a valid </title> before </head>")
                        return 1
        print("Packaged EXE smoke test passed.")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def main():
    """
    应用程序主入口。

    启动流程：
      1. 在 daemon 线程启动 Flask 后端服务。
      2. 轮询等待 Flask 端口就绪（最多 10 秒）。
      3. 创建 PyWebView 主窗口（内嵌 Edge WebView2）和 Token Monitor 悬浮窗。
      4. 设置系统托盘菜单。
      5. 进入 PyWebView 事件循环（阻塞直到窗口关闭）。

    窗口设计：
      - 主窗口 1280x800：适合大多数笔记本屏幕，min_size 保证内容不溢出。
      - Monitor 窗口 300x178：小型悬浮窗，frameless + transparent 实现无边框
        透明效果，on_top 始终置顶，供用户实时查看 token 用量。
      - hidden=False：Monitor 默认显示；若用户在设置中关闭，则启动时隐藏。

    Windows 平台特殊性：
      - gui="edgechromium"：在 Windows 上强制使用 Edge WebView2，而非 IE
        旧版渲染引擎。WebView2 基于 Chromium，支持现代 CSS/JS。
      - 若 Flask 启动超时（如端口被占用），打印错误并 sys.exit(1)，
        避免用户看到空白窗口。
    """
    global main_window, monitor_window, desktop_api, pending_launch_action
    pending_launch_action = _launch_action_from_args()
    _configure_desktop_run_logging()
    if not _acquire_single_instance_lock():
        if not _request_existing_desktop_action(pending_launch_action):
            _show_existing_desktop_window()
        print("Codex Enhance Manager is already running or still shutting down.")
        return
    if _existing_instance_responds(pending_launch_action):
        print("Codex Enhance Manager is already running.")
        return
    try:
        _set_backend_port(_select_backend_port(DEFAULT_PORT))
        _write_desktop_backend_state(PORT)
    except Exception as exc:
        print(f"Unable to choose local backend port: {exc}")
        sys.exit(1)
    _start_pyinstaller_parent_watchdog()
    # 启动 Flask 后台线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 轮询等待 Flask 就绪，最多 10 秒
    if not _wait_for_flask():
        print("Flask 启动超时，程序退出")
        sys.exit(1)

    # 创建 PyWebView 窗口（内嵌 Edge WebView2）
    desktop_api = DesktopApi()
    window = _create_main_window(desktop_api, hidden=pending_launch_action == DESKTOP_LAUNCH_ACTION_START_CODEX)
    main_window = window
    _setup_tray(window)
    try:
        from app_paths import app_data_path, ensure_app_dirs
        ensure_app_dirs()
        webview.start(
            _on_webview_started,
            gui="edgechromium",
            storage_path=str(app_data_path("webview_data")),
        )
    finally:
        # If the WebView loop returns without going through the tray Exit item,
        # ensure background tray/Flask threads do not keep a ghost process alive.
        _exit_app(window)


if __name__ == "__main__":
    if SMOKE_TEST_ARG in sys.argv:
        sys.exit(run_smoke_test())
    main()
