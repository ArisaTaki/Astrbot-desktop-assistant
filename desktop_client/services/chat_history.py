"""
聊天记录管理器

提供统一的消息管理，支持：
- 单例模式确保全局唯一
- 消息按 session_id 持久化到本地文件
- Qt 信号机制实现跨窗口同步
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """聊天消息数据结构"""

    id: str = ""
    role: str = "user"
    content: str = ""
    msg_type: str = "text"
    timestamp: float = 0.0
    file_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            id=data.get("id", ""),
            role=data.get("role", "user"),
            content=data.get("content", ""),
            msg_type=data.get("msg_type", "text"),
            timestamp=data.get("timestamp", 0.0),
            file_path=data.get("file_path", ""),
            metadata=data.get("metadata", {}),
        )


class ChatHistoryManager(QObject):
    """
    聊天记录管理器（单例模式）

    特性：
    - 按 session_id 隔离本地聊天历史
    - 原子写入，避免文件半写入
    - 保存串行化，避免旧快照覆盖新快照
    """

    message_added = Signal(object)
    message_updated = Signal(str, str)
    messages_cleared = Signal()
    history_loaded = Signal()

    _instance: Optional["ChatHistoryManager"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, history_path: str = ""):
        if ChatHistoryManager._initialized:
            if history_path and history_path != self._history_path:
                self._history_path = history_path
            return

        super().__init__()

        self._sessions: Dict[str, List[ChatMessage]] = {}
        self._current_session_id = "default"
        self._history_path = history_path or self._get_default_history_path()
        self._max_messages = 1000
        self._auto_save = True
        self._dirty = False
        self._save_version = 0
        self._save_lock = asyncio.Lock()
        self._sync_save_lock = threading.Lock()

        self.load_from_file()
        ChatHistoryManager._initialized = True

    @staticmethod
    def _get_default_history_path() -> str:
        if os.name == "nt":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            config_dir = Path(base) / "AstrBotDesktopClient"
        else:
            config_dir = Path.home() / ".config" / "astrbot-desktop-client"

        config_dir.mkdir(parents=True, exist_ok=True)
        return str(config_dir / "chat_history.json")

    @classmethod
    def get_instance(cls, history_path: str = "") -> "ChatHistoryManager":
        if cls._instance is None:
            cls._instance = cls(history_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._initialized = False

    def _normalize_session_id(self, session_id: Optional[str] = None) -> str:
        normalized = (session_id or self._current_session_id or "").strip()
        return normalized or "default"

    def _get_session_messages(self, session_id: Optional[str] = None) -> List[ChatMessage]:
        normalized = self._normalize_session_id(session_id)
        return self._sessions.setdefault(normalized, [])

    def _serialize_data(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "current_session_id": self._current_session_id,
            "sessions": {
                session_id: [msg.to_dict() for msg in messages]
                for session_id, messages in self._sessions.items()
            },
        }

    def set_history_path(self, path: str):
        if path != self._history_path:
            if self._dirty:
                self.save_to_file()
            self._history_path = path
            self.load_from_file()

    def get_history_path(self) -> str:
        return self._history_path

    def set_current_session(self, session_id: str):
        normalized = self._normalize_session_id(session_id)
        if normalized == self._current_session_id:
            return
        self._current_session_id = normalized
        self._sessions.setdefault(normalized, [])
        self.history_loaded.emit()

    def get_current_session(self) -> str:
        return self._current_session_id

    def add_message(
        self,
        role: str,
        content: str,
        msg_type: str = "text",
        file_path: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> ChatMessage:
        normalized = self._normalize_session_id(session_id)
        message = ChatMessage(
            role=role,
            content=content,
            msg_type=msg_type,
            file_path=file_path,
            metadata={**(metadata or {}), "session_id": normalized},
        )

        session_messages = self._get_session_messages(normalized)
        session_messages.append(message)
        self._dirty = True

        if len(session_messages) > self._max_messages:
            self._sessions[normalized] = session_messages[-self._max_messages :]

        if self._auto_save:
            self._schedule_save()

        self.message_added.emit(message)
        return message

    def update_message(
        self, message_id: str, content: str, session_id: Optional[str] = None
    ) -> bool:
        for msg in self._get_session_messages(session_id):
            if msg.id == message_id:
                msg.content = content
                self._dirty = True
                self.message_updated.emit(message_id, content)
                return True
        return False

    def get_last_message(self) -> Optional[ChatMessage]:
        messages = self._get_session_messages()
        if messages:
            return messages[-1]
        return None

    def get_messages(
        self, limit: int = 0, session_id: Optional[str] = None
    ) -> List[ChatMessage]:
        messages = self._get_session_messages(session_id)
        if limit > 0:
            return messages[-limit:]
        return messages.copy()

    def get_message_by_id(self, message_id: str) -> Optional[ChatMessage]:
        for messages in self._sessions.values():
            for msg in messages:
                if msg.id == message_id:
                    return msg
        return None

    def clear_history(self, session_id: Optional[str] = None):
        normalized = self._normalize_session_id(session_id)
        self._sessions[normalized] = []
        self._dirty = True

        if self._auto_save:
            self._schedule_save()

        self.messages_cleared.emit()

    def _schedule_save(self):
        self._save_version += 1
        expected_version = self._save_version

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(
                    self.save_to_file_async(expected_version=expected_version)
                )
            else:
                self.save_to_file_sync()
        except RuntimeError:
            self.save_to_file_sync()

    async def save_to_file_async(
        self, path: str = "", expected_version: Optional[int] = None
    ) -> bool:
        save_path = path or self._history_path

        try:
            async with self._save_lock:
                if expected_version is not None and expected_version != self._save_version:
                    return False

                data_to_save = self._serialize_data()

                def _write_file():
                    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                    temp_path = Path(save_path).with_suffix(
                        Path(save_path).suffix + ".tmp"
                    )
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f, ensure_ascii=False, indent=2)
                    os.replace(temp_path, save_path)

                await asyncio.to_thread(_write_file)

                if expected_version is None or expected_version == self._save_version:
                    self._dirty = False
                return True

        except Exception as e:
            logger.debug(f"异步保存聊天记录失败: {e}")
            return False

    def save_to_file_sync(self, path: str = "") -> bool:
        save_path = path or self._history_path

        try:
            with self._sync_save_lock:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                temp_path = Path(save_path).with_suffix(
                    Path(save_path).suffix + ".tmp"
                )
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._serialize_data(), f, ensure_ascii=False, indent=2)
                os.replace(temp_path, save_path)
                self._dirty = False

            logger.debug(f"聊天记录已保存到: {save_path}")
            return True

        except Exception as e:
            logger.debug(f"保存聊天记录失败: {e}")
            return False

    def save_to_file(self, path: str = "") -> bool:
        if path:
            return self.save_to_file_sync(path)

        self._schedule_save()
        return True

    def load_from_file(self, path: str = "") -> bool:
        load_path = path or self._history_path

        if not os.path.exists(load_path):
            logger.debug(f"聊天记录文件不存在: {load_path}，创建空历史记录文件")
            self._sessions = {self._current_session_id: []}
            self._dirty = True
            self.save_to_file_sync(load_path)
            self._dirty = False
            self.history_loaded.emit()
            return True

        try:
            with open(load_path, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                logger.debug(f"聊天记录文件为空: {load_path}")
                self._sessions = {self._current_session_id: []}
                self._dirty = False
                self.history_loaded.emit()
                return True

            data = json.loads(content)
            version = data.get("version", 1)
            if version not in (1, 2):
                logger.debug(f"警告: 聊天记录版本 {version} 可能不兼容")

            if version == 1:
                messages = []
                for i, m in enumerate(data.get("messages", [])):
                    try:
                        msg = ChatMessage.from_dict(m)
                        msg.metadata = {
                            **msg.metadata,
                            "session_id": self._current_session_id,
                        }
                        messages.append(msg)
                    except Exception as e:
                        logger.debug(f"跳过无效消息 {i}: {e}")
                self._sessions = {self._current_session_id: messages}
            else:
                loaded_sessions: Dict[str, List[ChatMessage]] = {}
                self._current_session_id = self._normalize_session_id(
                    data.get("current_session_id")
                )
                for session_id, messages in data.get("sessions", {}).items():
                    normalized = self._normalize_session_id(session_id)
                    loaded_sessions[normalized] = []
                    for i, m in enumerate(messages):
                        try:
                            msg = ChatMessage.from_dict(m)
                            msg.metadata = {
                                **msg.metadata,
                                "session_id": normalized,
                            }
                            loaded_sessions[normalized].append(msg)
                        except Exception as e:
                            logger.debug(f"跳过无效消息 {normalized}[{i}]: {e}")
                self._sessions = loaded_sessions or {self._current_session_id: []}
                self._sessions.setdefault(self._current_session_id, [])

            self._dirty = False
            logger.debug(
                f"成功加载 {sum(len(messages) for messages in self._sessions.values())} 条聊天记录 "
                f"(会话数 {len(self._sessions)}, 当前会话 {self._current_session_id}, 来自 {load_path})"
            )
            self.history_loaded.emit()
            return True

        except json.JSONDecodeError as e:
            logger.debug(f"聊天记录文件格式错误: {e}")
            self._backup_corrupted_file(load_path)
            self._sessions = {self._current_session_id: []}
            self._dirty = False
            self.history_loaded.emit()
            return False

        except Exception as e:
            logger.debug(f"加载聊天记录失败: {e}")
            import traceback

            traceback.print_exc()
            self._sessions = {self._current_session_id: []}
            self._dirty = False
            self.history_loaded.emit()
            return False

    def _backup_corrupted_file(self, path: str):
        try:
            if os.path.exists(path):
                backup_path = path + f".corrupted.{int(time.time())}"
                os.rename(path, backup_path)
                logger.debug(f"已备份损坏的文件到: {backup_path}")
        except Exception as e:
            logger.debug(f"备份损坏文件失败: {e}")

    def set_auto_save(self, enabled: bool):
        self._auto_save = enabled

    def set_max_messages(self, max_count: int):
        self._max_messages = max(100, max_count)

    def get_message_count(self) -> int:
        return len(self._get_session_messages())

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def export_to_file(self, path: str, format: str = "json") -> bool:
        try:
            if format == "txt":
                with open(path, "w", encoding="utf-8") as f:
                    for msg in self.get_messages():
                        role = "用户" if msg.role == "user" else "助手"
                        time_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp)
                        )
                        f.write(f"[{time_str}] {role}:\n{msg.content}\n\n")
            else:
                return self.save_to_file_sync(path)

            logger.debug(f"聊天记录已导出到: {path}")
            return True

        except Exception as e:
            logger.debug(f"导出聊天记录失败: {e}")
            return False


def get_chat_history_manager(history_path: str = "") -> ChatHistoryManager:
    return ChatHistoryManager.get_instance(history_path)
