"""
消息处理器

负责处理从服务器接收到的各类消息，包括：
- 文本消息（流式/非流式）
- 图片、语音、视频消息
- 结束标记和错误消息
"""

import logging
from typing import TYPE_CHECKING, Optional, Any

from PySide6.QtCore import QObject, Slot

if TYPE_CHECKING:
    from ..bridge import OutputMessage
    from ..config import ClientConfig

logger = logging.getLogger(__name__)


class MessageHandler(QObject):
    """消息处理器 - 处理接收到的消息"""

    def __init__(
        self,
        config: "ClientConfig",
        floating_ball: Optional[Any] = None,
        media_handler: Optional[Any] = None,
        chat_history_manager: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ):
        """
        初始化消息处理器

        Args:
            config: 客户端配置
            floating_ball: 悬浮球窗口实例
            media_handler: 媒体处理器实例
            chat_history_manager: 聊天记录管理器
            parent: 父对象
        """
        super().__init__(parent)
        self._config = config
        self._floating_ball = floating_ball
        self._media_handler = media_handler
        self._chat_history_manager = chat_history_manager

        # 主动对话响应状态
        self._proactive_dialog_pending = False

        # 静默响应缓冲区
        self._silent_response_buffer = ""

    def set_floating_ball(self, floating_ball: Any) -> None:
        """设置悬浮球实例"""
        self._floating_ball = floating_ball

    def set_media_handler(self, media_handler: Any) -> None:
        """设置媒体处理器"""
        self._media_handler = media_handler

    def set_chat_history_manager(self, manager: Any) -> None:
        """设置聊天记录管理器"""
        self._chat_history_manager = manager

    def set_proactive_pending(self, pending: bool) -> None:
        """设置主动对话等待状态"""
        self._proactive_dialog_pending = pending

    def is_proactive_pending(self) -> bool:
        """获取主动对话等待状态"""
        return self._proactive_dialog_pending

    @Slot(object)
    def handle_output_message(self, message: "OutputMessage") -> None:
        """
        处理接收到的消息 (Slot)

        Args:
            message: 输出消息对象
        """
        msg_type = message.msg_type
        content = message.content
        session_id = message.session_id or None

        if session_id and self._chat_history_manager:
            self._chat_history_manager.set_current_session(session_id)

        # 检查是否是主动对话的响应
        is_proactive_response = self._proactive_dialog_pending

        # 检查免打扰模式
        do_not_disturb = self._config.interaction.do_not_disturb

        # 检查是否正在等待响应（用户主动发起的对话）
        (
            self._floating_ball and self._floating_ball.is_waiting_response()
        )

        # 判断是否需要静默处理（免打扰模式）
        should_silent = do_not_disturb

        if msg_type == "text":
            self._handle_text_message(
                content,
                message.streaming,
                is_proactive_response,
                should_silent,
                do_not_disturb,
                session_id,
            )

        elif msg_type == "image":
            # AI 返回的图片
            if self._media_handler:
                self._media_handler.handle_image_response(
                    content, message.metadata, should_silent
                )

        elif msg_type == "voice":
            # AI 返回的语音
            if self._media_handler:
                self._media_handler.handle_voice_response(
                    content, message.metadata, should_silent
                )

        elif msg_type == "video":
            # AI 返回的视频
            if self._media_handler:
                self._media_handler.handle_video_response(
                    content, message.metadata, should_silent
                )

        elif msg_type == "end":
            self._handle_end_message(is_proactive_response, should_silent)

        elif msg_type == "status":
            self._handle_status_message(content)

        elif msg_type == "error":
            self._handle_error_message(content, is_proactive_response, should_silent)

    def _handle_status_message(self, content: str) -> None:
        """处理状态消息（连接状态变更）"""
        if not self._floating_ball:
            return

        from ..api_client import ConnectionState
        from ..gui.floating_ball import FloatingBallState

        # content 是 ConnectionState 的 value
        if content == ConnectionState.DISCONNECTED.value:
            self._floating_ball.set_state(FloatingBallState.DISCONNECTED)
            self._floating_ball.show_system_message("❌ 与服务器断开连接")
        elif content == ConnectionState.CONNECTED.value:
            self._floating_ball.set_state(FloatingBallState.NORMAL)
            self._floating_ball.show_system_message("✅ 已连接到服务器")
        elif content == ConnectionState.CONNECTING.value:
            # 连接中，暂不处理，保持当前状态或显示加载动画
            pass

    def _handle_text_message(
        self,
        content: str,
        streaming: bool,
        is_proactive_response: bool,
        should_silent: bool,
        do_not_disturb: bool,
        session_id: Optional[str],
    ) -> None:
        """处理文本消息"""
        # 忽略空消息
        if not content:
            return

        # 过滤掉语音消息的冗余文本提示
        if content.strip() in ["[收到语音]", "🔊 [收到语音]"]:
            return

        # 主动对话响应或静默模式：静默处理，不弹窗
        if is_proactive_response or should_silent:
            if streaming:
                # 流式响应时累积内容
                self._silent_response_buffer += content
            else:
                # 非流式完整响应：静默添加到历史记录，不显示气泡
                if self._chat_history_manager:
                    self._chat_history_manager.add_message(
                        role="assistant",
                        content=content,
                        msg_type="text",
                        session_id=session_id,
                    )
                # 仅设置未读消息标记（显示动画效果）
                if self._floating_ball:
                    self._floating_ball.set_unread_message(True)
                if is_proactive_response:
                    self._proactive_dialog_pending = False
            return

        if streaming:
            # 流式响应
            if self._floating_ball:
                # 只有当气泡正在显示或等待响应时才更新
                if (
                    self._floating_ball.is_waiting_response()
                    or not self._floating_ball._compact_window.isHidden()
                ):
                    self._floating_ball.update_streaming_response(content)

        else:
            # 完整响应（非流式）
            if self._floating_ball:
                if self._floating_ball.is_waiting_response():
                    self._floating_ball.update_streaming_response(content)
                    self._floating_ball.finish_response()
                else:
                    # 免打扰模式：静默处理，不弹窗
                    if do_not_disturb:
                        if self._chat_history_manager:
                            self._chat_history_manager.add_message(
                                role="assistant",
                                content=content,
                                msg_type="text",
                                session_id=session_id,
                            )
                        self._floating_ball.set_unread_message(True)
                    else:
                        # 在气泡中显示摘要
                        summary = (
                            content[:100] + "..." if len(content) > 100 else content
                        )
                        self._floating_ball.show_bubble(summary)
            else:
                # 没有 UI 实例，直接写入历史
                if self._chat_history_manager:
                    self._chat_history_manager.add_message(
                        role="assistant",
                        content=content,
                        msg_type="text",
                        session_id=session_id,
                    )

    def _handle_end_message(
        self, is_proactive_response: bool, should_silent: bool
    ) -> None:
        """处理结束消息"""
        # 主动对话响应或静默模式结束
        if is_proactive_response or should_silent:
            # 静默添加累积的响应内容到历史记录，不显示气泡
            buffer = self._silent_response_buffer
            if buffer and self._chat_history_manager:
                self._chat_history_manager.add_message(
                    role="assistant",
                    content=buffer,
                    msg_type="text",
                    session_id=self._chat_history_manager.get_current_session(),
                )
                # 仅设置未读消息标记（显示动画效果）
                if self._floating_ball:
                    self._floating_ball.set_unread_message(True)

            # 如果是用户等待中（但被静默了），需要重置等待状态
            if self._floating_ball and self._floating_ball.is_waiting_response():
                self._floating_ball.finish_response()

            # 清理状态
            if is_proactive_response:
                self._proactive_dialog_pending = False
            self._silent_response_buffer = ""
            return

        # 气泡输入框完成响应
        if self._floating_ball and self._floating_ball.is_waiting_response():
            self._floating_ball.finish_response()

    def _handle_error_message(
        self, content: str, is_proactive_response: bool, should_silent: bool
    ) -> None:
        """处理错误消息"""
        # 主动对话或静默模式错误
        if is_proactive_response or should_silent:
            logger.error(f"静默模式响应错误: {content}")
            if is_proactive_response:
                self._proactive_dialog_pending = False
            self._silent_response_buffer = ""

            # 如果是用户等待中（但被静默了），需要重置等待状态
            if self._floating_ball and self._floating_ball.is_waiting_response():
                self._floating_ball.finish_response()

            # 静默模式下错误也只显示未读标记
            if self._floating_ball:
                self._floating_ball.set_unread_message(True)
            return

        if self._floating_ball:
            # 如果气泡输入框在等待，也需要结束等待并显示错误
            if self._floating_ball.is_waiting_response():
                self._floating_ball.update_streaming_response(f"❌ {content}")
                self._floating_ball.finish_response()
            else:
                self._floating_ball.show_bubble(f"❌ {content}")
