"""
Redis 会话存储模块（替代原 JSON 文件版本）

使用 RedisMemoryStore 进行双层记忆管理：
- L1: Sorted Set 滑动窗口保存聊天历史
- L2: Hash 持久化保存用户偏好和摘要
"""

import json
import os
import uuid
from datetime import datetime
from typing import Optional

from utils.memory_store import RedisMemoryStore
from utils.path_tool import get_abs_path


# Redis 配置
REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", 6377)),
    "db": int(os.getenv("REDIS_DB", 0)),
    "password": os.getenv("REDIS_PASSWORD"),
    "window_size": int(os.getenv("MEMORY_WINDOW_SIZE", 50)),  # 滑动窗口大小
    "ttl_seconds": int(os.getenv("MEMORY_TTL", 604800)),  # 7 天
}

_memory_store: Optional[RedisMemoryStore] = None


def get_memory_store() -> RedisMemoryStore:
    """获取单例 Redis 存储实例。"""
    global _memory_store
    if _memory_store is None:
        _memory_store = RedisMemoryStore(**REDIS_CONFIG)
    return _memory_store


def _now() -> str:
    """统一生成 ISO 格式时间。"""
    return datetime.now().isoformat(timespec="seconds")


def _session_title_from_messages(messages: list[dict]) -> str:
    """用第一条用户消息生成会话标题。"""
    for message in messages:
        if message.get("role") == "user":
            content = (message.get("content") or "").strip()
            if content:
                return content[:24] + ("..." if len(content) > 24 else "")
    return "新对话"


def load_sessions() -> list[dict]:
    """
    加载所有会话列表。

    注意：此方法需要配合外部会话列表管理，
    实际只从 Redis 读取单个会话数据。
    """
    # 由于 Redis 方案中会话列表可能存储在别处，
    # 这里返回空列表或根据需求扩展
    return []


def save_sessions(sessions: list[dict]) -> None:
    """
    保存会话列表到 Redis。

    使用 Hash 存储所有会话 ID 列表。
    """
    store = get_memory_store()
    sessions_key = "memory:sessions:list"
    session_ids = [s["id"] for s in sessions]
    store.client.sadd(sessions_key, *session_ids)
    store.client.expire(sessions_key, REDIS_CONFIG["ttl_seconds"])


def create_session(title: str = "新对话") -> dict:
    """创建新会话并初始化 Redis 存储。"""
    now = _now()
    session_id = uuid.uuid4().hex
    session = {
        "id": session_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }

    # 初始化 Redis 中的短期记忆（空滑动窗口）
    store = get_memory_store()
    store.add_message(
        session_id=session_id,
        role="system",
        content=f"会话已创建：{title}",
        metadata={"type": "system"},
    )

    # 添加到会话列表
    sessions = load_sessions()
    sessions.append(session)
    save_sessions(sessions)

    return session


def upsert_session(sessions: list[dict], session: dict) -> list[dict]:
    """按 session_id 更新或插入会话。"""
    updated = []
    found = False
    for item in sessions:
        if item["id"] == session["id"]:
            updated.append(session)
            found = True
        else:
            updated.append(item)
    if not found:
        updated.append(session)
    return updated


def sort_sessions(sessions: list[dict]) -> list[dict]:
    """按最近更新时间倒序排列。"""
    return sorted(sessions, key=lambda item: item.get("updated_at", ""), reverse=True)


def update_session_messages(session: dict, messages: list[dict]) -> dict:
    """
    刷新会话消息到 Redis 滑动窗口。

    将每条消息单独存入 Short-term memory，便于查询最近 N 条上下文。
    """
    session_id = session["id"]
    store = get_memory_store()

    # 清空旧消息并重新添加
    store.clear_history(session_id)

    for msg in messages:
        store.add_message(
            session_id=session_id,
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            metadata=msg.get("metadata", {}),
        )

    # 更新会话元信息
    updated = dict(session)
    updated["messages"] = messages
    updated["updated_at"] = _now()
    updated["title"] = _session_title_from_messages(messages)

    return updated


def delete_session(sessions: list[dict], session_id: str) -> list[dict]:
    """删除指定会话的短期和长期记忆。"""
    store = get_memory_store()
    store.delete_session(session_id)
    return [session for session in sessions if session["id"] != session_id]


def get_session_messages(session_id: str) -> list[dict]:
    """
    从 Redis 获取会话的所有消息。

    Args:
        session_id: 会话 ID

    Returns:
        消息列表
    """
    store = get_memory_store()
    return store.get_messages(session_id)


def get_recent_context(session_id: str, count: int = 20) -> list[dict]:
    """
    获取最近 N 条消息作为 AI 回复的上下文。

    这是滑动窗口的核心用法，保证上下文连贯性。

    Args:
        session_id: 会话 ID
        count: 获取消息数量（默认 20 条）

    Returns:
        最近的 count 条消息
    """
    store = get_memory_store()
    return store.get_recent_context(session_id, count)


def set_user_preference(session_id: str, key: str, value: any) -> None:
    """
    保存用户偏好到长期记忆（L2）。

    Args:
        session_id: 会话 ID
        key: 偏好键名（如 language, preferred_style）
        value: 偏好值
    """
    store = get_memory_store()
    store.set_user_preference(session_id, key, value)


def get_user_preference(session_id: str, key: str, default=None) -> any:
    """
    获取用户偏好设置。

    Args:
        session_id: 会话 ID
        key: 偏好键名
        default: 默认值

    Returns:
        偏好值或默认值
    """
    store = get_memory_store()
    return store.get_user_preference(session_id, key, default)


def save_summary(session_id: str, summary: dict) -> None:
    """
    保存会话摘要到长期记忆。

    可用于跨会话的知识积累。

    Args:
        session_id: 会话 ID
        summary: 摘要内容
    """
    store = get_memory_store()
    store.save_summary(session_id, summary)


def get_summary(session_id: str) -> Optional[dict]:
    """获取会话摘要。"""
    store = get_memory_store()
    return store.get_summary(session_id)


def check_redis_health() -> bool:
    """检查 Redis 连接状态。"""
    try:
        store = get_memory_store()
        return store.health_check()
    except Exception:
        return False
