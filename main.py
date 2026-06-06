"""
main.py - Web 应用启动入口
启动 Flask 本地 HTTP 服务器 + 自动打开浏览器
"""
import sys
import webbrowser
import threading
from pathlib import Path

# 加入当前目录到模块搜索路径
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app


def open_browser(url: str):
    """延迟 1.5 秒后打开浏览器"""
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()


def main():
    app = create_app()
    port = 51234
    host = "127.0.0.1"
    url = f"http://{host}:{port}"
    open_browser(url)
    print(f"Codex History Manager 已启动: {url}")
    print("按 Ctrl+C 退出")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
