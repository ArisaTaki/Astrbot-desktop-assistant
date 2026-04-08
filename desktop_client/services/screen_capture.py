"""
屏幕捕获服务

提供全屏截图、区域截图、窗口截图等功能。
"""

import os
import logging
import subprocess
import sys
import tempfile
import time
from typing import Optional, Tuple, Callable
from io import BytesIO

try:
    import mss
    import mss.tools

    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import Quartz

    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False


class ScreenCaptureService:
    """屏幕捕获服务"""

    def __init__(self, save_dir: str = "./temp/screenshots"):
        """
        初始化屏幕捕获服务

        Args:
            save_dir: 截图保存目录
        """
        self.save_dir = save_dir
        self.last_error_message = ""
        self._logger = logging.getLogger(__name__)
        os.makedirs(save_dir, exist_ok=True)

        if not HAS_MSS:
            self._logger.warning("mss 库未安装，截图功能不可用")
        if not HAS_PIL:
            self._logger.warning("Pillow 库未安装，截图功能不可用")

    def get_last_error(self) -> str:
        return self.last_error_message

    def _set_last_error(self, message: str) -> None:
        self.last_error_message = message

    def _check_macos_screen_capture_access(
        self, request_if_needed: bool = False
    ) -> bool:
        if sys.platform != "darwin" or not HAS_QUARTZ:
            return True

        try:
            access_granted = Quartz.CGPreflightScreenCaptureAccess()
            self._logger.debug(
                "macOS 屏幕录制权限预检查: granted=%s", access_granted
            )
            if access_granted:
                return True

            if request_if_needed and hasattr(Quartz, "CGRequestScreenCaptureAccess"):
                try:
                    self._logger.info("尝试请求 macOS 屏幕录制权限")
                    Quartz.CGRequestScreenCaptureAccess()
                except Exception as e:
                    self._logger.warning("请求 macOS 屏幕录制权限失败: %s", e)
                access_granted = bool(Quartz.CGPreflightScreenCaptureAccess())
                self._logger.debug(
                    "macOS 屏幕录制权限复检: granted=%s", access_granted
                )
                if access_granted:
                    return True
        except Exception as e:
            self._logger.warning("检测 macOS 屏幕录制权限失败: %s", e)

        self._set_last_error(
            "截图失败：macOS 未授予屏幕录制权限。请在“系统设置 > 隐私与安全性 > 屏幕录制”中允许 AstrBot Desktop Assistant 后重试。"
        )
        return False

    def capture_full_screen(self) -> Optional[Image.Image]:
        """
        捕获全屏

        Returns:
            PIL Image 对象，失败返回 None
        """
        self._set_last_error("")
        if not HAS_PIL:
            self._set_last_error("截图失败：Pillow 未安装")
            return None

        try:
            if sys.platform == "darwin":
                self._logger.debug("macOS 全屏截图开始")
                if not self._check_macos_screen_capture_access(request_if_needed=True):
                    return None
                return self._capture_full_screen_macos()

            if not HAS_MSS:
                self._set_last_error("截图失败：mss 未安装")
                return None
            with mss.mss() as sct:
                # 获取所有显示器的组合
                monitor = sct.monitors[0]  # 0 是所有显示器的组合
                screenshot = sct.grab(monitor)
                return Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                )
        except Exception as e:
            self._set_last_error(f"全屏截图失败: {e}")
            self._logger.exception("全屏截图失败")
            return None

    def capture_monitor(self, monitor_index: int = 1) -> Optional[Image.Image]:
        """
        捕获指定显示器

        Args:
            monitor_index: 显示器索引，从 1 开始

        Returns:
            PIL Image 对象，失败返回 None
        """
        self._set_last_error("")
        if not HAS_PIL:
            self._set_last_error("截图失败：Pillow 未安装")
            return None

        try:
            if sys.platform == "darwin":
                # macOS 上优先走系统 screencapture，mss 常出现只抓到桌面层的问题。
                self._logger.debug("macOS 指定显示器截图退化为全屏截图")
                return self.capture_full_screen()

            if not HAS_MSS:
                self._set_last_error("截图失败：mss 未安装")
                return None
            with mss.mss() as sct:
                if monitor_index < 1 or monitor_index >= len(sct.monitors):
                    self._set_last_error(f"截图失败：显示器索引 {monitor_index} 无效")
                    self._logger.warning("显示器索引无效: %s", monitor_index)
                    return None
                monitor = sct.monitors[monitor_index]
                screenshot = sct.grab(monitor)
                return Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                )
        except Exception as e:
            self._set_last_error(f"显示器截图失败: {e}")
            self._logger.exception("显示器截图失败")
            return None

    def _capture_full_screen_macos(self) -> Optional[Image.Image]:
        """使用 macOS 原生 screencapture 抓取当前屏幕内容。"""
        fd, temp_path = tempfile.mkstemp(prefix="astrbot_capture_", suffix=".png")
        os.close(fd)
        self._logger.debug("准备调用 screencapture: temp_path=%s", temp_path)

        try:
            result = subprocess.run(
                ["screencapture", "-x", temp_path],
                check=False,
                capture_output=True,
                text=True,
            )
            file_exists = os.path.exists(temp_path)
            file_size = os.path.getsize(temp_path) if file_exists else 0
            self._logger.debug(
                "screencapture 返回: code=%s stderr=%r file_exists=%s file_size=%s",
                result.returncode,
                result.stderr.strip(),
                file_exists,
                file_size,
            )
            if result.returncode != 0:
                error_text = result.stderr.strip() or "未知错误"
                if "could not create image from display" in error_text.lower():
                    self._set_last_error(
                        "截图失败：macOS 当前无法访问屏幕内容。请确认 AstrBot Desktop Assistant 已获得“屏幕录制”权限，并在授权后重启客户端。"
                    )
                else:
                    self._set_last_error(f"macOS screencapture 失败: {error_text}")
                self._logger.warning("macOS screencapture 失败: %s", error_text)
                return None

            if not file_exists or file_size <= 0:
                self._set_last_error("截图失败：screencapture 未生成有效图片文件")
                self._logger.warning("screencapture 未生成有效图片文件: %s", temp_path)
                return None

            with Image.open(temp_path) as img:
                self._logger.debug(
                    "screencapture 打开成功: format=%s size=%sx%s",
                    img.format,
                    img.width,
                    img.height,
                )
                return img.convert("RGB")
        except Exception as e:
            self._set_last_error(f"macOS 全屏截图失败: {e}")
            self._logger.exception("macOS 全屏截图失败")
            return None
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def capture_region(
        self, left: int, top: int, width: int, height: int
    ) -> Optional[Image.Image]:
        """
        捕获指定区域

        Args:
            left: 左边界
            top: 上边界
            width: 宽度
            height: 高度

        Returns:
            PIL Image 对象，失败返回 None
        """
        self._set_last_error("")
        if not HAS_PIL:
            self._set_last_error("截图失败：Pillow 未安装")
            return None

        try:
            if sys.platform == "darwin":
                if not self._check_macos_screen_capture_access(request_if_needed=True):
                    return None
                return self._capture_region_macos(left, top, width, height)

            if not HAS_MSS:
                self._set_last_error("截图失败：区域截图依赖未安装")
                return None
            with mss.mss() as sct:
                region = {"left": left, "top": top, "width": width, "height": height}
                screenshot = sct.grab(region)
                return Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                )
        except Exception as e:
            self._set_last_error(f"区域截图失败: {e}")
            self._logger.exception("区域截图失败")
            return None

    def _capture_region_macos(
        self, left: int, top: int, width: int, height: int
    ) -> Optional[Image.Image]:
        """使用 macOS 原生 screencapture 抓取指定区域。"""
        if width <= 0 or height <= 0:
            self._set_last_error("区域截图失败：无效的选区大小")
            return None

        fd, temp_path = tempfile.mkstemp(prefix="astrbot_region_", suffix=".png")
        os.close(fd)
        region_arg = f"{left},{top},{width},{height}"
        self._logger.debug("准备调用 screencapture 区域截图: region=%s", region_arg)

        try:
            result = subprocess.run(
                ["screencapture", "-x", f"-R{region_arg}", temp_path],
                check=False,
                capture_output=True,
                text=True,
            )
            file_exists = os.path.exists(temp_path)
            file_size = os.path.getsize(temp_path) if file_exists else 0
            self._logger.debug(
                "区域 screencapture 返回: code=%s stderr=%r file_exists=%s file_size=%s",
                result.returncode,
                result.stderr.strip(),
                file_exists,
                file_size,
            )
            if result.returncode != 0:
                error_text = result.stderr.strip() or "未知错误"
                self._set_last_error(f"macOS 区域截图失败: {error_text}")
                return None

            if not file_exists or file_size <= 0:
                self._set_last_error("区域截图失败：screencapture 未生成有效图片文件")
                return None

            with Image.open(temp_path) as img:
                return img.convert("RGB")
        except Exception as e:
            self._set_last_error(f"macOS 区域截图失败: {e}")
            self._logger.exception("macOS 区域截图失败")
            return None
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def capture_full_screen_to_file(
        self, filename: Optional[str] = None
    ) -> Optional[str]:
        """
        捕获全屏并保存到文件

        Args:
            filename: 文件名，不指定则自动生成

        Returns:
            保存的文件路径，失败返回 None
        """
        image = self.capture_full_screen()
        if image is None:
            return None

        if filename is None:
            filename = f"screenshot_{int(time.time() * 1000)}.png"

        filepath = os.path.join(self.save_dir, filename)

        try:
            image.save(filepath, "PNG")
            return filepath
        except Exception as e:
            self._logger.exception("保存截图失败")
            return None

    def capture_to_bytes(self, image: Optional[Image.Image] = None) -> Optional[bytes]:
        """
        将截图转换为字节数据

        Args:
            image: PIL Image 对象，不指定则捕获全屏

        Returns:
            PNG 格式的字节数据，失败返回 None
        """
        if image is None:
            image = self.capture_full_screen()
        if image is None:
            return None

        try:
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as e:
            self._logger.exception("转换截图失败")
            return None

    def get_screen_size(self) -> Tuple[int, int]:
        """
        获取主屏幕大小

        Returns:
            (宽度, 高度) 元组
        """
        if not HAS_MSS:
            return (1920, 1080)

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # 主显示器
                return (monitor["width"], monitor["height"])
        except Exception:
            return (1920, 1080)  # 默认值

    def get_monitors_info(self) -> list:
        """
        获取所有显示器信息

        Returns:
            显示器信息列表
        """
        if not HAS_MSS:
            return []

        try:
            with mss.mss() as sct:
                return [
                    {
                        "index": i,
                        "left": m["left"],
                        "top": m["top"],
                        "width": m["width"],
                        "height": m["height"],
                    }
                    for i, m in enumerate(sct.monitors)
                ]
        except Exception as e:
            print(f"获取显示器信息失败: {e}")
            return []

    def capture_region_to_file(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        捕获指定区域并保存到文件

        Args:
            left: 左边界
            top: 上边界
            width: 宽度
            height: 高度
            filename: 文件名，不指定则自动生成

        Returns:
            保存的文件路径，失败返回 None
        """
        image = self.capture_region(left, top, width, height)
        if image is None:
            return None

        if filename is None:
            filename = f"region_{int(time.time() * 1000)}.png"

        filepath = os.path.join(self.save_dir, filename)

        try:
            image.save(filepath, "PNG")
            return filepath
        except Exception as e:
            print(f"保存区域截图失败: {e}")
            return None

    def capture_interactive_region_to_file(
        self, filename: Optional[str] = None
    ) -> Optional[str]:
        """
        使用系统交互式区域选择并保存到文件。

        macOS 下走原生 `screencapture -i`，其他平台暂不支持。
        """
        self._set_last_error("")

        if sys.platform != "darwin":
            self._set_last_error("交互式区域截图仅支持 macOS")
            return None

        if not self._check_macos_screen_capture_access(request_if_needed=True):
            return None

        if filename is None:
            filename = f"region_{int(time.time() * 1000)}.png"

        filepath = os.path.join(self.save_dir, filename)

        try:
            result = subprocess.run(
                ["screencapture", "-i", "-x", filepath],
                check=False,
                capture_output=True,
                text=True,
            )
            file_exists = os.path.exists(filepath)
            file_size = os.path.getsize(filepath) if file_exists else 0
            self._logger.debug(
                "交互式 screencapture 返回: code=%s stderr=%r file_exists=%s file_size=%s",
                result.returncode,
                result.stderr.strip(),
                file_exists,
                file_size,
            )

            if result.returncode != 0:
                error_text = result.stderr.strip() or "用户已取消"
                self._set_last_error(f"交互式区域截图失败: {error_text}")
                return None

            if not file_exists or file_size <= 0:
                self._set_last_error("交互式区域截图已取消或未生成图片")
                return None

            return filepath
        except Exception as e:
            self._set_last_error(f"交互式区域截图失败: {e}")
            self._logger.exception("交互式区域截图失败")
            return None

    def start_region_capture(
        self,
        on_complete: Optional[Callable[[Optional[str]], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        """
        启动交互式区域截图选择

        需要在 Qt 事件循环中调用此方法。

        Args:
            on_complete: 完成回调，参数为截图文件路径
            on_cancel: 取消回调，无参数
        """
        try:
            from ..gui.screenshot_selector import RegionScreenshotCapture

            capture = RegionScreenshotCapture(self.save_dir)
            capture.capture_async(
                on_complete=lambda path: on_complete(path) if on_complete else None
            )
        except ImportError as e:
            print(f"无法启动区域截图: {e}")
            if on_cancel:
                on_cancel()
