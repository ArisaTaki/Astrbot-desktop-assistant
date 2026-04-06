"""
Live2D 角色窗口

提供可拖拽的 Live2D 看板娘窗口，支持：
- 透明无边框窗口
- Live2D 模型加载与渲染
- 物理效果与自动眨眼
- 表情切换 / 唇形同步
- 鼠标拖拽与视线追踪
- 右键菜单
- 与 CompactChatWindow 集成
- 截图时的隐藏/恢复
- 与 FloatingBallWindow 完全兼容的信号和方法接口
"""

from typing import Optional
import os
import sys
import logging
from pathlib import Path

import OpenGL.GL as gl  # type: ignore[import-untyped]
import live2d.v3 as live2d  # type: ignore[import-untyped]

from PySide6.QtCore import (  # type: ignore[import-not-found]
    Qt,
    QPoint,
    QTimer,
    QTimerEvent,
    Signal,
)
from PySide6.QtGui import (  # type: ignore[import-not-found]
    QMouseEvent,
    QCursor,
    QGuiApplication,
)
from PySide6.QtWidgets import (  # type: ignore[import-not-found]
    QMenu,
    QApplication,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget  # type: ignore[import-not-found]

from .floating_ball import (
    FloatingBallState,
    CompactChatWindow,
    _set_macos_window_level,
    _HAS_PYOBJC,
    _NSNormalWindowLevel,
    _CAPTURE_SETTLE_MS,
    _CAPTURE_RESTORE_SETTLE_MS,
    _wait_for_window_server,
)
from .themes import theme_manager, Theme
from .icons import icon_manager
from ..services.screen_capture import ScreenCaptureService

logger = logging.getLogger(__name__)


class Live2DCharacterWindow(QOpenGLWidget):
    """Live2D 角色窗口 — 与 FloatingBallWindow 接口兼容"""

    # 信号（与 FloatingBallWindow 一致）
    clicked = Signal()
    double_clicked = Signal()
    settings_requested = Signal()
    restart_requested = Signal()
    quit_requested = Signal()
    screenshot_requested = Signal(str)
    message_sent = Signal(str)
    image_sent = Signal(str, str)

    def __init__(self, model_path: str, config=None, parent=None):
        super().__init__(parent)
        self.config = config or {}
        self._model_path = model_path

        # 状态
        self._state = FloatingBallState.NORMAL
        self._has_unread = False

        # 窗口尺寸
        self._win_width = 400
        self._win_height = 500

        # 窗口属性
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self._use_translucent_window = True
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self._win_width, self._win_height)

        # 系统缩放
        screen = QGuiApplication.primaryScreen()
        self._system_scale = screen.devicePixelRatio() if screen else 1.0

        # 模型
        self._model: Optional[live2d.LAppModel] = None
        self._gl_initialized = False
        self._render_ready = False

        # 拖拽状态
        self._dragging = False
        self._click_x = -1.0
        self._click_y = -1.0
        self._click_in_l2d_area = False
        self._drag_start_global = QPoint()
        self._drag_start_window_pos = QPoint()

        # 双击检测
        self._click_timer = QTimer()
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        self._pending_click = False
        self._double_click_interval = 300

        # 截图隐藏/恢复状态
        self._capture_prepared = False
        self._capture_prepare_depth = 0

        # 呼吸灯 / 未读指示器（仅用于兼容接口，实际状态通过 L2D 表情表达）
        self._breathing = True
        self._breath_phase = 0.0
        self._pulse_phase = 0.0

        # macOS 鼠标穿透状态
        self._mouse_passthrough = False
        self._ns_window_ref = None
        self._cursor_on_model = False  # paintGL 中缓存的命中检测
        self._disable_native_mouse_passthrough = False

        # 精简版对话窗口（与 FloatingBallWindow 相同）
        self._compact_window = CompactChatWindow(config=self.config)
        # L2D 模式下加大对话窗口
        self._compact_window.resize(460, 580)
        self._compact_window.message_sent.connect(self.message_sent)
        self._compact_window.image_sent.connect(self.image_sent)

        # 加载用户和Bot头像
        self._load_avatars_from_config()

        # 加载背景图设置
        self._load_background_from_config()

        # 从配置加载自动隐藏设置
        if hasattr(self.config, "interaction"):
            interaction = getattr(self.config, "interaction")
            auto_hide = getattr(interaction, "bubble_auto_hide", False)
            duration = getattr(interaction, "bubble_duration", 5) * 1000
            self._compact_window.set_auto_hide(auto_hide, duration)

        # 初始位置
        self._move_to_default_position()

        # 注册主题回调
        theme_manager.register_callback(self._on_theme_changed)

    # ==================== OpenGL 生命周期 ====================

    def initializeGL(self) -> None:
        model_path = Path(self._model_path).expanduser().resolve()
        model_dir = model_path.parent
        prev_cwd = Path.cwd()
        logger.debug(
            "Live2D initializeGL 开始: model=%s cwd=%s model_dir=%s",
            model_path,
            prev_cwd,
            model_dir,
        )
        try:
            live2d.glInit()
            self._model = live2d.LAppModel()
            os.chdir(model_dir)
            self._model.LoadModelJson(str(model_path))
            self._gl_initialized = True

            # 应用缩放
            scale = 1.0
            if hasattr(self.config, "appearance"):
                scale = getattr(self.config.appearance, "live2d_scale", 1.0) or 1.0
            if scale != 1.0 and hasattr(self._model, "SetScale"):
                self._model.SetScale(scale)

            self._render_ready = True
            logger.debug(
                "Live2D initializeGL 成功: scale=%s size=%sx%s",
                scale,
                self.width(),
                self.height(),
            )

            # 以约 60fps 进行渲染
            self.startTimer(int(1000 / 60))
        except Exception:
            self._render_ready = False
            logger.exception("Live2D initializeGL 失败")
            raise
        finally:
            try:
                os.chdir(prev_cwd)
            except Exception:
                logger.warning("恢复工作目录失败: %s", prev_cwd)

    def resizeGL(self, w: int, h: int) -> None:
        if self._model:
            self._model.Resize(w, h)

    def paintGL(self) -> None:
        try:
            live2d.clearBuffer()
            if self._model:
                self._model.Update()
                # 在 Update 之后、Draw 之前设置表情参数（覆盖动画默认值）
                self._apply_expression_for_state()
                self._model.Draw()
        except Exception:
            self._render_ready = False
            logger.exception("Live2D paintGL 失败")
            raise

        # 在 GL 上下文有效时缓存鼠标命中检测结果
        self._cache_cursor_hit()

    def _is_point_on_model(self, lx: float, ly: float) -> bool:
        """基于当前 framebuffer 做像素级命中检测。"""
        if not (0 <= lx < self.width() and 0 <= ly < self.height()):
            return False
        try:
            image = self.grabFramebuffer()
            if image.isNull():
                return False

            image_w = image.width()
            image_h = image.height()
            if image_w <= 0 or image_h <= 0:
                return False

            fx = int((lx / max(1, self.width())) * image_w)
            fy = int((ly / max(1, self.height())) * image_h)

            hit = False
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    sx = max(0, min(image_w - 1, fx + ox))
                    sy = max(0, min(image_h - 1, fy + oy))
                    color = image.pixelColor(sx, sy)
                    if color.alpha() > 0 or (
                        color.red() + color.green() + color.blue()
                    ) > 12:
                        hit = True
                        break
                if hit:
                    break
            return hit
        except Exception:
            logger.exception("Live2D framebuffer 命中检测失败")
            return False

    def _cache_cursor_hit(self):
        """paintGL 中缓存当前光标是否在模型不透明区域上"""
        try:
            cursor = QCursor.pos()
            lx = cursor.x() - self.x()
            ly = cursor.y() - self.y()
            self._cursor_on_model = self._is_point_on_model(lx, ly)
        except Exception:
            self._cursor_on_model = False

    def timerEvent(self, event: QTimerEvent) -> None:
        if not self.isVisible():
            return
        # 视线追踪
        if self._model:
            cursor = QCursor.pos()
            local_x = cursor.x() - self.x()
            local_y = cursor.y() - self.y()
            self._model.Drag(local_x, local_y)

            # 使用 paintGL 缓存的命中结果切换鼠标穿透（拖拽期间保持不穿透）
            if not self._click_in_l2d_area:
                self._update_mouse_passthrough(not self._cursor_on_model)

        self.update()

    # ==================== 鼠标事件 ====================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        x, y = event.scenePosition().x(), event.scenePosition().y()
        self._cursor_on_model = self._is_point_on_model(x, y)
        logger.debug(
            "Live2D mousePress: button=%s pos=(%.1f,%.1f) cursor_on_model=%s dragging=%s",
            event.button(),
            x,
            y,
            self._cursor_on_model,
            self._dragging,
        )
        if event.button() == Qt.MouseButton.LeftButton:
            if self._cursor_on_model:
                self._click_in_l2d_area = True
                self._click_x, self._click_y = x, y
                self._dragging = False
                self._drag_start_global = event.globalPosition().toPoint()
                self._drag_start_window_pos = self.pos()
                self._update_mouse_passthrough(False)
                self.grabMouse()
                logger.debug(
                    "Live2D drag start: global=%s window_pos=%s",
                    self._drag_start_global,
                    self._drag_start_window_pos,
                )
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._cursor_on_model:
                self._update_mouse_passthrough(False)
                self._show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        logger.debug(
            "Live2D mouseRelease: button=%s cursor_on_model=%s dragging=%s click_in_area=%s",
            event.button(),
            self._cursor_on_model,
            self._dragging,
            self._click_in_l2d_area,
        )
        if event.button() == Qt.MouseButton.LeftButton and self._click_in_l2d_area:
            self._click_in_l2d_area = False
            if not self._dragging:
                # 非拖拽，视为点击 —— 启动双击检测
                if self._click_timer.isActive():
                    # 定时器运行中说明之前已有一次点击，这是双击
                    self._click_timer.stop()
                    self._pending_click = False
                    self.double_clicked.emit()
                else:
                    self._pending_click = True
                    self._click_timer.start(self._double_click_interval)

                # 点击模型 —— 播放随机动作
                if self._model:
                    self._model.StartRandomMotion(priority=3)
            self._dragging = False
            self.releaseMouse()
            self._update_mouse_passthrough(not self._cursor_on_model)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._update_mouse_passthrough(not self._cursor_on_model)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._click_in_l2d_area:
            current_global = event.globalPosition().toPoint()
            dx = current_global.x() - self._drag_start_global.x()
            dy = current_global.y() - self._drag_start_global.y()
            logger.debug(
                "Live2D mouseMove: global=%s delta=(%s,%s) dragging=%s",
                current_global,
                dx,
                dy,
                self._dragging,
            )
            if abs(dx) > 3 or abs(dy) > 3:
                self._dragging = True
            if self._dragging:
                self.move(
                    self._drag_start_window_pos.x() + dx,
                    self._drag_start_window_pos.y() + dy,
                )
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # 已在 mouseReleaseEvent 中处理双击检测
        event.accept()

    def _on_single_click(self):
        if self._pending_click:
            self._pending_click = False
            self.clicked.emit()

    # ==================== 表情 & 唇形 ====================

    def set_expression(self, name: str):
        """切换表情（如 "泪珠"、"眯眯眼"、"笑咪咪"、"眼泪"）"""
        if self._model:
            self._model.SetExpression(name)

    def set_lip_sync(self, value: float):
        """设置唇形同步参数 (0.0 ~ 1.0)"""
        if self._model:
            self._model.SetParameterValue("ParamMouthOpenY", value, 1.0)

    def start_motion(self, group: str, index: int = 0, priority: int = 3):
        """播放指定动作"""
        if self._model:
            self._model.StartMotion(group, index, priority)

    # ==================== 右键菜单 ====================

    def _show_context_menu(self, pos: QPoint):
        """右键菜单"""
        menu = QMenu(self)

        c = theme_manager.get_current_colors()
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {c.bg_primary};
                border: 1px solid {c.border_light};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 20px 8px 12px;
                border-radius: 4px;
                color: {c.text_primary};
            }}
            QMenu::item:selected {{
                background-color: {c.bg_hover};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {c.border_light};
                margin: 4px 8px;
            }}
        """)

        # 截图功能
        region_screenshot_action = menu.addAction("区域截图")
        region_screenshot_action.setIcon(
            icon_manager.get_icon("screenshot", c.text_primary, 16)
        )
        region_screenshot_action.triggered.connect(self._on_region_screenshot)

        full_screenshot_action = menu.addAction("全屏截图")
        full_screenshot_action.setIcon(
            icon_manager.get_icon("screenshot", c.text_primary, 16)
        )
        full_screenshot_action.triggered.connect(self._on_full_screenshot)

        menu.addSeparator()

        # 主题子菜单
        theme_menu = menu.addMenu("切换主题")
        theme_menu.setIcon(icon_manager.get_icon("theme", c.text_primary, 16))
        theme_menu.setStyleSheet(menu.styleSheet())

        for theme_name, display_name in theme_manager.get_theme_names():
            action = theme_menu.addAction(display_name)
            action.triggered.connect(
                lambda checked, n=theme_name: self._switch_theme_and_save(n)
            )

        menu.addSeparator()

        restart_action = menu.addAction("重启")
        restart_action.setIcon(icon_manager.get_icon("restart", c.text_primary, 16))
        restart_action.triggered.connect(self.restart_requested.emit)

        settings_action = menu.addAction("设置")
        settings_action.setIcon(icon_manager.get_icon("settings", c.text_primary, 16))
        settings_action.triggered.connect(self.settings_requested.emit)

        quit_action = menu.addAction("退出")
        quit_action.setIcon(icon_manager.get_icon("exit", c.danger, 16))
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.exec(pos)

    def _switch_theme_and_save(self, theme_name: str):
        theme_manager.set_theme(theme_name)
        if hasattr(self.config, "appearance"):
            self.config.appearance.theme = theme_name  # type: ignore[union-attr]
            if hasattr(self.config, "save"):
                self.config.save()  # type: ignore[union-attr]

    # ==================== 截图功能 ====================

    def _on_region_screenshot(self):
        try:
            self._prepare_for_capture()
            QTimer.singleShot(0, self._start_region_capture)
        except ImportError as e:
            print(f"区域截图功能不可用: {e}")
            self._restore_after_capture()

    def _start_region_capture(self):
        try:
            from .screenshot_selector import RegionScreenshotCapture

            self._capture = RegionScreenshotCapture()
            self._capture.capture_async(self._on_screenshot_complete)
        except Exception as e:
            print(f"启动区域截图失败: {e}")
            self._restore_after_capture()

    def _on_full_screenshot(self):
        try:
            self._prepare_for_capture()
            QTimer.singleShot(0, self._do_full_screenshot)
        except ImportError as e:
            print(f"截图功能不可用: {e}")
            self._restore_after_capture()

    def _do_full_screenshot(self):
        try:
            service = ScreenCaptureService()
            screenshot_path = service.capture_full_screen_to_file()
            self._restore_after_capture()
            if screenshot_path:
                self.screenshot_requested.emit(screenshot_path)
        except Exception as e:
            print(f"全屏截图失败: {e}")
            self._restore_after_capture()

    def _on_screenshot_complete(self, screenshot_path):
        self._restore_after_capture()
        if screenshot_path:
            self.screenshot_requested.emit(screenshot_path)

    # ==================== 截图隐藏/恢复 ====================

    def _prepare_for_capture(self):
        """截图/桌面识别前的统一处理
        
        对于 QOpenGLWidget，避免使用 hide() 因为会销毁 GL 上下文。
        改用设置透明度为 0 + 移到屏幕外的方式隐藏。
        """
        self._capture_prepare_depth += 1
        if self._capture_prepared:
            return

        self._capture_prepared = True
        self._capture_restore_ball_visible = self.isVisible()
        self._capture_restore_chat_visible = self._compact_window.isVisible()
        self._capture_restore_ball_pos = self.pos()
        self._capture_restore_chat_pos = self._compact_window.pos()
        self._capture_restore_ball_opacity = self.windowOpacity()
        self._capture_restore_chat_opacity = self._compact_window.windowOpacity()

        if sys.platform == "darwin":
            _set_macos_window_level(self._compact_window, _NSNormalWindowLevel)
            _set_macos_window_level(self, _NSNormalWindowLevel)
            self._compact_window.lower()
            self.lower()

        self._compact_window.setWindowOpacity(0)
        self.setWindowOpacity(0)

        if self._capture_restore_chat_visible:
            self._compact_window.hide()
        # 对 QOpenGLWidget 使用移到屏幕外代替 hide()，避免 GL 上下文丢失
        if self._capture_restore_ball_visible:
            self.move(-9999, -9999)

        QApplication.processEvents()
        _wait_for_window_server(_CAPTURE_SETTLE_MS)

    def _restore_after_capture(self):
        """截图/桌面识别后恢复显示状态"""
        try:
            if self._capture_prepare_depth <= 0:
                return

            self._capture_prepare_depth -= 1
            if self._capture_prepare_depth > 0:
                return
            if not self._capture_prepared:
                return

            if getattr(self, "_capture_restore_ball_visible", False):
                self.move(getattr(self, "_capture_restore_ball_pos", self.pos()))
                self.setWindowOpacity(
                    getattr(self, "_capture_restore_ball_opacity", 1.0)
                )
                self.raise_()
            else:
                self.setWindowOpacity(
                    getattr(self, "_capture_restore_ball_opacity", 1.0)
                )

            if getattr(self, "_capture_restore_chat_visible", False):
                self._compact_window.move(
                    getattr(self, "_capture_restore_chat_pos", self._compact_window.pos())
                )
                self._compact_window.setWindowOpacity(
                    getattr(self, "_capture_restore_chat_opacity", 1.0)
                )
                self._compact_window.show()
                self._compact_window.raise_()
            else:
                self._compact_window.setWindowOpacity(
                    getattr(self, "_capture_restore_chat_opacity", 1.0)
                )

            QApplication.processEvents()

            if sys.platform == "darwin":
                if getattr(self, "_capture_restore_ball_visible", False):
                    QTimer.singleShot(0, lambda: _set_macos_window_level(self))
                if getattr(self, "_capture_restore_chat_visible", False):
                    QTimer.singleShot(
                        0, lambda: _set_macos_window_level(self._compact_window)
                    )
                _wait_for_window_server(_CAPTURE_RESTORE_SETTLE_MS)

            self._capture_prepared = False
        except Exception as e:
            print(f"[capture-restore] restore failed: {e}")
            self._capture_prepared = False
            self._capture_prepare_depth = 0

    # ==================== 对话窗口管理 ====================

    def show_bubble(self, text: str, duration: int = 0):
        """显示气泡（实际显示在精简窗口中）"""
        self._has_unread = False
        self._update_compact_window_position()
        self._compact_window.add_ai_message(text)
        self._compact_window.show()

    def show_system_message(self, text: str):
        """显示系统消息"""
        self._compact_window.add_system_message(text)

    def toggle_input(self):
        """切换输入框显示/隐藏"""
        if self._compact_window.isVisible():
            self._compact_window.hide()
        else:
            self.show_input()

    def show_input(self):
        """显示输入框"""
        self._has_unread = False
        self._update_compact_window_position()
        self._compact_window.show()
        self._compact_window.activateWindow()

    def _update_compact_window_position(self):
        """将对话窗口放置在屏幕中央"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            w = self._compact_window.width()
            h = self._compact_window.height()
            x = geo.x() + (geo.width() - w) // 2
            y = geo.y() + (geo.height() - h) // 2
            self._compact_window.move(x, y)

    # ==================== macOS 鼠标穿透 ====================

    def _get_ns_window(self):
        """获取 NSWindow 引用（缓存）"""
        if self._ns_window_ref is not None:
            return self._ns_window_ref
        if sys.platform != "darwin" or not _HAS_PYOBJC:
            return None
        try:
            import objc  # type: ignore[import-untyped]
            wid = int(self.winId())
            # PySide6 winId() 在 macOS 上返回 NSView 指针
            ns_view = objc.objc_object(c_void_p=wid)  # type: ignore[attr-defined]
            if hasattr(ns_view, "window") and ns_view.window():
                self._ns_window_ref = ns_view.window()
                return self._ns_window_ref
        except Exception:
            pass
        # 回退: 遍历 NSApp.windows
        try:
            from AppKit import NSApp  # type: ignore[import-untyped,attr-defined]
            wid = int(self.winId())
            for ns_win in NSApp.windows():
                if ns_win.windowNumber() == wid:
                    self._ns_window_ref = ns_win
                    return ns_win
        except Exception:
            pass
        return None

    def _update_mouse_passthrough(self, passthrough: bool):
        """动态切换鼠标事件穿透（仅在状态变化时操作）"""
        if self._disable_native_mouse_passthrough:
            self._mouse_passthrough = False
            return

        if passthrough == self._mouse_passthrough:
            return
        self._mouse_passthrough = passthrough

        if sys.platform == "darwin":
            ns_win = self._get_ns_window()
            if ns_win:
                ns_win.setIgnoresMouseEvents_(passthrough)
        else:
            self.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, passthrough
            )

    # ==================== Live2D 表情状态 ====================

    def _apply_expression_for_state(self):
        """根据当前状态设置 Live2D 表情参数"""
        if not self._model:
            return

        if self._state == FloatingBallState.DISCONNECTED:
            # 断连 → 眼泪 + 泪珠
            self._model.SetParameterValue("ParamExpression_1", 1.0)  # 眼泪
            self._model.SetParameterValue("ParamExpression_2", 1.0)  # 泪珠
            self._model.SetParameterValue("ParamExpression_3", 0.0)  # 关闭笑眯眯
        elif self._has_unread:
            # 未读消息 → 笑眯眯（期待互动）
            self._model.SetParameterValue("ParamExpression_1", 0.0)
            self._model.SetParameterValue("ParamExpression_2", 0.0)
            self._model.SetParameterValue("ParamExpression_3", 1.0)  # 笑眯眯
        else:
            # 正常状态 → 重置表情
            self._model.SetParameterValue("ParamExpression_1", 0.0)
            self._model.SetParameterValue("ParamExpression_2", 0.0)
            self._model.SetParameterValue("ParamExpression_3", 0.0)

    # ==================== 代理方法（与 FloatingBallWindow 兼容） ====================

    def set_state(self, state: FloatingBallState):
        """设置状态"""
        self._state = state
        self._apply_expression_for_state()

    def is_waiting_response(self) -> bool:
        return self._compact_window._is_waiting

    def update_streaming_response(self, content: str):
        self._compact_window.update_streaming_response(content)

    def finish_response(self):
        self._compact_window.finish_response()

    def set_attachment(self, path: str):
        self._compact_window.set_attachment(path)

    def add_user_message(self, text: str, image_path: Optional[str] = None):
        """添加用户消息"""
        self._compact_window.add_user_message(text, image_path)

    def set_unread_message(self, has_unread: bool = True):
        self._has_unread = has_unread
        self._apply_expression_for_state()

    def clear_unread_message(self):
        self.set_unread_message(False)

    def has_unread_message(self) -> bool:
        return self._has_unread

    def set_avatar(self, avatar_path: str):
        """设置头像（仅用于兼容接口）"""
        pass

    def set_user_avatar(self, avatar_path: str):
        self._compact_window.set_user_avatar(avatar_path)

    def set_bot_avatar(self, avatar_path: str):
        self._compact_window.set_bot_avatar(avatar_path)

    def set_breathing(self, enabled: bool):
        """设置呼吸灯效果"""
        self._breathing = enabled

    def update_appearance_config(self, config) -> None:
        """更新外观配置"""
        if not hasattr(config, "appearance"):
            return

        appearance = config.appearance

        # Live2D 缩放
        scale = getattr(appearance, "live2d_scale", 1.0) or 1.0
        if self._model and hasattr(self._model, "SetScale"):
            self._model.SetScale(scale)

        bg_path = getattr(appearance, "background_image_path", "") or ""
        bg_opacity = getattr(appearance, "background_opacity", 0.3)
        bg_blur = getattr(appearance, "background_blur", 0)
        self._compact_window.set_background_config(bg_path, bg_opacity, bg_blur)

        user_avatar = getattr(appearance, "user_avatar_path", "") or ""
        bot_avatar = getattr(appearance, "bot_avatar_path", "") or ""
        if not bot_avatar:
            bot_avatar = getattr(appearance, "avatar_path", "") or ""

        if user_avatar:
            self._compact_window.set_user_avatar(user_avatar)
        if bot_avatar:
            self._compact_window.set_bot_avatar(bot_avatar)

        QTimer.singleShot(100, self._compact_window.reload_history_display)

    # ==================== 初始化辅助 ====================

    def _load_avatars_from_config(self):
        """从配置加载头像"""
        if not hasattr(self.config, "appearance"):
            return
        appearance = getattr(self.config, "appearance")

        user_avatar = ""
        if hasattr(appearance, "user_avatar_path"):
            user_avatar = getattr(appearance, "user_avatar_path", "") or ""
        elif isinstance(appearance, dict) and "user_avatar_path" in appearance:
            user_avatar = appearance.get("user_avatar_path", "") or ""
        if user_avatar:
            self._compact_window.set_user_avatar(user_avatar)

        bot_avatar = ""
        if hasattr(appearance, "bot_avatar_path"):
            bot_avatar = getattr(appearance, "bot_avatar_path", "") or ""
        elif isinstance(appearance, dict) and "bot_avatar_path" in appearance:
            bot_avatar = appearance.get("bot_avatar_path", "") or ""
        if not bot_avatar:
            if hasattr(appearance, "avatar_path"):
                bot_avatar = getattr(appearance, "avatar_path", "") or ""
            elif isinstance(appearance, dict) and "avatar_path" in appearance:
                bot_avatar = appearance.get("avatar_path", "") or ""
        if bot_avatar:
            self._compact_window.set_bot_avatar(bot_avatar)

        if user_avatar or bot_avatar:
            QTimer.singleShot(150, self._compact_window.reload_history_display)

    def _load_background_from_config(self):
        """从配置加载背景图设置"""
        if not hasattr(self.config, "appearance"):
            return
        appearance = getattr(self.config, "appearance")
        bg_path = ""
        bg_opacity = 0.3
        bg_blur = 0
        if hasattr(appearance, "background_image_path"):
            bg_path = getattr(appearance, "background_image_path", "") or ""
        elif isinstance(appearance, dict):
            bg_path = appearance.get("background_image_path", "") or ""
        if hasattr(appearance, "background_opacity"):
            bg_opacity = getattr(appearance, "background_opacity", 0.3)
        elif isinstance(appearance, dict):
            bg_opacity = appearance.get("background_opacity", 0.3)
        if hasattr(appearance, "background_blur"):
            bg_blur = getattr(appearance, "background_blur", 0)
        elif isinstance(appearance, dict):
            bg_blur = appearance.get("background_blur", 0)
        if bg_path:
            self._compact_window.set_background_config(bg_path, bg_opacity, bg_blur)

    def _move_to_default_position(self):
        """移动到默认位置（屏幕右下角）"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = max(geo.left(), geo.right() - self._win_width - 50)
            y = max(geo.top(), geo.bottom() - self._win_height - 50)
            self.move(x, y)
            logger.debug(
                "Live2D 默认定位: pos=(%s,%s) size=%sx%s screen=%s",
                x,
                y,
                self._win_width,
                self._win_height,
                geo,
            )

    def _on_theme_changed(self, theme: Theme):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin":
            QTimer.singleShot(50, self._setup_macos_window)
        QTimer.singleShot(120, self._ensure_visible_state)
        QTimer.singleShot(1200, self._log_render_state)

    def _setup_macos_window(self):
        """macOS: 设置窗口层级 + 初始鼠标穿透"""
        _set_macos_window_level(self)
        # 默认穿透（timerEvent 会在光标移到模型上时切回来）
        ns_win = self._get_ns_window()
        if ns_win and not self._disable_native_mouse_passthrough:
            ns_win.setIgnoresMouseEvents_(True)
            self._mouse_passthrough = True
        else:
            self._mouse_passthrough = False
        logger.debug("Live2D macOS 窗口设置完成: visible=%s pos=%s", self.isVisible(), self.pos())

    def _ensure_visible_state(self):
        """确保窗口真正出现在当前屏幕可见区域内。"""
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            logger.warning("Live2D 可见性校验失败: 未找到屏幕")
            return

        geo = screen.availableGeometry()
        frame = self.frameGeometry()
        if not geo.intersects(frame):
            x = max(geo.left(), geo.right() - self.width() - 50)
            y = max(geo.top(), geo.bottom() - self.height() - 50)
            self.move(x, y)
            logger.warning(
                "Live2D 窗口原位置超出屏幕，已重置: old=%s new=(%s,%s) screen=%s",
                frame,
                x,
                y,
                geo,
            )

        if self.windowOpacity() < 0.99:
            self.setWindowOpacity(1.0)

        if not self.isVisible():
            self.show()

        self.raise_()
        self.update()
        self.repaint()

        if sys.platform == "darwin":
            _set_macos_window_level(self)

        logger.debug(
            "Live2D 可见性校验完成: pos=%s frame=%s visible=%s opacity=%.2f render_ready=%s",
            self.pos(),
            self.frameGeometry(),
            self.isVisible(),
            self.windowOpacity(),
            self._render_ready,
        )

    def _log_render_state(self):
        logger.debug(
            "Live2D 渲染状态: gl_initialized=%s render_ready=%s visible=%s size=%sx%s pos=%s",
            self._gl_initialized,
            self._render_ready,
            self.isVisible(),
            self.width(),
            self.height(),
            self.pos(),
        )

    def closeEvent(self, event):
        """释放 Live2D 资源"""
        self._ns_window_ref = None
        if self._model:
            self._model = None
        if self._gl_initialized:
            live2d.glRelease()
            self._gl_initialized = False
        super().closeEvent(event)
