"""
python -m webgui 启动本地控制台。

环境变量：
  GUI_HOST  监听地址，默认 127.0.0.1（仅本机）
  GUI_PORT  端口，默认 5000
"""

import os

from .app import create_app


def main() -> None:
    host = os.getenv("GUI_HOST", "127.0.0.1")
    port = int(os.getenv("GUI_PORT", "5000"))
    app = create_app()
    print(f"芯片资讯控制台已启动 → http://{host}:{port}")
    # threaded=True 为后续 SSE 实时日志预留并发；debug=False 避免 reloader 双进程
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
