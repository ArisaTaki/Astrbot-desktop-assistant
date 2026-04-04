"""
支持 python -m desktop_client 方式运行
"""

import sys
import warnings
import os
import logging

# ============================================================
# 添加路径（必须在最开始）
# ============================================================
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# ============================================================
# 依赖检查与自动安装（在导入其他模块之前）
# ============================================================
# 注意：dependency_checker 只使用标准库，不依赖第三方包
try:
    from desktop_client.dependency_checker import check_and_install_dependencies

    success, message = check_and_install_dependencies(
        auto_install=True,
        show_gui=True,
    )

    if not success:
        print(f"\n错误: {message}")
        print("请手动安装依赖: pip install -r requirements.txt")
        input("按回车键退出...")
        sys.exit(1)
    elif "安装" in message and "成功" in message:
        print(f"\n{message}")
        print("依赖已安装，正在启动应用...\n")
except ImportError as e:
    # dependency_checker 模块本身导入失败，继续尝试启动
    print(f"警告: 依赖检查模块加载失败: {e}")
except Exception as e:
    # 其他错误，记录但不阻止启动
    print(f"警告: 依赖检查过程出错: {e}")

# ============================================================
# 配置日志系统
# ============================================================
# 导入并配置日志
from desktop_client.logger import configure_root_logger  # noqa: E402
from desktop_client.config import ClientConfig  # noqa: E402

config_dir = ClientConfig.get_config_dir()
log_file = os.path.join(config_dir, "logs", "desktop_client.log")

configure_root_logger(
    level=logging.DEBUG,
    use_colors=True,
    log_file=log_file,
)

logger = logging.getLogger(__name__)

# ============================================================
# 抑制 httpcore 异步生成器清理警告
# 这是 httpcore/httpx 与 asyncio 结合使用时的已知问题
# https://github.com/encode/httpx/issues/2171
# ============================================================

# 抑制 warnings 模块的警告
warnings.filterwarnings("ignore", message="async generator ignored GeneratorExit")
warnings.filterwarnings("ignore", message="Attempted to exit cancel scope")

# 自定义异常钩子来过滤 httpcore 的 async generator 清理异常
_original_excepthook = sys.excepthook


def _custom_excepthook(exc_type, exc_value, exc_tb):
    """自定义异常钩子，过滤 httpcore 相关的异常"""
    # 检查是否是 httpcore async generator 清理异常
    if exc_type is RuntimeError:
        msg = str(exc_value)
        if "async generator ignored GeneratorExit" in msg:
            return  # 静默忽略
        if "Attempted to exit cancel scope" in msg:
            return  # 静默忽略

    # 其他异常正常处理
    _original_excepthook(exc_type, exc_value, exc_tb)


sys.excepthook = _custom_excepthook

# 同时处理 unraisable exceptions (Python 3.8+)
if hasattr(sys, "unraisablehook"):
    _original_unraisablehook = sys.unraisablehook

    def _custom_unraisablehook(unraisable):
        """自定义 unraisable 异常钩子"""
        exc_type = unraisable.exc_type
        exc_value = unraisable.exc_value

        # 检查是否是 httpcore async generator 清理异常
        if exc_type is RuntimeError:
            msg = str(exc_value) if exc_value else ""
            if "async generator ignored GeneratorExit" in msg:
                return  # 静默忽略
            if "Attempted to exit cancel scope" in msg:
                return  # 静默忽略

        # 检查对象名称是否包含 httpcore
        obj = unraisable.object
        if obj is not None:
            obj_repr = repr(obj)
            if "HTTP11ConnectionByteStream" in obj_repr:
                return  # 静默忽略 httpcore 流相关异常

        # 其他异常正常处理
        _original_unraisablehook(unraisable)

    sys.unraisablehook = _custom_unraisablehook

# ============================================================

logger.debug("正在加载模块")

try:
    logger.debug("导入 app 模块")
    from desktop_client.app import main

    logger.debug("模块导入成功")
except ImportError as e:
    logger.error(f"导入失败: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

if __name__ == "__main__":
    logger.debug("启动主函数")
    main()
