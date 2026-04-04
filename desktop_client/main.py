#!/usr/bin/env python3
"""
AstrBot 桌面助手独立客户端

独立运行的桌面悬浮球助手，通过 HTTP API 连接远程 AstrBot 服务器。

使用方法:
    python -m desktop_client

或:
    python desktop_client/main.py

命令行参数:
    -c, --config    配置文件路径
    -s, --server    服务器 URL (如 http://localhost:6185)
    -u, --username  登录用户名
"""

import sys
import os

# 确保父目录在 Python 路径中
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from desktop_client.app import main  # noqa: E402

if __name__ == "__main__":
    import logging
    from desktop_client.logger import configure_root_logger
    from desktop_client.config import ClientConfig

    # 配置日志系统（仅在直接运行 main.py 时需要）
    config_dir = ClientConfig.get_config_dir()
    log_file = os.path.join(config_dir, "logs", "desktop_client.log")

    configure_root_logger(
        level=logging.DEBUG,
        use_colors=True,
        log_file=log_file,
    )

    main()
