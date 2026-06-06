"""
main.py - 启动入口（PyWebView 内嵌窗口）
启动 Flask 后台线程 + PyWebView 内嵌浏览器窗口
"""
import sys
import os
import threading
import webview

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 51234
URL = f"http://127.0.0.1:{PORT}"


def start_flask():
    """在后台线程启动 Flask，不自动重载"""
    from app import create_app
    app = create_app()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def main():
    # 启动 Flask 后台线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 等待 Flask 启动
    import time
    time.sleep(2)

    # 创建 PyWebView 窗口（内嵌 Edge WebView2）
    webview.create_window(
        title="Codex 历史记录管理器",
        url=URL,
        width=1280,
        height=800,
        min_size=(900, 600),
        confirm_close=True,
        text_select=True,
    )
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
