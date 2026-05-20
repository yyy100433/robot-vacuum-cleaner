"""
Redis 双层记忆存储系统

架构设计：
- L1 短期记忆（滑动窗口）: Redis Sorted Set
  - Key 格式：memory:session:{session_id}:chat_history
  - 使用滑动窗口机制，保留最近 N 条消息保证上下文连贯性
  - Score 使用时间戳，自动按时间排序

- L2 长期记忆（持久化）: Redis Hash
  - Key 格式：memory:session:{session_id}:profile
  - 存储用户偏好、关键信息摘要等持久化数据
"""

import json
import os
from datetime import datetime
from typing import Any, Optional

try:
    import redis
except ImportError:
    raise ImportError("请安装 redis: pip install redis")

from utils.path_tool import get_abs_path


class RedisMemoryStore:
    """基于 Redis 的双层记忆存储。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6377,
        db: int = 0,
        password: Optional[str] = None,
        window_size: int = 50,  # 滑动窗口大小
        ttl_seconds: int = 86400 * 7,  # 7 天过期
    ):
        """
        初始化 Redis 连接。

        Args:
            host: Redis 主机地址
            port: Redis 端口
            db: Redis 数据库编号
            password: 密码（可选）
            window_size: 滑动窗口最大消息数
            ttl_seconds: 会话数据 TTL（秒）
        """
        self.client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )
        self.window_size = window_size
        self.ttl_seconds = ttl_seconds

    # ==================== L1 短期记忆（滑动窗口） ====================

    def _make_chat_key(self, session_id: str) -> str:
        """生成聊天历史 Key。"""
        return f"memory:session:{session_id}:chat_history"

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        添加单条消息到滑动窗口。

        使用 ZADD 按时间戳插入，ZREMRANGEBYRANK 保持窗口大小。
        """
        key = self._make_chat_key(session_id)
        now = datetime.now().isoformat(timespec="seconds")
        message = {
            "role": role,
            "content": content,
            "created_at": now,
            "metadata": metadata or {},
        }
        msg_id = f"{now}:{uuid.uuid4().hex[:8]}" if "uuid" in dir() else now
        score = datetime.now().timestamp()

        # 按分数（时间戳）添加到 Sorted Set
        self.client.zadd(key, {json.dumps(message): score})

        # 维护滑动窗口：移除最旧的消息
        count = self.client.zcard(key)
        if count > self.window_size:
            # 移除最旧的 (score 最小的) 一条
            self.client.zremrangebyrank(key, 0, 0)

        # 设置 TTL
        self.client.expire(key, self.ttl_seconds)

        return msg_id

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict]:
        """
        获取消息列表（从最新到最旧）。

        Args:
            session_id: 会话 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            消息列表，按时间倒序
        """
        key = self._make_chat_key(session_id)
        # 从最新到最旧取数据
        messages = self.client.zrevrange(key, offset, offset + limit - 1 if limit else -1)
        return [json.loads(msg) for msg in messages] if messages else []

    def get_recent_context(
        self,
        session_id: str,
        count: int,
    ) -> list[dict]:
        """
        获取最近 N 条消息作为上下文（滑动窗口的核心用法）。

        Args:
            session_id: 会话 ID
            count: 获取消息数量

        Returns:
            最近的 count 条消息
        """
        return self.get_messages(session_id, limit=count)

    def clear_history(self, session_id: str) -> None:
        """清空会话的短期记忆。"""
        key = self._make_chat_key(session_id)
        self.client.delete(key)

    def get_history_count(self, session_id: str) -> int:
        """获取当前会话的消息数量。"""
        key = self._make_chat_key(session_id)
        return self.client.zcard(key)

    # ==================== L2 长期记忆（持久化） ====================

    def _make_profile_key(self, session_id: str) -> str:
        """生成用户档案 Key。"""
        return f"memory:session:{session_id}:profile"

    def set_user_preference(self, session_id: str, key: str, value: Any) -> None:
        """
        保存用户偏好设置到长期记忆。

        Args:
            session_id: 会话 ID
            key: 偏好键名（如 language, tone）
            value: 偏好值
        """
        profile_key = self._make_profile_key(session_id)
        self.client.hset(profile_key, key, json.dumps(value))
        self.client.expire(profile_key, self.ttl_seconds)

    def get_user_preference(self, session_id: str, key: str, default: Any = None) -> Any:
        """
        获取用户偏好设置。

        Args:
            session_id: 会话 ID
            key: 偏好键名
            default: 默认值

        Returns:
            偏好值或默认值
        """
        profile_key = self._make_profile_key(session_id)
        value = self.client.hget(profile_key, key)
        return json.loads(value) if value else default

    def save_summary(self, session_id: str, summary: dict) -> None:
        """
        保存会话摘要到长期记忆。

        Args:
            session_id: 会话 ID
            summary: 摘要内容（如总结、关键点）
        """
        profile_key = self._make_profile_key(session_id)
        self.client.hset(profile_key, "summary", json.dumps(summary))
        self.client.expire(profile_key, self.ttl_seconds)

    def get_summary(self, session_id: str) -> Optional[dict]:
        """获取会话摘要。"""
        profile_key = self._make_profile_key(session_id)
        value = self.client.hget(profile_key, "summary")
        return json.loads(value) if value else None

    def delete_session(self, session_id: str) -> None:
        """删除整个会话的记忆数据。"""
        # 删除短期记忆
        chat_key = self._make_chat_key(session_id)
        # 删除长期记忆
        profile_key = self._make_profile_key(session_id)
        # 管道操作提高效率
        pipe = self.client.pipeline()
        pipe.delete(chat_key)
        pipe.delete(profile_key)
        pipe.execute()

    def exists(self, session_id: str) -> bool:
        """检查会话是否存在。"""
        chat_key = self._make_chat_key(session_id)
        return self.client.exists(chat_key) > 0

    def health_check(self) -> bool:
        """检查 Redis 连接状态。"""
        try:
            return self.client.ping()
        except redis.ConnectionError:
            return False


# 为了兼容原有的 uuid 引用
import uuid
