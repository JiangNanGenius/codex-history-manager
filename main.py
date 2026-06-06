"""
main.py - 启动入口（PyWebView 内嵌窗口）
启动 Flask 后台线程 + PyWebView 内嵌浏览器窗口
"""
import sys
import os
import threading
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
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
IDYES = 6
IDNO = 7

tray_icon = None
allow_exit = False


def start_flask():
    """在后台线程启动 Flask，不自动重载"""
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


def _exit_app(window):
    global allow_exit
    allow_exit = True
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass
    try:
        window.destroy()
    except Exception:
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

    def exit_from_menu(icon=None, item=None):
        _exit_app(window)

    tray_icon = pystray.Icon(
        "CodexHistoryManager",
        image,
        APP_TITLE,
        menu=pystray.Menu(
            pystray.MenuItem("显示窗口", show_from_menu, default=True),
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


def main():
    # 启动 Flask 后台线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 等待 Flask 启动
    import time
    time.sleep(2)

    # 创建 PyWebView 窗口（内嵌 Edge WebView2）
    window = webview.create_window(
        title=APP_TITLE,
        url=URL,
        width=1280,
        height=800,
        min_size=(900, 600),
        confirm_close=False,
        text_select=True,
    )
    _setup_tray(window)
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
