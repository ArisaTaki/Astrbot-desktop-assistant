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
    _NSNormalWindowLevel,
    _CAPTURE_SETTLE_MS,
    _CAPTURE_RESTORE_SETTLE_MS,
    _wait_for_window_server,
)
from .themes import theme_manager, Theme
from .icons import icon_manager
from ..services.screen_capture import ScreenCaptureService


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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self._win_width, self._win_height)

        # 系统缩放
        screen = QGuiApplication.primaryScreen()
        self._system_scale = screen.devicePixelRatio() if screen else 1.0

        # 模型
        self._model: Optional[live2d.LAppModel] = None
        self._gl_initialized = False

        # 拖拽状态
        self._dragging = False
        self._click_x = -1.0
        self._click_y = -1.0
        self._is_in_l2d_area = False
        self._click_in_l2d_area = False

        # 双击检测
        self._click_timer = QTimer()
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        self._pending_click = False
        self._double_click_interval = 300

        # 截图隐藏/恢复状态
        self._capture_prepared = False
        self._capture_prepare_depth = 0

        # 精简版对话窗口（与 FloatingBallWindow 相同）
        self._compact_window = CompactChatWindow(config=self.config)
        self._compact_window.message_sent.connect(self.message_sent)
        self._compact_window.image_sent.connect(self.image_sent)
        self._compact_window.window_moved.connect(self._on_compact_window_moved)
        self._compact_window.window_resized.connect(self._on_compact_window_resized)

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
        live2d.glInit()
        self._model = live2d.LAppModel()
        self._model.LoadModelJson(self._model_path)
        self._gl_initialized = True
        # 以约 60fps 进行渲染
        self.startTimer(int(1000 / 60))

    def resizeGL(self, w: int, h: int) -> None:
        if self._model:
            self._model.Resize(w, h)

    def paintGL(self) -> None:
        live2d.clearBuffer()
        if self._model:
            self._model.Update()
            self._model.Draw()

    def timerEvent(self, event: QTimerEvent) -> None:
        if not self.isVisible():
            return
        # 视线追踪：鼠标相对窗口中心
        if self._model:
            cursor = QCursor.pos()
            local_x = cursor.x() - self.x()
            local_y = cursor.y() - self.y()
            self._is_in_l2d_area = self._check_l2d_area(local_x, local_y)
            self._model.Drag(local_x, local_y)
        self.update()

    # ==================== 鼠标事件 ====================

    def _check_l2d_area(self, x: float, y: float) -> bool:
        """通过读取 alpha 通道判断鼠标是否在模型区域内"""
        try:
            h = self.height()
            data = gl.glReadPixels(
                int(x * self._system_scale),
                int((h - y) * self._system_scale),
                1, 1, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE
            )
            return data[3] > 0  # type: ignore[index]
        except Exception:
            return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        x, y = event.scenePosition().x(), event.scenePosition().y()
        if event.button() == Qt.MouseButton.LeftButton:
            if self._check_l2d_area(x, y):
                self._click_in_l2d_area = True
                self._click_x, self._click_y = x, y
                self._dragging = False
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
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
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x, y = event.scenePosition().x(), event.scenePosition().y()
        if self._click_in_l2d_area:
            dx = x - self._click_x
            dy = y - self._click_y
            if abs(dx) > 3 or abs(dy) > 3:
                self._dragging = True
            if self._dragging:
                self.move(int(self.x() + dx), int(self.y() + dy))
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
        """截图/桌面识别前的统一处理"""
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
        if self._capture_restore_ball_visible:
            self.hide()

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
                self.show()
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
        self._update_compact_window_position()
        self._compact_window.show()
        self._compact_window.activateWindow()

    def _update_compact_window_position(self):
        """更新精简窗口位置"""
        w = self._compact_window.width()
        h = self._compact_window.height()

        # 默认显示在左侧
        x = self.x() - w - 10
        y = self.y() + (self.height() - h) // 2

        # 如果左侧空间不足，显示在右侧
        if x < 0:
            x = self.x() + self.width() + 10

        self._compact_window.move(x, y)

    def _on_compact_window_moved(self, delta_x: int, delta_y: int):
        """当聊天窗口被拖动时，同步移动角色"""
        new_x = self.x() + delta_x
        new_y = self.y() + delta_y
        self.move(new_x, new_y)

    def _on_compact_window_resized(self):
        """当精简窗口大小改变时，调整位置"""
        center_x_ball = self.x() + self.width() // 2
        center_x_win = self._compact_window.x() + self._compact_window.width() // 2
        spacing = 10
        if center_x_ball > center_x_win:
            expected_x = self._compact_window.x() + self._compact_window.width() + spacing
            if abs(self.x() - expected_x) > 2:
                self.move(expected_x, self.y())
        else:
            expected_x = self._compact_window.x() - self.width() - spacing
            if abs(self.x() - expected_x) > 2:
                self.move(expected_x, self.y())

    # ==================== 代理方法（与 FloatingBallWindow 兼容） ====================

    def set_state(self, state: FloatingBallState):
        """设置状态"""
        self._state = state

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
        """兼容接口，Live2D 不需要呼吸灯"""
        pass

    def update_appearance_config(self, config) -> None:
        """更新外观配置"""
        if not hasattr(config, "appearance"):
            return

        appearance = config.appearance

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
            x = geo.right() - self._win_width - 50
            y = geo.bottom() - self._win_height - 50
            self.move(x, y)

    def _on_theme_changed(self, theme: Theme):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin":
            QTimer.singleShot(50, lambda: _set_macos_window_level(self))

    def closeEvent(self, event):
        """释放 Live2D 资源"""
        if self._model:
            self._model = None
        if self._gl_initialized:
            live2d.glRelease()
            self._gl_initialized = False
        super().closeEvent(event)
