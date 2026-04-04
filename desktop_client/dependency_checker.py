"""
依赖检查与自动安装模块

在应用启动时检测必要依赖是否已安装，如果缺失则自动安装。
"""

import subprocess
import sys
import importlib
import importlib.util
from typing import List, Tuple, Optional
from pathlib import Path


# 核心依赖列表：(模块名, pip包名, 是否必需)
# 模块名用于 import 检测，pip包名用于安装
CORE_DEPENDENCIES = [
    # GUI 框架
    ("PySide6", "PySide6>=6.5.0", True),
    ("qasync", "qasync>=0.27.1", True),
    # HTTP 客户端
    ("httpx", "httpx[http2]>=0.24.0", True),
    ("httpx_sse", "httpx-sse>=0.4.0", True),
    # WebSocket
    ("websockets", "websockets>=11.0.0", True),
    # 截图
    ("PIL", "Pillow>=9.0.0", True),
    ("mss", "mss>=9.0.0", True),
    # 系统信息
    ("psutil", "psutil>=5.9.0", True),
    # 全局快捷键
    ("pynput", "pynput>=1.7.0", False),
    # 配置管理
    ("pydantic", "pydantic>=2.0.0", True),
    # 工具
    ("dateutil", "python-dateutil>=2.8.0", True),
    # Markdown
    ("markdown", "markdown>=3.4.0", True),
    ("pygments", "pygments>=2.15.0", True),
]

# Windows 专用依赖
WINDOWS_DEPENDENCIES = [
    ("win32api", "pywin32>=306", False),
]

# macOS 专用依赖
MACOS_DEPENDENCIES = [
    ("objc", "pyobjc-framework-Cocoa>=9.0", False),
]


def check_module_installed(module_name: str) -> bool:
    """检查模块是否已安装

    Args:
        module_name: 模块名（用于 import）

    Returns:
        bool: 模块是否可导入
    """
    try:
        # 使用 importlib.util.find_spec 检查模块是否存在
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except (ModuleNotFoundError, ImportError, ValueError):
        return False


def get_missing_dependencies() -> List[Tuple[str, str, bool]]:
    """获取缺失的依赖列表

    Returns:
        List[Tuple[str, str, bool]]: 缺失的依赖列表 (模块名, pip包名, 是否必需)
    """
    missing = []

    # 检查核心依赖
    for module_name, pip_name, required in CORE_DEPENDENCIES:
        if not check_module_installed(module_name):
            missing.append((module_name, pip_name, required))

    # 检查平台专用依赖
    if sys.platform == "win32":
        for module_name, pip_name, required in WINDOWS_DEPENDENCIES:
            if not check_module_installed(module_name):
                missing.append((module_name, pip_name, required))
    elif sys.platform == "darwin":
        for module_name, pip_name, required in MACOS_DEPENDENCIES:
            if not check_module_installed(module_name):
                missing.append((module_name, pip_name, required))

    return missing


def install_package(pip_name: str, quiet: bool = False) -> bool:
    """安装单个包

    Args:
        pip_name: pip 包名（可包含版本约束）
        quiet: 是否静默安装

    Returns:
        bool: 安装是否成功
    """
    try:
        cmd = [sys.executable, "-m", "pip", "install", pip_name]
        if quiet:
            cmd.append("-q")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )

        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def install_missing_dependencies(
    missing: List[Tuple[str, str, bool]],
    progress_callback: Optional[callable] = None,
) -> Tuple[List[str], List[str]]:
    """安装缺失的依赖

    Args:
        missing: 缺失的依赖列表
        progress_callback: 进度回调函数，接收 (当前索引, 总数, 包名, 状态) 参数

    Returns:
        Tuple[List[str], List[str]]: (成功安装的包列表, 安装失败的包列表)
    """
    success = []
    failed = []
    total = len(missing)

    for i, (module_name, pip_name, required) in enumerate(missing):
        if progress_callback:
            progress_callback(i, total, pip_name, "installing")

        if install_package(pip_name):
            success.append(pip_name)
            if progress_callback:
                progress_callback(i, total, pip_name, "success")
        else:
            failed.append(pip_name)
            if progress_callback:
                progress_callback(i, total, pip_name, "failed")

    return success, failed


def check_and_install_dependencies(
    auto_install: bool = True,
    show_gui: bool = True,
) -> Tuple[bool, str]:
    """检查并安装依赖

    Args:
        auto_install: 是否自动安装缺失的依赖
        show_gui: 是否显示 GUI 进度（需要 tkinter）

    Returns:
        Tuple[bool, str]: (是否成功, 消息)
    """
    missing = get_missing_dependencies()

    if not missing:
        return True, "所有依赖已安装"

    # 分离必需和可选依赖
    required_missing = [(m, p, r) for m, p, r in missing if r]
    optional_missing = [(m, p, r) for m, p, r in missing if not r]

    if not auto_install:
        missing_names = [p for _, p, _ in missing]
        return False, f"缺失依赖: {', '.join(missing_names)}"

    # 尝试使用 GUI 显示进度
    if show_gui:
        try:
            return _install_with_gui(required_missing, optional_missing)
        except Exception:
            # GUI 失败，回退到命令行
            pass

    # 命令行模式安装
    return _install_cli(required_missing, optional_missing)


def _install_cli(
    required: List[Tuple[str, str, bool]],
    optional: List[Tuple[str, str, bool]],
) -> Tuple[bool, str]:
    """命令行模式安装依赖"""
    print("\n" + "=" * 60)
    print("  AstrBot Desktop Assistant - 依赖检查")
    print("=" * 60)

    all_missing = required + optional

    if required:
        print(f"\n发现 {len(required)} 个必需依赖缺失:")
        for _, pip_name, _ in required:
            print(f"  - {pip_name}")

    if optional:
        print(f"\n发现 {len(optional)} 个可选依赖缺失:")
        for _, pip_name, _ in optional:
            print(f"  - {pip_name}")

    print("\n正在自动安装依赖...")

    def progress(i, total, name, status):
        status_text = {
            "installing": "安装中",
            "success": "成功",
            "failed": "失败",
        }.get(status, status)
        print(f"  [{i+1}/{total}] {name}: {status_text}")

    success, failed = install_missing_dependencies(all_missing, progress)

    if failed:
        # 检查是否有必需依赖安装失败
        required_failed = [f for f in failed if any(f == p for _, p, r in required if r)]
        if required_failed:
            msg = f"必需依赖安装失败: {', '.join(required_failed)}"
            print(f"\n错误: {msg}")
            print("请手动运行: pip install -r requirements.txt")
            return False, msg
        else:
            msg = f"可选依赖安装失败: {', '.join(failed)}，但不影响核心功能"
            print(f"\n警告: {msg}")
            return True, msg

    print(f"\n成功安装 {len(success)} 个依赖")
    return True, f"成功安装 {len(success)} 个依赖"


def _install_with_gui(
    required: List[Tuple[str, str, bool]],
    optional: List[Tuple[str, str, bool]],
) -> Tuple[bool, str]:
    """使用 tkinter GUI 显示安装进度"""
    import tkinter as tk
    from tkinter import ttk
    import threading

    all_missing = required + optional
    total = len(all_missing)

    # 创建窗口
    root = tk.Tk()
    root.title("AstrBot Desktop Assistant - 依赖安装")
    root.geometry("500x200")
    root.resizable(False, False)

    # 居中显示
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 500) // 2
    y = (root.winfo_screenheight() - 200) // 2
    root.geometry(f"+{x}+{y}")

    # 标题
    title_label = tk.Label(
        root,
        text="正在安装缺失的依赖...",
        font=("Microsoft YaHei", 12, "bold"),
    )
    title_label.pack(pady=20)

    # 当前包名
    package_var = tk.StringVar(value="准备中...")
    package_label = tk.Label(root, textvariable=package_var)
    package_label.pack()

    # 进度条
    progress_var = tk.DoubleVar(value=0)
    progress_bar = ttk.Progressbar(
        root,
        variable=progress_var,
        maximum=100,
        length=400,
    )
    progress_bar.pack(pady=20)

    # 状态
    status_var = tk.StringVar(value=f"0/{total}")
    status_label = tk.Label(root, textvariable=status_var)
    status_label.pack()

    # 结果存储
    result = {"success": True, "message": ""}

    def update_progress(i, total, name, status):
        package_var.set(f"{name}: {status}")
        progress_var.set((i + 1) / total * 100)
        status_var.set(f"{i + 1}/{total}")
        root.update()

    def install_thread():
        nonlocal result
        success, failed = install_missing_dependencies(all_missing, update_progress)

        if failed:
            required_failed = [f for f in failed if any(f == p for _, p, r in required if r)]
            if required_failed:
                result["success"] = False
                result["message"] = f"必需依赖安装失败: {', '.join(required_failed)}"
            else:
                result["success"] = True
                result["message"] = f"可选依赖安装失败: {', '.join(failed)}"
        else:
            result["success"] = True
            result["message"] = f"成功安装 {len(success)} 个依赖"

        root.after(500, root.destroy)

    # 启动安装线程
    thread = threading.Thread(target=install_thread, daemon=True)
    thread.start()

    # 运行 GUI
    root.mainloop()

    return result["success"], result["message"]


def upgrade_pip() -> bool:
    """升级 pip 到最新版本"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-q"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    # 测试依赖检查
    print("检查依赖...")
    missing = get_missing_dependencies()

    if missing:
        print(f"缺失 {len(missing)} 个依赖:")
        for module, pip_name, required in missing:
            req_str = "[必需]" if required else "[可选]"
            print(f"  {req_str} {module} ({pip_name})")
    else:
        print("所有依赖已安装")
