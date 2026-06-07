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
import threading
import traceback
import webview

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 51234
URL = f"http://127.0.0.1:{PORT}"
APP_TITLE = "Codex 历史记录管理器"
SMOKE_TEST_ARG = "--smoke-test"
SMOKE_TEST_ENV = "CODEX_ENHANCE_MANAGER_SMOKE_TEST"
WEBVIEW_MONITOR_BACKGROUND = "#111827"
MONITOR_WINDOW_WIDTH = 300
MONITOR_WINDOW_EXPANDED_HEIGHT = 226
MONITOR_WINDOW_COMPACT_HEIGHT = 92
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
IDYES = 6
IDNO = 7

tray_icon = None
allow_exit = False
main_window = None
monitor_window = None
desktop_api = None


class DesktopApi:
    def show_monitor(self):
        return _show_monitor()

    def hide_monitor(self):
        return _hide_monitor()

    def show_main(self):
        if main_window is not None:
            _show_window(main_window)

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
    app = create_app()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def _load_tray_image():
    """加载托盘图标，缺失时生成一个简单 fallback。"""
    if Image is None:
        return None
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
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
    except Exception:
        pass


def _hide_to_tray(window):
    if tray_icon is None:
        return
    try:
        window.hide()
        tray_icon.notify("已最小化到系统托盘", APP_TITLE)
    except Exception:
        pass


def _show_monitor(api=None):
    try:
        if monitor_window is None:
            return {
                "success": False,
                "error": "Monitor window is not ready. Please restart the desktop app.",
            }
        monitor_window.show()
        monitor_window.restore()
        _resize_monitor(MONITOR_WINDOW_WIDTH, MONITOR_WINDOW_EXPANDED_HEIGHT)
        return {"success": True}
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": str(exc)}


def _hide_monitor():
    try:
        if monitor_window is not None:
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
        killer = threading.Timer(1.2, lambda: os._exit(0))
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
    # 先尝试正常退出，让 Python 执行 atexit、刷新缓冲区等清理
    try:
        import sys
        sys.exit(0)
    except SystemExit:
        pass
    # 兜底：若 daemon 线程未结束导致 sys.exit 阻塞，强制终止进程
    os._exit(0)


def _ask_close_action(window) -> str:
    """Return tray, exit, or cancel for the close button."""
    if os.name == "nt":
        try:
            import ctypes
            result = ctypes.windll.user32.MessageBoxW(
                0,
                "选择“是”最小化到系统托盘。\n选择“否”直接退出程序。\n选择“取消”返回窗口。",
                APP_TITLE,
                0x00000003 | 0x00000040,
            )
            if result == IDYES:
                return "tray"
            if result == IDNO:
                return "exit"
            return "cancel"
        except Exception:
            pass

    choice = window.create_confirmation_dialog(
        "关闭 Codex 历史记录管理器",
        "选择“确定”最小化到系统托盘。\n选择“取消”返回窗口。"
    )
    return "tray" if choice else "cancel"


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

    def show_monitor_from_menu(icon=None, item=None):
        _show_monitor()

    def exit_from_menu(icon=None, item=None):
        _exit_app(window)

    tray_icon = pystray.Icon(
        "CodexHistoryManager",
        image,
        APP_TITLE,
        menu=pystray.Menu(
            pystray.MenuItem("显示窗口", show_from_menu, default=True),
            pystray.MenuItem("显示 Token 监控", show_monitor_from_menu),
            pystray.MenuItem("退出", exit_from_menu),
        ),
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


def _wait_for_flask(host="127.0.0.1", port=PORT, timeout=10):
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
    import socket
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (socket.error, OSError):
            time.sleep(0.1)
    return False


def _create_main_window(api):
    window = webview.create_window(
        title=APP_TITLE,
        url=URL,
        js_api=api,
        width=1280,
        height=800,
        min_size=(900, 600),
        confirm_close=False,
        text_select=True,
    )
    return window


def _create_monitor_window(api):
    monitor_x, monitor_y = _default_monitor_position()
    monitor = webview.create_window(
        title="Token Monitor",
        url=f"{URL}/monitor",
        js_api=api,
        width=MONITOR_WINDOW_WIDTH,
        height=MONITOR_WINDOW_EXPANDED_HEIGHT,
        x=monitor_x,
        y=monitor_y,
        resizable=False,
        frameless=True,
        easy_drag=True,
        on_top=True,
        transparent=False,
        background_color=WEBVIEW_MONITOR_BACKGROUND,
        hidden=True,
        focus=False,
        text_select=False,
    )
    return monitor


def _create_desktop_windows(api):
    return _create_main_window(api), _create_monitor_window(api)


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
      - hidden=True：Monitor 默认隐藏，通过托盘菜单或 API 唤起。

    Windows 平台特殊性：
      - gui="edgechromium"：在 Windows 上强制使用 Edge WebView2，而非 IE
        旧版渲染引擎。WebView2 基于 Chromium，支持现代 CSS/JS。
      - 若 Flask 启动超时（如端口被占用），打印错误并 sys.exit(1)，
        避免用户看到空白窗口。
    """
    global main_window, monitor_window, desktop_api
    # 启动 Flask 后台线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 轮询等待 Flask 就绪，最多 10 秒
    if not _wait_for_flask():
        print("Flask 启动超时，程序退出")
        sys.exit(1)

    # 创建 PyWebView 窗口（内嵌 Edge WebView2）
    desktop_api = DesktopApi()
    window = _create_main_window(desktop_api)
    monitor_window = _create_monitor_window(desktop_api)
    main_window = window
    _setup_tray(window)
    try:
        webview.start(gui="edgechromium")
    finally:
        # If the WebView loop returns without going through the tray Exit item,
        # ensure background tray/Flask threads do not keep a ghost process alive.
        _exit_app(window)


if __name__ == "__main__":
    if SMOKE_TEST_ARG in sys.argv:
        sys.exit(run_smoke_test())
    main()
